"""Sync domain roles, memberships, and task mappings from a JSON manifest.

Usage:
    .venv/bin/python scripts/sync_domain_assignments.py --manifest manifest.json

Manifest shape:
    {
        "roles": [{"name": "Petrophysicist"}, ...],
        "memberships": [{"user": "Alice", "role": "Petrophysicist"}, ...],
        "mappings": [{"task": "Quicklook Logs", "role": "Petrophysicist"}, ...]
    }

The script validates the manifest, then performs an atomic sync inside a single
write_transaction: missing roles are created, roles not in the manifest are
removed, memberships are reconciled exactly, and task mappings are reconciled
exactly. It prints a summary of what changed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Set

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
from helpers import utc_now_str
from workflow.constants import PIPELINE_TEMPLATES
from workflow.domain_roles import (get_role, get_task_mapping,
                                   remove_creator_assignments_for_mapped_task_locked)
from workflow.users import find_active_user


def _load_manifest(path: str) -> Dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"Manifest file not found: {path}")
    with p.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("Manifest must be a JSON object.")
    return data


def _validate_manifest(session, manifest: Dict[str, Any]) -> None:
    """Validate the manifest contents; raise ValueError on any problem."""
    # Missing sections mean an empty desired set.  Present sections must still
    # be arrays: using ``or []`` here would silently turn falsy malformed values
    # such as ``{}``, ``""``, or ``None`` into an exact-empty sync.
    roles = manifest.get("roles", [])
    memberships = manifest.get("memberships", [])
    mappings = manifest.get("mappings", [])

    if not isinstance(roles, list):
        raise ValueError("roles must be an array.")
    if not isinstance(memberships, list):
        raise ValueError("memberships must be an array.")
    if not isinstance(mappings, list):
        raise ValueError("mappings must be an array.")

    # Role names must be unique and non-blank.
    seen_roles: Set[str] = set()
    for item in roles:
        if not isinstance(item, dict):
            raise ValueError("Each role entry must be an object.")
        name = str(item.get("name") or "").strip()
        if not name:
            raise ValueError("Role name is required.")
        if name.lower() in {r.lower() for r in seen_roles}:
            raise ValueError(f"Duplicate role name in manifest: {name}")
        seen_roles.add(name)

    # Membership users must exist and be active.
    for item in memberships:
        if not isinstance(item, dict):
            raise ValueError("Each membership entry must be an object.")
        user = str(item.get("user") or "").strip()
        role = str(item.get("role") or "").strip()
        if not user:
            raise ValueError("Membership user is required.")
        if not role:
            raise ValueError("Membership role is required.")
        user_row = find_active_user(session, user)
        if not user_row:
            raise ValueError(f"Unknown or inactive user: {user}")

    # Task names must exist in PIPELINE_TEMPLATES; each task at most one mapping.
    valid_task_names = {task_name for _seq, task_name, _stage in PIPELINE_TEMPLATES}
    seen_tasks: Set[str] = set()
    for item in mappings:
        if not isinstance(item, dict):
            raise ValueError("Each mapping entry must be an object.")
        task = str(item.get("task") or "").strip()
        role = str(item.get("role") or "").strip()
        if not task:
            raise ValueError("Mapping task is required.")
        if not role:
            raise ValueError("Mapping role is required.")
        if task not in valid_task_names:
            raise ValueError(f"Unknown task name: {task}")
        if task in seen_tasks:
            raise ValueError(f"Task '{task}' has more than one role mapping.")
        seen_tasks.add(task)

    # Every role referenced by memberships/mappings must be declared in roles.
    declared_lower = {r.lower() for r in seen_roles}
    for item in memberships:
        if str(item.get("role") or "").strip().lower() not in declared_lower:
            raise ValueError(
                f"Membership references undeclared role: {item.get('role')}")
    for item in mappings:
        if str(item.get("role") or "").strip().lower() not in declared_lower:
            raise ValueError(
                f"Mapping references undeclared role: {item.get('role')}")


def _sync(session, manifest: Dict[str, Any]) -> Dict[str, List[str]]:
    """Perform the atomic sync; return a summary of changes."""
    roles = manifest.get("roles", [])
    memberships = manifest.get("memberships", [])
    mappings = manifest.get("mappings", [])
    now = utc_now_str()

    created_roles: List[str] = []
    removed_roles: List[str] = []
    added_memberships: List[str] = []
    removed_memberships: List[str] = []
    set_mappings: List[str] = []
    removed_mappings: List[str] = []

    with db.write_transaction(session):
        # Ensure roles exist.
        role_ids: Dict[str, int] = {}
        role_names: Dict[int, str] = {}
        for item in roles:
            name = str(item.get("name") or "").strip()
            existing = get_role(session, name)
            if existing:
                role_ids[name.lower()] = existing["role_id"]
                role_names[existing["role_id"]] = existing["role_name"]
            else:
                result = db.execute(session, """
                    INSERT INTO domain_roles (role_name, created_at)
                    VALUES (:name, :now)
                """, {"name": name, "now": now})
                role_id = result.lastrowid
                created_roles.append(name)
                role_ids[name.lower()] = role_id
                role_names[role_id] = name
                db.execute(session, """
                    DELETE FROM app_settings WHERE key = :key
                """, {"key": f"role_deleted:{name.lower()}"})

        # Remove roles not in the manifest.
        declared_lower = set(role_ids.keys())
        existing_roles = db.fetch_all(session, "SELECT role_id, role_name FROM domain_roles")
        for role_row in existing_roles:
            if role_row["role_name"].lower() not in declared_lower:
                # Record mapping deletions before deleting the role.  Otherwise
                # the FK/cascade cleanup makes them invisible to the mapping
                # reconciliation below, and re-adding a default role later
                # allows bootstrap to recreate mappings intentionally omitted
                # from the exact manifest.
                removed_role_mappings = db.fetch_all(session, """
                    SELECT task_name FROM task_domain_role_mappings
                    WHERE role_id = :role_id
                """, {"role_id": role_row["role_id"]})
                for mapping_row in removed_role_mappings:
                    task_name = mapping_row["task_name"]
                    db.execute(session, """
                        INSERT OR IGNORE INTO app_settings (key, value)
                        VALUES (:key, '1')
                    """, {"key": f"mapping_deleted:{task_name}"})
                    removed_mappings.append(task_name)
                db.execute(session, "DELETE FROM domain_role_memberships WHERE role_id = :role_id",
                           {"role_id": role_row["role_id"]})
                db.execute(session, "DELETE FROM task_domain_role_mappings WHERE role_id = :role_id",
                           {"role_id": role_row["role_id"]})
                db.execute(session, "DELETE FROM domain_roles WHERE role_id = :role_id",
                           {"role_id": role_row["role_id"]})
                db.execute(session, """
                    INSERT OR IGNORE INTO app_settings (key, value) VALUES (:key, '1')
                """, {"key": f"role_deleted:{role_row['role_name'].lower()}"})
                removed_roles.append(role_row["role_name"])

        # Reconcile memberships exactly.
        desired_memberships: Set[tuple] = set()
        for item in memberships:
            user_name = str(item.get("user") or "").strip()
            role_name = str(item.get("role") or "").strip()
            role_id = role_ids[role_name.lower()]
            user_row = find_active_user(session, user_name)
            canonical_user = user_row["name"]
            desired_memberships.add((role_id, canonical_user))

        existing_memberships = db.fetch_all(session, """
            SELECT role_id, user_name FROM domain_role_memberships
        """)
        existing_set: Set[tuple] = {(r["role_id"], r["user_name"]) for r in existing_memberships}

        for role_id, user_name in desired_memberships - existing_set:
            db.execute(session, """
                INSERT INTO domain_role_memberships (user_name, role_id, is_active, created_at)
                VALUES (:user_name, :role_id, 1, :now)
            """, {"user_name": user_name, "role_id": role_id, "now": now})
            added_memberships.append(f"{user_name} -> {role_names[role_id]}")

        for role_id, user_name in existing_set - desired_memberships:
            db.execute(session, """
                DELETE FROM domain_role_memberships
                WHERE role_id = :role_id AND user_name = :user_name
            """, {"role_id": role_id, "user_name": user_name})
            removed_memberships.append(f"{user_name} -> {role_names[role_id]}")

        # Reconcile task mappings exactly.
        desired_mappings: Dict[str, int] = {}
        for item in mappings:
            task_name = str(item.get("task") or "").strip()
            role_name = str(item.get("role") or "").strip()
            role_id = role_ids[role_name.lower()]
            desired_mappings[task_name] = role_id

        existing_mappings = db.fetch_all(session, """
            SELECT task_name, role_id FROM task_domain_role_mappings
        """)
        existing_map: Dict[str, int] = {r["task_name"]: r["role_id"] for r in existing_mappings}

        for task_name, role_id in desired_mappings.items():
            if existing_map.get(task_name) == role_id:
                continue
            db.execute(session, """
                INSERT INTO task_domain_role_mappings (task_name, role_id, created_at)
                VALUES (:task_name, :role_id, :now)
                ON CONFLICT(task_name) DO UPDATE SET
                    role_id = excluded.role_id,
                    created_at = excluded.created_at
            """, {"task_name": task_name, "role_id": role_id, "now": now})
            db.execute(session, """
                DELETE FROM app_settings WHERE key = :key
            """, {"key": f"mapping_deleted:{task_name}"})
            remove_creator_assignments_for_mapped_task_locked(session, task_name, now)
            set_mappings.append(f"{task_name} -> {role_names[role_id]}")

        for task_name in existing_map:
            if task_name not in desired_mappings:
                db.execute(session, """
                    DELETE FROM task_domain_role_mappings WHERE task_name = :task_name
                """, {"task_name": task_name})
                db.execute(session, """
                    INSERT OR IGNORE INTO app_settings (key, value) VALUES (:key, '1')
                """, {"key": f"mapping_deleted:{task_name}"})
                removed_mappings.append(task_name)

    return {
        "created_roles": created_roles,
        "removed_roles": removed_roles,
        "added_memberships": added_memberships,
        "removed_memberships": removed_memberships,
        "set_mappings": set_mappings,
        "removed_mappings": removed_mappings,
    }


def main(argv: List[str] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sync domain roles, memberships, and task mappings.")
    parser.add_argument("--manifest", required=True, help="Path to JSON manifest.")
    args = parser.parse_args(argv)

    db.init_db()
    session = db.new_session()
    try:
        manifest = _load_manifest(args.manifest)
        _validate_manifest(session, manifest)
        summary = _sync(session, manifest)

        print("Sync complete.")
        print(f"  Roles created: {len(summary['created_roles'])}")
        for name in summary["created_roles"]:
            print(f"    + {name}")
        print(f"  Roles removed: {len(summary['removed_roles'])}")
        for name in summary["removed_roles"]:
            print(f"    - {name}")
        print(f"  Memberships added: {len(summary['added_memberships'])}")
        for item in summary["added_memberships"]:
            print(f"    + {item}")
        print(f"  Memberships removed: {len(summary['removed_memberships'])}")
        for item in summary["removed_memberships"]:
            print(f"    - {item}")
        print(f"  Mappings set/changed: {len(summary['set_mappings'])}")
        for item in summary["set_mappings"]:
            print(f"    + {item}")
        print(f"  Mappings removed: {len(summary['removed_mappings'])}")
        for item in summary["removed_mappings"]:
            print(f"    - {item}")
        return 0
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
