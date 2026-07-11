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

STAGE_ORDER = [
    "Lead Identification",
    "Risking",
    "Segmentation",
    "Pre-Well Delivery",
    "Well Delivery",
    "Post-Drilling",
    "Post-Testing",
]

PROSPECT_STAGES = ["Lead Identification", "Risking", "Segmentation", "Pre-Well Delivery"]
BP_EXECUTION_STAGES = ["Well Delivery", "Post-Drilling", "Post-Testing"]
BOARD_STAGE_ORDER = STAGE_ORDER[:]


def applicable_stages(pipeline_type):
    """Return the stage groups that make up a pipeline's operating scope.

    Applicability is a PURE FUNCTION of pipeline_type -- never stored per row: a
    prospect operates over PROSPECT_STAGES, a BP well over BP_EXECUTION_STAGES.
    All 31 task rows always exist regardless of pipeline; the rows outside the
    operating pipeline are simply excluded wherever it matters (completion,
    flow reconciliation, assignment cascade, state refresh) by filtering on
    ``stage_group IN applicable_stages(...)``.
    """
    return BP_EXECUTION_STAGES if str(pipeline_type or "prospect").lower() == "bp" else PROSPECT_STAGES

# Well-level formation interpretation (project_formations). Fixed formation
# list -- users never create formations. Rows are keyed by
# (project, formation, phase); ``phase`` says which interpretation step the
# values came from.
FORMATIONS = ["SARH", "QASM", "QWRH"]
FORMATION_PHASES = ["quicklook", "final"]
FORMATION_VALUE_FIELDS = [
    "top_tvdss_ft", "base_tvdss_ft", "thickness_ft", "porosity_pct",
    "swt_pct", "pay_ft", "ngr_pct", "fluid",
]
# All value fields except 'fluid' are REAL columns (project_formations); 'fluid'
# is a free-text description and stays TEXT.
FORMATION_NUMERIC_FIELDS = [f for f in FORMATION_VALUE_FIELDS if f != "fluid"]

# The 31-step pipeline definition: (sequence_no, task_name, stage_group).
# This list is the SINGLE SOURCE OF TRUTH for the workflow -- there is no
# task_templates table; project creation materializes project_tasks rows
# straight from these tuples.
#
# Pre-deployment, editing this list only affects NEW projects (existing dev
# databases are throwaway -- delete the .db and restart; see migrations.py).
# POST-deployment, changing it requires a numbered data migration for existing
# project_tasks rows: resequencing by task_name, and deactivating retired
# steps (is_active = 0) so their inputs and audit trail survive.
PIPELINE_TEMPLATES = [
    (1, "Reservoir Area Definition", "Lead Identification"),
    (2, "Thickness Estimation", "Lead Identification"),
    (3, "Lead Resource Assessment", "Lead Identification"),
    (4, "Seismic Signature Validation", "Risking"),
    (5, "Reservoir CoS", "Risking"),
    (6, "Trap CoS", "Risking"),
    (7, "Seal CoS", "Risking"),
    # v18: "Presence CoS Evaluation" (formerly step 8) was removed as a visible
    # step -- its value is derived (Reservoir x Trap x Seal), computed at read
    # time (calculate_total_cos) and surfaced as ``derisking`` in the /detail
    # payload's computed overview. The remaining steps renumber to a clean 1-31.
    (8, "Prospect Evaluation Presentation", "Segmentation"),
    (9, "Well Creation", "Pre-Well Delivery"),
    (10, "Pre-Drilling Resource Assessment", "Pre-Well Delivery"),
    (11, "Staking Moving Tolerance", "Pre-Well Delivery"),
    (12, "Approval to Stake", "Pre-Well Delivery"),
    (13, "BP Execution Gate", "Well Delivery"),
    (14, "Well Proposal", "Well Delivery"),
    (15, "Site Preparation", "Well Delivery"),
    (16, "Approval To Drill", "Well Delivery"),
    (17, "GHEER", "Well Delivery"),
    (18, "Quicklook Logs Interpretation", "Post-Drilling"),
    (19, "Aramco Picks", "Post-Drilling"),
    (20, "Post-Drilling Resource Assessment", "Post-Drilling"),
    (21, "SAD Model", "Post-Drilling"),
    (22, "Executive Summary", "Post-Drilling"),
    (23, "URED Update", "Post-Drilling"),
    (24, "Post-Well Outcome & Decision Gate", "Post-Drilling"),
    (25, "Flowback Results", "Post-Testing"),
    (26, "SAD Update", "Post-Testing"),
    (27, "Executive Summary Final", "Post-Testing"),
    (28, "Final Log Analysis", "Post-Testing"),
    (29, "PVAD Structural MTR", "Post-Testing"),
    (30, "Resource Assessment Update", "Post-Testing"),
    (31, "PDA", "Post-Testing"),
]


# READ mapping: overview_key -> ordered [(task_name, field_key), ...] sources;
# the first non-blank source wins. This is a display composition, not a stored
# mirror: a step's field feeds the overview the moment it is read, so a missed
# entry here shows a BLANK in the overview -- it can never show silently stale
# data. Multi-source keys follow the frontend's latest-assessment-first
# precedence (LATEST_PIIP_SOURCES in static/js/views/detail-form.js).
_OVERVIEW_READ_SOURCES = {
    "lead_ogip": [("Lead Resource Assessment", "lead_piip_gas_mean")],
    "pre_drill_estimation": [("Pre-Drilling Resource Assessment", "pre_drill_piip_gas_mean")],
    "post_drill_estimation": [("Resource Assessment Update", "resource_update_gas_mean"),
                              ("Post-Drilling Resource Assessment", "post_drill_piip_gas_mean")],
    "quick_look_pay": [("Quicklook Logs Interpretation", "quicklook_pay_thickness_ft")],
    "quick_look_porosity": [("Quicklook Logs Interpretation", "quicklook_average_porosity_pct")],
    "quick_look_swt": [("Quicklook Logs Interpretation", "quicklook_average_swt_pct")],
    "flowback_results": [("Flowback Results", "flowback_gas_rate_mmscfd")],
    # WS7: the Classification entered in the GHEER step feeds the Portfolio.
    "classification": [("GHEER", "gheer_classification")],
}

# Overview keys with no feeding step in the current 31-step pipeline; kept as
# blanks so the /detail ``overview`` shape stays stable for the frontend.
_OVERVIEW_LEGACY_KEYS = [
    "ogip", "preliminary_resource_estimation", "reservoir_pressure",
    "reservoir_gradient", "pay", "porosity", "swt",
]


# action -> (required current status, resulting status)
TASK_TRANSITIONS = {
    "submit": ("In Progress", "Ready"),
    "approve": ("Ready", "Approved"),
    "return": ("Ready", "In Progress"),
}

_TRANSITION_EVENTS = {
    "submit": "Component Submitted",
    "approve": "Component Approved",
    "return": "Component Returned",
}
