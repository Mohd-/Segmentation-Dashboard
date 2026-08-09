"""Acceptance coverage for the approved Business Plan Execution projection."""
from __future__ import annotations

import json
from datetime import date

from conftest import create_project, get_task_by_name, raw_sqlite_connect


def _bp_project(client, name="BPE-1", year=None):
    return create_project(
        client, name, pipeline_type="bp", business_plan_enabled=True,
        business_plan_year=year or date.today().year,
    )


def _save(client, project_id, step, key, value, **extra):
    payload = {"field_key": key, "value": value}
    payload.update(extra)
    return client.patch(
        f"/api/business-plan/wells/{project_id}/steps/{step}/field", json=payload)


def _formation(fluid, formation_id=None, interval_id=None):
    return {
        "id": formation_id,
        "formation": "SARH",
        "top_tvdss_ft": 1000,
        "base_tvdss_ft": 1100,
        "thickness_ft": 100,
        "porosity_pct": 12,
        "swt_pct": 40,
        "pay_ft": 50,
        "ngr_pct": 20,
        "pay_intervals": [{
            "id": interval_id,
            "top_tvdss_ft": 1010,
            "base_tvdss_ft": 1060,
            "phit_pct": 13,
            "swt_pct": 35,
            "ngr_pct": 18,
            "kint_md": 2.5,
            "fluid": fluid,
        }],
    }


def _put_formations(client, project_id, step, rows):
    return client.put(
        f"/api/business-plan/wells/{project_id}/steps/{step}/formations",
        json={"rows": rows},
    )


def _confirm_quicklook_files(client, project_id):
    for key in ("quicklook_pdf", "quicklook_las"):
        response = _save(client, project_id, "quicklook-logs", key, True)
        assert response.status_code == 200, response.get_json()


def _raw_fields(client, project_id, task_name, values):
    task = get_task_by_name(client, project_id, task_name)
    conn = raw_sqlite_connect(client.db_path)
    with conn:
        for key, value in values.items():
            conn.execute(
                "INSERT INTO task_dynamic_fields (task_id, field_key, field_value, updated_at) "
                "VALUES (?, ?, ?, '2026-01-01 00:00:00') "
                "ON CONFLICT(task_id, field_key) DO UPDATE SET field_value = excluded.field_value",
                (task["task_id"], key, str(value)),
            )
    conn.close()


def _history(client, project_id, action_type=None):
    conn = raw_sqlite_connect(client.db_path)
    try:
        sql = """
            SELECT history_id, task_name, action_type, old_status, new_status,
                   changed_at, changed_by, comment
            FROM task_history WHERE project_id = ?
        """
        params = [project_id]
        if action_type:
            sql += " AND action_type = ?"
            params.append(action_type)
        sql += " ORDER BY history_id"
        return [dict(row) for row in conn.execute(sql, params)]
    finally:
        conn.close()


def test_dashboard_has_approved_filters_steps_and_six_item_stage(client):
    project_id = _bp_project(client)
    response = client.get(f"/api/business-plan/dashboard?year={date.today().year}")
    assert response.status_code == 200
    body = response.get_json()
    assert body["options"]["statuses"] == [
        "All Status", "Completed", "Pending Approval", "In Progress"]
    assert body["options"]["years"] == list(range(1999, 2036))
    assert [step["label"] for step in body["options"]["steps"]][1:] == [
        "Business Plan Gate", "Well Proposal", "Site Preparation", "Approval to Drill",
        "GHEER: Geophysics", "GHEER: Geomechanics", "Quicklook Logs", "AAP",
        "SAD Model", "Executive Summary", "URED Update", "Learnings", "Flowback",
        "SAD Update", "Final Summary", "Final Logs", "MTR", "PDA & Booking",
    ]
    well = next(row for row in body["wells"] if row["project_id"] == project_id)
    assert well["stage_label"] == "Pre-Drilling"
    assert len(well["items"]) == 6
    assert [item["label"] for item in well["items"]] == [
        "Business Plan Gate", "Well Proposal", "Site Preparation", "Approval to Drill",
        "GHEER: Geophysics", "GHEER: Geomechanics",
    ]


def test_portfolio_promotion_year_immediately_feeds_business_plan_dashboard(client):
    project_id = create_project(client, "PORT-BPE-1", pipeline_type="prospect")
    before = client.get("/api/business-plan/dashboard?year=2030").get_json()
    assert project_id not in {row["project_id"] for row in before["wells"]}

    promoted = client.patch(
        f"/api/projects/{project_id}/flags",
        json={"business_plan_enabled": True, "business_plan_year": 2030},
    )
    assert promoted.status_code == 200, promoted.get_json()

    portfolio = client.get("/api/portfolio/rows?year=All&activity=All").get_json()
    portfolio_row = next(row for row in portfolio["rows"] if row["project_id"] == project_id)
    assert (portfolio_row["pipeline_type"], portfolio_row["year"]) == ("bp", 2030)

    dashboard = client.get("/api/business-plan/dashboard?year=2030").get_json()
    well = next(row for row in dashboard["wells"] if row["project_id"] == project_id)
    assert well["business_plan_year"] == 2030
    assert well["stage_label"] == "Pre-Drilling"

    other_year = client.get("/api/business-plan/dashboard?year=2031").get_json()
    assert project_id not in {row["project_id"] for row in other_year["wells"]}


def test_development_classification_resets_defaults_and_system_completes_only_proposal(client):
    project_id = _bp_project(client)
    response = _save(client, project_id, "business-plan-gate", "bp_gate_classification", "Development")
    assert response.status_code == 200, response.get_json()
    values = response.get_json()["detail"]["values"]
    assert values["bp_gate_logging_program"] == "Optimized Standard B"
    assert [values[key] for key in ("bp_gate_swc", "bp_gate_pressure_points", "bp_gate_fluid_samples")] == ["0", "3", "3"]

    detail = client.get(f"/api/business-plan/wells/{project_id}/steps/well-letters").get_json()
    states = {item["key"]: item for item in detail["stage_items"]}
    assert states["well-proposal"]["status"] == "Completed"
    assert states["well-proposal"]["color"] == "gray"
    assert states["well-proposal"]["locked"] is True
    assert states["site-preparation"]["status"] == "In Progress"
    assert states["approval-to-drill"]["status"] == "In Progress"

    blocked = _save(client, project_id, "well-letters", "well_proposal_shared", True)
    assert blocked.status_code == 400


def test_navigation_carries_every_step_status_for_the_detail_rail(client):
    project_id = _bp_project(client)

    def statuses():
        detail = client.get(
            f"/api/business-plan/wells/{project_id}/steps/business-plan-gate").get_json()
        assert [group["stage_key"] for group in detail["navigation"]] == [
            "pre_drilling", "post_drilling", "post_testing"]
        return {entry["slug"]: entry["status"]
                for group in detail["navigation"] for entry in group["details"]}

    initial = statuses()
    assert len(initial) == 14, "every step of every stage travels with the rail"
    assert set(initial.values()) == {"In Progress"}

    # Well Letters owns THREE tracking items. Completing one never finishes the
    # entry -- the rail must not claim a step is done because part of it is.
    _save(client, project_id, "well-letters", "site_preparation_shared", True)
    assert statuses()["well-letters"] == "In Progress"
    _save(client, project_id, "well-letters", "well_proposal_shared", True)
    _save(client, project_id, "well-letters", "approval_to_drill_shared", True)
    assert statuses()["well-letters"] == "Completed"

    # A step waiting on a supervisor is neither done nor merely in progress.
    _raw_fields(client, project_id, "BP Execution Gate", {
        "bp_gate_classification": "Appraisal",
        "bp_gate_calculated_td_ft_md": "12000",
        "bp_gate_actual_td_ft_md": "12100",
        "bp_gate_actual_drilling_days": "31.5",
        "bp_gate_logging_program": "Standard A",
        "bp_gate_interval_from": "SARH",
        "bp_gate_interval_to": "QASM",
        "bp_gate_swc": "30",
        "bp_gate_pressure_points": "20",
        "bp_gate_fluid_samples": "5",
        "bp_gate_coring_program": "No",
        "bp_gate_slides_saved": "1",
    })
    submitted = client.post(
        f"/api/business-plan/wells/{project_id}/steps/business-plan-gate/transition",
        json={"action": "submit"},
    )
    assert submitted.status_code == 200, submitted.get_json()
    assert statuses()["business-plan-gate"] == "Pending Approval"


def test_gate_submission_requires_complete_draft_and_supervisor_approval(client):
    project_id = _bp_project(client)
    incomplete = client.post(
        f"/api/business-plan/wells/{project_id}/steps/business-plan-gate/transition",
        json={"action": "submit"},
    )
    assert incomplete.status_code == 400
    # Calculated Drilling Days is deliberately ABSENT: the field is locked (no
    # equation ships yet), so requiring it would leave the Gate unapprovable.
    _raw_fields(client, project_id, "BP Execution Gate", {
        "bp_gate_classification": "Appraisal",
        "bp_gate_calculated_td_ft_md": "12000",
        "bp_gate_actual_td_ft_md": "12100",
        "bp_gate_actual_drilling_days": "31.5",
        "bp_gate_logging_program": "Standard A",
        "bp_gate_interval_from": "SARH",
        "bp_gate_interval_to": "QASM",
        "bp_gate_swc": "30",
        "bp_gate_pressure_points": "20",
        "bp_gate_fluid_samples": "5",
        "bp_gate_coring_program": "No",
        "bp_gate_slides_saved": "1",
    })
    submitted = client.post(
        f"/api/business-plan/wells/{project_id}/steps/business-plan-gate/transition",
        json={"action": "submit"},
    )
    assert submitted.status_code == 200, submitted.get_json()
    assert submitted.get_json()["detail"]["tracking"][0]["status"] == "Pending Approval"
    pending_edit = _save(
        client, project_id, "business-plan-gate", "bp_gate_actual_drilling_days", 32)
    assert pending_edit.status_code == 400
    assert "must be returned" in pending_edit.get_json()["detail"]
    approved = client.post(
        f"/api/business-plan/wells/{project_id}/steps/business-plan-gate/transition",
        json={"action": "approve"},
    )
    assert approved.status_code == 200
    assert approved.get_json()["detail"]["tracking"][0]["color"] == "green"
    edit = _save(client, project_id, "business-plan-gate", "bp_gate_actual_drilling_days", 32)
    assert edit.status_code == 400
    reopened = client.post(
        f"/api/business-plan/wells/{project_id}/steps/business-plan-gate/transition",
        json={"action": "reopen", "comment": "Scope changed"},
    )
    assert reopened.status_code == 200
    assert _save(client, project_id, "business-plan-gate", "bp_gate_actual_drilling_days", 32).status_code == 200
    approved_event = _history(client, project_id, "Component Approved")[-1]
    approved_context = json.loads(approved_event["comment"])
    assert approved_event["changed_by"] == "Web User"
    assert approved_context["role"] == "supervisor"
    assert approved_context["source"] == "supervisor"
    assert approved_context["correlation"]


def test_pay_intervals_keep_ids_and_water_dry_cascade_is_reversible(client):
    project_id = _bp_project(client)
    _confirm_quicklook_files(client, project_id)
    first = _put_formations(client, project_id, "quicklook-logs", [_formation("Dry Hole")])
    assert first.status_code == 200, first.get_json()
    row = first.get_json()["detail"]["formations"][0]
    formation_id = row["id"]
    interval_id = row["pay_intervals"][0]["id"]
    assert row["pay_intervals"][0]["fluid"] == "Dry Hole"
    detail = client.get(f"/api/business-plan/wells/{project_id}/steps/flowback-results").get_json()
    assert detail["tracking"][0] == {
        "color": "gray", "key": "flowback", "locked": True,
        "source": "system", "status": "Completed",
    }
    post_items = {item["key"]: item for item in detail["stage_items"]}
    assert post_items["sad-update"]["color"] == "gray"
    assert post_items["mtr"]["color"] == "gray"
    assert post_items["final-logs"]["status"] == "In Progress"

    second = _put_formations(
        client, project_id, "quicklook-logs",
        [_formation("Gas", formation_id=formation_id, interval_id=interval_id)],
    )
    assert second.status_code == 200, second.get_json()
    changed = second.get_json()["detail"]["formations"][0]
    assert changed["id"] == formation_id
    assert changed["pay_intervals"][0]["id"] == interval_id
    flowback = client.get(f"/api/business-plan/wells/{project_id}/steps/flowback-results").get_json()
    assert flowback["tracking"][0]["status"] == "In Progress"
    assert flowback["tracking"][0]["locked"] is False


def test_quicklook_and_final_logs_preserve_existing_file_confirmation_sets(client):
    project_id = _bp_project(client)
    assert _save(client, project_id, "quicklook-logs", "quicklook_pdf", True).status_code == 200
    quick = _put_formations(client, project_id, "quicklook-logs", [_formation("Gas")])
    assert quick.status_code == 200, quick.get_json()
    assert quick.get_json()["detail"]["formation_options"][:3] == ["SARH", "QASM", "QWRH"]
    assert quick.get_json()["detail"]["tracking"][0]["status"] == "In Progress"
    quick = _save(client, project_id, "quicklook-logs", "quicklook_las", True)
    assert quick.get_json()["detail"]["tracking"][0]["status"] == "Completed"

    for key in ("final_petrel", "final_pdf"):
        assert _save(client, project_id, "final-log-analysis", key, True).status_code == 200
    final = _put_formations(client, project_id, "final-log-analysis", [_formation("Oil")])
    assert final.status_code == 200, final.get_json()
    assert final.get_json()["detail"]["tracking"][0]["status"] == "In Progress"
    final = _save(client, project_id, "final-log-analysis", "final_las", True)
    assert final.get_json()["detail"]["tracking"][0]["status"] == "Completed"


def test_flowback_comparison_copies_or_selects_manual_sad_update_without_guessing(client):
    project_id = _bp_project(client)
    _confirm_quicklook_files(client, project_id)
    assert _put_formations(client, project_id, "quicklook-logs", [_formation("Gas")]).status_code == 200
    for key, value in {
        "sad_area_km2_p90": 10, "sad_area_km2_p10": 20,
        "sad_grv_p90": 100, "sad_grv_p10": 200,
        "post_drill_piip_gas_p90": 40, "post_drill_piip_gas_mean": 50,
        "post_drill_piip_gas_p10": 60,
        "sad_surfaces_polygons_loaded": True, "sad_slides_loaded": True,
    }.items():
        assert _save(client, project_id, "sad-model", key, value).status_code == 200

    copied = client.put(f"/api/business-plan/wells/{project_id}/flowback-stages", json={"rows": [{
        "formation": "SARH", "top_md": 1000, "base_md": 1100,
        "dynamic_area_km2": 9, "dynamic_ogip_bcf": 49,
        "choke_size_in": 0.5, "fwhp_psi": 1000,
    }]})
    assert copied.status_code == 200, copied.get_json()
    update = client.get(f"/api/business-plan/wells/{project_id}/steps/sad-model-update").get_json()
    assert update["sad_update_branch"] == "copied_from_sad"
    assert update["tracking"][0]["color"] == "gray"
    assert update["values"]["resource_update_gas_mean"] == "50"

    row = copied.get_json()["detail"]["flowback_stages"][0]
    row["dynamic_area_km2"] = 11
    manual = client.put(f"/api/business-plan/wells/{project_id}/flowback-stages", json={"rows": [row]})
    assert manual.status_code == 200
    update = client.get(f"/api/business-plan/wells/{project_id}/steps/sad-model-update").get_json()
    assert update["sad_update_branch"] == "manual_update"
    assert update["tracking"][0]["status"] == "In Progress"
    assert update["values"]["resource_update_gas_mean"] == ""

    sad_update_task = get_task_by_name(client, project_id, "SAD Update")
    conn = raw_sqlite_connect(client.db_path)
    with conn:
        conn.execute(
            "UPDATE project_tasks SET status = 'Approved' WHERE task_id = ?",
            (sad_update_task["task_id"],),
        )
    conn.close()
    copied_attempt = dict(row, dynamic_area_km2=9, dynamic_ogip_bcf=49)
    blocked = client.put(
        f"/api/business-plan/wells/{project_id}/flowback-stages",
        json={"rows": [copied_attempt]},
    )
    assert blocked.status_code == 400
    assert "Reopen the approved SAD Model Update" in blocked.get_json()["detail"]
    conn = raw_sqlite_connect(client.db_path)
    with conn:
        conn.execute(
            "UPDATE project_tasks SET status = 'In Progress' WHERE task_id = ?",
            (sad_update_task["task_id"],),
        )
    conn.close()

    row["dynamic_ogip_bcf"] = ""
    unresolved = client.put(f"/api/business-plan/wells/{project_id}/flowback-stages", json={"rows": [row]})
    assert unresolved.status_code == 200
    assert unresolved.get_json()["detail"]["sad_update_branch"] == "unresolved_comparison"


def test_flowback_last_stage_deletion_persists_an_initialized_empty_collection(client):
    project_id = _bp_project(client)
    initial = client.get(
        f"/api/business-plan/wells/{project_id}/steps/flowback-results").get_json()
    assert initial["flowback_stages"] == []
    assert initial["flowback_initialized"] is False

    created = client.put(
        f"/api/business-plan/wells/{project_id}/flowback-stages",
        json={"rows": [{
            "id": "persistent-stage", "formation": "SARH", "top_md": 1000,
            "base_md": 1100, "choke_size_in": 0.5, "fwhp_psi": 1500,
        }]},
    )
    assert created.status_code == 200, created.get_json()
    assert created.get_json()["detail"]["flowback_initialized"] is True

    deleted = client.put(
        f"/api/business-plan/wells/{project_id}/flowback-stages", json={"rows": []})
    assert deleted.status_code == 200, deleted.get_json()
    assert deleted.get_json()["detail"]["flowback_stages"] == []
    assert deleted.get_json()["detail"]["flowback_initialized"] is True

    reloaded = client.get(
        f"/api/business-plan/wells/{project_id}/steps/flowback-results").get_json()
    assert reloaded["flowback_stages"] == []
    assert reloaded["flowback_initialized"] is True
    removed = _history(client, project_id, "Flowback Stage Removed")[-1]
    assert removed["old_status"] == "persistent-stage"


def test_bpe_flowback_stage_reaches_well_summary_in_canonical_shape(client):
    """The BPE writer and its record-level Well Summary bundle carry the same
    concise row keys the shared card reads; no flat EAV alias is required."""
    project_id = _bp_project(client, "BPE-SUMMARY-FLOWBACK-1")
    saved = client.put(
        f"/api/business-plan/wells/{project_id}/flowback-stages", json={"rows": [{
            "id": "summary-stage", "formation": "SARH", "top_md": 11200,
            "base_md": 11450, "gas_rate_mmscfd": 6.1,
            "choke_size_in": 0.5, "fwhp_psi": 2100,
        }]})
    assert saved.status_code == 200, saved.get_json()

    detail = client.get(
        f"/api/business-plan/wells/{project_id}/steps/business-plan-gate").get_json()
    flowback = detail["well_summary"]["fields"]["Flowback Results"]
    rows = json.loads(flowback["flowback_stages_rows"])
    assert rows == [{
        "id": "summary-stage", "formation": "SARH", "top_md": "11200",
        "base_md": "11450", "dynamic_area_km2": "", "dynamic_ogip_bcf": "",
        "gas_rate_mmscfd": "6.1", "water_rate_bwpd": "", "liquid_rate_bpd": "",
        "choke_size_in": "0.5", "fwhp_psi": "2100",
    }]


def test_dashboard_kpis_share_filters_and_preserve_zero_actual_precedence(client):
    project_id = _bp_project(client, "KPI-1")
    _raw_fields(client, project_id, "BP Execution Gate", {"bp_gate_actual_drilling_days": "12.5"})
    _raw_fields(client, project_id, "Pre-Drilling GeoX Assessment", {"pre_drill_piip_gas_mean": "80.4"})
    _raw_fields(client, project_id, "SAD Model", {"post_drill_piip_gas_mean": "45"})
    _raw_fields(client, project_id, "SAD Update", {"resource_update_gas_mean": "0"})
    task = get_task_by_name(client, project_id, "BP Execution Gate")
    conn = raw_sqlite_connect(client.db_path)
    with conn:
        conn.execute("UPDATE project_tasks SET status = 'Approved' WHERE task_id = ?", (task["task_id"],))
        conn.execute("""
            INSERT INTO project_formations
              (project_id, formation, phase, top_tvdss_ft, base_tvdss_ft, thickness_ft, fluid)
            VALUES (?, 'SARH', 'quicklook', 1000, 1100, 100, '')
        """, (project_id,))
        conn.execute("""
            INSERT INTO project_formation_pay_intervals
              (project_id, formation, phase, seq, top_tvdss_ft, base_tvdss_ft,
               phit_pct, swt_pct, ngr_pct, kint_md, fluid)
            VALUES (?, 'SARH', 'quicklook', 1, 1010, 1060, 13, 35, 18, 2.5, 'Gas')
        """, (project_id,))
    conn.close()
    body = client.get(f"/api/business-plan/dashboard?year={date.today().year}&step=all").get_json()
    assert body["kpis"] == {
        "rig_inventory_days": 12.5,
        "rig_target_days": 12.5,
        "success_rate_pct": 100,
        "classified_rate": 1,
        "actual_mean_ogip_bcf": 0,
        "simulated_mean_ogip_bcf": 80,
    }


def _insert_quicklook_fluid(client, project_id, fluid):
    conn = raw_sqlite_connect(client.db_path)
    with conn:
        conn.execute("""
            INSERT INTO project_formations
              (project_id, formation, phase, top_tvdss_ft, base_tvdss_ft, thickness_ft, fluid)
            VALUES (?, 'SARH', 'quicklook', 1000, 1100, 100, ?)
        """, (project_id, fluid or ""))
        conn.execute("""
            INSERT INTO project_formation_pay_intervals
              (project_id, formation, phase, seq, top_tvdss_ft, base_tvdss_ft,
               phit_pct, swt_pct, ngr_pct, kint_md, fluid)
            VALUES (?, 'SARH', 'quicklook', 1, 1010, 1060, 13, 35, 18, 2.5, ?)
        """, (project_id, fluid or ""))
    conn.close()


def test_success_rate_dry_hole_counts_as_failure(client):
    _bp_project(client, "SR-DRY-1")
    body = client.get(f"/api/business-plan/dashboard?year={date.today().year}&step=all").get_json()
    assert body["kpis"]["success_rate_pct"] is None
    assert body["kpis"]["classified_rate"] == 0
    _insert_quicklook_fluid(client, body["wells"][0]["project_id"], "Dry Hole")
    body = client.get(f"/api/business-plan/dashboard?year={date.today().year}&step=all").get_json()
    assert body["kpis"]["success_rate_pct"] == 0
    assert body["kpis"]["classified_rate"] == 1


def test_success_rate_water_bearing_counts_as_failure(client):
    project_id = _bp_project(client, "SR-WB-1")
    _insert_quicklook_fluid(client, project_id, "Water Bearing")
    body = client.get(f"/api/business-plan/dashboard?year={date.today().year}&step=all").get_json()
    well = next(w for w in body["wells"] if w["project_id"] == project_id)
    assert well["fluid_decision"] == "all_water_or_dry"
    assert well["successful"] is False
    assert body["kpis"]["success_rate_pct"] == 0
    assert body["kpis"]["classified_rate"] == 1


def test_success_rate_productive_fluid_counts_as_success(client):
    project_id = _bp_project(client, "SR-GAS-1")
    _insert_quicklook_fluid(client, project_id, "Gas")
    body = client.get(f"/api/business-plan/dashboard?year={date.today().year}&step=all").get_json()
    well = next(w for w in body["wells"] if w["project_id"] == project_id)
    assert well["fluid_decision"] == "productive"
    assert well["successful"] is True
    assert body["kpis"]["success_rate_pct"] == 100
    assert body["kpis"]["classified_rate"] == 1


def test_success_rate_no_fluid_excluded_from_calculation(client):
    project_id = _bp_project(client, "SR-NOF-1")
    body = client.get(f"/api/business-plan/dashboard?year={date.today().year}&step=all").get_json()
    well = next(w for w in body["wells"] if w["project_id"] == project_id)
    assert well["fluid_decision"] == "incomplete"
    assert body["kpis"]["success_rate_pct"] is None
    assert body["kpis"]["classified_rate"] == 0


def test_success_rate_mixed_classified_and_unclassified(client):
    pid_classified = _bp_project(client, "SR-MIX-C")
    pid_unclassified = _bp_project(client, "SR-MIX-U")
    _insert_quicklook_fluid(client, pid_classified, "Oil")
    body = client.get(f"/api/business-plan/dashboard?year={date.today().year}&step=all").get_json()
    assert body["kpis"]["success_rate_pct"] == 100
    assert body["kpis"]["classified_rate"] == 1


def test_success_rate_filters_recalculate_kpi(client):
    year_a = date.today().year
    year_b = year_a + 1
    pid_a = _bp_project(client, "SR-F-A", year=year_a)
    pid_b = _bp_project(client, "SR-F-B", year=year_b)
    _insert_quicklook_fluid(client, pid_a, "Gas")
    _insert_quicklook_fluid(client, pid_b, "Dry Hole")
    body_a = client.get(f"/api/business-plan/dashboard?year={year_a}&step=all").get_json()
    assert body_a["kpis"]["success_rate_pct"] == 100
    assert body_a["kpis"]["classified_rate"] == 1
    body_b = client.get(f"/api/business-plan/dashboard?year={year_b}&step=all").get_json()
    assert body_b["kpis"]["success_rate_pct"] == 0
    assert body_b["kpis"]["classified_rate"] == 1


def test_success_rate_eight_of_ten_is_eighty_percent(client):
    year = date.today().year
    for i in range(8):
        pid = _bp_project(client, f"SR-8OF10-S{i}", year=year)
        _insert_quicklook_fluid(client, pid, "Gas")
    for i in range(2):
        pid = _bp_project(client, f"SR-8OF10-F{i}", year=year)
        _insert_quicklook_fluid(client, pid, "Dry Hole")
    body = client.get(f"/api/business-plan/dashboard?year={year}&step=all").get_json()
    assert body["kpis"]["classified_rate"] == 10
    assert body["kpis"]["success_rate_pct"] == 80


def test_success_rate_empty_population_returns_none(client):
    _bp_project(client, "SR-EMPTY-1")
    _bp_project(client, "SR-EMPTY-2")
    body = client.get(f"/api/business-plan/dashboard?year={date.today().year}&step=all").get_json()
    assert body["kpis"]["success_rate_pct"] is None
    assert body["kpis"]["classified_rate"] == 0


def test_audit_records_field_tracking_progress_and_no_event_for_unchanged_replay(client):
    project_id = _bp_project(client, "AUDIT-BPE-1")
    saved = _save(client, project_id, "well-letters", "site_preparation_shared", True)
    assert saved.status_code == 200, saved.get_json()

    field_events = _history(client, project_id, "Business Plan Field Updated")
    event = next(row for row in field_events if json.loads(row["comment"])["field"] == "site_preparation_shared")
    context = json.loads(event["comment"])
    assert (event["old_status"], event["new_status"], event["changed_by"]) == ("", "1", "Web User")
    assert event["changed_at"]
    assert context["source"] == "user"
    assert context["role"] == "supervisor"
    assert context["correlation"]

    tracking = _history(client, project_id, "Business Plan Tracking Updated")
    tracking_event = next(row for row in tracking if json.loads(row["comment"])["tracking_item"] == "site-preparation")
    assert json.loads(tracking_event["old_status"])["status"] == "In Progress"
    assert json.loads(tracking_event["new_status"])["status"] == "Completed"
    progress = _history(client, project_id, "Business Plan Progress Updated")
    assert [(row["old_status"], row["new_status"]) for row in progress] == [("0/6", "1/6")]

    before = _history(client, project_id)
    replay = _save(client, project_id, "well-letters", "site_preparation_shared", True)
    assert replay.status_code == 200
    assert _history(client, project_id) == before


def test_audit_correlates_system_defaults_approval_and_repeatable_structure_edits(client):
    project_id = _bp_project(client, "AUDIT-BPE-2")
    classified = _save(
        client, project_id, "business-plan-gate", "bp_gate_classification", "Development")
    assert classified.status_code == 200
    system_fields = [
        row for row in _history(client, project_id, "Business Plan Field Updated")
        if json.loads(row["comment"])["source"] == "system"
    ]
    assert {json.loads(row["comment"])["field"] for row in system_fields} >= {
        "bp_gate_logging_program", "bp_gate_swc", "bp_gate_pressure_points",
        "bp_gate_fluid_samples", "bp_gate_coring_program",
    }
    correlations = {json.loads(row["comment"])["correlation"] for row in system_fields}
    assert len(correlations) == 1
    proposal_event = next(
        row for row in _history(client, project_id, "Business Plan Tracking Updated")
        if json.loads(row["comment"])["tracking_item"] == "well-proposal")
    assert json.loads(proposal_event["new_status"])["source"] == "system"

    formed = _put_formations(client, project_id, "quicklook-logs", [_formation("Gas")])
    assert formed.status_code == 200, formed.get_json()
    row = formed.get_json()["detail"]["formations"][0]
    structure_actions = {
        event["action_type"] for event in _history(client, project_id)
        if event["action_type"].startswith(("Formation", "Pay Interval"))
    }
    assert structure_actions >= {
        "Formation Added", "Formation Field Updated",
        "Pay Interval Added", "Pay Interval Field Updated",
    }
    before_replay = _history(client, project_id)
    assert _put_formations(client, project_id, "quicklook-logs", [row]).status_code == 200
    assert _history(client, project_id) == before_replay

    flowback_payload = [{
        "id": "stable-stage-1", "formation": "SARH", "top_md": 1000,
        "base_md": 1100, "dynamic_area_km2": 9, "dynamic_ogip_bcf": 45,
        "gas_rate_mmscfd": "", "water_rate_bwpd": "", "liquid_rate_bpd": "",
        "choke_size_in": 0.5, "fwhp_psi": 1500,
    }]
    added = client.put(
        f"/api/business-plan/wells/{project_id}/flowback-stages", json={"rows": flowback_payload})
    assert added.status_code == 200, added.get_json()
    flow_events = _history(client, project_id, "Flowback Stage Added")
    assert flow_events[-1]["new_status"] == "stable-stage-1"
    assert json.loads(flow_events[-1]["comment"])["correlation"]
    before_flow_replay = _history(client, project_id)
    assert client.put(
        f"/api/business-plan/wells/{project_id}/flowback-stages",
        json={"rows": flowback_payload},
    ).status_code == 200
    assert _history(client, project_id) == before_flow_replay


def test_a_stored_legacy_fluid_round_trips_but_is_never_written_afresh(client):
    """A database that has not run migration v10 still serves pre-v10 fluid
    labels to the editor, so saving an UNEDITED row back must not 400. The
    allowance is exactly that -- a replace of the interval that already holds
    the value; a new interval or a switch to another retired label is a WRITE
    of retired vocabulary and stays rejected."""
    project_id = _bp_project(client)
    _confirm_quicklook_files(client, project_id)
    created = _put_formations(client, project_id, "quicklook-logs", [_formation("Gas")])
    assert created.status_code == 200, created.get_json()
    row = created.get_json()["detail"]["formations"][0]
    formation_id = row["id"]
    interval_id = row["pay_intervals"][0]["id"]

    conn = raw_sqlite_connect(client.db_path)
    with conn:
        conn.execute("UPDATE project_formation_pay_intervals SET fluid = 'Condensate' WHERE id = ?",
                     (interval_id,))
    conn.close()

    unchanged = _put_formations(
        client, project_id, "quicklook-logs",
        [_formation("Condensate", formation_id=formation_id, interval_id=interval_id)])
    assert unchanged.status_code == 200, unchanged.get_json()
    assert unchanged.get_json()["detail"]["formations"][0]["pay_intervals"][0]["fluid"] == "Condensate"

    fresh = _put_formations(client, project_id, "quicklook-logs",
                            [_formation("Condensate", formation_id=formation_id)])
    assert fresh.status_code == 400
    switched = _put_formations(
        client, project_id, "quicklook-logs",
        [_formation("Liquid", formation_id=formation_id, interval_id=interval_id)])
    assert switched.status_code == 400
    assert "Fluid" in switched.get_json()["detail"]


def _interval(top, interval_id=None, fluid="Gas"):
    return {
        "id": interval_id, "top_tvdss_ft": top, "base_tvdss_ft": top + 50,
        "phit_pct": 13, "swt_pct": 35, "ngr_pct": 18, "kint_md": 2.5, "fluid": fluid,
    }


def _named(name, intervals=None, formation_id=None):
    row = _formation("Gas", formation_id=formation_id)
    row["formation"] = name
    if intervals is not None:
        row["pay_intervals"] = intervals
    return row


def _seqs(response, index=0):
    intervals = response.get_json()["detail"]["formations"][index]["pay_intervals"]
    return [(item["id"], item["seq"]) for item in intervals]


def test_dropping_the_first_pay_interval_renumbers_instead_of_colliding(client):
    """seq is unique per (project, formation, phase), so the survivor can only
    move into seq 1 once the row holding it is gone -- the delete has to happen
    BEFORE the renumbering, not after it."""
    project_id = _bp_project(client)
    _confirm_quicklook_files(client, project_id)
    created = _put_formations(client, project_id, "quicklook-logs",
                              [_named("SARH", [_interval(1010), _interval(1100)])])
    assert created.status_code == 200, created.get_json()
    row = created.get_json()["detail"]["formations"][0]
    first_id, second_id = [item["id"] for item in row["pay_intervals"]]
    assert _seqs(created) == [(first_id, 1), (second_id, 2)]

    trimmed = _put_formations(client, project_id, "quicklook-logs", [
        _named("SARH", [_interval(1100, interval_id=second_id)], formation_id=row["id"])])
    assert trimmed.status_code == 200, trimmed.get_json()
    assert _seqs(trimmed) == [(second_id, 1)]


def test_reordering_pay_intervals_swaps_seq_instead_of_colliding(client):
    """A pure reorder collides in either direction unless the kept rows are
    parked off the 1..n range first."""
    project_id = _bp_project(client)
    _confirm_quicklook_files(client, project_id)
    created = _put_formations(client, project_id, "quicklook-logs",
                              [_named("SARH", [_interval(1010), _interval(1100)])])
    assert created.status_code == 200, created.get_json()
    row = created.get_json()["detail"]["formations"][0]
    first_id, second_id = [item["id"] for item in row["pay_intervals"]]

    reordered = _put_formations(client, project_id, "quicklook-logs", [_named(
        "SARH",
        [_interval(1100, interval_id=second_id), _interval(1010, interval_id=first_id)],
        formation_id=row["id"],
    )])
    assert reordered.status_code == 200, reordered.get_json()
    assert _seqs(reordered) == [(second_id, 1), (first_id, 2)]


def test_renaming_a_formation_onto_a_kept_name_is_a_400_not_a_500(client):
    """Formation names are unique per (project, phase) and kept rows are renamed
    IN PLACE. A payload that repeats a name is caught by the payload cleaner; a
    payload whose names are all distinct but whose renames pass THROUGH a name a
    surviving row still holds -- the A->B / B->C chain and the A<->B swap, which
    no ordering of in-place UPDATEs can perform -- is the case that used to
    reach SQLite and 500. Only a name freed in the same save may be taken."""
    project_id = _bp_project(client)
    _confirm_quicklook_files(client, project_id)
    created = _put_formations(client, project_id, "quicklook-logs",
                              [_named("SARH"), _named("QASM")])
    assert created.status_code == 200, created.get_json()
    sarh, qasm = created.get_json()["detail"]["formations"]

    repeated = _put_formations(client, project_id, "quicklook-logs", [
        _named("QASM", formation_id=sarh["id"]), _named("QASM", formation_id=qasm["id"])])
    assert repeated.status_code == 400
    assert "Duplicate formation" in repeated.get_json()["detail"]

    chain = _put_formations(client, project_id, "quicklook-logs", [
        _named("QASM", formation_id=sarh["id"]), _named("QWRH", formation_id=qasm["id"])])
    assert chain.status_code == 400
    assert "already exists" in chain.get_json()["detail"]

    # The A<->B swap is REJECTED rather than supported: it is the only payload
    # a two-pass rename would buy, and the pay intervals hang off the formation
    # NAME, so carrying it out would mean shuffling those rows through a
    # temporary name too. Two saves do it; the message says so.
    swap = _put_formations(client, project_id, "quicklook-logs", [
        _named("QASM", formation_id=sarh["id"]), _named("SARH", formation_id=qasm["id"])])
    assert swap.status_code == 400
    assert "already exists" in swap.get_json()["detail"]

    unchanged = client.get(
        f"/api/business-plan/wells/{project_id}/steps/quicklook-logs").get_json()
    assert [row["formation"] for row in unchanged["formations"]] == ["SARH", "QASM"]

    # Dropping QASM in the same save frees its name for SARH's rename.
    freed = _put_formations(client, project_id, "quicklook-logs",
                            [_named("QASM", formation_id=sarh["id"])])
    assert freed.status_code == 200, freed.get_json()
    assert [row["formation"] for row in freed.get_json()["detail"]["formations"]] == ["QASM"]


# ---------------------------------------------------------------------------
# The BPE transition's lifecycle safeguards (its own state machine, the SAME
# completion stamp, notification fan-out and route-level role gate)
# ---------------------------------------------------------------------------

def _submittable_gate(client, project_id):
    _raw_fields(client, project_id, "BP Execution Gate", {
        "bp_gate_classification": "Appraisal",
        "bp_gate_calculated_td_ft_md": "12000",
        "bp_gate_actual_td_ft_md": "12100",
        "bp_gate_actual_drilling_days": "31.5",
        "bp_gate_logging_program": "Standard A",
        "bp_gate_interval_from": "SARH",
        "bp_gate_interval_to": "QASM",
        "bp_gate_swc": "30",
        "bp_gate_pressure_points": "20",
        "bp_gate_fluid_samples": "5",
        "bp_gate_coring_program": "No",
        "bp_gate_slides_saved": "1",
    })


def _transition(client, project_id, action, slug="business-plan-gate", **extra):
    payload = {"action": action}
    payload.update(extra)
    return client.post(
        f"/api/business-plan/wells/{project_id}/steps/{slug}/transition", json=payload)


def _approve_every_other_task(client, project_id, except_name):
    """Leave exactly one applicable step open, so the next approval is the one
    that completes the project."""
    conn = raw_sqlite_connect(client.db_path)
    with conn:
        conn.execute("UPDATE project_tasks SET status = 'Approved' "
                     "WHERE project_id = ? AND task_name != ?", (project_id, except_name))
    conn.close()


def _completed_at(client, project_id):
    conn = raw_sqlite_connect(client.db_path)
    try:
        return conn.execute("SELECT completed_at FROM projects WHERE project_id = ?",
                            (project_id,)).fetchone()["completed_at"]
    finally:
        conn.close()


def test_bpe_approval_stamps_and_reopening_clears_project_completion(client):
    """The BPE transition runs the same derived completion stamp every other
    write does: approving the last open applicable step completes the project,
    and reopening it un-completes it."""
    project_id = _bp_project(client)
    _submittable_gate(client, project_id)
    assert _transition(client, project_id, "submit").status_code == 200
    _approve_every_other_task(client, project_id, "BP Execution Gate")
    assert _completed_at(client, project_id) is None

    assert _transition(client, project_id, "approve").status_code == 200
    assert _completed_at(client, project_id)

    assert _transition(client, project_id, "reopen").status_code == 200
    assert _completed_at(client, project_id) is None

    # A return leaves it cleared too -- the set is not complete while the step
    # is only Ready.
    assert _transition(client, project_id, "submit").status_code == 200
    assert _transition(client, project_id, "return").status_code == 200
    assert _completed_at(client, project_id) is None


def _notifications(client, project_id):
    conn = raw_sqlite_connect(client.db_path)
    try:
        return [dict(row) for row in conn.execute(
            "SELECT recipient, actor, event, task_name, message FROM notifications "
            "WHERE project_id = ? ORDER BY id", (project_id,))]
    finally:
        conn.close()


def _map_petrophysicist(app_modules, task_names, member="Employee"):
    """Populate the bootstrap role and map the requested BPE task rows."""
    import workflow

    _main, dbmod = app_modules
    session = dbmod.new_session()
    try:
        role = workflow.domain_roles.get_role(session, "Petrophysicist")
        assert role
        memberships = workflow.domain_roles.list_memberships(
            session, role_id=role["role_id"], user_name=member)
        if not memberships:
            workflow.domain_roles.add_membership(session, member, role["role_id"])
        for task_name in task_names:
            workflow.domain_roles.set_task_mapping(session, task_name, role["role_id"])
    finally:
        session.close()


def _set_task_statuses(client, project_id, task_names, status="Approved"):
    conn = raw_sqlite_connect(client.db_path)
    try:
        with conn:
            conn.execute(
                "UPDATE project_tasks SET status = ? "
                "WHERE project_id = ? AND task_name IN (%s)"
                % ", ".join("?" for _name in task_names),
                [status, project_id] + list(task_names),
            )
    finally:
        conn.close()


def test_bpe_effective_reach_activates_quicklook_role_and_notifies(client, app_modules):
    _map_petrophysicist(app_modules, ["Quicklook Logs"])
    project_id = _bp_project(client, "BPE-ROLE-QUICKLOOK")
    _set_task_statuses(client, project_id, ["BP Execution Gate"])
    _raw_fields(client, project_id, "Well Proposal", {"well_proposal_shared": "1"})
    _raw_fields(client, project_id, "Site Preparation", {"site_preparation_shared": "1"})
    _raw_fields(client, project_id, "Approval To Drill", {"approval_to_drill_shared": "1"})
    _raw_fields(client, project_id, "GHEER", {"gheer_geophysical_shared": "1"})

    reached = _save(
        client, project_id, "gheer-inputs", "gheer_geomechanical_shared", True)
    assert reached.status_code == 200, reached.get_json()

    quicklook = get_task_by_name(client, project_id, "Quicklook Logs")
    assert quicklook["status"] == "In Progress"
    assert quicklook["assigned_to"] == "Employee"
    assert quicklook["assignees"] == [
        {"name": "Employee", "source": "role", "notified": True},
    ]
    assert [(row["recipient"], row["event"], row["task_name"])
            for row in _notifications(client, project_id)] == [
        ("Employee", "assigned", "Quicklook Logs"),
    ]
    # BPE completion remains field-derived; ordinary predecessor rows are not
    # rewritten merely to make the generic lifecycle resolver advance.
    assert get_task_by_name(client, project_id, "Well Proposal")["status"] == "Not Assigned"


def test_manual_assignment_activates_the_current_bpe_item(client):
    project_id = _bp_project(client, "BPE-MANUAL-QUICKLOOK")
    _set_task_statuses(client, project_id, ["BP Execution Gate"])
    _raw_fields(client, project_id, "Well Proposal", {"well_proposal_shared": "1"})
    _raw_fields(client, project_id, "Site Preparation", {"site_preparation_shared": "1"})
    _raw_fields(client, project_id, "Approval To Drill", {"approval_to_drill_shared": "1"})
    _raw_fields(client, project_id, "GHEER", {
        "gheer_geophysical_shared": "1",
        "gheer_geomechanical_shared": "1",
    })

    assigned = client.post(
        f"/api/business-plan/wells/{project_id}/steps/quicklook-logs/assign",
        json={"add": ["Employee"]},
    )
    assert assigned.status_code == 200, assigned.get_json()
    quicklook = get_task_by_name(client, project_id, "Quicklook Logs")
    assert quicklook["status"] == "In Progress"
    assert quicklook["assignees"] == [
        {"name": "Employee", "source": "manual", "notified": True},
    ]
    assert [(row["recipient"], row["event"], row["task_name"])
            for row in _notifications(client, project_id)] == [
        ("Employee", "assigned", "Quicklook Logs"),
    ]


def test_well_letters_items_activate_their_own_persisted_tasks(client, app_modules):
    _map_petrophysicist(app_modules, ["Site Preparation", "Approval To Drill"])
    project_id = _bp_project(client, "BPE-ROLE-WELL-LETTERS")
    _set_task_statuses(client, project_id, ["BP Execution Gate"])

    proposal_done = _save(
        client, project_id, "well-letters", "well_proposal_shared", True)
    assert proposal_done.status_code == 200, proposal_done.get_json()
    site = get_task_by_name(client, project_id, "Site Preparation")
    proposal = get_task_by_name(client, project_id, "Well Proposal")
    assert site["status"] == "In Progress"
    assert site["assigned_to"] == "Employee"
    assert proposal["status"] == "Not Assigned"
    assert proposal["assignees"] == []

    site_done = _save(
        client, project_id, "well-letters", "site_preparation_shared", True)
    assert site_done.status_code == 200, site_done.get_json()
    approval = get_task_by_name(client, project_id, "Approval To Drill")
    assert approval["status"] == "In Progress"
    assert approval["assigned_to"] == "Employee"
    assert [(row["task_name"], row["event"])
            for row in _notifications(client, project_id)] == [
        ("Site Preparation", "assigned"),
        ("Approval To Drill", "assigned"),
    ]


def test_bpe_effective_reach_activates_final_logs_role(client, app_modules):
    _map_petrophysicist(app_modules, ["Final Log Analysis"])
    project_id = _bp_project(client, "BPE-ROLE-FINAL-LOGS")
    _set_task_statuses(client, project_id, [
        "BP Execution Gate", "SAD Model", "Post-Well Outcome & Decision Gate",
    ])
    _raw_fields(client, project_id, "Well Proposal", {"well_proposal_shared": "1"})
    _raw_fields(client, project_id, "Site Preparation", {"site_preparation_shared": "1"})
    _raw_fields(client, project_id, "Approval To Drill", {"approval_to_drill_shared": "1"})
    _raw_fields(client, project_id, "GHEER", {
        "gheer_geophysical_shared": "1",
        "gheer_geomechanical_shared": "1",
    })
    _raw_fields(client, project_id, "Quicklook Logs", {
        "quicklook_pdf": "1", "quicklook_las": "1",
    })
    _raw_fields(client, project_id, "Aramco Picks", {"aap_geoknowledge_loaded": "1"})
    quicklook = _put_formations(
        client, project_id, "quicklook-logs", [_formation("Dry Hole")])
    assert quicklook.status_code == 200, quicklook.get_json()

    reached = _save(
        client, project_id, "aramco-approved-pics", "aap_petrel_loaded", True)
    assert reached.status_code == 200, reached.get_json()

    final_logs = get_task_by_name(client, project_id, "Final Log Analysis")
    assert final_logs["status"] == "In Progress"
    assert final_logs["assigned_to"] == "Employee"
    assert final_logs["assignees"] == [
        {"name": "Employee", "source": "role", "notified": True},
    ]
    assert [(row["recipient"], row["event"], row["task_name"])
            for row in _notifications(client, project_id)] == [
        ("Employee", "assigned", "Final Log Analysis"),
    ]


def _login(client, name):
    resp = client.post("/api/login", json={"name": name})
    assert resp.status_code == 200, resp.get_json()


def test_bpe_transitions_notify_the_same_people_a_lifecycle_transition_does(client):
    """Submit asks every supervisor; approve and reopen tell the assignee. The
    fan-out policy is shared with workflow/notifications.py -- the BPE state
    machine only supplies the pre-transition row. A reopen files under the
    'returned' event (the stored vocabulary is fixed by the table's CHECK
    constraint) and is told apart by its message verb."""
    _login(client, "Supervisor")
    project_id = _bp_project(client)
    _submittable_gate(client, project_id)
    assigned = client.post(
        f"/api/business-plan/wells/{project_id}/steps/business-plan-gate/assign",
        json={"assignee": "Employee"})
    assert assigned.status_code == 200, assigned.get_json()

    _login(client, "Employee")
    assert _transition(client, project_id, "submit").status_code == 200
    assert [(row["recipient"], row["event"]) for row in _notifications(client, project_id)] == [
        ("Employee", "assigned"), ("Supervisor", "submitted")]

    _login(client, "Supervisor")
    assert _transition(client, project_id, "approve").status_code == 200
    assert _transition(client, project_id, "reopen").status_code == 200
    rows = _notifications(client, project_id)
    assert [(row["recipient"], row["actor"], row["event"]) for row in rows] == [
        ("Employee", "Supervisor", "assigned"),
        ("Supervisor", "Employee", "submitted"),
        ("Employee", "Supervisor", "approved"),
        ("Employee", "Supervisor", "returned"),
    ]
    assert "reopened for update" in rows[-1]["message"]
    assert {row["task_name"] for row in rows} == {"BP Execution Gate"}


def test_bpe_assignment_uses_the_shared_manual_group(client):
    _login(client, "Supervisor")
    project_id = _bp_project(client, "BPE-GROUP-1")
    endpoint = f"/api/business-plan/wells/{project_id}/steps/business-plan-gate/assign"

    response = client.post(endpoint, json={"add": ["Employee", "Staff Member"]})
    assert response.status_code == 200, response.get_json()
    task = response.get_json()["detail"]["task"]
    assert [member["name"] for member in task["assignees"]] == ["Employee", "Staff Member"]
    assert {member["source"] for member in task["assignees"]} == {"manual"}
    assert task["status"] == "In Progress"

    response = client.post(endpoint, json={"remove": ["Employee"]})
    assert response.status_code == 200, response.get_json()
    task = response.get_json()["detail"]["task"]
    assert task["assignees"] == [{"name": "Staff Member", "source": "manual", "notified": True}]

    dashboard = client.get(f"/api/business-plan/dashboard?year={date.today().year}").get_json()
    well = next(row for row in dashboard["wells"] if row["project_id"] == project_id)
    assert well["assignees"] == ["Staff Member"]


def test_bpe_un_approving_actions_are_supervisor_only_at_the_route(client):
    """approve / return / reopen all un-approve or grant an approval, so the
    route gates them exactly like POST /api/tasks/<id>/transition does."""
    _login(client, "Supervisor")
    project_id = _bp_project(client)
    _submittable_gate(client, project_id)
    assert _transition(client, project_id, "submit").status_code == 200

    _login(client, "Employee")
    assert _transition(client, project_id, "approve").status_code == 403
    assert _transition(client, project_id, "return").status_code == 403

    _login(client, "Supervisor")
    assert _transition(client, project_id, "approve").status_code == 200
    _login(client, "Employee")
    reopened = _transition(client, project_id, "reopen")
    assert reopened.status_code == 403
    assert "supervisor" in reopened.get_json()["detail"].lower()


def test_detail_carries_the_well_summary_bundle_for_the_card_beside_the_step(client):
    """Card 3E: the Well Summary panel is the maturation shell's own card, so
    every step's payload carries the four record-level inputs that card reads.

    They ride on THIS payload rather than a second request, which is what keeps
    the panel and the step it sits beside on one vintage.
    """
    project_id = _bp_project(client)
    # Values the card reads, each from a step that is NOT the one being opened:
    # the bundle is record-level, not step-level.
    _raw_fields(client, project_id, "SAD Model", {
        "post_drill_piip_gas_p90": "90",
        "post_drill_piip_gas_mean": "116",
        "post_drill_piip_gas_p10": "140",
    })
    _raw_fields(client, project_id, "Quicklook Logs", {"quicklook_fluid_type": "Gas"})
    client.put(f"/api/business-plan/wells/{project_id}/steps/quicklook-logs/formations",
               json={"rows": [_formation("Gas")]})

    detail = client.get(
        f"/api/business-plan/wells/{project_id}/steps/business-plan-gate").get_json()
    bundle = detail["well_summary"]
    assert set(bundle) == {"fields", "formations", "lead_summary", "derisking"}

    # The field map is record-wide, so a step's own payload can answer for the
    # whole well.
    assert bundle["fields"]["SAD Model"]["post_drill_piip_gas_mean"] == "116"
    assert bundle["fields"]["Quicklook Logs"]["quicklook_fluid_type"] == "Gas"

    # EVERY phase, unlike the step-scoped `formations` list beside it: this step
    # has no formations of its own, and the card still resolves the well's.
    assert detail["formations"] == []
    assert [(row["formation"], row["phase"]) for row in bundle["formations"]] == [("SARH", "quicklook")]

    # A well that was never matured through the lead phase has no snapshot --
    # the key is present and null rather than absent, so the card renders its
    # Lead Summary fold with dashes instead of failing to render.
    assert bundle["lead_summary"] is None
    # Total CoS is computed server-side (there is no client-side second formula)
    # and is blank until the lead CoS steps are scored.
    assert bundle["derisking"] == ""


def test_the_board_opens_on_every_step(client):
    """The Step filter defaults to `all`; narrowing to the gate is the
    Pre-Drilling column's own toggle (see the gate-status test below).

    The default used to be `business-plan-gate`, which is a STEP filter wearing
    a gate's name: it restricted every caller to wells whose current stage still
    holds that item, so Post-Drilling and Post-Testing looked permanently empty.
    """
    project_id = _bp_project(client, "BPE-GATE")
    year = date.today().year

    default_body = client.get(f"/api/business-plan/dashboard?year={year}").get_json()
    explicit_all = client.get(f"/api/business-plan/dashboard?year={year}&step=all").get_json()
    assert [row["project_id"] for row in default_body["wells"]] == \
        [row["project_id"] for row in explicit_all["wells"]], \
        "omitting the step filter is the same request as asking for all steps"

    assert project_id in [row["project_id"] for row in default_body["wells"]]

    # The filter itself is untouched -- asking for one step still narrows to it.
    narrowed = client.get(
        f"/api/business-plan/dashboard?year={year}&step=business-plan-gate").get_json()
    assert project_id in [row["project_id"] for row in narrowed["wells"]]
    assert all("business-plan-gate" in {item["key"] for item in row["items"]}
               for row in narrowed["wells"])


def test_all_years_shows_wells_from_every_year_at_once(client):
    """The year filter defaults to the current year and always did. "All Years"
    is a real option rather than a cleared filter, so it travels as the literal
    `all` and the year comparison is skipped entirely.
    """
    near = _bp_project(client, "BPE-YEAR-NEAR", year=date.today().year)
    far = _bp_project(client, "BPE-YEAR-FAR", year=2033)

    this_year = client.get(
        f"/api/business-plan/dashboard?year={date.today().year}").get_json()
    visible = {row["project_id"] for row in this_year["wells"]}
    assert near in visible and far not in visible

    every_year = client.get("/api/business-plan/dashboard?year=all").get_json()
    visible = {row["project_id"] for row in every_year["wells"]}
    assert near in visible and far in visible

    # A year that is neither a number nor the sentinel still matches nothing,
    # rather than falling through to "show everything".
    nonsense = client.get("/api/business-plan/dashboard?year=every").get_json()
    assert nonsense["wells"] == []


# ---------------------------------------------------------------------------
# The Pre-Drilling column's BP Gate toggle, and Active Drilling
# ---------------------------------------------------------------------------

def test_a_well_states_its_business_plan_gate_status_for_the_column_toggle(client):
    """The toggle shows wells whose gate is approved or awaiting approval, so
    the payload states the gate item's own effective status rather than a
    boolean that answers a different question.

    It used to publish `at_business_plan_gate` (= "not Completed"), which every
    well matched until someone approved a gate -- so the toggle looked dead on a
    board where nothing had been submitted yet.
    """
    project_id = _bp_project(client, "BPE-GATE")

    def gate_status():
        body = client.get(
            f"/api/business-plan/dashboard?year={date.today().year}").get_json()
        well = next(row for row in body["wells"] if row["project_id"] == project_id)
        # It is the gate ITEM's status, not a second derivation of it.
        assert well["bp_gate_status"] == well["all_states"]["business-plan-gate"]["status"]
        return well["bp_gate_status"]

    assert gate_status() == "In Progress"
    _submittable_gate(client, project_id)
    assert _transition(client, project_id, "submit").status_code == 200
    assert gate_status() == "Pending Approval"
    assert _transition(client, project_id, "approve").status_code == 200
    assert gate_status() == "Completed"


def test_only_a_post_drilling_well_can_be_marked_as_actively_drilling(client):
    """The rule lives on the WRITE, not in the gear menu that offers it: a
    direct PATCH from anywhere has to meet it too.
    """
    project_id = _bp_project(client, "BPE-DRILL")

    def flags(value):
        return client.patch(f"/api/projects/{project_id}/flags",
                            json={"active_drilling": value, "changed_by": "Supervisor"})

    def detail():
        return client.get(
            f"/api/business-plan/wells/{project_id}/steps/business-plan-gate").get_json()

    # Pre-Drilling: refused, and the step page says so before anyone tries.
    assert detail()["project"]["stage_key"] == "pre_drilling"
    assert detail()["project"]["active_drilling_allowed"] is False
    refused = flags(True)
    assert refused.status_code == 400
    assert "Post-Drilling" in refused.get_json()["detail"]
    assert detail()["project"]["active_drilling"] == 0

    # Drive the well into Post-Drilling: every Pre-Drilling item complete.
    _submittable_gate(client, project_id)
    assert _transition(client, project_id, "submit").status_code == 200
    assert _transition(client, project_id, "approve").status_code == 200
    for key in ("well_proposal_shared", "site_preparation_shared", "approval_to_drill_shared"):
        _save(client, project_id, "well-letters", key, True)
    for key in ("gheer_geophysical_shared", "gheer_geomechanical_shared"):
        _save(client, project_id, "gheer-inputs", key, True)
    assert detail()["project"]["stage_key"] == "post_drilling"
    assert detail()["project"]["active_drilling_allowed"] is True

    # Now it is allowed, audited once, and visible on the board row that draws
    # the animated border.
    assert flags(True).status_code == 200
    assert detail()["project"]["active_drilling"] == 1
    board = client.get(f"/api/business-plan/dashboard?year={date.today().year}").get_json()
    row = next(well for well in board["wells"] if well["project_id"] == project_id)
    assert row["active_drilling"] == 1 and row["stage_key"] == "post_drilling"
    events = [event for event in _history(client, project_id)
              if event["action_type"] == "Active Drilling Flag"]
    assert len(events) == 1
    # Saving the same state again is not an event.
    assert flags(True).status_code == 200
    assert len([event for event in _history(client, project_id)
                if event["action_type"] == "Active Drilling Flag"]) == 1


def test_active_drilling_can_always_be_turned_off_and_needs_a_supervisor(client):
    """Clearing the flag is never blocked by the stage rule -- a well that moved
    on must not be stuck reading "drilling" -- and setting it is supervisor-only
    at the route, which is where authorization belongs.
    """
    project_id = _bp_project(client, "BPE-DRILL-OFF")
    _submittable_gate(client, project_id)
    _transition(client, project_id, "submit")
    _transition(client, project_id, "approve")
    for key in ("well_proposal_shared", "site_preparation_shared", "approval_to_drill_shared"):
        _save(client, project_id, "well-letters", key, True)
    for key in ("gheer_geophysical_shared", "gheer_geomechanical_shared"):
        _save(client, project_id, "gheer-inputs", key, True)
    assert client.patch(f"/api/projects/{project_id}/flags",
                        json={"active_drilling": True}).status_code == 200

    # Move the well on to Post-Testing; the flag is still clearable there.
    _save(client, project_id, "quicklook-logs", "quicklook_pdf", True)
    client.post("/api/login", json={"name": "Employee"})
    denied = client.patch(f"/api/projects/{project_id}/flags", json={"active_drilling": False})
    assert denied.status_code == 403
    client.post("/api/logout", json={})
    assert client.patch(f"/api/projects/{project_id}/flags",
                        json={"active_drilling": False}).status_code == 200
    project = client.get(f"/api/projects/{project_id}").get_json()
    assert project["active_drilling"] == 0
    # The maturation gear reads the same availability fact off this payload.
    assert "active_drilling_allowed" in project
