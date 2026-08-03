"""Project and task lifecycle domain logic -- the heart of the application.

A package split along the old module's section seams; ``import workflow``
exposes the full public API exactly as the single-file module did:

- constants.py  -- statuses, stages, PIPELINE_TEMPLATES (the single source of
  truth for the 24-step workflow), formation vocabulary, StaleRevisionError.
- users.py      -- login identity lookups (seeded from config.SEED_USERS).
- projects.py   -- project CRUD + the derived board state.
- lifecycle.py  -- task reads/saves, assignment, submit/approve/return.
- mapdata.py    -- the map's wells overlay (project coordinates + board state).
- notifications.py -- who a transition tells, and the per-user bell feed.
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
    AUTO_COMPLETE_COMMENT,
    AUTO_COMPLETE_EVENT,
    BOARD_STAGE_ORDER,
    BP_EXECUTION_STAGES,
    CHECKBOX_SUBMIT_FROM_STATUSES,
    CHECKBOX_SUBMIT_STEPS,
    DONE_STATUSES,
    ENGINE_TRANSITIONS,
    FIELD_COMPLETION,
    FIELD_COMPLETION_COMMENT,
    FIELD_COMPLETION_EVENT,
    FIELD_COMPLETION_MANUAL_APPROVAL_STEPS,
    FIELD_REOPEN_COMMENT,
    FIELD_REOPEN_EVENT,
    FORMATION_FLUID_TYPES,
    FORMATION_NUMERIC_FIELDS,
    FORMATION_PHASES,
    FORMATION_VALUE_FIELDS,
    FORMATIONS,
    MERGED_COS_LEGACY_NAMES,
    MERGED_COS_TASK_NAME,
    NON_PROSPECTIVE_AUTO_COMPLETE_STEPS,
    NON_PROSPECTIVE_FLUIDS,
    NUMERIC_FIELDS,
    PAY_INTERVAL_NUMERIC_FIELDS,
    PAY_INTERVAL_VALUE_FIELDS,
    PIPELINE_TEMPLATES,
    POSITIVE_NUMBER_FIELDS,
    PROSPECT_STAGES,
    RENAMED_TASK_NAMES,
    REQUIRED_FIELDS_FOR_SUBMIT,
    RETIRED_TASK_NAMES,
    STAKING_WELL_CREATED_KEY,
    STAGE_ORDER,
    STATUSES,
    TASK_TRANSITIONS,
    StaleRevisionError,
    applicable_stages,
    checkbox_submit_met,
    field_completion_met,
    is_number,
    positive_number,
    unmet_submit_requirements,
)
from .formations import (
    auto_complete_non_prospective_steps,
    get_project_formations,
    non_prospective_quicklook_fluid,
    upsert_project_formations,
)
from .history import log_task_event
from .lifecycle import (
    apply_checkbox_submission,
    apply_field_completion,
    assign_task,
    ensure_task_approved,
    get_project_tasks,
    get_task,
    get_task_dynamic_fields,
    satisfy_submit_gate,
    save_task,
    save_task_dynamic_fields,
    set_task_priority,
    transition_task,
)
from .mapdata import map_wells
from .notifications import (
    list_notifications,
    mark_all_read,
    mark_read,
    notification_feed,
    notify_transition,
    unread_count,
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
    first_reservoir_cos_row_value,
    get_project_dynamic_field_map,
    get_project_overview,
    total_cos_from_fields,
)
from .users import SYSTEM_USER, ensure_system_user, find_active_user, get_active_users

__all__ = [
    # constants
    "ACTIVE_STATUSES", "AUTO_COMPLETE_COMMENT", "AUTO_COMPLETE_EVENT",
    "BOARD_STAGE_ORDER", "BP_EXECUTION_STAGES",
    "CHECKBOX_SUBMIT_FROM_STATUSES", "CHECKBOX_SUBMIT_STEPS",
    "DONE_STATUSES", "ENGINE_TRANSITIONS",
    "FIELD_COMPLETION", "FIELD_COMPLETION_COMMENT", "FIELD_COMPLETION_EVENT",
    "FIELD_COMPLETION_MANUAL_APPROVAL_STEPS",
    "FIELD_REOPEN_COMMENT", "FIELD_REOPEN_EVENT",
    "FORMATION_FLUID_TYPES", "FORMATION_NUMERIC_FIELDS",
    "FORMATION_PHASES", "FORMATION_VALUE_FIELDS", "FORMATIONS",
    "MERGED_COS_LEGACY_NAMES", "MERGED_COS_TASK_NAME",
    "NON_PROSPECTIVE_AUTO_COMPLETE_STEPS", "NON_PROSPECTIVE_FLUIDS",
    "NUMERIC_FIELDS",
    "PAY_INTERVAL_NUMERIC_FIELDS", "PAY_INTERVAL_VALUE_FIELDS", "PIPELINE_TEMPLATES",
    "POSITIVE_NUMBER_FIELDS",
    "PROSPECT_STAGES", "RENAMED_TASK_NAMES", "REQUIRED_FIELDS_FOR_SUBMIT",
    "RETIRED_TASK_NAMES", "STAKING_WELL_CREATED_KEY",
    "STAGE_ORDER", "STATUSES", "TASK_TRANSITIONS",
    "StaleRevisionError", "applicable_stages", "checkbox_submit_met",
    "field_completion_met", "is_number", "positive_number",
    "unmet_submit_requirements",
    # users
    "SYSTEM_USER", "ensure_system_user", "find_active_user", "get_active_users",
    # projects
    "add_project", "archive_project", "delete_project", "get_project",
    "get_projects", "project_completion_percent", "restore_project",
    "update_project_name",
    # lifecycle
    "apply_checkbox_submission", "apply_field_completion", "assign_task",
    "ensure_task_approved",
    "get_project_tasks", "get_task",
    "get_task_dynamic_fields", "satisfy_submit_gate", "save_task",
    "save_task_dynamic_fields", "set_task_priority", "transition_task",
    # mapdata
    "map_wells",
    # notifications
    "list_notifications", "mark_all_read", "mark_read", "notification_feed",
    "notify_transition", "unread_count",
    # promotion
    "get_lead_summary_snapshot", "set_business_plan", "update_project_flags",
    # formations
    "auto_complete_non_prospective_steps", "get_project_formations",
    "non_prospective_quicklook_fluid", "upsert_project_formations",
    # summary
    "calculate_total_cos", "first_reservoir_cos_row_value",
    "get_project_dynamic_field_map", "get_project_overview",
    "total_cos_from_fields",
    # history
    "log_task_event",
]
