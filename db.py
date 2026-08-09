"""Engine, session lifecycle and one-time bootstrap for the application.

What belongs here:
- Creating the SQLAlchemy engine (SQLite tuning: connection timeout, PRAGMA
  foreign_keys/busy_timeout on connect, WAL + synchronous=NORMAL at bootstrap).
- The session factory and Flask request integration (a session bound to
  ``flask.g`` with teardown).
- ``bootstrap()`` -- create tables + seed base data (migrations.run), guarded so
  it runs once per process but is re-armable for tests via ``reset_for_tests()``.
- The SQLAlchemy version guard.
- The shared SQL execution helpers (``fetch_one``/``fetch_all``/``execute``/
  ``execute_many``) and the write-transaction primitives used by the domain,
  reporting and migration layers.

What does NOT belong here:
- Business logic or report SQL (workflow.py / reporting.py) and schema shape
  (models.py / migrations.py).

Query idiom (ONE way, used everywhere -- domain, reporting, migrations):
textual SQL via ``sqlalchemy.text()`` with NAMED bind parameters::

    db.fetch_one(session, "SELECT * FROM projects WHERE project_id = :project_id",
                 {"project_id": project_id})

Named binds are dialect-portable (SQLAlchemy translates them to each driver's
paramstyle), so moving to Postgres really is just a DATABASE_URL change. For
dynamic IN-lists, pass a list/tuple/set value and write ``IN :name`` -- the
helpers attach an expanding ``bindparam`` automatically (see ``_prepare``).
Rows come back as plain dicts so call sites use ``row["col"]`` / ``.get()``.

Writing convention (keep it uniform -- one obvious way to run a write):
    with db.write_transaction(session):
        db.execute(session, "UPDATE ...", {...})
The block takes the database write lock upfront (SQLite ``BEGIN IMMEDIATE``),
commits on success and rolls back on error. Reads use ``fetch_one`` /
``fetch_all`` directly and never need an explicit transaction block.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import sqlalchemy
from sqlalchemy import bindparam, create_engine, event, text
from sqlalchemy.orm import sessionmaker

import config

# ---------------------------------------------------------------------------
# SQLAlchemy version guard
# ---------------------------------------------------------------------------

def _check_sqlalchemy_version() -> None:
    """Fail loudly if SQLAlchemy is older than 1.4 (this code needs 1.4/2.0)."""
    parts = sqlalchemy.__version__.split(".")
    try:
        version_tuple = (int(parts[0]), int(parts[1]))
    except (IndexError, ValueError):
        return  # Unparseable version string; assume a modern build.
    if version_tuple < (1, 4):
        raise RuntimeError(
            "SQLAlchemy >= 1.4 is required (found {}). Upgrade with "
            "'pip install \"sqlalchemy>=1.4\"'.".format(sqlalchemy.__version__)
        )


# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------

_engine = None
_SessionFactory = None
_bootstrapped = False
_bootstrap_lock = threading.Lock()
_current_display = None  # Human-readable target for the health endpoint.


def _as_url(db_path_or_url: Optional[str]) -> str:
    """Turn a filesystem path or a full URL into a SQLAlchemy URL string."""
    if db_path_or_url is None:
        return config.database_url()
    text_value = str(db_path_or_url)
    if "://" in text_value:
        return text_value
    return "sqlite:///" + str(Path(text_value).expanduser().resolve())


def _sqlite_on_connect(dbapi_connection, _connection_record) -> None:
    """Enforce foreign keys and a busy timeout on every new SQLite connection."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.execute("PRAGMA busy_timeout = 30000")
    cursor.close()


def init_engine(db_path_or_url: Optional[str] = None):
    """Create the engine + session factory for a path or URL.

    Accepts either a filesystem path (turned into a ``sqlite:///`` URL) or a full
    URL (so a Postgres ``DATABASE_URL`` works unchanged later).
    """
    global _engine, _SessionFactory, _current_display
    _check_sqlalchemy_version()
    url = _as_url(db_path_or_url)
    if url.startswith("sqlite"):
        engine = create_engine(url, future=True, connect_args={"timeout": 30})
        event.listen(engine, "connect", _sqlite_on_connect)
        _current_display = url[len("sqlite:///"):] if url.startswith("sqlite:///") else url
    else:
        engine = create_engine(url, future=True)
        _current_display = url
    _engine = engine
    _SessionFactory = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    return engine


def get_engine():
    """Return the process engine, creating it from config on first use."""
    if _engine is None:
        init_engine(None)
    return _engine


def current_display() -> str:
    """The database location string reported by /api/health."""
    if _current_display is None:
        return config.database_url()
    return _current_display


@contextmanager
def _sqlite_bootstrap_lock(engine):
    """Serialize the complete SQLite bootstrap across OS processes.

    ``BEGIN IMMEDIATE`` in ``migrations.run`` cannot protect the setup that
    precedes it: configuring WAL and ``Base.metadata.create_all`` both touch
    the database before that transaction starts.  A sidecar lock therefore
    guards the entire sequence, including WAL configuration, schema creation,
    migrations, and seed data.  ``flock`` releases the lock if a worker exits
    unexpectedly, so a stale lock file does not strand future startups.

    In-memory SQLite databases have no shared file to coordinate and are left
    alone.  Non-SQLite engines also pass through unchanged.
    """
    if engine.dialect.name != "sqlite":
        yield
        return

    database = engine.url.database
    query = engine.url.query
    if not database or database == ":memory:" or query.get("mode") == "memory":
        yield
        return

    # Import only on the SQLite path.  Production deployments run on Unix,
    # where flock provides the required inter-process advisory lock.
    import fcntl

    database_path = Path(database).expanduser().resolve()
    lock_path = database_path.with_name(database_path.name + ".bootstrap.lock")
    with lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def bootstrap(db_path_or_url: Optional[str] = None) -> None:
    """Create tables, run migrations and seed templates -- once per process.

    Re-armable for tests via :func:`reset_for_tests`. SQLite WAL journalling and
    ``synchronous=NORMAL`` are applied here because they are database-file-level
    settings, not per-connection.
    """
    global _bootstrapped
    with _bootstrap_lock:
        if _bootstrapped:
            return
        engine = _engine if _engine is not None else init_engine(db_path_or_url)
        with _sqlite_bootstrap_lock(engine):
            if engine.dialect.name == "sqlite":
                with engine.connect() as connection:
                    connection.exec_driver_sql("PRAGMA journal_mode = WAL")
                    connection.exec_driver_sql("PRAGMA synchronous = NORMAL")
            # Imported lazily to avoid an import cycle (migrations -> db).
            import migrations
            session = _SessionFactory()
            try:
                migrations.run(session, engine)
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()
        _bootstrapped = True


def init_db(db_path_or_url: Optional[str] = None):
    """Convenience used by app startup and tests: init engine then bootstrap."""
    init_engine(db_path_or_url)
    bootstrap(db_path_or_url)
    return _engine


def reset_for_tests() -> None:
    """Dispose the engine and clear the bootstrap guard so a new DB can be used."""
    global _engine, _SessionFactory, _bootstrapped, _current_display
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionFactory = None
    _bootstrapped = False
    _current_display = None


# ---------------------------------------------------------------------------
# Session factory + Flask integration
# ---------------------------------------------------------------------------

def new_session():
    """Create a standalone Session (callers own its lifecycle)."""
    if _SessionFactory is None:
        get_engine()
    return _SessionFactory()


def get_session():
    """Return the per-request Session bound to ``flask.g`` (created on first use)."""
    from flask import g
    session = getattr(g, "_db_session", None)
    if session is None:
        session = new_session()
        g._db_session = session
    return session


def remove_session(exception=None) -> None:
    """Flask teardown hook: roll back on error, then close the request session."""
    from flask import g
    session = getattr(g, "_db_session", None)
    if session is not None:
        try:
            if exception is not None:
                session.rollback()
        finally:
            session.close()
        g._db_session = None


# ---------------------------------------------------------------------------
# Write transactions (upfront SQLite write lock)
# ---------------------------------------------------------------------------

def begin_write(session, attempts: int = 5) -> None:
    """Take the database write lock upfront for the session's next transaction.

    SQLite: issues ``BEGIN IMMEDIATE`` on the raw DBAPI connection with a
    bounded retry loop (5 attempts, exponential backoff starting at 0.08s,
    retrying only on "locked" OperationalErrors). This is required for
    correctness under concurrent writers (Gunicorn / threaded Flask): with a
    deferred transaction under WAL, a read-then-write transaction that races
    another commit fails immediately with SQLITE_BUSY_SNAPSHOT ("database is
    locked"), and busy_timeout does NOT retry that case because the stale
    snapshot can never succeed by waiting. Taking the write lock before the
    first read serializes writers safely.

    Convention (enforced by SQLite itself): callers must NOT have executed
    uncommitted DML on this session before entering -- pysqlite only auto-begins
    a SQLite transaction on DML (never on SELECT), so a session that has only
    read is safe. A violation raises "cannot start a transaction within a
    transaction", surfacing the misuse loudly instead of corrupting state.

    Non-SQLite dialects: no-op. A Postgres deployment would take an advisory
    lock here (e.g. pg_advisory_xact_lock) if cross-process serialization of a
    write path is ever needed; MVCC makes the upfront lock unnecessary for
    plain correctness there.
    """
    connection = session.connection()
    if connection.dialect.name != "sqlite":
        return
    raw = connection.connection  # pool proxy; forwards execute() to sqlite3.Connection
    for attempt in range(attempts):
        try:
            raw.execute("BEGIN IMMEDIATE")
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt == attempts - 1:
                raise
            time.sleep(0.08 * (2 ** attempt))


@contextmanager
def write_transaction(session):
    """Run a write inside a transaction: commit on success, roll back on error.

    Acquires the database write lock upfront via :func:`begin_write` (SQLite
    ``BEGIN IMMEDIATE``; no-op on other dialects). See ``begin_write`` for the
    concurrency rationale and the no-prior-uncommitted-DML convention.
    """
    begin_write(session)
    try:
        yield
    except Exception:
        session.rollback()
        raise
    else:
        try:
            session.commit()
        except Exception:
            # A commit that fails partway (e.g. SQLite "database is locked" at
            # COMMIT time) strands the session transaction in the 'prepared'
            # state, where EVERY later statement raises InvalidRequestError.
            # Roll back so the session stays usable and re-raise the real error.
            session.rollback()
            raise


# ---------------------------------------------------------------------------
# SQL helpers (text() + named binds; rows returned as plain dicts)
# ---------------------------------------------------------------------------

def _prepare(sql: str, params: Optional[Dict[str, Any]]):
    """Wrap SQL in ``text()`` and attach expanding binds for list-valued params.

    Any parameter whose value is a list/tuple/set is treated as an ``IN :name``
    expanding bind, so call sites never build placeholder strings by hand.
    """
    stmt = text(sql)
    if params:
        expanding = [bindparam(key, expanding=True)
                     for key, value in params.items()
                     if isinstance(value, (list, tuple, set))]
        if expanding:
            stmt = stmt.bindparams(*expanding)
    return stmt


def _normalize_params(params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Coerce set/tuple values to lists (expanding binds want sequences)."""
    if not params:
        return {}
    return {key: (list(value) if isinstance(value, (set, tuple)) else value)
            for key, value in params.items()}


def execute(session, sql: str, params: Optional[Dict[str, Any]] = None):
    """Execute one statement with named (``:name``) binds; return CursorResult.

    Use ``.rowcount`` on the result as needed. ``.lastrowid`` works on SQLite;
    a Postgres port should switch those call sites to ``RETURNING``.
    """
    return session.execute(_prepare(sql, params), _normalize_params(params))


def execute_many(session, sql: str, seq_of_params: Iterable[Dict[str, Any]]):
    """Execute one statement against many parameter dicts (executemany)."""
    return session.execute(text(sql), list(seq_of_params))


def fetch_one(session, sql: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Return the first row as a dict, or ``None``."""
    row = execute(session, sql, params).fetchone()
    return dict(row._mapping) if row is not None else None


def fetch_all(session, sql: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Return all rows as a list of dicts."""
    result = execute(session, sql, params)
    return [dict(r._mapping) for r in result.fetchall()]
