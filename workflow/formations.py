"""Well-level formation data (project_formations)."""
from __future__ import annotations

import db
from helpers import utc_now_str

from .constants import (
    FORMATION_NUMERIC_FIELDS,
    FORMATION_PHASES,
    FORMATION_VALUE_FIELDS,
    FORMATIONS,
)
from .history import log_task_event
from .lifecycle import get_task
from .projects import get_project


def get_project_formations(session, project_id: int):
    """Return all formation rows for a project, ordered by phase (pipeline
    order), then the canonical formation order (SARH, QASM, QWRH), then any
    custom formation names alphabetically."""
    rows = db.fetch_all(session, """
        SELECT * FROM project_formations
        WHERE project_id = :project_id
    """, {"project_id": project_id})
    phase_order = {name: index for index, name in enumerate(FORMATION_PHASES)}
    formation_order = {name: index for index, name in enumerate(FORMATIONS)}
    rows.sort(key=lambda r: (
        phase_order.get(r["phase"], 99),
        formation_order.get(r["formation"], len(FORMATIONS)),
        r["formation"],
    ))
    return rows


def upsert_project_formations(session, project_id, phase, rows, changed_by="Web User", source_task_id=None):
    """Replace the formation rows for one phase; return the fresh full list.

    Each PUT is the full row set for its phase: rows in the payload are
    upserted, and any existing (project_id, phase) row whose formation is NOT
    in the payload is deleted -- so the editor can remove a formation by
    simply omitting it. Other phases are untouched. Absent numeric fields are
    stored as NULL, absent ``fluid`` as ''.

    ``formation`` accepts the canonical trio (SARH/QASM/QWRH) OR a custom
    name: normalized ``strip().upper()``, must be non-empty and <= 40 chars,
    else ValueError (-> 400). Two payload rows that normalize to the same
    formation name raise ValueError (-> 400) rather than silently collapsing
    (and then losing the original row to the full-replacement DELETE).
    Validation is otherwise strict -- an unknown phase or field key raises
    ValueError (-> 400) rather than being silently dropped, so client typos
    never lose data quietly. Numeric fields that don't parse as a float also
    raise ValueError (-> 400) naming the offending field, so junk input never
    lands silently as NULL.

    When ``source_task_id`` is provided, ONE "Formation Data Updated" history
    event is logged against that task: listing the formations touched on an
    upsert, or the formations dropped on a deletion-only PUT (empty payload or
    fewer rows than stored). No role gate here: step-level assignment governs
    who edits. No commit -- runs in its own write transaction like the other
    mutators.
    """
    phase = str(phase or "").strip().lower()
    if phase not in FORMATION_PHASES:
        raise ValueError("Unknown phase. Use one of: " + ", ".join(FORMATION_PHASES) + ".")
    rows = rows or []
    if not isinstance(rows, list):
        raise ValueError("rows must be a list of formation objects.")

    clean_rows = []
    seen_formations = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Each formation row must be an object.")
        formation = str(row.get("formation") or "").strip().upper()
        if not formation:
            raise ValueError("Formation name is required.")
        if len(formation) > 40:
            raise ValueError("Formation name must be 40 characters or fewer.")
        # Two payload rows normalizing to the same formation would silently
        # collapse (last wins) under the upsert, and the full-replacement DELETE
        # would then remove the user's original row -- data loss from one
        # mis-click. Reject the payload instead of quietly losing a row.
        if formation in seen_formations:
            raise ValueError(f"Duplicate formation '{formation}' in payload.")
        seen_formations.add(formation)
        unknown = [k for k in row if k not in FORMATION_VALUE_FIELDS and k != "formation"]
        if unknown:
            raise ValueError("Unknown formation fields: " + ", ".join(sorted(unknown)) + ".")
        values = {"fluid": "" if row.get("fluid") is None else str(row.get("fluid")).strip()}
        for field in FORMATION_NUMERIC_FIELDS:
            raw = row.get(field)
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                values[field] = None
                continue
            try:
                values[field] = float(raw)
            except (TypeError, ValueError):
                raise ValueError(f"Invalid numeric value for {field}: {raw!r}.")
        clean_rows.append((formation, values))

    with db.write_transaction(session):
        project = get_project(session, project_id)
        if not project:
            raise ValueError("Lead / well not found.")
        # Formations currently stored for this phase, captured BEFORE the
        # full-replacement DELETE so a PUT that only removes rows (empty payload
        # clearing a phase, or fewer rows than stored) can still name what it
        # dropped in the history event.
        existing_formations = [r["formation"] for r in db.fetch_all(session, """
            SELECT formation FROM project_formations
            WHERE project_id = :project_id AND phase = :phase
        """, {"project_id": project_id, "phase": phase})]
        now = utc_now_str()
        for formation, values in clean_rows:
            params = {"project_id": project_id, "formation": formation, "phase": phase,
                      "source_task_id": source_task_id, "now": now, "changed_by": changed_by}
            params.update(values)
            db.execute(session, """
                INSERT INTO project_formations (
                    project_id, formation, phase, top_tvdss_ft, base_tvdss_ft, thickness_ft,
                    porosity_pct, swt_pct, pay_ft, ngr_pct, fluid, source_task_id, updated_at, updated_by
                ) VALUES (:project_id, :formation, :phase, :top_tvdss_ft, :base_tvdss_ft, :thickness_ft,
                          :porosity_pct, :swt_pct, :pay_ft, :ngr_pct, :fluid, :source_task_id, :now, :changed_by)
                ON CONFLICT(project_id, formation, phase) DO UPDATE SET
                    top_tvdss_ft = excluded.top_tvdss_ft,
                    base_tvdss_ft = excluded.base_tvdss_ft,
                    thickness_ft = excluded.thickness_ft,
                    porosity_pct = excluded.porosity_pct,
                    swt_pct = excluded.swt_pct,
                    pay_ft = excluded.pay_ft,
                    ngr_pct = excluded.ngr_pct,
                    fluid = excluded.fluid,
                    source_task_id = excluded.source_task_id,
                    updated_at = excluded.updated_at,
                    updated_by = excluded.updated_by
            """, params)
        # Phase-scoped full replacement: a PUT is the entire row set for its
        # phase, so anything stored under this (project, phase) that isn't in
        # the payload gets removed -- this is how the editor deletes a
        # formation. Other phases are untouched.
        kept = list({formation for formation, _values in clean_rows})
        if kept:
            db.execute(session, """
                DELETE FROM project_formations
                WHERE project_id = :project_id AND phase = :phase AND formation NOT IN :kept
            """, {"project_id": project_id, "phase": phase, "kept": kept})
        else:
            db.execute(session, """
                DELETE FROM project_formations
                WHERE project_id = :project_id AND phase = :phase
            """, {"project_id": project_id, "phase": phase})
        removed = [f for f in existing_formations if f not in kept]
        if source_task_id is not None and (clean_rows or removed):
            task = get_task(session, source_task_id)
            if task:
                if clean_rows:
                    touched = ", ".join(formation for formation, _values in clean_rows)
                    comment = f"Updated formation data ({phase}): {touched}."
                else:
                    comment = f"Removed formation data ({phase}): {', '.join(removed)}."
                log_task_event(session, task["task_id"], project_id, task["task_name"],
                               "Formation Data Updated", None, None, changed_by, comment)
    return get_project_formations(session, project_id)
