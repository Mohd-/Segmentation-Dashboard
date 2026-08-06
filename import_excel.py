"""Excel importer for the 43-column "Portfolio Export" sheet -- the inverse of
``portfolio_export.get_portfolio_export_rows``.

What this does
--------------
Ingests external well/lead data shaped exactly like the app's Portfolio Export
sheet: for each row it creates (or, with ``--update``, upserts) a project, fills
the mapped step inputs through the REAL domain functions (so the audit trail,
server-side recomputes and the lead-summary snapshot behave exactly as if a
human had driven the UI), walks each data step through the state machine to
Approved, and places the record in the right pipeline (proposed lead / mature
lead / BP well / historical well).

Why it drives the domain layer (never raw SQL for anything the layer exposes)
-----------------------------------------------------------------------------
Mirrors seed_dev.py: every write goes through ``workflow.add_project``,
``save_task_dynamic_fields``, ``upsert_project_formations``, ``assign_task``,
``transition_task`` and ``update_project_flags``. That keeps derived state
(board pointers, Total CoS, Portfolio composition, ``completed_at``, the
promotion snapshot) and the ``task_history`` audit trail as consistent as the
live app. ``changed_by`` is ``"External Import"`` on every write. The one
sanctioned direct-SQL exception is the idempotent import-user seed
(``INSERT OR IGNORE`` on the UNIQUE ``users.name``), mirroring
seed_dev._seed_extra_users / migrations' base-data idiom.

Column contract
---------------
``portfolio_export.PORTFOLIO_EXPORT_COLUMNS`` is the single source of truth for
the column set (imported, never re-listed here). Only "Well Name" is required.
Header detection and column matching are case-insensitive (casefold), with
matched headers mapped back to the canonical column names.

Which column is the record's IDENTITY (Card 3V)
-----------------------------------------------
A record has two names: ``projects.project_name`` -- the lead name, the row
this application stores -- and the name it is KNOWN BY, which becomes the
staked well name once staking is confirmed. The export writes the second as
"Well Name" and carries the first alongside as "Lead Name".

So **"Lead Name" is the identity** whenever the sheet has one: matching on
"Well Name" would fail to find a staked record and create a SECOND record for
a well already here. A sheet without that column (a hand-made one) is
unaffected -- its "Well Name" is the only name it has. When the two differ,
the well name is recorded as the record's staked name, which becomes its
canonical name under the app's own confirmation rule, never by import alone.

Flagged assumptions (documented, cheap to change)
-------------------------------------------------
1. OGIP/Condensate trio destination step, by (record type, fluid presence):
   - proposed / mature lead            -> 'Lead Assessment' (lead_piip_*)
   - bp / historical WITHOUT a fluid   -> 'Pre-Drilling GeoX Assessment' (pre_drill_piip_*)
   - any record WITH a fluid status    -> 'SAD Update' (resource_update_*)
     (v4 merged the old 'Resource Assessment Update' step into 'SAD Update',
     which kept the resource_update_* keys verbatim.)
   (The plan states the lead / bp-without-fluid / with-fluid cases explicitly;
   a *historical* well without a fluid status is not separately specified, so it
   is grouped with the bp-without-fluid case -> pre_drill_piip.)
2. SARH formation phase for the P50 Pay/Porosity/Swt + fluid row: 'final' for a
   record that carries a fluid status (a drilled well), else 'quicklook'. The
   owning step used as ``source_task_id`` follows: 'Final Log Analysis' for
   final, 'Quicklook Logs' for quicklook.
3. Dry-run: the domain layer owns its own commits (``db.write_transaction``), so
   an in-process rollback of already-committed work is not available. ``--dry-run``
   therefore runs the identical flow against a throwaway *copy* of the target
   database and discards it -- the same observable contract as a rollback (zero
   effect on the real DB, identical report), while keeping ``import_rows`` honest
   (it always drives the real domain functions and is what the tests call).
   The real file is never opened, created or migrated by a dry-run: when it does
   not exist yet, the dry-run simply runs against a fresh empty database.

CLI
---
    SEGMENT_TRACKER_DB_PATH=... .venv/bin/python import_excel.py sheet.xlsx [--update] [--dry-run]

Structured as pure functions the tests drive directly: ``parse_workbook``,
``classify_row``, ``import_rows``.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import openpyxl

import config
import db
import workflow
from helpers import utc_now_str
from portfolio_export import PORTFOLIO_EXPORT_COLUMNS

# The actor stamped on every write, seeded as an active supervisor so it can
# assign and approve through the real lifecycle.
IMPORT_USER = "External Import"

# The non-empty fluid vocabulary (schema.js FLUID_TYPES, minus the blank
# option), matched case-insensitively; the canonical casing is what gets
# stored. External sheets also write "Tight" (or "Dry/Tight") for a dry well
# and may spell Gas over Water with a slash -- those alias to the canonical
# values instead of erroring the row. The pre-v10 labels (Dry/Water/Condensate/
# Liquid) alias FORWARD onto their replacements, exactly as migration v10 maps
# stored rows, so an old sheet imports as current vocabulary rather than
# reintroducing retired values.
_FLUID_CANONICAL = {name.lower(): name for name in
                    ("Gas", "Gas over Water", "Water Bearing", "Dry Hole", "Oil",
                     "Oil over Gas", "Oil over Water")}
_FLUID_CANONICAL.update({
    "dry": "Dry Hole", "tight": "Dry Hole", "dry/tight": "Dry Hole",
    "dry / tight": "Dry Hole",
    "water": "Water Bearing",
    "condensate": "Oil over Gas", "liquid": "Oil",
    "gas/water": "Gas over Water", "gas / water": "Gas over Water",
})

# Canonical select vocabularies (schema.js) for cells that feed <select> inputs:
# matched case-insensitively so "yes"/"EXPLORATION" render selected in the UI;
# an unknown token is stored as-is with a cell warning.
_PULL_UP_CANONICAL = {name.lower(): name for name in ("No", "Semi", "Yes")}
_CLASSIFICATION_CANONICAL = {name.lower(): name for name in
                             ("Development", "Appraisal", "Exploration")}

# Booked cells that mean "booked": the export writes Yes/No, but a hand-made
# sheet (or an openpyxl boolean, which _cell_str renders "True") may carry
# TRUE/true/1. Anything else (No/blank/...) writes nothing.
_BOOKED_TRUTHY = {"yes", "true", "1"}

# The Seal CoS form inputs. Saving ANY of these fires lifecycle.py's server-side
# recompute (_SEAL_COS_INPUT_KEYS guard) which overwrites seal_cos_pct -- so a
# sheet-supplied "Seal CoS (%)" that disagrees needs a second, inputs-free save
# to win. Kept as a local copy (this module never imports another module's
# private list); it must track lifecycle._SEAL_COS_INPUT_KEYS.
_SEAL_INPUT_KEYS = (
    "seal_recent_activity_age", "seal_dip", "seal_azimuth_vs_shmax",
    "seal_fault_level_confidence", "seal_fracture_permeability",
)

# The step that owns BOTH CoS halves since v5 (they used to be two steps,
# "Trap CoS" and "Seal CoS", now retired). Named once so the Trap/Seal writes
# below read as what they are: several saves against one component.
_COS_STEP = workflow.MERGED_COS_TASK_NAME

# v7 merged Area Definition / Thickness Estimation / GRV Inputs / Resource
# Assessment into one active row. Their EAV keys stay unchanged and now share
# this owner, so every lead import writes the consolidated workspace directly.
_LEAD_ASSESSMENT_STEP = "Lead Assessment"

# The per-stage measurement keys of the flowback_stages_rows mini-sheet
# (schema.js FLOWBACK_STAGE_COLUMNS); must track portfolio_export's own
# _FLOWBACK_STAGE_KEYS so this module's primary-stage predicate agrees with
# the export's.
_FLOWBACK_STAGE_KEYS = (
    "flowback_gas_rate_mmscfd", "flowback_water_rate_bwpd",
    "flowback_liquid_rate_bpd", "flowback_choke_size_in", "flowback_fwhp_psi",
)


# ---------------------------------------------------------------------------
# Cell / value normalization
# ---------------------------------------------------------------------------

def _cell_str(value) -> str:
    """Normalize any openpyxl cell value to a stripped string.

    Numeric cells arrive as ``float`` from openpyxl (e.g. 2027.0 for a year);
    integer-valued floats collapse to their int form ("2027") so year parsing
    and percentage round-trips are clean, while genuine decimals keep their
    fractional part ("0.5")."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)
    if isinstance(value, int):
        return str(value)
    return str(value).strip()


def _text(row: Dict[str, str], col: str) -> str:
    """Stripped string for a column, '' when absent/None."""
    value = row.get(col, "")
    return "" if value is None else str(value).strip()


def _to_int(text: str) -> Optional[int]:
    """Parse an integer year, tolerating a float form ("2027.0"); None if the
    value is non-numeric or has a fractional part."""
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    return int(number) if number == int(number) else None


def _num(row: Dict[str, str], col: str, warnings: List[str]) -> Optional[str]:
    """Return a numeric column's value as a validated string, or None.

    Blank -> None (cell simply omitted). Non-numeric -> a cell-level warning and
    None (cell skipped, the rest of the row still lands). The string form is kept
    as-is for the EAV store (which stringifies everything anyway)."""
    raw = _text(row, col)
    if not raw:
        return None
    try:
        float(raw)
    except ValueError:
        warnings.append(f"non-numeric value in '{col}': {raw!r} (cell skipped)")
        return None
    return raw


def _num_nonzero(row: Dict[str, str], col: str, warnings: List[str]) -> Optional[str]:
    """Like :func:`_num`, but a value that parses to exactly 0 is skipped -- the
    export writes '0' as its blank Condensate default, so it must not round-trip
    into a stored zero."""
    value = _num(row, col, warnings)
    if value is None or float(value) == 0.0:
        return None
    return value


def _norm_num(text) -> Optional[float]:
    """Parse a value as a float for tolerant equality checks ("60" == "60.0"),
    None when it does not parse."""
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _canon_select(raw: str, canonical: Dict[str, str], col: str,
                  warnings: List[str]) -> str:
    """Map a select-backed cell to its canonical casing (case-insensitive); an
    unknown token is stored as-is with a cell warning so the operator knows the
    UI's <select> will not show it as chosen."""
    match = canonical.get(raw.lower())
    if match is not None:
        return match
    warnings.append(f"'{col}' value {raw!r} is not one of "
                    f"{'/'.join(canonical.values())} (stored as-is)")
    return raw


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _find_header(all_rows) -> Tuple[Optional[int], int]:
    """Locate a sheet's header row and score how well it matches the contract.

    Returns ``(header_index, score)``: the first row within the sheet's first
    ~10 holding a cell equal to "Well Name" (case-insensitive), and how many
    PORTFOLIO_EXPORT_COLUMNS names that row matches. The score disambiguates
    sheets that merely CONTAIN a Well Name column (our export's "Wells
    Overview" tab does) from the actual well-data sheet. ``(None, 0)`` when no
    header row is found."""
    canonical = {col.casefold() for col in PORTFOLIO_EXPORT_COLUMNS}
    for index, raw_row in enumerate(all_rows[:10]):
        names = {_cell_str(cell).casefold() for cell in raw_row}
        if "well name" in names:
            return index, len(names & canonical)
    return None, 0


def parse_workbook(path) -> List[Dict[str, str]]:
    """Read the well-data worksheet into a list of ``{column: stripped_str}`` rows.

    Worksheet autodetect: every sheet is scored by how many
    PORTFOLIO_EXPORT_COLUMNS its header row matches and the best one wins
    (ties -> first), so both a single-sheet hand-made file and our own
    multi-sheet export import without naming a sheet -- in the export,
    "Portfolio Export" (41 matches) beats "Wells Overview", whose header also
    contains a Well Name column. Header autodetect within a sheet: the first
    cell equal to "Well Name" (case-insensitive) within the first ~10 rows
    marks the header row (our own export writes headers at Excel row 4 /
    startrow=3; hand-made sheets use row 1). Columns are matched
    case-insensitively by header text against PORTFOLIO_EXPORT_COLUMNS and
    mapped back to the canonical names; unknown extra columns are warned about
    and ignored, missing columns default to ''. Fully-blank rows are skipped."""
    workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        best_rows: List[tuple] = []
        header_index = None
        best_score = 0
        for sheet in workbook.worksheets:
            sheet_rows = [tuple(r) for r in sheet.iter_rows(values_only=True)]
            index, score = _find_header(sheet_rows)
            if index is not None and score > best_score:
                best_rows, header_index, best_score = sheet_rows, index, score
    finally:
        workbook.close()
    if header_index is None:
        raise ValueError("No worksheet with a 'Well Name' header row found "
                         "(searched the first 10 rows of every sheet).")
    all_rows = best_rows

    canonical_by_fold = {col.casefold(): col for col in PORTFOLIO_EXPORT_COLUMNS}
    col_by_index: Dict[int, str] = {}
    unknown: List[str] = []
    for col_index, cell in enumerate(all_rows[header_index]):
        name = _cell_str(cell)
        if not name:
            continue
        canonical = canonical_by_fold.get(name.casefold())
        if canonical is not None:
            col_by_index[col_index] = canonical
        else:
            unknown.append(name)
    if unknown:
        print(f"Warning: ignoring unknown column(s): {', '.join(unknown)}", file=sys.stderr)

    rows: List[Dict[str, str]] = []
    for raw_row in all_rows[header_index + 1:]:
        if not any(_cell_str(cell) for cell in raw_row):
            continue  # fully-blank spacer row
        row = {col_by_index[i]: _cell_str(raw_row[i])
               for i in col_by_index if i < len(raw_row)}
        for col in PORTFOLIO_EXPORT_COLUMNS:
            row.setdefault(col, "")  # missing columns -> blank
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Record-type inference
# ---------------------------------------------------------------------------

def _analyze(row: Dict[str, str]) -> Tuple[Optional[str], List[str], Optional[int], str]:
    """Single source of record-type inference + row-local validation.

    Returns ``(record_type, errors, year, fluid)``. ``record_type`` is one of
    'proposed' / 'mature' / 'bp' / 'historical', or None when ``errors`` is
    non-empty. ``fluid`` is the canonical fluid casing ('' if the Status is not a
    fluid). Only checks derivable from the row alone live here; duplicate-name
    and already-in-DB checks need cross-row / DB context and live in
    :func:`import_rows`."""
    errors: List[str] = []
    name = _text(row, "Well Name")
    if not name:
        errors.append("blank Well Name")

    # Status token: a fluid, "Proposed"/"Staked", blank, or unknown (an error).
    status_raw = _text(row, "Status")
    fluid = ""
    status_kind = "blank"
    if status_raw:
        low = status_raw.lower()
        if low in _FLUID_CANONICAL:
            fluid, status_kind = _FLUID_CANONICAL[low], "fluid"
        elif low == "proposed":
            status_kind = "proposed"
        elif low == "staked":
            status_kind = "staked"
        else:
            status_kind = "unknown"
            errors.append(f"unknown Status token {status_raw!r}")

    # BP Year: optional; when present it must be an integer in 1990-2040 (the
    # promotion guard's window). Rejecting an out-of-range year HERE keeps the
    # row from half-importing: update_project_flags would only refuse it after
    # all the prospect writes/approvals had already committed.
    year: Optional[int] = None
    year_raw = _text(row, "BP Year")
    if year_raw:
        parsed = _to_int(year_raw)
        if parsed is None:
            errors.append(f"BP Year is not an integer: {year_raw!r}")
        elif parsed > 2040:
            errors.append(f"BP Year {parsed} is after 2040")
        elif parsed < 1990:
            errors.append(f"BP Year {parsed} is before 1990")
        else:
            year = parsed

    # A drilled (fluid) well needs a BP Year. Only flag the missing-year case
    # when no year was supplied at all -- an invalid/out-of-range year is
    # already reported above and would double-count here.
    if status_kind == "fluid" and year is None and not year_raw:
        errors.append("a drilled well needs a BP Year")

    if errors:
        return None, errors, year, fluid

    if year is not None:
        record_type = "historical" if year < 2026 else "bp"
    elif status_kind == "staked":
        record_type = "mature"
    else:  # "proposed" or blank
        record_type = "proposed"
    return record_type, errors, year, fluid


def classify_row(row: Dict[str, str]) -> Tuple[Optional[str], List[str]]:
    """Public record-type classifier: ``(record_type, errors)`` for one row."""
    record_type, errors, _year, _fluid = _analyze(row)
    return record_type, errors


# ---------------------------------------------------------------------------
# Payload builders (only non-blank cells are ever included)
# ---------------------------------------------------------------------------

def _reservoir_contribution(row: Dict[str, str], warnings: List[str]) -> dict:
    """The sheet's Reservoir-CoS-row contribution: only the non-blank cells,
    keyed by the reservoir_cos_rows row keys. {} when the sheet has nothing.
    A blank AR Number cell is never contributed (an update must not blank a
    stored AR); a filled one is cross-checked against the seismic block map --
    the export DERIVES its Seismic Block column from the AR, so a sheet block
    that contradicts the AR's mapped block would silently flip on round-trip."""
    payload: dict = {}
    block = _text(row, "Seismic Block")
    if block:
        payload["seismic_block"] = block
    ar_number = _text(row, "AR Number")
    if ar_number:
        payload["seismic_volume_ar_number"] = ar_number
        mapped_block = config.AR_TO_SEISMIC_BLOCK.get(ar_number)
        if block and mapped_block and mapped_block != block:
            warnings.append(f"Seismic Block {block!r} does not match AR "
                            f"{ar_number}'s block {mapped_block!r} (the export "
                            f"derives the block from the AR, so it will show "
                            f"{mapped_block!r})")
    for col, key in (("Amplitude Ratio", "amplitude_ratio"),
                     ("BTS", "base_tight_sarah"),
                     ("Reservoir CoS (%)", "reservoir_cos_pct")):
        value = _num(row, col, warnings)
        if value is not None:
            payload[key] = value
    pull_up = _text(row, "Pull-up")
    if pull_up:
        payload["pull_up"] = _canon_select(pull_up, _PULL_UP_CANONICAL, "Pull-up", warnings)
    return payload


def _reservoir_primary(json_row: dict) -> bool:
    """The export's primary-row predicate (portfolio_export
    ._parse_reservoir_cos_primary_row): pct OR AR non-blank."""
    return bool(str(json_row.get("reservoir_cos_pct") or "").strip()
                or str(json_row.get("seismic_volume_ar_number") or "").strip())


def _flowback_primary(json_row: dict) -> bool:
    """The export's primary-stage predicate: any measurement non-blank."""
    return any(str(json_row.get(key) or "").strip() for key in _FLOWBACK_STAGE_KEYS)


def _merge_primary_json_row(stored_json, contribution: dict,
                            is_primary: Callable[[dict], bool]) -> Optional[List[dict]]:
    """Merge a sheet contribution into the PRIMARY row of a stored JSON blob.

    The primary row is the first row satisfying ``is_primary`` (the export's own
    predicate, so the merge lands on the row the export/Portfolio actually
    reads), else row 0. Non-blank sheet cells win key-by-key; every other key
    and every sibling row/stage is preserved. Returns the merged row list, or
    None when the stored blob is absent/malformed/empty -- the caller then
    builds a fresh single-row list (the create path)."""
    if not stored_json:
        return None
    try:
        rows = json.loads(stored_json)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(rows, list) or not rows:
        return None
    index = next((i for i, r in enumerate(rows) if is_primary(r or {})), 0)
    merged = dict(rows[index] or {})
    merged.update(contribution)
    rows[index] = merged
    return rows


def _piip_trio(row: Dict[str, str], prefix: str, warnings: List[str]) -> dict:
    """The OGIP (gas) + Condensate (liquid) assessment payload under ``prefix``.

    Blank/zero Condensate cells are skipped; ``<prefix>_has_liquid`` is set only
    when at least one non-zero liquid value made it in. A P90/P10 without a Mean
    draws a warning: the export's source-prefix scan keys on ``_gas_mean``, so a
    mean-less trio is stored but exports blank."""
    payload: dict = {}
    for col, suffix in (("OGIP P90 (BCF)", "gas_p90"),
                        ("OGIP Mean (BCF)", "gas_mean"),
                        ("OGIP P10 (BCF)", "gas_p10")):
        value = _num(row, col, warnings)
        if value is not None:
            payload[f"{prefix}_{suffix}"] = value
    if f"{prefix}_gas_mean" not in payload and (
            f"{prefix}_gas_p90" in payload or f"{prefix}_gas_p10" in payload):
        warnings.append("OGIP P90/P10 present without 'OGIP Mean (BCF)': the export's "
                        "assessment scan keys on the mean, so the trio will export blank")
    has_liquid = False
    for col, suffix in (("Condensate P90 (MMSTB)", "liquid_p90"),
                        ("Condensate Mean (MMSTB)", "liquid_mean"),
                        ("Condensate P10 (MMSTB)", "liquid_p10")):
        value = _num_nonzero(row, col, warnings)
        if value is not None:
            payload[f"{prefix}_{suffix}"] = value
            has_liquid = True
    if has_liquid:
        payload[f"{prefix}_has_liquid"] = "1"
    return payload


def _flowback_contribution(row: Dict[str, str], warnings: List[str]) -> Tuple[Optional[str], dict]:
    """(dynamic OGIP scalar or None, per-stage measurement contribution dict).

    The stage dict uses the flowback_stages_rows row keys (stage #1 is the
    primary read everywhere) and carries only the non-blank cells."""
    ogip = _num(row, "Dynamic Mean (BCF)", warnings)
    stage: dict = {}
    for col, key in (("Gas Rate (MMSCFD)", "flowback_gas_rate_mmscfd"),
                     ("Water Rate (BWPD)", "flowback_water_rate_bwpd"),
                     ("Condensate Rate (BPD)", "flowback_liquid_rate_bpd"),
                     ("Choke Size (in)", "flowback_choke_size_in"),
                     ("WHP (psi)", "flowback_fwhp_psi")):
        cell = _num(row, col, warnings)
        if cell is not None:
            stage[key] = cell
    return ogip, stage


def _sarh_values(row: Dict[str, str], fluid: str, warnings: List[str]) -> dict:
    """The SARH project_formations row's new values from the sheet: P50 Pay /
    Porosity / Swt (only non-blank) plus ``fluid`` when the record is drilled."""
    values: dict = {}
    for col, key in (("P50 Pay Thickness (ft)", "pay_ft"),
                     ("P50 Porosity (%)", "porosity_pct"),
                     ("Water Saturation (%)", "swt_pct")):
        value = _num(row, col, warnings)
        if value is not None:
            values[key] = value
    if fluid:
        values["fluid"] = fluid
    return values


def _merge_sarh_rows(existing_phase_rows: List[dict], new_sarh: dict) -> List[dict]:
    """Build the full-phase replacement row set for upsert_project_formations,
    merging the new SARH values into any stored SARH row (new keys override,
    stored sibling values and other formations are preserved -- upsert is a
    full-phase replace, so omitting them would delete them)."""
    rows: List[dict] = []
    found_sarh = False
    for stored in existing_phase_rows:
        row = {"formation": stored["formation"]}
        for key in workflow.FORMATION_VALUE_FIELDS:
            value = stored.get(key)
            if value is not None and str(value).strip() != "":
                row[key] = value
        if stored["formation"] == "SARH":
            found_sarh = True
            row.update(new_sarh)
        rows.append(row)
    if not found_sarh:
        rows.append(dict(new_sarh, formation="SARH"))
    return rows


# ---------------------------------------------------------------------------
# Lifecycle helpers
# ---------------------------------------------------------------------------

def _ensure_import_user(session) -> None:
    """Idempotently seed IMPORT_USER as an active supervisor (the one sanctioned
    direct-SQL write, mirroring seed_dev._seed_extra_users). Note: INSERT OR
    IGNORE cannot resurrect a deactivated row -- if "External Import" is ever
    set is_active = 0, it needs manual reactivation before importing again."""
    with db.write_transaction(session):
        db.execute(session, """
            INSERT OR IGNORE INTO users (name, role, created_at)
            VALUES (:name, :role, :now)
        """, {"name": IMPORT_USER, "role": "supervisor", "now": utc_now_str()})


def _ensure_approved(session, task_id) -> None:
    """Drive one task to Approved as IMPORT_USER, WALKING the state machine.

    Thin alias for ``workflow.ensure_task_approved`` -- the shared walk the
    domain layer owns (Not Assigned -> assign -> submit -> approve, resuming
    from wherever the task is, already-Approved a no-op, so on --update this
    only ever ADDS approvals). It also satisfies a step's submit gate
    (workflow.REQUIRED_FIELDS_FOR_SUBMIT -- today "SAD Update") through the
    normal audited field-save path: an imported historical well IS complete by
    definition, so the sign-off is RECORDED rather than the gate bypassed.
    """
    workflow.ensure_task_approved(session, task_id, IMPORT_USER)


def _save(session, task_id, fields, data_bearing: set) -> None:
    """save_task_dynamic_fields wrapper that records the step as data-bearing."""
    # reconcile=False: bulk writers drive statuses explicitly via
    # ensure_task_approved, the engine must not fight them.
    workflow.save_task_dynamic_fields(session, task_id, fields,
                                      changed_by=IMPORT_USER, reconcile=False)
    data_bearing.add(task_id)


# ---------------------------------------------------------------------------
# Per-record placement (mirrors seed_dev's write order)
# ---------------------------------------------------------------------------

def _import_record(session, row, record_type, year, fluid, pid, is_update):
    """Fill, approve and place ONE record. Returns ``(warnings, notes)``.

    ``pid`` is a freshly-created project (create) or the resolved existing one
    (update). The write order mirrors seed_dev and the plan:
      1. prospect-step fields + the quicklook SARH formation row,
      2. approve prospect steps,
      3. bp/historical only: promote (snapshot captures prospect data), then
         BP-step fields + the final SARH formation row, then approve BP steps.

    On ``is_update``, the JSON mini-sheets (reservoir_cos_rows,
    flowback_stages_rows) MERGE the sheet's contribution into the stored blob's
    primary row/stage (non-blank cells win, sibling rows/stages and in-row keys
    preserved) instead of replacing it, matching the formations merge.
    """
    warnings: List[str] = []
    notes: List[str] = []
    data_bearing: set = set()
    has_fluid = bool(fluid)

    all_tasks = workflow.get_project_tasks(session, pid)
    by_name = {t["task_name"]: t for t in all_tasks}
    tid = lambda name: by_name[name]["task_id"]
    prospect_tasks = [t for t in all_tasks if t["stage_group"] in workflow.PROSPECT_STAGES]
    bp_tasks = [t for t in all_tasks if t["stage_group"] in workflow.BP_EXECUTION_STAGES]

    # Trio destination step by record type + fluid presence (flagged assumption 1).
    if has_fluid:
        prefix, trio_step = "resource_update", "SAD Update"
    elif record_type in ("bp", "historical"):
        prefix, trio_step = "pre_drill_piip", "Pre-Drilling GeoX Assessment"
    else:
        prefix, trio_step = "lead_piip", _LEAD_ASSESSMENT_STEP

    # Lead rows have no BP phase, so BP-only cells would vanish silently: name
    # the ignored columns instead. Booked counts only when truthy -- the export
    # writes 'No' as every lead's default, so a round-tripped sheet stays quiet.
    if record_type in ("proposed", "mature"):
        ignored = [col for col in ("Classification", "Dynamic Mean (BCF)",
                                   "Gas Rate (MMSCFD)", "Water Rate (BWPD)",
                                   "Condensate Rate (BPD)", "Choke Size (in)",
                                   "WHP (psi)") if _text(row, col)]
        if _text(row, "Booked").lower() in _BOOKED_TRUTHY:
            ignored.append("Booked")
        if ignored:
            warnings.append("BP-only column(s) ignored on a lead row: " + ", ".join(ignored))

    # ---- 1. Prospect-step fields -------------------------------------------
    area = {}
    for col, key in (("P90 Area (km2)", "p90_area_km2"), ("P10 Area (km2)", "p10_area_km2")):
        value = _num(row, col, warnings)
        if value is not None:
            area[key] = value
    if area:
        _save(session, tid(_LEAD_ASSESSMENT_STEP), area, data_bearing)

    thickness = _num(row, "SARH Formation Thickness (ft)", warnings)
    if thickness is not None:
        _save(session, tid(_LEAD_ASSESSMENT_STEP), {"formation_thickness_ft": thickness}, data_bearing)

    reservoir_contribution = _reservoir_contribution(row, warnings)
    if reservoir_contribution:
        stored_blob = (workflow.get_task_dynamic_fields(session, tid("Reservoir CoS"))
                       .get("reservoir_cos_rows") if is_update else None)
        rows_list = _merge_primary_json_row(stored_blob, reservoir_contribution, _reservoir_primary)
        if rows_list is None:
            # Fresh single-row list; the import has no AR number, so it is ''.
            rows_list = [dict({"seismic_volume_ar_number": ""}, **reservoir_contribution)]
        # The export/Portfolio only read a row whose pct or AR is non-blank: a
        # blob without one stores fine but stays invisible to both.
        if not any(_reservoir_primary(r or {}) for r in rows_list):
            warnings.append("Reservoir CoS data stored but not export-visible: no "
                            "'Reservoir CoS (%)' (and no AR number) on any row")
        _save(session, tid("Reservoir CoS"),
              {"reservoir_cos_rows": json.dumps(rows_list, separators=(",", ":"))},
              data_bearing)

    trap = {}
    value = _num(row, "SARH-QWRH Thickness (ft)", warnings)
    if value is not None:
        trap["sarah_quwarah_thickness_ft"] = value
    value = _num(row, "Trap CoS (%)", warnings)
    if value is not None:
        trap["trap_cos_pct"] = value
    if trap:
        _save(session, tid(_COS_STEP), trap, data_bearing)

    # Seal CoS: inputs first (their save fires the server-side recompute), then
    # a pct-only override when the sheet disagrees. Since v5 the Trap and Seal
    # halves share ONE step (_COS_STEP) and therefore one task_id; the two
    # saves stay separate because the Seal half has its own recompute /
    # incomplete-inputs retry contract.
    seal = {}
    for col, key in (("Most Recent Age of Fault", "seal_recent_activity_age"),
                     ("Dip", "seal_dip"),
                     ("Azimuth vs SHmax", "seal_azimuth_vs_shmax"),
                     ("Fault LoC", "seal_fault_level_confidence"),
                     ("FPPM", "seal_fracture_permeability")):
        value = _num(row, col, warnings)
        if value is not None:
            seal[key] = value
    pore = _num(row, "Pore Pressure Gradient (psi/ft)", warnings)
    if pore is not None:
        seal["seal_pore_pressure_gradient_psi_ft"] = pore  # not a recompute input; rides along
    sheet_seal_pct = _num(row, "Seal CoS (%)", warnings)
    has_seal_inputs = any(key in seal for key in _SEAL_INPUT_KEYS)
    if seal:
        try:
            _save(session, tid(_COS_STEP), seal, data_bearing)
        except ValueError as exc:
            # cos.calculate_seal_cos rejects an INCOMPLETE input set (some but
            # not all of the required keys). The recompute raises before any
            # write, inside its own transaction, so the retry starts clean.
            # Dropping the recompute-input keys keeps the non-input riders
            # (pore pressure) and lets the sheet's pct land via the inputs-free
            # path below -- without re-implementing cos.py's completeness rule.
            skipped = [key for key in _SEAL_INPUT_KEYS if key in seal]
            warnings.append(f"incomplete Seal CoS inputs skipped "
                            f"({', '.join(skipped)}): {exc}")
            retry = {key: val for key, val in seal.items() if key not in _SEAL_INPUT_KEYS}
            if retry:
                _save(session, tid(_COS_STEP), retry, data_bearing)
            has_seal_inputs = False
    if sheet_seal_pct is not None:
        if has_seal_inputs:
            stored = workflow.get_task_dynamic_fields(session, tid(_COS_STEP)).get("seal_cos_pct", "")
            if _norm_num(stored) != _norm_num(sheet_seal_pct):
                # Inputs-free save -> no recompute -> the sheet value wins.
                _save(session, tid(_COS_STEP), {"seal_cos_pct": sheet_seal_pct}, data_bearing)
                notes.append(f"Seal CoS (%): sheet value {sheet_seal_pct} pinned over recomputed {stored!r}")
        else:
            _save(session, tid(_COS_STEP), {"seal_cos_pct": sheet_seal_pct}, data_bearing)

    if prefix in ("lead_piip", "pre_drill_piip"):
        trio = _piip_trio(row, prefix, warnings)
        if trio:
            _save(session, tid(trio_step), trio, data_bearing)

    # Undrilled records place the SARH P50 row at the 'quicklook' phase.
    if not has_fluid:
        sarh = _sarh_values(row, fluid, warnings)
        if sarh:
            existing = [r for r in workflow.get_project_formations(session, pid) if r["phase"] == "quicklook"]
            workflow.upsert_project_formations(
                session, pid, "quicklook", _merge_sarh_rows(existing, sarh),
                changed_by=IMPORT_USER, source_task_id=tid("Quicklook Logs"))
            data_bearing.add(tid("Quicklook Logs"))

    # ---- 2. Approve prospect steps -----------------------------------------
    # proposed lead: only data-bearing steps (so 'Approval to Stake' stays open
    # -> status Proposed, and the lead stays on the Prospect board). mature lead
    # and bp/historical: ALL prospect steps (mature -> Staked + off the board;
    # bp/historical -> a fully-matured prospect ready to promote).
    for task in prospect_tasks:
        if record_type == "proposed" and task["task_id"] not in data_bearing:
            continue
        _ensure_approved(session, task["task_id"])

    # ---- 3. BP phase (bp / historical only) --------------------------------
    if record_type in ("bp", "historical"):
        project = workflow.get_project(session, pid) or {}
        already_enabled = int(project.get("business_plan_enabled") or 0) == 1
        stored_year = project.get("business_plan_year")
        if not already_enabled or int(stored_year or 0) != year:
            # Promote AFTER the prospect approvals so the lead-summary snapshot
            # captures the approved prospect data. Also re-fired on --update
            # when only the YEAR changed: set_business_plan handles a year-only
            # change, and it re-captures the snapshot only for a non-bp
            # pipeline_type, so an already-promoted well keeps its snapshot.
            # (Year < 2026 for historicals relies on the guard's 1990 floor --
            # allow_historical_year=True skips the promotion-only current-year
            # floor, since imports legitimately land historical BP wells.)
            workflow.update_project_flags(
                session, pid, business_plan_enabled=True, business_plan_year=year,
                changed_by=IMPORT_USER, allow_historical_year=True)

        classification = _text(row, "Classification")
        if classification:
            classification = _canon_select(classification, _CLASSIFICATION_CANONICAL,
                                           "Classification", warnings)
            _save(session, tid("BP Execution Gate"), {"bp_gate_classification": classification}, data_bearing)

        if _text(row, "Booked").lower() in _BOOKED_TRUTHY:  # No/blank -> write nothing
            _save(session, tid("PDA"), {"pda_booked": "1"}, data_bearing)

        ogip_scalar, stage_contribution = _flowback_contribution(row, warnings)
        flowback_payload: dict = {}
        if ogip_scalar is not None:
            flowback_payload["flowback_dynamic_ogip_bcf"] = ogip_scalar
        if stage_contribution:
            stored_blob = (workflow.get_task_dynamic_fields(session, tid("Flowback Results"))
                           .get("flowback_stages_rows") if is_update else None)
            stages = _merge_primary_json_row(stored_blob, stage_contribution, _flowback_primary)
            if stages is None:
                # Fresh flowback: the sole stage row carries its own formation
                # (SARH default). The --update merge path never sets this, so an
                # update cannot clobber a user-chosen per-stage formation.
                stage_contribution["flowback_formation"] = "SARH"
                stages = [stage_contribution]
            flowback_payload["flowback_stages_rows"] = json.dumps(stages, separators=(",", ":"))
        if flowback_payload:
            _save(session, tid("Flowback Results"), flowback_payload, data_bearing)

        if prefix == "resource_update":
            trio = _piip_trio(row, prefix, warnings)
            if trio:
                _save(session, tid("SAD Update"), trio, data_bearing)

        # Drilled records place the SARH P50 + fluid row at the 'final' phase.
        if has_fluid:
            sarh = _sarh_values(row, fluid, warnings)
            if sarh:
                existing = [r for r in workflow.get_project_formations(session, pid) if r["phase"] == "final"]
                workflow.upsert_project_formations(
                    session, pid, "final", _merge_sarh_rows(existing, sarh),
                    changed_by=IMPORT_USER, source_task_id=tid("Final Log Analysis"))
                data_bearing.add(tid("Final Log Analysis"))

        # bp well: only data-bearing BP steps (stays on the BP board). historical:
        # ALL BP steps (completed -> leaves the BP board under the completed-wells
        # -exit rule).
        for task in bp_tasks:
            if record_type == "bp" and task["task_id"] not in data_bearing:
                continue
            _ensure_approved(session, task["task_id"])

    return warnings, notes


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

@dataclass
class RowResult:
    well_name: str
    record_type: Optional[str]
    outcome: str  # created | updated | skipped | error
    reason: str = ""
    warnings: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


@dataclass
class ImportReport:
    results: List[RowResult] = field(default_factory=list)

    def add(self, **kwargs) -> None:
        self.results.append(RowResult(**kwargs))

    def counts(self) -> Dict[str, int]:
        """Outcome and per-record-type tallies."""
        tally: Dict[str, int] = {}
        for result in self.results:
            tally[result.outcome] = tally.get(result.outcome, 0) + 1
            if result.outcome in ("created", "updated") and result.record_type:
                tally[result.record_type] = tally.get(result.record_type, 0) + 1
        return tally

    def format(self) -> str:
        lines: List[str] = []
        for result in self.results:
            head = f"  {result.well_name or '(blank)'}: {result.outcome}"
            if result.record_type and result.outcome in ("created", "updated"):
                head += f" [{result.record_type}]"
            if result.reason:
                head += f" -- {result.reason}"
            lines.append(head)
            for warning in result.warnings:
                lines.append(f"      warning: {warning}")
            for note in result.notes:
                lines.append(f"      note: {note}")
        tally = self.counts()
        lines.append("")
        lines.append("Summary:")
        for outcome in ("created", "updated", "skipped", "error"):
            if tally.get(outcome):
                lines.append(f"  {outcome}: {tally[outcome]}")
        by_type = [f"{t}={tally[t]}" for t in ("proposed", "mature", "bp", "historical") if tally.get(t)]
        if by_type:
            lines.append("  by record type: " + ", ".join(by_type))
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _find_project(session, name):
    return db.fetch_one(session, "SELECT * FROM projects WHERE project_name = :name",
                        {"name": name})


def _record_staked_name(session, project_id, staked_name) -> str:
    """Store the sheet's well name as the record's staked name (Card 3V).

    Called only when "Well Name" and "Lead Name" DIFFER, which is exactly the
    export's way of saying "this lead was staked and is now known by that well
    name". Dropping it would round-trip a staked well back into an
    unrecognizable lead.

    What this does NOT do is assert that staking happened: the name becomes the
    record's canonical one through the app's own predicate (the Well Site
    Location letter plus stored coordinates), and this sheet carries no
    evidence of either. So the name is RECORDED and the record keeps its lead
    name until a human confirms the step -- except on a record that is already
    confirmed, where the value is simply already there.

    Returns a note when it wrote something, '' when the record already had it.
    A domain guard (a name another record answers to) raises ValueError, which
    the caller reports as a row warning rather than losing the record.
    """
    task = next((t for t in workflow.get_project_tasks(session, project_id)
                 if t["task_name"] == workflow.WELL_SITE_LOCATION_STEP), None)
    if task is None:
        return ""
    stored = workflow.get_task_dynamic_fields(session, task["task_id"]).get(
        workflow.STAKED_WELL_NAME_FIELD) or ""
    if str(stored).strip() == staked_name:
        return ""
    workflow.save_task_dynamic_fields(
        session, task["task_id"], {workflow.STAKED_WELL_NAME_FIELD: staked_name},
        changed_by=IMPORT_USER, reconcile=False)
    return (f"stored {staked_name!r} as the staked well name; the record is named by it "
            "once Well Site Location is confirmed (letter loaded + coordinates)")


def import_rows(session, rows, update=False) -> ImportReport:
    """Import a list of parsed rows; return a structured :class:`ImportReport`.

    Each row is classified, validated (blank/duplicate/unknown-token/etc.), then
    created or upserted through :func:`_import_record`. A row whose write raises
    is reported as an error and never aborts the batch. Because the domain layer
    commits per call, an unexpected mid-record failure would otherwise leave a
    committed partial project; as a safety net, a project CREATED by the failing
    row is hard-deleted again (workflow.delete_project; the schema's FK cascades
    remove its tasks/fields/history), and the error reason says whether that
    cleanup succeeded."""
    _ensure_import_user(session)
    report = ImportReport()
    seen_names: set = set()

    for row in rows:
        record_type, errors, year, fluid = _analyze(row)
        errors = list(errors)
        well_name = _text(row, "Well Name")
        # A record has TWO names since Card 3V, and only one of them is its
        # IDENTITY. "Well Name" is what the record is KNOWN BY -- the staked
        # well name once staking is confirmed -- while projects.project_name
        # (the sheet's "Lead Name") is the row this application stores and
        # matches on. Matching a staked record on its well name finds nothing
        # and creates a SECOND record for a well that is already here, so the
        # lead name is the identity whenever the sheet carries one. A
        # hand-made sheet with no "Lead Name" column is unchanged: its well
        # name is the only name it has, so it is the identity.
        lead_name = _text(row, "Lead Name")
        name = lead_name or well_name

        if name:
            if name in seen_names:
                errors.append("duplicate name within the sheet")
            seen_names.add(name)

        if errors:
            report.add(well_name=well_name, record_type=record_type, outcome="error",
                       reason="; ".join(errors))
            continue

        existing = _find_project(session, name)
        if existing is not None and not update:
            report.add(well_name=well_name, record_type=record_type, outcome="skipped",
                       reason="already exists (use --update)")
            continue

        is_update = existing is not None  # --update on a new name still creates
        created_pid = None
        # X/Y are project-level (projects.lead_x/lead_y), not step fields, so
        # they ride on the create call itself / a coordinates-only rename on
        # update. Blank cells never erase stored coordinates (rename only
        # writes a coordinate it was actually given). project_warnings collects
        # every PROJECT-level cell's complaint (X, Y, NUCD Area) so they lead
        # the row's warning list ahead of the step-field ones.
        project_warnings: List[str] = []
        lead_x = _num(row, "X", project_warnings)
        lead_y = _num(row, "Y", project_warnings)
        try:
            if is_update:
                pid = existing["project_id"]
                if lead_x is not None or lead_y is not None:
                    workflow.update_project_name(session, pid, name, changed_by=IMPORT_USER,
                                                 lead_x=lead_x, lead_y=lead_y)
            else:
                # auto_assign=False: an imported record carries its own
                # historical lifecycle state -- the creation auto-assignment
                # rules are for brand-new leads, and _ensure_approved below
                # must find steps exactly as a pre-rule creation left them
                # (Not Assigned, then walked as IMPORT_USER).
                pid = workflow.add_project(session, name, changed_by=IMPORT_USER,
                                           lead_x=lead_x, lead_y=lead_y, auto_assign=False)
                created_pid = pid
            # NUCD Area is project-level too (projects.nucd_area) and this
            # sheet is its ONLY input -- nothing in the UI writes it. A blank
            # cell never erases a stored area, matching the X/Y rule above; an
            # over-long value is reported as a cell warning rather than losing
            # the whole record over one field the sheet got wrong.
            nucd_area = _text(row, "NUCD Area")
            if nucd_area:
                try:
                    workflow.set_nucd_area(session, pid, nucd_area, changed_by=IMPORT_USER)
                except ValueError as exc:
                    project_warnings.append(f"NUCD Area not stored: {exc}")
            # Two different names on one row means the lead was staked.
            project_notes: List[str] = []
            if well_name and lead_name and well_name != lead_name:
                try:
                    staked_note = _record_staked_name(session, pid, well_name)
                    if staked_note:
                        project_notes.append(staked_note)
                except ValueError as exc:
                    project_warnings.append(f"Staked well name not stored: {exc}")
            warnings, notes = _import_record(session, row, record_type, year, fluid, pid, is_update)
            warnings = project_warnings + warnings
            notes = project_notes + notes
        except Exception as exc:  # keep the batch going; report the failure verbatim
            # Recover the session FIRST: a failure can leave it mid-transaction
            # (worst case, a commit that died partway strands it in the
            # 'prepared' state where every statement raises InvalidRequestError
            # -- including the cleanup delete below and the next row's
            # _find_project). rollback() on an already-clean session is a no-op.
            try:
                session.rollback()
            except Exception:
                pass
            reason = str(exc)
            if created_pid is not None:
                # Undo the partial create so a re-run of the corrected sheet is
                # a clean 'created', not a misleading 'skipped (exists)'.
                try:
                    workflow.delete_project(session, created_pid, changed_by=IMPORT_USER)
                    reason += " (partially created project removed)"
                except Exception:
                    reason += " (record left partially imported)"
            elif is_update:
                reason += " (record left partially imported)"
            report.add(well_name=well_name, record_type=record_type, outcome="error", reason=reason)
            continue

        # Reported under the sheet's OWN "Well Name" throughout, so a line in
        # the report is findable in the sheet that produced it.
        report.add(well_name=well_name, record_type=record_type,
                   outcome="updated" if is_update else "created",
                   warnings=warnings, notes=notes)
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _copy_db(source, dest) -> None:
    """Copy a SQLite DB file plus its WAL/SHM sidecars (if present)."""
    shutil.copy2(source, dest)
    for suffix in ("-wal", "-shm"):
        side = f"{source}{suffix}"
        if os.path.exists(side):
            shutil.copy2(side, f"{dest}{suffix}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("workbook", help="Path to the .xlsx Portfolio-Export-shaped sheet.")
    parser.add_argument("--update", action="store_true",
                        help="Upsert: update existing projects by name (blank cells never erase).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run everything against a throwaway copy of the DB and discard it.")
    args = parser.parse_args()

    rows = parse_workbook(args.workbook)
    real_path = str(config.db_path())

    tmp_path = None
    try:
        if args.dry_run:
            # The domain layer commits internally, so run against a copy and
            # throw it away -- the same observable contract as a rollback. The
            # real file is never opened/created/migrated on this path: a
            # dry-run against a not-yet-existing DB runs on a fresh empty one.
            fd, tmp_path = tempfile.mkstemp(prefix="import-dryrun-", suffix=".db")
            os.close(fd)
            if os.path.exists(real_path):
                _copy_db(real_path, tmp_path)
            # reset_for_tests() is, despite the test-flavored name, db.py's
            # only public hook for re-pointing the engine mid-process (dispose
            # + clear the bootstrap guard) -- needed in case an engine on the
            # real path already exists in this process.
            db.reset_for_tests()
            db.init_db(tmp_path)
            print(f"Target database: {real_path} (DRY RUN -- changes discarded)")
        else:
            db.init_db(real_path)
            print(f"Target database: {real_path}")

        session = db.new_session()
        try:
            report = import_rows(session, rows, update=args.update)
        finally:
            session.close()
        print(report.format())
    finally:
        if tmp_path:
            for suffix in ("", "-wal", "-shm"):
                path = f"{tmp_path}{suffix}"
                if os.path.exists(path):
                    os.remove(path)


if __name__ == "__main__":
    main()
