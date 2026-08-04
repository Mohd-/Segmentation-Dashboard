"""Auto-fill from grid surfaces: values a save can derive from WHERE a lead is.

Two fills share one mechanism -- resolve the project's coordinates, sample a
configured ZMAP+ surface (surfaces.py) there, store the result:

- :func:`fill_tsq` samples the SARH-QWRH thickness ("TSQ") surface and fills
  the Trap and Seal CoS step's ``sarah_quwarah_thickness_ft`` field, but ONLY
  while that field is empty -- a manual entry always wins and is never
  overwritten. The write goes through the same task_dynamic_fields upsert every
  field save uses, with a task_history note beside it, so the value is
  indistinguishable in storage from a typed one (and auditable as auto-filled).
- :func:`fill_ground_elevation` samples the digital-elevation surface into
  ``projects.ground_elevation``. That column is MACHINE-DERIVED, so overwriting
  is always correct; it is only left untouched when there is nothing to say
  (no coordinates, no surface, or no value at the point).

Coordinate precedence mirrors workflow/mapdata.py exactly: the STAKED pair
(``staked_x``/``staked_y`` in task_dynamic_fields, both present and numeric)
supersedes the planned ``projects.lead_x``/``lead_y`` pair, and half a pair is
not a location. The staked fold is retired-inclusive with active rows winning,
reproduced from mapdata._staked_coordinates for the single-project case.

Both fills are cheap no-ops when their surface file is not configured/present,
and a corrupt surface can never raise out of them (surfaces.sample_surface's
contract) -- safe to call after ANY save.

Session semantics: like history.log_task_event and lifecycle's helpers, nothing
here commits or opens a transaction -- the CALLER owns the transaction (the
save path that triggered the fill, or scripts/backfill_surfaces.py's
write_transaction blocks).

Import direction: lifecycle.py will call INTO this module (the save-time
wiring); this module must therefore never import lifecycle.
"""
from __future__ import annotations

import logging
from typing import Optional, Tuple

import config
import db
import surfaces
from helpers import utc_now_str

from .history import log_task_event
from .mapdata import _coordinate_pair

logger = logging.getLogger(__name__)

# The step and field the TSQ surface feeds. Field-keyed writes would survive a
# step rename only if the row does too, so the ACTIVE row is looked up by the
# current step name (the same name lifecycle's recompute hooks key on).
TSQ_TASK_NAME = "Trap and Seal CoS"
TSQ_FIELD_KEY = "sarah_quwarah_thickness_ft"

# The audit-trail actor for auto-filled values (the migration-actor idiom).
SURFACE_FILL_ACTOR = "System (surface auto-fill)"

# The staked location keys, read by KEY across active AND retired rows -- the
# same reason mapdata reads them that way: a value answers to its key whichever
# step row carries it, so a step rename/merge cannot strand the coordinates.
_STAKED_COORD_KEYS = ["staked_x", "staked_y"]


def _resolve_coordinates(session, project_id) -> Optional[Tuple[float, float]]:
    """The (x, y) a fill should sample at, or None when the project has none.

    mapdata.map_wells' precedence, for one project: the staked pair wins when
    BOTH halves parse as numbers, else the lead pair, else None. The staked
    fold is retired-inclusive with ``ORDER BY pt.is_active`` putting inactive
    rows first, so an active row's non-blank value folds in last and wins while
    a legacy value on a retired row still resolves (first-non-blank-wins,
    mapdata._staked_coordinates' rule).
    """
    rows = db.fetch_all(session, """
        SELECT tdf.field_key, tdf.field_value
        FROM project_tasks pt
        JOIN task_dynamic_fields tdf ON tdf.task_id = pt.task_id
        WHERE pt.project_id = :project_id
          AND tdf.field_key IN :field_keys
        ORDER BY pt.is_active, pt.task_id
    """, {"project_id": project_id, "field_keys": _STAKED_COORD_KEYS})
    staked = {}
    for row in rows:
        value = row["field_value"] or ""
        if value or row["field_key"] not in staked:
            staked[row["field_key"]] = value
    position = _coordinate_pair(staked.get("staked_x"), staked.get("staked_y"))
    if position is not None:
        return position
    project = db.fetch_one(session,
                           "SELECT lead_x, lead_y FROM projects WHERE project_id = :project_id",
                           {"project_id": project_id})
    if not project:
        return None
    return _coordinate_pair(project.get("lead_x"), project.get("lead_y"))


def _format_field_value(value) -> str:
    """A sampled float as the string a user might have typed: two decimals,
    trailing zeros (and a bare point) trimmed -- '30', not '30.00'."""
    text_value = "{:.2f}".format(float(value))
    if "." in text_value:
        text_value = text_value.rstrip("0").rstrip(".")
    return text_value or "0"


def fill_tsq(session, project_id) -> Optional[float]:
    """Auto-fill the Trap and Seal CoS SARH-QWRH thickness from the TSQ surface.

    Returns the sampled value WHEN IT WAS WRITTEN, else None -- and it is only
    written when every gate passes: the surface file exists, the project
    resolves coordinates, the active step row exists, its
    ``sarah_quwarah_thickness_ft`` is empty/absent (a manual entry always
    wins), and the surface actually has a value at the point. No commit; the
    caller owns the transaction.
    """
    surface_path = config.tsq_surface_file()
    if not surface_path.is_file():
        return None                     # unconfigured: no-op before any query
    task = db.fetch_one(session, """
        SELECT task_id FROM project_tasks
        WHERE project_id = :project_id AND task_name = :task_name AND is_active = 1
        ORDER BY task_id DESC
        LIMIT 1
    """, {"project_id": project_id, "task_name": TSQ_TASK_NAME})
    if not task:
        return None                     # e.g. a BP-era record without the step
    existing = db.fetch_one(session, """
        SELECT field_value FROM task_dynamic_fields
        WHERE task_id = :task_id AND field_key = :field_key
    """, {"task_id": task["task_id"], "field_key": TSQ_FIELD_KEY})
    if existing is not None and str(existing["field_value"] or "").strip():
        return None                     # manual (or earlier auto) entry wins
    position = _resolve_coordinates(session, project_id)
    if position is None:
        return None
    value = surfaces.sample_surface(surface_path, position[0], position[1])
    if value is None:
        return None

    stored = _format_field_value(value)
    now = utc_now_str()
    # The exact upsert every field save uses (lifecycle._apply_dynamic_fields),
    # so the stored shape is identical to a typed value's.
    db.execute(session, """
        INSERT INTO task_dynamic_fields (task_id, field_key, field_value, updated_at)
        VALUES (:task_id, :field_key, :field_value, :now)
        ON CONFLICT(task_id, field_key) DO UPDATE
        SET field_value = excluded.field_value, updated_at = excluded.updated_at
    """, {"task_id": task["task_id"], "field_key": TSQ_FIELD_KEY,
          "field_value": stored, "now": now})
    db.execute(session, "UPDATE project_tasks SET last_updated = :now WHERE task_id = :task_id",
               {"now": now, "task_id": task["task_id"]})
    log_task_event(session, task["task_id"], project_id, TSQ_TASK_NAME,
                   "Component Inputs Updated", None, None, SURFACE_FILL_ACTOR,
                   "Auto-filled from TSQ surface: sarah quwarah thickness ft = {}.".format(stored))
    return value


def fill_ground_elevation(session, project_id) -> Optional[float]:
    """Sample the DEM at the project's coordinates into projects.ground_elevation.

    Overwriting is fine -- the column is machine-derived, so the surface is
    always right about itself. Returns the value written, or None (leaving any
    existing stored value untouched) when the project has no coordinates, the
    surface is not configured/present, or it has no value at the point. No
    commit; the caller owns the transaction.
    """
    surface_path = config.ground_elevation_surface_file()
    if not surface_path.is_file():
        return None                     # unconfigured: no-op before any query
    position = _resolve_coordinates(session, project_id)
    if position is None:
        return None
    value = surfaces.sample_surface(surface_path, position[0], position[1])
    if value is None:
        return None
    db.execute(session, """
        UPDATE projects SET ground_elevation = :ground_elevation
        WHERE project_id = :project_id
    """, {"ground_elevation": float(value), "project_id": project_id})
    return value
