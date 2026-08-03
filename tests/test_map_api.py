"""Contract tests for the map surface: /api/map/layers, /<name> and /wells.

The layer routes read FILES (map_layers.py), so every test here re-points
config.map_data_dir() at a pytest tmp directory through the
SEGMENT_TRACKER_MAP_DATA_DIR env var -- the same lazy-read pattern the DB path
uses -- and generates its shapefiles with pyshp. Nothing touches the
repository's own data/map tree.

The wells route reads the DATABASE (workflow.map_wells) and uses the ordinary
`client` fixture's scratch database.
"""
from __future__ import annotations

import json

import pytest
import shapefile  # pyshp

from conftest import create_project, get_task_by_name, get_tasks

# Two rings: a 100 km square with a 20 km square hole. Pins the part-splitting
# in map_layers._shape_geometry -- the outer ring and the hole must come back
# as SEPARATE rings, not one 10-point stroke crossing the block.
OUTER_RING = [
    [400000.0, 2800000.0], [400000.0, 2900000.0], [500000.0, 2900000.0],
    [500000.0, 2800000.0], [400000.0, 2800000.0],
]
HOLE_RING = [
    [440000.0, 2840000.0], [460000.0, 2840000.0], [460000.0, 2860000.0],
    [440000.0, 2860000.0], [440000.0, 2840000.0],
]

BORDERS_JSON = {
    "name": "borders",
    "bbox": [200000.0, 2000000.0, 900000.0, 3600000.0],
    "features": [
        {"properties": {"name": "Saudi Arabia"},
         "geometry": {"type": "polygon",
                      "coordinates": [[[200000.0, 2000000.0], [900000.0, 3600000.0]]]}},
    ],
}


@pytest.fixture()
def map_dir(tmp_path, monkeypatch):
    """A scratch map data directory carrying one 2-ring polygon layer.

    Yields the directory itself so a test can add or remove the borders file;
    the layers live in ``<dir>/layers`` exactly as config lays them out.
    """
    data_dir = tmp_path / "mapdata"
    layers_dir = data_dir / "layers"
    layers_dir.mkdir(parents=True)
    monkeypatch.setenv("SEGMENT_TRACKER_MAP_DATA_DIR", str(data_dir))

    writer = shapefile.Writer(str(layers_dir / "test_blocks"), shapeType=shapefile.POLYGON)
    writer.field("name", "C", size=40)
    writer.field("area_km2", "N", decimal=1)
    writer.poly([OUTER_RING, HOLE_RING])
    writer.record("Block Z", 9600.0)
    writer.close()

    (data_dir / "borders_utm37.json").write_text(json.dumps(BORDERS_JSON), encoding="utf-8")
    return data_dir


# ---------------------------------------------------------------------------
# GET /api/map/layers
# ---------------------------------------------------------------------------

def test_layers_lists_borders_first(client, map_dir):
    """The borders pseudo-layer leads the list, flagged and with its bbox."""
    resp = client.get("/api/map/layers")
    assert resp.status_code == 200
    layers = resp.get_json()["layers"]
    assert [layer["name"] for layer in layers] == ["borders", "test_blocks"]

    borders = layers[0]
    assert borders["is_borders"] is True
    assert borders["geom_type"] == "line"
    assert borders["feature_count"] == 1
    assert borders["bbox"] == BORDERS_JSON["bbox"]

    # A real shapefile layer carries no is_borders flag; its bbox is read from
    # the .shp header, in UTM37 metres.
    blocks = layers[1]
    assert "is_borders" not in blocks
    assert blocks["geom_type"] == "polygon"
    assert blocks["feature_count"] == 1
    assert blocks["bbox"] == [400000.0, 2800000.0, 500000.0, 2900000.0]


def test_layers_without_a_borders_file_lists_only_shapefiles(client, map_dir):
    (map_dir / "borders_utm37.json").unlink()
    layers = client.get("/api/map/layers").get_json()["layers"]
    assert [layer["name"] for layer in layers] == ["test_blocks"]


def test_a_corrupt_shapefile_is_skipped_and_the_good_ones_still_list(client, map_dir):
    """A half-copied .shp must not blank the map.

    pyshp does NOT report one failure mode: a truncated header raises a bare
    struct.error out of the header unpack, not a ShapefileException, so the
    skip has to be by outcome ("could not read it") rather than by exception
    class.
    """
    (map_dir / "layers" / "aborted_copy.shp").write_bytes(b"\x00\x01\x02\x03junk")
    resp = client.get("/api/map/layers")
    assert resp.status_code == 200
    assert [layer["name"] for layer in resp.get_json()["layers"]] == ["borders", "test_blocks"]


def test_a_layer_whose_name_could_never_load_is_not_listed(client, map_dir):
    """Spaces are everywhere in GIS deliverables, and _safe_path rejects them.

    Listing such a set would offer a checkbox that can only ever answer 400,
    so it is filtered out of the list instead.
    """
    writer = shapefile.Writer(str(map_dir / "layers" / "Seismic Blocks 2024"),
                              shapeType=shapefile.POLYGON)
    writer.field("name", "C", size=40)
    writer.poly([OUTER_RING])
    writer.record("Block Q")
    writer.close()

    layers = client.get("/api/map/layers").get_json()["layers"]
    assert [layer["name"] for layer in layers] == ["borders", "test_blocks"]


def test_a_corrupt_borders_file_drops_the_backdrop_instead_of_failing_the_list(client, map_dir):
    (map_dir / "borders_utm37.json").write_text("{not json", encoding="utf-8")
    resp = client.get("/api/map/layers")
    assert resp.status_code == 200
    assert [layer["name"] for layer in resp.get_json()["layers"]] == ["test_blocks"]


def test_layers_with_no_map_directory_at_all_is_empty_not_an_error(client, tmp_path, monkeypatch):
    monkeypatch.setenv("SEGMENT_TRACKER_MAP_DATA_DIR", str(tmp_path / "absent"))
    resp = client.get("/api/map/layers")
    assert resp.status_code == 200
    assert resp.get_json() == {"layers": []}


# ---------------------------------------------------------------------------
# GET /api/map/layers/<name>
# ---------------------------------------------------------------------------

def test_layer_returns_features_with_geometry_and_properties(client, map_dir):
    resp = client.get("/api/map/layers/test_blocks")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["name"] == "test_blocks"
    assert body["geom_type"] == "polygon"
    assert body["bbox"] == [400000.0, 2800000.0, 500000.0, 2900000.0]
    assert len(body["features"]) == 1

    feature = body["features"][0]
    assert set(feature) == {"geometry", "properties"}
    # DBF attributes ride along for the hover card.
    assert feature["properties"]["name"] == "Block Z"
    assert feature["properties"]["area_km2"] == 9600.0


def test_polygon_parts_split_into_separate_rings(client, map_dir):
    """A 2-ring polygon comes back as 2 rings, each closed, in file order."""
    feature = client.get("/api/map/layers/test_blocks").get_json()["features"][0]
    geometry = feature["geometry"]
    assert geometry["type"] == "polygon"
    rings = geometry["coordinates"]
    assert len(rings) == 2
    assert rings[0] == OUTER_RING
    assert rings[1] == HOLE_RING
    assert all(ring[0] == ring[-1] for ring in rings)


@pytest.mark.parametrize("name", [
    "../x",                         # a traversal attempt
    "..%2Fx",                       # the same, percent-encoded
    "a/b",                          # a separator inside the name
    "..%2F..%2Fetc%2Fpasswd",       # a traversal reaching for a real file
    "layer%20name",                 # a space -- outside the allowlist
    "layer%00.shp",                 # a NUL byte
])
def test_invalid_layer_names_are_rejected(client, map_dir, name):
    """Traversal and separator attempts never reach the filesystem -> 400.

    The route takes a ``path`` converter precisely so these reach the domain
    guard and are REJECTED by name, rather than falling through to a routing
    404 that would say nothing about whether the name was safe.
    """
    resp = client.get("/api/map/layers/{}".format(name))
    assert resp.status_code == 400
    assert "Invalid layer name" in resp.get_json()["detail"]


def test_a_layer_set_missing_its_dbf_is_a_400_about_the_layer(client, map_dir):
    """The .shp opens fine without its .dbf -- reading the FIELDS is what fails.

    That is a broken layer, not a broken server, so it must surface as a 400
    naming the layer rather than as an unhandled ShapefileException 500.
    """
    (map_dir / "layers" / "test_blocks.dbf").unlink()
    resp = client.get("/api/map/layers/test_blocks")
    assert resp.status_code == 400
    assert "test_blocks cannot be read" in resp.get_json()["detail"]


def test_unknown_layer_is_404(client, map_dir):
    resp = client.get("/api/map/layers/no_such_layer")
    assert resp.status_code == 404
    assert resp.get_json()["detail"] == "No such layer: no_such_layer"


def test_borders_layer_is_served_from_the_prebuilt_file(client, map_dir):
    resp = client.get("/api/map/layers/borders")
    assert resp.status_code == 200
    assert resp.mimetype == "application/json"
    # Passed through verbatim -- byte-for-byte the file's own content.
    assert resp.get_json() == BORDERS_JSON


def test_missing_borders_file_returns_an_empty_feature_collection(client, map_dir):
    (map_dir / "borders_utm37.json").unlink()
    resp = client.get("/api/map/layers/borders")
    assert resp.status_code == 200
    assert resp.get_json() == {"name": "borders", "features": [], "bbox": None}


# ---------------------------------------------------------------------------
# GET /api/map/wells
# ---------------------------------------------------------------------------

# The overlay row, pinned by name. Widening it is a deliberate
# workflow.mapdata.map_wells change, never an accidental raw-row leak (the
# board row it is composed from carries dates, folder paths and revisions the
# map has no business publishing).
WELL_ROW_KEYS = [
    "coord_source", "display_stage", "field", "gas_field", "mean_gas_bcf",
    "overall_status", "p10_area_km2", "p90_area_km2", "pipeline_type",
    "project_id", "project_name", "record_status", "total_cos", "x", "y",
    "year",
]


def _wells(client):
    resp = client.get("/api/map/wells")
    assert resp.status_code == 200
    return resp.get_json()["wells"]


def _well_for(client, project_id):
    return next(well for well in _wells(client) if well["project_id"] == project_id)


def _save_fields(client, project_id, task_name, fields):
    task = get_task_by_name(client, project_id, task_name)
    resp = client.patch(f"/api/tasks/{task['task_id']}/dynamic-fields",
                        json={"fields": fields})
    assert resp.status_code == 200, resp.get_json()


def _save_staked(client, project_id, fields):
    """Write staked coordinates the way a bulk writer does (reconcile=False).

    Straight to the domain function: the map only cares that the values are in
    task_dynamic_fields, and reconciling would additionally drive the step's
    status, which is a different test's subject.
    """
    import db
    import workflow

    task = get_task_by_name(client, project_id, "Well Site Location")
    session = db.new_session()
    try:
        workflow.save_task_dynamic_fields(session, task["task_id"], fields,
                                          changed_by="Bulk Writer", reconcile=False)
    finally:
        session.close()


def test_wells_row_shape_and_lead_coordinates(client):
    pid = create_project(client, "MAPPY-1", lead_x="512000.5", lead_y="2903000.25")
    wells = _wells(client)
    assert len(wells) == 1

    well = wells[0]
    assert sorted(well.keys()) == WELL_ROW_KEYS
    assert well["project_id"] == pid
    assert well["project_name"] == "MAPPY-1"
    assert well["pipeline_type"] == "prospect"
    assert well["overall_status"] == "In Progress"
    assert well["display_stage"] == "Lead Assessment"
    # Derived by the same rules the board uses -- no stored field column, and
    # nothing recorded yet means a null mean gas (not a zero).
    assert well["field"] == "MAPPY"
    assert well["gas_field"] == "MAPPY"
    assert well["mean_gas_bcf"] is None
    # An immature, non-BP lead is a left-join participant: it stays visible as
    # Proposed with a null year and blank report measures.
    assert well["year"] is None
    assert well["record_status"] == "Proposed"
    assert well["total_cos"] == ""
    assert well["p90_area_km2"] == ""
    assert well["p10_area_km2"] == ""
    # Coordinates pass through as floats, unprojected.
    assert well["coord_source"] == "lead"
    assert well["x"] == 512000.5
    assert well["y"] == 2903000.25


def test_staked_coordinates_supersede_the_lead_pair(client):
    pid = create_project(client, "MAPPY-2", lead_x="512000.5", lead_y="2903000.25")
    _save_staked(client, pid, {"staked_x": "515500.75", "staked_y": "2899000.5"})

    well = _wells(client)[0]
    assert well["project_id"] == pid
    assert well["coord_source"] == "staked"
    assert (well["x"], well["y"]) == (515500.75, 2899000.5)


def test_half_a_staked_pair_falls_back_to_the_lead_coordinates(client):
    pid = create_project(client, "MAPPY-3", lead_x="512000.5", lead_y="2903000.25")
    _save_staked(client, pid, {"staked_x": "515500.75", "staked_y": ""})

    well = _wells(client)[0]
    assert well["project_id"] == pid
    assert well["coord_source"] == "lead"
    assert (well["x"], well["y"]) == (512000.5, 2903000.25)


def test_a_record_with_no_usable_coordinates_is_omitted(client):
    create_project(client, "NOWHERE-1")
    mapped = create_project(client, "MAPPY-4", lead_x="400000", lead_y="2800000")
    assert [well["project_id"] for well in _wells(client)] == [mapped]


def test_staked_only_records_appear_without_any_lead_pair(client):
    pid = create_project(client, "MAPPY-5")
    _save_staked(client, pid, {"staked_x": "601000", "staked_y": "2705000"})

    well = _wells(client)[0]
    assert well["coord_source"] == "staked"
    assert (well["x"], well["y"]) == (601000.0, 2705000.0)


def test_map_reporting_attributes_match_portfolio_semantics(client):
    """BP year, fluid/stake status, CoS percent and area bounds ride together."""
    pid = create_project(client, "FILTERS-1", lead_x="512000", lead_y="2903000",
                         pipeline_type="bp", business_plan_enabled=True,
                         business_plan_year=2031)
    _save_fields(client, pid, "Reservoir CoS", {"reservoir_cos_rows": json.dumps([
        {"reservoir_cos_pct": "50"},
    ])})
    _save_fields(client, pid, "Trap and Seal CoS",
                 {"trap_cos_pct": "80", "seal_cos_pct": "50"})
    _save_fields(client, pid, "Area Definition",
                 {"p90_area_km2": " 2.4 ", "p10_area_km2": "9.8"})
    _save_fields(client, pid, "Resource Assessment", {"lead_piip_gas_mean": "12.5"})

    stake = get_task_by_name(client, pid, "Approval to Stake")
    resp = client.patch(f"/api/tasks/{stake['task_id']}", json={
        "status": "Approved", "revision": stake["revision"],
    })
    assert resp.status_code == 200, resp.get_json()

    well = _well_for(client, pid)
    assert well["year"] == 2031
    assert well["record_status"] == "Staked"
    # Total CoS is a WHOLE percentage, not the 0.2 fraction used by the plot.
    assert well["total_cos"] == "20"
    assert well["mean_gas_bcf"] == 12.5
    # Area bounds remain strings; whitespace is normalized by the reporting
    # read ladder and numeric validation/aggregation stays a client concern.
    assert well["p90_area_km2"] == "2.4"
    assert well["p10_area_km2"] == "9.8"

    resp = client.put(f"/api/projects/{pid}/formations", json={
        "phase": "final", "rows": [{"formation": "SARH", "fluid": "Gas"}],
    })
    assert resp.status_code == 200, resp.get_json()
    assert _well_for(client, pid)["record_status"] == "Gas"


def test_map_area_fold_keeps_retired_fallback_but_active_nonblank_wins(client, app_modules):
    """The reporting fold's retired-row precedence applies to both area keys."""
    _main, db = app_modules
    pid = create_project(client, "AREA-LEGACY-1", lead_x="500000", lead_y="2800000")
    active = get_task_by_name(client, pid, "Area Definition")
    # Active P90 should win; active blank P10 must not erase the legacy value.
    _save_fields(client, pid, "Area Definition",
                 {"p90_area_km2": "2.4", "p10_area_km2": ""})

    session = db.new_session()
    try:
        with db.write_transaction(session):
            result = db.execute(session, """
                INSERT INTO project_tasks
                    (project_id, sequence_no, task_name, stage_group, status,
                     priority, is_active, revision)
                VALUES (:project_id, 999, 'Legacy Area Definition',
                        'Lead Assessment', 'Approved', 'Medium', 0, 0)
            """, {"project_id": pid})
            legacy_task_id = result.lastrowid
            db.execute_many(session, """
                INSERT INTO task_dynamic_fields
                    (task_id, field_key, field_value, updated_at)
                VALUES (:task_id, :field_key, :field_value, '2020-01-01 00:00:00')
            """, [
                {"task_id": legacy_task_id, "field_key": "p90_area_km2", "field_value": "1.1"},
                {"task_id": legacy_task_id, "field_key": "p10_area_km2", "field_value": "8.8"},
            ])
    finally:
        session.close()

    well = _well_for(client, pid)
    assert active["task_id"] < legacy_task_id  # precedence is activity, not id
    assert well["p90_area_km2"] == "2.4"
    assert well["p10_area_km2"] == "8.8"


def test_completed_positioned_lead_remains_on_the_map(client):
    pid = create_project(client, "MAP-DONE-1", lead_x="500000", lead_y="2800000")
    for task in get_tasks(client, pid):
        if task["stage_group"] in {"Lead Assessment", "Risk Analysis", "Pre-Well Delivery"}:
            resp = client.patch(f"/api/tasks/{task['task_id']}", json={
                "status": "Approved", "revision": task["revision"],
            })
            assert resp.status_code == 200, resp.get_json()

    well = _well_for(client, pid)
    assert well["overall_status"] == "Completed"
    assert well["record_status"] in {"Proposed", "Staked"}


def test_map_reporting_reads_are_batched_for_the_whole_overlay(client, app_modules):
    """Adding pins cannot add a task/formation reporting query per project."""
    from sqlalchemy import event

    _main, db = app_modules
    for index in range(4):
        create_project(client, f"MAP-BATCH-{index}",
                       lead_x=str(500000 + index), lead_y=str(2800000 + index))

    statements = []

    def _record(_conn, _cursor, statement, *_args):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    engine = db.get_engine()
    event.listen(engine, "before_cursor_execute", _record)
    try:
        assert len(_wells(client)) == 4
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    # Mean OGIP, staked coordinates and reporting fields each have one EAV
    # query for all four ids; SARH fluid and staking status each have one too.
    field_reads = [sql for sql in statements
                   if "JOIN task_dynamic_fields tdf" in sql and "tdf.field_key IN" in sql]
    formation_reads = [sql for sql in statements if "FROM project_formations" in sql]
    stake_reads = [sql for sql in statements
                   if "MAX(CASE WHEN pt.status = 'Approved'" in sql]
    assert len(field_reads) == 3, field_reads
    assert len(formation_reads) == 1, formation_reads
    assert len(stake_reads) == 1, stake_reads
