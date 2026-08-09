"""Domain roles: role catalog, memberships, task mappings, and assignee management.

Domain roles are distinct from auth roles (supervisor/staff/employee). A domain
role groups users by expertise area (Petrophysicist, Inversion Expert, etc.);
when a step mapped to that role becomes the next unapproved step in its active
pipeline, every active member is assigned and notified.

This module owns the domain_role* tables and the task_assignees table. It provides
functions for:
- Managing the role catalog (create/list roles)
- Managing user-to-role memberships (add/remove/list)
- Managing task-to-role mappings (one default role per task name)
- Querying active members of a role
- Querying the mapped role for a task
- Managing per-task multi-assignee rows

All writes go through ``db.write_transaction``; reads need no transaction.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import db
from helpers import utc_now_str


# ---------------------------------------------------------------------------
# Role catalog
# ---------------------------------------------------------------------------

def list_roles(session) -> List[Dict[str, Any]]:
    """Return all domain roles, ordered by name."""
    return db.fetch_all(session, """
        SELECT role_id, role_name, created_at
        FROM domain_roles
        ORDER BY role_name
    """)


def get_role(session, role_name: str) -> Optional[Dict[str, Any]]:
    """Return one role by name (case-insensitive), or None."""
    return db.fetch_one(session, """
        SELECT role_id, role_name, created_at
        FROM domain_roles
        WHERE LOWER(role_name) = LOWER(:role_name)
        LIMIT 1
    """, {"role_name": role_name})


def get_role_by_id(session, role_id: int) -> Optional[Dict[str, Any]]:
    """Return one role by id, or None."""
    return db.fetch_one(session, """
        SELECT role_id, role_name, created_at
        FROM domain_roles
        WHERE role_id = :role_id
    """, {"role_id": role_id})


def create_role(session, role_name: str) -> int:
    """Create a new domain role; return its id.

    Raises ValueError if the name is blank or already exists (case-insensitive).
    """
    name = (role_name or "").strip()
    if not name:
        raise ValueError("Role name is required.")
    existing = get_role(session, name)
    if existing:
        raise ValueError(f"Role '{name}' already exists.")
    now = utc_now_str()
    with db.write_transaction(session):
        result = db.execute(session, """
            INSERT INTO domain_roles (role_name, created_at)
            VALUES (:role_name, :created_at)
        """, {"role_name": name, "created_at": now})
        return result.lastrowid


def delete_role(session, role_id: int) -> None:
    """Delete a role and its memberships/mappings.

    Raises ValueError if the role does not exist.
    """
    role = get_role_by_id(session, role_id)
    if not role:
        raise ValueError("Role not found.")
    with db.write_transaction(session):
        db.execute(session, "DELETE FROM domain_role_memberships WHERE role_id = :role_id",
                   {"role_id": role_id})
        db.execute(session, "DELETE FROM task_domain_role_mappings WHERE role_id = :role_id",
                   {"role_id": role_id})
        db.execute(session, "DELETE FROM domain_roles WHERE role_id = :role_id",
                   {"role_id": role_id})


# ---------------------------------------------------------------------------
# Memberships
# ---------------------------------------------------------------------------

def list_memberships(session, role_id: Optional[int] = None,
                     user_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return memberships, optionally filtered by role or user."""
    params: Dict[str, Any] = {}
    clauses = []
    if role_id is not None:
        clauses.append("m.role_id = :role_id")
        params["role_id"] = role_id
    if user_name is not None:
        clauses.append("m.user_name = :user_name")
        params["user_name"] = user_name
    where = " AND ".join(clauses) if clauses else "1=1"
    return db.fetch_all(session, f"""
        SELECT m.id, m.user_name, m.role_id, r.role_name, m.is_active, m.created_at
        FROM domain_role_memberships m
        JOIN domain_roles r ON r.role_id = m.role_id
        WHERE {where}
        ORDER BY r.role_name, m.user_name
    """, params)


def get_user_roles(session, user_name: str) -> List[Dict[str, Any]]:
    """Return the active roles for a user."""
    return db.fetch_all(session, """
        SELECT r.role_id, r.role_name
        FROM domain_role_memberships m
        JOIN domain_roles r ON r.role_id = m.role_id
        WHERE m.user_name = :user_name AND m.is_active = 1
        ORDER BY r.role_name
    """, {"user_name": user_name})


def add_membership(session, user_name: str, role_id: int) -> int:
    """Add a user to a role; return the membership id.

    Raises ValueError if the user or role does not exist, or if the membership
    already exists.
    """
    from .users import find_active_user
    user = find_active_user(session, user_name)
    if not user:
        raise ValueError(f"Unknown or inactive user: {user_name}")
    role = get_role_by_id(session, role_id)
    if not role:
        raise ValueError("Role not found.")
    existing = db.fetch_one(session, """
        SELECT id FROM domain_role_memberships
        WHERE user_name = :user_name AND role_id = :role_id
    """, {"user_name": user["name"], "role_id": role_id})
    if existing:
        raise ValueError(f"User '{user['name']}' is already a member of role '{role['role_name']}'.")
    now = utc_now_str()
    with db.write_transaction(session):
        result = db.execute(session, """
            INSERT INTO domain_role_memberships (user_name, role_id, is_active, created_at)
            VALUES (:user_name, :role_id, 1, :created_at)
        """, {"user_name": user["name"], "role_id": role_id, "created_at": now})
        return result.lastrowid


def remove_membership(session, user_name: str, role_id: int) -> None:
    """Remove a user from a role.

    Raises ValueError if the membership does not exist.
    """
    existing = db.fetch_one(session, """
        SELECT id FROM domain_role_memberships
        WHERE user_name = :user_name AND role_id = :role_id
    """, {"user_name": user_name, "role_id": role_id})
    if not existing:
        raise ValueError("Membership not found.")
    with db.write_transaction(session):
        db.execute(session, """
            DELETE FROM domain_role_memberships
            WHERE user_name = :user_name AND role_id = :role_id
        """, {"user_name": user_name, "role_id": role_id})


def get_active_role_members(session, role_id: int) -> List[str]:
    """Return the active user names for a role."""
    rows = db.fetch_all(session, """
        SELECT m.user_name FROM domain_role_memberships m
        JOIN users u ON u.name = m.user_name AND u.is_active = 1
        WHERE m.role_id = :role_id AND m.is_active = 1
        ORDER BY m.user_name
    """, {"role_id": role_id})
    return [row["user_name"] for row in rows]


# ---------------------------------------------------------------------------
# Task-role mappings
# ---------------------------------------------------------------------------

def list_task_mappings(session) -> List[Dict[str, Any]]:
    """Return all task-to-role mappings."""
    return db.fetch_all(session, """
        SELECT m.id, m.task_name, m.role_id, r.role_name, m.created_at
        FROM task_domain_role_mappings m
        JOIN domain_roles r ON r.role_id = m.role_id
        ORDER BY m.task_name
    """)


def get_task_mapping(session, task_name: str) -> Optional[Dict[str, Any]]:
    """Return the mapping for a task name, or None."""
    return db.fetch_one(session, """
        SELECT m.id, m.task_name, m.role_id, r.role_name, m.created_at
        FROM task_domain_role_mappings m
        JOIN domain_roles r ON r.role_id = m.role_id
        WHERE m.task_name = :task_name
    """, {"task_name": task_name})


def get_task_mappings_for_names(session, task_names: List[str]) -> Dict[str, str]:
    """Return {task_name: role_name} for a batch of task names."""
    if not task_names:
        return {}
    rows = db.fetch_all(session, """
        SELECT m.task_name, r.role_name
        FROM task_domain_role_mappings m
        JOIN domain_roles r ON r.role_id = m.role_id
        WHERE m.task_name IN :task_names
    """, {"task_names": list(task_names)})
    return {row["task_name"]: row["role_name"] for row in rows}


def set_task_mapping(session, task_name: str, role_id: int) -> int:
    """Set the default role for a task name; return the mapping id.

    Raises ValueError if the task name is blank or the role does not exist.
    If a mapping already exists for this task name, it is replaced.
    """
    name = (task_name or "").strip()
    if not name:
        raise ValueError("Task name is required.")
    role = get_role_by_id(session, role_id)
    if not role:
        raise ValueError("Role not found.")
    now = utc_now_str()
    with db.write_transaction(session):
        existing = db.fetch_one(session, """
            SELECT id FROM task_domain_role_mappings WHERE task_name = :task_name
        """, {"task_name": name})
        if existing:
            db.execute(session, """
                UPDATE task_domain_role_mappings
                SET role_id = :role_id, created_at = :created_at
                WHERE task_name = :task_name
            """, {"role_id": role_id, "created_at": now, "task_name": name})
            return existing["id"]
        result = db.execute(session, """
            INSERT INTO task_domain_role_mappings (task_name, role_id, created_at)
            VALUES (:task_name, :role_id, :created_at)
        """, {"task_name": name, "role_id": role_id, "created_at": now})
        return result.lastrowid


def delete_task_mapping(session, task_name: str) -> None:
    """Delete the mapping for a task name.

    Raises ValueError if no mapping exists.
    """
    existing = get_task_mapping(session, task_name)
    if not existing:
        raise ValueError("Mapping not found.")
    with db.write_transaction(session):
        db.execute(session, """
            DELETE FROM task_domain_role_mappings WHERE task_name = :task_name
        """, {"task_name": task_name})


# ---------------------------------------------------------------------------
# Task assignees
# ---------------------------------------------------------------------------

def list_task_assignees(session, task_id: int) -> List[Dict[str, Any]]:
    """Return the assignees for a task, ordered by name."""
    return db.fetch_all(session, """
        SELECT id, task_id, assignee_name, source, notified, created_at
        FROM task_assignees
        WHERE task_id = :task_id
        ORDER BY assignee_name
    """, {"task_id": task_id})


def get_task_assignees_map(session, task_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
    """Return {task_id: [assignee rows]} for a batch of task ids."""
    if not task_ids:
        return {}
    rows = db.fetch_all(session, """
        SELECT id, task_id, assignee_name, source, notified, created_at
        FROM task_assignees
        WHERE task_id IN :task_ids
        ORDER BY task_id, assignee_name
    """, {"task_ids": task_ids})
    result: Dict[int, List[Dict[str, Any]]] = {}
    for row in rows:
        result.setdefault(row["task_id"], []).append(row)
    return result


def add_task_assignee(session, task_id: int, assignee_name: str,
                      source: str = "manual", notified: bool = False) -> int:
    """Add an assignee to a task; return the assignee row id.

    Raises ValueError if the task or user does not exist, or if the assignee
    is already on the task.
    """
    from .lifecycle import get_task
    from .users import find_active_user
    task = get_task(session, task_id)
    if not task:
        raise ValueError("Task not found.")
    user = find_active_user(session, assignee_name)
    if not user:
        raise ValueError(f"Unknown or inactive user: {assignee_name}")
    existing = db.fetch_one(session, """
        SELECT id FROM task_assignees
        WHERE task_id = :task_id AND assignee_name = :assignee_name
    """, {"task_id": task_id, "assignee_name": user["name"]})
    if existing:
        raise ValueError(f"User '{user['name']}' is already assigned to this task.")
    now = utc_now_str()
    with db.write_transaction(session):
        result = db.execute(session, """
            INSERT INTO task_assignees (task_id, assignee_name, source, notified, created_at)
            VALUES (:task_id, :assignee_name, :source, :notified, :created_at)
        """, {"task_id": task_id, "assignee_name": user["name"],
              "source": source, "notified": 1 if notified else 0, "created_at": now})
        return result.lastrowid


def remove_task_assignee(session, task_id: int, assignee_name: str) -> None:
    """Remove an assignee from a task.

    Raises ValueError if the assignment does not exist.
    """
    existing = db.fetch_one(session, """
        SELECT id FROM task_assignees
        WHERE task_id = :task_id AND assignee_name = :assignee_name
    """, {"task_id": task_id, "assignee_name": assignee_name})
    if not existing:
        raise ValueError("Assignee not found on this task.")
    with db.write_transaction(session):
        db.execute(session, """
            DELETE FROM task_assignees
            WHERE task_id = :task_id AND assignee_name = :assignee_name
        """, {"task_id": task_id, "assignee_name": assignee_name})


def mark_assignees_notified(session, task_id: int, assignee_names: List[str]) -> None:
    """Mark the specified assignees as notified."""
    if not assignee_names:
        return
    with db.write_transaction(session):
        db.execute(session, """
            UPDATE task_assignees SET notified = 1
            WHERE task_id = :task_id AND assignee_name IN :names AND notified = 0
        """, {"task_id": task_id, "names": assignee_names})


def get_primary_assignee(session, task_id: int) -> Optional[str]:
    """Return the primary (first alphabetically) assignee name, or None.

    Used for backward compatibility with code that expects a single assignee.
    """
    rows = list_task_assignees(session, task_id)
    return rows[0]["assignee_name"] if rows else None


def get_assignee_names(session, task_id: int) -> List[str]:
    """Return the assignee names for a task."""
    return [row["assignee_name"] for row in list_task_assignees(session, task_id)]


__all__ = [
    "list_roles", "get_role", "get_role_by_id", "create_role", "delete_role",
    "list_memberships", "get_user_roles", "add_membership", "remove_membership",
    "get_active_role_members",
    "list_task_mappings", "get_task_mapping", "get_task_mappings_for_names",
    "set_task_mapping", "delete_task_mapping",
    "list_task_assignees", "get_task_assignees_map", "add_task_assignee",
    "remove_task_assignee", "mark_assignees_notified", "get_primary_assignee",
    "get_assignee_names",
]
