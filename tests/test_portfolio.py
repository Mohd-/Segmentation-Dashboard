"""Tests for the Portfolio rework (reporting.get_portfolio_rows).

The Portfolio is the analysis surface for BP-enabled wells PLUS fully-matured
leads. Column sources each get pinned here -- gas-field derivation from the
project name, seismic AR -> block-name mapping (config.AR_TO_SEISMIC_BLOCK,
FIRST non-empty AR) with raw fallback, raw fluid precedence (final ->
resource_update -> post_drill -> quicklook, final being the petrophysical
authority), status (fluid -> Staked when Approval to Stake approved ->
Proposed default), mean-OGIP precedence (post-drill -> pre-drill -> lead),
classification (BP Execution Gate beats legacy GHEER), and mature-lead
membership (a prospect with every prospect step Approved).
"""
from __future__ import annotations

import json

from conftest import create_project, get_task_by_name, get_tasks

BP_KWARGS = {"business_plan_enabled": True, "business_plan_year": 2027}
PROSPECT_STAGES = {"Lead Identification", "Risking", "Segmentation", "Pre-Well Delivery"}


def _approve_all_prospect_tasks(client, pid):
    """Approve every prospect-stage task so the lead matures (overall Completed)."""
    for task in get_tasks(client, pid):
        if task["stage_group"] in PROSPECT_STAGES and task["status"] != "Approved":
            resp = client.patch(f"/api/tasks/{task['task_id']}", json={
                "status": "Approved", "revision": task["revision"],
            })
            assert resp.status_code == 200, resp.get_json()


def _rows(client, **query):
    qs = "&".join(f"{k}={v}" for k, v in query.items())
    resp = client.get("/api/portfolio/rows" + (f"?{qs}" if qs else ""))
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def _row_for(client, pid):
    return next(r for r in _rows(client)["rows"] if r["project_id"] == pid)


def _save_fields(client, pid, task_name, fields):
    task = get_task_by_name(client, pid, task_name)
    resp = client.patch(f"/api/tasks/{task['task_id']}/dynamic-fields", json={"fields": fields})
    assert resp.status_code == 200, resp.get_json()


# ---------------------------------------------------------------------------
# Gas field derivation
# ---------------------------------------------------------------------------

def test_gas_field_is_name_prefix_before_first_hyphen(client):
    pid = create_project(client, "JOHN-4", **BP_KWARGS)
    row = _row_for(client, pid)
    assert row["well_name"] == "JOHN-4"
    assert row["gas_field"] == "JOHN"


def test_gas_field_whole_name_when_no_hyphen(client):
    pid = create_project(client, "SOLO", **BP_KWARGS)
    assert _row_for(client, pid)["gas_field"] == "SOLO"


# ---------------------------------------------------------------------------
# Seismic block
# ---------------------------------------------------------------------------

def test_seismic_block_maps_first_nonempty_ar_number(client, monkeypatch):
    import config
    monkeypatch.setattr(config, "AR_TO_SEISMIC_BLOCK", {"2525": "Block A"})

    pid = create_project(client, "SEISMIC-1", **BP_KWARGS)
    # The mapped AR '2525' is the FIRST non-empty AR; the later AR-1111111 must
    # be ignored under the first-non-empty rule.
    _save_fields(client, pid, "Reservoir CoS", {"reservoir_cos_rows": json.dumps([
        {"seismic_volume_ar_number": "", "reservoir_cos_pct": "50"},
        {"seismic_volume_ar_number": "2525", "reservoir_cos_pct": ""},
        {"seismic_volume_ar_number": "AR-1111111", "reservoir_cos_pct": "40"},
    ])})
    assert _row_for(client, pid)["seismic_block"] == "Block A"


def test_seismic_block_falls_back_to_raw_first_ar_number(client, monkeypatch):
    import config
    monkeypatch.setattr(config, "AR_TO_SEISMIC_BLOCK", {"2525": "Block A"})

    pid = create_project(client, "SEISMIC-2", **BP_KWARGS)
    # First non-empty AR is unmapped -> raw AR shows; the later mapped '2525'
    # is not consulted.
    _save_fields(client, pid, "Reservoir CoS", {"reservoir_cos_rows": json.dumps([
        {"seismic_volume_ar_number": "AR-9999999"},
        {"seismic_volume_ar_number": "2525"},
    ])})
    assert _row_for(client, pid)["seismic_block"] == "AR-9999999"


def test_seismic_block_empty_when_no_rows(client):
    pid = create_project(client, "SEISMIC-3", **BP_KWARGS)
    assert _row_for(client, pid)["seismic_block"] == ""


def test_seismic_block_key_survives_dynamic_fields_patch_round_trip(client):
    """A 'seismic_block' key inside a reservoir_cos_rows row is opaque to the
    storage path (PATCH /api/tasks/<id>/dynamic-fields -> save_task_dynamic_fields,
    the same route api.saveFields uses), so it must survive untouched. (Reservoir
    CoS recompute -- cos.calculate_reservoir_cos_rows -- only fires on the
    full PATCH /api/tasks/<id> save_task path; it does `dict(item)` plus one
    added key, so unknown keys survive there too, but this test pins the
    dynamic-fields route the seismic_block/AR dropdowns actually save through.)"""
    pid = create_project(client, "SEISMIC-4", **BP_KWARGS)
    task = get_task_by_name(client, pid, "Reservoir CoS")
    resp = client.patch(f"/api/tasks/{task['task_id']}/dynamic-fields", json={"fields": {
        "reservoir_cos_rows": json.dumps([
            {"seismic_block": "Block A", "seismic_volume_ar_number": "2525",
             "pull_up": "Yes", "amplitude_ratio": 0.5, "base_tight_sarah": 0.5,
             "reservoir_cos_pct": "40"},
        ]),
    }})
    assert resp.status_code == 200, resp.get_json()

    detail = client.get(f"/api/tasks/{task['task_id']}/dynamic-fields")
    assert detail.status_code == 200, detail.get_json()
    stored_rows = json.loads(detail.get_json()["reservoir_cos_rows"])
    assert stored_rows[0]["seismic_block"] == "Block A"
    assert stored_rows[0]["seismic_volume_ar_number"] == "2525"
    assert stored_rows[0]["reservoir_cos_pct"] == "40"

    # Also pin the OTHER save route, PATCH /api/tasks/<id> (save_task), which
    # DOES run the row set through cos.calculate_reservoir_cos_rows
    # (workflow/lifecycle.py's task_name == "Reservoir CoS" branch). That
    # function does `row = dict(item or {})` then adds only
    # `reservoir_cos_pct`, so unknown keys must survive this recompute too --
    # confirming no fix to cos.py was needed for seismic_block to round-trip.
    resp = client.patch(f"/api/tasks/{task['task_id']}", json={"fields": {
        "reservoir_cos_rows": json.dumps([
            {"seismic_block": "Block B", "seismic_volume_ar_number": "1201",
             "pull_up": "Yes", "amplitude_ratio": 0.5, "base_tight_sarah": 0.5},
        ]),
    }})
    assert resp.status_code == 200, resp.get_json()
    detail = client.get(f"/api/tasks/{task['task_id']}/dynamic-fields")
    recomputed_rows = json.loads(detail.get_json()["reservoir_cos_rows"])
    assert recomputed_rows[0]["seismic_block"] == "Block B"
    assert recomputed_rows[0]["seismic_volume_ar_number"] == "1201"
    assert "reservoir_cos_pct" in recomputed_rows[0]  # recomputed by the RF model


# ---------------------------------------------------------------------------
# config seismic-block map inversion (unit, no HTTP)
# ---------------------------------------------------------------------------

def test_ar_to_seismic_block_is_correct_inversion_of_shipped_map():
    """AR_TO_SEISMIC_BLOCK, as loaded from the real seismic_blocks.json at
    import time, must be the exact reverse index of SEISMIC_BLOCK_AR_MAP."""
    import config
    expected = {}
    for block, ars in config.SEISMIC_BLOCK_AR_MAP.items():
        for ar in ars:
            expected.setdefault(ar, block)
    assert config.AR_TO_SEISMIC_BLOCK == expected
    # Sanity: the shipped placeholder file actually has content to invert.
    assert config.SEISMIC_BLOCK_AR_MAP
    assert config.AR_TO_SEISMIC_BLOCK


# ---------------------------------------------------------------------------
# Status (Proposed / Staked / fluid) + raw fluid precedence
# ---------------------------------------------------------------------------

def test_status_defaults_to_proposed(client):
    pid = create_project(client, "STATUS-1", **BP_KWARGS)
    row = _row_for(client, pid)
    assert row["status"] == "Proposed"
    # The old 'Not Drilled Yet' fluid fallback is gone: raw fluid is ''.
    assert row["fluid"] == ""


def test_status_staked_when_approval_to_stake_approved(client):
    pid = create_project(client, "STATUS-2", **BP_KWARGS)
    task = get_task_by_name(client, pid, "Approval to Stake")
    resp = client.patch(f"/api/tasks/{task['task_id']}", json={
        "status": "Approved", "revision": task["revision"],
    })
    assert resp.status_code == 200, resp.get_json()
    row = _row_for(client, pid)
    assert row["status"] == "Staked"
    assert row["fluid"] == ""  # no fluid recorded yet


def test_fluid_wins_status_and_final_beats_quicklook(client):
    pid = create_project(client, "STATUS-3", **BP_KWARGS)
    _save_fields(client, pid, "Quicklook Logs Interpretation", {"quicklook_fluid_type": "Gas"})
    row = _row_for(client, pid)
    assert row["fluid"] == "Gas"
    assert row["status"] == "Gas"  # a recorded fluid outranks Proposed/Staked

    _save_fields(client, pid, "Final Log Analysis", {"final_fluid_type": "Dry"})
    row = _row_for(client, pid)
    assert row["fluid"] == "Dry"
    assert row["status"] == "Dry"


def test_fluid_precedence_four_key_ladder(client):
    """final -> resource_update -> post_drill -> quicklook, driven through the
    API so record_status resolves the same ladder the raw fluid column does."""
    pid = create_project(client, "STATUS-4", **BP_KWARGS)
    _save_fields(client, pid, "Quicklook Logs Interpretation", {"quicklook_fluid_type": "Gas"})
    _save_fields(client, pid, "Post-Drilling Resource Assessment",
                 {"post_drill_fluid_type": "Gas Condensate"})
    row = _row_for(client, pid)
    assert row["fluid"] == "Gas Condensate"  # post_drill beats quicklook
    assert row["status"] == "Gas Condensate"

    # A Resource Assessment Update revision beats post_drill and quicklook...
    _save_fields(client, pid, "Resource Assessment Update", {"resource_update_fluid_type": "Wet"})
    row = _row_for(client, pid)
    assert row["fluid"] == "Wet"
    assert row["status"] == "Wet"

    # ...but loses to the final petrophysical read.
    _save_fields(client, pid, "Final Log Analysis", {"final_fluid_type": "Dry"})
    row = _row_for(client, pid)
    assert row["fluid"] == "Dry"
    assert row["status"] == "Dry"


# ---------------------------------------------------------------------------
# Mean OGIP precedence
# ---------------------------------------------------------------------------

def test_mean_ogip_precedence_post_beats_pre_beats_lead(client):
    pid = create_project(client, "OGIP-1", **BP_KWARGS)
    _save_fields(client, pid, "Lead Resource Assessment", {"lead_piip_gas_mean": "5.0"})
    assert _row_for(client, pid)["mean_ogip"] == "5.0"

    _save_fields(client, pid, "Pre-Drilling Resource Assessment", {"pre_drill_piip_gas_mean": "7.5"})
    assert _row_for(client, pid)["mean_ogip"] == "7.5"

    _save_fields(client, pid, "Post-Drilling Resource Assessment", {"post_drill_piip_gas_mean": "9.25"})
    assert _row_for(client, pid)["mean_ogip"] == "9.25"


def test_summary_cumulative_ogip_sums_mean_ogip(client):
    pid_a = create_project(client, "SUM-A", **BP_KWARGS)
    pid_b = create_project(client, "SUM-B", **BP_KWARGS)
    _save_fields(client, pid_a, "Lead Resource Assessment", {"lead_piip_gas_mean": "4.0"})
    _save_fields(client, pid_b, "Post-Drilling Resource Assessment", {"post_drill_piip_gas_mean": "6.5"})

    payload = _rows(client)
    assert payload["summary"]["business_plan_wells"] == 2
    assert payload["summary"]["cumulative_ogip"] == 10.5


# ---------------------------------------------------------------------------
# Classification (BP Execution Gate, with legacy GHEER fallback)
# ---------------------------------------------------------------------------

def test_classification_falls_back_to_gheer_save(client):
    pid = create_project(client, "CLASS-1", **BP_KWARGS)
    assert _row_for(client, pid)["classification"] == ""

    _save_fields(client, pid, "GHEER", {"gheer_classification": "Appraisal"})
    assert _row_for(client, pid)["classification"] == "Appraisal"


def test_classification_bp_gate_beats_gheer(client):
    pid = create_project(client, "CLASS-2", **BP_KWARGS)
    _save_fields(client, pid, "GHEER", {"gheer_classification": "Exploration"})
    assert _row_for(client, pid)["classification"] == "Exploration"

    # The BP Execution Gate value is the primary source and wins over GHEER.
    _save_fields(client, pid, "BP Execution Gate", {"bp_gate_classification": "Development"})
    assert _row_for(client, pid)["classification"] == "Development"


# ---------------------------------------------------------------------------
# Scope / filters keep working with the new row shape
# ---------------------------------------------------------------------------

def test_portfolio_scope_is_bp_enabled_only(client):
    create_project(client, "NOT-IN-PORTFOLIO")
    pid = create_project(client, "IN-PORTFOLIO", **BP_KWARGS)
    rows = _rows(client)["rows"]
    assert [r["project_id"] for r in rows] == [pid]


def test_portfolio_membership_includes_bp_wells_and_mature_leads(client):
    fresh_pid = create_project(client, "FRESH-PROSPECT")            # not mature
    bp_pid = create_project(client, "BP-WELL", pipeline_type="bp", **BP_KWARGS)
    lead_pid = create_project(client, "MATURE-LEAD")
    _approve_all_prospect_tasks(client, lead_pid)

    payload = _rows(client)
    by_id = {r["project_id"]: r for r in payload["rows"]}
    assert set(by_id) == {bp_pid, lead_pid}                          # fresh prospect excluded

    assert by_id[bp_pid]["is_mature_lead"] == 0
    assert by_id[bp_pid]["pipeline_type"] == "bp"
    assert by_id[lead_pid]["is_mature_lead"] == 1
    assert by_id[lead_pid]["pipeline_type"] == "prospect"
    assert by_id[lead_pid]["status"] in {"Proposed", "Staked"}

    assert payload["summary"]["business_plan_wells"] == 1
    assert payload["summary"]["mature_leads"] == 1

    # A fully-matured lead leaves the prospect board; the fresh one stays.
    prospect_ids = [r["project_id"] for r in client.get("/api/projects?pipeline_filter=prospect").get_json()]
    assert lead_pid not in prospect_ids
    assert fresh_pid in prospect_ids


def test_portfolio_year_and_activity_filters(client):
    pid_2027 = create_project(client, "FILTER-2027", **BP_KWARGS)
    pid_2030 = create_project(client, "FILTER-2030", business_plan_enabled=True, business_plan_year=2030)
    client.patch(f"/api/projects/{pid_2030}/flags", json={"active_well_enabled": True})

    rows = _rows(client, year=2027)["rows"]
    assert [r["project_id"] for r in rows] == [pid_2027]

    rows = _rows(client, activity="Active")["rows"]
    assert [r["project_id"] for r in rows] == [pid_2030]

    rows = _rows(client, activity="Non-Active")["rows"]
    assert [r["project_id"] for r in rows] == [pid_2027]
