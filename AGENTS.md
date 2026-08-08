# AGENTS.md

## Verification (run before considering work done)

```bash
.venv/bin/pytest tests/ -q                     # backend: 532 tests
.venv/bin/python run_frontend_tests.py         # frontend: 416 tests (headless Firefox)
```

Both suites are the contract. Frontend tests live in `static/tests/test-*.js` and are registered in `static/tests/runner.html` (unregistered files never run). No linter exists.

## Commands

```bash
python main.py                                 # dev server on http://127.0.0.1:8020
.venv/bin/pytest tests/ -q -k "keyword"        # filtered backend run
.venv/bin/python run_frontend_tests.py --browser open   # watch frontend tests
```

Fresh DB: stop the app, delete `pipeline_tracker.db` (and `-wal`/`-shm`); bootstrap recreates it on next start. Tests never touch the local DB.

## Constraints that bite

**Python 3.9.** Production runs 3.9. Banned: `match`/`case`, runtime `X | Y` unions (use `Optional[...]`, `Dict[...]` from `typing`), parenthesized multi-line `with`, `tomllib`, `except*`, `typing.Self`. Every module starts with `from __future__ import annotations`. The dev venv is newer and won't catch violations.

**SQL goes through `db` helpers.** `db.fetch_one`, `db.fetch_all`, `db.execute`, `db.execute_many` with **named binds** (`:param_name`, dict params). Never interpolate values. For dynamic IN-lists, pass a Python list and write `IN :stages` (the helpers expand it). Writes run inside `with db.write_transaction(session):` (takes the write lock up front). Reads need no transaction.

**Two test suites, both required.** Backend `pytest tests/ -q` (532 tests). Frontend `.venv/bin/python run_frontend_tests.py` (416 tests, 18 modules in `static/tests/`). A frontend change lands with `static/tests/test-*.js` + registration in `runner.html`.

## Domain conventions

- `workflow/` is the domain package. All SQL there. Functions take `session` first, `changed_by` last.
- Validation failures raise `ValueError("user-facing message.")` — never return error dicts, never catch-and-400 in routes. Centralized handlers in `main.py` map `ValueError`→400, `StaleRevisionError`→409, `FileNotFoundError`→404, else 500.
- Routes in `main.py` are thin: parse, call domain function, jsonify. No try/except.
- Schema changes: edit `models.py` AND append a `(version, fn)` step to `MIGRATIONS` in `migrations.py` (append-only, never edit shipped steps). Bump `LATEST_SCHEMA_VERSION`.
- Workflow definition lives in `workflow/constants.py` `PIPELINE_TEMPLATES` (no templates table).

## Architecture quick-reference

- `config.py` — all configuration, env-driven
- `models.py` — SQLAlchemy schema (single source of truth, fresh DBs created from it)
- `migrations.py` — in-place upgrades via `MIGRATIONS` list
- `db.py` — engine, session factory, `write_transaction`
- `workflow/` — domain (projects, lifecycle, promotion, formations, summary, users, notifications)
- `cos.py` — pure CoS math (no DB/Flask imports)
- `reporting.py` — read-only dashboards
- `resource_engine/` — vendored Monte Carlo PIIP engine (treat as third-party)
- `main.py` — Flask routes, error handlers, identity
- `static/` — vanilla JS SPA, no framework, no build step, no npm
