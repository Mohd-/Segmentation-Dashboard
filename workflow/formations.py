"""Well-level formation data (project_formations + their pay intervals)."""
from __future__ import annotations

import db
from helpers import utc_now_str

from .constants import (
    AUTO_COMPLETE_COMMENT,
    AUTO_COMPLETE_EVENT,
    FORMATION_FLUID_TYPES,
    FORMATION_NUMERIC_FIELDS,
    FORMATION_PHASES,
    FORMATION_VALUE_FIELDS,
    FORMATIONS,
    NON_PROSPECTIVE_AUTO_COMPLETE_STEPS,
    NON_PROSPECTIVE_FLUIDS,
    PAY_INTERVAL_NUMERIC_FIELDS,
    PAY_INTERVAL_VALUE_FIELDS,
    applicable_stages,
)
from .history import log_task_event
from .lifecycle import ensure_task_approved, get_task
from .projects import get_project
from .users import ensure_system_user


def get_project_formations(session, project_id: int):
    """Return all formation rows for a project, ordered by phase (pipeline
    order), then the canonical formation order (SARH, QASM, QWRH), then any
    custom formation names alphabetically.

    Each row carries a ``pay_intervals`` list (ordered by ``seq``) holding the
    formation's pay intervals for that same phase -- always present, empty when
    the formation has none, so the client never has to special-case it.
    """
    rows = db.fetch_all(session, """
        SELECT * FROM project_formations
        WHERE project_id = :project_id
    """, {"project_id": project_id})
    intervals = db.fetch_all(session, """
        SELECT * FROM project_formation_pay_intervals
        WHERE project_id = :project_id
        ORDER BY seq
    """, {"project_id": project_id})
    grouped: dict = {}
    for interval in intervals:
        grouped.setdefault((interval["formation"], interval["phase"]), []).append(interval)
    for row in rows:
        row["pay_intervals"] = grouped.get((row["formation"], row["phase"]), [])
    phase_order = {name: index for index, name in enumerate(FORMATION_PHASES)}
    formation_order = {name: index for index, name in enumerate(FORMATIONS)}
    rows.sort(key=lambda r: (
        phase_order.get(r["phase"], 99),
        formation_order.get(r["formation"], len(FORMATIONS)),
        r["formation"],
    ))
    return rows


# ---------------------------------------------------------------------------
# Non-prospective auto-completion (the "BP pipeline" rule)
# ---------------------------------------------------------------------------

def non_prospective_quicklook_fluid(session, project_id):
    """Return the quicklook fluid that proves the well non-prospective, else None.

    The condition is deliberately narrow and read entirely from stored state
    (so it is re-derivable, never cached): EXACTLY ONE formation row exists for
    the project at phase 'quicklook', and its ``fluid`` -- stripped and
    lowercased -- is in ``NON_PROSPECTIVE_FLUIDS``. Zero rows, two or more
    rows, a blank fluid or any other fluid (including 'Gas over Water', which
    is a hydrocarbon result) all return None.

    The value comes back in its STORED spelling so the audit-trail comment
    quotes what the interpreter actually recorded.
    """
    rows = db.fetch_all(session, """
        SELECT fluid FROM project_formations
        WHERE project_id = :project_id AND phase = 'quicklook'
    """, {"project_id": project_id})
    if len(rows) != 1:
        return None
    fluid = str(rows[0].get("fluid") or "").strip()
    return fluid if fluid.lower() in NON_PROSPECTIVE_FLUIDS else None


def auto_complete_non_prospective_steps(session, project_id):
    """Close the BP paperwork formalities for a proven non-prospective well.

    Fired as a POST-COMMIT hook from :func:`upsert_project_formations` (see the
    call site) when a 'quicklook' phase write leaves the project matching
    :func:`non_prospective_quicklook_fluid`. Post-commit because every step of
    the walk (assign / submit / approve / the gate-satisfying field save) opens
    its OWN write transaction, which must not nest inside the upsert's
    ``BEGIN IMMEDIATE``.

    Scope: the active rows named in ``NON_PROSPECTIVE_AUTO_COMPLETE_STEPS``
    whose stage is in the project's ``applicable_stages``. All four live in the
    BP execution stages, so a prospect-pipeline project is filtered out here
    with no ``pipeline_type`` literal; a missing or deactivated row is simply
    skipped.

    Each in-scope step is driven to Approved by WALKING the state machine as
    the SYSTEM_USER identity (``ensure_task_approved``: assign -> satisfy the
    submit gate -> submit -> approve), then gets one ``AUTO_COMPLETE_EVENT``
    history row naming the fluid, so the trail explains why the step closed
    without a human.

    FIRES ONCE PER STEP, EVER. Two guards, both cheap:
      - already Approved -> nothing to do;
      - already carrying an AUTO_COMPLETE_EVENT row -> stand down.
    The second is what keeps the rule from FIGHTING the user: a step the rule
    closed and a human deliberately reopened stays reopened, even if a later
    formations save still matches. Replaying the same PUT is therefore a no-op
    -- no status churn and no duplicate history.

    The rule is also NOT reversible: editing the formations so the condition no
    longer holds does nothing, because this hook only ever moves steps FORWARD.

    Returns the list of task names it completed (empty when it stood down),
    which is what the tests assert on.
    """
    fluid = non_prospective_quicklook_fluid(session, project_id)
    if fluid is None:
        return []
    project = get_project(session, project_id)
    if not project:
        return []
    tasks = db.fetch_all(session, """
        SELECT * FROM project_tasks
        WHERE project_id = :project_id AND is_active = 1
          AND task_name IN :names AND stage_group IN :stages
        ORDER BY sequence_no
    """, {"project_id": project_id,
          "names": list(NON_PROSPECTIVE_AUTO_COMPLETE_STEPS),
          "stages": applicable_stages(project.get("pipeline_type"))})
    fired_before = {row["task_id"] for row in db.fetch_all(session, """
        SELECT DISTINCT task_id FROM task_history
        WHERE project_id = :project_id AND action_type = :action
    """, {"project_id": project_id, "action": AUTO_COMPLETE_EVENT})}
    pending = [task for task in tasks
               if task["task_id"] not in fired_before
               and (task.get("status") or "Not Assigned") != "Approved"]
    if not pending:
        return []
    # Seeded lazily, and only now that the rule has actually matched, so a
    # database where it never fires never grows the row. None means the
    # identity was deactivated on purpose -- stand down rather than 500 the
    # formations save that triggered us.
    actor = ensure_system_user(session)
    if not actor:
        return []
    comment = AUTO_COMPLETE_COMMENT.format(fluid=fluid)
    completed = []
    for task in pending:
        ensure_task_approved(session, task["task_id"], actor["name"])
        with db.write_transaction(session):
            log_task_event(session, task["task_id"], project_id, task["task_name"],
                           AUTO_COMPLETE_EVENT, None, "Approved", actor["name"], comment)
        completed.append(task["task_name"])
    return completed


# Canonical spelling lookup for current values plus historical labels that an
# older full-row client must be able to round-trip unchanged. The BPE endpoint
# and editor remain strict; numbered migration v10 maps stored Dry/Water rows.
_FLUID_BY_LOWER = {value.lower(): value for value in FORMATION_FLUID_TYPES}
_FLUID_BY_LOWER.update({"dry": "Dry", "water": "Water",
                        "condensate": "Condensate", "liquid": "Liquid"})


def _clean_pay_intervals(raw, formation):
    """Validate/coerce one formation row's ``pay_intervals`` payload.

    Returns a list of value dicts in payload order (the caller stamps ``seq``
    from that order). Same strictness as the formation row itself: a non-list,
    a non-object entry, an unknown key, a non-numeric measurement or a fluid
    outside FORMATION_FLUID_TYPES all raise ValueError (-> 400) naming the
    offending formation, so a client typo never lands as a silently NULLed or
    dropped interval.
    """
    if not isinstance(raw, list):
        raise ValueError(f"pay_intervals for {formation} must be a list of interval objects.")
    cleaned = []
    for interval in raw:
        if not isinstance(interval, dict):
            raise ValueError(f"Each pay interval for {formation} must be an object.")
        unknown = [k for k in interval if k not in PAY_INTERVAL_VALUE_FIELDS]
        if unknown:
            raise ValueError(
                f"Unknown pay interval fields for {formation}: " + ", ".join(sorted(unknown)) + ".")
        fluid = "" if interval.get("fluid") is None else str(interval.get("fluid")).strip()
        if fluid.lower() not in _FLUID_BY_LOWER:
            raise ValueError(
                f"Unknown pay interval fluid for {formation}: {fluid!r}. Use one of: "
                + ", ".join(f for f in FORMATION_FLUID_TYPES if f) + ".")
        values = {"fluid": _FLUID_BY_LOWER[fluid.lower()]}
        for field in PAY_INTERVAL_NUMERIC_FIELDS:
            value = interval.get(field)
            if value is None or (isinstance(value, str) and not value.strip()):
                values[field] = None
                continue
            try:
                values[field] = float(value)
            except (TypeError, ValueError):
                raise ValueError(
                    f"Invalid numeric value for pay interval {field} ({formation}): {value!r}.")
        cleaned.append(values)
    return cleaned


def upsert_project_formations(session, project_id, phase, rows, changed_by="Web User", source_task_id=None):
    """Replace the formation rows for one phase; return the fresh full list.

    Each PUT is the full row set for its phase: rows in the payload are
    upserted, and any existing (project_id, phase) row whose formation is NOT
    in the payload is deleted -- so the editor can remove a formation by
    simply omitting it. Other phases are untouched. Absent numeric fields are
    stored as NULL, absent ``fluid`` as ''.

    A row may also carry ``pay_intervals``: the ordered list of that
    formation's pay intervals for this phase (top/base + Phit/Swt/NGR/Kint/
    fluid). It is a full replacement WITHIN the (project, formation, phase)
    scope -- the stored intervals are dropped and re-inserted with ``seq``
    assigned from the payload order (1-based). The key is OPTIONAL and its
    ABSENCE means "leave this formation's intervals alone" (send ``[]`` to
    clear them): callers that predate pay intervals -- the import script's
    SARH merge, seed_dev -- therefore cannot silently wipe them. Intervals of a
    formation the payload DROPS are deleted with the formation row itself, so
    no orphans survive the full-phase replacement.

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
        unknown = [k for k in row
                   if k not in FORMATION_VALUE_FIELDS and k not in ("formation", "pay_intervals")]
        if unknown:
            raise ValueError("Unknown formation fields: " + ", ".join(sorted(unknown)) + ".")
        # None (key absent) vs [] (key present and empty) is meaningful here:
        # absent leaves the stored intervals untouched, empty clears them.
        intervals = (_clean_pay_intervals(row["pay_intervals"], formation)
                     if "pay_intervals" in row else None)
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
        clean_rows.append((formation, values, intervals))

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
        for formation, values, intervals in clean_rows:
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
            if intervals is None:
                continue  # key absent -> this formation's stored intervals stay
            # Formation-scoped full replacement: drop and re-insert, so seq
            # always matches the payload order with no stale tail left behind
            # when the list shrinks.
            db.execute(session, """
                DELETE FROM project_formation_pay_intervals
                WHERE project_id = :project_id AND phase = :phase AND formation = :formation
            """, {"project_id": project_id, "phase": phase, "formation": formation})
            for seq, interval in enumerate(intervals, start=1):
                interval_params = {"project_id": project_id, "formation": formation, "phase": phase,
                                   "seq": seq, "source_task_id": source_task_id, "now": now,
                                   "changed_by": changed_by}
                interval_params.update(interval)
                db.execute(session, """
                    INSERT INTO project_formation_pay_intervals (
                        project_id, formation, phase, seq, top_tvdss_ft, base_tvdss_ft,
                        phit_pct, swt_pct, ngr_pct, kint_md, fluid,
                        source_task_id, updated_at, updated_by
                    ) VALUES (:project_id, :formation, :phase, :seq, :top_tvdss_ft, :base_tvdss_ft,
                              :phit_pct, :swt_pct, :ngr_pct, :kint_md, :fluid,
                              :source_task_id, :now, :changed_by)
                """, interval_params)
        # Phase-scoped full replacement: a PUT is the entire row set for its
        # phase, so anything stored under this (project, phase) that isn't in
        # the payload gets removed -- this is how the editor deletes a
        # formation. Other phases are untouched.
        kept = list({formation for formation, _values, _intervals in clean_rows})
        if kept:
            db.execute(session, """
                DELETE FROM project_formations
                WHERE project_id = :project_id AND phase = :phase AND formation NOT IN :kept
            """, {"project_id": project_id, "phase": phase, "kept": kept})
            # A dropped formation takes its pay intervals with it -- otherwise
            # they would linger invisibly and reattach if the name came back.
            db.execute(session, """
                DELETE FROM project_formation_pay_intervals
                WHERE project_id = :project_id AND phase = :phase AND formation NOT IN :kept
            """, {"project_id": project_id, "phase": phase, "kept": kept})
        else:
            db.execute(session, """
                DELETE FROM project_formations
                WHERE project_id = :project_id AND phase = :phase
            """, {"project_id": project_id, "phase": phase})
            db.execute(session, """
                DELETE FROM project_formation_pay_intervals
                WHERE project_id = :project_id AND phase = :phase
            """, {"project_id": project_id, "phase": phase})
        removed = [f for f in existing_formations if f not in kept]
        if source_task_id is not None and (clean_rows or removed):
            task = get_task(session, source_task_id)
            if task:
                if clean_rows:
                    touched = ", ".join(formation for formation, _values, _intervals in clean_rows)
                    comment = f"Updated formation data ({phase}): {touched}."
                else:
                    comment = f"Removed formation data ({phase}): {', '.join(removed)}."
                log_task_event(session, task["task_id"], project_id, task["task_name"],
                               "Formation Data Updated", None, None, changed_by, comment)
    # POST-COMMIT hook, same pattern as lifecycle._apply_well_name_override: a
    # quicklook write can prove the well non-prospective, and closing the BP
    # paperwork walks the state machine -- whose every step opens its own write
    # transaction and so must not nest inside the one above. Keeping the call
    # HERE (rather than in the route) preserves the route -> one-domain-function
    # rule: main.py still calls upsert_project_formations and nothing else.
    if phase == "quicklook":
        auto_complete_non_prospective_steps(session, project_id)
    return get_project_formations(session, project_id)
