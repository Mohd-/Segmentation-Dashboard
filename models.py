"""SQLAlchemy ORM models -- the single authoritative description of the schema.

This file IS the schema, not a mirror of one. Every table and column here is
what the application stores; there is no separate "production schema" it must
stay compatible with. Pre-deployment (nothing is in production yet), changing
this file to change the schema is normal: edit the model, delete the local
``.db`` file (and its ``-shm``/``-wal`` sidecars), and restart the app --
``migrations.run`` recreates and reseeds a fresh database from exactly what's
defined here. See migrations.py for when that stops being true (first
production deployment) and a real migration path becomes necessary.

What belongs here:
- ``declarative_base`` table definitions and their indexes.

What does NOT belong here:
- Queries, business logic, migrations, or engine/session setup (see db.py,
  migrations.py, workflow.py, reporting.py).

Style note: we use classic ``Column(...)`` definitions (NOT the 2.0-only
``Mapped[]``/``mapped_column``) so this file works under both SQLAlchemy 1.4 and
2.0. ``Base.metadata.create_all`` builds the whole schema from these
definitions on every bootstrap (see migrations.py).
"""
from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    REAL,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Project(Base):
    """A lead or well tracked through the maturation/execution pipeline.

    ``pipeline_type`` ('prospect' or 'bp') decides which operational board it
    belongs to; ``business_plan_enabled`` controls Portfolio inclusion.

    Board pointers (current stage/task/owner, overall status, stage-started
    date) are NOT stored: they are fully derivable from ``project_tasks`` and
    are computed at read time by ``workflow._annotate_derived_state``. Only
    ``completed_at`` persists, because "when did the applicable set FIRST
    become fully approved" is history, not current state.
    """
    __tablename__ = "projects"

    project_id = Column(Integer, primary_key=True)
    project_name = Column(Text, nullable=False, unique=True)
    start_date = Column(Text)
    target_date = Column(Text)
    business_plan_enabled = Column(Integer, nullable=False, server_default=text("0"))
    business_plan_year = Column(Integer)
    active_well_enabled = Column(Integer, nullable=False, server_default=text("0"))
    pipeline_type = Column(Text, nullable=False, server_default=text("'prospect'"))
    last_updated = Column(Text)
    archived = Column(Integer, nullable=False, server_default=text("0"))
    lead_folder_path = Column(Text)
    lead_x = Column(REAL)
    lead_y = Column(REAL)
    revision = Column(Integer, nullable=False, server_default=text("0"))
    # Set by workflow._sync_completed_at exactly when the applicable task set
    # becomes fully approved; cleared (NULL) when the project reopens. Kept
    # stored (v16) so completion-month reporting no longer drifts with later
    # edits.
    completed_at = Column(Text)

    __table_args__ = (
        Index("idx_projects_archived_pipeline", "archived", "pipeline_type"),
        Index("idx_projects_portfolio", "archived", "business_plan_enabled",
              "business_plan_year", "active_well_enabled"),
        {"sqlite_autoincrement": True},
    )


class ProjectTask(Base):
    """One workflow component instance for a project (the working task rows).

    Materialized from ``workflow.PIPELINE_TEMPLATES`` (the in-code workflow
    definition -- there is no templates table) at project creation. Retired
    components stay as ``is_active = 0`` records so their inputs and audit
    trail survive. ``revision`` powers optimistic locking on save.
    """
    __tablename__ = "project_tasks"

    task_id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.project_id", ondelete="CASCADE"), nullable=False)
    sequence_no = Column(Integer, nullable=False)
    task_name = Column(Text, nullable=False)
    stage_group = Column(Text, nullable=False)
    assigned_to = Column(Text)
    status = Column(Text, nullable=False, server_default=text("'Not Started'"))
    actual_start = Column(Text)
    actual_finish = Column(Text)
    comments = Column(Text)
    priority = Column(Text, nullable=False, server_default=text("'Normal'"))
    business_plan_enabled = Column(Integer, nullable=False, server_default=text("0"))
    business_plan_year = Column(Integer)
    is_active = Column(Integer, nullable=False, server_default=text("1"))
    last_updated = Column(Text)
    revision = Column(Integer, nullable=False, server_default=text("0"))

    __table_args__ = (
        UniqueConstraint("project_id", "task_name"),
        Index("idx_project_tasks_project_active_sequence", "project_id", "is_active", "sequence_no"),
        Index("idx_project_tasks_project_status", "project_id", "status"),
        Index("idx_project_tasks_project_name", "project_id", "task_name"),
        {"sqlite_autoincrement": True},
    )


class TaskHistory(Base):
    """Append-only audit trail of every status/field/lifecycle change."""
    __tablename__ = "task_history"

    history_id = Column(Integer, primary_key=True)
    task_id = Column(Integer, ForeignKey("project_tasks.task_id", ondelete="CASCADE"), nullable=False)
    project_id = Column(Integer, ForeignKey("projects.project_id", ondelete="CASCADE"), nullable=False)
    task_name = Column(Text, nullable=False)
    action_type = Column(Text)
    old_status = Column(Text)
    new_status = Column(Text)
    changed_at = Column(Text, nullable=False)
    changed_by = Column(Text)
    comment = Column(Text)

    __table_args__ = (
        Index("idx_task_history_project_changed", "project_id", text("changed_at DESC")),
        {"sqlite_autoincrement": True},
    )


class TaskDynamicField(Base):
    """Free-form key/value inputs attached to a task (the component form data)."""
    __tablename__ = "task_dynamic_fields"

    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, ForeignKey("project_tasks.task_id", ondelete="CASCADE"), nullable=False)
    field_key = Column(Text, nullable=False)
    field_value = Column(Text)
    updated_at = Column(Text)

    __table_args__ = (
        UniqueConstraint("task_id", "field_key"),
        Index("idx_task_dynamic_fields_task_key", "task_id", "field_key"),
        {"sqlite_autoincrement": True},
    )


class ProjectFormation(Base):
    """Per-well formation interpretation values.

    ``formation`` accepts the canonical trio (SARH / QASM / QWRH) OR a custom,
    user-entered name (normalized ``strip().upper()``, non-empty, <= 40 chars --
    enforced by ``workflow.formations.upsert_project_formations``, not a DB
    constraint). One row per (project, formation, phase). Formation data
    belongs to the WELL, not to a workflow step: the quicklook, post-drill,
    final and resource-update interpretation components edit these rows
    through the formations mini-sheet, and ``source_task_id`` records which
    component last wrote them (also the anchor for the "Formation Data
    Updated" history event).

    Measurement columns are REAL (they are genuinely numeric and get computed
    on -- averages, ratios, comparisons); ``fluid`` stays TEXT (a free-text
    description, e.g. "Gas over Water"). ``workflow.upsert_project_formations``
    coerces incoming values to float (blank/whitespace -> NULL) and raises
    ValueError -> 400 on anything non-numeric.
    """
    __tablename__ = "project_formations"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.project_id", ondelete="CASCADE"), nullable=False)
    formation = Column(Text, nullable=False)
    phase = Column(Text, nullable=False)
    top_tvdss_ft = Column(REAL)
    base_tvdss_ft = Column(REAL)
    thickness_ft = Column(REAL)
    porosity_pct = Column(REAL)
    swt_pct = Column(REAL)
    pay_ft = Column(REAL)
    ngr_pct = Column(REAL)
    fluid = Column(Text)
    source_task_id = Column(Integer)
    updated_at = Column(Text)
    updated_by = Column(Text)

    __table_args__ = (
        UniqueConstraint("project_id", "formation", "phase"),
        CheckConstraint("phase IN ('quicklook','post_drill','final','resource_update')"),
        Index("idx_project_formations_project", "project_id"),
        {"sqlite_autoincrement": True},
    )


class LeadSummarySnapshot(Base):
    """Frozen JSON of a lead's Prospect-stage inputs captured at BP promotion."""
    __tablename__ = "lead_summary_snapshots"

    project_id = Column(Integer, ForeignKey("projects.project_id", ondelete="CASCADE"), primary_key=True)
    snapshot_json = Column(Text, nullable=False)
    captured_at = Column(Text, nullable=False)
    captured_by = Column(Text)


class BusinessPlanCommitment(Base):
    """Single-row (commitment_id = 1) Business Plan commitment totals."""
    __tablename__ = "business_plan_commitment"

    commitment_id = Column(Integer, primary_key=True)
    produced = Column(REAL, nullable=False, server_default=text("0"))
    pending_tie_in = Column(REAL, nullable=False, server_default=text("0"))
    base = Column(REAL, nullable=False, server_default=text("0"))
    core_extension_wells = Column(REAL, nullable=False, server_default=text("0"))
    planned_yet_to_find = Column(REAL, nullable=False, server_default=text("0"))
    last_updated = Column(Text)

    __table_args__ = (
        CheckConstraint("commitment_id = 1"),
    )


class AppSetting(Base):
    """Key/value application settings; holds ``schema_version`` for migrations."""
    __tablename__ = "app_settings"

    key = Column(Text, primary_key=True)
    value = Column(Text, nullable=False)


class User(Base):
    """A known application user (login identity + role for permissions).

    Seeded from ``config.SEED_USERS`` on every bootstrap (idempotent INSERT OR
    IGNORE by name in migrations._ensure_base_data). Login (POST /api/login)
    only accepts names present here with ``is_active = 1``; the matched row's
    ``role`` is stored in the session and drives role-gated actions.

    ``Base.metadata.create_all`` runs on every bootstrap (migrations.run), so
    this table is created automatically for existing databases -- no numbered
    migration step is needed for a purely additive table.
    """
    __tablename__ = "users"

    user_id = Column(Integer, primary_key=True)
    name = Column(Text, nullable=False, unique=True)
    role = Column(Text, nullable=False, server_default=text("'employee'"))
    # Optional per-user password (werkzeug generate_password_hash). NULL/blank
    # keeps the pre-password behavior: login by name alone (plus the shared
    # passcode when config.SHARED_PASSCODE is set). Set via add_users.py.
    password_hash = Column(Text)
    is_active = Column(Integer, nullable=False, server_default=text("1"))
    created_at = Column(Text)

    __table_args__ = (
        CheckConstraint("role IN ('supervisor','staff','employee')"),
        {"sqlite_autoincrement": True},
    )
