"""Tests for well-level formation data (WS6, project_formations).

Formation interpretation values (SARH / QASM / QWRH) belong to the WELL, not to
a workflow step: GET/PUT /api/projects/<id>/formations, upsert keyed by
(project, formation, phase), strict validation (including numeric coercion --
measurement fields are REAL columns), one history event against the source
task, and /detail exposure.
"""
from __future__ import annotations

from conftest import create_project, get_task_by_name

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

def test_unknown_formation_rejected(client):
    pid = create_project(client, "FORM-BAD-1")
    resp = _put(client, pid, "quicklook", [{"formation": "ARAB-D"}])
    assert resp.status_code == 400
    assert "Unknown formation" in resp.get_json()["detail"]


def test_unknown_phase_rejected(client):
    pid = create_project(client, "FORM-BAD-2")
    resp = _put(client, pid, "post_test", [SARH_ROW])
    assert resp.status_code == 400
    assert "Unknown phase" in resp.get_json()["detail"]


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
    quicklook = get_task_by_name(client, pid, "Quicklook Logs Interpretation")
    resp = _put(client, pid, "quicklook", [SARH_ROW, {"formation": "QWRH", "pay_ft": "5"}],
                source_task_id=quicklook["task_id"])
    assert resp.status_code == 200

    events = client.get(f"/api/activity?project_id={pid}").get_json()
    formation_events = [e for e in events if e["action_type"] == "Formation Data Updated"]
    assert len(formation_events) == 1  # ONE event per PUT, not per row
    event = formation_events[0]
    assert event["task_name"] == "Quicklook Logs Interpretation"
    assert "SARH" in event["comment"] and "QWRH" in event["comment"]
    assert "quicklook" in event["comment"]


def test_no_history_event_without_source_task(client):
    pid = create_project(client, "FORM-HISTORY-2")
    _put(client, pid, "quicklook", [SARH_ROW])
    events = client.get(f"/api/activity?project_id={pid}").get_json()
    assert not [e for e in events if e["action_type"] == "Formation Data Updated"]
