"""Project and task lifecycle domain logic -- the heart of the application.

A package split along the old module's section seams; ``import workflow``
exposes the full public API exactly as the single-file module did:

- constants.py  -- statuses, stages, PIPELINE_TEMPLATES (the single source of
  truth for the 31-step workflow), formation vocabulary, StaleRevisionError.
- users.py      -- login identity lookups (seeded from config.SEED_USERS).
- projects.py   -- project CRUD + the derived board state.
- lifecycle.py  -- task reads/saves, assignment, submit/approve/return.
- promotion.py  -- lead-summary snapshots, BP promotion / demotion, flags.
- formations.py -- well-level formation data (project_formations).
- summary.py    -- the computed overview + Total Chance of Success reads.
- history.py    -- the append-only task_history writer.

What does NOT belong here:
- Pure CoS math (cos.py), report/aggregate SQL (reporting.py), Excel
  (export_excel.py), folder links (folders.py), engine/session (db.py).

Conventions:
- Every public function takes a SQLAlchemy ``session`` as its first argument; no
  hidden globals.
- Writes use ``db.write_transaction`` (upfront write lock, commit on success /
  rollback on error); reads use ``db.fetch_one`` / ``db.fetch_all``. All SQL is
  ``text()`` with named ``:param`` binds (dialect-portable). Rows come back as
  plain dicts.
- Behavior is preserved exactly, including the optimistic-locking revision bumps
  and the actual_start/actual_finish date rules.
- Module imports stay acyclic: constants/history at the bottom, then
  users/projects, then lifecycle/promotion/summary, then formations.
"""
from .constants import (
    ACTIVE_STATUSES,
    BOARD_STAGE_ORDER,
    BP_EXECUTION_STAGES,
    DONE_STATUSES,
    FORMATION_NUMERIC_FIELDS,
    FORMATION_PHASES,
    FORMATION_VALUE_FIELDS,
    FORMATIONS,
    PIPELINE_TEMPLATES,
    PROSPECT_STAGES,
    STAGE_ORDER,
    STATUSES,
    TASK_TRANSITIONS,
    StaleRevisionError,
    applicable_stages,
)
from .formations import get_project_formations, upsert_project_formations
from .history import log_task_event
from .lifecycle import (
    assign_task,
    get_project_tasks,
    get_task,
    get_task_dynamic_fields,
    save_task,
    save_task_dynamic_fields,
    set_task_priority,
    transition_task,
)
from .projects import (
    add_project,
    archive_project,
    delete_project,
    get_project,
    get_projects,
    project_completion_percent,
    restore_project,
    update_project_name,
)
from .promotion import (
    get_lead_summary_snapshot,
    set_business_plan,
    update_project_flags,
)
from .summary import (
    calculate_total_cos,
    get_project_dynamic_field_map,
    get_project_overview,
    last_reservoir_cos_row_value,
    total_cos_from_fields,
)
from .users import find_active_user, get_active_users

__all__ = [
    # constants
    "ACTIVE_STATUSES", "BOARD_STAGE_ORDER", "BP_EXECUTION_STAGES",
    "DONE_STATUSES", "FORMATION_NUMERIC_FIELDS", "FORMATION_PHASES",
    "FORMATION_VALUE_FIELDS", "FORMATIONS", "PIPELINE_TEMPLATES",
    "PROSPECT_STAGES", "STAGE_ORDER", "STATUSES", "TASK_TRANSITIONS",
    "StaleRevisionError", "applicable_stages",
    # users
    "find_active_user", "get_active_users",
    # projects
    "add_project", "archive_project", "delete_project", "get_project",
    "get_projects", "project_completion_percent", "restore_project",
    "update_project_name",
    # lifecycle
    "assign_task", "get_project_tasks", "get_task", "get_task_dynamic_fields",
    "save_task", "save_task_dynamic_fields", "set_task_priority",
    "transition_task",
    # promotion
    "get_lead_summary_snapshot", "set_business_plan", "update_project_flags",
    # formations
    "get_project_formations", "upsert_project_formations",
    # summary
    "calculate_total_cos", "get_project_dynamic_field_map",
    "get_project_overview", "last_reservoir_cos_row_value",
    "total_cos_from_fields",
    # history
    "log_task_event",
]
