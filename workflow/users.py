"""User lookups (login identities + roles; seeded from config.SEED_USERS)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import db


def get_active_users(session) -> List[Dict[str, Any]]:
    """Active users as [{name, role}] ordered by name (assignee/login dropdowns)."""
    return db.fetch_all(session,
                        "SELECT name, role FROM users WHERE is_active = 1 ORDER BY name")


def find_active_user(session, name: str) -> Optional[Dict[str, Any]]:
    """Look up an active user by name, case-insensitively.

    Returns the full row (canonical-cased ``name`` plus ``role``) or None.
    Used by login so users can type their name in any casing but the session
    always stores the canonical spelling from the users table.
    """
    return db.fetch_one(session, """
        SELECT * FROM users
        WHERE LOWER(name) = LOWER(:name) AND is_active = 1
    """, {"name": str(name or "").strip()})
