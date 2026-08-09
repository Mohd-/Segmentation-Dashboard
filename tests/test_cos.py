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
    seal = get_task_by_name(client, pid, "Trap and Seal CoS")
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
# KI-004: a Seal CoS outside 0-100% must be unstorable, and a legacy one that
# is already stored must not be able to fail a READ.
#
# The write and the read used to disagree about the domain: calculate_seal_cos
# range-checks each INPUT but not its PRODUCT (activity x fracture permeability
# on the "recently active" branch), while _cos_probability -- reached from
# GET /api/projects/<id>/detail on every call -- rejects anything above 100.
# The fix is two-layered and BOTH layers are pinned here.
# ---------------------------------------------------------------------------

# The audit's own repro inputs: 1.33 x 0.87 = 1.1571 -> 116%.
POISON_SEAL_INPUTS = {
    "seal_recent_activity_age": "1.33",
    "seal_fracture_permeability": "0.87",
    "seal_dip": "0.23",
    "seal_azimuth_vs_shmax": "0.52",
    "seal_fault_level_confidence": "0.59",
}


def _seal_task(client, pid):
    return get_task_by_name(client, pid, "Trap and Seal CoS")


def test_seal_cos_above_100_is_refused_by_the_dynamic_fields_save(client):
    """LAYER 1. The save that produced the poisoned row now 400s, and its
    message names the computed value AND the two inputs that produced it."""
    pid = create_project(client, "SEAL-GUARD-1")
    seal = _seal_task(client, pid)
    resp = client.patch(f"/api/tasks/{seal['task_id']}/dynamic-fields",
                        json={"fields": dict(POISON_SEAL_INPUTS)})
    assert resp.status_code == 400
    assert resp.get_json()["detail"] == (
        "Seal CoS computes to 116% from these inputs; "
        "adjust Most recent age of activity or Fracture Permeability."
    )


def test_seal_cos_above_100_is_refused_by_the_full_save_too(client):
    """The same guard on the other save path (PATCH /api/tasks/<id>)."""
    pid = create_project(client, "SEAL-GUARD-2")
    seal = _seal_task(client, pid)
    resp = client.patch(f"/api/tasks/{seal['task_id']}",
                        json={"fields": dict(POISON_SEAL_INPUTS), "comments": "attempt"})
    assert resp.status_code == 400
    assert "116%" in resp.get_json()["detail"]


def test_refused_seal_cos_save_writes_nothing_at_all(client):
    """NO PARTIAL WRITE. The refusal is raised before the first DML statement on
    both paths, so neither the offending inputs nor the comment land, and a
    previously good seal_cos_pct is still the stored one."""
    pid = create_project(client, "SEAL-GUARD-3")
    seal = _seal_task(client, pid)
    ok = client.patch(f"/api/tasks/{seal['task_id']}/dynamic-fields", json={"fields": {
        "seal_recent_activity_age": "0.95", "seal_fracture_permeability": "0.5",
    }})
    assert ok.status_code == 200
    before = client.get(f"/api/tasks/{seal['task_id']}/dynamic-fields").get_json()
    assert before.get("seal_cos_pct") == "48"

    assert client.patch(f"/api/tasks/{seal['task_id']}/dynamic-fields",
                        json={"fields": dict(POISON_SEAL_INPUTS)}).status_code == 400
    assert client.patch(f"/api/tasks/{seal['task_id']}",
                        json={"fields": dict(POISON_SEAL_INPUTS),
                              "comments": "should not be stored"}).status_code == 400

    after = client.get(f"/api/tasks/{seal['task_id']}/dynamic-fields").get_json()
    assert after == before, "the refused saves left the stored fields untouched"
    task = _seal_task(client, pid)
    assert not (task.get("comments") or ""), "and the comment never landed either"


def test_seal_cos_of_exactly_100_is_accepted(client):
    """The boundary is INCLUSIVE -- 100% is a legitimate certainty, and
    _cos_probability accepts it. Only past it is a mis-entry."""
    pid = create_project(client, "SEAL-GUARD-4")
    seal = _seal_task(client, pid)
    resp = client.patch(f"/api/tasks/{seal['task_id']}/dynamic-fields", json={"fields": {
        "seal_recent_activity_age": "1.0", "seal_fracture_permeability": "1.0",
    }})
    assert resp.status_code == 200
    fields = client.get(f"/api/tasks/{seal['task_id']}/dynamic-fields").get_json()
    assert fields.get("seal_cos_pct") == "100"


def test_seal_guard_names_the_average_branch_inputs_when_that_branch_overflows(client):
    """The message points at whichever inputs the OFFENDING branch multiplied.
    Below the 0.9 activity threshold that is the three directional terms and
    the permeability, not the activity age."""
    import workflow

    with pytest.raises(ValueError, match="Dip, Azimuth vs. SHmax, Fault Level of Confidence"):
        workflow.lifecycle._apply_seal_cos_calculation(
            {"task_name": "Trap and Seal CoS"},
            {"seal_recent_activity_age": "0.5", "seal_fracture_permeability": "2.0",
             "seal_dip": "0.9", "seal_azimuth_vs_shmax": "0.9",
             "seal_fault_level_confidence": "0.9"})


def test_trap_and_reservoir_cos_cannot_leave_the_cos_domain(client):
    """WHY ONLY SEAL IS GUARDED. The other two save-time recomputes are bounded
    by construction: Trap CoS returns a member of a fixed score table and
    Reservoir CoS a model probability, so neither can produce the out-of-domain
    percentage Seal CoS could."""
    import cos

    assert all(0 <= score * 100 <= 100 for score in cos._TRAP_COS_SCORES)
    # Every reachable trap answer, including the 0.5 floor, is inside the domain.
    for b in ("1", "50", "100", "130", "314", "10000"):
        computed = cos.calculate_trap_cos("100", b)
        assert computed is None or 0 <= int(computed) <= 100


def _poison_seal_row(client, pid, value="116"):
    """Write an out-of-domain seal_cos_pct straight into the database.

    Deliberately RAW SQL: the save-time guard above now makes this shape
    unreachable through the API, but rows written before it exists are exactly
    what the read side has to survive. This is the legacy row, reproduced.
    """
    from conftest import raw_sqlite_connect

    seal = _seal_task(client, pid)
    conn = raw_sqlite_connect(client.db_path)
    with conn:
        conn.execute(
            "INSERT INTO task_dynamic_fields (task_id, field_key, field_value, updated_at) "
            "VALUES (?, 'seal_cos_pct', ?, '2026-01-01T00:00:00Z') "
            "ON CONFLICT(task_id, field_key) DO UPDATE SET field_value = excluded.field_value",
            (seal["task_id"], value))
    conn.close()


def test_detail_endpoint_survives_a_poisoned_seal_cos(client):
    """LAYER 2. The audit's repro, with the poisoning done in SQL because the
    API no longer allows it: GET /api/projects/<id>/detail must stay 200 and
    report the Total as UNAVAILABLE, never 400. A read-only endpoint that can
    be bricked by stored data is the actual defect -- the guard above only
    stops NEW ones."""
    pid = create_project(client, "SEAL-POISON-1")
    reservoir = get_task_by_name(client, pid, "Reservoir CoS")
    client.patch(f"/api/tasks/{reservoir['task_id']}/dynamic-fields",
                 json={"fields": {"reservoir_cos_rows": json.dumps([{"reservoir_cos_pct": "70"}])}})
    seal = _seal_task(client, pid)
    client.patch(f"/api/tasks/{seal['task_id']}/dynamic-fields",
                 json={"fields": {"trap_cos_pct": "50"}})
    _poison_seal_row(client, pid)

    resp = client.get(f"/api/projects/{pid}/detail")
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body["overview"]["derisking"] == "", "Total CoS degrades to unavailable (the em-dash path)"
    # The offending value is still VISIBLE, so the user can see what to fix --
    # degrading the derived Total is not the same as hiding the stored input.
    assert body["fields"]["Trap and Seal CoS"]["seal_cos_pct"] == "116"


def test_poisoned_seal_cos_degrades_everywhere_the_total_is_read(client):
    """The tolerance lives in total_cos_from_fields, so every read-only surface
    that resolves a Total -- the board, the portfolio rows, the Excel export --
    degrades identically instead of one of them 400ing."""
    pid = create_project(client, "SEAL-POISON-2")
    seal = _seal_task(client, pid)
    client.patch(f"/api/tasks/{seal['task_id']}/dynamic-fields",
                 json={"fields": {"trap_cos_pct": "50"}})
    _poison_seal_row(client, pid, "150")

    assert client.get(f"/api/projects/{pid}").status_code == 200
    assert client.get("/api/projects").status_code == 200
    rows = client.get("/api/portfolio/rows")
    assert rows.status_code == 200
    assert client.get("/api/export/excel").status_code == 200


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
    trap = get_task_by_name(client, pid, "Trap and Seal CoS")
    seal = get_task_by_name(client, pid, "Trap and Seal CoS")
    assert get_task_by_name(client, pid, "Presence CoS Evaluation") is None  # step removed in v18

    resp = client.patch(f"/api/tasks/{reservoir['task_id']}", json={
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
    trap = get_task_by_name(client, pid, "Trap and Seal CoS")
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
    trap = get_task_by_name(client, pid, "Trap and Seal CoS")
    seal = get_task_by_name(client, pid, "Trap and Seal CoS")

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
# Trap CoS (formula-derived) + Resource Assessment save contract
# ---------------------------------------------------------------------------
# calculate_trap_cos(a, b) walks the approved threshold table and keeps the
# score of the largest factor for which a*(1+factor) < b (strictly
# less-than); 0.5 is the floor when b <= a. The Resource Assessment test
# pins a separate contract: PIIP values change only via the pop-up
# calculator's Apply flow, so a plain save never auto-overwrites them.

def test_trap_cos_none_for_missing_non_numeric_or_non_positive_inputs(client):
    import cos
    assert cos.calculate_trap_cos("", "") is None            # nothing entered
    assert cos.calculate_trap_cos("120", "") is None          # partial input
    assert cos.calculate_trap_cos("", "250") is None          # partial input
    assert cos.calculate_trap_cos("abc", "250") is None       # non-numeric a
    assert cos.calculate_trap_cos("120", "abc") is None       # non-numeric b
    assert cos.calculate_trap_cos("0", "250") is None         # a <= 0
    assert cos.calculate_trap_cos("-5", "250") is None        # a <= 0
    assert cos.calculate_trap_cos("120", "0") is None         # b <= 0
    assert cos.calculate_trap_cos("120", "-5") is None        # b <= 0


@pytest.mark.parametrize("a,b,expected", [
    (100, 100, "50"),   # no threshold strictly below b -> 0.5 floor
    (100, 101, "70"),   # exceeds only the first threshold (100)
    (100, 105, "72"),   # 0.725 -> 72.5 -> int(round) floating-point pin
    (100, 130, "80"),
    (100, 314, "100"),  # exceeds every threshold, including the last (313)
])
def test_trap_cos_threshold_table_examples(client, a, b, expected):
    import cos
    assert cos.calculate_trap_cos(a, b) == expected


def test_trap_cos_input_only_save_still_computes_from_thickness_task(client):
    """A payload carrying the Trap INPUT without a trap_cos_pct (an older
    client, the Excel importer's input-only rows) still gets the server
    recompute, sourced cross-task from Lead Assessment -- both through
    the dynamic-fields PATCH and the full save."""
    import cos

    pid = create_project(client, "TRAP-CALC-1")
    thickness = get_task_by_name(client, pid, "Lead Assessment")
    resp = client.patch(f"/api/tasks/{thickness['task_id']}/dynamic-fields",
                        json={"fields": {"formation_thickness_ft": "100"}})
    assert resp.status_code == 200

    trap = get_task_by_name(client, pid, "Trap and Seal CoS")
    resp = client.patch(f"/api/tasks/{trap['task_id']}/dynamic-fields", json={"fields": {
        "sarah_quwarah_thickness_ft": "130",
    }})
    assert resp.status_code == 200
    fields = client.get(f"/api/tasks/{trap['task_id']}/dynamic-fields").get_json()
    assert fields.get("trap_cos_pct") == cos.calculate_trap_cos("100", "130") == "80"

    resp = client.patch(f"/api/tasks/{trap['task_id']}", json={"fields": {
        "sarah_quwarah_thickness_ft": "314",
    }})
    assert resp.status_code == 200
    fields = client.get(f"/api/tasks/{trap['task_id']}/dynamic-fields").get_json()
    assert fields.get("trap_cos_pct") == cos.calculate_trap_cos("100", "314") == "100"


# ---------------------------------------------------------------------------
# ASAS redesign: the CLIENT is the primary CoS calculator. A payload that
# explicitly carries trap_cos_pct / seal_cos_pct (live-computed or manually
# typed -- the hook cannot tell and must not care) is stored as sent; the
# server recompute stands down. The KI-004 range discipline moves with it:
# an explicitly-sent value outside 0-100 is refused before anything is
# written, on both save paths.
# ---------------------------------------------------------------------------

def test_explicit_trap_cos_value_skips_the_server_recompute(client):
    """Inputs + an explicit trap_cos_pct in ONE payload: the sent value wins,
    even though the formula over the same inputs would give a different one."""
    import cos

    pid = create_project(client, "TRAP-EXPLICIT-1")
    thickness = get_task_by_name(client, pid, "Lead Assessment")
    client.patch(f"/api/tasks/{thickness['task_id']}/dynamic-fields",
                 json={"fields": {"formation_thickness_ft": "100"}})
    trap = get_task_by_name(client, pid, "Trap and Seal CoS")
    assert cos.calculate_trap_cos("100", "130") == "80"  # what a recompute would say

    resp = client.patch(f"/api/tasks/{trap['task_id']}/dynamic-fields", json={"fields": {
        "sarah_quwarah_thickness_ft": "130",
        "trap_cos_pct": "42",  # manual override, sent by the client
    }})
    assert resp.status_code == 200
    fields = client.get(f"/api/tasks/{trap['task_id']}/dynamic-fields").get_json()
    assert fields.get("trap_cos_pct") == "42"

    # The full-save path honors the explicit value the same way.
    resp = client.patch(f"/api/tasks/{trap['task_id']}", json={"fields": {
        "sarah_quwarah_thickness_ft": "130", "trap_cos_pct": "37",
    }})
    assert resp.status_code == 200
    fields = client.get(f"/api/tasks/{trap['task_id']}/dynamic-fields").get_json()
    assert fields.get("trap_cos_pct") == "37"


def test_explicit_seal_cos_value_skips_the_server_recompute(client):
    """Same contract for the Seal half: inputs + an explicit seal_cos_pct in
    one payload stores the sent value, not the formula's 48."""
    pid = create_project(client, "SEAL-EXPLICIT-1")
    seal = get_task_by_name(client, pid, "Trap and Seal CoS")
    resp = client.patch(f"/api/tasks/{seal['task_id']}/dynamic-fields", json={"fields": {
        "seal_recent_activity_age": "0.95",
        "seal_fracture_permeability": "0.5",
        "seal_cos_pct": "33",  # manual override; the formula would say 48
    }})
    assert resp.status_code == 200
    fields = client.get(f"/api/tasks/{seal['task_id']}/dynamic-fields").get_json()
    assert fields.get("seal_cos_pct") == "33"

    # An input-only follow-up save recomputes over the manual value -- the
    # "manual persists until an input next changes (without a pct)" rule.
    resp = client.patch(f"/api/tasks/{seal['task_id']}/dynamic-fields", json={"fields": {
        "seal_recent_activity_age": "0.95", "seal_fracture_permeability": "0.5",
    }})
    assert resp.status_code == 200
    fields = client.get(f"/api/tasks/{seal['task_id']}/dynamic-fields").get_json()
    assert fields.get("seal_cos_pct") == "48"


def test_explicit_seal_cos_outside_the_domain_is_refused_on_both_paths(client):
    """KI-004 still guards the explicit door: a sent seal_cos_pct past 100 (or
    below 0) is refused whole, and the message names the field's rule."""
    pid = create_project(client, "SEAL-EXPLICIT-GUARD-1")
    seal = get_task_by_name(client, pid, "Trap and Seal CoS")
    resp = client.patch(f"/api/tasks/{seal['task_id']}/dynamic-fields",
                        json={"fields": {"seal_cos_pct": "116"}})
    assert resp.status_code == 400
    assert resp.get_json()["detail"] == "Seal CoS must be between 0 and 100%."

    resp = client.patch(f"/api/tasks/{seal['task_id']}",
                        json={"fields": {"seal_cos_pct": "-3"}, "comments": "attempt"})
    assert resp.status_code == 400
    assert "between 0 and 100" in resp.get_json()["detail"]

    # Nothing landed: no seal_cos_pct row, no comment.
    fields = client.get(f"/api/tasks/{seal['task_id']}/dynamic-fields").get_json()
    assert "seal_cos_pct" not in fields
    task = get_task_by_name(client, pid, "Trap and Seal CoS")
    assert not (task.get("comments") or "")


def test_explicit_trap_cos_outside_the_domain_or_non_numeric_is_refused(client):
    """The Trap half gets the same guard: by-construction boundedness only
    holds for COMPUTED values, so the explicit door needs its own check."""
    pid = create_project(client, "TRAP-EXPLICIT-GUARD-1")
    trap = get_task_by_name(client, pid, "Trap and Seal CoS")
    resp = client.patch(f"/api/tasks/{trap['task_id']}/dynamic-fields",
                        json={"fields": {"trap_cos_pct": "101"}})
    assert resp.status_code == 400
    assert resp.get_json()["detail"] == "Trap CoS must be between 0 and 100%."

    resp = client.patch(f"/api/tasks/{trap['task_id']}/dynamic-fields",
                        json={"fields": {"trap_cos_pct": "abc"}})
    assert resp.status_code == 400
    assert resp.get_json()["detail"] == "Trap CoS must be numeric."

    # Boundaries are inclusive; an explicit blank clears the stored value.
    assert client.patch(f"/api/tasks/{trap['task_id']}/dynamic-fields",
                        json={"fields": {"trap_cos_pct": "100"}}).status_code == 200
    assert client.patch(f"/api/tasks/{trap['task_id']}/dynamic-fields",
                        json={"fields": {"trap_cos_pct": ""}}).status_code == 200
    fields = client.get(f"/api/tasks/{trap['task_id']}/dynamic-fields").get_json()
    assert fields.get("trap_cos_pct") == ""


@pytest.mark.parametrize("field_key,label", [
    ("trap_cos_pct", "Trap CoS"),
    ("seal_cos_pct", "Seal CoS"),
])
def test_explicit_cos_non_finite_values_are_refused(client, field_key, label):
    """NaN slips past ordinary ``< 0`` / ``> 100`` comparisons, while
    infinities are numeric but outside the finite percentage domain.  Both CoS
    inputs must reject every non-finite spelling on both API save paths.
    """
    pid = create_project(client, f"{label.upper()}-EXPLICIT-FINITE-GUARD")
    task = get_task_by_name(client, pid, "Trap and Seal CoS")

    for value in ("nan", "NaN", "inf", "-inf", "Infinity", "-Infinity"):
        resp = client.patch(f"/api/tasks/{task['task_id']}/dynamic-fields",
                            json={"fields": {field_key: value}})
        assert resp.status_code == 400, value
        assert resp.get_json()["detail"] == f"{label} must be between 0 and 100%."

    # The full component-save route shares the same hook and guard.
    resp = client.patch(f"/api/tasks/{task['task_id']}",
                        json={"fields": {field_key: "nan"}, "comments": "attempt"})
    assert resp.status_code == 400
    assert resp.get_json()["detail"] == f"{label} must be between 0 and 100%."

    # Every refusal is atomic: neither the invalid field nor the comment lands.
    fields = client.get(f"/api/tasks/{task['task_id']}/dynamic-fields").get_json()
    assert field_key not in fields
    task = get_task_by_name(client, pid, "Trap and Seal CoS")
    assert not (task.get("comments") or "")


def test_trap_cos_save_keeps_stored_value_when_thickness_missing(client):
    """When the Thickness Estimation task has no Sarah prognosis thickness yet,
    an INPUT-ONLY save cannot compute (calculate_trap_cos returns None) and
    must leave the stored trap_cos_pct untouched; a payload carrying the pct
    explicitly stores it regardless (the client is the primary calculator)."""
    pid = create_project(client, "TRAP-CALC-2")
    trap = get_task_by_name(client, pid, "Trap and Seal CoS")
    resp = client.patch(f"/api/tasks/{trap['task_id']}/dynamic-fields", json={"fields": {
        "sarah_quwarah_thickness_ft": "250",
        "trap_cos_pct": "55",
    }})
    assert resp.status_code == 200
    fields = client.get(f"/api/tasks/{trap['task_id']}/dynamic-fields").get_json()
    assert fields.get("trap_cos_pct") == "55"

    # Input-only follow-up: still nothing to compute from, "55" survives.
    resp = client.patch(f"/api/tasks/{trap['task_id']}/dynamic-fields", json={"fields": {
        "sarah_quwarah_thickness_ft": "260",
    }})
    assert resp.status_code == 200
    fields = client.get(f"/api/tasks/{trap['task_id']}/dynamic-fields").get_json()
    assert fields.get("trap_cos_pct") == "55"


def test_lead_resource_assessment_save_never_overwrites_piip(client):
    """A plain save of the Resource Assessment step must leave its PIIP
    fields exactly as entered. The PIIP values now change only via the pop-up
    calculator's explicit Apply flow (POST .../resource-assessment) -- there is
    no auto-compute on save, so saved values are never silently overwritten."""
    pid = create_project(client, "LEADRA-STUB-1")
    areas = get_task_by_name(client, pid, "Lead Assessment")
    client.patch(f"/api/tasks/{areas['task_id']}/dynamic-fields",
                 json={"fields": {"p90_area_km2": "5", "p10_area_km2": "12"}})
    thickness = get_task_by_name(client, pid, "Lead Assessment")
    client.patch(f"/api/tasks/{thickness['task_id']}/dynamic-fields",
                 json={"fields": {"formation_thickness_ft": "110"}})

    lead_ra = get_task_by_name(client, pid, "Lead Assessment")
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
