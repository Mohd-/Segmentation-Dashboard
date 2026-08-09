"""Shared pytest fixtures for the characterization test suite.

CRITICAL ORDERING REQUIREMENT
------------------------------
`dependencies.py` reads the env var SEGMENT_TRACKER_DB_PATH at import time, and
`database.py` reads SEGMENT_TRACKER_RF_MODEL_PATH at import time into the module
global RF_MODEL_PATH. `main.py` calls init_db(DB_PATH) at import time too. Because
of this, both env vars MUST be set before `main`, `database`, or `dependencies`
are ever imported by any test process. We do that here, at conftest module import
time (which pytest guarantees happens before test collection imports test modules
that import main/database). Every test module must import main/database lazily
(inside fixtures/tests), never at module level, to keep this ordering intact.

We never touch the real repository ./pipeline_tracker.db: every test gets a fresh
sqlite file under a pytest tmp path.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# main.py / database.py / dependencies.py live at the repo root. pytest.ini sets
# `pythonpath = .` for the normal case, but we defensively add it here too so
# this conftest works even if invoked in a way that ini option is skipped.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# A single process-wide scratch directory for the RF model stub and a fallback DB
# path. Individual tests get their own DB file via the `client` fixture, but the
# env vars must point *somewhere* valid before the very first import of
# database.py / dependencies.py / main.py happens (which occurs during the first
# fixture use below, not at conftest import time -- we only set env vars here).
_BOOTSTRAP_DIR = tempfile.mkdtemp(prefix="segtracker-conftest-")
os.environ.setdefault("SEGMENT_TRACKER_DB_PATH", os.path.join(_BOOTSTRAP_DIR, "bootstrap.db"))
os.environ.setdefault("SEGMENT_TRACKER_RF_MODEL_PATH", os.path.join(_BOOTSTRAP_DIR, "RF_model.joblib"))

import json
import sqlite3

import joblib
import pytest
from sklearn.ensemble import RandomForestClassifier


@pytest.fixture(scope="session", autouse=True)
def _rf_model_stub():
    """Train and persist a tiny RandomForestClassifier at SEGMENT_TRACKER_RF_MODEL_PATH.

    `database.calculate_reservoir_cos_rows` loads this via a joblib-cached loader
    (`database._load_reservoir_cos_model`, memoized with lru_cache). We write the
    stub file before the first import of `database` happens (this fixture is
    session-scoped and autouse, and conftest collection triggers before any test
    module imports main/database), so the very first cache-fill sees our stub.
    We also clear the cache defensively in case some other import order occurs.
    """
    model_path = os.environ["SEGMENT_TRACKER_RF_MODEL_PATH"]
    os.makedirs(os.path.dirname(model_path), exist_ok=True)

    # 3 features: [pull_up (0/1/2), amplitude_ratio, base_tight_sarah].
    # Includes both class labels (0 and 1) as required.
    X = [
        [0, 0.1, 0.1],
        [0, 0.9, 0.1],
        [1, 0.3, 0.7],
        [1, 0.5, 0.5],
        [2, 0.9, 0.9],
        [2, 0.1, 0.9],
    ]
    y = [0, 0, 0, 1, 1, 1]
    model = RandomForestClassifier(random_state=0, n_estimators=10)
    model.fit(X, y)
    joblib.dump(model, model_path)

    try:
        import cos
        cos._load_reservoir_cos_model.cache_clear()
    except Exception:
        pass

    yield model_path


@pytest.fixture()
def app_modules(_rf_model_stub):
    """Import main/db lazily, after env vars are guaranteed set."""
    import main
    import db
    return main, db


@pytest.fixture()
def client(tmp_path, app_modules):
    """Fresh, isolated sqlite DB per test.

    db.init_db(path) only bootstraps the schema once per process (guarded by the
    module-global `_bootstrapped` flag). To get real per-test isolation we call
    db.reset_for_tests() (disposes the engine and clears the bootstrap flag) and
    re-run init_db against a brand new tmp-path sqlite file. This never touches
    the real repository pipeline_tracker.db.
    """
    main, db = app_modules

    db.reset_for_tests()
    db_path = tmp_path / "test_pipeline_tracker.db"
    db.init_db(str(db_path))

    # database.calculate_reservoir_cos_rows caches the model at module scope; the
    # cache is process-wide and independent of which DB is active, so no reset
    # is needed here between tests.

    with main.app.test_client() as test_client:
        # Exposed for tests (e.g. test_known_bugs.py) that need a raw sqlite3
        # connection to the exact file backing this test's app instance.
        test_client.db_path = db_path
        yield test_client

    db.reset_for_tests()


def create_project(client, name, **payload):
    """POST /api/projects and return the new project_id.

    Raises AssertionError with the response body on non-201 so failures are
    diagnosable from calling tests.
    """
    body = {"project_name": name}
    body.update(payload)
    resp = client.post("/api/projects", json=body)
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["project_id"]


def get_tasks(client, project_id):
    """GET /api/projects/<id>/tasks and return the list of task dicts."""
    resp = client.get(f"/api/projects/{project_id}/tasks")
    assert resp.status_code == 200
    return resp.get_json()


def get_task_by_name(client, project_id, task_name):
    """Return the single task dict matching task_name for a project, or None."""
    for task in get_tasks(client, project_id):
        if task["task_name"] == task_name:
            return task
    return None


def reach_task(client, project_id, task_name):
    """Put a test's target at the front of its pipeline through the API.

    Lifecycle-focused tests often exercise one later component in isolation.
    Role assignment deliberately leaves such a component ``Not Assigned``
    until every preceding component has been approved, so those tests must
    establish that workflow position before assigning or field-completing the
    target.  This uses the internal domain service to walk each preceding task
    through the real lifecycle (assign -> submit -> approve), never a direct
    status PATCH.
    """
    import workflow
    import db as dbmod
    tasks = get_tasks(client, project_id)
    target = next((task for task in tasks if task["task_name"] == task_name), None)
    # Authenticated projects now carry a creator preassignment. Use that same
    # person for the setup walk so the target's automatic activation audit is
    # attributed consistently; anonymous legacy tests keep Employee.
    walk_actor = (target or {}).get("assigned_to") or "Employee"
    for task in tasks:
        if task["task_name"] == task_name:
            return get_task_by_name(client, project_id, task_name)
        if task["status"] == "Approved":
            continue
        session = dbmod.new_session()
        try:
            workflow.lifecycle.ensure_task_approved(
                session, task["task_id"], walk_actor, automated=True)
        finally:
            session.close()
    raise AssertionError(f"Unknown task {task_name!r}")


def approve_task(client, task_id, assignee="Employee"):
    """Approve a single task programmatically via the domain service.

    Walks the real lifecycle (assign -> submit -> satisfy any submit gate ->
    approve).  Use this for test setup that needs a task in the Approved state
    without exercising the HTTP transition endpoint.
    """
    import workflow
    import db as dbmod
    session = dbmod.new_session()
    try:
        workflow.lifecycle.ensure_task_approved(
            session, task_id, assignee, automated=True)
    finally:
        session.close()


def raw_sqlite_connect(client_db_path):
    """Open a direct sqlite3 connection to the same DB file the app is using.

    Used only by tests/test_known_bugs.py to simulate legacy data shapes that
    the application layer cannot itself produce (e.g. duplicate task rows left
    over from an old migration).
    """
    conn = sqlite3.connect(str(client_db_path))
    conn.row_factory = sqlite3.Row
    return conn
