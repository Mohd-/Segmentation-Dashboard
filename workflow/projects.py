"""Project CRUD and the derived board state.

Creation materializes one ``project_tasks`` row per PIPELINE_TEMPLATES step;
the board pointers (current stage/task/owner/status) are DERIVED from the
task rows at read time -- never stored (see _annotate_derived_state).
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List

from sqlalchemy.exc import IntegrityError

import config
import db
import folders
from helpers import health_from_target, parse_iso_date, today_str, utc_now_str

from .constants import (BP_EXECUTION_STAGES, LATEST_MEAN_GAS_SOURCES, PIPELINE_TEMPLATES,
                        STAGE_ORDER, applicable_stages)
from .history import log_task_event

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Project creation
# ---------------------------------------------------------------------------

def _duplicate_name_message(pipeline_type):
    """The user-facing duplicate-name error.

    Card 1D pins the wording the Add New Lead control shows verbatim; the BP
    side still creates wells, so it keeps the older combined phrasing.
    """
    return ("A lead with this name already exists." if pipeline_type == "prospect"
            else "A lead / well with this name already exists.")


def _validated_coordinate(value, label):
    """Return ``value`` unchanged when it is a usable coordinate, else raise.

    Coordinates stay OPTIONAL at the API level (the Excel importer and older
    callers create records without them), so only a supplied, non-blank value is
    checked. What is rejected is a value that is not a finite number -- letters,
    malformed decimals, inf/nan -- which would otherwise be stored as text and
    resurface as a broken well location in Staking. No sign or range rule: real
    coordinates are signed. The ORIGINAL string is stored, never a reformatted
    float, so the entered precision survives.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        raise ValueError(f"Enter a valid Lead {label} Coordinate.")
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(f"Enter a valid Lead {label} Coordinate.")
    return value


def add_project(session, project_name, start_date=None, target_date=None, changed_by="System", lead_x=None, lead_y=None,
                business_plan_year=None, business_plan_enabled=False, active_well_enabled=False, pipeline_type="prospect"):
    """Create a project and materialize its 27 workflow tasks; return project_id."""
    project_name = (project_name or '').strip()
    if not project_name:
        raise ValueError("Lead / well name is required.")
    if len(project_name) > 120:
        raise ValueError("Lead / well name must be 120 characters or less.")
    pipeline_type = str(pipeline_type or "prospect").strip().lower()
    if pipeline_type not in {"prospect", "bp"}:
        pipeline_type = "prospect"
    lead_x = _validated_coordinate(lead_x, "X")
    lead_y = _validated_coordinate(lead_y, "Y")
    now = utc_now_str()
    start_date = start_date or today_str()
    target_date = target_date or ""
    if business_plan_year:
        try:
            year_val = int(business_plan_year)
        except (TypeError, ValueError):
            raise ValueError("Select a business plan year from 1990 to 2040.")
    else:
        year_val = None
    bp_enabled = 1 if business_plan_enabled or year_val else 0
    # Floor is 1990, not 2026: the Excel importer creates historical BP wells
    # drilled in the past. The promote dialog UI still only offers 2026+.
    if bp_enabled and (year_val is None or year_val < 1990 or year_val > 2040):
        raise ValueError("Select a business plan year from 1990 to 2040.")

    # Friendly duplicate check up front; the IntegrityError catch below still
    # covers the race where another request inserts the same name in between.
    #
    # CASE-INSENSITIVE and whitespace-insensitive (Card 1D): 'WWWW-44',
    # 'wwww-44' and ' WWWW-44 ' are the same lead to a human, and the derived
    # field/folder split (folders.parse_field_and_well) would collide anyway.
    # project_name is already stripped above; trim() on the stored side catches
    # legacy rows that were written with padding. The DB's UNIQUE(project_name)
    # index stays case-SENSITIVE, so this check -- not the constraint -- is what
    # enforces the rule; the IntegrityError catch below remains the race net.
    duplicate = db.fetch_one(session,
                             "SELECT 1 AS present FROM projects "
                             "WHERE lower(trim(project_name)) = lower(trim(:project_name))",
                             {"project_name": project_name})
    if duplicate:
        raise ValueError(_duplicate_name_message(pipeline_type))

    # The workflow definition lives in code (PIPELINE_TEMPLATES); the creation
    # history event anchors on the pipeline's first step.
    first_template = (next((t for t in PIPELINE_TEMPLATES if t[2] in BP_EXECUTION_STAGES), PIPELINE_TEMPLATES[0])
                      if pipeline_type == "bp" else PIPELINE_TEMPLATES[0])

    try:
        return _insert_project_with_tasks(session, project_name, start_date, target_date, changed_by,
                                          lead_x, lead_y, year_val, bp_enabled, active_well_enabled,
                                          pipeline_type, first_template, now)
    except IntegrityError as exc:
        # UNIQUE(project_name) race lost to a concurrent insert.
        if "unique" in str(getattr(exc, "orig", None) or exc).lower():
            raise ValueError(_duplicate_name_message(pipeline_type)) from exc
        raise


def _insert_project_with_tasks(session, project_name, start_date, target_date, changed_by, lead_x, lead_y,
                               year_val, bp_enabled, active_well_enabled, pipeline_type,
                               first_template, now):
    """Insert the project row plus one task per PIPELINE_TEMPLATES step in one locked transaction."""
    first_sequence, first_task_name, _first_stage = first_template
    with db.write_transaction(session):
        result = db.execute(session, """
            INSERT INTO projects (
                project_name, start_date, target_date, last_updated,
                lead_folder_path, lead_x, lead_y, business_plan_enabled, business_plan_year,
                active_well_enabled, pipeline_type
            ) VALUES (:project_name, :start_date, :target_date, :last_updated,
                      :lead_folder_path, :lead_x, :lead_y, :business_plan_enabled, :business_plan_year,
                      :active_well_enabled, :pipeline_type)
        """, {
            "project_name": project_name, "start_date": start_date,
            "target_date": target_date, "last_updated": now,
            "lead_folder_path": folders.default_lead_folder_path(project_name),
            "lead_x": lead_x or None, "lead_y": lead_y or None,
            "business_plan_enabled": bp_enabled, "business_plan_year": year_val,
            "active_well_enabled": 1 if active_well_enabled else 0, "pipeline_type": pipeline_type,
        })
        project_id = result.lastrowid  # PG: use RETURNING when on Postgres
        first_task_id = None
        for sequence_no, task_name, stage_group in PIPELINE_TEMPLATES:
            # Every step starts Not Assigned regardless of pipeline_type;
            # assignment moves it to In Progress. Applicability is derived per
            # pipeline at query time (applicable_stages), never stored per row,
            # so all 27 rows are materialized identically.
            initial_status = "Not Assigned"
            task_result = db.execute(session, """
                INSERT INTO project_tasks (
                    project_id, sequence_no, task_name, stage_group, assigned_to,
                    status, actual_start, actual_finish, comments, priority, business_plan_enabled,
                    business_plan_year, is_active, last_updated
                ) VALUES (:project_id, :sequence_no, :task_name, :stage_group, :assigned_to,
                          :status, :actual_start, :actual_finish, :comments, :priority, :business_plan_enabled,
                          :business_plan_year, 1, :last_updated)
            """, {
                "project_id": project_id,
                "sequence_no": sequence_no, "task_name": task_name,
                "stage_group": stage_group, "assigned_to": None,
                "status": initial_status,
                "actual_start": None, "actual_finish": None,
                # Card 1D: a brand-new record starts at the LOWEST priority, so
                # its board card renders gray until somebody deliberately
                # raises a step (PATCH /api/tasks/<id>/priority, supervisor
                # only). _lead_priority derives the card color from these rows,
                # so 'Low' here is what makes a fresh lead read as un-escalated
                # instead of borrowing Medium's blue.
                "comments": None, "priority": "Low",
                "business_plan_enabled": bp_enabled, "business_plan_year": year_val,
                "last_updated": now,
            })
            if sequence_no == first_sequence:
                first_task_id = task_result.lastrowid  # PG: use RETURNING when on Postgres

        if first_task_id is not None:
            action = "Well Added to BP" if pipeline_type == "bp" else "Lead Created"
            comment = f"{'Well added to Business Plan Execution' if pipeline_type == 'bp' else 'Lead created'}: {project_name}"
            log_task_event(
                session,
                task_id=first_task_id,
                project_id=project_id,
                task_name=first_task_name,
                action_type=action,
                old_status=None,
                new_status="Created",
                changed_by=changed_by,
                comment=comment,
            )
    return project_id


# ---------------------------------------------------------------------------
# Project reads (board pointers are DERIVED from project_tasks, never stored)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# The board card's derived fields (Card 1B)
# ---------------------------------------------------------------------------
# A READ-TIME projection over the stored workflow. It stores nothing, changes no
# status and adds no query: every value is derived from the task rows
# _annotate_derived_state has already batch-loaded.
#
# Since v5 the stored prospect pipeline IS what the board shows -- twelve steps
# across the three stage groups the columns are named after -- so there is no
# mapping table any more, only the two cosmetic rules below. (Before v5 this
# block was a presentation ADAPTER that folded four stage groups into three and
# invented two items with no stored step; migrations._migrate_v5_prospect_
# template_restructure made all twelve real and the adapter was deleted whole.)

# Board labels that are SHORTER than the step they stand for, because a card dot
# gets a few characters. Display-only: the stored step name is untouched and is
# what ``steps`` carries, so a client always opens the real component.
_TRACKED_ITEM_LABELS = {
    "Reservoir CoS": "Reservoir",
    "Trap and Seal CoS": "Trap and Seal",
    "Seismic Signature Validation": "Seismic Validation",
    "Pre-Drilling GeoX Assessment": "GeoX Assessment",
}

# The ONE item whose supervisor queue the board surfaces: a submitted-but-
# unapproved Segmentation Slides reads "Pending Approval". Everywhere else a
# Ready step reads "In Progress", because a returned submission drops back to
# In Progress and the card must not distinguish the two.
_READY_SHOWS_PENDING = frozenset({"Segmentation Slides"})

# The twelve tracked items ARE the prospect steps, in sequence order.
_TRACKED_ITEM_STEPS = tuple(
    (stage_group, task_name, _TRACKED_ITEM_LABELS.get(task_name, task_name))
    for _sequence_no, task_name, stage_group in PIPELINE_TEMPLATES
    if stage_group not in BP_EXECUTION_STAGES
)

# Card border / column sort vocabulary. "Normal" (the models.py server default
# for legacy rows) and anything else unrecognized is treated as ABSENT.
_PRIORITY_RANK = {"High": 0, "Medium": 1, "Low": 2}


def _tracked_items(status_by_task):
    """The 12-item board model for one prospect (pure), 1:1 with its steps.

    ``status_by_task`` maps a stored step name to its stored lifecycle status.
    A step the project does not carry is simply not Approved, so it reads
    "In Progress" -- the same thing a brand-new lead's Not Assigned rows read.
    "Not Assigned" is not a display status: the card shows work as done, waiting,
    or ongoing, nothing else.

    ``steps`` carries the item's stored step name as a one-element list. The key
    predates v5 (an item could then stand for two steps, or none) and is KEPT at
    exactly that shape: Card 2A's three-stage detail sidebar opens the real
    component from it, and freezing the payload shape is what let v5 land
    without touching a line of client board code.
    """
    items = []
    for stage_group, task_name, label in _TRACKED_ITEM_STEPS:
        status = status_by_task.get(task_name)
        if status == "Approved":
            display = "Completed"
        elif status == "Ready" and task_name in _READY_SHOWS_PENDING:
            display = "Pending Approval"
        else:
            display = "In Progress"
        items.append({"stage": stage_group, "label": label, "status": display,
                      "steps": [task_name]})
    return items


def _lead_priority(rows):
    """The lead's priority: the highest priority still riding on OPEN work.

    There is no stored per-project priority column -- priority is a per-TASK
    field (project_tasks.priority, supervisor-only via
    PATCH /api/tasks/<id>/priority). The lead-level value the board needs is
    therefore derived: the most urgent priority among the tasks the lead still
    has to do. Approved rows are excluded (finished work cannot make a lead
    urgent), and unrecognized/legacy values ("Normal") are ignored, leaving the
    documented "Low" default when nothing applies.

    ``rows`` is already narrowed to the project's applicable active tasks.
    """
    ranks = [_PRIORITY_RANK[r["priority"]] for r in rows
             if r["status"] != "Approved" and r.get("priority") in _PRIORITY_RANK]
    if not ranks:
        return "Low"
    best = min(ranks)
    return next(name for name, rank in _PRIORITY_RANK.items() if rank == best)


def _annotate_card_state(project, rows, stages):
    """Attach the Card 1B card fields to one project dict, in place.

    ``rows`` are the project's active task rows (already loaded); ``stages`` its
    applicable stage groups. No query, no write.
    """
    applicable = [r for r in rows if r["stage_group"] in stages]
    assignees = []
    for row in applicable:
        name = (row.get("assigned_to") or "").strip()
        if name and name not in assignees:
            assignees.append(name)
    project["assignees"] = assignees
    project["lead_priority"] = _lead_priority(applicable)
    # Since v5 the stored stage group IS the board column, so display_stage is
    # the derived current_stage verbatim. The key stays on the payload (the
    # board and its tests read it) rather than making every client fall back to
    # current_stage.
    project["display_stage"] = project.get("current_stage")
    if str(project.get("pipeline_type") or "prospect").lower() == "bp":
        project["tracked_items"] = []   # the BP board carries no tracked items
        return
    # project_tasks has UNIQUE(project_id, task_name), so one row per step.
    project["tracked_items"] = _tracked_items({r["task_name"]: r["status"] for r in applicable})


# ---------------------------------------------------------------------------
# Card 1E -- the record's LATEST saved Mean Gas (BCF), derived at read time
# ---------------------------------------------------------------------------
# There is no stored mean-gas column: the number lives in task_dynamic_fields
# under whichever assessment step last recorded it. LATEST_MEAN_GAS_SOURCES
# (constants.py) is the precedence -- newest assessment first, each surviving
# v4 step immediately followed by the retired step it absorbed -- and is the
# server-side twin of the client's LATEST_PIIP_SOURCES.

# The distinct names/keys the ladder can touch, so the batched query narrows on
# BOTH axes instead of dragging every EAV row of every board project back.
_MEAN_GAS_TASK_NAMES = tuple(dict.fromkeys(name for name, _key in LATEST_MEAN_GAS_SOURCES))
_MEAN_GAS_FIELD_KEYS = tuple(dict.fromkeys(key for _name, key in LATEST_MEAN_GAS_SOURCES))


def _parse_mean_gas(raw, project_id, task_name, field_key):
    """Return a stored mean-gas string as a float, or None (logging garbage).

    A blank/absent value is simply "not recorded" and yields None silently. A
    NON-NUMERIC stored value is a data fault: it is logged and also yields None,
    so the board renders the lead as 0 BCF rather than crashing or showing text
    where a number belongs. The unit is BCF exactly as stored -- no conversion.
    """
    text_value = str(raw if raw is not None else "").strip()
    if not text_value:
        return None
    try:
        value = float(text_value)
    except (TypeError, ValueError):
        logger.warning("Project %s: non-numeric %s.%s mean gas %r; reporting null",
                       project_id, task_name, field_key, raw)
        return None
    # NaN/inf survive float() ("nan", "inf") and would poison a client-side sum.
    if value != value or value in (float("inf"), float("-inf")):
        logger.warning("Project %s: non-finite %s.%s mean gas %r; reporting null",
                       project_id, task_name, field_key, raw)
        return None
    return value


def _annotate_mean_gas(session, projects):
    """Fill ``mean_gas_bcf`` on a list of project dicts, in place.

    ONE batched query for the whole board, keyed by (project_id, task_name) --
    the ladder addresses buckets by step NAME, and a retired step is its own
    name. RETIRED-INCLUSIVE for exactly that reason (no ``is_active`` filter):
    a pre-v4 well whose numbers were entered on "Resource Assessment Update"
    must still resolve, the same way get_project_dynamic_field_map and
    reporting._bp_task_fields stay retired-inclusive.

    Within one (project, step) bucket, legacy duplicate task rows fold
    first-non-blank-wins with the higher task_id winning ties -- the ORDER BY
    plus reporting.fold_task_field_rows' rule, reproduced here because this
    fold is keyed by task_name as well as project.

    Precedence stops at the FIRST NON-BLANK source: that source IS the latest
    saved assessment, so a garbage value there reports null rather than quietly
    presenting an older assessment as the current one. P90/P10 are never read.
    """
    for project in projects:
        project["mean_gas_bcf"] = None
    if not projects:
        return
    rows = db.fetch_all(session, """
        SELECT pt.project_id, pt.task_name, tdf.field_key, tdf.field_value
        FROM project_tasks pt
        JOIN task_dynamic_fields tdf ON tdf.task_id = pt.task_id
        WHERE pt.project_id IN :project_ids
          AND pt.task_name IN :task_names
          AND tdf.field_key IN :field_keys
        ORDER BY pt.task_id
    """, {"project_ids": [p["project_id"] for p in projects],
          "task_names": _MEAN_GAS_TASK_NAMES,
          "field_keys": _MEAN_GAS_FIELD_KEYS})
    folded: Dict[tuple, Dict[str, str]] = {}
    for row in rows:
        bucket = folded.setdefault((row["project_id"], row["task_name"]), {})
        value = row["field_value"] or ""
        if value or row["field_key"] not in bucket:
            bucket[row["field_key"]] = value

    for project in projects:
        project_id = project["project_id"]
        for task_name, field_key in LATEST_MEAN_GAS_SOURCES:
            raw = (folded.get((project_id, task_name)) or {}).get(field_key) or ""
            if not str(raw).strip():
                continue
            project["mean_gas_bcf"] = _parse_mean_gas(raw, project_id, task_name, field_key)
            break


def _annotate_derived_state(session, projects):
    """Fill the derived board pointers on a list of project dicts, in place.

    The projects table stores no current stage/task/owner/status: they are a
    pure function of the active task rows. This is the ONE implementation of
    that derivation, shared by get_projects (board) and get_project (detail):

    - current task  = first active task with status != 'Approved' in the
      pipeline's applicable stages, ordered by sequence_no. Its stage_group,
      assigned_to and priority become current_stage / current_owner /
      current_task_priority; overall_status = 'In Progress'.
    - no open task  = 'Completed', anchored on the LAST applicable active task
      (falling back to the last applicable PIPELINE_TEMPLATES entry when no
      active rows survive: "Approval to Stake" for a prospect, "PDA" for a BP
      well); current_owner is NULL.
    - current_stage_started_at = MIN(actual_start) of the tasks in the derived
      current stage, falling back to the project's start_date.

    One batched query for the whole list, so the board never multiplies rows
    (legacy duplicate task rows collapse into the per-project grouping).

    The board card fields (assignees / tracked_items / display_stage /
    lead_priority) are derived from the SAME batched rows -- see
    _annotate_card_state above; still one query for the whole board.

    Card 1E's ``mean_gas_bcf`` needs task_dynamic_fields, which the task query
    above does not join, so it adds exactly ONE more batched query for the
    whole list (_annotate_mean_gas) -- two queries per board, never per project.
    """
    projects = [p for p in projects if p]
    if not projects:
        return
    task_rows = db.fetch_all(session, """
        SELECT project_id, task_name, stage_group, assigned_to, status, priority, actual_start
        FROM project_tasks
        WHERE project_id IN :project_ids AND is_active = 1
        ORDER BY project_id, sequence_no
    """, {"project_ids": [p["project_id"] for p in projects]})
    by_project: Dict[int, List[Dict[str, Any]]] = {}
    for row in task_rows:
        by_project.setdefault(row["project_id"], []).append(row)

    for project in projects:
        rows = by_project.get(project["project_id"], [])
        stages = applicable_stages(project.get("pipeline_type"))
        open_task = next((r for r in rows if r["stage_group"] in stages and r["status"] != "Approved"), None)
        if open_task:
            anchor = open_task
            current_owner = open_task["assigned_to"]
            overall_status = "In Progress"
        else:
            # Completed: anchor on the final applicable step of the project's
            # OWN pipeline. Prefer the last active applicable row; derive from
            # the templates when none survive, so this stays correct if a
            # later workstream removes/renumbers a step.
            fallback = next((t for t in reversed(PIPELINE_TEMPLATES) if t[2] in stages), None)
            anchor = next((r for r in reversed(rows) if r["stage_group"] in stages), None) \
                or {"task_name": fallback[1] if fallback else None,
                    "stage_group": fallback[2] if fallback else stages[-1],
                    "priority": "Medium"}
            current_owner = None
            overall_status = "Completed"
        current_stage = anchor["stage_group"]
        started = [r["actual_start"] for r in rows
                   if r["stage_group"] == current_stage and r["actual_start"]]
        project["current_stage"] = current_stage
        project["current_task"] = anchor["task_name"]
        project["current_owner"] = current_owner
        project["overall_status"] = overall_status
        project["current_stage_started_at"] = min(started) if started else project.get("start_date")
        project["current_task_priority"] = anchor.get("priority") or "Medium"
        # The field a record belongs to. There is NO stored field column: the
        # field is the first segment of the record name ("GALV-2" -> "GALV"),
        # exactly as folders.parse_field_and_well derives it for the share
        # paths. Deriving it here (instead of a second convention) keeps the
        # board's Field filter and the folder links agreeing by construction.
        project["field"] = folders.parse_field_and_well(project.get("project_name") or "")[0]
        # The board card fields, off the SAME already-loaded rows.
        _annotate_card_state(project, rows, stages)

    # Card 1E's mean gas is the one derived field the task rows above cannot
    # supply (it lives in task_dynamic_fields), so it gets its own single
    # batched query for the whole list -- never one per project.
    _annotate_mean_gas(session, projects)


def get_projects(session, search_text="", stage_filter="All", status_filter="All",
                 owner_filter="All", health_filter="All", sort_key="Well Name", pipeline_filter="All",
                 include_completed=False):
    """Return the (filtered, sorted) project board rows with derived state.

    Search/pipeline/archived filters act on stored columns and stay in SQL; the
    stage/status/owner/health filters act on DERIVED values (see
    _annotate_derived_state) and are applied in Python after annotation.

    ``include_completed`` is an OPT-IN escape from the pipeline board's
    "a finished record leaves its board" rule below. Card 1C's Segment
    Maturation board filters client-side over one dataset and offers an
    explicit "Completed" status, so it asks for the completed leads too;
    every other caller (the BP board, the tests) keeps the default and the
    historical behaviour.

    The active_drilling subquery aggregates per project (one row each), so a
    project with multiple Quicklook task rows carrying the field appears exactly
    once, flagged active if ANY of them is truthy.
    """
    conditions = ["COALESCE(p.archived, 0) = 0"]
    params: Dict[str, Any] = {}
    needle = (search_text or "").strip().lower()
    if needle:
        conditions.append("LOWER(COALESCE(p.project_name, '')) LIKE :search_text")
        params["search_text"] = f"%{needle}%"
    if pipeline_filter in {"prospect", "bp"}:
        conditions.append("LOWER(COALESCE(p.pipeline_type, 'prospect')) = :pipeline_filter")
        params["pipeline_filter"] = pipeline_filter
    where_clause = " AND ".join(conditions)
    rows = db.fetch_all(session, f"""
        SELECT p.*,
               COALESCE(priority_flags.has_high_priority_tasks, 0) AS has_high_priority_tasks,
               COALESCE(active_drilling.is_drilling, 0) AS is_drilling
        FROM projects p
        LEFT JOIN (
            SELECT project_id,
                   MAX(CASE WHEN priority = 'High' THEN 1 ELSE 0 END) AS has_high_priority_tasks
            FROM project_tasks
            WHERE is_active = 1
            GROUP BY project_id
        ) priority_flags ON priority_flags.project_id = p.project_id
        LEFT JOIN (
            -- Aggregated per project so multiple Quicklook rows (legacy +
            -- canonical) never multiply the outer projects row.
            SELECT pt.project_id,
                   MAX(CASE WHEN LOWER(COALESCE(tdf.field_value, '')) IN ('1', 'true', 'yes', 'on')
                            THEN 1 ELSE 0 END) AS is_drilling
            FROM project_tasks pt
            JOIN task_dynamic_fields tdf ON tdf.task_id = pt.task_id
            WHERE pt.task_name IN ('Quicklook Logs Interpretation', 'Quicklook Logs')
              AND tdf.field_key = 'active_drilling'
            GROUP BY pt.project_id
        ) active_drilling ON active_drilling.project_id = p.project_id
        WHERE {where_clause}
        ORDER BY p.project_id DESC
    """, params)
    _annotate_derived_state(session, rows)
    filtered = []
    for item in rows:
        # Drilling is only surfaced while the project sits in Post-Drilling.
        item["active_drilling"] = 1 if (item.get("current_stage") == "Post-Drilling"
                                        and int(item.pop("is_drilling") or 0) == 1) else 0
        item["active_well_enabled"] = int(item.get("active_well_enabled") or 0)
        item["health"] = health_from_target(item.get("target_date"), item.get("overall_status"))
        # A fully-matured lead (every prospect step Approved) leaves the lead
        # board for the Portfolio until a supervisor promotes it; a
        # fully-approved BP well (drilled/finished, including imported
        # historical wells) likewise leaves the BP execution board, while
        # both stay visible in the Portfolio and the Excel export
        # (reporting.py / portfolio_export.py are separate readers, untouched
        # here).
        if (pipeline_filter in ("prospect", "bp") and not include_completed
                and item.get("overall_status") == "Completed"):
            continue
        if stage_filter != "All" and item.get("current_stage") != stage_filter:
            continue
        if status_filter != "All" and item.get("overall_status") != status_filter:
            continue
        if owner_filter != "All" and item.get("current_owner") != owner_filter:
            continue
        if health_filter != "All" and item["health"] != health_filter:
            continue
        filtered.append(item)

    def sort_fn(item):
        if sort_key == "Well Name":
            return (item.get("project_name") or "").lower()
        if sort_key == "Date Created":
            return -(item.get("project_id") or 0)
        if sort_key == "Stage":
            return STAGE_ORDER.index(item["current_stage"]) if item.get("current_stage") in STAGE_ORDER else 999
        if sort_key == "Assignee":
            return (item.get("current_owner") or "").lower()
        if sort_key == "Health":
            return {"Overdue": 0, "Due Soon": 1, "On Track": 2, "Completed": 3}.get(item["health"], 99)
        return parse_iso_date(item.get("target_date")) or date.max
    filtered.sort(key=sort_fn)
    return filtered


def get_project(session, project_id):
    """Return one project dict with derived board pointers, or None."""
    project = db.fetch_one(session, "SELECT * FROM projects WHERE project_id = :project_id",
                           {"project_id": project_id})
    if not project:
        return None
    if not project.get("lead_folder_path"):
        project["lead_folder_path"] = folders.default_lead_folder_path(project.get("project_name") or "")
    _annotate_derived_state(session, [project])
    return project


def project_completion_percent(session, project_id):
    """Percent of the current pipeline's applicable tasks that are done.

    Scoped to the stages of the project's operating pipeline (Prospect
    Maturation stages for prospects, BP Execution stages for BP wells) so the
    figure agrees with the derived overall_status (_annotate_derived_state): a
    prospect that has approved every Prospect-stage task reads 100% even though
    its BP-stage tasks are untouched. The stage filter IS the scope: every task
    in the operating pipeline counts toward the denominator.
    """
    project = get_project(session, project_id) or {}
    stages = applicable_stages(project.get("pipeline_type"))
    row = db.fetch_one(session, """
        SELECT
            COUNT(*) AS applicable_total,
            SUM(CASE WHEN status = 'Approved' THEN 1 ELSE 0 END) AS done
        FROM project_tasks
        WHERE project_id = :project_id AND is_active = 1 AND stage_group IN :stages
    """, {"project_id": project_id, "stages": stages})
    total = int(row["applicable_total"] or 0)
    done = int(row["done"] or 0)
    return round((done / total) * 100, 1) if total else 0.0


def _sync_completed_at(session, project_id):
    """Keep projects.completed_at consistent with the derived completion state.

    ``completed_at`` records when the applicable task set FIRST became fully
    approved -- history, not current state, so it is the one completion fact
    that stays stored. Rule: stamp utc_now when a write leaves the applicable
    set fully approved and the stamp is empty; clear it when a write reopens
    the set. Called from every write that can change completeness: save_task /
    transition_task (status changes) and promotion/demotion (the applicable set
    itself changes). No commit -- runs in the caller's transaction.
    """
    project = db.fetch_one(session,
                           "SELECT pipeline_type, completed_at FROM projects WHERE project_id = :project_id",
                           {"project_id": project_id})
    if not project:
        return
    stages = applicable_stages(project.get("pipeline_type"))
    open_count = db.fetch_one(session, """
        SELECT COUNT(*) AS c FROM project_tasks
        WHERE project_id = :project_id AND is_active = 1 AND stage_group IN :stages
          AND status != 'Approved'
    """, {"project_id": project_id, "stages": stages})["c"]
    if open_count == 0 and not project.get("completed_at"):
        db.execute(session,
                   "UPDATE projects SET completed_at = :now WHERE project_id = :project_id",
                   {"now": utc_now_str(), "project_id": project_id})
    elif open_count > 0 and project.get("completed_at"):
        db.execute(session,
                   "UPDATE projects SET completed_at = NULL WHERE project_id = :project_id",
                   {"project_id": project_id})


def update_project_name(session, project_id, new_name, changed_by="Admin", lead_x=None, lead_y=None):
    """Rename a project, realign default folders, and log the rename event.

    Only the name and lead coordinates are writable here. Promotion state
    (business_plan_enabled / business_plan_year / pipeline_type) and
    active_well_enabled are owned exclusively by update_project_flags
    (workflow/promotion.py), keeping pipeline_type <-> business_plan_enabled
    in lockstep.
    """
    new_name = (new_name or "").strip()
    if not new_name:
        raise ValueError("Lead / well name is required.")
    if len(new_name) > 120:
        raise ValueError("Lead / well name must be 120 characters or less.")
    old = get_project(session, project_id)
    if not old:
        raise ValueError("Lead / well not found.")
    # Friendly duplicate check up front; the IntegrityError catch below still
    # covers the race where another request takes the name in between.
    # Case-/whitespace-insensitive, matching add_project (Card 1D): a rename
    # must not be able to create the case-variant pair creation refuses. The
    # project_id exclusion keeps a pure re-casing of the record's OWN name
    # ('WWWW-44' -> 'wwww-44') legal.
    duplicate = db.fetch_one(session,
                             "SELECT 1 AS present FROM projects "
                             "WHERE lower(trim(project_name)) = lower(trim(:project_name)) "
                             "AND project_id != :project_id",
                             {"project_name": new_name, "project_id": project_id})
    if duplicate:
        raise ValueError("A lead / well with this name already exists.")
    updates: Dict[str, Any] = {"project_name": new_name, "last_updated": utc_now_str()}
    old_default_folder = folders.default_lead_folder_path(old.get("project_name") or "")
    if not old.get("lead_folder_path") or old.get("lead_folder_path") == old_default_folder:
        updates["lead_folder_path"] = folders.default_lead_folder_path(new_name)
    if lead_x is not None:
        updates["lead_x"] = lead_x or None
    if lead_y is not None:
        updates["lead_y"] = lead_y or None
    # Column names come from the fixed allowlisted keys above (never user input);
    # only the values are bound parameters.
    assignments = ", ".join([f"{k} = :{k}" for k in updates])
    try:
        _rename_project(session, project_id, old, new_name, assignments, updates, changed_by)
    except IntegrityError as exc:
        # UNIQUE(project_name) race lost to a concurrent rename/insert.
        if "unique" in str(getattr(exc, "orig", None) or exc).lower():
            raise ValueError("A lead / well with this name already exists.") from exc
        raise


def _rename_project(session, project_id, old, new_name, assignments, updates, changed_by):
    """The transactional body of update_project_name (validation done by caller)."""
    with db.write_transaction(session):
        db.execute(session, f"UPDATE projects SET {assignments} WHERE project_id = :project_id",
                   dict(updates, project_id=project_id))
        # Keep the optional mounted server folder aligned when it is available.
        # UNC links are always regenerated from the current record name.
        try:
            old_field, old_well = folders.parse_field_and_well(old.get("project_name") or "")
            new_field, new_well = folders.parse_field_and_well(new_name)
            for root in (config.WELL_OVERVIEW_DIRECTORY_ROOT, config.LEAD_WORKFLOW_DIRECTORY_ROOT):
                old_path = root / old_field / old_well
                new_path = root / new_field / new_well
                if old_path.exists() and not new_path.exists():
                    new_path.parent.mkdir(parents=True, exist_ok=True)
                    old_path.rename(new_path)
        except Exception:
            # Folder links must not prevent a record rename when the share is not mounted.
            pass
        first_task = db.fetch_one(session,
                                  "SELECT task_id, task_name FROM project_tasks WHERE project_id = :project_id ORDER BY sequence_no LIMIT 1",
                                  {"project_id": project_id})
        if first_task:
            record_type = "Well" if str((old or {}).get("pipeline_type") or "prospect").lower() == "bp" else "Lead"
            log_task_event(session, first_task["task_id"], project_id, first_task["task_name"], f"{record_type} Renamed",
                           old.get("project_name") if old else None, new_name, changed_by, f"Renamed {record_type.lower()} to {new_name}")


def archive_project(session, project_id, changed_by="Admin", *args, **kwargs):
    """Soft-archive a project (recoverable); log the archive event."""
    project = get_project(session, project_id)
    if not project:
        raise ValueError("Lead / well not found.")
    if int(project.get("archived") or 0):
        return
    with db.write_transaction(session):
        db.execute(session,
                   "UPDATE projects SET archived = 1, last_updated = :now, revision = COALESCE(revision, 0) + 1 WHERE project_id = :project_id",
                   {"now": utc_now_str(), "project_id": project_id})
        first_task = db.fetch_one(session,
                                  "SELECT task_id, task_name FROM project_tasks WHERE project_id = :project_id ORDER BY sequence_no LIMIT 1",
                                  {"project_id": project_id})
        if first_task:
            log_task_event(session, first_task["task_id"], project_id, first_task["task_name"], "Well Archived", None, "Archived",
                           changed_by, f"Archived well: {project.get('project_name') or project_id}")


def restore_project(session, project_id, changed_by="Admin"):
    """Restore a previously archived project; log the restore event."""
    project = get_project(session, project_id)
    if not project:
        raise ValueError("Lead / well not found.")
    if not int(project.get("archived") or 0):
        return
    with db.write_transaction(session):
        db.execute(session,
                   "UPDATE projects SET archived = 0, last_updated = :now, revision = COALESCE(revision, 0) + 1 WHERE project_id = :project_id",
                   {"now": utc_now_str(), "project_id": project_id})
        first_task = db.fetch_one(session,
                                  "SELECT task_id, task_name FROM project_tasks WHERE project_id = :project_id ORDER BY sequence_no LIMIT 1",
                                  {"project_id": project_id})
        if first_task:
            log_task_event(session, first_task["task_id"], project_id, first_task["task_name"], "Well Restored", "Archived", "Active",
                           changed_by, f"Restored well: {project.get('project_name') or project_id}")


def delete_project(session, project_id, changed_by="Admin"):
    """Permanent deletion, reserved for controlled maintenance (web routes archive)."""
    with db.write_transaction(session):
        db.execute(session, "DELETE FROM projects WHERE project_id = :project_id",
                   {"project_id": project_id})
