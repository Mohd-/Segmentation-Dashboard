"""Characterization tests for the Chance-of-Success (CoS) domain math.

Pure-function tests (calculate_seal_cos, calculate_reservoir_cos_rows,
segment_class) import cos directly. Total CoS / final Reservoir CoS tests
exercise the flow through the Flask API: Reservoir/Trap/Seal CoS inputs are
saved as task dynamic fields, and the Total Chance of Success is COMPUTED AT
READ TIME (workflow.calculate_total_cos / total_cos_from_fields) -- nothing is
stored and no recalculation event is written. Since v18 the Presence CoS
Evaluation step is gone: the value surfaces only as ``derisking`` in /detail's
computed ``overview``.

The RF model backing calculate_reservoir_cos_rows is a tiny stub
(RandomForestClassifier) trained and joblib-dumped by the session-scoped
`_rf_model_stub` autouse fixture in conftest.py, at the path
SEGMENT_TRACKER_RF_MODEL_PATH. We never hardcode its predicted probabilities:
each test that needs a model-derived percentage loads the same joblib file and
computes int(round(model.predict_proba(features)[0][1] * 100)).
"""
from __future__ import annotations

import json
import os

import joblib
import pytest

from conftest import create_project, get_task_by_name


def _load_stub_model():
    return joblib.load(os.environ["SEGMENT_TRACKER_RF_MODEL_PATH"])


def _expected_reservoir_pct(model, pull_up_encoded, amplitude_ratio, base_tight_sarah):
    features = [[pull_up_encoded, amplitude_ratio, base_tight_sarah]]
    probability = float(model.predict_proba(features)[0][1])
    return str(int(round(probability * 100)))


# ---------------------------------------------------------------------------
# calculate_seal_cos
# ---------------------------------------------------------------------------

def test_seal_cos_all_blank_returns_empty_string(client):
    import cos
    assert cos.calculate_seal_cos({}) == ""
    assert cos.calculate_seal_cos(None) == ""


def test_seal_cos_activity_above_point_nine_ignores_dip_azimuth_fault(client):
    import cos
    result = cos.calculate_seal_cos({
        "seal_recent_activity_age": "0.95",
        "seal_fracture_permeability": "0.5",
    })
    assert result == str(int(round(0.95 * 0.5 * 100)))
    assert result == "48"


def test_seal_cos_activity_exactly_point_nine_uses_average_branch(client):
    import cos
    result = cos.calculate_seal_cos({
        "seal_recent_activity_age": "0.9",
        "seal_fracture_permeability": "0.5",
        "seal_dip": "0.3",
        "seal_azimuth_vs_shmax": "0.6",
        "seal_fault_level_confidence": "0.9",
    })
    expected = ((0.3 + 0.6 + 0.9) / 3.0) * 0.5
    assert result == str(int(round(expected * 100)))
    assert result == "30"


def test_seal_cos_missing_required_field_raises_named_error(client):
    import cos
    with pytest.raises(ValueError, match="Dip"):
        cos.calculate_seal_cos({
            "seal_recent_activity_age": "0.5",
            "seal_fracture_permeability": "0.5",
        })


def test_seal_cos_survives_saves_without_form_inputs(client):
    """A save whose payload carries no Seal CoS form inputs (comment-only full
    save, or a dynamic-fields PATCH of unrelated keys) must NOT wipe the stored
    seal_cos_pct with a blank-form recompute. Recompute fires only when the
    payload contains at least one of the form's input keys."""
    pid = create_project(client, "SEAL-KEEP-1")
    seal = get_task_by_name(client, pid, "Seal CoS")
    resp = client.patch(f"/api/tasks/{seal['task_id']}/dynamic-fields", json={"fields": {
        "seal_recent_activity_age": "0.95",
        "seal_fracture_permeability": "0.5",
    }})
    assert resp.status_code == 200
    fields = client.get(f"/api/tasks/{seal['task_id']}/dynamic-fields").get_json()
    assert fields.get("seal_cos_pct") == "48"

    # Comment-only full save (the step editor's Save with an empty form diff).
    resp = client.patch(f"/api/tasks/{seal['task_id']}",
                        json={"comments": "reviewed, no changes", "fields": {}})
    assert resp.status_code == 200
    fields = client.get(f"/api/tasks/{seal['task_id']}/dynamic-fields").get_json()
    assert fields.get("seal_cos_pct") == "48"

    # Dynamic-fields save of an unrelated key.
    resp = client.patch(f"/api/tasks/{seal['task_id']}/dynamic-fields",
                        json={"fields": {"seal_pore_pressure_gradient_psi_ft": "0.62"}})
    assert resp.status_code == 200
    fields = client.get(f"/api/tasks/{seal['task_id']}/dynamic-fields").get_json()
    assert fields.get("seal_cos_pct") == "48"

    # A save carrying the inputs still recomputes as before.
    resp = client.patch(f"/api/tasks/{seal['task_id']}/dynamic-fields", json={"fields": {
        "seal_recent_activity_age": "0.95",
        "seal_fracture_permeability": "0.6",
    }})
    assert resp.status_code == 200
    fields = client.get(f"/api/tasks/{seal['task_id']}/dynamic-fields").get_json()
    assert fields.get("seal_cos_pct") == "57"


# ---------------------------------------------------------------------------
# calculate_reservoir_cos_rows
# ---------------------------------------------------------------------------

def test_reservoir_cos_rows_pull_up_mapping(client):
    import cos
    model = _load_stub_model()
    rows_in = [
        {"pull_up": "No", "amplitude_ratio": "0.5", "base_tight_sarah": "0.5"},
        {"pull_up": "Semi", "amplitude_ratio": "0.5", "base_tight_sarah": "0.5"},
        {"pull_up": "Yes", "amplitude_ratio": "0.5", "base_tight_sarah": "0.5"},
    ]
    out = json.loads(cos.calculate_reservoir_cos_rows(rows_in))
    assert out[0]["reservoir_cos_pct"] == _expected_reservoir_pct(model, 0.0, 0.5, 0.5)
    assert out[1]["reservoir_cos_pct"] == _expected_reservoir_pct(model, 1.0, 0.5, 0.5)
    assert out[2]["reservoir_cos_pct"] == _expected_reservoir_pct(model, 2.0, 0.5, 0.5)


def test_reservoir_cos_rows_legacy_numeric_pull_up_accepted(client):
    import cos
    model = _load_stub_model()
    out = json.loads(cos.calculate_reservoir_cos_rows(
        [{"pull_up": "1", "amplitude_ratio": "0.5", "base_tight_sarah": "0.5"}]
    ))
    assert out[0]["reservoir_cos_pct"] == _expected_reservoir_pct(model, 1.0, 0.5, 0.5)


def test_reservoir_cos_rows_invalid_pull_up_raises(client):
    import cos
    with pytest.raises(ValueError):
        cos.calculate_reservoir_cos_rows([{"pull_up": "maybe"}])


def test_reservoir_cos_rows_accepts_json_string_input(client):
    import cos
    model = _load_stub_model()
    payload = json.dumps([{"pull_up": "No", "amplitude_ratio": "0.1", "base_tight_sarah": "0.1"}])
    out = json.loads(cos.calculate_reservoir_cos_rows(payload))
    assert out[0]["reservoir_cos_pct"] == _expected_reservoir_pct(model, 0.0, 0.1, 0.1)


def test_reservoir_cos_rows_non_list_raises(client):
    import cos
    with pytest.raises(ValueError):
        cos.calculate_reservoir_cos_rows("not a json list")  # invalid JSON
    with pytest.raises(ValueError):
        cos.calculate_reservoir_cos_rows(123)  # not a list at all


def test_reservoir_cos_rows_empty_or_none_returns_empty_json_list(client):
    import cos
    assert json.loads(cos.calculate_reservoir_cos_rows(None)) == []
    assert json.loads(cos.calculate_reservoir_cos_rows("")) == []


# ---------------------------------------------------------------------------
# Total CoS via API (integration; computed at read)
# ---------------------------------------------------------------------------

def _derisking(client, pid):
    """The read-time Total Chance of Success, as /detail's overview surfaces it."""
    detail = client.get(f"/api/projects/{pid}/detail").get_json()
    return detail["overview"]["derisking"]


def test_total_cos_computed_at_read_via_task_save_endpoint(client):
    """Reservoir CoS rows saved through PATCH /api/tasks/<id> (main save route)
    trigger the model recalculation of reservoir_cos_pct; the Total Chance of
    Success is then composed at READ time from the stored Reservoir/Trap/Seal
    inputs. Since v18 there is no Presence task: the result appears as
    overview.derisking in the /detail payload -- with no recalculation write
    or history event behind it."""
    model = _load_stub_model()

    pid = create_project(client, "PRESENCE-1")
    reservoir = get_task_by_name(client, pid, "Reservoir CoS")
    trap = get_task_by_name(client, pid, "Trap CoS")
    seal = get_task_by_name(client, pid, "Seal CoS")
    assert get_task_by_name(client, pid, "Presence CoS Evaluation") is None  # step removed in v18

    resp = client.patch(f"/api/tasks/{reservoir['task_id']}", json={
        "status": reservoir["status"],
        "revision": reservoir["revision"],
        "fields": {"reservoir_cos_rows": json.dumps([
            {"pull_up": "Yes", "amplitude_ratio": "0.5", "base_tight_sarah": "0.5"},
        ])},
    })
    assert resp.status_code == 200
    expected_reservoir_pct = _expected_reservoir_pct(model, 2.0, 0.5, 0.5)
    # reservoir_cos_rows lives in dynamic fields, not directly on the task row;
    # confirm via the dynamic-fields endpoint.
    reservoir_fields = client.get(f"/api/tasks/{reservoir['task_id']}/dynamic-fields").get_json()
    stored_rows = json.loads(reservoir_fields["reservoir_cos_rows"])
    assert stored_rows[-1]["reservoir_cos_pct"] == expected_reservoir_pct

    client.patch(f"/api/tasks/{trap['task_id']}/dynamic-fields", json={"fields": {"trap_cos_pct": "80"}})
    client.patch(f"/api/tasks/{seal['task_id']}/dynamic-fields", json={"fields": {
        "seal_recent_activity_age": "0.95", "seal_fracture_permeability": "0.5",
    }})

    reservoir_probability = int(expected_reservoir_pct) / 100.0
    trap_probability = 0.80
    seal_probability = 0.48  # 0.95 * 0.5
    expected_presence = str(int(round(reservoir_probability * trap_probability * seal_probability * 100)))
    assert _derisking(client, pid) == expected_presence

    # Computed at read means NO recalculation write: no "Presence CoS
    # Calculated" history event exists anywhere in the project's log.
    events = client.get(f"/api/activity?project_id={pid}").get_json()
    assert not [e for e in events if e["action_type"] == "Presence CoS Calculated"]


def test_total_cos_blank_if_any_component_missing(client):
    pid = create_project(client, "PRESENCE-2")
    trap = get_task_by_name(client, pid, "Trap CoS")
    client.patch(f"/api/tasks/{trap['task_id']}/dynamic-fields", json={"fields": {"trap_cos_pct": "80"}})
    assert (_derisking(client, pid) or "") == ""


def test_final_reservoir_cos_is_first_row_with_nonempty_pct(client):
    """Saving raw reservoir_cos_rows JSON through the dynamic-fields endpoint
    bypasses the model recalculation (only the main task-save route on the
    Reservoir CoS task recomputes reservoir_cos_pct). This lets us pin the
    "skip blank pct, use the FIRST non-blank one" selection logic in
    workflow.first_reservoir_cos_row_value via the derisking result."""
    pid = create_project(client, "PRESENCE-FINAL-1")
    reservoir = get_task_by_name(client, pid, "Reservoir CoS")
    trap = get_task_by_name(client, pid, "Trap CoS")
    seal = get_task_by_name(client, pid, "Seal CoS")

    raw_rows = json.dumps([
        {"reservoir_cos_pct": "40"},
        {"reservoir_cos_pct": ""},
        {"reservoir_cos_pct": "55"},
    ])
    client.patch(f"/api/tasks/{reservoir['task_id']}/dynamic-fields", json={
        "fields": {"reservoir_cos_rows": raw_rows},
    })
    client.patch(f"/api/tasks/{trap['task_id']}/dynamic-fields", json={"fields": {"trap_cos_pct": "80"}})
    client.patch(f"/api/tasks/{seal['task_id']}/dynamic-fields", json={"fields": {
        "seal_recent_activity_age": "0.95", "seal_fracture_permeability": "0.5",
    }})

    # FIRST non-blank reservoir pct (40) x trap (0.80) x seal (0.48) -> "15"
    expected = str(int(round(0.40 * 0.80 * 0.48 * 100)))
    assert expected == "15"
    assert _derisking(client, pid) == expected


# ---------------------------------------------------------------------------
# Trap CoS / initial Resource Assessment stubs (formulas pending)
# ---------------------------------------------------------------------------
# These pin the STUB contract the save-path wiring relies on: None = "not
# computed", so stored/manual values survive every save until the approved
# formulas land in cos.py. When a formula is implemented, replace the
# None-assertions here with real expected values.

def test_trap_cos_stub_returns_none(client):
    import cos
    assert cos.calculate_trap_cos("", "") is None          # nothing entered
    assert cos.calculate_trap_cos("120", "") is None       # partial input
    assert cos.calculate_trap_cos("120", "250") is None    # formula pending
    assert cos.calculate_trap_cos("abc", "250") is None    # non-numeric


def test_initial_resource_assessment_stub_returns_none(client):
    import cos
    assert cos.calculate_initial_resource_assessment("", "", "") is None
    assert cos.calculate_initial_resource_assessment("5", "12", "") is None
    assert cos.calculate_initial_resource_assessment("5", "12", "110", "GRV") is None


def test_trap_cos_save_keeps_manual_value_while_stub_pending(client):
    """Saving the Trap CoS form (with the cross-task SARH thickness present)
    must keep the manually entered trap_cos_pct while calculate_trap_cos still
    returns None -- both through the dynamic-fields PATCH and the full save."""
    pid = create_project(client, "TRAP-STUB-1")
    thickness = get_task_by_name(client, pid, "Thickness Estimation")
    resp = client.patch(f"/api/tasks/{thickness['task_id']}/dynamic-fields",
                        json={"fields": {"formation_thickness_ft": "120"}})
    assert resp.status_code == 200

    trap = get_task_by_name(client, pid, "Trap CoS")
    resp = client.patch(f"/api/tasks/{trap['task_id']}/dynamic-fields", json={"fields": {
        "sarah_quwarah_thickness_ft": "250",
        "trap_cos_pct": "55",
    }})
    assert resp.status_code == 200
    fields = client.get(f"/api/tasks/{trap['task_id']}/dynamic-fields").get_json()
    assert fields.get("trap_cos_pct") == "55"

    resp = client.patch(f"/api/tasks/{trap['task_id']}", json={"fields": {
        "sarah_quwarah_thickness_ft": "260",
        "trap_cos_pct": "60",
    }})
    assert resp.status_code == 200
    fields = client.get(f"/api/tasks/{trap['task_id']}/dynamic-fields").get_json()
    assert fields.get("trap_cos_pct") == "60"


def test_lead_resource_assessment_save_keeps_manual_values_while_stub_pending(client):
    """Saving the Lead Resource Assessment step (areas + SARH thickness already
    on their own tasks) must keep the manually entered PIIP trio while
    calculate_initial_resource_assessment still returns None."""
    pid = create_project(client, "LEADRA-STUB-1")
    areas = get_task_by_name(client, pid, "Reservoir Area Definition")
    client.patch(f"/api/tasks/{areas['task_id']}/dynamic-fields",
                 json={"fields": {"p90_area_km2": "5", "p10_area_km2": "12"}})
    thickness = get_task_by_name(client, pid, "Thickness Estimation")
    client.patch(f"/api/tasks/{thickness['task_id']}/dynamic-fields",
                 json={"fields": {"formation_thickness_ft": "110"}})

    lead_ra = get_task_by_name(client, pid, "Lead Resource Assessment")
    resp = client.patch(f"/api/tasks/{lead_ra['task_id']}/dynamic-fields", json={"fields": {
        "lead_calculation_method": "GRV",
        "lead_piip_gas_p90": "2.5",
        "lead_piip_gas_mean": "4.0",
        "lead_piip_gas_p10": "7.5",
    }})
    assert resp.status_code == 200
    fields = client.get(f"/api/tasks/{lead_ra['task_id']}/dynamic-fields").get_json()
    assert fields.get("lead_piip_gas_p90") == "2.5"
    assert fields.get("lead_piip_gas_mean") == "4.0"
    assert fields.get("lead_piip_gas_p10") == "7.5"


# ---------------------------------------------------------------------------
# segment_class quadrants
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ogip,chance,expected", [
    (10, 50, "Super Star"),
    (15, 90, "Super Star"),
    (10, 49.9, "Risk Taker"),
    (20, 0, "Risk Taker"),
    (9.9, 50, "Value Hunter"),
    (0, 100, "Value Hunter"),
    (9.9, 49.9, "Dog"),
    (0, 0, "Dog"),
])
def test_segment_class_quadrants(client, ogip, chance, expected):
    import cos
    assert cos.segment_class(ogip, chance) == expected


def test_segment_class_missing_value_returns_empty_string(client):
    import cos
    assert cos.segment_class(None, 50) == ""
    assert cos.segment_class(10, None) == ""
    assert cos.segment_class(None, None) == ""
    assert cos.segment_class("", "") == ""
