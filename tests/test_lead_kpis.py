"""Card 1E -- the server half of the dashboard KPIs.

The board's Total Mean OGIP tile sums ``mean_gas_bcf`` over the filtered active
leads. That value is DERIVED AT READ TIME from task_dynamic_fields on the
LATEST_MEAN_GAS_SOURCES precedence (workflow/constants.py), which is the
server-side twin of the client's LATEST_PIIP_SOURCES
(static/js/views/detail-form.js): newest assessment first, each surviving v4
step immediately followed by the retired step it absorbed.

    SAD Update.resource_update_gas_mean
    Resource Assessment Update.resource_update_gas_mean          (retired)
    SAD Model.post_drill_piip_gas_mean
    Post-Drilling Resource Assessment.post_drill_piip_gas_mean   (retired)
    Pre-Drilling GeoX Assessment.pre_drill_piip_gas_mean
    Resource Assessment.lead_piip_gas_mean

Nothing here asserts a KPI number: the tiles themselves are pinned by the
front-end harness (static/tests/test-lead-kpis.js). This module pins the ONE
value the client cannot derive on its own.
"""
from __future__ import annotations

import logging

import pytest

from conftest import create_project, get_task_by_name, raw_sqlite_connect


def _save_fields(client, pid, task_name, fields):
    """Store dynamic fields through the real save route (no raw SQL)."""
    task = get_task_by_name(client, pid, task_name)
    assert task is not None, task_name
    resp = client.patch(f"/api/tasks/{task['task_id']}/dynamic-fields", json={"fields": fields})
    assert resp.status_code == 200, resp.get_json()


def _rows(client):
    resp = client.get("/api/projects")
    assert resp.status_code == 200
    return resp.get_json()


def _mean_gas(client, pid):
    """The ``mean_gas_bcf`` the LIST payload carries for one project."""
    for row in _rows(client):
        if row["project_id"] == pid:
            return row["mean_gas_bcf"]
    raise AssertionError(f"project {pid} missing from the board payload")


def _add_retired_task(client, pid, task_name, sequence_no, stage_group, fields):
    """Materialize a RETIRED (is_active = 0) task row carrying stored inputs.

    v4 merged four BP steps away; a pre-v4 well keeps those rows with
    ``is_active = 0`` and its numbers still live under the retired task_name.
    New projects never get such a row, so the legacy shape has to be written
    directly -- the same escape hatch tests/test_known_bugs.py uses.
    """
    conn = raw_sqlite_connect(client.db_path)
    try:
        cur = conn.execute(
            "INSERT INTO project_tasks (project_id, sequence_no, task_name, stage_group, "
            "status, priority, is_active) VALUES (?, ?, ?, ?, 'Not Assigned', 'Low', 0)",
            (pid, sequence_no, task_name, stage_group))
        task_id = cur.lastrowid
        for key, value in fields.items():
            conn.execute(
                "INSERT INTO task_dynamic_fields (task_id, field_key, field_value) VALUES (?, ?, ?)",
                (task_id, key, value))
        conn.commit()
    finally:
        conn.close()
    return task_id


# ---------------------------------------------------------------------------
# The precedence
# ---------------------------------------------------------------------------

def test_mean_gas_is_null_when_nothing_is_recorded(client):
    """Missing/blank reads null in the payload -- the tile treats it as 0."""
    pid = create_project(client, "MEANGAS-1")
    assert _mean_gas(client, pid) is None


def test_mean_gas_reads_the_lead_assessment_when_it_is_the_only_source(client):
    pid = create_project(client, "MEANGAS-2")
    _save_fields(client, pid, "Resource Assessment", {"lead_piip_gas_mean": "120.5"})
    assert _mean_gas(client, pid) == 120.5


def test_pre_drill_assessment_beats_the_lead_assessment(client):
    """Newest assessment wins: the pre-drill number supersedes the lead one."""
    pid = create_project(client, "MEANGAS-3")
    _save_fields(client, pid, "Resource Assessment", {"lead_piip_gas_mean": "120"})
    _save_fields(client, pid, "Pre-Drilling GeoX Assessment", {"pre_drill_piip_gas_mean": "310"})
    assert _mean_gas(client, pid) == 310.0


def test_post_drill_assessments_beat_the_pre_drill_one_in_order(client):
    """SAD Update > SAD Model > Pre-Drilling, each step added on top."""
    pid = create_project(client, "MEANGAS-4")
    _save_fields(client, pid, "Pre-Drilling GeoX Assessment", {"pre_drill_piip_gas_mean": "310"})
    assert _mean_gas(client, pid) == 310.0
    _save_fields(client, pid, "SAD Model", {"post_drill_piip_gas_mean": "420"})
    assert _mean_gas(client, pid) == 420.0
    _save_fields(client, pid, "SAD Update", {"resource_update_gas_mean": "505.25"})
    assert _mean_gas(client, pid) == 505.25


def test_legacy_only_data_resolves_through_a_retired_step(client):
    """A pre-v4 well whose only number sits on a RETIRED step still resolves.

    "Resource Assessment Update" was merged into "SAD Update" by v4 and its row
    survives as is_active = 0, holding the SAME EAV key. The read ladder is
    retired-inclusive, so the value is still the record's latest mean.
    """
    pid = create_project(client, "MEANGAS-5")
    _add_retired_task(client, pid, "Resource Assessment Update", 30, "Post-Testing",
                      {"resource_update_gas_mean": "777"})
    assert _mean_gas(client, pid) == 777.0


def test_legacy_post_drill_step_resolves_and_loses_to_a_newer_assessment(client):
    """The other retired twin, and the newer-assessment rule across the merge.

    "Post-Drilling Resource Assessment" (retired, post_drill tier) reads on its
    own, but a value on the newer resource_update tier -- whether entered on
    the surviving "SAD Update" or on its retired twin -- outranks it.
    """
    pid = create_project(client, "MEANGAS-6")
    _add_retired_task(client, pid, "Post-Drilling Resource Assessment", 20, "Post-Drilling",
                      {"post_drill_piip_gas_mean": "600"})
    assert _mean_gas(client, pid) == 600.0
    _add_retired_task(client, pid, "Resource Assessment Update", 30, "Post-Testing",
                      {"resource_update_gas_mean": "900"})
    assert _mean_gas(client, pid) == 900.0


def test_surviving_step_beats_the_retired_twin_it_absorbed(client):
    """Both filled -> the SURVIVING step wins, matching the shipped fallback
    order (surviving first, retired straight behind as the legacy fallback)."""
    pid = create_project(client, "MEANGAS-7")
    _add_retired_task(client, pid, "Resource Assessment Update", 30, "Post-Testing",
                      {"resource_update_gas_mean": "900"})
    _save_fields(client, pid, "SAD Update", {"resource_update_gas_mean": "950"})
    assert _mean_gas(client, pid) == 950.0


def test_blank_on_a_newer_step_does_not_erase_an_older_number(client):
    """A blank is "not recorded", not "recorded as nothing" -- first NON-BLANK
    source wins, so an empty SAD Update falls through to the lead assessment."""
    pid = create_project(client, "MEANGAS-8")
    _save_fields(client, pid, "Resource Assessment", {"lead_piip_gas_mean": "120"})
    _save_fields(client, pid, "SAD Update", {"resource_update_gas_mean": "   "})
    assert _mean_gas(client, pid) == 120.0


def test_p90_and_p10_are_never_used_to_derive_the_mean(client):
    """The mean is a saved input in its own right; a record that has only the
    low/high cases has NO mean, and the tile must not interpolate one."""
    pid = create_project(client, "MEANGAS-9")
    _save_fields(client, pid, "Resource Assessment",
                 {"lead_piip_gas_p90": "80", "lead_piip_gas_p10": "400"})
    assert _mean_gas(client, pid) is None


def test_non_numeric_stored_value_reads_null_and_is_logged(client, caplog):
    """Garbage in the cell is a data fault: null in the payload (the tile shows
    0 BCF rather than breaking) plus a warning naming the project and step."""
    pid = create_project(client, "MEANGAS-10")
    _save_fields(client, pid, "Resource Assessment", {"lead_piip_gas_mean": "not a number"})
    with caplog.at_level(logging.WARNING, logger="workflow.projects"):
        assert _mean_gas(client, pid) is None
    assert any("not a number" in record.getMessage() and str(pid) in record.getMessage()
               for record in caplog.records), caplog.text


def test_non_numeric_latest_does_not_fall_through_to_an_older_assessment(client):
    """A broken LATEST value reports null rather than presenting a superseded
    assessment as if it were current."""
    pid = create_project(client, "MEANGAS-11")
    _save_fields(client, pid, "Resource Assessment", {"lead_piip_gas_mean": "120"})
    _save_fields(client, pid, "SAD Update", {"resource_update_gas_mean": "TBD"})
    assert _mean_gas(client, pid) is None


# ---------------------------------------------------------------------------
# Batching -- the board must not multiply queries by project count
# ---------------------------------------------------------------------------

def test_mean_gas_costs_exactly_one_query_for_the_whole_board(client, app_modules):
    """ONE batched EAV query per board read, regardless of how many leads.

    The board payload is a few hundred rows in production; a per-project lookup
    here would be a few hundred round trips. Counting the statements that touch
    task_dynamic_fields is the direct proof.
    """
    from sqlalchemy import event

    _main, db = app_modules
    ids = [create_project(client, f"MEANBATCH-{n}") for n in range(4)]
    for index, pid in enumerate(ids):
        _save_fields(client, pid, "Resource Assessment",
                     {"lead_piip_gas_mean": str(100 + index)})

    seen = []

    def _record(_conn, _cursor, statement, *_args):
        if "task_dynamic_fields" in statement and statement.lstrip().upper().startswith("SELECT"):
            seen.append(statement)

    engine = db.get_engine()
    event.listen(engine, "before_cursor_execute", _record)
    try:
        rows = _rows(client)
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    # The board query itself carries an active_drilling subquery over
    # task_dynamic_fields; the mean-gas read adds exactly one more.
    assert len(seen) == 2, seen
    by_id = {row["project_id"]: row["mean_gas_bcf"] for row in rows}
    assert [by_id[pid] for pid in ids] == [100.0, 101.0, 102.0, 103.0]


@pytest.mark.parametrize("stored,expected", [
    ("0", 0.0),
    ("0.0", 0.0),
    ("1234.567", 1234.567),
    ("  860  ", 860.0),
])
def test_stored_value_is_reported_in_bcf_exactly_as_stored(client, stored, expected):
    """No unit conversion and no rounding server-side: the client sums at full
    precision and rounds ONCE for display. Note 0 is a real recorded value and
    is NOT null."""
    pid = create_project(client, f"MEANUNIT-{stored.strip() or 'blank'}")
    _save_fields(client, pid, "Resource Assessment", {"lead_piip_gas_mean": stored})
    assert _mean_gas(client, pid) == expected
