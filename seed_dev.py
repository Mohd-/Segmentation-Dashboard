"""Synthetic seed data for local development / UI verification (Track A).

Fills a dev database with realistic projects, tasks, comments and history by
driving the SAME domain functions the app itself uses -- workflow.add_project,
workflow.assign_task, workflow.transition_task, workflow.save_task,
workflow.save_task_dynamic_fields, workflow.upsert_project_formations -- never
raw SQL for anything the domain layer already exposes. That keeps derived
state (board pointers, Total CoS, Portfolio composition) and the audit trail
exactly as consistent as if a human had driven the UI. The one direct-SQL
exception is an idempotent extra-user seed, mirroring migrations.py's own
``users`` seeding idiom (INSERT OR IGNORE keyed on the UNIQUE name).

Usage:
    SEGMENT_TRACKER_DB_PATH=/tmp/seed.db .venv/bin/python seed_dev.py
    SEGMENT_TRACKER_DB_PATH=/tmp/seed.db .venv/bin/python seed_dev.py --force

Refuses to run against a database that already has projects (prints the count
and exits non-zero) unless ``--force``, which only ADDS more synthetic data --
existing rows are never touched or deleted.

Reservoir CoS is normally model-derived (cos.calculate_reservoir_cos_rows via
the approved RF_model.joblib, which is deployed out-of-band and not versioned
-- see .gitignore). This script writes already-scored reservoir_cos_rows JSON
straight through workflow.save_task_dynamic_fields, the same path
PATCH /api/tasks/<id>/dynamic-fields uses, which does not re-invoke the model
(only the full workflow.save_task path does, for a live Reservoir CoS save).
That keeps seeding independent of the model file while still going through
the domain layer. Seal CoS has no such external dependency, so its inputs are
seeded and left to the real formula (cos.calculate_seal_cos, invoked by
save_task_dynamic_fields for the "Seal CoS" task) to compute the stored
percentage.
"""
from __future__ import annotations

import argparse
import json
import random
import sys

import config
import db
import workflow
from helpers import utc_now_str

# ---------------------------------------------------------------------------
# Extra synthetic users (idempotent direct-SQL insert -- the one sanctioned
# exception; mirrors migrations._ensure_base_data's INSERT OR IGNORE idiom).
# Do NOT touch config.SEED_USERS: that list is the owner's placeholder for the
# real team roster.
# ---------------------------------------------------------------------------
EXTRA_USERS = [
    ("Test Supervisor A", "supervisor"),
    ("Test Supervisor B", "supervisor"),
    ("Test Staff A", "staff"),
    ("Test Staff B", "staff"),
    ("Test Employee A", "employee"),
    ("Test Employee B", "employee"),
    ("Test Employee C", "employee"),
    ("Test Employee D", "employee"),
]

# Distinct field-code pools so prospect and BP well names never collide, and
# BP well names stay in the "FIELD-N" shape reporting.py's gas_field parser
# (folders.parse_field_and_well) expects.
PROSPECT_FIELD_CODES = ["GALV", "ORYX", "FYNN", "CROX", "WREN", "IBEX", "LUNA", "VEGA"]
BP_FIELD_CODES = ["MDFT", "QASM", "SARH", "RUBX", "TANQ", "HOFR", "DYNE", "KELS", "BRAN"]

DRILLED_FLUIDS = ["Dry", "Gas", "Water", "Condensate", "Liquid", "Gas over Water"]
GHEER_CLASSIFICATIONS = ["Development", "Appraisal", "Exploration"]
PULL_UP_OPTIONS = ["No", "Semi", "Yes"]

COMMENT_SAMPLES = [
    "Looks good, proceeding to next step.",
    "Waiting on updated seismic volume before finalizing.",
    "Cross-checked against offset well data -- consistent.",
    "Needs supervisor sign-off before submission.",
    "Minor revision requested; resubmitting shortly.",
    "Flagging for review at next segment meeting.",
    "Numbers reconciled with the regional database.",
]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _seed_extra_users(session) -> None:
    """Idempotently insert EXTRA_USERS so assignees/actors vary in the seed."""
    with db.write_transaction(session):
        for name, role in EXTRA_USERS:
            db.execute(session, """
                INSERT OR IGNORE INTO users (name, role, created_at)
                VALUES (:name, :role, :now)
            """, {"name": name, "role": role, "now": utc_now_str()})


def _unique_name(session, prefix) -> str:
    """First "<prefix>-<n>" not already used, so a --force rerun never
    collides with names from a prior run (or a hand-created project)."""
    n = 1
    while True:
        candidate = f"{prefix}-{n}"
        if not db.fetch_one(session, "SELECT 1 FROM projects WHERE project_name = :name",
                            {"name": candidate}):
            return candidate
        n += 1


def _ar_number(n) -> str:
    return f"AR-{n:07d}"


def _prospect_stage_windows():
    """Map each PROSPECT_STAGES stage to (start_index, step_count) within the
    ordered prospect-stage task list, derived from PIPELINE_TEMPLATES rather
    than a hardcoded step count so lead placement tracks the pipeline
    definition if it ever changes."""
    windows = {}
    index = 0
    for _seq, _name, stage in workflow.PIPELINE_TEMPLATES:
        if stage not in workflow.PROSPECT_STAGES:
            continue
        start, count = windows.get(stage, (index, 0))
        windows[stage] = (start, count + 1)
        index += 1
    return windows


# ---------------------------------------------------------------------------
# Lifecycle drivers (assign / submit / return / approve via the domain layer)
# ---------------------------------------------------------------------------

def _complete_task(session, task, assignee, role_by_name, approver, cycle=False):
    """Drive one task to Approved: assign -> submit -> approve.

    When ``cycle`` is set, insert a submit -> return -> submit round-trip
    first, so the Audit Trail carries a realistic back-and-forth.
    """
    role = role_by_name.get(assignee)
    task = workflow.assign_task(session, task["task_id"], assignee, cascade=False, changed_by=assignee)
    task = workflow.transition_task(session, task["task_id"], "submit", changed_by=assignee,
                                     actor_role=role, actor_name=assignee)
    if cycle:
        task = workflow.transition_task(session, task["task_id"], "return", changed_by=approver)
        task = workflow.transition_task(session, task["task_id"], "submit", changed_by=assignee,
                                         actor_role=role, actor_name=assignee)
    return workflow.transition_task(session, task["task_id"], "approve", changed_by=approver)


def _advance_to(session, task, status, assignee, role_by_name):
    """Drive one task to Not Assigned / In Progress / Ready (never Approved --
    callers wanting Approved use _complete_task so the audit trail and
    actual_start/finish stamping stay realistic)."""
    if status == "Not Assigned":
        return task
    task = workflow.assign_task(session, task["task_id"], assignee, cascade=False, changed_by=assignee)
    if status == "In Progress":
        return task
    role = role_by_name.get(assignee)
    return workflow.transition_task(session, task["task_id"], "submit", changed_by=assignee,
                                    actor_role=role, actor_name=assignee)


def _seed_pipeline_progress(session, tasks, approve_count, anchor_status, users, role_by_name, supervisors):
    """Approve the first ``approve_count`` tasks (occasionally with a
    submit/return/submit cycle for audit-trail depth), then drive the next
    task -- the one the board derives as "current" -- to ``anchor_status``.
    Tasks beyond that stay Not Assigned exactly as materialized, so stage
    placement is controlled precisely by ``approve_count``.
    """
    for task in tasks[:approve_count]:
        assignee = random.choice(users)
        approver = random.choice(supervisors)
        _complete_task(session, task, assignee, role_by_name, approver, cycle=random.random() < 0.25)
    if approve_count < len(tasks) and anchor_status != "Not Assigned":
        _advance_to(session, tasks[approve_count], anchor_status, random.choice(users), role_by_name)


def _sprinkle_priorities(session, tasks, changed_by):
    """Set Low/Medium/High on a few tasks so the priority chips/filters vary."""
    for task in random.sample(tasks, k=min(3, len(tasks))):
        workflow.set_task_priority(session, task["task_id"], random.choice(["Low", "Medium", "High"]),
                                   changed_by=changed_by)


def _add_comment(session, task, changed_by):
    """Attach a comment via save_task, preserving the task's current priority
    explicitly -- save_task resets priority to "Medium" when the key is
    absent from the payload (it has no "supplied" guard like assigned_to's),
    so a comment-only save must resend it to avoid silently flattening the
    priority variety seeded above."""
    fresh = workflow.get_task(session, task["task_id"])
    workflow.save_task(session, task["task_id"],
                       {"comments": random.choice(COMMENT_SAMPLES), "priority": fresh.get("priority") or "Medium"},
                       changed_by=changed_by)


# ---------------------------------------------------------------------------
# BP well field builders (feed reporting._BP_TASK_FIELD_KEYS / the Portfolio)
# ---------------------------------------------------------------------------

def _piip_fields(prefix):
    """One PIIP mean/P90/P10 trio (BCF), P90 low - Mean - P10 high."""
    mean = round(random.uniform(2.0, 20.0), 2)
    return {
        f"{prefix}_gas_p90": round(mean * random.uniform(0.5, 0.75), 2),
        f"{prefix}_gas_mean": mean,
        f"{prefix}_gas_p10": round(mean * random.uniform(1.3, 1.8), 2),
    }


def _random_block_ar_pair():
    """A coherent (block, ar) pair drawn from config.SEISMIC_BLOCK_AR_MAP, or
    ("", "") when the map is empty (falls back to the synthetic _ar_number
    behavior below so seeding still works with no seismic_blocks.json)."""
    block_map = config.SEISMIC_BLOCK_AR_MAP
    if not block_map:
        return "", ""
    block = random.choice(list(block_map.keys()))
    ars = block_map.get(block) or []
    if not ars:
        return block, ""
    return block, random.choice(ars)


def _reservoir_cos_rows(force_ar_one=False):
    """A pre-scored Reservoir CoS Evaluations row set (see module docstring
    for why this bypasses the RF model instead of calling
    cos.calculate_reservoir_cos_rows)."""
    rows = []
    for _ in range(random.randint(1, 3)):
        block, ar = _random_block_ar_pair()
        row = {
            "amplitude_ratio": round(random.uniform(0.1, 0.9), 2),
            "base_tight_sarah": round(random.uniform(0.1, 0.9), 2),
            "pull_up": random.choice(PULL_UP_OPTIONS),
            "reservoir_cos_pct": str(random.randint(20, 90)),
        }
        if block or ar:
            row["seismic_block"] = block
            row["seismic_volume_ar_number"] = ar
        else:
            row["seismic_block"] = ""
            row["seismic_volume_ar_number"] = _ar_number(random.randint(2, 40))
        rows.append(row)
    if force_ar_one:
        # Deterministically pins the last row to the FIRST block's FIRST AR
        # in config.SEISMIC_BLOCK_AR_MAP, demonstrating the Portfolio's
        # Block/AR mapping without editing config.py or seismic_blocks.json.
        block_map = config.SEISMIC_BLOCK_AR_MAP
        if block_map:
            first_block = next(iter(block_map))
            first_ars = block_map.get(first_block) or []
            rows[-1]["seismic_block"] = first_block
            rows[-1]["seismic_volume_ar_number"] = first_ars[0] if first_ars else ""
        else:
            rows[-1]["seismic_block"] = ""
            rows[-1]["seismic_volume_ar_number"] = _ar_number(1)
    return json.dumps(rows, separators=(",", ":"))


def _seal_fields():
    """Raw Seal CoS inputs; save_task_dynamic_fields computes seal_cos_pct
    from these via the real formula (cos.calculate_seal_cos)."""
    return {
        "seal_recent_activity_age": round(random.uniform(0.1, 1.4), 2),
        "seal_dip": round(random.uniform(0.1, 0.9), 2),
        "seal_azimuth_vs_shmax": round(random.uniform(0.1, 0.9), 2),
        "seal_fault_level_confidence": round(random.uniform(0.1, 0.9), 2),
        "seal_fracture_permeability": round(random.uniform(0.1, 0.9), 2),
    }


def _formation_row(formation):
    """One project_formations row; numeric fields are real numbers (not
    strings) as upsert_project_formations expects for a clean coercion."""
    top = round(random.uniform(8500, 12000), 1)
    thickness = round(random.uniform(30, 150), 1)
    return {
        "formation": formation,
        "top_tvdss_ft": top,
        "base_tvdss_ft": round(top + thickness, 1),
        "thickness_ft": thickness,
        "porosity_pct": round(random.uniform(5, 16), 1),
        "swt_pct": round(random.uniform(18, 45), 1),
        "pay_ft": round(random.uniform(10, thickness), 1),
        "ngr_pct": round(random.uniform(4, 22), 1),
        "fluid": random.choice(DRILLED_FLUIDS),
    }


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------

def _seed_prospect_leads(session, users, role_by_name, supervisors):
    """~16 prospect leads, 4 spread across each of the 4 PROSPECT_STAGES.

    Stage placement is DERIVED from task rows (workflow._annotate_derived_state
    reads the first non-Approved task in sequence order): approving a stage's
    full prefix and leaving the next task un-Approved lands the board's
    current_stage on it.
    """
    windows = _prospect_stage_windows()
    stage_targets = []
    for stage in workflow.PROSPECT_STAGES:
        stage_targets += [stage] * 4
    random.shuffle(stage_targets)

    project_ids = []
    for i, stage in enumerate(stage_targets):
        code = PROSPECT_FIELD_CODES[i % len(PROSPECT_FIELD_CODES)]
        name = _unique_name(session, code)
        pid = workflow.add_project(session, name, changed_by=random.choice(users))
        tasks = [t for t in workflow.get_project_tasks(session, pid)
                if t["stage_group"] in workflow.PROSPECT_STAGES]

        start, count = windows[stage]
        approve_count = start + random.randint(0, count - 1)
        anchor_status = random.choice(["Not Assigned", "In Progress", "Ready"])
        _seed_pipeline_progress(session, tasks, approve_count, anchor_status, users, role_by_name, supervisors)
        _sprinkle_priorities(session, tasks, random.choice(users))
        project_ids.append(pid)
    return project_ids


def _seed_bp_wells(session, users, role_by_name, supervisors):
    """8-10 BP wells at varied maturity, with the Portfolio-composing task
    fields filled in via save_task_dynamic_fields (independent of each task's
    lifecycle status, exactly like a real user entering data mid-workflow).
    """
    years = [2026, 2027, 2028, 2029, 2030, 2031, 2032, 2026, 2029]
    project_ids = []
    for i, code in enumerate(BP_FIELD_CODES):
        name = _unique_name(session, code)
        pid = workflow.add_project(
            session, name, pipeline_type="bp", business_plan_enabled=True,
            business_plan_year=years[i % len(years)], active_well_enabled=(i % 2 == 0),
            changed_by=random.choice(users),
        )
        project_ids.append(pid)

        tasks = workflow.get_project_tasks(session, pid)
        bp_tasks = [t for t in tasks if t["stage_group"] in workflow.BP_EXECUTION_STAGES]
        by_name = {t["task_name"]: t for t in tasks}

        # maturity: 0 = just entered Well Delivery, 1 = mid Post-Drilling,
        # 2 = fully drilled and tested (gets formation data + final logs).
        maturity = i % 3
        approve_count = {
            0: random.randint(0, 4),
            1: random.randint(5, 11),
            2: random.randint(12, 18),
        }[maturity]
        anchor_status = random.choice(["Not Assigned", "In Progress", "Ready"])
        _seed_pipeline_progress(session, bp_tasks, approve_count, anchor_status, users, role_by_name, supervisors)
        _sprinkle_priorities(session, tasks, random.choice(users))

        # Always filled, regardless of maturity: the Lead/Trap/Seal/GHEER
        # inputs a prospect would have captured before ever reaching BP.
        workflow.save_task_dynamic_fields(session, by_name["Lead Resource Assessment"]["task_id"],
                                          _piip_fields("lead_piip"), changed_by="Seed Script")
        workflow.save_task_dynamic_fields(session, by_name["Pre-Drilling Resource Assessment"]["task_id"],
                                          _piip_fields("pre_drill_piip"), changed_by="Seed Script")
        workflow.save_task_dynamic_fields(session, by_name["Reservoir CoS"]["task_id"],
                                          {"reservoir_cos_rows": _reservoir_cos_rows(force_ar_one=(i == 0))},
                                          changed_by="Seed Script")
        workflow.save_task_dynamic_fields(session, by_name["Trap CoS"]["task_id"],
                                          {"trap_cos_pct": str(random.randint(20, 90))}, changed_by="Seed Script")
        workflow.save_task_dynamic_fields(session, by_name["Seal CoS"]["task_id"],
                                          _seal_fields(), changed_by="Seed Script")
        workflow.save_task_dynamic_fields(session, by_name["GHEER"]["task_id"],
                                          {"gheer_classification": random.choice(GHEER_CLASSIFICATIONS)},
                                          changed_by="Seed Script")

        if maturity >= 1:
            workflow.save_task_dynamic_fields(session, by_name["Quicklook Logs Interpretation"]["task_id"],
                                              {"quicklook_fluid_type": random.choice(DRILLED_FLUIDS)},
                                              changed_by="Seed Script")

        if maturity == 2:
            workflow.save_task_dynamic_fields(session, by_name["Post-Drilling Resource Assessment"]["task_id"],
                                              _piip_fields("post_drill_piip"), changed_by="Seed Script")
            workflow.save_task_dynamic_fields(session, by_name["Final Log Analysis"]["task_id"],
                                              {"final_fluid_type": random.choice(DRILLED_FLUIDS)},
                                              changed_by="Seed Script")
            workflow.save_task_dynamic_fields(session, by_name["Resource Assessment Update"]["task_id"],
                                              _piip_fields("resource_update"), changed_by="Seed Script")
            workflow.save_task_dynamic_fields(session, by_name["Flowback Results"]["task_id"],
                                              {"flowback_gas_rate_mmscfd": round(random.uniform(1, 15), 2)},
                                              changed_by="Seed Script")

            # A "few drilled wells" get formation interpretation rows.
            workflow.upsert_project_formations(
                session, pid, "quicklook", [_formation_row(f) for f in workflow.FORMATIONS],
                changed_by="Seed Script", source_task_id=by_name["Quicklook Logs Interpretation"]["task_id"])
            workflow.upsert_project_formations(
                session, pid, "final", [_formation_row(f) for f in workflow.FORMATIONS],
                changed_by="Seed Script", source_task_id=by_name["Final Log Analysis"]["task_id"])
    return project_ids


def _seed_comments(session, project_ids, users):
    """Scatter comments (and the priority-preserving save they trigger)
    across a sample of touched tasks so the Audit Trail is non-trivial."""
    for pid in random.sample(project_ids, k=min(15, len(project_ids))):
        touched = [t for t in workflow.get_project_tasks(session, pid) if t["status"] != "Not Assigned"]
        if touched:
            _add_comment(session, random.choice(touched), random.choice(users))


def seed(session) -> None:
    _seed_extra_users(session)
    users_rows = db.fetch_all(session, "SELECT name, role FROM users WHERE is_active = 1 ORDER BY name")
    role_by_name = {row["name"]: row["role"] for row in users_rows}
    users = [row["name"] for row in users_rows]
    supervisors = [row["name"] for row in users_rows if row["role"] == "supervisor"]

    random.seed(42)
    prospects = _seed_prospect_leads(session, users, role_by_name, supervisors)
    wells = _seed_bp_wells(session, users, role_by_name, supervisors)
    _seed_comments(session, prospects + wells, users)

    total_tasks = db.fetch_one(session, "SELECT COUNT(*) AS c FROM task_history")["c"]
    print(f"Seeded {len(prospects)} prospect leads and {len(wells)} BP wells "
          f"({len(users)} active users, {total_tasks} task_history rows total).")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true",
                        help="Seed even if projects already exist (only ADDS -- never deletes).")
    args = parser.parse_args()

    print(f"Target database: {config.db_path()}")
    db.init_db()  # Same bootstrap main.py runs at import time: create_all + seed config.SEED_USERS.
    session = db.new_session()
    try:
        existing = db.fetch_one(session, "SELECT COUNT(*) AS c FROM projects")["c"]
        if existing and not args.force:
            print(f"Refusing to seed: {existing} project(s) already exist in this database. "
                  f"Pass --force to add more synthetic data (existing rows are never deleted).",
                  file=sys.stderr)
            sys.exit(1)
        seed(session)
    finally:
        session.close()


if __name__ == "__main__":
    main()
