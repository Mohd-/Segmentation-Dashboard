"""Excel export composers for the Portfolio-wide analysis + staking sheets.

What belongs here:
- ``get_portfolio_export_rows(session)`` -- one row per non-archived lead/well
  (BP wells, mature leads, AND still-maturing prospect leads), the full
  per-record analysis table with the latest available value in every column.
- ``get_staking_export_rows(session)`` -- one row per mature lead, the
  staking-options sheet.

What does NOT belong here:
- Any writes or lifecycle logic (workflow.py) or the workbook/styling layout
  (export_excel.py) -- this module only composes rows.

No new DB model backs either sheet -- both are derived at export time from the
same read layer the rest of the app uses. Rationale: at this app's scale
(tens of projects), the whole export is ~3 batched queries, so a materialized
export table buys nothing but risk. A materialized table would need its
write path kept in lockstep with every mutation site that touches a task's
dynamic fields, formations, or the projects table itself; the day one of
those sites is missed, the export silently drifts from the live data. A
shared pure-read composer, by contrast, reads the exact same task inputs the
UI reads, so Excel and the UI can never disagree.

Style matches reporting.py: readable textual SQL with named binds through
db.fetch_one/db.fetch_all, batched (never per-row) queries over the id list.
``# PG:`` flags SQLite-only constructs for a future Postgres pass.
"""
from __future__ import annotations

import json
from typing import Dict, List

import config
import db
import folders
import reporting

# ---------------------------------------------------------------------------
# Column headers (shared with export_excel.py so a zero-row export still
# ships the header row).
# ---------------------------------------------------------------------------

PORTFOLIO_EXPORT_COLUMNS: List[str] = [
    "Well Name", "BP Year", "Classification", "Field", "Seismic Block", "Status",
    "Dynamic Mean (BCF)", "Booked",
    "OGIP P90 (BCF)", "OGIP Mean (BCF)", "OGIP P10 (BCF)",
    "Condensate P90 (MMSTB)", "Condensate Mean (MMSTB)", "Condensate P10 (MMSTB)",
    "Pull-up", "Amplitude Ratio", "BTS", "Reservoir CoS (%)",
    "P90 Area (km2)", "P10 Area (km2)",
    "SARH Formation Thickness (ft)",
    "P50 Pay Thickness (ft)", "P50 Porosity (%)", "Water Saturation (%)",
    "SARH-QWRH Thickness (ft)", "Trap CoS (%)",
    "Most Recent Age of Fault", "Dip", "Azimuth vs SHmax", "Fault LoC", "FPPM",
    "Seal CoS (%)", "Pore Pressure Gradient (psi/ft)",
    "Gas Rate (MMSCFD)", "Water Rate (BWPD)", "Choke Size (in)", "WHP (psi)",
]

STAKING_EXPORT_COLUMNS: List[str] = [
    "Lead Name", "X", "Y",
    "Option 1 Max Distance (m)", "Option 1 Azimuth (deg)",
    "Option 2 Max Distance (m)", "Option 2 Azimuth (deg)",
    "Option 3 Max Distance (m)", "Option 3 Azimuth (deg)",
]

# The task-level EAV keys the Portfolio Export row is composed from. Kept as
# a locally-owned list (NOT reporting._BP_TASK_FIELD_KEYS -- that list is
# reporting.py's own, and this module never imports another module's private
# key list) so the export table's inputs can grow independently of the
# business-plan scorecard's.
_PIIP_PREFIXES = ("resource_update", "post_drill_piip", "pre_drill_piip", "lead_piip")

_PORTFOLIO_TASK_FIELD_KEYS: List[str] = [
    "bp_gate_classification", "gheer_classification",
    "final_fluid_type", "resource_update_fluid_type",
    "post_drill_fluid_type", "quicklook_fluid_type",
    "reservoir_cos_rows",
    "flowback_dynamic_ogip_bcf",
    "pda_booked",
    "p90_area_km2", "p10_area_km2",
    "formation_thickness_ft", "reservoir_thickness_ft",
    "quicklook_pay_thickness_ft", "quicklook_average_porosity_pct", "quicklook_average_swt_pct",
    "sarah_quwarah_thickness_ft",
    "trap_cos_pct",
    "seal_recent_activity_age", "seal_dip", "seal_azimuth_vs_shmax",
    "seal_fault_level_confidence", "seal_fracture_permeability",
    "seal_cos_pct", "seal_pore_pressure_gradient_psi_ft",
    "flowback_gas_rate_mmscfd", "flowback_water_rate_bwpd",
    "flowback_choke_size_in", "flowback_fwhp_psi",
] + [f"{prefix}_{suffix}" for prefix in _PIIP_PREFIXES
     for suffix in ("gas_p90", "gas_mean", "gas_p10", "liquid_p90", "liquid_mean", "liquid_p10")]

_STAKING_TASK_FIELD_KEYS: List[str] = [
    "staking_well_x", "staking_well_y",
    "staking_opt1_max_distance_m", "staking_opt1_azimuth_deg",
    "staking_opt2_max_distance_m", "staking_opt2_azimuth_deg",
    "staking_opt3_max_distance_m", "staking_opt3_azimuth_deg",
]

_TRUTHY_STRINGS = {"1", "true", "yes", "on"}

# Formation-data "actual" phase precedence for the SARH pay/porosity/Swt trio
# (flagged assumption in the approved plan, cheap to change): final beats a
# resource-update revision beats the original post-drill read beats the
# original quicklook read.
_SARH_PHASE_PRECEDENCE = ("final", "resource_update", "post_drill", "quicklook")


def _first_filled(*values) -> str:
    """Return the first non-blank value as a stripped string, else ''."""
    for value in values:
        text_value = "" if value is None else str(value).strip()
        if text_value:
            return text_value
    return ""


def _truthy(value) -> bool:
    """Checkbox-style truthiness, matching dom.js truthy() and the workflow.py
    'is this checkbox on' SQL CASE ('1'/'true'/'yes'/'on', case-insensitive)."""
    return str(value or "").strip().lower() in _TRUTHY_STRINGS


def _task_fields(session, project_ids, keys) -> Dict[int, Dict[str, str]]:
    """Batched {project_id: {field_key: value}} for an arbitrary key list.

    Same shape/idiom as reporting._bp_task_fields (one query for the whole id
    list; higher task_id wins on legacy duplicate rows) but generalized over
    the caller's own key list instead of a module-private one.
    """
    if not project_ids or not keys:
        return {}
    rows = db.fetch_all(session, """
        SELECT pt.project_id, tdf.field_key, tdf.field_value
        FROM project_tasks pt
        JOIN task_dynamic_fields tdf ON tdf.task_id = pt.task_id
        WHERE pt.project_id IN :project_ids AND pt.is_active = 1
          AND tdf.field_key IN :field_keys
        ORDER BY pt.task_id
    """, {"project_ids": list(project_ids), "field_keys": list(keys)})
    fields: Dict[int, Dict[str, str]] = {}
    for row in rows:
        fields.setdefault(row["project_id"], {})[row["field_key"]] = row["field_value"] or ""
    return fields


def _sarh_formations(session, project_ids) -> Dict[int, Dict[str, dict]]:
    """Batched {project_id: {phase: project_formations row}} for formation SARH.

    One query for the whole id list, restricted to the canonical SARH row --
    the only formation the export's pay/porosity/Swt trio reads.
    """
    if not project_ids:
        return {}
    rows = db.fetch_all(session, """
        SELECT * FROM project_formations
        WHERE project_id IN :project_ids AND formation = 'SARH'
    """, {"project_ids": list(project_ids)})
    result: Dict[int, Dict[str, dict]] = {}
    for row in rows:
        result.setdefault(row["project_id"], {})[row["phase"]] = row
    return result


def _export_projects(session):
    """Return one membership row per NON-ARCHIVED project (export-only reader).

    Deliberately wider than reporting._portfolio_projects (the portfolio UI's
    BP-wells-plus-mature-leads contract, which the Staking sheet still uses):
    the analysis sheet also carries the still-maturing prospect leads, each
    filled with the latest available value per column. Same row shape and
    ordering as the shared reader -- BP year then name; leads' NULL years
    group together at the front.
    """
    return db.fetch_all(session, """
        SELECT p.project_id,
               p.project_name,
               p.business_plan_year AS year,
               COALESCE(p.pipeline_type, 'prospect') AS pipeline_type,
               COALESCE(p.business_plan_enabled, 0) AS business_plan_enabled,
               COALESCE(p.active_well_enabled, 0) AS active_well_enabled
        FROM projects p
        WHERE COALESCE(p.archived, 0) = 0
        ORDER BY p.business_plan_year, p.project_name COLLATE NOCASE
    """)  # PG: COLLATE NOCASE


def _project_lead_xy(session, project_ids) -> Dict[int, dict]:
    """Batched {project_id: {'lead_x':..., 'lead_y':...}} -- one extra query.

    _portfolio_projects (reporting.py, not touched by this module) does not
    carry lead_x/lead_y, so the staking sheet fetches them itself rather than
    asking WS2 to widen the shared query.
    """
    if not project_ids:
        return {}
    rows = db.fetch_all(session, """
        SELECT project_id, lead_x, lead_y FROM projects WHERE project_id IN :project_ids
    """, {"project_ids": list(project_ids)})
    return {row["project_id"]: row for row in rows}


def _parse_reservoir_cos_primary_row(raw_rows_json) -> dict:
    """Return the PRIMARY row of a reservoir_cos_rows JSON blob, or {} if bad.

    The primary row is the first row whose ``reservoir_cos_pct`` OR
    ``seismic_volume_ar_number`` is non-blank. Every Reservoir-CoS-derived value
    the export reads -- pull_up, amplitude_ratio, base_tight_sarah,
    reservoir_cos_pct AND the AR number that drives the Seismic Block column --
    is read from this ONE row, so the export never mixes vintages: a blank
    leading row can no longer contribute empty Pull-up/Amplitude/BTS/CoS columns
    while a later row supplies the Block. This mirrors, resolved to a single
    row, the first-non-empty notion workflow.first_reservoir_cos_row_value
    applies per key. Malformed/absent JSON, or no row with either field filled,
    yields {}."""
    if not raw_rows_json:
        return {}
    try:
        rows = json.loads(raw_rows_json)
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(rows, list):
        return {}
    for row in rows:
        row = row or {}
        if _first_filled(row.get("reservoir_cos_pct")) or _first_filled(row.get("seismic_volume_ar_number")):
            return row
    return {}


def get_portfolio_export_rows(session) -> List[dict]:
    """One row per non-archived lead/well (_export_projects) for Excel.

    Membership is EVERY non-archived project -- BP wells, mature leads, and
    still-maturing prospect leads (wider than the portfolio UI). Each column
    carries the latest available value for that record: proposed/staked rows
    fill their estimate columns from the prospect-step inputs, and the
    BP-execution-only columns (Dynamic Mean, flowback, Booked='No',
    classification) stay blank/No for leads because nothing later exists yet.

    Everything is batched: one EAV query for the ~40 task-input keys the
    columns are built from (_task_fields), one project_formations fetch
    restricted to the SARH row (_sarh_formations), one Approval-to-Stake map
    (reporting._approval_to_stake_map). Column order/keys match
    PORTFOLIO_EXPORT_COLUMNS exactly.

    The OGIP + Condensate trios are source-consistent: the FIRST assessment
    step (resource_update -> post_drill_piip -> pre_drill_piip -> lead_piip)
    whose ``_gas_mean`` is filled supplies all three OGIP values AND the
    matching Condensate trio from that same step's ``_liquid_*`` keys, so a
    row never mixes numbers from two different vintages of the assessment.
    Condensate values default to '0' when blank (OGIP stays '' -- there is no
    sensible zero-fallback for a gas volume with no source step).

    P50 Pay / P50 Porosity / Water Saturation read the SARH project_formations
    row at phase precedence final -> resource_update -> post_drill ->
    quicklook (a flagged, cheap-to-change assumption); when no SARH row
    exists at any of those phases, this falls back to the legacy pre-formations
    -table scalar EAV keys quicklook_pay_thickness_ft /
    quicklook_average_porosity_pct / quicklook_average_swt_pct so old wells
    (written before the multi-formation editor existed) still populate. P50 Pay
    falls back one rung further, to the Thickness Estimation step's
    reservoir_thickness_ft -- the pre-drill estimate an undrilled lead carries.
    """
    projects = _export_projects(session)
    project_ids = [item["project_id"] for item in projects]
    task_fields = _task_fields(session, project_ids, _PORTFOLIO_TASK_FIELD_KEYS)
    formations = _sarh_formations(session, project_ids)
    stake_map = reporting._approval_to_stake_map(session, project_ids)

    rows: List[dict] = []
    for item in projects:
        fields = task_fields.get(item["project_id"], {})

        classification = _first_filled(fields.get("bp_gate_classification"), fields.get("gheer_classification"))
        field_name = folders.parse_field_and_well(item["project_name"])[0]
        primary_reservoir_row = _parse_reservoir_cos_primary_row(fields.get("reservoir_cos_rows"))
        first_ar = _first_filled(primary_reservoir_row.get("seismic_volume_ar_number"))
        seismic_block = config.AR_TO_SEISMIC_BLOCK.get(first_ar, first_ar) if first_ar else ""
        status = reporting.record_status(fields, stake_map.get(item["project_id"], False))
        booked = "Yes" if _truthy(fields.get("pda_booked")) else "No"

        source_prefix = None
        for prefix in _PIIP_PREFIXES:
            if _first_filled(fields.get(f"{prefix}_gas_mean")):
                source_prefix = prefix
                break
        if source_prefix:
            ogip_p90 = _first_filled(fields.get(f"{source_prefix}_gas_p90"))
            ogip_mean = _first_filled(fields.get(f"{source_prefix}_gas_mean"))
            ogip_p10 = _first_filled(fields.get(f"{source_prefix}_gas_p10"))
            condensate_p90 = _first_filled(fields.get(f"{source_prefix}_liquid_p90")) or "0"
            condensate_mean = _first_filled(fields.get(f"{source_prefix}_liquid_mean")) or "0"
            condensate_p10 = _first_filled(fields.get(f"{source_prefix}_liquid_p10")) or "0"
        else:
            ogip_p90 = ogip_mean = ogip_p10 = ""
            condensate_p90 = condensate_mean = condensate_p10 = "0"

        pull_up = _first_filled(primary_reservoir_row.get("pull_up"))
        amplitude_ratio = _first_filled(primary_reservoir_row.get("amplitude_ratio"))
        bts = _first_filled(primary_reservoir_row.get("base_tight_sarah"))
        reservoir_cos_pct = _first_filled(primary_reservoir_row.get("reservoir_cos_pct"))

        sarh_by_phase = formations.get(item["project_id"], {})
        sarh_row = None
        for phase in _SARH_PHASE_PRECEDENCE:
            if phase in sarh_by_phase:
                sarh_row = sarh_by_phase[phase]
                break
        if sarh_row is not None:
            p50_pay = _first_filled(sarh_row.get("pay_ft"))
            p50_porosity = _first_filled(sarh_row.get("porosity_pct"))
            water_saturation = _first_filled(sarh_row.get("swt_pct"))
        else:
            p50_pay = _first_filled(fields.get("quicklook_pay_thickness_ft"))
            p50_porosity = _first_filled(fields.get("quicklook_average_porosity_pct"))
            water_saturation = _first_filled(fields.get("quicklook_average_swt_pct"))
        # Undrilled leads have no formation row or quicklook read yet: the
        # Thickness Estimation step's reservoir thickness is their latest
        # available pay estimate.
        p50_pay = p50_pay or _first_filled(fields.get("reservoir_thickness_ft"))

        rows.append({
            "Well Name": item["project_name"],
            "BP Year": item.get("year") or "",
            "Classification": classification,
            "Field": field_name,
            "Seismic Block": seismic_block,
            "Status": status,
            "Dynamic Mean (BCF)": _first_filled(fields.get("flowback_dynamic_ogip_bcf")),
            "Booked": booked,
            "OGIP P90 (BCF)": ogip_p90,
            "OGIP Mean (BCF)": ogip_mean,
            "OGIP P10 (BCF)": ogip_p10,
            "Condensate P90 (MMSTB)": condensate_p90,
            "Condensate Mean (MMSTB)": condensate_mean,
            "Condensate P10 (MMSTB)": condensate_p10,
            "Pull-up": pull_up,
            "Amplitude Ratio": amplitude_ratio,
            "BTS": bts,
            "Reservoir CoS (%)": reservoir_cos_pct,
            "P90 Area (km2)": _first_filled(fields.get("p90_area_km2")),
            "P10 Area (km2)": _first_filled(fields.get("p10_area_km2")),
            "SARH Formation Thickness (ft)": _first_filled(fields.get("formation_thickness_ft")),
            "P50 Pay Thickness (ft)": p50_pay,
            "P50 Porosity (%)": p50_porosity,
            "Water Saturation (%)": water_saturation,
            "SARH-QWRH Thickness (ft)": _first_filled(fields.get("sarah_quwarah_thickness_ft")),
            "Trap CoS (%)": _first_filled(fields.get("trap_cos_pct")),
            "Most Recent Age of Fault": _first_filled(fields.get("seal_recent_activity_age")),
            "Dip": _first_filled(fields.get("seal_dip")),
            "Azimuth vs SHmax": _first_filled(fields.get("seal_azimuth_vs_shmax")),
            "Fault LoC": _first_filled(fields.get("seal_fault_level_confidence")),
            "FPPM": _first_filled(fields.get("seal_fracture_permeability")),
            "Seal CoS (%)": _first_filled(fields.get("seal_cos_pct")),
            "Pore Pressure Gradient (psi/ft)": _first_filled(fields.get("seal_pore_pressure_gradient_psi_ft")),
            "Gas Rate (MMSCFD)": _first_filled(fields.get("flowback_gas_rate_mmscfd")),
            "Water Rate (BWPD)": _first_filled(fields.get("flowback_water_rate_bwpd")),
            "Choke Size (in)": _first_filled(fields.get("flowback_choke_size_in")),
            "WHP (psi)": _first_filled(fields.get("flowback_fwhp_psi")),
        })
    return rows


def get_staking_export_rows(session) -> List[dict]:
    """One row per mature lead (business_plan_enabled == 0 Portfolio members).

    X/Y prefer the 'Staking Moving Tolerance' step's own staking_well_x/y
    (once a user has moved/confirmed a location) and fall back to the
    project's lead_x/lead_y (the pre-fill source, req 5) when the step has
    never been touched. Batches one EAV query (the 8 staking keys) and one
    extra projects query for lead_x/lead_y (reporting._portfolio_projects is
    left untouched, per WS4's file ownership).
    """
    projects = [item for item in reporting._portfolio_projects(session)
                if int(item.get("business_plan_enabled") or 0) == 0]
    project_ids = [item["project_id"] for item in projects]
    task_fields = _task_fields(session, project_ids, _STAKING_TASK_FIELD_KEYS)
    lead_xy = _project_lead_xy(session, project_ids)

    rows: List[dict] = []
    for item in projects:
        fields = task_fields.get(item["project_id"], {})
        xy = lead_xy.get(item["project_id"], {})
        rows.append({
            "Lead Name": item["project_name"],
            "X": _first_filled(fields.get("staking_well_x"), xy.get("lead_x")),
            "Y": _first_filled(fields.get("staking_well_y"), xy.get("lead_y")),
            "Option 1 Max Distance (m)": _first_filled(fields.get("staking_opt1_max_distance_m")),
            "Option 1 Azimuth (deg)": _first_filled(fields.get("staking_opt1_azimuth_deg")),
            "Option 2 Max Distance (m)": _first_filled(fields.get("staking_opt2_max_distance_m")),
            "Option 2 Azimuth (deg)": _first_filled(fields.get("staking_opt2_azimuth_deg")),
            "Option 3 Max Distance (m)": _first_filled(fields.get("staking_opt3_max_distance_m")),
            "Option 3 Azimuth (deg)": _first_filled(fields.get("staking_opt3_azimuth_deg")),
        })
    return rows
