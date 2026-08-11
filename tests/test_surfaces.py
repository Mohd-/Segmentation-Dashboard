"""Tests for the ZMAP+ grid reader (surfaces.py) and the surface auto-fills
(workflow/surfaces_fill.py).

The parser/sampling half runs against the canonical 3x3 grid in
tests/zmap_fixtures.py (see its docstring for the hand-computed reference
points). The fill half runs against the normal per-test app database, with the
surface file locations re-pointed through their env overrides
(SEGMENT_TRACKER_TSQ_SURFACE_FILE / SEGMENT_TRACKER_GROUND_ELEVATION_SURFACE_FILE)
-- config reads them lazily, so a monkeypatched env is all it takes.
"""
from __future__ import annotations

import os
import subprocess
import sys
import json
from datetime import date
from pathlib import Path

import pytest

import surfaces
from conftest import create_project, get_task_by_name, raw_sqlite_connect
from zmap_fixtures import (GRID_VALUES, GRID_XMAX, GRID_XMIN, GRID_YMAX, GRID_YMIN,
                           write_sample_grid, write_zmap_grid)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def test_load_grid_parses_shape_extents_values_and_nulls(tmp_path):
    grid = surfaces.load_grid(write_sample_grid(tmp_path / "grid.dat"))
    assert (grid.rows, grid.cols) == (3, 3)
    assert (grid.xmin, grid.xmax, grid.ymin, grid.ymax) == (
        GRID_XMIN, GRID_XMAX, GRID_YMIN, GRID_YMAX)
    assert grid.null_value == -99999.0
    # Every node reads back at its (col, row-from-top) address...
    for row_index, row_values in enumerate(GRID_VALUES):
        for col_index, expected in enumerate(row_values):
            assert grid.node(col_index, row_index) == expected
    # ... and the declared null value became None, never a -99999.0 "reading".
    assert grid.node(2, 0) is None
    assert -99999.0 not in [value for value in grid.values if value is not None]


def test_load_grid_skips_comments_and_treats_blank_fields_as_missing(tmp_path):
    path = tmp_path / "commented.dat"
    path.write_text(
        "! a leading comment\n"
        "@ANY NAME AT ALL, GRID, 2\n"
        "  15, -99999.0, , 4, 1\n"
        "! a comment inside the header\n"
        "  2, 2, 0.0, 100.0, 0.0, 100.0\n"
        "  0.0, 0.0, 0.0\n"
        "@\n"
        "  1.0, ,\n"          # blank comma field = missing node
        "  3.0 4.0\n",         # space-separated works too
        encoding="utf-8")
    grid = surfaces.load_grid(path)
    assert grid.values == [1.0, None, 3.0, 4.0]


def test_load_grid_reads_canonical_fixed_width_fields_and_text_nulls(tmp_path):
    path = tmp_path / "fixed.dat"
    width = 10
    fields = ["1234", "NULL", "3.5D+0", "4000"]
    path.write_text(
        "@FIXED WIDTH, GRID, 4\n"
        "10, , NULL, 2, 3\n"
        "2, 2, 0, 1, 0, 1\n"
        "0.0, 0.0, 0.0\n"
        "@\n"
        "xx" + "".join(value.rjust(width) for value in fields) + "\n",
        encoding="utf-8")

    grid = surfaces.load_grid(path)
    # No decimal point means the header's two implied decimal places; text
    # nulls and Fortran D exponents are both common exporter variants.
    assert grid.values == [12.34, None, 3.5, 40.0]
    assert grid.null_value is None
    assert grid.null_text == "NULL"


def test_load_grid_recaches_when_the_file_changes(tmp_path):
    path = write_sample_grid(tmp_path / "grid.dat")
    assert surfaces.load_grid(path).node(0, 2) == 30.0
    doubled = [[None if value is None else value * 2 for value in row] for row in GRID_VALUES]
    write_zmap_grid(path, doubled, GRID_XMIN, GRID_XMAX, GRID_YMIN, GRID_YMAX)
    # Force a different mtime -- a same-second rewrite is exactly the staleness
    # window the (path, mtime) key must catch.
    stat = os.stat(path)
    os.utime(path, (stat.st_atime, stat.st_mtime + 10))
    assert surfaces.load_grid(path).node(0, 2) == 60.0


def test_load_grid_recaches_an_atomic_replacement_with_preserved_mtime(tmp_path):
    path = write_sample_grid(tmp_path / "grid.dat")
    first_stat = path.stat()
    assert surfaces.load_grid(path).node(0, 2) == 30.0

    replacement = tmp_path / "replacement.dat"
    doubled = [[None if value is None else value * 2 for value in row] for row in GRID_VALUES]
    write_zmap_grid(replacement, doubled, GRID_XMIN, GRID_XMAX, GRID_YMIN, GRID_YMAX)
    os.utime(replacement, ns=(first_stat.st_atime_ns, first_stat.st_mtime_ns))
    replacement.replace(path)

    assert surfaces.load_grid(path).node(0, 2) == 60.0


@pytest.mark.parametrize("body", [
    "not a grid at all\n",
    # Right header, one node value short of rows * cols.
    "@T, GRID, 4\n 15, -99999.0, , 4, 1\n 2, 2, 0.0, 1.0, 0.0, 1.0\n 0.0, 0.0, 0.0\n@\n 1.0 2.0 3.0\n",
    # Truncated header (no records, no terminator).
    "@T, GRID, 4\n",
    # Not a GRID section.
    "@T, VERT, 4\n 15, -99999.0, , 4, 1\n 2, 2, 0.0, 1.0, 0.0, 1.0\n 0.0, 0.0, 0.0\n@\n",
    # Unreadable node token.
    "@T, GRID, 4\n 15, -99999.0, , 4, 1\n 2, 2, 0.0, 1.0, 0.0, 1.0\n 0.0, 0.0, 0.0\n@\n 1.0 2.0 3.0 oops\n",
    # Degenerate 1-column grid.
    "@T, GRID, 4\n 15, -99999.0, , 4, 1\n 2, 1, 0.0, 1.0, 0.0, 1.0\n 0.0, 0.0, 0.0\n@\n 1.0 2.0\n",
])
def test_load_grid_raises_value_error_on_a_defective_file(tmp_path, body):
    path = tmp_path / "bad.dat"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError):
        surfaces.load_grid(path)


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------

@pytest.fixture()
def grid(tmp_path):
    return surfaces.load_grid(write_sample_grid(tmp_path / "grid.dat"))


def test_bilinear_interpolation_at_a_hand_computed_interior_point(grid):
    # Cell corners 10/40/20/50, dead centre -> the plain average.
    assert surfaces.sample(grid, 50.0, 150.0) == pytest.approx(30.0)
    # An off-centre point, weights hand-computed: tx=0.25, ty=0.75 in the
    # bottom-left cell (corners 20/50/30/60) -> 20*0.1875 + 50*0.0625 +
    # 30*0.5625 + 60*0.1875 = 35.0.
    assert surfaces.sample(grid, 25.0, 25.0) == pytest.approx(35.0)


def test_sample_exactly_on_a_node_returns_that_node_value(grid):
    assert surfaces.sample(grid, 100.0, 100.0) == pytest.approx(50.0)
    # Corner nodes -- the max-edge clamp must not push the lookup off-grid.
    assert surfaces.sample(grid, 0.0, 200.0) == pytest.approx(10.0)
    assert surfaces.sample(grid, 200.0, 0.0) == pytest.approx(90.0)


def test_sample_on_a_grid_line_uses_only_the_contributing_nodes(grid):
    # x = 200 exactly: the two right-edge nodes 80/90 interpolate; the null at
    # (200, 200) is elsewhere and the zero-weight column cannot veto.
    assert surfaces.sample(grid, 200.0, 50.0) == pytest.approx(85.0)


def test_sample_snaps_decimal_grid_lines_before_testing_null_contributions(tmp_path):
    values = [
        [1.0, 2.0, None],
        [3.0, 4.0, None],
        [5.0, 6.0, None],
    ]
    decimal_grid = surfaces.load_grid(write_zmap_grid(
        tmp_path / "decimal-grid.dat", values, 0.1, 0.3, 0.1, 0.3))
    # x=0.2 is exactly the middle column in the decimal header. Binary floating
    # point can put its fractional index infinitesimally above 1; the null third
    # column must still have zero weight and cannot veto this grid-line sample.
    assert surfaces.sample(decimal_grid, 0.2, 0.2) == pytest.approx(4.0)


def test_sample_outside_the_extents_returns_none(grid):
    assert surfaces.sample(grid, -0.1, 100.0) is None
    assert surfaces.sample(grid, 200.1, 100.0) is None
    assert surfaces.sample(grid, 100.0, -5.0) is None
    assert surfaces.sample(grid, 100.0, 200.5) is None
    assert surfaces.sample(grid, float("nan"), 100.0) is None
    assert surfaces.sample(grid, "not a number", 100.0) is None


def test_sample_returns_none_when_any_contributing_node_is_null(grid):
    # (150, 150) leans on the null node at (200, 200): a hole stays a hole.
    assert surfaces.sample(grid, 150.0, 150.0) is None
    # Exactly ON the null node is a hole too -- never a nearest-neighbour guess.
    assert surfaces.sample(grid, 200.0, 200.0) is None


def test_sample_surface_never_raises(tmp_path):
    # Falsy path, missing file, corrupt file: all None, no exception.
    assert surfaces.sample_surface(None, 50, 50) is None
    assert surfaces.sample_surface("", 50, 50) is None
    assert surfaces.sample_surface(tmp_path / "absent.dat", 50, 50) is None
    corrupt = tmp_path / "corrupt.dat"
    corrupt.write_text("@T, GRID, 4\n garbage everywhere\n", encoding="utf-8")
    assert surfaces.sample_surface(corrupt, 50, 50) is None
    binary = tmp_path / "binary.dat"
    binary.write_bytes(b"\x00\xff\xfe not text \x9c")
    assert surfaces.sample_surface(binary, 50, 50) is None
    # And a healthy file still samples through the same entry point.
    good = write_sample_grid(tmp_path / "good.dat")
    assert surfaces.sample_surface(good, 50.0, 150.0) == pytest.approx(30.0)


def test_backfill_dry_run_reports_an_unreadable_database_without_a_traceback(tmp_path):
    invalid = tmp_path / "not-a-database.db"
    invalid.write_text("not sqlite", encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "scripts" / "backfill_surfaces.py"

    completed = subprocess.run(
        [sys.executable, str(script), str(invalid), "--dry-run"],
        text=True, capture_output=True, check=False)

    assert completed.returncode == 2
    assert "Could not copy database for dry run" in completed.stderr
    assert "Traceback" not in completed.stderr


# ---------------------------------------------------------------------------
# fill_tsq -- the Trap and Seal CoS thickness auto-fill
# ---------------------------------------------------------------------------

def _run_fill(fill, project_id):
    """Run one fill the way the save-time wiring will: inside the caller's
    write transaction, committed on success."""
    import db

    session = db.new_session()
    try:
        with db.write_transaction(session):
            return fill(session, project_id)
    finally:
        session.close()


def _tsq_fields(client, project_id):
    task = get_task_by_name(client, project_id, "Trap and Seal CoS")
    return task, client.get(f"/api/tasks/{task['task_id']}/dynamic-fields").get_json()


def _tsq_history(db_path, project_id):
    conn = raw_sqlite_connect(db_path)
    try:
        return [dict(row) for row in conn.execute(
            "SELECT task_name, action_type, changed_by, comment FROM task_history "
            "WHERE project_id = ? AND comment LIKE 'Auto-filled from TSQ surface%' "
            "ORDER BY history_id", (project_id,))]
    finally:
        conn.close()


@pytest.fixture()
def tsq_surface(tmp_path, monkeypatch):
    path = write_sample_grid(tmp_path / "tsq.dat")
    monkeypatch.delenv("SEGMENT_TRACKER_TSQ_SURFACE_FILE", raising=False)

    def configure():
        monkeypatch.setenv("SEGMENT_TRACKER_TSQ_SURFACE_FILE", str(path))
        return path

    return configure


@pytest.fixture()
def elevation_surface(tmp_path, monkeypatch):
    path = write_sample_grid(tmp_path / "elevation.dat")
    monkeypatch.delenv("SEGMENT_TRACKER_GROUND_ELEVATION_SURFACE_FILE", raising=False)

    def configure():
        monkeypatch.setenv("SEGMENT_TRACKER_GROUND_ELEVATION_SURFACE_FILE", str(path))
        return path

    return configure


def test_fill_tsq_fills_an_empty_field_and_logs_the_history_event(client, tsq_surface):
    from workflow import surfaces_fill

    pid = create_project(client, "SURF-TSQ-1", lead_x="50", lead_y="150")
    tsq_surface()
    assert _run_fill(surfaces_fill.fill_tsq, pid) == pytest.approx(30.0)

    _task, fields = _tsq_fields(client, pid)
    assert fields["sarah_quwarah_thickness_ft"] == "30"
    events = _tsq_history(client.db_path, pid)
    assert len(events) == 1
    assert events[0]["task_name"] == "Trap and Seal CoS"
    assert events[0]["action_type"] == "Component Inputs Updated"
    assert events[0]["changed_by"] == surfaces_fill.SURFACE_FILL_ACTOR
    assert "30" in events[0]["comment"]

    # A second pass is a no-op: the field now has a value (the auto-filled one
    # counts), so nothing is rewritten and no second event appears.
    assert _run_fill(surfaces_fill.fill_tsq, pid) is None
    assert len(_tsq_history(client.db_path, pid)) == 1


def test_fill_tsq_never_overwrites_a_manual_entry(client, tsq_surface):
    from workflow import surfaces_fill

    pid = create_project(client, "SURF-TSQ-2", lead_x="50", lead_y="150")
    task, _fields = _tsq_fields(client, pid)
    assert client.patch(f"/api/tasks/{task['task_id']}/dynamic-fields",
                        json={"fields": {"sarah_quwarah_thickness_ft": "999"}}).status_code == 200

    tsq_surface()
    assert _run_fill(surfaces_fill.fill_tsq, pid) is None
    _task, fields = _tsq_fields(client, pid)
    assert fields["sarah_quwarah_thickness_ft"] == "999"
    assert _tsq_history(client.db_path, pid) == []


def test_fill_tsq_prefers_staked_coordinates_over_the_lead_pair(client, tsq_surface):
    import db
    import workflow
    from workflow import surfaces_fill

    # Lead pair samples 30.0; the staked pair sits exactly on the 50.0 node and
    # must supersede it (mapdata's precedence, mirrored).
    pid = create_project(client, "SURF-TSQ-3", lead_x="50", lead_y="150")
    staking = get_task_by_name(client, pid, "Well Site Location")
    session = db.new_session()
    try:
        workflow.save_task_dynamic_fields(session, staking["task_id"],
                                          {"staked_x": "100", "staked_y": "100"},
                                          changed_by="Bulk Writer", reconcile=False)
    finally:
        session.close()

    tsq_surface()
    assert _run_fill(surfaces_fill.fill_tsq, pid) == pytest.approx(50.0)
    _task, fields = _tsq_fields(client, pid)
    assert fields["sarah_quwarah_thickness_ft"] == "50"


def test_fill_tsq_is_a_quiet_noop_when_it_has_nothing_to_say(client, tmp_path, monkeypatch, tsq_surface):
    from workflow import surfaces_fill

    # No coordinates at all -> None, nothing written.
    bare = create_project(client, "SURF-TSQ-NOCOORD")
    tsq_surface()
    assert _run_fill(surfaces_fill.fill_tsq, bare) is None
    assert _tsq_history(client.db_path, bare) == []

    # Coordinates outside the surface extents -> None, nothing written.
    outside = create_project(client, "SURF-TSQ-OUTSIDE", lead_x="512000", lead_y="2903000")
    assert _run_fill(surfaces_fill.fill_tsq, outside) is None

    # Unconfigured/missing surface file -> None even with good coordinates.
    monkeypatch.setenv("SEGMENT_TRACKER_TSQ_SURFACE_FILE", str(tmp_path / "not-there.dat"))
    covered = create_project(client, "SURF-TSQ-NOSURF", lead_x="50", lead_y="150")
    assert _run_fill(surfaces_fill.fill_tsq, covered) is None
    assert _tsq_history(client.db_path, covered) == []

    # A corrupt surface must not break the call either (sample_surface's
    # never-raise contract carried through the fill).
    corrupt = tmp_path / "corrupt.dat"
    corrupt.write_text("@T, GRID, 4\nnot a header\n", encoding="utf-8")
    monkeypatch.setenv("SEGMENT_TRACKER_TSQ_SURFACE_FILE", str(corrupt))
    assert _run_fill(surfaces_fill.fill_tsq, covered) is None


# ---------------------------------------------------------------------------
# Save-time wiring
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("save_path", ["dynamic-fields", "full-save"])
def test_staked_coordinate_save_fills_both_surfaces_cos_and_history(
        client, tsq_surface, elevation_surface, save_path):
    import cos

    pid = create_project(client, "SURF-WIRED-{}".format(save_path))
    lead_assessment = get_task_by_name(client, pid, "Lead Assessment")
    response = client.patch(
        f"/api/tasks/{lead_assessment['task_id']}/dynamic-fields",
        json={"fields": {"formation_thickness_ft": "20"}},
    )
    assert response.status_code == 200

    # Configure the surfaces only after the prerequisite thickness save, so
    # the staked-coordinate save below is the operation that triggers both
    # fills.  (The project has no lead-coordinate fallback.)
    tsq_surface()
    elevation_surface()
    staking = get_task_by_name(client, pid, "Well Site Location")
    endpoint = (f"/api/tasks/{staking['task_id']}/dynamic-fields"
                if save_path == "dynamic-fields" else f"/api/tasks/{staking['task_id']}")
    response = client.patch(endpoint, json={"fields": {
        "staked_x": "50", "staked_y": "150",
    }})
    assert response.status_code == 200

    _task, fields = _tsq_fields(client, pid)
    assert fields["sarah_quwarah_thickness_ft"] == "30"
    assert fields["trap_cos_pct"] == cos.calculate_trap_cos("20", "30") == "85"
    assert _stored_elevation(client.db_path, pid) == pytest.approx(30.0)
    events = _tsq_history(client.db_path, pid)
    assert len(events) == 1
    assert events[0]["changed_by"] == "System (surface auto-fill)"


def test_surface_fill_preserves_an_explicit_manual_trap_cos(
        client, tsq_surface, elevation_surface):
    pid = create_project(client, "SURF-WIRED-MANUAL")
    trap, _fields = _tsq_fields(client, pid)
    response = client.patch(
        f"/api/tasks/{trap['task_id']}/dynamic-fields",
        json={"fields": {"trap_cos_pct": "42"}},
    )
    assert response.status_code == 200

    tsq_surface()
    elevation_surface()
    staking = get_task_by_name(client, pid, "Well Site Location")
    response = client.patch(
        f"/api/tasks/{staking['task_id']}/dynamic-fields",
        json={"fields": {"staked_x": "50", "staked_y": "150"}},
    )
    assert response.status_code == 200

    _task, fields = _tsq_fields(client, pid)
    assert fields["sarah_quwarah_thickness_ft"] == "30"
    assert fields["trap_cos_pct"] == "42"
    assert _stored_elevation(client.db_path, pid) == pytest.approx(30.0)


def test_surface_directory_paths_are_unavailable_and_save_is_a_quiet_noop(
        client, tmp_path, monkeypatch):
    surface_directory = tmp_path / "configured-as-directory"
    surface_directory.mkdir()
    monkeypatch.setenv("SEGMENT_TRACKER_TSQ_SURFACE_FILE", str(surface_directory))
    monkeypatch.setenv("SEGMENT_TRACKER_GROUND_ELEVATION_SURFACE_FILE",
                       str(surface_directory))

    pid = create_project(client, "SURF-WIRED-DIRECTORY", lead_x="50", lead_y="150")
    staking = get_task_by_name(client, pid, "Well Site Location")
    response = client.patch(
        f"/api/tasks/{staking['task_id']}/dynamic-fields",
        json={"fields": {"staked_x": "100", "staked_y": "100"}},
    )
    assert response.status_code == 200
    _task, fields = _tsq_fields(client, pid)
    assert "sarah_quwarah_thickness_ft" not in fields
    assert _stored_elevation(client.db_path, pid) is None
    assert _tsq_history(client.db_path, pid) == []


# ---------------------------------------------------------------------------
# fill_ground_elevation -- the machine-derived projects column
# ---------------------------------------------------------------------------

def _stored_elevation(db_path, project_id):
    conn = raw_sqlite_connect(db_path)
    try:
        return conn.execute("SELECT ground_elevation FROM projects WHERE project_id = ?",
                            (project_id,)).fetchone()["ground_elevation"]
    finally:
        conn.close()


def _set_elevation(db_path, project_id, value):
    conn = raw_sqlite_connect(db_path)
    try:
        conn.execute("UPDATE projects SET ground_elevation = ? WHERE project_id = ?",
                     (value, project_id))
        conn.commit()
    finally:
        conn.close()


def test_fill_ground_elevation_writes_and_freely_overwrites(client, elevation_surface):
    from workflow import surfaces_fill

    pid = create_project(client, "SURF-ELEV-1", lead_x="50", lead_y="150")
    elevation_surface()
    assert _stored_elevation(client.db_path, pid) is None
    assert _run_fill(surfaces_fill.fill_ground_elevation, pid) == pytest.approx(30.0)
    assert _stored_elevation(client.db_path, pid) == pytest.approx(30.0)

    # Machine-derived: a stale stored number is simply replaced on the next run.
    _set_elevation(client.db_path, pid, 999.0)
    assert _run_fill(surfaces_fill.fill_ground_elevation, pid) == pytest.approx(30.0)
    assert _stored_elevation(client.db_path, pid) == pytest.approx(30.0)


def test_fill_ground_elevation_leaves_the_stored_value_when_it_has_nothing_to_say(
        client, tmp_path, monkeypatch, elevation_surface):
    from workflow import surfaces_fill

    # No coordinates: the existing value is NOT nulled out.
    bare = create_project(client, "SURF-ELEV-NOCOORD")
    _set_elevation(client.db_path, bare, 42.0)
    elevation_surface()
    assert _run_fill(surfaces_fill.fill_ground_elevation, bare) is None
    assert _stored_elevation(client.db_path, bare) == pytest.approx(42.0)

    # No value at the point (outside extents): untouched too.
    pid = create_project(client, "SURF-ELEV-OUTSIDE", lead_x="512000", lead_y="2903000")
    _set_elevation(client.db_path, pid, 42.0)
    assert _run_fill(surfaces_fill.fill_ground_elevation, pid) is None
    assert _stored_elevation(client.db_path, pid) == pytest.approx(42.0)

    # No surface configured: untouched.
    monkeypatch.setenv("SEGMENT_TRACKER_GROUND_ELEVATION_SURFACE_FILE",
                       str(tmp_path / "not-there.dat"))
    assert _run_fill(surfaces_fill.fill_ground_elevation, pid) is None
    assert _stored_elevation(client.db_path, pid) == pytest.approx(42.0)


# ---------------------------------------------------------------------------
# BP Gate governed TD / drilling-days calculations
# ---------------------------------------------------------------------------

def _bp_project(client, name, **kwargs):
    return create_project(
        client, name, pipeline_type="bp", business_plan_enabled=True,
        business_plan_year=date.today().year, **kwargs)


def _bp_calc_fields(client, project_id):
    task = get_task_by_name(client, project_id, "BP Execution Gate")
    return task, client.get(f"/api/tasks/{task['task_id']}/dynamic-fields").get_json()


def _save_bp_field(client, project_id, key, value):
    return client.patch(
        f"/api/business-plan/wells/{project_id}/steps/business-plan-gate/field",
        json={"field_key": key, "value": value})


def _save_lead_assessment_prognosis(client, project_id, value):
    task = get_task_by_name(client, project_id, "Lead Assessment")
    return client.patch(
        f"/api/tasks/{task['task_id']}/dynamic-fields",
        json={"fields": {"sarh_formation_prognosis_pre_drill": value}})


def test_bp_calculations_use_lead_assessment_prognosis_and_half_up_rounding(
        client, tmp_path, monkeypatch):
    from workflow import surfaces_fill

    elevation = write_zmap_grid(
        tmp_path / "elevation.dat", [[20.25] * 3 for _ in range(3)],
        GRID_XMIN, GRID_XMAX, GRID_YMIN, GRID_YMAX)
    monkeypatch.setenv("SEGMENT_TRACKER_GROUND_ELEVATION_SURFACE_FILE", str(elevation))
    # The removed SARH-thickness setting must not be a hidden BP dependency.
    monkeypatch.setenv("SEGMENT_TRACKER_SARH_THICKNESS_SURFACE_FILE",
                       str(tmp_path / "obsolete-missing.dat"))

    pid = _bp_project(client, "BPE-CALC-1", lead_x="50", lead_y="150")
    response = _save_lead_assessment_prognosis(client, pid, "30.25")
    assert response.status_code == 200, response.get_json()
    response = _save_bp_field(client, pid, "bp_gate_classification", "Appraisal")
    assert response.status_code == 200, response.get_json()
    values = response.get_json()["detail"]["values"]
    assert values["bp_gate_calculated_td_ft_md"] == "1251"
    assert values["bp_gate_calculated_drilling_days"] == "127"
    assert not values.get("bp_gate_actual_td_ft_md")
    assert not values.get("bp_gate_actual_drilling_days")

    response = _save_bp_field(client, pid, "bp_gate_coring_program", "Yes")
    assert response.get_json()["detail"]["values"]["bp_gate_calculated_drilling_days"] == "137"
    metadata = response.get_json()["detail"]["calculations"]
    assert metadata[surfaces_fill.BP_TD_FIELD_KEY]["status"] == "calculated"
    assert metadata[surfaces_fill.BP_TD_FIELD_KEY]["inputs"] == {
        "base_ft": 1200.0, "x": 50.0, "y": 150.0,
        "sarh_formation_prognosis_pre_drill": 30.25,
        "digital_elevation_ft": 20.25,
    }
    assert metadata[surfaces_fill.BP_TD_FIELD_KEY]["formula"] == (
        "TD base + Lead Assessment.sarh_formation_prognosis_pre_drill + "
        "digital elevation at well X/Y")


def test_bp_td_recomputes_with_staked_coordinate_precedence(
        client, elevation_surface):
    import db
    import workflow

    elevation_surface()
    pid = _bp_project(client, "BPE-CALC-STAKED", lead_x="50", lead_y="150")
    assert _save_lead_assessment_prognosis(client, pid, "30").status_code == 200
    assert _save_bp_field(client, pid, "bp_gate_classification", "Development").status_code == 200
    assert _bp_calc_fields(client, pid)[1]["bp_gate_calculated_td_ft_md"] == "1260"

    staking = get_task_by_name(client, pid, "Well Site Location")
    session = db.new_session()
    try:
        workflow.save_task_dynamic_fields(
            session, staking["task_id"], {"staked_x": "100", "staked_y": "100"},
            changed_by="Test", reconcile=False)
    finally:
        session.close()
    assert _bp_calc_fields(client, pid)[1]["bp_gate_calculated_td_ft_md"] == "1280"


@pytest.mark.parametrize("prognosis", [None, "", "not-a-number", "nan", "inf", "-inf"])
def test_bp_td_requires_a_finite_lead_assessment_prognosis(
        client, elevation_surface, prognosis):
    from workflow import surfaces_fill

    elevation_surface()
    pid = _bp_project(client, "BPE-CALC-BAD-PROGNOSIS", lead_x="50", lead_y="150")
    assert _save_bp_field(client, pid, "bp_gate_classification", "Development").status_code == 200

    if prognosis is not None:
        lead = get_task_by_name(client, pid, "Lead Assessment")
        conn = raw_sqlite_connect(client.db_path)
        with conn:
            conn.execute(
                "INSERT INTO task_dynamic_fields (task_id, field_key, field_value, updated_at) "
                "VALUES (?, ?, ?, '2026-01-01') "
                "ON CONFLICT(task_id, field_key) DO UPDATE SET field_value=excluded.field_value",
                (lead["task_id"], "sarh_formation_prognosis_pre_drill", prognosis))
        conn.close()

    result = _run_fill(surfaces_fill.fill_bp_calculations, pid)
    assert result["td"]["status"] == "unavailable"
    assert result["td"]["unavailable_reason"] == "Lead Assessment SARH prognosis"
    assert result["td"]["inputs"]["sarh_formation_prognosis_pre_drill"] is None
    fields = _bp_calc_fields(client, pid)[1]
    assert fields["bp_gate_calculated_td_ft_md"] == ""


def test_bp_calculation_archives_legacy_value_and_clears_stale_active_output(
        client, elevation_surface, tmp_path, monkeypatch):
    from workflow import surfaces_fill

    pid = _bp_project(client, "BPE-CALC-LEGACY", lead_x="50", lead_y="150")
    gate = get_task_by_name(client, pid, "BP Execution Gate")
    conn = raw_sqlite_connect(client.db_path)
    with conn:
        for key, value in {
            "bp_gate_calculated_td_ft_md": "9999",
            "bp_gate_calculated_td_source": "Supervisor override",
            "bp_gate_calculated_td_override_reason": "Historic approved exception",
        }.items():
            conn.execute(
                "INSERT INTO task_dynamic_fields (task_id, field_key, field_value, updated_at) "
                "VALUES (?, ?, ?, '2026-01-01') "
                "ON CONFLICT(task_id, field_key) DO UPDATE SET field_value=excluded.field_value",
                (gate["task_id"], key, value))
    conn.close()

    _save_lead_assessment_prognosis(client, pid, "30")
    elevation_surface()
    result = _run_fill(surfaces_fill.fill_bp_calculations, pid)
    assert result["td"]["legacy"] == {
        "value": "9999", "source": "Supervisor override",
        "reason": "Historic approved exception",
    }
    fields = _bp_calc_fields(client, pid)[1]
    assert fields["bp_gate_calculated_td_ft_md"] == "1260"
    assert fields["bp_gate_calculated_td_source"] == "System calculation"
    assert fields["bp_gate_calculated_td_override_reason"] == ""
    assert json.loads(fields["bp_gate_calculated_td_metadata"])["legacy"]["value"] == "9999"

    monkeypatch.setenv("SEGMENT_TRACKER_GROUND_ELEVATION_SURFACE_FILE",
                       str(tmp_path / "missing.dat"))
    result = _run_fill(surfaces_fill.fill_bp_calculations, pid)
    assert result["td"]["status"] == "unavailable"
    fields = _bp_calc_fields(client, pid)[1]
    assert fields["bp_gate_calculated_td_ft_md"] == ""
    assert json.loads(fields["bp_gate_calculated_td_metadata"])["legacy"]["value"] == "9999"


def test_bp_calculation_invalid_configuration_makes_both_outputs_unavailable(
        client, elevation_surface, tmp_path, monkeypatch):
    from workflow import surfaces_fill

    elevation_surface()
    pid = _bp_project(client, "BPE-CALC-BAD-CONFIG", lead_x="50", lead_y="150")
    assert _save_lead_assessment_prognosis(client, pid, "30").status_code == 200
    assert _save_bp_field(client, pid, "bp_gate_classification", "Exploration").status_code == 200
    bad = tmp_path / "bad-bp-calculations.json"
    bad.write_text('{"td_base_ft": "not a number"}', encoding="utf-8")
    monkeypatch.setenv("SEGMENT_TRACKER_BP_CALCULATIONS_PATH", str(bad))
    result = _run_fill(surfaces_fill.fill_bp_calculations, pid)
    assert result["td"]["status"] == "unavailable"
    assert result["days"]["status"] == "unavailable"
    fields = _bp_calc_fields(client, pid)[1]
    assert fields["bp_gate_calculated_td_ft_md"] == ""
    assert fields["bp_gate_calculated_drilling_days"] == ""


def test_bp_promotion_recomputes_preloaded_gate_inputs(
        client, elevation_surface):
    elevation_surface()
    pid = create_project(client, "BPE-CALC-PROMOTE", lead_x="50", lead_y="150")
    assert _save_lead_assessment_prognosis(client, pid, "30").status_code == 200
    gate = get_task_by_name(client, pid, "BP Execution Gate")
    conn = raw_sqlite_connect(client.db_path)
    with conn:
        for key, value in {
            "bp_gate_classification": "Exploration",
            "bp_gate_coring_program": "Yes",
        }.items():
            conn.execute(
                "INSERT INTO task_dynamic_fields (task_id, field_key, field_value, updated_at) "
                "VALUES (?, ?, ?, '2026-01-01')",
                (gate["task_id"], key, value))
    conn.close()

    promoted = client.patch(f"/api/projects/{pid}/flags", json={
        "business_plan_enabled": True,
        "business_plan_year": date.today().year,
    })
    assert promoted.status_code == 200, promoted.get_json()
    fields = _bp_calc_fields(client, pid)[1]
    assert fields["bp_gate_calculated_td_ft_md"] == "1260"
    assert fields["bp_gate_calculated_drilling_days"] == "137"
