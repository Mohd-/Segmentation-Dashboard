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
    All active task rows always exist regardless of pipeline; the rows
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

# The 24-step pipeline definition: (sequence_no, task_name, stage_group).
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
# Still 12 prospect steps at v5 (so then 27 in total, and the BP numbers 13-27
# did not move), and at that version they were the tracked items themselves:
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
#
# v7 consolidated the four Lead Assessment task rows into one lifecycle row.
# Their inputs moved with it, while the four former names remain ordered,
# field-derived checkpoints so board and summary progress stay out of twelve.
PIPELINE_TEMPLATES = [
    # v7 folds the four former Lead Assessment rows into this single lifecycle
    # row.  Their field-state predicates remain visible as the four derived
    # checkpoints in LEAD_ASSESSMENT_CHECKPOINTS below, so the board still
    # communicates x/4 and n/12 without manufacturing four task rows.
    (1, "Lead Assessment", "Lead Assessment"),
    (2, "Reservoir CoS", "Risk Analysis"),
    # v5: "Trap CoS" + "Seal CoS" merged here. It keeps BOTH steps' EAV keys
    # verbatim (sarah_quwarah_thickness_ft / trap_cos_pct and the five seal_*
    # inputs / seal_cos_pct), which is what lets the recompute hooks and the
    # Total CoS read carry straight over.
    (3, "Trap and Seal CoS", "Risk Analysis"),
    (4, "Seismic Signature Validation", "Risk Analysis"),
    # v18: "Presence CoS Evaluation" was removed as a visible step -- its value
    # is derived (Reservoir x Trap x Seal), computed at read time
    # (calculate_total_cos) and surfaced as ``derisking`` in the /detail
    # payload's computed overview.
    (5, "Segmentation Slides", "Risk Analysis"),
    (6, "Moving Tolerance", "Pre-Well Delivery"),
    (7, "Approval to Stake", "Pre-Well Delivery"),
    (8, "Well Site Location", "Pre-Well Delivery"),
    (9, "Pre-Drilling GeoX Assessment", "Pre-Well Delivery"),
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
    # v7 (prospect): the four field-owning rows became derived checkpoints on
    # the one Lead Assessment row.  Their history remains under these ids;
    # their EAV values are moved to the survivor by migration v7.
    "Area Definition",
    "Thickness Estimation",
    "GRV Inputs",
    "Resource Assessment",
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
    "lead_ogip": [("Lead Assessment", "lead_piip_gas_mean"),
                  ("Resource Assessment", "lead_piip_gas_mean"),
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

# Overview keys with no feeding step in the current 24-step pipeline; kept as
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


def _checkbox_on(value):
    """Is a stored dynamic-field value a ticked checkbox? (pure)"""
    return str(value or "").strip().lower() in _CHECKBOX_TRUTHY


def unmet_submit_requirements(task_name, fields):
    """Return the labels of a step's unchecked submit-gate boxes (pure).

    ``fields`` is a {field_key: value} map of the task's stored dynamic
    fields. A step with no entry in REQUIRED_FIELDS_FOR_SUBMIT always returns
    an empty list.
    """
    fields = fields or {}
    return [label for key, label in REQUIRED_FIELDS_FOR_SUBMIT.get(task_name, ())
            if not _checkbox_on(fields.get(key))]


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
#   "required_greater": ((hi_key, lo_key), ...)  hi must parse numeric and be
#                                         STRICTLY greater than lo
#
# "Valid" defaults to "non-blank", but a key may declare a richer notion of
# presence in the engine's value-validator table (lifecycle._field_present) --
# reservoir_cos_rows does, because the whole mini-sheet is stored as ONE JSON
# array whose empty form ("[]") is a perfectly non-blank string, and card 2B's
# numeric pairs do, because "0" and "-3" are non-blank but are not a lead's
# area, thickness, GRV or PIIP mean (see POSITIVE_NUMBER_FIELDS below).
#
# "required_greater" is the CROSS-FIELD half card 2B needs: a P90/P10 pair or a
# Reservoir/Formation pair is only a valid capture when the two sides are in the
# right order, and EQUALITY is not (equal percentiles are a mis-entry, not a
# degenerate-but-legal distribution). Stated as data rather than a callable for
# the same reason the other two lists are: the whole rule stays inspectable.
#
# All three lists are AND-ed and an absent list is vacuously satisfied, so a
# checkbox-only card (Seismic Signature Validation) declares one key and a
# card that also demands ordered real inputs (Area Definition) declares more.
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
    # Card 3B. The v5 merge put BOTH CoS halves on one step, so "done" means
    # BOTH results are stored -- the Trap percentage AND the Seal percentage --
    # plus the Seal supporting-slides confirmation. A Trap half filled in while
    # the Seal form is still blank leaves the step In Progress, which is the
    # whole point of merging them: the lead's Total Chance of Success needs both
    # factors, and half a merged step is not a completed one. Both results are
    # WRITTEN BY THE SERVER's own recompute hooks on save
    # (lifecycle._apply_trap_cos_calculation / _apply_seal_cos_calculation) and
    # stored as whole-number percentage strings, with "" / an absent key meaning
    # "not computed" -- so the DEFAULT non-blank notion of presence is exactly
    # right here and neither key needs an entry in lifecycle._field_present.
    "Trap and Seal CoS": {
        "required_checked": ("seal_slides_loaded",),
        "required_present": ("trap_cos_pct", "seal_cos_pct"),
    },
    # ---- Card 2B: the four tracked items of the consolidated Lead Assessment
    # page. The page is ONE workspace with ONE Save, but the four items keep
    # their own rows, their own dots on the board and their own rules -- so the
    # engine still decides each of them independently, from the fields that
    # item owns. That is what lets a user fill Section 2's Area pair and watch
    # exactly one dot go green.
    #
    # Section 2's left half. Both percentiles must be real positive numbers and
    # P10 must exceed P90 -- an area "distribution" of 5 to 5 is a typo, not a
    # narrow one. top_formation_tvdss_ft also lives on this task (Section 3) and
    # is deliberately ABSENT from the predicate: the card asks for it as
    # reference information, not as a gate, so a lead with no TVDSS still
    # completes Area Definition.
    "Area Definition": {
        "required_present": ("p90_area_km2", "p10_area_km2"),
        "required_greater": (("p10_area_km2", "p90_area_km2"),),
    },
    # Section 1. The canonical thickness reads (reservoir_thickness_ft /
    # formation_thickness_ft) are the predicate -- NOT the twt_*_ms inputs
    # beside them, which are one of two interchangeable ways to arrive at those
    # feet (see config.TWT_THICKNESS_COEFFICIENTS). Whichever side the user
    # typed, the step is done when both thicknesses are stored and the formation
    # envelope genuinely contains the reservoir (strictly greater; equal
    # thicknesses would mean a zero-thickness overburden).
    "Thickness Estimation": {
        "required_present": ("reservoir_thickness_ft", "formation_thickness_ft"),
        "required_greater": (("formation_thickness_ft", "reservoir_thickness_ft"),),
    },
    # Section 2's right half. Same P90/P10 shape as Area Definition, in
    # 10^3 acre.ft.
    "GRV Inputs": {
        "required_present": ("grv_p90_thousand_acre_ft", "grv_p10_thousand_acre_ft"),
        "required_greater": (("grv_p10_thousand_acre_ft", "grv_p90_thousand_acre_ft"),),
    },
    # Section 4 + Section 3's checkbox. BOTH halves, for the same reason
    # Reservoir CoS needs both: a ticked "the polygons are filed" box with no
    # computed volume carries nothing forward into the portfolio, and a computed
    # volume whose supporting surfaces were never filed is not a reviewable
    # deliverable. lead_piip_gas_mean is the ONE stored number every downstream
    # reader resolves the lead's volume from (_OVERVIEW_READ_SOURCES's
    # "lead_ogip", LATEST_MEAN_GAS_SOURCES, the Lead Summary's gas trio), so
    # keying the predicate on it means "this step is complete" and "this step
    # feeds the portfolio" can never disagree -- exactly the reservoir_cos_rows
    # argument. It is written by the page's auto-run, never typed.
    "Resource Assessment": {
        "required_checked": ("polygons_surfaces_loaded",),
        "required_present": ("lead_piip_gas_mean",),
    },
    # ---- Card 4A: Moving Tolerance. The step's WHOLE capture is its predicate:
    # the lead's X/Y plus THREE complete max-distance/azimuth option pairs --
    # eight fields, all eight required. A staking tolerance with two options is
    # not a smaller tolerance, it is an unfinished one: the surveyors are handed
    # three directions to move the rig within, and the step's own form asks for
    # exactly three. Anything less still SAVES (a partial capture is a normal
    # work-in-progress save; nothing here rejects a write) -- it just leaves the
    # item In Progress, which is what a half-filled pair should read as.
    #
    # THE NUMERIC CHOICE, stated: these are NUMERIC_FIELDS, not
    # POSITIVE_NUMBER_FIELDS. An azimuth of 0 degrees is due north -- a perfectly
    # ordinary bearing -- so a >0 rule would silently refuse to complete a
    # legitimate capture, and no positive-only rule exists for them anywhere
    # today (the client's own numericFieldError admits 0 and rejects only
    # negatives). The X/Y coordinates keep their pre-existing shape too: UTM
    # eastings/northings are six/seven digits (schema.js marks them bigOk) and
    # carry no magnitude rule beyond "it is a number".
    #
    # No azimuth-range (0-360) or distance ceiling is imposed: the card is
    # explicit that this step gains no new constraints, only a completion rule.
    "Moving Tolerance": {
        "required_present": (
            "staking_well_x", "staking_well_y",
            "staking_opt1_max_distance_m", "staking_opt1_azimuth_deg",
            "staking_opt2_max_distance_m", "staking_opt2_azimuth_deg",
            "staking_opt3_max_distance_m", "staking_opt3_azimuth_deg",
        ),
    },
    # ---- Card 4B: the TWO tracked items of the consolidated Staking Letters
    # page. Same shape as card 2B's four: one page, one Save, but each item
    # still completes from the fields IT owns, so ticking the first two boxes
    # turns exactly one dot green.
    #
    # Approval to Stake = the well record exists AND its letter is filed. The
    # first key is the v5 backfill (STAKING_WELL_CREATED_KEY): the retired "Well
    # Creation" step's sign-off became this checkbox, and migration v5 wrote '1'
    # for every project whose Well Creation row had been Approved -- so a
    # pre-v5 lead arrives here with box 1 already ticked and only has to file the
    # letter. Well creation is a PREREQUISITE recorded here, never a fifth
    # tracked item: the step is retired and stays retired.
    #
    # The letter box ALONE is deliberately insufficient. A filed Approval to
    # Stake letter for a well that does not exist yet is a document without a
    # subject, and the portfolio reads this exact step's status as "Staked"
    # (reporting._approval_to_stake_map) -- so the rule that closes it is the
    # rule that says a lead has been staked.
    "Approval to Stake": {
        "required_checked": (STAKING_WELL_CREATED_KEY, "approval_stake_letter_loaded"),
    },
    # Well Site Location = the Wellsite Location letter is filed AND the location
    # it names is recorded. The letter is the deliverable; the staked coordinates
    # are what the rest of the business reads off it, and a filed letter whose
    # coordinates were never captured leaves the well site undefined. The two
    # inputs are only REVEALED once the box is ticked (progressive disclosure on
    # the page), which is the same order the predicate states.
    #
    # staked_x/staked_y are NUMERIC_FIELDS for the same reason the Moving
    # Tolerance coordinates are: UTM readings, no sign or magnitude rule.
    "Well Site Location": {
        "required_checked": ("wellsite_letter_loaded",),
        "required_present": ("staked_x", "staked_y"),
    },
}

# Card 2B remains a four-checkpoint communication model even though v7 gives
# it one canonical task row and lifecycle.  The labels deliberately retain the
# former step names: they are stable board vocabulary, not runnable tasks.
# Keep this list ordered; it is both the x/4 counter order and the migration's
# source order for status/assignee selection.
LEAD_ASSESSMENT_CHECKPOINTS = (
    "Area Definition",
    "Thickness Estimation",
    "GRV Inputs",
    "Resource Assessment",
)

# The one real Lead Assessment lifecycle may be submitted and approved as a
# whole.  Completion of the four checkpoints is derived from its fields, not a
# second state machine.  This aggregate predicate is intentionally available
# to callers that need to ask whether all four checkpoints are complete, but
# is excluded from the automatic field-completion engine below: filling the
# form must not bypass the supervisor's approval of the consolidated stage.
FIELD_COMPLETION["Lead Assessment"] = {
    "required_checked": tuple(
        key for checkpoint in LEAD_ASSESSMENT_CHECKPOINTS
        for key in FIELD_COMPLETION[checkpoint].get("required_checked", ())
    ),
    "required_present": tuple(
        key for checkpoint in LEAD_ASSESSMENT_CHECKPOINTS
        for key in FIELD_COMPLETION[checkpoint].get("required_present", ())
    ),
    "required_greater": tuple(
        pair for checkpoint in LEAD_ASSESSMENT_CHECKPOINTS
        for pair in FIELD_COMPLETION[checkpoint].get("required_greater", ())
    ),
}

# Named independently so lifecycle can keep the established automatic behavior
# for all field-driven cards while leaving Lead Assessment's submit/approve
# lifecycle under human control.
FIELD_COMPLETION_AUTOMATED_STEPS = frozenset(FIELD_COMPLETION) - {"Lead Assessment"}

# Keys whose "has a valid value" means a POSITIVE NUMBER, not merely a non-blank
# string. Read by the engine's value validator (lifecycle._field_present) and
# by the ordering check below, so both halves of a card 2B predicate agree on
# what a usable number is.
#
# Every one of these is a physical magnitude a lead cannot have zero or less of:
# an area, a thickness, a gross rock volume, a mean volume in place. "0" is the
# value a half-filled form and a cleared input both leave behind, so accepting
# it would let an empty section read as complete.
POSITIVE_NUMBER_FIELDS = frozenset({
    "p90_area_km2", "p10_area_km2",
    "reservoir_thickness_ft", "formation_thickness_ft",
    "grv_p90_thousand_acre_ft", "grv_p10_thousand_acre_ft",
    "lead_piip_gas_mean",
})


# Keys whose "has a valid value" means A NUMBER -- any number, including zero
# and negatives -- rather than merely a non-blank string. The looser sibling of
# POSITIVE_NUMBER_FIELDS above, and the third (and last) entry in the engine's
# value-validator table (lifecycle._field_present).
#
# Card 4A/4B's coordinates and bearings live here because they are genuinely
# unbounded readings: a UTM easting is a six/seven-digit magnitude with no upper
# rule, and an azimuth of 0 degrees is due north. What they must NOT be is the
# free text a plain non-blank test would accept -- "TBD" in a coordinate box is
# an absent coordinate, and a step whose staking location reads "TBD" is not a
# staked one.
NUMERIC_FIELDS = frozenset({
    # Card 4A -- Moving Tolerance's eight inputs.
    "staking_well_x", "staking_well_y",
    "staking_opt1_max_distance_m", "staking_opt1_azimuth_deg",
    "staking_opt2_max_distance_m", "staking_opt2_azimuth_deg",
    "staking_opt3_max_distance_m", "staking_opt3_azimuth_deg",
    # Card 4B -- the staked location revealed by the Wellsite Location letter.
    "staked_x", "staked_y",
})


def positive_number(value):
    """Does a stored dynamic-field value parse as a number > 0? (pure)"""
    try:
        return float(str(value or "").strip()) > 0
    except (TypeError, ValueError):
        return False


def _number_or_none(value):
    """float(value) or None -- never raises. (pure)"""
    try:
        return float(str(value or "").strip())
    except (TypeError, ValueError):
        return None


def is_number(value):
    """Does a stored dynamic-field value parse as a number at all? (pure)

    The NUMERIC_FIELDS validator: blank, "TBD" and "12 m" are absent; "0",
    "-6500" and "532100.5" are present.
    """
    return _number_or_none(value) is not None


def lead_assessment_checkpoint_met(checkpoint, fields):
    """Return the field-derived state of one v7 Lead Assessment checkpoint.

    The ordinary lifecycle engine needs one richer presence rule for the
    Reservoir CoS JSON sheet.  Lead Assessment never carries that field, so
    its checkpoint helper can remain dependency-free while exactly preserving
    the positive-number and generic-number semantics used by the engine.
    """
    if checkpoint not in LEAD_ASSESSMENT_CHECKPOINTS:
        return False

    def present(field_key, value):
        if field_key in POSITIVE_NUMBER_FIELDS:
            return positive_number(value)
        if field_key in NUMERIC_FIELDS:
            return is_number(value)
        return str(value or "").strip() != ""

    return field_completion_met(checkpoint, fields, present)

# Steps whose completion is a HUMAN APPROVAL and must never become field-driven.
# "Segmentation Slides" is the one tracked item the board still shows as
# "Pending Approval" (projects._READY_SHOWS_PENDING): its submit -> approve walk
# IS the deliverable's review gate (card 3D), so putting it in FIELD_COMPLETION
# would silently delete a supervisor's job. Named here rather than left as a
# comment so a test can assert the two tables never overlap.
FIELD_COMPLETION_MANUAL_APPROVAL_STEPS = frozenset({"Segmentation Slides"})

# ---------------------------------------------------------------------------
# Checkbox-driven SUBMISSION (card 3D)
# ---------------------------------------------------------------------------
# The other half of a manual-approval step: the employee never sees a separate
# "Submit for Approval" button, so THE SAVE ITSELF has to ask for the review.
# task_name -> the confirmation whose ticked state means "this deliverable is
# ready for a supervisor to look at".
#
# Deliberately a SIBLING of FIELD_COMPLETION, not an extension of it: the engine
# drives a step all the way to Approved, whereas this table stops at Ready and
# leaves the approval where card 3D wants it -- with a human. The two tables
# must never name the same step (a test asserts it), and every step named here
# must be a manual-approval one.
#
# The move is Not Assigned / In Progress -> Ready ONLY. A step already Ready is
# waiting on a supervisor, so re-saving it (a typo fix in the comments, say)
# must not file a second request for the same review; a step already Approved is
# finished. Unticking the box does NOT withdraw a pending submission either --
# there is no "withdraw" in the lifecycle, and inventing one would silently
# cancel a review the supervisor may already be reading. Reopening a submitted
# step stays the supervisor's "return" action.
CHECKBOX_SUBMIT_STEPS = {
    "Segmentation Slides": "segmentation_slides_loaded",
}

# The statuses a checkbox-driven submission may move FROM (see above).
CHECKBOX_SUBMIT_FROM_STATUSES = frozenset({"Not Assigned", "In Progress"})


def checkbox_submit_met(task_name, fields):
    """Is a step's CHECKBOX_SUBMIT_STEPS confirmation ticked? (pure)

    ``fields`` is a {field_key: value} map of the task's stored dynamic fields.
    A step with no entry returns False -- "not checkbox-submitted", NOT "ready":
    the hook must never touch a step this table does not claim.
    """
    key = CHECKBOX_SUBMIT_STEPS.get(task_name)
    return bool(key) and _checkbox_on((fields or {}).get(key))

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
        if not _checkbox_on(fields.get(key)):
            return False
    present = is_present or (lambda _key, value: str(value or "").strip() != "")
    for key in spec.get("required_present", ()):
        if not present(key, fields.get(key)):
            return False
    # Cross-field ordering (card 2B's P90/P10 and Reservoir/Formation pairs).
    # STRICTLY greater: equality is a mis-entry, not a valid capture. A pair
    # whose sides do not both parse as numbers fails here rather than raising --
    # required_present has normally already rejected that, and a spec that
    # orders a key it did not also require must not blow up the save hook.
    for hi_key, lo_key in spec.get("required_greater", ()):
        hi = _number_or_none(fields.get(hi_key))
        lo = _number_or_none(fields.get(lo_key))
        if hi is None or lo is None or hi <= lo:
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
