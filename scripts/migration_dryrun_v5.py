#!/usr/bin/env python
"""Dry-run the v5 prospect-template migration against a COPY of a real database.

    .venv/bin/python scripts/migration_dryrun_v5.py pipeline_tracker.db

WHAT IT DOES -- and, more importantly, what it does NOT do: it never opens the
database you point it at for writing. It takes a byte-exact copy through the
SQLite BACKUP API (so a live -wal/-shm pair is captured consistently), snapshots
every project as the app renders it TODAY, runs the real bootstrap
(``db.init_db`` -> ``migrations.run``) against the COPY, snapshots again, and
diffs the two.

The pre-migration snapshot deliberately uses a FROZEN COPY of the pre-v5
presentation adapter (the twelve tracked items the board faked over the old
stored steps, and the four-stage-groups-into-three column mapping). That is the
only honest baseline: the question this script answers is "does a lead look the
same to its owner after the migration", not "do two different code paths agree".

THE INVARIANTS IT ENFORCES (card 29 constraint 2: an existing lead must
"preserve its current stage")

  1. no lead's BOARD COLUMN moves backward. v5 inserts two steps into the middle
     of the workflow, and the derived current_stage is the stage of the first
     non-Approved row -- so an unstarted row dropped into a stage the lead had
     already finished would drag its card back a column. The migration's
     per-stage backfill is what prevents that, and this is the check on it.
  2. a lead's number of COMPLETED TRACKED ITEMS never falls. This -- not the
     raw percentage -- is the continuity measure: it is what the board dots, the
     x/4 stage counters and the KPI donut all render, and it is comparable
     across the migration because both sides count out of the same fixed twelve.
  3. a Completed lead is still Completed afterwards.
  4. a lead at 100% is still at 100%.

The raw completion PERCENT is reported for every lead but is deliberately NOT a
hard invariant for a lead still in flight, because it cannot be one: v5 changes
the checklist itself (two halves become one step, one step retires, two new
steps appear), so an unfinished lead's denominator legitimately re-baselines.
A lead that had approved 11 of 12 rows including both CoS halves and Well
Creation comes out having approved 9 of 12 -- the same work, measured against a
checklist that now contains two items nobody has done yet. Invariant 1 is what
catches a real regression there; the percent column is shown so the size of the
re-baseline is visible rather than hidden.

EXIT CODES
  0  the migration is safe on this database
  1  a REGRESSION was found -- one of the four invariants above broke. The run
     fails loudly rather than reporting it in passing.
  2  the database could not be migrated at all (too old / refused / unreadable).
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# FROZEN pre-v5 vocabulary (what the app showed BEFORE this migration)
# ---------------------------------------------------------------------------

PRE_PROSPECT_STAGES = ("Lead Identification", "Risking", "Segmentation", "Pre-Well Delivery")
BP_STAGES = ("Well Delivery", "Post-Drilling", "Post-Testing")

PRE_DISPLAY_STAGE = {
    "Lead Identification": "Lead Assessment",
    "Risking": "Risk Analysis",
    "Segmentation": "Risk Analysis",
    "Pre-Well Delivery": "Pre-Well Delivery",
}

# (display stage, label, source step names, ready_shows_pending)
PRE_TRACKED_ITEMS = (
    ("Lead Assessment", "Area Definition", ("Reservoir Area Definition",), False),
    ("Lead Assessment", "Thickness Estimation", ("Thickness Estimation",), False),
    ("Lead Assessment", "GRV Inputs", (), False),
    ("Lead Assessment", "Resource Assessment", ("Lead Resource Assessment",), False),
    ("Risk Analysis", "Reservoir", ("Reservoir CoS",), False),
    ("Risk Analysis", "Trap and Seal", ("Trap CoS", "Seal CoS"), False),
    ("Risk Analysis", "Seismic Validation", ("Seismic Signature Validation",), False),
    ("Risk Analysis", "Segmentation Slides", ("Prospect Evaluation Presentation",), True),
    ("Pre-Well Delivery", "Moving Tolerance", ("Staking Moving Tolerance",), False),
    ("Pre-Well Delivery", "Approval to Stake", ("Approval to Stake",), False),
    ("Pre-Well Delivery", "Well Site Location", (), False),
    ("Pre-Well Delivery", "GeoX Assessment", ("Pre-Drilling Resource Assessment",), False),
)

PRIORITY_RANK = {"High": 0, "Medium": 1, "Low": 2}

# The step names v5 touches, for the row-level counts.
V5_RENAMES = {
    "Reservoir Area Definition": "Area Definition",
    "Lead Resource Assessment": "Resource Assessment",
    "Prospect Evaluation Presentation": "Segmentation Slides",
    "Staking Moving Tolerance": "Moving Tolerance",
    "Pre-Drilling Resource Assessment": "Pre-Drilling GeoX Assessment",
}
V5_MERGED = "Trap and Seal CoS"
V5_MERGE_SOURCES = ("Trap CoS", "Seal CoS")
V5_RETIRED_STEP = "Well Creation"
V5_NEW_STEPS = ("GRV Inputs", "Well Site Location")

# The pre-v5 prospect template: task_name -> (sequence_no, stage_group). Used
# ONLY by --devolve (see its docstring) to rebuild a pre-v5 baseline on the COPY.
PRE_V5_TEMPLATE = {
    "Reservoir Area Definition": (1, "Lead Identification"),
    "Thickness Estimation": (2, "Lead Identification"),
    "Lead Resource Assessment": (3, "Lead Identification"),
    "Seismic Signature Validation": (4, "Risking"),
    "Reservoir CoS": (5, "Risking"),
    "Trap CoS": (6, "Risking"),
    "Seal CoS": (7, "Risking"),
    "Prospect Evaluation Presentation": (8, "Segmentation"),
    "Well Creation": (9, "Pre-Well Delivery"),
    "Pre-Drilling Resource Assessment": (10, "Pre-Well Delivery"),
    "Staking Moving Tolerance": (11, "Pre-Well Delivery"),
    "Approval to Stake": (12, "Pre-Well Delivery"),
}


# ---------------------------------------------------------------------------
# Snapshotting (raw sqlite3 -- no app imports, so it works on both shapes)
# ---------------------------------------------------------------------------

def _connect(path):
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _lead_priority(rows):
    ranks = [PRIORITY_RANK[r["priority"]] for r in rows
             if r["status"] != "Approved" and r["priority"] in PRIORITY_RANK]
    if not ranks:
        return "Low"
    best = min(ranks)
    return next(name for name, rank in PRIORITY_RANK.items() if rank == best)


def _pre_tracked_items(status_by_task):
    """The pre-v5 presentation adapter, verbatim."""
    items = []
    for _stage, label, sources, ready_shows_pending in PRE_TRACKED_ITEMS:
        if not sources:
            status = "In Progress"
        elif all(status_by_task.get(name) == "Approved" for name in sources):
            status = "Completed"
        elif ready_shows_pending and any(status_by_task.get(name) == "Ready" for name in sources):
            status = "Pending Approval"
        else:
            status = "In Progress"
        items.append((label, status))
    return items


def _post_tracked_items(status_by_task):
    """The v5 projection, taken from the live code so the two can never drift."""
    from workflow.projects import _tracked_items
    return [(item["label"], item["status"]) for item in _tracked_items(status_by_task)]


def snapshot(db_path, *, pre):
    """{project_id: {...}} -- how every project reads on this database shape."""
    prospect_stages = PRE_PROSPECT_STAGES if pre else None
    if not pre:
        import workflow
        prospect_stages = tuple(workflow.PROSPECT_STAGES)
    items_fn = _pre_tracked_items if pre else _post_tracked_items

    conn = _connect(db_path)
    try:
        projects = conn.execute(
            "SELECT project_id, project_name, pipeline_type, archived FROM projects "
            "ORDER BY project_id").fetchall()
        task_rows = conn.execute(
            "SELECT project_id, task_name, stage_group, assigned_to, status, priority, "
            "       sequence_no, is_active "
            "FROM project_tasks ORDER BY project_id, sequence_no, task_id").fetchall()
    finally:
        conn.close()

    by_project = {}
    for row in task_rows:
        by_project.setdefault(row["project_id"], []).append(row)

    out = {}
    for project in projects:
        pipeline = str(project["pipeline_type"] or "prospect").lower()
        stages = BP_STAGES if pipeline == "bp" else prospect_stages
        rows = [r for r in by_project.get(project["project_id"], []) if r["is_active"] == 1]
        applicable = [r for r in rows if r["stage_group"] in stages]
        open_task = next((r for r in applicable if r["status"] != "Approved"), None)
        approved = sum(1 for r in applicable if r["status"] == "Approved")
        total = len(applicable)
        anchor = open_task or (applicable[-1] if applicable else None)
        stage = anchor["stage_group"] if anchor else ""
        assignees = []
        for row in applicable:
            name = (row["assigned_to"] or "").strip()
            if name and name not in assignees:
                assignees.append(name)
        out[project["project_id"]] = {
            "name": project["project_name"],
            "pipeline": pipeline,
            "archived": int(project["archived"] or 0),
            "stage": PRE_DISPLAY_STAGE.get(stage, stage) if pre else stage,
            "overall_status": "In Progress" if open_task else "Completed",
            "completion": round((approved / total) * 100, 1) if total else 0.0,
            "priority": _lead_priority(applicable),
            "assignees": tuple(assignees),
            "items": tuple(items_fn({r["task_name"]: r["status"] for r in rows}))
                     if pipeline != "bp" else (),
        }
    return out


def row_counts(db_path):
    """Row-level facts the summary reports (name -> active/inactive counts)."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT task_name, is_active, status, COUNT(*) AS c FROM project_tasks "
            "GROUP BY task_name, is_active, status").fetchall()
    finally:
        conn.close()
    counts = {}
    for row in rows:
        counts.setdefault(row["task_name"], []).append(
            (int(row["is_active"]), row["status"], int(row["c"])))
    return counts


def _total(counts, name, *, active=None, status=None):
    return sum(c for is_active, st, c in counts.get(name, [])
               if (active is None or is_active == active)
               and (status is None or st == status))


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

def copy_database(source: Path, destination: Path):
    """Byte-consistent copy through the SQLite BACKUP API (never a file copy:
    the source may have uncheckpointed -wal content)."""
    src = sqlite3.connect(str(source))
    try:
        dst = sqlite3.connect(str(destination))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def devolve_to_v4(db_path):
    """Rebuild a PRE-v5 baseline on the copy, then let the real migration re-run.

    Needed when the database you want to rehearse against has ALREADY been
    migrated -- the common case on a dev box, where any running server
    bootstraps the file at import time the moment the new code lands. Without
    this the "before" snapshot would be taken of an already-migrated database
    and the whole run would be vacuous.

    This is the exact INVERSE of the v5 step (delete what it inserted, reactivate
    what it retired, rename back, restore the old sequence/stage vocabulary,
    re-stamp 4) and it runs ONLY against the temp copy -- never the source.

    What it CANNOT rebuild is information v5 did not destroy but did not record
    either: the merged row's own history event and the EAV rows v5 COPIED onto
    it are deleted with the row, which is exactly right, and the retired halves
    still hold the originals. Returns the number of projects devolved.
    """
    conn = _connect(db_path)
    try:
        added = [V5_MERGED] + list(V5_NEW_STEPS)
        placeholders = ",".join("?" for _ in added)
        conn.execute(
            f"DELETE FROM task_dynamic_fields WHERE task_id IN "
            f"(SELECT task_id FROM project_tasks WHERE task_name IN ({placeholders}))", added)
        conn.execute(
            f"DELETE FROM task_history WHERE task_id IN "
            f"(SELECT task_id FROM project_tasks WHERE task_name IN ({placeholders}))", added)
        conn.execute(f"DELETE FROM project_tasks WHERE task_name IN ({placeholders})", added)
        # The Well Creation sign-off v5 carried onto Approval to Stake.
        conn.execute("DELETE FROM task_dynamic_fields WHERE field_key = 'staking_well_created'")
        conn.execute("DELETE FROM task_history WHERE changed_by LIKE 'System (migration v5)%'")
        # Un-retire and un-rename, then restore the pre-v5 numbering/vocabulary.
        for new_name, old_name in ((v, k) for k, v in V5_RENAMES.items()):
            conn.execute("UPDATE project_tasks SET task_name = ? WHERE task_name = ?",
                         (old_name, new_name))
        for name, (sequence_no, stage_group) in PRE_V5_TEMPLATE.items():
            conn.execute(
                "UPDATE project_tasks SET sequence_no = ?, stage_group = ?, is_active = 1 "
                "WHERE task_name = ?", (sequence_no, stage_group, name))
        conn.execute("UPDATE app_settings SET value = '4' WHERE key = 'schema_version'")
        conn.commit()
        return conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    finally:
        conn.close()


def stored_version(db_path):
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = 'schema_version'").fetchone()
        return int(row["value"]) if row else None
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def bootstrap(db_path):
    os.environ["SEGMENT_TRACKER_DB_PATH"] = str(db_path)
    import db as dbmod
    dbmod.reset_for_tests()
    dbmod.init_db(str(db_path))
    dbmod.reset_for_tests()


def render(pre, post, counts_pre, counts_post):
    """Print the per-project table and the summary; return the exit code."""
    ids = sorted(set(pre) | set(post))
    leads = [pid for pid in ids if (post.get(pid) or pre.get(pid))["pipeline"] != "bp"]

    print()
    print("PER-PROJECT (pre -> post)")
    print("-" * 118)
    print(f"{'id':>4}  {'record':<22} {'pipe':<8} {'stage (pre -> post)':<40} "
          f"{'status':<26} {'done %':<14} {'items done':<10}")
    print("-" * 118)
    regressed_items, regressed_completed, regressed_full = [], [], []
    rebaselined, stage_moved_back = [], []
    stage_index = {"Lead Assessment": 0, "Risk Analysis": 1, "Pre-Well Delivery": 2}
    for pid in ids:
        before, after = pre.get(pid), post.get(pid)
        if not before or not after:
            print(f"{pid:>4}  {(after or before)['name']:<22} "
                  f"{'(project only on one side -- investigate)'}")
            continue
        done_pre = sum(1 for _label, status in before["items"] if status == "Completed")
        done_post = sum(1 for _label, status in after["items"] if status == "Completed")
        stage = f"{before['stage']} -> {after['stage']}"
        status = f"{before['overall_status']} -> {after['overall_status']}"
        pct = f"{before['completion']} -> {after['completion']}"
        flag = ""
        if done_post < done_pre:
            regressed_items.append(pid)
            flag += "  <-- ITEMS DONE FELL"
        if before["overall_status"] == "Completed" and after["overall_status"] != "Completed":
            regressed_completed.append(pid)
            flag += "  <-- COMPLETED LEAD REOPENED"
        if before["completion"] == 100.0 and after["completion"] < 100.0:
            regressed_full.append(pid)
            flag += "  <-- 100% LEAD FELL BELOW 100%"
        if after["completion"] < before["completion"]:
            rebaselined.append(pid)
            flag += "  (percent re-baselined)"
        if (before["pipeline"] != "bp"
                and stage_index.get(after["stage"], 9) < stage_index.get(before["stage"], -1)):
            stage_moved_back.append(pid)
            flag += "  <-- BOARD COLUMN MOVED BACK"
        print(f"{pid:>4}  {after['name']:<22} {after['pipeline']:<8} {stage:<40} "
              f"{status:<26} {pct:<14} {f'{done_pre}/12 -> {done_post}/12':<10}{flag}")

    renamed = sum(_total(counts_post, new) for new in V5_RENAMES.values())
    merged = _total(counts_post, V5_MERGED, active=1)
    retired = (_total(counts_post, "Trap CoS", active=0)
               + _total(counts_post, "Seal CoS", active=0)
               + _total(counts_post, V5_RETIRED_STEP, active=0))
    inserted_approved = sum(_total(counts_post, name, active=1, status="Approved")
                            - _total(counts_pre, name, active=1, status="Approved")
                            for name in V5_NEW_STEPS)
    inserted_total = sum(_total(counts_post, name) - _total(counts_pre, name)
                         for name in V5_NEW_STEPS)
    changed_stage = [pid for pid in ids
                     if pid in pre and pid in post and pre[pid]["stage"] != post[pid]["stage"]]
    newly_completed = [pid for pid in ids
                       if pid in pre and pid in post
                       and pre[pid]["overall_status"] != "Completed"
                       and post[pid]["overall_status"] == "Completed"]

    print()
    print("SUMMARY")
    print("-" * 60)
    print(f"  projects total                       {len(ids)}")
    print(f"  ... of which leads (prospect)         {len(leads)}")
    print(f"  renamed rows (5 steps, in place)      {renamed}")
    print(f"  merged rows created (Trap and Seal)   {merged}")
    print(f"  retired rows (Trap/Seal/Well Creation){retired:>4}")
    print(f"  inserted rows (GRV + Well Site Loc.)  {inserted_total}")
    print(f"      ... Approved backfill             {inserted_approved}")
    print(f"      ... Not Assigned                  {inserted_total - inserted_approved}")
    print(f"  display stage changed                 {len(changed_stage)}")
    print(f"  leads that became Completed           {len(newly_completed)}"
          f"{'  ' + str(newly_completed) if newly_completed else ''}")
    print()
    print("  INVARIANTS (a violation exits 1)")
    print(f"    board column never moves back       {len(stage_moved_back)}"
          f"{'  OK' if not stage_moved_back else '  VIOLATED ' + str(stage_moved_back)}")
    print(f"    items-done never falls              {'OK' if not regressed_items else 'VIOLATED ' + str(regressed_items)}")
    print(f"    Completed stays Completed           {'OK' if not regressed_completed else 'VIOLATED ' + str(regressed_completed)}")
    print(f"    100% stays 100%                     {'OK' if not regressed_full else 'VIOLATED ' + str(regressed_full)}")
    print()
    print("  EXPECTED RE-BASELINING (informational -- see the module docstring)")
    print(f"    in-flight leads whose % re-based    {len(rebaselined)}  {rebaselined if rebaselined else ''}")
    print("    (the checklist itself changed: two halves became one step, one step")
    print("     retired, and a stage still in flight gained an un-started row.)")
    print("-" * 60)
    return 1 if (stage_moved_back or regressed_items or regressed_completed
                 or regressed_full) else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("database", help="path to the database to dry-run against (never written)")
    parser.add_argument("--devolve", action="store_true",
                        help="the database is ALREADY at v5 (e.g. a running dev server "
                             "bootstrapped it): rebuild the pre-v5 shape on the COPY first, "
                             "so the rehearsal has a real 'before'. Never touches the source.")
    args = parser.parse_args(argv)

    source = Path(args.database).resolve()
    if not source.exists():
        print(f"No such database: {source}", file=sys.stderr)
        return 2

    workdir = Path(tempfile.mkdtemp(prefix="v5-dryrun-"))
    copy = workdir / "copy.db"
    try:
        copy_database(source, copy)
        version = stored_version(copy)
        print(f"source     : {source}")
        print(f"copy       : {copy}")
        print(f"stamped schema_version: {version}")
        if version is None:
            print("\nThis database has no app_settings/schema_version row -- it predates the "
                  "numbered-migration era, so migrations.run cannot upgrade it in place. "
                  "SKIPPING (not forcing).", file=sys.stderr)
            return 2

        if args.devolve:
            devolved = devolve_to_v4(copy)
            print(f"--devolve  : rebuilt the pre-v5 shape on the copy "
                  f"({devolved} projects); schema_version now {stored_version(copy)}")

        pre = snapshot(copy, pre=True)
        counts_pre = row_counts(copy)
        try:
            bootstrap(copy)
        except Exception as exc:                                   # noqa: BLE001
            print(f"\nBootstrap REFUSED this database: {exc}", file=sys.stderr)
            return 2
        post = snapshot(copy, pre=False)
        counts_post = row_counts(copy)
        print(f"stamped schema_version after: {stored_version(copy)}")
        return render(pre, post, counts_pre, counts_post)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
