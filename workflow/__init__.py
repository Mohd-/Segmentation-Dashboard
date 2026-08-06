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
    AUTO_APPROVE_ON_SAVE_STEPS,
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
    STAKED_WELL_NAME_FIELD,
    display_record_name,
    FORMATION_VALUE_FIELDS,
    FORMATIONS,
    LEAD_FOLDER_HANDOVER_FIELD,
    CANONICAL_RENAME_EVENT,
    WELL_SITE_LOCATION_STEP,
    staking_confirmed,
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
    apply_canonical_name,
    apply_checkbox_submission,
    apply_field_completion,
    guard_staking_name,
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
    annotate_canonical_names,
    canonical_record_name,
    set_project_priority,
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
from .business_plan import (
    APPROVAL_DETAILS as BPE_APPROVAL_DETAILS,
    CLASSIFICATIONS as BPE_CLASSIFICATIONS,
    DETAILS as BPE_DETAILS,
    FLUIDS as BPE_FLUIDS,
    STAGES as BPE_STAGES,
    assign_detail as assign_bpe_detail,
    get_dashboard as get_bpe_dashboard,
    get_detail as get_bpe_detail,
    save_field as save_bpe_field,
    save_flowback_stages as save_bpe_flowback_stages,
    save_formations as save_bpe_formations,
    transition_approval as transition_bpe_approval,
)

__all__ = [
    # constants
    "ACTIVE_STATUSES", "AUTO_APPROVE_ON_SAVE_STEPS",
    "AUTO_COMPLETE_COMMENT", "AUTO_COMPLETE_EVENT",
    "BOARD_STAGE_ORDER", "BP_EXECUTION_STAGES",
    "CHECKBOX_SUBMIT_FROM_STATUSES", "CHECKBOX_SUBMIT_STEPS",
    "DONE_STATUSES", "ENGINE_TRANSITIONS",
    "FIELD_COMPLETION", "FIELD_COMPLETION_COMMENT", "FIELD_COMPLETION_EVENT",
    "FIELD_COMPLETION_MANUAL_APPROVAL_STEPS",
    "FIELD_REOPEN_COMMENT", "FIELD_REOPEN_EVENT",
    "FORMATION_FLUID_TYPES", "FORMATION_NUMERIC_FIELDS",
    "FORMATION_PHASES", "FORMATION_VALUE_FIELDS", "FORMATIONS",
    "display_record_name", "STAKED_WELL_NAME_FIELD", "WELL_SITE_LOCATION_STEP",
    "LEAD_FOLDER_HANDOVER_FIELD", "CANONICAL_RENAME_EVENT", "staking_confirmed",
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
    "annotate_canonical_names", "canonical_record_name",
    "set_project_priority", "update_project_name",
    # lifecycle
    "apply_canonical_name", "apply_checkbox_submission", "apply_field_completion",
    "guard_staking_name", "assign_task",
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
    # approved Business Plan Execution projection
    "BPE_APPROVAL_DETAILS", "BPE_CLASSIFICATIONS", "BPE_DETAILS", "BPE_FLUIDS",
    "BPE_STAGES", "assign_bpe_detail", "get_bpe_dashboard",
    "get_bpe_detail", "save_bpe_field", "save_bpe_flowback_stages",
    "save_bpe_formations", "transition_bpe_approval",
]
