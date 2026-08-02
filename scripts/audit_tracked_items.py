#!/usr/bin/env python
"""Audit the permanent 12-tracked-item prospect model against a real database.

    .venv/bin/python scripts/audit_tracked_items.py pipeline_tracker.db
    .venv/bin/python scripts/audit_tracked_items.py pipeline_tracker.db --rehearse-v5

NEVER WRITES THE DATABASE YOU POINT IT AT. Like scripts/migration_dryrun_v5.py
it takes a byte-exact copy through the SQLite BACKUP API (so a live -wal/-shm
pair is captured consistently) and does all of its work -- including the
optional migration rehearsal -- on that copy.

WHY IT EXISTS: v5 made the twelve tracked items the STORED prospect workflow
(migrations._migrate_v5_prospect_template_restructure). Everything the board,
the KPI donut and the detail sidebar render is derived from those rows, so a
single stray row -- a reactivated legacy step, a duplicate, an orphaned EAV
row -- is invisible in the UI right up until it changes a number. This script
is the structural check the UI cannot perform on itself.

WHAT IT CHECKS, per prospect lead unless stated:

  1. EXACTLY 12 tracked items: the active prospect-stage rows are precisely the
     twelve names in PIPELINE_TEMPLATES -- no missing item, no thirteenth.
  2. NO LEGACY 13th item: no ACTIVE row carries a retired prospect name
     ("Well Creation", "Trap CoS", "Seal CoS") or a pre-v5 name that v5 renamed
     ("Reservoir Area Definition", "Lead Resource Assessment",
     "Prospect Evaluation Presentation", "Staking Moving Tolerance",
     "Pre-Drilling Resource Assessment"). Retired rows may EXIST -- that is how
     their inputs stay readable -- but only with is_active = 0.
  3. NO DUPLICATE STEP ROWS: one row per (project_id, task_name), and the twelve
     active sequence numbers are exactly 1..12 with no repeats.
  4. NO ORPHANS (whole database): every task_dynamic_fields.task_id and
     task_history.task_id resolves to a live project_tasks row.
  5. TRAP AND SEAL COUNTED ONCE, BOTH VALUES READABLE: exactly one active
     "Trap and Seal CoS" row, and wherever a trap_cos_pct / seal_cos_pct was
     ever stored (on the merged row OR on a retired half) the surviving-first
     ladder in workflow.constants still resolves it.
  6. STAGE COUNTERS CONSISTENT, in both of their DIFFERENT meanings:
       - the DETAIL SIDEBAR counter counts COMPLETED ITEMS within one stage:
         every stage group holds exactly 4 of the twelve, so each reads x/4 with
         0 <= x <= 4, and the three x values sum to the lead's completed count;
       - the MAIN BOARD BADGE counts LEADS: every board lead lands in exactly
         one stage badge and the three badges sum to the number of leads on the
         board.
  7. THE ONE COMPLETION FORMULA: the server's project_completion_percent equals
     round(completed / 12 * 100, 1) for every lead, and the app's own derived
     ``tracked_items`` payload (what the board and the sidebar actually read)
     agrees item-for-item with the raw SQL truth above.

EXIT CODES
  0  every lead passes
  1  at least one FAILURE (details are printed per lead, then summarised)
  2  the database could not be read/migrated at all
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

# Reused wholesale rather than re-implemented: the same copy/devolve/bootstrap
# helpers the v5 dry-run rehearses with, so "audit the rehearsed database" is
# literally the same upgrade this repo already exercises.
from migration_dryrun_v5 import (bootstrap, copy_database,  # noqa: E402
                                 devolve_to_v4, stored_version)

from workflow.constants import (BP_EXECUTION_STAGES, MERGED_COS_LEGACY_NAMES,  # noqa: E402
                                MERGED_COS_TASK_NAME, PIPELINE_TEMPLATES,
                                PROSPECT_STAGES, RENAMED_TASK_NAMES,
                                RETIRED_TASK_NAMES, SEAL_COS_SOURCES,
                                TRAP_COS_SOURCES)

# The twelve, taken from the ONE canonical source (no second copy of the list
# can drift out of step with the template the app materialises from).
EXPECTED_STEPS = [(seq, name, stage) for seq, name, stage in PIPELINE_TEMPLATES
                  if stage not in BP_EXECUTION_STAGES]
EXPECTED_NAMES = [name for _seq, name, _stage in EXPECTED_STEPS]
EXPECTED_BY_STAGE = defaultdict(list)
for _seq, _name, _stage in EXPECTED_STEPS:
    EXPECTED_BY_STAGE[_stage].append(_name)

# The names that must never be ACTIVE on a lead again: the prospect steps v5
# retired, plus the pre-v5 spellings of the five it renamed in place.
FORBIDDEN_ACTIVE_NAMES = set(RENAMED_TASK_NAMES.values()) | {
    name for name in RETIRED_TASK_NAMES
    if name in set(MERGED_COS_LEGACY_NAMES) | {"Well Creation"}}


def connect(path):
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Structural checks -- RAW SQL ONLY (deliberately independent of the app code
# they are auditing; the app's own derivation is cross-checked separately)
# ---------------------------------------------------------------------------

class Findings:
    """Collected failures, grouped by check id, plus the per-lead detail."""

    def __init__(self):
        self.failures = defaultdict(list)

    def fail(self, check, message):
        self.failures[check].append(message)

    def ok(self):
        return not self.failures

    def count(self):
        return sum(len(v) for v in self.failures.values())


def leads_of(conn):
    """Every non-archived PROSPECT project, in id order."""
    return conn.execute("""
        SELECT project_id, project_name, COALESCE(pipeline_type, 'prospect') AS pipeline_type
        FROM projects
        WHERE COALESCE(archived, 0) = 0
          AND LOWER(COALESCE(pipeline_type, 'prospect')) != 'bp'
        ORDER BY project_id
    """).fetchall()


def task_rows(conn, project_id):
    return conn.execute("""
        SELECT task_id, task_name, stage_group, sequence_no, status, is_active
        FROM project_tasks WHERE project_id = ? ORDER BY sequence_no, task_id
    """, (project_id,)).fetchall()


def field_value(conn, project_id, task_name, field_key):
    """The stored EAV value for one (project, step, key), '' when absent."""
    row = conn.execute("""
        SELECT tdf.field_value FROM project_tasks pt
        LEFT JOIN task_dynamic_fields tdf
               ON tdf.task_id = pt.task_id AND tdf.field_key = ?
        WHERE pt.project_id = ? AND pt.task_name = ?
        ORDER BY pt.task_id DESC LIMIT 1
    """, (field_key, project_id, task_name)).fetchone()
    if not row or row["field_value"] is None:
        return ""
    return str(row["field_value"]).strip()


def ladder_value(conn, project_id, sources):
    """First non-blank value down a ((task_name, field_key), ...) ladder."""
    for task_name, field_key in sources:
        value = field_value(conn, project_id, task_name, field_key)
        if value:
            return value
    return ""


def audit_lead(conn, lead, findings):
    """Run checks 1/2/3/5/6-sidebar for one lead; return its per-lead summary."""
    pid = lead["project_id"]
    name = lead["project_name"]
    rows = task_rows(conn, pid)
    active = [r for r in rows if r["is_active"] == 1 and r["stage_group"] in PROSPECT_STAGES]
    active_names = [r["task_name"] for r in active]

    # --- 1. exactly twelve, and exactly the right twelve --------------------
    if sorted(active_names) != sorted(EXPECTED_NAMES):
        missing = sorted(set(EXPECTED_NAMES) - set(active_names))
        extra = sorted(set(active_names) - set(EXPECTED_NAMES))
        findings.fail("1-twelve-items",
                      f"[{pid}] {name}: {len(active_names)} active prospect steps "
                      f"(missing={missing}, unexpected={extra})")

    # --- 2. no ACTIVE legacy/renamed row (a retired row may exist inactive) --
    live_legacy = sorted({r["task_name"] for r in rows
                          if r["is_active"] == 1 and r["task_name"] in FORBIDDEN_ACTIVE_NAMES})
    if live_legacy:
        findings.fail("2-no-legacy-13th",
                      f"[{pid}] {name}: ACTIVE legacy step rows {live_legacy}")

    # --- 3. no duplicates ---------------------------------------------------
    dup_names = [n for n, c in Counter(r["task_name"] for r in rows).items() if c > 1]
    if dup_names:
        findings.fail("3-no-duplicates",
                      f"[{pid}] {name}: duplicate task_name rows {sorted(dup_names)}")
    seqs = sorted(r["sequence_no"] for r in active)
    if len(active) == 12 and seqs != list(range(1, 13)):
        findings.fail("3-no-duplicates",
                      f"[{pid}] {name}: active sequence numbers are {seqs}, expected 1..12")

    # --- 5. Trap and Seal: one row, both numbers still readable -------------
    merged = [r for r in rows if r["task_name"] == MERGED_COS_TASK_NAME and r["is_active"] == 1]
    if len(merged) != 1:
        findings.fail("5-trap-seal-once",
                      f"[{pid}] {name}: {len(merged)} ACTIVE '{MERGED_COS_TASK_NAME}' rows (expected 1)")
    live_halves = [r["task_name"] for r in rows
                   if r["task_name"] in MERGED_COS_LEGACY_NAMES and r["is_active"] == 1]
    if live_halves:
        findings.fail("5-trap-seal-once",
                      f"[{pid}] {name}: pre-merge CoS halves are still ACTIVE {sorted(live_halves)}")
    # "Readable" is only assertable where a value was ever stored ANYWHERE --
    # on the merged row or on either retired half. Where one exists, the
    # surviving-first ladder must resolve it.
    trap_stored = any(field_value(conn, pid, n, "trap_cos_pct")
                      for n in (MERGED_COS_TASK_NAME,) + tuple(MERGED_COS_LEGACY_NAMES))
    seal_stored = any(field_value(conn, pid, n, "seal_cos_pct")
                      for n in (MERGED_COS_TASK_NAME,) + tuple(MERGED_COS_LEGACY_NAMES))
    trap = ladder_value(conn, pid, TRAP_COS_SOURCES)
    seal = ladder_value(conn, pid, SEAL_COS_SOURCES)
    if trap_stored and not trap:
        findings.fail("5-trap-seal-once", f"[{pid}] {name}: trap_cos_pct stored but the ladder reads blank")
    if seal_stored and not seal:
        findings.fail("5-trap-seal-once", f"[{pid}] {name}: seal_cos_pct stored but the ladder reads blank")

    # --- 6a. SIDEBAR counter: COMPLETED ITEMS per stage, always out of 4 -----
    per_stage = {}
    for stage in PROSPECT_STAGES:
        in_stage = [r for r in active if r["stage_group"] == stage]
        done = sum(1 for r in in_stage if r["status"] == "Approved")
        per_stage[stage] = (done, len(in_stage))
        if len(in_stage) != 4:
            findings.fail("6-stage-counters",
                          f"[{pid}] {name}: stage '{stage}' holds {len(in_stage)} items (expected 4)")
        if not 0 <= done <= len(in_stage):
            findings.fail("6-stage-counters",
                          f"[{pid}] {name}: stage '{stage}' counter {done}/{len(in_stage)} out of range")
    completed = sum(1 for r in active if r["status"] == "Approved")
    if sum(d for d, _t in per_stage.values()) != completed:
        findings.fail("6-stage-counters",
                      f"[{pid}] {name}: sidebar counters {per_stage} do not sum to {completed}")

    return {"project_id": pid, "name": name, "active": len(active),
            "completed": completed, "per_stage": per_stage,
            "trap": trap, "seal": seal}


def audit_orphans(conn, findings):
    """Check 4: EAV + history rows must all point at a live task row."""
    eav = conn.execute("""
        SELECT COUNT(*) AS c FROM task_dynamic_fields tdf
        LEFT JOIN project_tasks pt ON pt.task_id = tdf.task_id
        WHERE pt.task_id IS NULL
    """).fetchone()["c"]
    hist = conn.execute("""
        SELECT COUNT(*) AS c FROM task_history th
        LEFT JOIN project_tasks pt ON pt.task_id = th.task_id
        WHERE pt.task_id IS NULL
    """).fetchone()["c"]
    if eav:
        findings.fail("4-no-orphans", f"{eav} task_dynamic_fields rows point at a missing task")
    if hist:
        findings.fail("4-no-orphans", f"{hist} task_history rows point at a missing task")
    return {"orphan_fields": eav, "orphan_history": hist}


# ---------------------------------------------------------------------------
# Cross-check against the APP's own derivation (checks 6b + 7)
# ---------------------------------------------------------------------------

def audit_against_app(db_path, per_lead, findings):
    """Board badges count LEADS; the donut/percent counts ITEMS out of twelve.

    Runs the real read path (workflow.get_projects / project_completion_percent)
    against the copy and reconciles it with the raw-SQL truth collected above.
    """
    os.environ["SEGMENT_TRACKER_DB_PATH"] = str(db_path)
    import db as dbmod
    import workflow

    dbmod.reset_for_tests()
    dbmod.init_db(str(db_path))
    session = dbmod.new_session()
    try:
        rows = workflow.get_projects(session, include_completed=True)
        leads = [r for r in rows if str(r.get("pipeline_type") or "prospect").lower() != "bp"]
        by_id = {r["project_id"]: r for r in leads}

        # --- 6b. BOARD BADGE = LEADS PER STAGE ------------------------------
        badges = Counter()
        for row in leads:
            stage = row.get("display_stage")
            if stage in PROSPECT_STAGES:
                badges[stage] += 1
            else:
                findings.fail("6-stage-counters",
                              f"[{row['project_id']}] {row['project_name']}: display_stage "
                              f"{stage!r} is not one of the three board columns")
        if sum(badges.values()) != len(leads):
            findings.fail("6-stage-counters",
                          f"board badges sum to {sum(badges.values())} but {len(leads)} leads are on the board")

        # --- 7. one formula: tracked_items agree, percent = completed/12 ----
        for summary in per_lead:
            pid = summary["project_id"]
            row = by_id.get(pid)
            if row is None:
                findings.fail("7-completion-formula", f"[{pid}] {summary['name']}: absent from get_projects")
                continue
            items = row.get("tracked_items") or []
            if len(items) != 12:
                findings.fail("7-completion-formula",
                              f"[{pid}] {summary['name']}: tracked_items has {len(items)} entries, expected 12")
            done = sum(1 for i in items if i.get("status") == "Completed")
            if done != summary["completed"]:
                findings.fail("7-completion-formula",
                              f"[{pid}] {summary['name']}: payload says {done} completed, SQL says "
                              f"{summary['completed']}")
            # Nothing waiting on a human may read as done.
            for item in items:
                if item.get("status") not in {"Completed", "In Progress", "Pending Approval"}:
                    findings.fail("7-completion-formula",
                                  f"[{pid}] {summary['name']}: item {item.get('label')!r} has "
                                  f"unknown display status {item.get('status')!r}")
            percent = workflow.project_completion_percent(session, pid)
            expected = round(summary["completed"] / 12 * 100, 1)
            if percent != expected:
                findings.fail("7-completion-formula",
                              f"[{pid}] {summary['name']}: completion percent {percent} != "
                              f"{expected} (= {summary['completed']}/12)")
            summary["badge_stage"] = row.get("display_stage")
        return badges
    finally:
        session.close()
        dbmod.reset_for_tests()


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def render(per_lead, badges, orphans, findings, label):
    print()
    print(f"PER-LEAD ({label})")
    print("-" * 104)
    print(f"{'id':>4}  {'lead':<22} {'items':<6} {'done':<6} {'LA':<5} {'RA':<5} {'PWD':<5} "
          f"{'trap':<6} {'seal':<6} {'board badge':<18}")
    print("-" * 104)
    for s in per_lead:
        la, ra, pwd = (s["per_stage"].get(stage, (0, 0)) for stage in PROSPECT_STAGES)
        print(f"{s['project_id']:>4}  {s['name'][:22]:<22} {s['active']:<6} {s['completed']:<6} "
              f"{la[0]}/{la[1]:<3} {ra[0]}/{ra[1]:<3} {pwd[0]}/{pwd[1]:<3} "
              f"{(s['trap'] or '-'):<6} {(s['seal'] or '-'):<6} {str(s.get('badge_stage') or '-'):<18}")

    print()
    print("SUMMARY")
    print("-" * 60)
    print(f"  leads audited                        {len(per_lead)}")
    print(f"  leads with exactly 12 tracked items  "
          f"{sum(1 for s in per_lead if s['active'] == 12)}")
    print(f"  completed tracked items (total)      {sum(s['completed'] for s in per_lead)}"
          f" / {12 * len(per_lead)}")
    print(f"  orphaned task_dynamic_fields rows    {orphans['orphan_fields']}")
    print(f"  orphaned task_history rows           {orphans['orphan_history']}")
    print()
    print("  BOARD BADGES (leads per stage -- NOT items)")
    for stage in PROSPECT_STAGES:
        print(f"    {stage:<20} {badges.get(stage, 0)}")
    print(f"    {'sum':<20} {sum(badges.values())}  (leads on board: {len(per_lead)})")
    print()
    print("  CHECKS")
    checks = ["1-twelve-items", "2-no-legacy-13th", "3-no-duplicates", "4-no-orphans",
              "5-trap-seal-once", "6-stage-counters", "7-completion-formula"]
    for check in checks:
        bad = findings.failures.get(check) or []
        print(f"    {check:<24} {'OK' if not bad else f'FAIL ({len(bad)})'}")
        for message in bad:
            print(f"      - {message}")
    print("-" * 60)
    verdict = "PASS" if findings.ok() else f"FAIL ({findings.count()} findings)"
    print(f"  VERDICT: {verdict}")
    return 0 if findings.ok() else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("database", help="database to audit (a COPY is taken; never written)")
    parser.add_argument("--rehearse-v5", action="store_true",
                        help="rebuild the pre-v5 shape on the copy and re-run the real "
                             "migration before auditing, so the audit covers an UPGRADED "
                             "database rather than one born at v5")
    parser.add_argument("--label", default=None, help="name this run in the output")
    args = parser.parse_args(argv)

    source = Path(args.database).resolve()
    if not source.exists():
        print(f"No such database: {source}", file=sys.stderr)
        return 2

    workdir = Path(tempfile.mkdtemp(prefix="tracked-items-audit-"))
    copy = workdir / "copy.db"
    try:
        copy_database(source, copy)
        label = args.label or source.name
        print(f"source     : {source}")
        print(f"copy       : {copy}")
        print(f"stamped schema_version: {stored_version(copy)}")

        if args.rehearse_v5:
            devolved = devolve_to_v4(copy)
            print(f"--rehearse-v5: rebuilt the pre-v5 shape on the copy ({devolved} projects); "
                  f"schema_version now {stored_version(copy)}")
            try:
                bootstrap(copy)
            except Exception as exc:                                # noqa: BLE001
                print(f"\nBootstrap REFUSED this database: {exc}", file=sys.stderr)
                return 2
            print(f"              re-migrated; schema_version now {stored_version(copy)}")
            label = f"{label} (devolved to v4, then migrated)"

        findings = Findings()
        conn = connect(copy)
        try:
            per_lead = [audit_lead(conn, lead, findings) for lead in leads_of(conn)]
            orphans = audit_orphans(conn, findings)
        finally:
            conn.close()

        badges = audit_against_app(copy, per_lead, findings)
        return render(per_lead, badges, orphans, findings, label)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
