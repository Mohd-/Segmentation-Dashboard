"""User lookups (login identities + roles; seeded from config.SEED_USERS)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import db
from helpers import utc_now_str

# The automation identity. Automated completions still WALK the state machine
# (assign -> submit -> approve), and assign_task only accepts an ACTIVE user --
# so the machine needs a name of its own rather than borrowing a human's.
SYSTEM_USER = "System"


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


def ensure_system_user(session) -> Optional[Dict[str, Any]]:
    """Idempotently seed :data:`SYSTEM_USER` as an active supervisor; return it.

    The one direct-SQL ``users`` write in the package, mirroring
    ``import_excel._ensure_import_user`` and migrations.py's own INSERT OR
    IGNORE idiom (keyed on the UNIQUE name). A *supervisor* so no role gate can
    ever block an automated walk, and seeded LAZILY -- only the first time an
    automation actually fires -- so a database where no rule ever triggers
    never grows the row (and the assignee/login dropdowns stay human-only).

    Returns None when the row exists but is deactivated: INSERT OR IGNORE
    cannot resurrect it, so ``UPDATE users SET is_active = 0 WHERE name =
    'System'`` is the deliberate OFF SWITCH for every automated completion --
    callers must treat None as "stand down", never as an error.

    Opens its own write transaction, so callers must not be inside one.
    """
    with db.write_transaction(session):
        db.execute(session, """
            INSERT OR IGNORE INTO users (name, role, created_at)
            VALUES (:name, :role, :now)
        """, {"name": SYSTEM_USER, "role": "supervisor", "now": utc_now_str()})
    return find_active_user(session, SYSTEM_USER)
