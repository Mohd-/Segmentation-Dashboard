"""Database lifecycle for the Segment Maturation and Execution System.

Each request thread receives its own SQLite connection. Bootstrap/migration work runs
once during application startup rather than on every request.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

from database import Database

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("SEGMENT_TRACKER_DB_PATH", str(BASE_DIR / "pipeline_tracker.db"))).expanduser().resolve()

_local = threading.local()
_bootstrap_lock = threading.Lock()
_bootstrapped = False


def init_db(path: Path = DB_PATH) -> Database:
    """Create/upgrade the database once, then return the calling thread's connection."""
    global _bootstrapped, DB_PATH
    DB_PATH = Path(path).expanduser().resolve()
    with _bootstrap_lock:
        if not _bootstrapped:
            bootstrap = Database(DB_PATH, bootstrap=True)
            bootstrap.close()
            _bootstrapped = True
    return get_db()


def get_db() -> Database:
    db = getattr(_local, "db", None)
    if db is None:
        # Schema is already initialized by init_db() at app startup.
        db = Database(DB_PATH, bootstrap=False)
        _local.db = db
    return db


def close_db(_exception=None) -> None:
    db = getattr(_local, "db", None)
    if db is not None:
        db.close()
        _local.db = None
