"""Tests for well-level formation data (WS1, project_formations).

Formation interpretation values (the canonical SARH / QASM / QWRH trio, plus
custom user-entered names) belong to the WELL, not to a workflow step:
GET/PUT /api/projects/<id>/formations, upsert keyed by (project, formation,
phase), phase-scoped full replacement, strict validation (including numeric
coercion -- measurement fields are REAL columns), one history event against
the source task, and /detail exposure.
"""
from __future__ import annotations

from conftest import create_project, get_task_by_name, get_tasks

SARH_ROW = {
    "formation": "SARH",
    "top_tvdss_ft": "10500",
    "base_tvdss_ft": "10620",
    "thickness_ft": "120",
    "porosity_pct": "8.5",
    "swt_pct": "35",
    "pay_ft": "60",
    "ngr_pct": "12",
    "fluid": "Gas",
}


def _put(client, pid, phase, rows, source_task_id=None):
    payload = {"phase": phase, "rows": rows}
    if source_task_id is not None:
        payload["source_task_id"] = source_task_id
    return client.put(f"/api/projects/{pid}/formations", json=payload)


# ---------------------------------------------------------------------------
# GET / PUT round trip
# ---------------------------------------------------------------------------

def test_get_formations_empty_for_new_project(client):
    pid = create_project(client, "FORM-EMPTY-1")
    resp = client.get(f"/api/projects/{pid}/formations")
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_get_formations_unknown_project_404(client):
    assert client.get("/api/projects/999999/formations").status_code == 404


def test_put_get_round_trip_both_phases(client):
    pid = create_project(client, "FORM-ROUNDTRIP-1")
    resp = _put(client, pid, "quicklook", [SARH_ROW, {"formation": "QASM", "pay_ft": "20"}])
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True

    final_row = dict(SARH_ROW)
    final_row["pay_ft"] = "72"
    resp = _put(client, pid, "final", [final_row])
    assert resp.status_code == 200

    rows = client.get(f"/api/projects/{pid}/formations").get_json()
    assert len(rows) == 3
    by_key = {(r["phase"], r["formation"]): r for r in rows}
    assert by_key[("quicklook", "SARH")]["top_tvdss_ft"] == 10500.0
    assert by_key[("quicklook", "SARH")]["fluid"] == "Gas"
    # Absent numeric fields are stored as NULL (full-row replacement semantics);
    # fluid (TEXT) still defaults to ''.
    assert by_key[("quicklook", "QASM")]["pay_ft"] == 20.0
    assert by_key[("quicklook", "QASM")]["top_tvdss_ft"] is None
    assert by_key[("quicklook", "QASM")]["fluid"] == ""
    assert by_key[("final", "SARH")]["pay_ft"] == 72.0


def test_upsert_overwrites_not_duplicates(client):
    pid = create_project(client, "FORM-UPSERT-1")
    _put(client, pid, "quicklook", [SARH_ROW])
    updated = dict(SARH_ROW)
    updated["pay_ft"] = "99"
    resp = _put(client, pid, "quicklook", [updated])
    assert resp.status_code == 200

    rows = client.get(f"/api/projects/{pid}/formations").get_json()
    assert len(rows) == 1  # upsert, not append
    assert rows[0]["pay_ft"] == 99.0


def test_formation_rows_ordered_by_phase_then_canonical_formation(client):
    pid = create_project(client, "FORM-ORDER-1")
    _put(client, pid, "quicklook", [
        {"formation": "QWRH"}, {"formation": "SARH"}, {"formation": "QASM"},
    ])
    rows = client.get(f"/api/projects/{pid}/formations").get_json()
    assert [r["formation"] for r in rows] == ["SARH", "QASM", "QWRH"]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_custom_formation_normalized_and_ordered_after_canonical_trio(client):
    pid = create_project(client, "FORM-CUSTOM-1")
    resp = _put(client, pid, "quicklook", [
        {"formation": "QWRH"}, {"formation": "SARH"},
        {"formation": "  unayzah  "},
    ])
    assert resp.status_code == 200

    rows = client.get(f"/api/projects/{pid}/formations").get_json()
    assert [r["formation"] for r in rows] == ["SARH", "QWRH", "UNAYZAH"]


def test_blank_custom_formation_name_rejected(client):
    pid = create_project(client, "FORM-BAD-1")
    resp = _put(client, pid, "quicklook", [{"formation": "   "}])
    assert resp.status_code == 400
    assert "required" in resp.get_json()["detail"].lower()


def test_overlong_custom_formation_name_rejected(client):
    pid = create_project(client, "FORM-BAD-1B")
    resp = _put(client, pid, "quicklook", [{"formation": "X" * 41}])
    assert resp.status_code == 400
    assert "40 characters" in resp.get_json()["detail"]


def test_unknown_phase_rejected(client):
    pid = create_project(client, "FORM-BAD-2")
    resp = _put(client, pid, "post_test", [SARH_ROW])
    assert resp.status_code == 400
    assert "Unknown phase" in resp.get_json()["detail"]


def test_round_trip_post_drill_and_resource_update_phases(client):
    pid = create_project(client, "FORM-PHASE-1")
    resp = _put(client, pid, "post_drill", [SARH_ROW])
    assert resp.status_code == 200
    resp = _put(client, pid, "resource_update", [{"formation": "QASM", "pay_ft": "15"}])
    assert resp.status_code == 200

    rows = client.get(f"/api/projects/{pid}/formations").get_json()
    by_key = {(r["phase"], r["formation"]): r for r in rows}
    assert by_key[("post_drill", "SARH")]["pay_ft"] == 60.0
    assert by_key[("resource_update", "QASM")]["pay_ft"] == 15.0
    # Pipeline order: quicklook, post_drill, final, resource_update.
    assert [r["phase"] for r in rows] == ["post_drill", "resource_update"]


def test_put_is_phase_scoped_full_replacement(client):
    pid = create_project(client, "FORM-REPLACE-1")
    _put(client, pid, "quicklook", [{"formation": "SARH"}, {"formation": "QASM"}])
    _put(client, pid, "final", [{"formation": "SARH"}])

    resp = _put(client, pid, "quicklook", [{"formation": "SARH"}])
    assert resp.status_code == 200

    rows = client.get(f"/api/projects/{pid}/formations").get_json()
    by_key = {(r["phase"], r["formation"]) for r in rows}
    assert by_key == {("quicklook", "SARH"), ("final", "SARH")}
    # QASM was dropped from quicklook; the untouched 'final' phase survives.


def test_duplicate_formation_in_payload_rejected(client):
    pid = create_project(client, "FORM-DUP-1")
    resp = _put(client, pid, "quicklook", [SARH_ROW])
    assert resp.status_code == 200

    # Two payload rows normalizing to the same name must 400 -- under the old
    # last-wins collapse the full-replacement DELETE would then have dropped
    # the user's original row (data loss from one mis-click).
    dup = dict(SARH_ROW)
    dup["pay_ft"] = "10"
    resp = _put(client, pid, "quicklook", [{"formation": "  sarh "}, dup])
    assert resp.status_code == 400
    assert "Duplicate formation 'SARH'" in resp.get_json()["detail"]

    # The stored row is untouched: rejected before any write.
    rows = client.get(f"/api/projects/{pid}/formations").get_json()
    assert len(rows) == 1
    assert rows[0]["pay_ft"] == 60.0


def test_unknown_field_rejected_not_silently_dropped(client):
    pid = create_project(client, "FORM-BAD-3")
    resp = _put(client, pid, "quicklook", [{"formation": "SARH", "porosityy_pct": "9"}])
    assert resp.status_code == 400
    assert "porosityy_pct" in resp.get_json()["detail"]


def test_put_formations_unknown_project_400(client):
    resp = _put(client, 999999, "quicklook", [SARH_ROW])
    assert resp.status_code == 400
    assert resp.get_json()["detail"] == "Lead / well not found."


def test_non_numeric_measurement_value_rejected(client):
    pid = create_project(client, "FORM-BAD-4")
    resp = _put(client, pid, "quicklook", [{"formation": "SARH", "porosity_pct": "not-a-number"}])
    assert resp.status_code == 400
    assert "porosity_pct" in resp.get_json()["detail"]


def test_negative_measurement_rejected_except_tvdss(client):
    """A thickness cannot be negative; a TVDSS above datum legitimately is.

    The client emits min="0" on exactly the same set (schema.js allowNegative
    / detail-form.js), so this is the server half of one rule, not a second
    opinion.
    """
    pid = create_project(client, "FORM-NEG-1")
    resp = _put(client, pid, "quicklook", [{"formation": "SARH", "thickness_ft": "-5"}])
    assert resp.status_code == 400
    assert "thickness_ft" in resp.get_json()["detail"]
    assert "negative" in resp.get_json()["detail"].lower()

    resp = _put(client, pid, "quicklook", [{"formation": "SARH", "top_tvdss_ft": "-120"}])
    assert resp.status_code == 200, "TVDSS above datum is signed on purpose"
    rows = client.get(f"/api/projects/{pid}/formations").get_json()
    assert rows[0]["top_tvdss_ft"] == -120


def test_percentage_measurement_cannot_exceed_100(client):
    pid = create_project(client, "FORM-PCT-1")
    resp = _put(client, pid, "quicklook", [{"formation": "SARH", "porosity_pct": "140"}])
    assert resp.status_code == 400
    assert "porosity_pct" in resp.get_json()["detail"]
    # The boundary itself is a legal reading.
    assert _put(client, pid, "quicklook",
                [{"formation": "SARH", "porosity_pct": "100"}]).status_code == 200


def test_pay_interval_measurements_run_the_same_rules(client):
    pid = create_project(client, "PAYINT-RANGE-1")
    resp = _put(client, pid, "quicklook",
                [dict(SARH_ROW, pay_intervals=[dict(INTERVAL_A, kint_md="-3")])])
    assert resp.status_code == 400
    assert "kint_md" in resp.get_json()["detail"]
    resp = _put(client, pid, "quicklook",
                [dict(SARH_ROW, pay_intervals=[dict(INTERVAL_A, swt_pct="101")])])
    assert resp.status_code == 400
    assert "swt_pct" in resp.get_json()["detail"]


def test_blank_measurement_value_stored_as_null(client):
    pid = create_project(client, "FORM-BLANK-1")
    resp = _put(client, pid, "quicklook", [{"formation": "SARH", "top_tvdss_ft": "  "}])
    assert resp.status_code == 200
    rows = client.get(f"/api/projects/{pid}/formations").get_json()
    assert rows[0]["top_tvdss_ft"] is None


# ---------------------------------------------------------------------------
# Detail payload + history
# ---------------------------------------------------------------------------

def test_detail_payload_includes_formations(client):
    pid = create_project(client, "FORM-DETAIL-1")
    _put(client, pid, "quicklook", [SARH_ROW])
    detail = client.get(f"/api/projects/{pid}/detail").get_json()
    assert "formations" in detail
    assert len(detail["formations"]) == 1
    assert detail["formations"][0]["formation"] == "SARH"
    assert detail["formations"][0]["phase"] == "quicklook"


def test_history_event_logged_against_source_task(client):
    pid = create_project(client, "FORM-HISTORY-1")
    quicklook = get_task_by_name(client, pid, "Quicklook Logs")
    resp = _put(client, pid, "quicklook", [SARH_ROW, {"formation": "QWRH", "pay_ft": "5"}],
                source_task_id=quicklook["task_id"])
    assert resp.status_code == 200

    events = client.get(f"/api/activity?project_id={pid}").get_json()
    formation_events = [e for e in events if e["action_type"] == "Formation Data Updated"]
    assert len(formation_events) == 1  # ONE event per PUT, not per row
    event = formation_events[0]
    assert event["task_name"] == "Quicklook Logs"
    assert "SARH" in event["comment"] and "QWRH" in event["comment"]
    assert "quicklook" in event["comment"]


def test_deletion_only_put_logs_history_event(client):
    pid = create_project(client, "FORM-HISTORY-3")
    quicklook = get_task_by_name(client, pid, "Quicklook Logs")
    resp = _put(client, pid, "quicklook", [SARH_ROW, {"formation": "QWRH", "pay_ft": "5"}],
                source_task_id=quicklook["task_id"])
    assert resp.status_code == 200

    # An empty payload clears the phase: rows only get DELETEd, yet the change
    # must still land in the audit trail (the log used to fire only when the
    # payload carried rows).
    resp = _put(client, pid, "quicklook", [], source_task_id=quicklook["task_id"])
    assert resp.status_code == 200
    assert client.get(f"/api/projects/{pid}/formations").get_json() == []

    events = client.get(f"/api/activity?project_id={pid}").get_json()
    formation_events = [e for e in events if e["action_type"] == "Formation Data Updated"]
    assert len(formation_events) == 2  # one for the upsert, one for the clearing PUT
    removal = next(e for e in formation_events if "Removed" in e["comment"])
    assert removal["task_name"] == "Quicklook Logs"
    assert "SARH" in removal["comment"] and "QWRH" in removal["comment"]
    assert "quicklook" in removal["comment"]


def test_no_history_event_without_source_task(client):
    pid = create_project(client, "FORM-HISTORY-2")
    _put(client, pid, "quicklook", [SARH_ROW])
    events = client.get(f"/api/activity?project_id={pid}").get_json()
    assert not [e for e in events if e["action_type"] == "Formation Data Updated"]


# ---------------------------------------------------------------------------
# Pay intervals (project_formation_pay_intervals)
# ---------------------------------------------------------------------------
# A formation keeps its envelope (top/base/thickness) and carries zero or more
# pay intervals, each with its own top/base + Phit/Swt/NGR/Kint/fluid. They ride
# inside the formation row's optional `pay_intervals` array: a full replacement
# within the (project, formation, phase) scope, seq assigned from array order,
# with the KEY'S ABSENCE meaning "leave the stored intervals alone".

INTERVAL_A = {
    "top_tvdss_ft": "10520",
    "base_tvdss_ft": "10545",
    "phit_pct": "9.2",
    "swt_pct": "32",
    "ngr_pct": "80",
    "kint_md": "1.4",
    "fluid": "Gas",
}
INTERVAL_B = {"top_tvdss_ft": "10560", "base_tvdss_ft": "10580", "phit_pct": "7",
              "fluid": "Water Bearing"}


def _formation(client, pid, phase, name):
    rows = client.get(f"/api/projects/{pid}/formations").get_json()
    return next((r for r in rows if r["phase"] == phase and r["formation"] == name), None)


def test_pay_intervals_round_trip_in_payload_order(client):
    pid = create_project(client, "PAYINT-ROUNDTRIP-1")
    row = dict(SARH_ROW, pay_intervals=[INTERVAL_A, INTERVAL_B])
    assert _put(client, pid, "quicklook", [row]).status_code == 200

    stored = _formation(client, pid, "quicklook", "SARH")
    intervals = stored["pay_intervals"]
    assert [i["seq"] for i in intervals] == [1, 2]  # seq follows array order, 1-based
    assert intervals[0]["top_tvdss_ft"] == 10520.0  # coerced to REAL like formation metrics
    assert intervals[0]["kint_md"] == 1.4
    assert intervals[0]["fluid"] == "Gas"
    # Absent numeric keys land as NULL, not as junk.
    assert intervals[1]["ngr_pct"] is None
    assert intervals[1]["fluid"] == "Water Bearing"
    # The envelope itself is untouched by the intervals.
    assert stored["thickness_ft"] == 120.0


def test_formations_without_intervals_report_an_empty_list(client):
    pid = create_project(client, "PAYINT-EMPTY-1")
    assert _put(client, pid, "quicklook", [SARH_ROW]).status_code == 200
    assert _formation(client, pid, "quicklook", "SARH")["pay_intervals"] == []


def test_pay_intervals_are_replaced_not_appended(client):
    pid = create_project(client, "PAYINT-REPLACE-1")
    _put(client, pid, "quicklook", [dict(SARH_ROW, pay_intervals=[INTERVAL_A, INTERVAL_B])])
    # A shorter list must leave no stale tail behind (seq 2 is gone, not orphaned).
    _put(client, pid, "quicklook", [dict(SARH_ROW, pay_intervals=[INTERVAL_B])])
    intervals = _formation(client, pid, "quicklook", "SARH")["pay_intervals"]
    assert len(intervals) == 1
    assert intervals[0]["seq"] == 1
    assert intervals[0]["fluid"] == "Water Bearing"
    # An empty array clears them outright.
    _put(client, pid, "quicklook", [dict(SARH_ROW, pay_intervals=[])])
    assert _formation(client, pid, "quicklook", "SARH")["pay_intervals"] == []


def test_omitting_the_key_leaves_stored_intervals_alone(client):
    """Callers that predate pay intervals (the Excel import's SARH merge, the
    seed script, the post_drill / resource_update panels) send formation rows
    with no `pay_intervals` key at all -- that must never wipe them."""
    pid = create_project(client, "PAYINT-OMIT-1")
    _put(client, pid, "quicklook", [dict(SARH_ROW, pay_intervals=[INTERVAL_A])])
    _put(client, pid, "quicklook", [dict(SARH_ROW, pay_ft="99")])
    stored = _formation(client, pid, "quicklook", "SARH")
    assert stored["pay_ft"] == 99.0
    assert len(stored["pay_intervals"]) == 1


def test_pay_intervals_are_scoped_per_formation_and_phase(client):
    pid = create_project(client, "PAYINT-SCOPE-1")
    _put(client, pid, "quicklook", [dict(SARH_ROW, pay_intervals=[INTERVAL_A]),
                                    {"formation": "QASM", "pay_intervals": [INTERVAL_B]}])
    _put(client, pid, "final", [dict(SARH_ROW, pay_intervals=[INTERVAL_B, INTERVAL_A])])
    assert len(_formation(client, pid, "quicklook", "SARH")["pay_intervals"]) == 1
    assert len(_formation(client, pid, "quicklook", "QASM")["pay_intervals"]) == 1
    assert len(_formation(client, pid, "final", "SARH")["pay_intervals"]) == 2
    # Rewriting one formation leaves the sibling formation and the other phase alone.
    _put(client, pid, "quicklook", [dict(SARH_ROW, pay_intervals=[]),
                                    {"formation": "QASM", "pay_intervals": [INTERVAL_B]}])
    assert _formation(client, pid, "quicklook", "SARH")["pay_intervals"] == []
    assert len(_formation(client, pid, "quicklook", "QASM")["pay_intervals"]) == 1
    assert len(_formation(client, pid, "final", "SARH")["pay_intervals"]) == 2


def test_dropping_a_formation_drops_its_pay_intervals(client):
    """The phase-scoped full replacement deletes the formation row; its
    intervals must go with it instead of lingering and reattaching if the
    formation name comes back."""
    pid = create_project(client, "PAYINT-ORPHAN-1")
    _put(client, pid, "quicklook", [dict(SARH_ROW, pay_intervals=[INTERVAL_A]),
                                    {"formation": "QWRH", "pay_intervals": [INTERVAL_B]}])
    _put(client, pid, "quicklook", [dict(SARH_ROW, pay_intervals=[INTERVAL_A])])
    assert _formation(client, pid, "quicklook", "QWRH") is None
    # Re-adding the formation comes back clean.
    _put(client, pid, "quicklook", [dict(SARH_ROW, pay_intervals=[INTERVAL_A]),
                                    {"formation": "QWRH", "pay_ft": "5"}])
    assert _formation(client, pid, "quicklook", "QWRH")["pay_intervals"] == []


def test_clearing_a_phase_clears_its_pay_intervals(client):
    pid = create_project(client, "PAYINT-CLEARPHASE-1")
    _put(client, pid, "quicklook", [dict(SARH_ROW, pay_intervals=[INTERVAL_A])])
    assert _put(client, pid, "quicklook", []).status_code == 200
    assert client.get(f"/api/projects/{pid}/formations").get_json() == []


def test_pay_interval_unknown_field_rejected(client):
    pid = create_project(client, "PAYINT-BADKEY-1")
    resp = _put(client, pid, "quicklook",
                [dict(SARH_ROW, pay_intervals=[dict(INTERVAL_A, porosity_pct="8")])])
    assert resp.status_code == 400
    assert "porosity_pct" in resp.get_json()["detail"]
    assert _formation(client, pid, "quicklook", "SARH") is None  # nothing was written


def test_pay_interval_non_numeric_rejected(client):
    pid = create_project(client, "PAYINT-BADNUM-1")
    resp = _put(client, pid, "quicklook",
                [dict(SARH_ROW, pay_intervals=[dict(INTERVAL_A, kint_md="tight")])])
    assert resp.status_code == 400
    assert "kint_md" in resp.get_json()["detail"]


def test_pay_interval_fluid_must_come_from_the_vocabulary(client):
    pid = create_project(client, "PAYINT-BADFLUID-1")
    resp = _put(client, pid, "quicklook",
                [dict(SARH_ROW, pay_intervals=[dict(INTERVAL_A, fluid="Sludge")])])
    assert resp.status_code == 400
    assert "fluid" in resp.get_json()["detail"].lower()
    # Casing slips are normalized back to the canonical spelling, not rejected.
    assert _put(client, pid, "quicklook",
                [dict(SARH_ROW, pay_intervals=[dict(INTERVAL_A, fluid="gas over water")])]
                ).status_code == 200
    assert _formation(client, pid, "quicklook", "SARH")["pay_intervals"][0]["fluid"] == "Gas over Water"


def test_legacy_pay_interval_fluid_labels_map_forward(client):
    """A pre-v10 client's spelling is accepted, but it is not what gets STORED:
    the alias resolves to the replacement label, the same four-way mapping
    migration v10 applies to rows already in the database."""
    pid = create_project(client, "PAYINT-LEGACY-1")
    for legacy, current in (("Dry", "Dry Hole"), ("Water", "Water Bearing"),
                            ("Condensate", "Oil over Gas"), ("Liquid", "Oil")):
        assert _put(client, pid, "quicklook",
                    [dict(SARH_ROW, pay_intervals=[dict(INTERVAL_A, fluid=legacy)])]
                    ).status_code == 200
        stored = _formation(client, pid, "quicklook", "SARH")["pay_intervals"][0]["fluid"]
        assert stored == current, (legacy, stored)


def test_pay_intervals_must_be_a_list_of_objects(client):
    pid = create_project(client, "PAYINT-SHAPE-1")
    assert _put(client, pid, "quicklook", [dict(SARH_ROW, pay_intervals="nope")]).status_code == 400
    assert _put(client, pid, "quicklook", [dict(SARH_ROW, pay_intervals=["nope"])]).status_code == 400


def test_pay_intervals_rejected_for_an_unknown_phase(client):
    pid = create_project(client, "PAYINT-BADPHASE-1")
    resp = _put(client, pid, "nonsense", [dict(SARH_ROW, pay_intervals=[INTERVAL_A])])
    assert resp.status_code == 400


def test_detail_payload_carries_pay_intervals(client):
    pid = create_project(client, "PAYINT-DETAIL-1")
    _put(client, pid, "final", [dict(SARH_ROW, pay_intervals=[INTERVAL_A])])
    detail = client.get(f"/api/projects/{pid}/detail").get_json()
    assert detail["formations"][0]["pay_intervals"][0]["phit_pct"] == 9.2


def test_pay_interval_write_records_source_task_and_actor(client):
    pid = create_project(client, "PAYINT-SOURCE-1")
    final = get_task_by_name(client, pid, "Final Log Analysis")
    _put(client, pid, "final", [dict(SARH_ROW, pay_intervals=[INTERVAL_A])],
         source_task_id=final["task_id"])
    interval = _formation(client, pid, "final", "SARH")["pay_intervals"][0]
    assert interval["source_task_id"] == final["task_id"]
    assert interval["updated_at"]


# ---------------------------------------------------------------------------
# Non-prospective auto-completion (the "BP pipeline" rule)
#
# A quicklook interpretation showing EXACTLY ONE formation whose fluid is Water
# or Dry proves the well non-prospective; the remaining BP paperwork steps are
# then formalities and are driven to Approved automatically, by WALKING the
# state machine as the System identity (never a raw status write).
# ---------------------------------------------------------------------------

AUTO_STEPS = ["Executive Summary", "Flowback Results", "SAD Update", "PVAD Structural MTR"]


def _bp_project(client, name):
    return create_project(client, name, pipeline_type="bp",
                          business_plan_enabled=True, business_plan_year=2029)


def _water_row(fluid="Water"):
    return {"formation": "SARH", "top_tvdss_ft": "9000", "pay_ft": "0", "fluid": fluid}


def _statuses(client, pid):
    return {t["task_name"]: t["status"] for t in get_tasks(client, pid)}


def _auto_events(client, pid):
    return [e for e in client.get(f"/api/activity?project_id={pid}").get_json()
            if e["action_type"] == "Auto-Completed"]


def test_single_water_quicklook_formation_auto_completes_the_bp_paperwork(client):
    """The whole rule end to end: four steps Approved, one distinct history
    event each naming the fluid, and NOTHING else touched."""
    pid = _bp_project(client, "AUTO-WATER-1")
    quicklook = get_task_by_name(client, pid, "Quicklook Logs")
    resp = _put(client, pid, "quicklook", [_water_row()], source_task_id=quicklook["task_id"])
    assert resp.status_code == 200

    statuses = _statuses(client, pid)
    for name in AUTO_STEPS:
        assert statuses[name] == "Approved", (name, statuses[name])
    # Every other BP step is left exactly as materialized -- the rule closes
    # the paperwork formalities, not the pipeline.
    for name in ("BP Execution Gate", "Quicklook Logs", "SAD Model", "Final Log Analysis", "PDA"):
        assert statuses[name] == "Not Assigned", (name, statuses[name])

    events = _auto_events(client, pid)
    assert sorted(e["task_name"] for e in events) == sorted(AUTO_STEPS)
    for event in events:
        assert event["comment"] == (
            "Auto-completed: single quicklook formation with non-hydrocarbon fluid (Water)")
        assert event["new_status"] == "Approved"
        assert event["changed_by"] == "System"

    # The walk really happened (assign -> submit -> approve), so each step
    # carries the full trail rather than a teleport to Approved.
    all_events = client.get(f"/api/activity?project_id={pid}").get_json()
    exec_summary = [e["action_type"] for e in all_events if e["task_name"] == "Executive Summary"]
    for action in ("Component Assigned", "Component Submitted", "Component Approved"):
        assert action in exec_summary

    # Completion is derived from the WHOLE applicable set: four approvals do
    # not complete a 15-step BP pipeline.
    project = client.get(f"/api/projects/{pid}").get_json()
    assert project["overall_status"] != "Completed"
    assert not project.get("completed_at")
    assert client.get(f"/api/projects/{pid}/detail").get_json()["completion"]["percent"] < 100


def test_dry_fluid_triggers_case_insensitively(client):
    pid = _bp_project(client, "AUTO-DRY-1")
    assert _put(client, pid, "quicklook", [_water_row("dry")]).status_code == 200
    statuses = _statuses(client, pid)
    assert all(statuses[name] == "Approved" for name in AUTO_STEPS)
    # The comment quotes what the interpreter actually recorded, not a
    # normalized spelling.
    assert "(dry)" in _auto_events(client, pid)[0]["comment"]


def test_hydrocarbon_fluid_does_not_trigger(client):
    pid = _bp_project(client, "AUTO-GAS-1")
    assert _put(client, pid, "quicklook", [_water_row("Gas")]).status_code == 200
    statuses = _statuses(client, pid)
    assert all(statuses[name] == "Not Assigned" for name in AUTO_STEPS)
    assert _auto_events(client, pid) == []


def test_blank_fluid_does_not_trigger(client):
    pid = _bp_project(client, "AUTO-BLANK-1")
    assert _put(client, pid, "quicklook", [{"formation": "SARH", "pay_ft": "3"}]).status_code == 200
    assert all(_statuses(client, pid)[name] == "Not Assigned" for name in AUTO_STEPS)


def test_two_quicklook_formations_never_trigger_even_when_one_is_water(client):
    """'Non-prospective' means the WHOLE quicklook interpretation is barren --
    a second formation means there is still something to evaluate."""
    pid = _bp_project(client, "AUTO-TWO-1")
    assert _put(client, pid, "quicklook",
                [_water_row(), {"formation": "QASM", "pay_ft": "40", "fluid": "Gas"}]).status_code == 200
    assert all(_statuses(client, pid)[name] == "Not Assigned" for name in AUTO_STEPS)
    assert _auto_events(client, pid) == []


def test_two_water_formations_do_not_trigger(client):
    pid = _bp_project(client, "AUTO-TWO-2")
    assert _put(client, pid, "quicklook",
                [_water_row(), {"formation": "QASM", "fluid": "Water"}]).status_code == 200
    assert all(_statuses(client, pid)[name] == "Not Assigned" for name in AUTO_STEPS)


def test_clearing_the_quicklook_phase_does_not_trigger(client):
    """Zero rows is not a non-prospective result, it is no result."""
    pid = _bp_project(client, "AUTO-ZERO-1")
    assert _put(client, pid, "quicklook", []).status_code == 200
    assert all(_statuses(client, pid)[name] == "Not Assigned" for name in AUTO_STEPS)
    assert _auto_events(client, pid) == []


def test_a_water_row_at_another_phase_does_not_trigger(client):
    """The rule reads the QUICKLOOK interpretation only."""
    pid = _bp_project(client, "AUTO-PHASE-1")
    assert _put(client, pid, "final", [_water_row()]).status_code == 200
    assert all(_statuses(client, pid)[name] == "Not Assigned" for name in AUTO_STEPS)


def test_prospect_pipeline_project_is_untouched(client):
    """The four steps are not applicable to a prospect (they live in the BP
    execution stages), so the rule has nothing in scope -- no pipeline_type
    literal needed, the applicable-stages filter does it."""
    pid = create_project(client, "AUTO-PROSPECT-1")
    assert _put(client, pid, "quicklook", [_water_row()]).status_code == 200
    assert all(_statuses(client, pid)[name] == "Not Assigned" for name in AUTO_STEPS)
    assert _auto_events(client, pid) == []
    assert "System" not in [u["name"] for u in client.get("/api/users").get_json()]


def test_replaying_the_same_put_adds_no_new_history(client):
    """Idempotence: the second (and third) identical PUT is a pure no-op."""
    pid = _bp_project(client, "AUTO-REPLAY-1")
    _put(client, pid, "quicklook", [_water_row()])
    first = client.get(f"/api/activity?project_id={pid}").get_json()
    assert len(_auto_events(client, pid)) == 4

    _put(client, pid, "quicklook", [_water_row()])
    _put(client, pid, "quicklook", [_water_row()])
    assert len(_auto_events(client, pid)) == 4
    # No status churn either: nothing but the formation-data events was added.
    after = client.get(f"/api/activity?project_id={pid}").get_json()
    added = [e["action_type"] for e in after if e not in first]
    assert set(added) <= {"Formation Data Updated"}


def test_sad_update_submit_gate_is_satisfied_not_bypassed(client):
    """SAD Update refuses a submit until both sign-off boxes are ticked. The
    walk RECORDS the sign-off through the audited field-save path, so the
    stored fields show it and the step really is Approved."""
    pid = _bp_project(client, "AUTO-GATE-1")
    _put(client, pid, "quicklook", [_water_row()])

    task = get_task_by_name(client, pid, "SAD Update")
    assert task["status"] == "Approved"
    fields = client.get(f"/api/tasks/{task['task_id']}/dynamic-fields").get_json()
    assert fields["sad_update_done"] == "1"
    assert fields["final_exec_summary_done"] == "1"


def test_an_already_approved_step_is_left_alone(client):
    """A step a human already approved gets no auto-completion event: the rule
    only ever moves steps FORWARD."""
    pid = _bp_project(client, "AUTO-PREAPPROVED-1")
    task = get_task_by_name(client, pid, "Flowback Results")
    task = client.post(f"/api/tasks/{task['task_id']}/assign", json={
        "assignee": "Employee", "cascade": False, "revision": task["revision"]}).get_json()["task"]
    client.post(f"/api/tasks/{task['task_id']}/transition",
                json={"action": "submit", "revision": task["revision"]})
    task = get_task_by_name(client, pid, "Flowback Results")
    client.post(f"/api/tasks/{task['task_id']}/transition",
                json={"action": "approve", "revision": task["revision"]})

    _put(client, pid, "quicklook", [_water_row()])
    assert all(_statuses(client, pid)[name] == "Approved" for name in AUTO_STEPS)
    assert sorted(e["task_name"] for e in _auto_events(client, pid)) == sorted(
        [n for n in AUTO_STEPS if n != "Flowback Results"])


def test_a_reopened_step_is_not_fought_by_later_formation_saves(client):
    """The rule fires ONCE PER STEP, EVER (the Auto-Completed history row is
    the marker). A user who reopens an auto-completed step keeps it open --
    whether the next formations save still matches or not. Nothing is reverted
    either: an edit that breaks the condition leaves the other approvals
    standing, and the audit trail explains them."""
    pid = _bp_project(client, "AUTO-REOPEN-1")
    _put(client, pid, "quicklook", [_water_row()])
    task = get_task_by_name(client, pid, "Flowback Results")
    resp = client.patch(f"/api/tasks/{task['task_id']}",
                        json={"status": "In Progress", "revision": task["revision"]})
    assert resp.status_code == 200, resp.get_json()

    # A save that BREAKS the condition changes nothing (not reversible)...
    assert _put(client, pid, "quicklook", [_water_row("Gas")]).status_code == 200
    statuses = _statuses(client, pid)
    assert statuses["Flowback Results"] == "In Progress"
    assert all(statuses[n] == "Approved" for n in AUTO_STEPS if n != "Flowback Results")

    # ...and neither does one that matches again: the step stays the user's.
    assert _put(client, pid, "quicklook", [_water_row()]).status_code == 200
    assert _statuses(client, pid)["Flowback Results"] == "In Progress"
    assert len(_auto_events(client, pid)) == 4


def test_the_system_identity_is_seeded_only_when_the_rule_fires(client):
    """The automation needs an active user to assign to (assign_task refuses
    anything else), but a database where the rule never fires never grows the
    row."""
    names = [u["name"] for u in client.get("/api/users").get_json()]
    assert "System" not in names

    pid = _bp_project(client, "AUTO-SYSUSER-1")
    _put(client, pid, "quicklook", [_water_row()])
    seeded = {u["name"]: u["role"] for u in client.get("/api/users").get_json()}
    assert seeded.get("System") == "supervisor"
    assert get_task_by_name(client, pid, "SAD Update")["assigned_to"] == "System"


def test_deactivating_the_system_identity_switches_the_rule_off(client):
    """``UPDATE users SET is_active = 0 WHERE name = 'System'`` is the off
    switch: the rule stands down instead of failing the formations save."""
    from conftest import raw_sqlite_connect
    pid = _bp_project(client, "AUTO-SYSUSER-2")
    _put(client, pid, "quicklook", [_water_row()])  # seeds the row
    connection = raw_sqlite_connect(client.db_path)
    connection.execute("UPDATE users SET is_active = 0 WHERE name = 'System'")
    connection.commit()
    connection.close()

    pid2 = _bp_project(client, "AUTO-SYSUSER-3")
    assert _put(client, pid2, "quicklook", [_water_row()]).status_code == 200
    assert all(_statuses(client, pid2)[name] == "Not Assigned" for name in AUTO_STEPS)
