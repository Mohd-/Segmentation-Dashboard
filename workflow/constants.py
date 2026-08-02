"""Workflow domain vocabulary: statuses, stages, the pipeline definition.

The bottom of the package's dependency graph -- nothing here imports another
workflow module (or the database), so every other module can import from it
freely. Also home to ``StaleRevisionError`` (the optimistic-lock conflict
signal main.py maps to HTTP 409) and the shared read-mapping tables.
"""
from __future__ import annotations


class StaleRevisionError(RuntimeError):
    """Raised when an optimistic-lock revision check fails on a task save.

    The caller supplied a ``revision`` that no longer matches the stored row
    (someone else saved first). main.py maps this to HTTP 409 with the message
    intact; it is the ONLY exception type that becomes a 409. Other
    RuntimeErrors are treated as internal errors (500).
    """


# ---------------------------------------------------------------------------
# Domain constants
# ---------------------------------------------------------------------------

# The 4-state implicit lifecycle (v17): Not Assigned -> In Progress (via
# assignment) -> Ready (submit) -> Approved (supervisor). There is no stored
# "not applicable" state: applicability is a pure function of the pipeline (see
# applicable_stages), so every task row lives in one of these four states.
STATUSES = [
    "Not Assigned",
    "In Progress",
    "Ready",
    "Approved",
]

DONE_STATUSES = {"Approved"}
ACTIVE_STATUSES = {"In Progress", "Ready"}

# v5 made the three board columns the STORED stage groups: the prospect side
# used to store four groups (Lead Identification / Risking / Segmentation /
# Pre-Well Delivery) that a read-time adapter folded into the three the users
# signed off on. The adapter is gone -- these ARE the three, and
# migrations._migrate_v5_prospect_template_restructure remapped every existing
# prospect row onto them. BP groups are untouched.
STAGE_ORDER = [
    "Lead Assessment",
    "Risk Analysis",
    "Pre-Well Delivery",
    "Well Delivery",
    "Post-Drilling",
    "Post-Testing",
]

PROSPECT_STAGES = ["Lead Assessment", "Risk Analysis", "Pre-Well Delivery"]
BP_EXECUTION_STAGES = ["Well Delivery", "Post-Drilling", "Post-Testing"]
BOARD_STAGE_ORDER = STAGE_ORDER[:]

# The pre-v5 prospect stage groups -> their v5 replacements. Nothing in the
# runtime reads a legacy group any more (v5 rewrote every row), but the mapping
# stays named here as the documented history and is what folders.py widens its
# prospect-share test with, so a row the migration could not reach still files
# its component folder under the Leads share instead of the Wells one.
LEGACY_PROSPECT_STAGE_GROUPS = {
    "Lead Identification": "Lead Assessment",
    "Risking": "Risk Analysis",
    "Segmentation": "Risk Analysis",
}


def applicable_stages(pipeline_type):
    """Return the stage groups that make up a pipeline's operating scope.

    Applicability is a PURE FUNCTION of pipeline_type -- never stored per row: a
    prospect operates over PROSPECT_STAGES, a BP well over BP_EXECUTION_STAGES.
    All 27 active task rows always exist regardless of pipeline; the rows
    outside the operating pipeline are simply excluded wherever it matters
    (completion, flow reconciliation, assignment cascade, state refresh) by
    filtering on ``stage_group IN applicable_stages(...)``.
    """
    return BP_EXECUTION_STAGES if str(pipeline_type or "prospect").lower() == "bp" else PROSPECT_STAGES

# Well-level formation interpretation (project_formations). SARH/QASM/QWRH
# are the canonical trio (always seeded/offered), but users may also add
# custom formation names (normalized strip().upper(), enforced in
# workflow.formations.upsert_project_formations, not a DB constraint). Rows
# are keyed by (project, formation, phase); ``phase`` says which
# interpretation step the values came from.
FORMATIONS = ["SARH", "QASM", "QWRH"]
FORMATION_PHASES = ["quicklook", "post_drill", "final", "resource_update"]
FORMATION_VALUE_FIELDS = [
    "top_tvdss_ft", "base_tvdss_ft", "thickness_ft", "porosity_pct",
    "swt_pct", "pay_ft", "ngr_pct", "fluid",
]
# All value fields except 'fluid' are REAL columns (project_formations); 'fluid'
# is a free-text description and stays TEXT.
FORMATION_NUMERIC_FIELDS = [f for f in FORMATION_VALUE_FIELDS if f != "fluid"]

# Per-formation PAY INTERVALS (project_formation_pay_intervals): a formation
# keeps its envelope (top/base/thickness) and carries zero or more pay
# intervals, each with its own top/base plus petrophysical averages. Rows are
# keyed by (project, formation, phase, seq); ``seq`` is the interval's 1-based
# position in the editor's list, assigned from payload order.
PAY_INTERVAL_VALUE_FIELDS = [
    "top_tvdss_ft", "base_tvdss_ft", "phit_pct", "swt_pct", "ngr_pct",
    "kint_md", "fluid",
]
PAY_INTERVAL_NUMERIC_FIELDS = [f for f in PAY_INTERVAL_VALUE_FIELDS if f != "fluid"]
# The fluid vocabulary offered by the formation/pay-interval editors. Mirrors
# FLUID_TYPES in static/js/schema.js -- keep the two lists in sync. Pay-interval
# fluids are validated against it (case-insensitively, normalized back to the
# canonical spelling); the formation envelope's own ``fluid`` stays free text so
# legacy/imported descriptions keep round-tripping unchanged.
FORMATION_FLUID_TYPES = ["", "Dry", "Gas", "Water", "Condensate", "Liquid", "Gas over Water"]

# The 27-step pipeline definition: (sequence_no, task_name, stage_group).
# This list is the SINGLE SOURCE OF TRUTH for the workflow -- there is no
# task_templates table; project creation materializes project_tasks rows
# straight from these tuples.
#
# Pre-deployment, editing this list only affects NEW projects (existing dev
# databases are throwaway -- delete the .db and restart; see migrations.py).
# POST-deployment, changing it requires a numbered data migration for existing
# project_tasks rows: resequencing by task_name, and deactivating retired
# steps (is_active = 0) so their inputs and audit trail survive. That is
# exactly what migrations._migrate_v4_bp_step_merges does for the v4 merges
# and migrations._migrate_v5_prospect_template_restructure for the v5
# prospect restructure below.
#
# v4 merged four BP steps away (31 -> 27 steps: 12 prospect + 15 BP):
#   "URED Update"                       -> folded into "Executive Summary"
#   "Post-Drilling Resource Assessment" -> folded into "SAD Model"
#   "Resource Assessment Update"        -> folded into "SAD Update"
#   "Executive Summary Final"           -> folded into "SAD Update"
# The merged steps keep the RETIRED steps' EAV keys (post_drill_piip_* on SAD
# Model, resource_update_* on SAD Update) so no stored value is orphaned; the
# retired rows survive as is_active = 0 and every EAV reader keeps reading
# them (see RETIRED_TASK_NAMES below).
#
# v5 restructured the PROSPECT half into the permanent 12 tracked items the
# board and the detail sidebar had been faking through a read-time adapter.
# Still 12 prospect steps (so still 27 in total, and the BP numbers 13-27 did
# not move), but they are now the tracked items themselves:
#   renamed  "Reservoir Area Definition"        -> "Area Definition"
#            "Lead Resource Assessment"         -> "Resource Assessment"
#            "Prospect Evaluation Presentation" -> "Segmentation Slides"
#            "Staking Moving Tolerance"         -> "Moving Tolerance"
#            "Pre-Drilling Resource Assessment" -> "Pre-Drilling GeoX Assessment"
#   merged   "Trap CoS" + "Seal CoS"            -> "Trap and Seal CoS"
#   retired  "Well Creation"  (its sign-off became the staking_well_created
#            checkbox on "Approval to Stake")
#   added    "GRV Inputs", "Well Site Location"
# The rename is a task_name rewrite IN PLACE, so a renamed row keeps its
# task_id, EAV, history and folder card; the merge and the retirement follow
# the v4 pattern (is_active = 0, nothing deleted, same EAV keys).
PIPELINE_TEMPLATES = [
    (1, "Area Definition", "Lead Assessment"),
    (2, "Thickness Estimation", "Lead Assessment"),
    (3, "GRV Inputs", "Lead Assessment"),
    (4, "Resource Assessment", "Lead Assessment"),
    (5, "Reservoir CoS", "Risk Analysis"),
    # v5: "Trap CoS" + "Seal CoS" merged here. It keeps BOTH steps' EAV keys
    # verbatim (sarah_quwarah_thickness_ft / trap_cos_pct and the five seal_*
    # inputs / seal_cos_pct), which is what lets the recompute hooks and the
    # Total CoS read carry straight over.
    (6, "Trap and Seal CoS", "Risk Analysis"),
    (7, "Seismic Signature Validation", "Risk Analysis"),
    # v18: "Presence CoS Evaluation" was removed as a visible step -- its value
    # is derived (Reservoir x Trap x Seal), computed at read time
    # (calculate_total_cos) and surfaced as ``derisking`` in the /detail
    # payload's computed overview.
    (8, "Segmentation Slides", "Risk Analysis"),
    (9, "Moving Tolerance", "Pre-Well Delivery"),
    (10, "Approval to Stake", "Pre-Well Delivery"),
    (11, "Well Site Location", "Pre-Well Delivery"),
    (12, "Pre-Drilling GeoX Assessment", "Pre-Well Delivery"),
    (13, "BP Execution Gate", "Well Delivery"),
    (14, "Well Proposal", "Well Delivery"),
    (15, "Site Preparation", "Well Delivery"),
    (16, "Approval To Drill", "Well Delivery"),
    (17, "GHEER", "Well Delivery"),
    # Renamed in v3 (the old name carried a trailing "Interpretation");
    # existing project_tasks rows are carried over by
    # migrations._migrate_v3_rename_quicklook_logs.
    (18, "Quicklook Logs", "Post-Drilling"),
    (19, "Aramco Picks", "Post-Drilling"),
    # v4: absorbed "Post-Drilling Resource Assessment" (old step 20) -- keeps
    # its post_drill_piip_* / post_drill_fluid_type keys.
    (20, "SAD Model", "Post-Drilling"),
    # v4: absorbed "URED Update" (old step 23) as a checkbox.
    (21, "Executive Summary", "Post-Drilling"),
    (22, "Post-Well Outcome & Decision Gate", "Post-Drilling"),
    (23, "Flowback Results", "Post-Testing"),
    # v4: absorbed "Resource Assessment Update" (old step 30, keeps its
    # resource_update_* keys) and "Executive Summary Final" (old step 27, now a
    # required submit checkbox).
    (24, "SAD Update", "Post-Testing"),
    (25, "Final Log Analysis", "Post-Testing"),
    (26, "PVAD Structural MTR", "Post-Testing"),
    (27, "PDA", "Post-Testing"),
]

# The steps merged/retired away by v4 and v5. They are never materialized for a
# NEW project; EXISTING project_tasks rows carrying these names survive as
# is_active = 0 (rows, EAV data and history all intact), which is why every EAV
# reader is retired-inclusive -- see
# workflow.summary.get_project_dynamic_field_map, reporting._bp_task_fields and
# portfolio_export._task_fields.
RETIRED_TASK_NAMES = (
    # v4 (BP)
    "URED Update",
    "Post-Drilling Resource Assessment",
    "Resource Assessment Update",
    "Executive Summary Final",
    # v5 (prospect): both CoS halves folded into "Trap and Seal CoS"; the
    # Well Creation sign-off became a checkbox on "Approval to Stake".
    "Trap CoS",
    "Seal CoS",
    "Well Creation",
)

# v5 renames: CURRENT name -> the pre-v5 name the same row used to carry.
# Renames rewrite ``project_tasks.task_name`` in place, so after the migration
# the rows THEMSELVES answer to the new name and no runtime read strictly needs
# the old one. The map exists because two places still meet the old spelling:
#   - ``lead_summary_snapshots`` froze its {task_name: {key: value}} JSON at
#     promotion time and is never rewritten (it is a historical record), so the
#     client's snapshot-merged field map still holds old-name buckets;
#   - the rename skips a project that somehow carries BOTH names (UNIQUE
#     (project_id, task_name)), exactly like the v3 quicklook guard, leaving a
#     stale old-name row behind for manual reconciliation.
# Both are read-side concerns only -- see the fallback chains below.
RENAMED_TASK_NAMES = {
    "Area Definition": "Reservoir Area Definition",
    "Resource Assessment": "Lead Resource Assessment",
    "Segmentation Slides": "Prospect Evaluation Presentation",
    "Moving Tolerance": "Staking Moving Tolerance",
    "Pre-Drilling GeoX Assessment": "Pre-Drilling Resource Assessment",
}

# The v5 CoS merge, named once for every reader that has to address it.
MERGED_COS_TASK_NAME = "Trap and Seal CoS"
MERGED_COS_LEGACY_NAMES = ("Trap CoS", "Seal CoS")

# The Well Creation retirement's replacement: a checkbox on "Approval to Stake"
# recording that the well record exists. v5 backfills it (= '1') for every
# project whose Well Creation step had been Approved.
STAKING_WELL_CREATED_KEY = "staking_well_created"


# ---------------------------------------------------------------------------
# Non-prospective auto-completion (the "BP pipeline" rule)
# ---------------------------------------------------------------------------
# When the Quicklook Logs interpretation proves a well NON-PROSPECTIVE -- a
# single formation row recorded at the 'quicklook' phase whose fluid is Water
# or Dry -- the remaining BP paperwork steps are formalities. They are then
# driven to Approved automatically by walking the state machine (see
# workflow.formations.auto_complete_non_prospective_steps).
#
# The steps are named, not derived: they are exactly the post-mortem paperwork
# a dry/wet well still has to file. Names are the POST-v4-merge ones ("SAD
# Update" absorbed "Resource Assessment Update" + "Executive Summary Final",
# "Executive Summary" absorbed "URED Update"), so a legacy well's retired rows
# (is_active = 0) are never in scope -- the auto-walk only touches active rows.
# All four live in BP_EXECUTION_STAGES, so the applicable-stages filter alone
# keeps a prospect-pipeline project out of scope; there is no pipeline_type
# literal anywhere in the rule.
NON_PROSPECTIVE_AUTO_COMPLETE_STEPS = (
    "Executive Summary",       # Post-Drilling
    "Flowback Results",        # Post-Testing
    "SAD Update",              # Post-Testing
    "PVAD Structural MTR",     # Post-Testing
)

# The quicklook fluids that mean "no hydrocarbons here" (compared lowercased
# after strip, so 'dry', 'DRY' and ' Water ' all count). Blank or anything else
# -- including 'Gas over Water' -- is NOT a trigger: the rule fires only on an
# unambiguous non-hydrocarbon result.
NON_PROSPECTIVE_FLUIDS = {"water", "dry"}

# The distinct task_history action_type + comment the auto-walk leaves behind.
# The action_type doubles as the ONCE-EVER marker: a task that already carries
# one is never auto-completed again, so a user who reopens an auto-completed
# step is not fought by a later formations save (see requirement "NOT
# reversible" -- the audit trail explains the state).
AUTO_COMPLETE_EVENT = "Auto-Completed"
AUTO_COMPLETE_COMMENT = (
    "Auto-completed: single quicklook formation with non-hydrocarbon fluid ({fluid})")


# READ mapping: overview_key -> ordered [(task_name, field_key), ...] sources;
# the first non-blank source wins. This is a display composition, not a stored
# mirror: a step's field feeds the overview the moment it is read, so a missed
# entry here shows a BLANK in the overview -- it can never show silently stale
# data. Multi-source keys follow the frontend's latest-assessment-first
# precedence (LATEST_PIIP_SOURCES in static/js/views/detail-form.js).
#
# The ordered-list shape is ALSO how the v4 merges keep legacy wells readable:
# a surviving step is listed first and the retired step it absorbed second
# (same EAV key, different task_name bucket), so a well whose values were
# entered before the merge still resolves. get_project_dynamic_field_map is
# retired-inclusive precisely so those legacy buckets are present.
#
# The v5 renames add a second entry to the two lead-phase keys for the SAME
# reason in reverse: those rows were renamed in place, so the CURRENT name is
# listed first and the pre-v5 spelling second, covering a project the rename's
# both-names guard skipped (and keeping the ladder readable as history).
_OVERVIEW_READ_SOURCES = {
    "lead_ogip": [("Resource Assessment", "lead_piip_gas_mean"),
                  ("Lead Resource Assessment", "lead_piip_gas_mean")],
    "pre_drill_estimation": [("Pre-Drilling GeoX Assessment", "pre_drill_piip_gas_mean"),
                             ("Pre-Drilling Resource Assessment", "pre_drill_piip_gas_mean")],
    "post_drill_estimation": [("SAD Update", "resource_update_gas_mean"),
                              ("Resource Assessment Update", "resource_update_gas_mean"),
                              ("SAD Model", "post_drill_piip_gas_mean"),
                              ("Post-Drilling Resource Assessment", "post_drill_piip_gas_mean")],
    "quick_look_pay": [("Quicklook Logs", "quicklook_pay_thickness_ft")],
    "quick_look_porosity": [("Quicklook Logs", "quicklook_average_porosity_pct")],
    "quick_look_swt": [("Quicklook Logs", "quicklook_average_swt_pct")],
    "flowback_results": [("Flowback Results", "flowback_gas_rate_mmscfd")],
    # WS7: the Classification entered in the BP Execution Gate step feeds the
    # Portfolio; GHEER is a read-fallback for rows entered before the move.
    "classification": [("BP Execution Gate", "bp_gate_classification"),
                       ("GHEER", "gheer_classification")],
}

# The Total-CoS inputs as ordered (task_name, field_key) ladders -- surviving
# step first, the v5-retired half second. Unlike every other v5 read, THIS pair
# genuinely needs the fallback: the merge did not rename a row, it created a new
# one, so a lead's pre-v5 trap_cos_pct/seal_cos_pct live under the retired
# "Trap CoS" / "Seal CoS" task_ids. (v5 also COPIES those values onto the merged
# row so the merged FORM prefills -- the ladder is what covers the rows the copy
# guard skipped, and any project whose merged row already existed.)
TRAP_COS_SOURCES = (("Trap and Seal CoS", "trap_cos_pct"),
                    ("Trap CoS", "trap_cos_pct"))
SEAL_COS_SOURCES = (("Trap and Seal CoS", "seal_cos_pct"),
                    ("Seal CoS", "seal_cos_pct"))
# Reservoir CoS was neither renamed nor merged; named alongside its two
# siblings so calculate_total_cos reads one uniform shape.
RESERVOIR_COS_ROWS_SOURCES = (("Reservoir CoS", "reservoir_cos_rows"),)

# The LATEST saved Mean Gas (BCF) for one record, as an ordered
# ((task_name, field_key), ...) precedence -- newest assessment first, each
# surviving step immediately followed by the retired step it absorbed.
#
# Composed from the _OVERVIEW_READ_SOURCES entries above rather than retyped,
# so the two can never drift; the ORDER of the three keys (post-drill, then
# pre-drill, then lead) is the server-side twin of LATEST_PIIP_SOURCES in
# static/js/views/detail-form.js
# (POST_DRILL_PIIP_SOURCES + LEAD_PIIP_SOURCES, where the lead half is
# [Pre-Drilling GeoX Assessment, Resource Assessment], each followed by its
# pre-v5 spelling).
#
# Read by workflow.projects._annotate_mean_gas to put ``mean_gas_bcf`` on every
# board row (Card 1E's Total Mean OGIP tile). P90/P10 are NEVER consulted: the
# mean is a saved input in its own right, not something to interpolate.
LATEST_MEAN_GAS_SOURCES = tuple(
    tuple(source) for source in (
        _OVERVIEW_READ_SOURCES["post_drill_estimation"]
        + _OVERVIEW_READ_SOURCES["pre_drill_estimation"]
        + _OVERVIEW_READ_SOURCES["lead_ogip"]
    )
)

# Overview keys with no feeding step in the current 27-step pipeline; kept as
# blanks so the /detail ``overview`` shape stays stable for the frontend.
_OVERVIEW_LEGACY_KEYS = [
    "ogip", "preliminary_resource_estimation", "reservoir_pressure",
    "reservoir_gradient", "pay", "porosity", "swt",
]


# Steps whose SUBMIT is gated on checkbox inputs: task_name -> ordered
# ((field_key, human label), ...). A submit is refused (ValueError -> HTTP 400)
# until every listed key holds a checkbox-truthy value; the error message names
# the unmet ones by label. Deliberately generic -- a future gated step is one
# entry here plus its mirror in static/js/schema.js REQUIRED_FIELDS_FOR_SUBMIT.
#
# The labels ride along (rather than keys alone) because the SERVER composes
# the message and has no access to the client's SCHEMA labels; the client
# mirror is a pre-check only -- this table is the authority.
REQUIRED_FIELDS_FOR_SUBMIT = {
    "SAD Update": (
        ("sad_update_done", "SAD Update"),
        ("final_exec_summary_done", "Final Executive Summary"),
    ),
}

# Checkbox truthiness, matching dom.js truthy() and the SQL CASE used by the
# board's active-drilling flag.
_CHECKBOX_TRUTHY = {"1", "true", "yes", "on"}


def unmet_submit_requirements(task_name, fields):
    """Return the labels of a step's unchecked submit-gate boxes (pure).

    ``fields`` is a {field_key: value} map of the task's stored dynamic
    fields. A step with no entry in REQUIRED_FIELDS_FOR_SUBMIT always returns
    an empty list.
    """
    fields = fields or {}
    return [label for key, label in REQUIRED_FIELDS_FOR_SUBMIT.get(task_name, ())
            if str(fields.get(key) or "").strip().lower() not in _CHECKBOX_TRUTHY]


# ---------------------------------------------------------------------------
# Field-driven completion (the redesign's detail cards)
# ---------------------------------------------------------------------------
# The redesign defines a step's completion by its FIELD STATE -- the user ticks
# the confirmations, fills the inputs and saves; there is no separate
# submit -> approve walk to remember. This table declares, per step, WHAT
# "done" means; :func:`workflow.lifecycle.apply_field_completion` is the engine
# that reads it on every save of that step and reconciles the status.
#
# The value is a DECLARATIVE predicate spec the engine interprets -- never a
# callable -- so a new detail card is one entry here plus its checkbox in
# static/js/schema.js, and the whole rule stays inspectable/testable as data:
#
#   "required_checked": (field_key, ...)  every key must be checkbox-truthy
#   "required_present": (field_key, ...)  every key must hold a VALID value
#
# "Valid" defaults to "non-blank", but a key may declare a richer notion of
# presence in the engine's value-validator table (lifecycle._field_present) --
# reservoir_cos_rows does, because the whole mini-sheet is stored as ONE JSON
# array whose empty form ("[]") is a perfectly non-blank string.
#
# Both lists are AND-ed and an absent list is vacuously satisfied, so a
# checkbox-only card (Seismic Signature Validation) declares one key and a
# card that also demands real inputs (Reservoir CoS) declares both.
FIELD_COMPLETION = {
    # Card 3A. BOTH halves are required: the supporting-slides confirmation AND
    # a stored, model-scored evaluation. The checkbox alone is not completion --
    # a Reservoir CoS step with an empty mini-sheet has no CoS to carry forward
    # into the Total Chance of Success.
    "Reservoir CoS": {
        "required_checked": ("reservoir_slides_loaded",),
        "required_present": ("reservoir_cos_rows",),
    },
    # Card 3C. The step has no inputs of its own; the confirmation IS the work.
    "Seismic Signature Validation": {
        "required_checked": ("seismic_slides_loaded",),
    },
}

# Steps whose completion is a HUMAN APPROVAL and must never become field-driven.
# "Segmentation Slides" is the one tracked item the board still shows as
# "Pending Approval" (projects._READY_SHOWS_PENDING): its submit -> approve walk
# IS the deliverable's review gate (card 3D), so putting it in FIELD_COMPLETION
# would silently delete a supervisor's job. Named here rather than left as a
# comment so a test can assert the two tables never overlap.
FIELD_COMPLETION_MANUAL_APPROVAL_STEPS = frozenset({"Segmentation Slides"})

# The distinct task_history action_type + comment the engine leaves behind, in
# both directions. Unlike AUTO_COMPLETE_EVENT these are NOT once-ever markers:
# the engine is a reconciliation, so a step may legitimately close and reopen as
# many times as the user ticks and unticks the box, each move audited.
FIELD_COMPLETION_EVENT = "Field Completion"
FIELD_COMPLETION_COMMENT = "Completed: required confirmations satisfied"
FIELD_REOPEN_EVENT = "Field Reopen"
FIELD_REOPEN_COMMENT = "Reopened: required confirmation removed"


def field_completion_met(task_name, fields, is_present=None):
    """Is a step's FIELD_COMPLETION predicate satisfied? (pure)

    ``fields`` is a {field_key: value} map of the task's stored dynamic fields.
    ``is_present`` is an optional ``(field_key, value) -> bool`` override for
    keys whose "has a valid value" is richer than "non-blank" (see
    lifecycle._field_present); the default is a plain non-blank test.

    A step with no entry returns False -- "not field-driven", NOT "done": the
    engine must never touch a step this table does not claim.
    """
    spec = FIELD_COMPLETION.get(task_name)
    if not spec:
        return False
    fields = fields or {}
    for key in spec.get("required_checked", ()):
        if str(fields.get(key) or "").strip().lower() not in _CHECKBOX_TRUTHY:
            return False
    present = is_present or (lambda _key, value: str(value or "").strip() != "")
    for key in spec.get("required_present", ()):
        if not present(key, fields.get(key)):
            return False
    return True


# action -> (required current status, resulting status). This is the PUBLIC
# vocabulary of POST /api/tasks/<id>/transition; main.py validates the incoming
# action against it before calling transition_task.
TASK_TRANSITIONS = {
    "submit": ("In Progress", "Ready"),
    "approve": ("Ready", "Approved"),
    "return": ("Ready", "In Progress"),
}

# ENGINE-ONLY reverse move, deliberately kept OUT of TASK_TRANSITIONS.
#
# The manual lifecycle has no way back out of Approved -- "return" only undoes a
# submit (Ready -> In Progress) -- because un-approving is a supervisor decision
# the UI does not offer. The field-completion engine needs exactly that move:
# when a user unticks a confirmation on a step the engine itself closed, the
# step must reopen. Listing it in TASK_TRANSITIONS would publish an ungated
# Approved -> In Progress action on the transition endpoint (the assignee/
# supervisor checks in transition_task are keyed on the "return" action alone),
# so it lives here and only :func:`workflow.lifecycle.apply_field_completion`
# ever names it.
ENGINE_TRANSITIONS = {
    "reopen": ("Approved", "In Progress"),
}

# Everything transition_task itself will honor: the public actions plus the
# engine-only one.
_ALL_TRANSITIONS = dict(TASK_TRANSITIONS, **ENGINE_TRANSITIONS)

_TRANSITION_EVENTS = {
    "submit": "Component Submitted",
    "approve": "Component Approved",
    "return": "Component Returned",
    "reopen": "Component Reopened",
}
