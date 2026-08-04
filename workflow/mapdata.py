"""The wells overlay: where each lead / well sits on the UTM37N map.

One read, one shape. ``map_wells`` is the ONLY domain function behind
GET /api/map/wells, and it composes the overlay from state that already
exists rather than from anything stored for the map:

- the board row itself (name, pipeline, derived status/stage, field and the
  latest Mean Gas) comes from :func:`workflow.projects.get_projects`, so a
  pin's label can never disagree with the same record's board card -- the
  mean-gas precedence ladder in particular is NOT reimplemented here;
- its filter/summary attributes (BP year, reporting status, Total CoS and
  area bounds) use reporting's batched, retired-inclusive field semantics.
  This is a left join over every positioned project, not Portfolio membership,
  so an immature lead stays on the map as Proposed with blank measures;
- the position is the STAKED coordinate when the well has one, else the lead's
  planned coordinate. Staked X/Y are user inputs on the Pre-Well Delivery
  "Well Site Location" step (task_dynamic_fields), so they arrive through one
  batched EAV read for the whole board -- never a query per project.

Coordinates pass through as floats in UTM37N metres, unprojected, exactly like
the shapefile layers (map_layers.py): the map is one flat plane and every
source is already in it.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import db

from .projects import get_projects

# The staked location, captured on "Well Site Location" (card 4B). Read by KEY
# rather than by step name -- the same reason reporting._bp_task_fields does:
# a value answers to its key whichever step row carries it, so a rename or a
# merge of the step cannot strand a well's coordinates.
_STAKED_COORD_KEYS = ["staked_x", "staked_y"]


def _number_or_none(value) -> Optional[float]:
    """float(value) or None -- never raises. NaN/inf are rejected too.

    A non-finite coordinate would place a pin nowhere and poison any bbox the
    client computes from the overlay, so it counts as absent.
    """
    try:
        number = float(str(value if value is not None else "").strip())
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _coordinate_pair(x_value, y_value) -> Optional[Tuple[float, float]]:
    """(x, y) as floats, or None unless BOTH parse as numbers.

    Half a pair is not a location: a well with an easting but no northing is
    left off the map rather than pinned on the zero line.
    """
    x = _number_or_none(x_value)
    y = _number_or_none(y_value)
    if x is None or y is None:
        return None
    return x, y


def _staked_coordinates(session, project_ids) -> Dict[int, Dict[str, str]]:
    """Batched {project_id: {staked_x/staked_y: value}} for the whole board.

    ONE query for every project, like reporting._bp_task_fields. RETIRED-
    INCLUSIVE (no ``is_active`` filter) with ``ORDER BY pt.is_active`` putting
    inactive rows FIRST, so a surviving step's value folds in last and wins
    while a legacy well's coordinates on a retired row still resolve. The fold
    is first-NON-BLANK-wins -- reporting.fold_task_field_rows' rule, reproduced
    here (as workflow.projects._annotate_mean_gas reproduces it) because the
    workflow package deliberately does not import reporting.
    """
    if not project_ids:
        return {}
    rows = db.fetch_all(session, """
        SELECT pt.project_id, tdf.field_key, tdf.field_value
        FROM project_tasks pt
        JOIN task_dynamic_fields tdf ON tdf.task_id = pt.task_id
        WHERE pt.project_id IN :project_ids
          AND tdf.field_key IN :field_keys
        ORDER BY pt.is_active, pt.task_id
    """, {"project_ids": list(project_ids), "field_keys": _STAKED_COORD_KEYS})
    folded: Dict[int, Dict[str, str]] = {}
    for row in rows:
        bucket = folded.setdefault(row["project_id"], {})
        value = row["field_value"] or ""
        if value or row["field_key"] not in bucket:
            bucket[row["field_key"]] = value
    return folded


def map_wells(session) -> List[Dict[str, Any]]:
    """Every lead / well that HAS a position, as map-overlay rows.

    ``coord_source`` says which coordinate the pin is drawn from: "staked" when
    the well's surveyed location is recorded (it supersedes the plan), "lead"
    for the planned lead coordinate the record was created with. A record with
    neither complete pair is OMITTED -- there is no sensible place to draw it,
    and a pin at a guessed location is worse than no pin.

    Completed records are INCLUDED (include_completed=True): a drilled well is
    exactly the thing a map of the acreage must show.
    """
    # Local import keeps the workflow package's import graph acyclic:
    # reporting imports workflow at module load, while map_wells is only called
    # after application/module initialisation is complete.
    import reporting

    projects = get_projects(session, include_completed=True)
    staked = _staked_coordinates(session, [p["project_id"] for p in projects])

    positioned = []
    for project in projects:
        fields = staked.get(project["project_id"]) or {}
        position = _coordinate_pair(fields.get("staked_x"), fields.get("staked_y"))
        source = "staked"
        if position is None:
            position = _coordinate_pair(project.get("lead_x"), project.get("lead_y"))
            source = "lead"
        if position is None:
            continue
        positioned.append((project, position, source))

    # Three fixed-size reporting reads for the complete plotted set (task EAV,
    # stake state and SARH fluid formations), never one lookup per pin.
    attributes = reporting.map_attributes_by_project(
        session, [project["project_id"] for project, _position, _source in positioned])

    wells = []
    for project, position, source in positioned:
        attrs = attributes[project["project_id"]]
        gas_field = project.get("field")
        wells.append({
            "project_id": project["project_id"],
            "project_name": project.get("project_name"),
            "pipeline_type": project.get("pipeline_type"),
            # Derived board state, straight from get_projects -- one source of
            # truth for status/stage/field/mean gas across every surface.
            "overall_status": project.get("overall_status"),
            "display_stage": project.get("display_stage"),
            # ``field`` is retained for the existing tooltip. ``gas_field`` is
            # the canonical filter key shared with Portfolio, and both values
            # originate in folders.parse_field_and_well via get_projects.
            "field": gas_field,
            "gas_field": gas_field,
            "year": project.get("business_plan_year"),
            "record_status": attrs["record_status"],
            # Whole-percent string / blank exactly like Portfolio. Quadrant
            # cutoffs and area midpoint aggregation are client concerns.
            "total_cos": attrs["total_cos"],
            "p90_area_km2": attrs["p90_area_km2"],
            "p10_area_km2": attrs["p10_area_km2"],
            "mean_gas_bcf": project.get("mean_gas_bcf"),
            "x": position[0],
            "y": position[1],
            "coord_source": source,
        })
    return wells
