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
save_task_dynamic_fields for the merged "Trap and Seal CoS" task) to compute
the stored percentage.
"""
from __future__ import annotations

import argparse
import hashlib
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

# ---------------------------------------------------------------------------
# Map coordinates (UTM Zone 37N metres -- the plane the map surface draws in)
# ---------------------------------------------------------------------------
# Every seeded record is born with a lead X/Y so the map has pins in dev. One
# CENTER per field code, because a field is a place: GALV-1 and GALV-2 belong
# beside each other, not scattered across the country. The centers sit inside
# Saudi Arabia's UTM37N extent (eastings ~250-800 km, northings ~2200-3300 km)
# and are grouped into the four quadrants scripts/seed_map_layers.py draws its
# sample blocks around -- that script imports LEAD_CLUSTER_CENTERS from here,
# so the blocks always enclose the wells and the two never drift apart.
#
# Offsets within a field are DETERMINISTIC (a hash of the record name, never
# random): re-seeding puts the same well in the same place, and the map is
# diffable across runs. random.seed(42) governs the rest of this script, but a
# coordinate drawn from the shared stream would move every well whenever any
# earlier draw changed.
LEAD_CLUSTER_CENTERS = {
    # North-west quadrant (Block A)
    "GALV": (350000.0, 3100000.0),
    "ORYX": (460000.0, 2950000.0),
    "FYNN": (330000.0, 2870000.0),
    "MDFT": (470000.0, 3180000.0),
    "QASM": (390000.0, 2980000.0),
    # North-east quadrant (Block B)
    "CROX": (600000.0, 3100000.0),
    "WREN": (720000.0, 2950000.0),
    "SARH": (650000.0, 2870000.0),
    "RUBX": (740000.0, 3180000.0),
    # South-west quadrant (Block C)
    "IBEX": (340000.0, 2600000.0),
    "LUNA": (460000.0, 2450000.0),
    "TANQ": (380000.0, 2320000.0),
    "HOFR": (490000.0, 2620000.0),
    # South-east quadrant (Block D)
    "VEGA": (610000.0, 2600000.0),
    "DYNE": (730000.0, 2450000.0),
    "KELS": (620000.0, 2320000.0),
    "BRAN": (750000.0, 2620000.0),
}

# Half-width of the per-record scatter around its field center, in metres. A
# few km: far enough apart to be distinguishable pins at field zoom, close
# enough that the field still reads as one cluster (and well inside the sample
# blocks, whose edges are ~30 km further out).
LEAD_CLUSTER_JITTER_M = 5000.0

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


def _lead_coordinates(name):
    """Deterministic (lead_x, lead_y) in UTM37N metres for a record name.

    The field code (the part before the dash, exactly as
    folders.parse_field_and_well splits it) picks the cluster center; a
    BLAKE2b digest of the full name picks the offset inside it. hashlib, not
    the builtin ``hash``, because PYTHONHASHSEED randomizes the latter per
    process -- the same seed run twice would then place the same well in two
    places. An unknown code (a hand-added field) gets no coordinates rather
    than an invented location.
    """
    code = name.split("-")[0]
    center = LEAD_CLUSTER_CENTERS.get(code)
    if not center:
        return None, None
    digest = hashlib.blake2b(name.encode("utf-8"), digest_size=8).digest()
    # Two independent 24-bit slices -> two offsets in [-jitter, +jitter].
    span = 2 * LEAD_CLUSTER_JITTER_M
    dx = (int.from_bytes(digest[0:3], "big") / 0xFFFFFF) * span - LEAD_CLUSTER_JITTER_M
    dy = (int.from_bytes(digest[3:6], "big") / 0xFFFFFF) * span - LEAD_CLUSTER_JITTER_M
    return round(center[0] + dx, 1), round(center[1] + dy, 1)


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

# Steps like "SAD Update" refuse a submit until their sign-off boxes are checked
# (workflow.lifecycle._check_submit_requirements). Seeded wells drive those
# steps to Approved, so the seeder records the sign-off first, through the same
# shared helper the domain layer's own automated walks use -- the audit trail
# then matches what a real user's tick would leave behind. Not inlined into
# _complete_task because _advance_to needs it for the Ready anchor too.
_satisfy_submit_gate = workflow.satisfy_submit_gate


def _complete_task(session, task, assignee, role_by_name, approver, cycle=False):
    """Drive one task to Approved: assign -> submit -> approve.

    When ``cycle`` is set, insert a submit -> return -> submit round-trip
    first, so the Audit Trail carries a realistic back-and-forth.
    """
    role = role_by_name.get(assignee)
    _satisfy_submit_gate(session, task, assignee)
    task = workflow.assign_task(session, task["task_id"], assignee, cascade=False, changed_by=assignee)
    task = workflow.transition_task(session, task["task_id"], "submit", changed_by=assignee,
                                     actor_role=role, actor_name=assignee)
    if cycle:
        task = workflow.transition_task(session, task["task_id"], "return", changed_by=approver,
                                        actor_role="supervisor", actor_name=approver)
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
    _satisfy_submit_gate(session, task, assignee)
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
    priority variety seeded above.

    The stored dynamic fields are resent too, mirroring the real UI (which
    always submits the whole form): save_task on a "Trap and Seal CoS" task recomputes
    seal_cos_pct from the PAYLOAD's fields, so a fields-less save would trip
    calculate_seal_cos's blank-form guard and wipe the stored percentage.
    ``reservoir_cos_rows`` is the one exception: resending it would make
    save_task re-invoke the RF model, which is deployed out-of-band and absent
    in dev (see module docstring), so it is left out of the payload -- the
    stored rows survive untouched because save_task only recalculates when
    the key is present."""
    fresh = workflow.get_task(session, task["task_id"])
    fields = workflow.get_task_dynamic_fields(session, task["task_id"])
    fields.pop("reservoir_cos_rows", None)
    workflow.save_task(session, task["task_id"],
                       {"comments": random.choice(COMMENT_SAMPLES),
                        "priority": fresh.get("priority") or "Medium",
                        "fields": fields},
                       changed_by=changed_by)


# ---------------------------------------------------------------------------
# BP well field builders (feed reporting._BP_TASK_FIELD_KEYS / the Portfolio)
# ---------------------------------------------------------------------------

def _piip_fields(prefix, include_liquid=False):
    """One PIIP mean/P90/P10 gas trio (BCF), P90 low - Mean - P10 high. When
    ``include_liquid``, also the condensate trio (MMSTB) behind the schema's
    ``<prefix>_has_liquid`` checkbox, so the export's Condensate columns get
    non-zero data on some records."""
    mean = round(random.uniform(2.0, 20.0), 2)
    fields = {
        f"{prefix}_gas_p90": round(mean * random.uniform(0.5, 0.75), 2),
        f"{prefix}_gas_mean": mean,
        f"{prefix}_gas_p10": round(mean * random.uniform(1.3, 1.8), 2),
    }
    if include_liquid:
        liquid_mean = round(random.uniform(0.5, 8.0), 2)
        fields[f"{prefix}_has_liquid"] = "1"
        fields[f"{prefix}_liquid_p90"] = round(liquid_mean * random.uniform(0.5, 0.75), 2)
        fields[f"{prefix}_liquid_mean"] = liquid_mean
        fields[f"{prefix}_liquid_p10"] = round(liquid_mean * random.uniform(1.3, 1.8), 2)
    return fields


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
        # Deterministically pins the FIRST row -- the primary row per
        # workflow.first_reservoir_cos_row_value (WS2 flipped the primary
        # from last to first) -- to the FIRST block's FIRST AR in
        # config.SEISMIC_BLOCK_AR_MAP, demonstrating the Portfolio's
        # Block/AR mapping without editing config.py or seismic_blocks.json.
        block_map = config.SEISMIC_BLOCK_AR_MAP
        if block_map:
            first_block = next(iter(block_map))
            first_ars = block_map.get(first_block) or []
            rows[0]["seismic_block"] = first_block
            rows[0]["seismic_volume_ar_number"] = first_ars[0] if first_ars else ""
        else:
            rows[0]["seismic_block"] = ""
            rows[0]["seismic_volume_ar_number"] = _ar_number(1)
    return json.dumps(rows, separators=(",", ":"))


def _seal_fields():
    """Raw Seal CoS inputs; save_task_dynamic_fields computes seal_cos_pct
    from these via the real formula (cos.calculate_seal_cos).

    The activity range is capped at 1.0 (it was 0.1-1.4 until KI-004): above
    0.9 the formula takes the ``activity x fracture_permeability`` branch and
    range-checks nothing, so a draw over 1/0.9 -- the largest permeability
    below -- produced a stored seal_cos_pct above 100%. That value is outside
    the domain every READER accepts, and roughly one seeded lead per run was
    therefore born with a detail page that could not be opened. 1.0 x the 0.9
    permeability ceiling is 90%, comfortably inside the domain, and still
    leaves ~11% of draws exercising the "recently active" branch.
    """
    return {
        "seal_recent_activity_age": round(random.uniform(0.1, 1.0), 2),
        "seal_dip": round(random.uniform(0.1, 0.9), 2),
        "seal_azimuth_vs_shmax": round(random.uniform(0.1, 0.9), 2),
        "seal_fault_level_confidence": round(random.uniform(0.1, 0.9), 2),
        "seal_fracture_permeability": round(random.uniform(0.1, 0.9), 2),
    }


def _staking_fields():
    """Coherent 'Moving Tolerance' inputs: a well location plus the
    3 distance/azimuth option pairs (schema.js's staking_opt1/2/3 rows) --
    used to seed fully-mature leads for the Staking Options export sheet."""
    fields = {
        "staking_well_x": round(random.uniform(300000, 700000), 1),
        "staking_well_y": round(random.uniform(2400000, 2900000), 1),
    }
    for opt in (1, 2, 3):
        fields[f"staking_opt{opt}_max_distance_m"] = round(random.uniform(50, 500), 1)
        fields[f"staking_opt{opt}_azimuth_deg"] = round(random.uniform(0, 359), 1)
    return fields


def _flowback_fields(legacy=False):
    """Full Flowback Results field set. New-style wells carry 1-3 stage rows in
    the flowback_stages_rows JSON mini-sheet, each row carrying its own
    per-stage Formation column (SARH, the schema default); stage #1 is the primary read
    everywhere); ``legacy=True`` writes the per-stage measurements ONLY
    through the retired step-level flat keys instead -- like a well written
    before the stages sheet existed -- exercising the readers' flat-key
    fallback (detail.js flowback rate, portfolio_export flowback columns).
    Every rate field is filled regardless of fluid type so a fluid-type
    change in the UI never reveals a blank (incl. flowback_liquid_rate_bpd,
    the BPD path for Condensate/Liquid fluids)."""
    def _stage():
        return {
            "flowback_formation": "SARH",
            "flowback_gas_rate_mmscfd": round(random.uniform(1, 15), 2),
            "flowback_water_rate_bwpd": round(random.uniform(50, 800), 1),
            "flowback_liquid_rate_bpd": round(random.uniform(100, 2500), 1),
            "flowback_choke_size_in": round(random.uniform(0.25, 1.5), 3),
            "flowback_fwhp_psi": round(random.uniform(500, 4500), 1),
        }
    fields = {
        "flowback_dynamic_area_km2": round(random.uniform(1, 20), 2),
        "flowback_dynamic_ogip_bcf": round(random.uniform(5, 60), 2),
    }
    if legacy:
        fields.update(_stage())
    else:
        fields["flowback_stages_rows"] = json.dumps(
            [_stage() for _ in range(random.randint(1, 3))])
    return fields


def _formation_row(formation, fluid=None):
    """One project_formations row; numeric fields are real numbers (not
    strings) as upsert_project_formations expects for a clean coercion.

    ``fluid`` overrides the random per-formation fluid: pass the WELL's fluid
    for SARH rows (the well inherits SARH's fluid through
    reporting.resolve_well_fluid) or "" to leave the row fluid-less (the
    legacy-fallback well)."""
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
        "fluid": random.choice(DRILLED_FLUIDS) if fluid is None else fluid,
    }


def _phase_formation_rows(names, sarh_fluid):
    """Formation rows for one phase upsert: SARH carries ``sarh_fluid`` (the
    well-level fluid the resolve_well_fluid ladder inherits, or "" for none);
    the other formations keep a random per-formation fluid."""
    return [_formation_row(f, fluid=(sarh_fluid if f == "SARH" else None)) for f in names]


def _prospect_step_fields(task_name, force_ar_one=False, force_pore_pressure=False):
    """Coherent dynamic-field payload for one data-entry prospect step, or
    None for the steps that carry no inputs (Seismic Signature Validation,
    Segmentation Slides, Approval to Stake, Well Site Location).

    The SINGLE source of prospect-step seed data, shared by the proposed-lead,
    mature-lead and BP-well seeders, so a step that has been progressed
    through always carries the fields a real user would have filled:
    - Lead Assessment: P90/P10 areas, formation/reservoir thickness, lead PIIP
      gas trio (occasionally + the liquid trio).
    - Risk Analysis: pre-scored reservoir_cos_rows (coherent Block/AR pairs) and
      the merged Trap and Seal CoS form -- the Trap inputs PLUS the 5 Seal CoS
      inputs in one payload (occasionally + pore pressure; forced via
      ``force_pore_pressure`` for fully-drilled BP wells).
    - Pre-Well Delivery: pre-drill PIIP gas trio, staking location + the 3
      distance/azimuth option pairs.
    """
    if task_name == "Lead Assessment":
        p90 = round(random.uniform(2, 25), 2)
        reservoir_thickness = round(random.uniform(30, 150), 1)
        grv_p90 = round(random.uniform(20, 300), 2)
        fields = {
            "p90_area_km2": p90,
            "p10_area_km2": round(p90 * random.uniform(1.5, 3.5), 2),
            "reservoir_thickness_ft": reservoir_thickness,
            "formation_thickness_ft": round(reservoir_thickness * random.uniform(1.2, 2.5), 1),
            "grv_p90_thousand_acre_ft": grv_p90,
            "grv_p10_thousand_acre_ft": round(grv_p90 * random.uniform(1.5, 3.5), 2),
            "polygons_surfaces_loaded": "1",
        }
        fields.update(_piip_fields("lead_piip", include_liquid=random.random() < 0.35))
        return fields
    if task_name == "Reservoir CoS":
        return {"reservoir_cos_rows": _reservoir_cos_rows(force_ar_one=force_ar_one)}
    if task_name == workflow.MERGED_COS_TASK_NAME:
        # One save carrying both halves. The explicit trap_cos_pct is KEPT as
        # sent (the client is the primary calculator now; the server hook
        # stands down for a payload that carries the pct); seal_cos_pct is
        # absent from the payload, so the server still recomputes it from the
        # 5 inputs.
        fields = {"sarah_quwarah_thickness_ft": round(random.uniform(60, 400), 1),
                  "trap_cos_pct": str(random.randint(20, 90))}
        fields.update(_seal_fields())
        if force_pore_pressure or random.random() < 0.5:
            fields["seal_pore_pressure_gradient_psi_ft"] = round(random.uniform(0.35, 0.75), 3)
        return fields
    if task_name == "Pre-Drilling GeoX Assessment":
        return _piip_fields("pre_drill_piip", include_liquid=random.random() < 0.35)
    if task_name == "Moving Tolerance":
        return _staking_fields()
    return None


def _fill_prospect_step_data(session, tasks, force_ar_one=False, force_pore_pressure=False):
    """Save coherent field data for every prospect-stage task in ``tasks``
    (callers pass only the progressed prefix -- approved or in-progress -- so
    an untouched step stays realistically blank)."""
    for task in tasks:
        fields = _prospect_step_fields(task["task_name"], force_ar_one=force_ar_one,
                                       force_pore_pressure=force_pore_pressure)
        if fields:
            # reconcile=False: bulk writer -- ensure_task_approved drives status.
            workflow.save_task_dynamic_fields(session, task["task_id"], fields,
                                              changed_by="Seed Script", reconcile=False)


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------

def _seed_prospect_leads(session, users, role_by_name, supervisors):
    """12 prospect leads, 4 spread across each of the 3 PROSPECT_STAGES, plus
    3 fully-mature leads (every prospect-phase task Approved, incl. 'Approval
    to Stake').

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
        lead_x, lead_y = _lead_coordinates(name)
        pid = workflow.add_project(session, name, changed_by=random.choice(users),
                                   lead_x=lead_x, lead_y=lead_y)
        tasks = [t for t in workflow.get_project_tasks(session, pid)
                if t["stage_group"] in workflow.PROSPECT_STAGES]

        start, count = windows[stage]
        approve_count = start + random.randint(0, count - 1)
        anchor_status = random.choice(["Not Assigned", "In Progress", "Ready"])
        _seed_pipeline_progress(session, tasks, approve_count, anchor_status, users, role_by_name, supervisors)
        # A real approved (or in-progress) step has its inputs filled: seed
        # coherent data for exactly the prefix of steps this lead has
        # progressed through, so an exported proposed lead carries the data
        # its stage implies -- while untouched steps stay realistically blank.
        progressed = approve_count + (0 if anchor_status == "Not Assigned" else 1)
        _fill_prospect_step_data(session, tasks[:progressed])
        _sprinkle_priorities(session, tasks, random.choice(users))
        project_ids.append(pid)

    # 2-3 fully-mature leads: every prospect-phase task (incl. 'Approval to
    # Stake') driven to Approved via the SAME _complete_task path used above,
    # so they exit the Prospect board (workflow.get_projects's
    # pipeline_filter=='prospect' branch drops overall_status=='Completed'
    # rows) and surface in the Portfolio as is_mature_lead=1 rows
    # (reporting._portfolio_projects). No prospect-stage task carries a fluid
    # value, so reporting.record_status falls through to its 'Staked' branch
    # (Approval to Stake is Approved) for every one of them -- that is the
    # expected, correct behavior, not a bug.
    for i in range(3):
        code = PROSPECT_FIELD_CODES[(len(stage_targets) + i) % len(PROSPECT_FIELD_CODES)]
        name = _unique_name(session, code)
        lead_x, lead_y = _lead_coordinates(name)
        pid = workflow.add_project(session, name, changed_by=random.choice(users),
                                   lead_x=lead_x, lead_y=lead_y)
        tasks = [t for t in workflow.get_project_tasks(session, pid)
                if t["stage_group"] in workflow.PROSPECT_STAGES]
        _seed_pipeline_progress(session, tasks, len(tasks), "Not Assigned", users, role_by_name, supervisors)
        # Every prospect step is Approved, so every data-entry step gets its
        # coherent inputs (areas/thickness/OGIP, all three CoS, staking).
        _fill_prospect_step_data(session, tasks, force_ar_one=(i == 0))
        _sprinkle_priorities(session, tasks, random.choice(users))
        project_ids.append(pid)
    return project_ids


def _seed_bp_wells(session, users, role_by_name, supervisors):
    """8-10 BP wells at varied maturity, with the Portfolio-composing task
    fields filled in via save_task_dynamic_fields (independent of each task's
    lifecycle status, exactly like a real user entering data mid-workflow).

    Each well is born a prospect lead, gets its prospect-stage inputs saved,
    and is THEN promoted through the real domain flow
    (workflow.update_project_flags -> set_business_plan ->
    _capture_lead_summary_snapshot) -- never created as BP directly. The order
    matters: the promotion snapshot freezes the prospect-stage fields into
    lead_summary_snapshots, which feeds /detail's ``lead_summary`` and the
    Well card's Prediction-vs-Actual rows (snapshot Lead Assessment /
    Pre-Drilling RA values vs the live post-drill actuals). BP-stage data is
    saved only AFTER promotion, matching real usage.
    """
    years = [2026, 2027, 2028, 2029, 2030, 2031, 2032, 2026, 2029]
    project_ids = []
    for i, code in enumerate(BP_FIELD_CODES):
        name = _unique_name(session, code)
        lead_x, lead_y = _lead_coordinates(name)
        pid = workflow.add_project(session, name, changed_by=random.choice(users),
                                   lead_x=lead_x, lead_y=lead_y)
        project_ids.append(pid)

        tasks = workflow.get_project_tasks(session, pid)
        bp_tasks = [t for t in tasks if t["stage_group"] in workflow.BP_EXECUTION_STAGES]
        by_name = {t["task_name"]: t for t in tasks}

        # maturity: 0 = just entered Well Delivery, 1 = mid Post-Drilling,
        # 2 = fully drilled and tested (gets formation data + final logs).
        maturity = i % 3

        # PROSPECT-stage inputs FIRST, so the promotion snapshot below
        # captures them: every data-entry prospect step (areas, thickness,
        # lead/pre-drill assessments, the three CoS steps, staking) is filled
        # via the same shared per-step generator the lead seeders use.
        # Fully-drilled wells (maturity 2) always get the seal pore-pressure
        # gradient so the export column has guaranteed data.
        prospect_tasks = [t for t in tasks if t["stage_group"] in workflow.PROSPECT_STAGES]
        _fill_prospect_step_data(session, prospect_tasks, force_ar_one=(i == 0),
                                 force_pore_pressure=(maturity == 2))

        # Promote through the real domain flow: this fires
        # _capture_lead_summary_snapshot, so lead_summary is populated exactly
        # like a genuinely promoted lead's.
        workflow.update_project_flags(
            session, pid, business_plan_enabled=True, active_well_enabled=(i % 2 == 0),
            business_plan_year=years[i % len(years)], changed_by=random.choice(supervisors))

        # BP-stage lifecycle progress + inputs, AFTER promotion.
        # Windows over the 15 BP-execution steps (v4 merged four away):
        # 0 = early Well Delivery, 1 = mid pipeline, 2 = fully drilled.
        approve_count = {
            0: random.randint(0, 3),
            1: random.randint(4, 9),
            2: random.randint(10, 15),
        }[maturity]
        anchor_status = random.choice(["Not Assigned", "In Progress", "Ready"])
        _seed_pipeline_progress(session, bp_tasks, approve_count, anchor_status, users, role_by_name, supervisors)
        _sprinkle_priorities(session, tasks, random.choice(users))

        # reconcile=False: bulk writer -- ensure_task_approved drives status.
        workflow.save_task_dynamic_fields(session, by_name["GHEER"]["task_id"],
                                          {"gheer_classification": random.choice(GHEER_CLASSIFICATIONS)},
                                          changed_by="Seed Script", reconcile=False)
        # WS7: half the wells ALSO get the new BP-gate classification key, so
        # reporting._first_filled(bp_gate, gheer) picks it there; the other
        # half keep ONLY the legacy GHEER key above, exercising the
        # read-fallback (constants.py's _OVERVIEW_READ_SOURCES["classification"]).
        if i % 2 == 0:
            # reconcile=False: bulk writer -- ensure_task_approved drives status.
            workflow.save_task_dynamic_fields(session, by_name["BP Execution Gate"]["task_id"],
                                              {"bp_gate_classification": random.choice(GHEER_CLASSIFICATIONS)},
                                              changed_by="Seed Script", reconcile=False)

        # The well's fluid, inherited from its SARH formation rows through
        # reporting.resolve_well_fluid (the step-level Quicklook / Final Log
        # Analysis fluid selects are gone). i == 2 is pinned to Condensate so
        # at least one well always exercises the flowback_liquid_rate_bpd /
        # BPD unit path (schema.js's FLOWBACK_RATE_FIELDS) in the summary
        # card. i == 5 (drilled, maturity 2) is the ONE legacy-fallback well:
        # its fluid/tops are seeded ONLY through the retired step-level EAV
        # keys -- which nothing writes anymore but resolve_well_fluid still
        # reads as fallback rungs -- with NO fluid on its SARH formation rows
        # and the kept post_drill/resource_update fluid selects left blank,
        # so the ladder must fall all the way through to the legacy keys,
        # exercising that path end-to-end like a well written before the
        # multi-formation editor existed.
        fluid = "Condensate" if i == 2 else random.choice(DRILLED_FLUIDS)
        legacy_fluid_well = (i == 5)
        sarh_fluid = "" if legacy_fluid_well else fluid

        if maturity >= 1:
            # Quicklook interpretation now writes per-formation rows (the
            # step-level quicklook_fluid_type key was removed); the well
            # inherits SARH's fluid.
            workflow.upsert_project_formations(
                session, pid, "quicklook", _phase_formation_rows(workflow.FORMATIONS, sarh_fluid),
                changed_by="Seed Script", source_task_id=by_name["Quicklook Logs"]["task_id"])

        if maturity == 2:
            post_drill_fields = _piip_fields("post_drill_piip")
            resource_update_fields = _piip_fields("resource_update")
            if legacy_fluid_well:
                # Legacy EAV fluid + step-level tops on Quicklook / Final Log
                # Analysis only (rungs 2 and 6 of the ladder); see the comment
                # above for why this well seeds nothing else fluid-wise.
                top = round(random.uniform(8500, 12000), 1)
                # reconcile=False: bulk writer -- ensure_task_approved drives status.
                workflow.save_task_dynamic_fields(
                    session, by_name["Quicklook Logs"]["task_id"],
                    {"quicklook_fluid_type": fluid,
                     "quicklook_top_reservoir_tvdss_ft": top,
                     "quicklook_base_reservoir_tvdss_ft": round(top + random.uniform(30, 150), 1)},
                    changed_by="Seed Script", reconcile=False)
                top = round(random.uniform(8500, 12000), 1)
                # reconcile=False: bulk writer -- ensure_task_approved drives status.
                workflow.save_task_dynamic_fields(
                    session, by_name["Final Log Analysis"]["task_id"],
                    {"final_fluid_type": fluid,
                     "final_top_reservoir_tvdss_ft": top,
                     "final_base_reservoir_tvdss_ft": round(top + random.uniform(30, 150), 1)},
                    changed_by="Seed Script", reconcile=False)
            else:
                # SAD Model / SAD Update kept the merged-away steps' fluid
                # selects (post_drill_fluid_type / resource_update_fluid_type);
                # one fluid value flows through everything for coherence.
                post_drill_fields["post_drill_fluid_type"] = fluid
                resource_update_fields["resource_update_fluid_type"] = fluid
            # v4: the post-drill / resource-update PIIP trios now live on the
            # steps that absorbed them, under their ORIGINAL EAV keys.
            post_drill_fields["sad_surfaces_polygons_loaded"] = "1"
            resource_update_fields["sad_update_done"] = "1"
            resource_update_fields["final_exec_summary_done"] = "1"
            # reconcile=False: bulk writer -- ensure_task_approved drives status.
            workflow.save_task_dynamic_fields(session, by_name["SAD Model"]["task_id"],
                                              post_drill_fields, changed_by="Seed Script", reconcile=False)
            # reconcile=False: bulk writer -- ensure_task_approved drives status.
            workflow.save_task_dynamic_fields(session, by_name["SAD Update"]["task_id"],
                                              resource_update_fields, changed_by="Seed Script", reconcile=False)
            # reconcile=False: bulk writer -- ensure_task_approved drives status.
            workflow.save_task_dynamic_fields(session, by_name["Executive Summary"]["task_id"],
                                              {"exec_summary_loaded": "1", "ured_update_loaded": "1"},
                                              changed_by="Seed Script", reconcile=False)
            # The legacy-fallback well also keeps its flowback data in the
            # retired flat keys (no stages sheet), so the flat-key fallback
            # is exercised end-to-end alongside the legacy fluid ladder.
            # reconcile=False: bulk writer -- ensure_task_approved drives status.
            workflow.save_task_dynamic_fields(session, by_name["Flowback Results"]["task_id"],
                                              _flowback_fields(legacy=legacy_fluid_well),
                                              changed_by="Seed Script", reconcile=False)
            # pda_booked on SOME (not all) maturity-2 wells, so the Portfolio
            # Export sheet's Booked column shows both 'Yes' and the blank/'No'
            # case.
            if (i // 3) % 2 == 0:
                # reconcile=False: bulk writer -- ensure_task_approved drives status.
                workflow.save_task_dynamic_fields(session, by_name["PDA"]["task_id"],
                                                  {"pda_booked": "1"}, changed_by="Seed Script", reconcile=False)

            # Drilled wells get formation interpretation rows across the
            # remaining FORMATION_PHASES (quicklook was written above), SARH
            # carrying the well fluid at every phase it has rows for; the
            # first maturity-2 well also gets a custom, non-canonical
            # formation name (the 'Other...' free-text path) alongside the
            # SARH/QASM/QWRH trio.
            custom = ["UNAYZAH"] if i == 2 else []
            workflow.upsert_project_formations(
                session, pid, "final", _phase_formation_rows(workflow.FORMATIONS + custom, sarh_fluid),
                changed_by="Seed Script", source_task_id=by_name["Final Log Analysis"]["task_id"])
            workflow.upsert_project_formations(
                session, pid, "post_drill", _phase_formation_rows(workflow.FORMATIONS, sarh_fluid),
                changed_by="Seed Script", source_task_id=by_name["SAD Model"]["task_id"])
            workflow.upsert_project_formations(
                session, pid, "resource_update", _phase_formation_rows(workflow.FORMATIONS + custom, sarh_fluid),
                changed_by="Seed Script", source_task_id=by_name["SAD Update"]["task_id"])
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
