#!/usr/bin/env python
"""Backfill surface-derived values across every non-archived project.

    .venv/bin/python scripts/backfill_surfaces.py pipeline_tracker.db
    .venv/bin/python scripts/backfill_surfaces.py pipeline_tracker.db --dry-run

Runs the fills from workflow/surfaces_fill.py -- the TSQ thickness auto-fill
(Trap and Seal CoS ``sarah_quwarah_thickness_ft``, only where empty) and the
machine-derived ``projects.ground_elevation`` (overwritten freely), plus the
two governed BP Gate calculations -- over every NON-ARCHIVED project, and
prints a per-project report:

    filled            the surface produced a value and it was written
    had-value         (TSQ only) the field already holds a value; manual wins
    no-coords         the project resolves neither a staked nor a lead pair
    no-value-here     coordinates resolve, but the surface has no value at the
                      point (outside its extents, or a null hole)
    no-surface        the surface file itself is absent/unconfigured
    no-step           (TSQ only) the project has no active Trap and Seal CoS row

--dry-run NEVER WRITES THE DATABASE YOU POINT IT AT: like
scripts/migration_dryrun_v5.py it takes a byte-exact copy through the SQLite
BACKUP API (so a live -wal/-shm pair is captured consistently) and does all of
its work -- including the bootstrap -- on that copy, reporting what a real run
WOULD do. A real run bootstraps the target first (``db.init_db`` ->
``migrations.run``), which is what guarantees the v6 ground_elevation column
exists; that bootstrap is the same guarded, append-only upgrade an app restart
performs.

EXIT CODES
  0  ran to completion (including "nothing to do")
  2  the database could not be opened/bootstrapped
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

# The same consistent-copy helper the v5 dry-run uses (the audit script's
# precedent): a --dry-run must never write the source, not even a checkpoint.
from migration_dryrun_v5 import copy_database  # noqa: E402


def _tsq_state(session, project_id):
    """'no-step' / 'had-value' / 'empty' for the project's Trap and Seal row --
    the classification fill_tsq itself decides silently (it just returns None),
    made visible for the report."""
    import db
    from workflow import surfaces_fill

    task = db.fetch_one(session, """
        SELECT task_id FROM project_tasks
        WHERE project_id = :project_id AND task_name = :task_name AND is_active = 1
        ORDER BY task_id DESC
        LIMIT 1
    """, {"project_id": project_id, "task_name": surfaces_fill.TSQ_TASK_NAME})
    if not task:
        return "no-step"
    existing = db.fetch_one(session, """
        SELECT field_value FROM task_dynamic_fields
        WHERE task_id = :task_id AND field_key = :field_key
    """, {"task_id": task["task_id"], "field_key": surfaces_fill.TSQ_FIELD_KEY})
    if existing is not None and str(existing["field_value"] or "").strip():
        return "had-value"
    return "empty"


def run_backfill(db_path, apply_writes):
    """Bootstrap ``db_path``, run/classify both fills per project, print the
    report. Returns the process exit code."""
    os.environ["SEGMENT_TRACKER_DB_PATH"] = str(db_path)
    import config
    import db
    import surfaces
    from workflow import surfaces_fill

    try:
        db.reset_for_tests()
        db.init_db(str(db_path))
    except Exception as exc:  # noqa: BLE001
        print("Bootstrap REFUSED this database: {}".format(exc), file=sys.stderr)
        return 2

    tsq_path = config.tsq_surface_file()
    elevation_path = config.ground_elevation_surface_file()
    sarh_path = config.sarh_thickness_surface_file()
    print("database             : {}".format(db_path))
    print("mode                 : {}".format("apply" if apply_writes else "DRY RUN (on a copy)"))
    print("TSQ surface          : {} ({})".format(
        tsq_path, "found" if tsq_path.exists() else "MISSING -- tsq skipped"))
    print("elevation surface    : {} ({})".format(
        elevation_path, "found" if elevation_path.exists() else "MISSING -- elevation skipped"))
    print("BP SARH surface      : {} ({})".format(
        sarh_path, "found" if sarh_path.exists() else "MISSING -- BP TD unavailable"))
    print("BP calculation config: {} ({})".format(
        config.bp_calculations_path(),
        "valid" if config.bp_calculations() is not None else "MISSING/INVALID -- BP outputs unavailable"))

    session = db.new_session()
    counts = {"tsq": {}, "elev": {}}

    def note(surface, outcome):
        counts[surface][outcome] = counts[surface].get(outcome, 0) + 1
        return outcome

    try:
        projects = db.fetch_all(session, """
            SELECT project_id, project_name FROM projects
            WHERE archived = 0
            ORDER BY project_id
        """)
        print()
        print("PER-PROJECT")
        print("-" * 78)
        for project in projects:
            project_id = project["project_id"]
            position = surfaces_fill._resolve_coordinates(session, project_id)

            # Classify first (read-only), so the dry run reports the same
            # outcomes a real run would produce.
            if not tsq_path.exists():
                tsq = note("tsq", "no-surface")
            else:
                state = _tsq_state(session, project_id)
                if state != "empty":
                    tsq = note("tsq", state)
                elif position is None:
                    tsq = note("tsq", "no-coords")
                elif surfaces.sample_surface(tsq_path, position[0], position[1]) is None:
                    tsq = note("tsq", "no-value-here")
                else:
                    tsq = note("tsq", "filled")

            if not elevation_path.exists():
                elevation = note("elev", "no-surface")
            elif position is None:
                elevation = note("elev", "no-coords")
            elif surfaces.sample_surface(elevation_path, position[0], position[1]) is None:
                elevation = note("elev", "no-value-here")
            else:
                elevation = note("elev", "filled")

            tsq_value = elevation_value = None
            bp_values = None
            if apply_writes:
                with db.write_transaction(session):
                    if tsq == "filled":
                        tsq_value = surfaces_fill.fill_tsq(session, project_id)
                    if elevation == "filled":
                        elevation_value = surfaces_fill.fill_ground_elevation(session, project_id)
                    bp_values = surfaces_fill.fill_bp_calculations(session, project_id)
            else:
                # --dry-run operates on a disposable SQLite backup, so running
                # the governed calculation there gives an exact preview while
                # the source database remains byte-for-byte untouched.
                with db.write_transaction(session):
                    bp_values = surfaces_fill.fill_bp_calculations(session, project_id)

            def _shown(outcome, value):
                if outcome != "filled":
                    return outcome
                if value is not None:
                    return "filled {:.2f}".format(value)
                return "would fill" if not apply_writes else "filled"

            bp_status = "not-bp"
            if bp_values:
                bp_status = "td={}, days={}".format(
                    bp_values["td"]["status"], bp_values["days"]["status"])
            print("{:>5}  {:<28} tsq: {:<18} elevation: {:<18} bp: {}".format(
                project_id, project["project_name"][:28],
                _shown(tsq, tsq_value), _shown(elevation, elevation_value), bp_status))
    finally:
        session.close()
        db.reset_for_tests()

    print()
    print("SUMMARY ({} non-archived projects)".format(len(projects)))
    print("-" * 40)
    for surface, label in (("tsq", "TSQ thickness"), ("elev", "ground elevation")):
        tally = ", ".join("{} {}".format(count, outcome)
                          for outcome, count in sorted(counts[surface].items())) or "nothing to do"
        print("  {:<18} {}".format(label, tally))
    if not apply_writes:
        print("\n(dry run: the source database was not written; re-run without "
              "--dry-run to apply)")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("database", help="path to the database to backfill")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be filled without writing the source "
                             "database (all work happens on a temp copy)")
    args = parser.parse_args(argv)

    source = Path(args.database).resolve()
    if not source.exists():
        print("No such database: {}".format(source), file=sys.stderr)
        return 2

    if not args.dry_run:
        return run_backfill(source, apply_writes=True)

    workdir = Path(tempfile.mkdtemp(prefix="surface-backfill-dryrun-"))
    try:
        copy = workdir / "copy.db"
        try:
            copy_database(source, copy)
        except Exception as exc:  # noqa: BLE001 -- command-line boundary
            print("Could not copy database for dry run: {}".format(exc), file=sys.stderr)
            return 2
        return run_backfill(copy, apply_writes=False)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
