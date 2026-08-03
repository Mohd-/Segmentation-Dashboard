#!/usr/bin/env python
"""Dry-run migration v7 against a SQLite backup, never the source database.

Usage::

    .venv/bin/python scripts/migration_dryrun_v7.py pipeline_tracker.db
    .venv/bin/python scripts/migration_dryrun_v7.py pipeline_tracker.db --devolve

The normal path expects a schema-v6 database. ``--devolve`` supports the common
rehearsal case where the source has already reached v7: it reconstructs the v6
four-row Lead Assessment shape *only on the temporary backup*, then runs the
real application bootstrap back to v7.
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

from workflow.constants import (BP_EXECUTION_STAGES, LEAD_ASSESSMENT_CHECKPOINTS,  # noqa: E402
                                PROSPECT_STAGES,
                                lead_assessment_checkpoint_met)

MERGED_STEP = "Lead Assessment"
LEGACY_STEPS = tuple(LEAD_ASSESSMENT_CHECKPOINTS)
STATUS_RANK = {"Not Assigned": 0, "In Progress": 1, "Ready": 2, "Approved": 3}
V6_SEQUENCE = {
    "Area Definition": 1,
    "Thickness Estimation": 2,
    "GRV Inputs": 3,
    "Resource Assessment": 4,
    "Reservoir CoS": 5,
    "Trap and Seal CoS": 6,
    "Seismic Signature Validation": 7,
    "Segmentation Slides": 8,
    "Moving Tolerance": 9,
    "Approval to Stake": 10,
    "Well Site Location": 11,
    "Pre-Drilling GeoX Assessment": 12,
}

_AREA_KEYS = {"p90_area_km2", "p10_area_km2", "top_formation_tvdss_ft"}
_THICKNESS_KEYS = {
    "twt_reservoir_ms", "twt_formation_ms", "reservoir_thickness_ft",
    "formation_thickness_ft", "thickness_source_mode",
}
_GRV_KEYS = {"grv_p90_thousand_acre_ft", "grv_p10_thousand_acre_ft"}


def _connect(path: Path, *, readonly: bool = False):
    target = path.resolve().as_uri() + "?mode=ro" if readonly else str(path)
    conn = sqlite3.connect(target, uri=readonly)
    conn.row_factory = sqlite3.Row
    if not readonly:
        conn.execute("PRAGMA foreign_keys = ON")
    return conn


def copy_database(source: Path, destination: Path) -> None:
    """Take a WAL-consistent backup while opening the source read-only."""
    source = Path(source).resolve()
    destination = Path(destination).resolve()
    src = _connect(source, readonly=True)
    try:
        dst = _connect(destination)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def stored_version(db_path: Path):
    try:
        # This helper is also used on macOS's per-user /var temp symlink, where
        # SQLite URI mode=ro can be rejected by the host sandbox. It performs
        # only SELECTs; the user-supplied source itself is still opened mode=ro
        # by copy_database, the only function that ever touches that path.
        conn = _connect(Path(db_path))
        try:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = 'schema_version'").fetchone()
            return int(row["value"]) if row else None
        finally:
            conn.close()
    except (sqlite3.Error, TypeError, ValueError):
        return None


def bootstrap(db_path: Path) -> None:
    """Run the application's real bootstrap against the disposable copy."""
    os.environ["SEGMENT_TRACKER_DB_PATH"] = str(db_path)
    import db as dbmod

    dbmod.reset_for_tests()
    dbmod.init_db(str(db_path))
    dbmod.reset_for_tests()


def _legacy_owner(field_key: str) -> str:
    """Put each merged EAV key back on its v6 owner without dropping data.

    Current Card 2B keys have explicit owners. PIIP output keys and any custom
    historical key fall back to Resource Assessment, the v6 calculation owner;
    this preserves every row and keeps all unknown keys on one source task, so
    the real v7 migration can merge them again without a cross-task collision.
    """
    if field_key in _AREA_KEYS:
        return "Area Definition"
    if field_key in _THICKNESS_KEYS:
        return "Thickness Estimation"
    if field_key in _GRV_KEYS:
        return "GRV Inputs"
    return "Resource Assessment"


def devolve_to_v6(db_path: Path) -> int:
    """Invert v7 on a disposable v7 copy and stamp it v6.

    Migrated databases retain the four inactive source rows, including their
    original lifecycle states. Fresh v7 databases do not; for those, four
    synthetic source rows inherit the merged row's lifecycle metadata. Both
    shapes retain every EAV row and are sufficient to rehearse the real merge.
    Partial legacy shapes are refused rather than guessed.

    Any v7-OR-NEWER stamp qualifies: later steps (v8 repair fold, v9 lead-level
    priority) never change the merged Lead Assessment shape this tool inverts.
    """
    if (stored_version(db_path) or 0) < 7:
        raise RuntimeError("--devolve requires a schema-v7-or-newer database copy")

    conn = _connect(Path(db_path))
    try:
        conn.execute("BEGIN IMMEDIATE")
        projects = conn.execute(
            "SELECT project_id FROM projects ORDER BY project_id").fetchall()

        for project in projects:
            pid = project["project_id"]
            merged = conn.execute(
                "SELECT * FROM project_tasks WHERE project_id = ? AND task_name = ?",
                (pid, MERGED_STEP)).fetchone()
            legacy = conn.execute(
                "SELECT * FROM project_tasks WHERE project_id = ? AND task_name IN (?,?,?,?) "
                "ORDER BY task_id", (pid,) + LEGACY_STEPS).fetchall()
            if legacy and {row["task_name"] for row in legacy} != set(LEGACY_STEPS):
                raise RuntimeError(
                    f"Cannot safely devolve project {pid}: partial v6 Lead Assessment rows")

            # A database stamped v7 by an earlier build may still carry a BP
            # record in the untouched v6 shape. It is already a valid rehearsal
            # baseline; include it in the all-project count and leave it alone.
            if merged is None:
                if len(legacy) == 4 and all(row["is_active"] == 1 for row in legacy):
                    for name, sequence in V6_SEQUENCE.items():
                        conn.execute(
                            "UPDATE project_tasks SET sequence_no = ? WHERE project_id = ? "
                            "AND task_name = ? AND is_active = 1", (sequence, pid, name))
                    continue
                raise RuntimeError(
                    f"Cannot safely devolve project {pid}: no active v7 survivor or complete v6 shape")

            if merged["is_active"] != 1:
                raise RuntimeError(f"Cannot safely devolve project {pid}: v7 survivor is inactive")

            if not legacy:
                for name in LEGACY_STEPS:
                    conn.execute("""
                        INSERT INTO project_tasks (
                            project_id, sequence_no, task_name, stage_group, assigned_to,
                            status, actual_start, actual_finish, comments, priority,
                            business_plan_enabled, business_plan_year, is_active,
                            last_updated, revision
                        ) VALUES (?, ?, ?, 'Lead Assessment', ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """, (
                        pid, V6_SEQUENCE[name], name, merged["assigned_to"], merged["status"],
                        merged["actual_start"], merged["actual_finish"], merged["comments"],
                        merged["priority"], merged["business_plan_enabled"],
                        merged["business_plan_year"], merged["last_updated"], merged["revision"],
                    ))
                legacy = conn.execute(
                    "SELECT * FROM project_tasks WHERE project_id = ? AND task_name IN (?,?,?,?)",
                    (pid,) + LEGACY_STEPS).fetchall()
            else:
                conn.execute(
                    "UPDATE project_tasks SET is_active = 1 WHERE project_id = ? "
                    "AND task_name IN (?,?,?,?)", (pid,) + LEGACY_STEPS)

            owner_ids = {row["task_name"]: row["task_id"] for row in legacy}
            fields = conn.execute(
                "SELECT id, field_key FROM task_dynamic_fields WHERE task_id = ? ORDER BY id",
                (merged["task_id"],)).fetchall()
            for field in fields:
                conn.execute("UPDATE task_dynamic_fields SET task_id = ? WHERE id = ?",
                             (owner_ids[_legacy_owner(field["field_key"])], field["id"]))

            # Only survivor-owned v7 audit rows are removed. History attached to
            # retained legacy task ids is the original audit trail and remains.
            conn.execute("DELETE FROM task_history WHERE task_id = ?", (merged["task_id"],))
            conn.execute("DELETE FROM project_tasks WHERE task_id = ?", (merged["task_id"],))
            for name, sequence in V6_SEQUENCE.items():
                conn.execute(
                    "UPDATE project_tasks SET sequence_no = ? WHERE project_id = ? "
                    "AND task_name = ? AND is_active = 1", (sequence, pid, name))

        conn.execute("UPDATE app_settings SET value = '6' WHERE key = 'schema_version'")
        conn.commit()
        return len(projects)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _fields_for(conn, task_ids):
    if not task_ids:
        return {}
    marks = ",".join("?" for _ in task_ids)
    rows = conn.execute(
        f"SELECT field_key, field_value FROM task_dynamic_fields WHERE task_id IN ({marks}) "
        "ORDER BY id", tuple(task_ids)).fetchall()
    return {row["field_key"]: row["field_value"] for row in rows}


def snapshot(db_path: Path, *, pre: bool):
    """Capture the v6 baseline or v7 result in the fixed 12-item vocabulary."""
    conn = _connect(Path(db_path))
    try:
        projects = conn.execute("""
            SELECT project_id, project_name,
                   lower(COALESCE(pipeline_type, 'prospect')) AS pipeline_type
            FROM projects ORDER BY project_id
        """).fetchall()
        out = {}
        for project in projects:
            pid = project["project_id"]
            rows = conn.execute("""
                SELECT task_id, task_name, stage_group, status, is_active FROM project_tasks
                WHERE project_id = ? ORDER BY sequence_no, task_id
            """, (pid,)).fetchall()
            pipeline = project["pipeline_type"]
            applicable_stages = BP_EXECUTION_STAGES if pipeline == "bp" else PROSPECT_STAGES
            active = [row for row in rows
                      if row["is_active"] == 1 and row["stage_group"] in applicable_stages]
            by_name = {row["task_name"]: row for row in active}
            if pre:
                # BP completion uses BP execution rows, but its universal v6
                # source rows still live outside that applicable-stage subset.
                all_active = {row["task_name"]: row for row in rows if row["is_active"] == 1}
                sources = [all_active.get(name) for name in LEGACY_STEPS]
                if any(row is None for row in sources):
                    raise RuntimeError(f"Project {pid} is not in the complete v6 four-row shape")
                source_statuses = {row["task_name"]: row["status"] for row in sources}
                merged_status = min(source_statuses.values(), key=lambda value: STATUS_RANK[value])
                fields = _fields_for(conn, [row["task_id"] for row in sources])
            else:
                all_active = {row["task_name"]: row for row in rows if row["is_active"] == 1}
                merged = all_active.get(MERGED_STEP)
                if merged is None:
                    raise RuntimeError(f"Project {pid} has no active v7 Lead Assessment row")
                source_statuses = None
                merged_status = merged["status"]
                fields = _fields_for(conn, [merged["task_id"]])

            checkpoints = {
                name: lead_assessment_checkpoint_met(name, fields) for name in LEGACY_STEPS
            }
            if pipeline == "bp":
                completed = sum(row["status"] == "Approved" for row in active)
                denominator = len(active)
            else:
                ordinary = [row for row in active
                            if row["task_name"] not in LEGACY_STEPS + (MERGED_STEP,)]
                completed = sum(checkpoints.values()) + sum(
                    row["status"] == "Approved" for row in ordinary)
                denominator = 12
            out[pid] = {
                "name": project["project_name"], "pipeline": pipeline,
                "merged_status": merged_status,
                "source_statuses": source_statuses, "checkpoints": checkpoints,
                "fields": fields, "completed": completed,
                "percent": round(completed / denominator * 100, 1) if denominator else 0.0,
                "active": len(active),
            }
        return out
    finally:
        conn.close()


def render(before, after) -> int:
    regressions = []
    prospect_ids = [pid for pid, row in before.items() if row["pipeline"] != "bp"]
    print()
    print("PER-LEAD (v6 -> v7)")
    print("-" * 128)
    print(f"{'id':>4}  {'lead':<24} {'merged status':<31} {'Area':<10} {'Thick':<10} "
          f"{'GRV':<10} {'Resource':<10} {'completion':<18}")
    print("-" * 128)
    for pid in sorted(set(before) | set(after)):
        old = before.get(pid)
        new = after.get(pid)
        if old is None or new is None:
            regressions.append(f"project {pid} disappeared from one snapshot")
            continue
        expected = old["merged_status"]
        if new["merged_status"] != expected:
            regressions.append(
                f"[{pid}] merged status {new['merged_status']!r} != expected {expected!r}")
        if old["checkpoints"] != new["checkpoints"]:
            regressions.append(f"[{pid}] checkpoint states changed")
        if old["fields"] != new["fields"]:
            old_keys, new_keys = set(old["fields"]), set(new["fields"])
            changed = sorted(key for key in old_keys & new_keys
                             if old["fields"][key] != new["fields"][key])
            regressions.append(
                f"[{pid}] EAV projection changed (missing={sorted(old_keys - new_keys)}, "
                f"added={sorted(new_keys - old_keys)}, changed={changed})")
        if new["percent"] < old["percent"]:
            regressions.append(
                f"[{pid}] completion regressed {old['percent']:.1f}% -> {new['percent']:.1f}%")
        elif old["pipeline"] == "bp" and new["percent"] != old["percent"]:
            regressions.append(
                f"[{pid}] BP completion changed {old['percent']:.1f}% -> {new['percent']:.1f}%")
        if old["pipeline"] == "bp":
            continue
        cells = []
        for checkpoint in LEGACY_STEPS:
            a = "done" if old["checkpoints"][checkpoint] else "open"
            b = "done" if new["checkpoints"][checkpoint] else "open"
            cells.append(f"{a}->{b}")
        print(f"{pid:>4}  {old['name'][:24]:<24} "
              f"{expected + ' -> ' + new['merged_status']:<31} "
              f"{cells[0]:<10} {cells[1]:<10} {cells[2]:<10} {cells[3]:<10} "
              f"{old['percent']:>5.1f}% -> {new['percent']:>5.1f}%")

    print()
    print(f"records checked: {len(before)} ({len(prospect_ids)} prospect, "
          f"{len(before) - len(prospect_ids)} BP)")
    print(f"active task rows: {sum(v['active'] for v in before.values())} -> "
          f"{sum(v['active'] for v in after.values())}")
    if regressions:
        print(f"FAIL — {len(regressions)} migration invariant(s) failed")
        for message in regressions:
            print(f"  - {message}")
        return 1
    print("PASS — every record preserved its merged status, EAV projection and four "
          "checkpoint states; no completion percentage regressed")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("database", help="source SQLite database (opened read-only)")
    parser.add_argument("--devolve", action="store_true",
                        help="reconstruct v6 only on the backup before rehearsing v7")
    args = parser.parse_args(argv)
    source = Path(args.database).resolve()
    if not source.exists():
        print(f"No such database: {source}", file=sys.stderr)
        return 2

    workdir = Path(tempfile.mkdtemp(prefix="migration-v7-dryrun-"))
    copy = workdir / "copy.db"
    try:
        copy_database(source, copy)
        print(f"source: {source}")
        print(f"copy  : {copy}")
        version = stored_version(copy)
        print(f"schema_version before: {version}")
        if args.devolve:
            count = devolve_to_v6(copy)
            print(f"devolved {count} project(s) on the copy; schema_version now 6")
        elif version != 6:
            print("Expected schema version 6 (use --devolve to rehearse from v7).",
                  file=sys.stderr)
            return 2

        before = snapshot(copy, pre=True)
        try:
            bootstrap(copy)
        except Exception as exc:  # noqa: BLE001
            print(f"Migration refused the backup: {exc}", file=sys.stderr)
            return 2
        if (stored_version(copy) or 0) < 7:
            print("Migration did not reach schema version 7.", file=sys.stderr)
            return 2
        after = snapshot(copy, pre=False)
        return render(before, after)
    except (RuntimeError, sqlite3.Error, KeyError) as exc:
        print(f"Dry-run failed: {exc}", file=sys.stderr)
        return 2
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
