# Architecture

How the backend is organized and why. For hands-on recipes see
`CONTRIBUTING.md`; each module's own docstring is the authoritative statement
of what belongs in it.

## Module map

- **config.py** — the ONLY place configuration lives: app identity
  (APP_NAME/APP_VERSION), database location, RF-model path, auth settings, and
  the Windows share roots/maps. Env-derived values that can change at runtime
  are functions (`db_path()`, `database_url()`, `rf_model_path()`). Never put
  SQL, Flask objects or business logic here.

- **models.py** — SQLAlchemy `declarative_base` table definitions mirroring the
  production schema exactly; doubles as schema documentation. Used only by
  `create_all` for brand-new databases. No queries, no business logic.

- **db.py** — engine creation (SQLite pragmas, WAL), the session factory,
  Flask per-request session (`get_session` bound to `flask.g`), the one-time
  `bootstrap()`, and the shared SQL helpers `execute` / `execute_many` /
  `fetch_one` / `fetch_all` plus `write_transaction` / `begin_write`. No
  business logic or report SQL.

- **migrations.py** — the numbered migration framework: fresh-DB creation,
  legacy column ensures, and the `MIGRATIONS` step list. Runtime domain logic
  stays out (though a step may call domain helpers to reproduce behavior).

- **workflow.py** — the domain: workflow constants (statuses, stages,
  `PIPELINE_TEMPLATES`), and every project/task lifecycle operation (create,
  save, promote/demote, snapshots, presence-CoS recalculation, history).
  Every function takes a `session` first; no Flask imports.

- **cos.py** — pure Chance-of-Success math (Reservoir/Seal/Presence CoS,
  `segment_class`) and the cached RF-model loader. No SQLAlchemy or Flask
  imports — values in, values out.

- **reporting.py** — read-only dashboards and aggregates (metrics, monthly
  trend, portfolio, activity log). No writes.

- **folders.py** — Windows/UNC share-path building and folder-link resolution
  from the roots/maps in config. No lifecycle logic.

- **export_excel.py** — the styled Excel workbook export; layout only, metrics
  come from reporting/workflow.

- **helpers.py** — small pure date/number utilities shared by the above.
  Nothing that touches the database or Flask.

- **main.py** — Flask routes and centralized error handlers; parses the
  request, calls one domain function, returns JSON. Identity (login/logout/me,
  `actor()` stamping) lives here because it is HTTP-session state.

## The request path

```
HTTP request
  -> main.py route            (parse args/payload; no try/except)
  -> db.get_session()         (one Session per request, bound to flask.g)
  -> workflow/reporting/...   (domain function; session is the first argument)
  -> json_response(...)       (route serializes the returned dicts)
```

Reads call `db.fetch_one`/`db.fetch_all` directly — no explicit transaction.
Writes always run inside `with db.write_transaction(session):`, which takes the
database write lock up front (`db.begin_write`, SQLite `BEGIN IMMEDIATE`),
commits on success and rolls back on error. The upfront lock exists because
under WAL a read-then-write transaction that races another writer fails
immediately with SQLITE_BUSY_SNAPSHOT, which `busy_timeout` cannot retry;
locking first serializes writers safely (full rationale in `db.py`'s
`begin_write` docstring). Errors propagate to the handlers in main.py:
`ValueError` → 400, `workflow.StaleRevisionError` → 409, `FileNotFoundError` →
404, everything else → generic 500 + server-side log.

## The data model, in domain terms

- **projects** — one row per lead/well. `pipeline_type` is `'prospect'`
  (maturing lead, works Prospect stages) or `'bp'` (promoted well, works
  Business Plan Execution stages). `business_plan_enabled` only controls
  Portfolio reporting inclusion. `revision` powers optimistic locking;
  `completed_at` is stamped when the project completes.
- **task_templates / project_tasks** — the canonical 32-component workflow and
  its per-project instances ("components" in the UI). Retired components stay
  as `is_active = 0` rows so their inputs and history survive.
- **task_dynamic_fields** — key/value inputs attached to a task (the component
  form data). This EAV table is why most new inputs need no schema change.
- **task_history** — append-only audit trail of every change (`changed_by`,
  action type, old/new status, comment).
- **project_overview** — one denormalized row per project mirroring selected
  dynamic fields (via `DYNAMIC_FIELD_OVERVIEW_MAP`) for fast reporting.
- **lead_summary_snapshots** — a frozen JSON copy of all Prospect-stage inputs,
  captured at the moment a lead is promoted to BP Execution (refreshed on
  re-promotion, kept on demotion).
- **business_plan_commitment / app_settings** — single-row commitment totals;
  key/value settings including `schema_version`.

## Migrations

`app_settings.schema_version` records the database's shape. At startup,
`migrations.run` creates missing tables (`create_all`), applies the legacy
column ensures, seeds templates on an empty DB, then applies every
`MIGRATIONS = [(version, fn), ...]` step whose version is greater than the
stored one — each step in its own write-locked transaction, bumping the version
as it commits. A brand-new database jumps straight to `LATEST_SCHEMA_VERSION`
without replaying history. Version 15 is the adoption baseline: any older
database runs `_consolidate_to_v15`, a faithful port of the legacy upgrade
(renames, template upserts, task backfills, status normalization).
`_upgrade_to_v16` is the model for new steps. Never edit a shipped step —
append a new one.

## Design decisions

- **Explicit `text()` SQL with named binds instead of ORM queries.** One
  uniform query idiom everywhere (readable by grep, reviewable as plain SQL),
  a faithful port of the battle-tested legacy queries, and no ORM
  session/identity-map subtleties for a junior developer to trip over.
  Named binds keep every query dialect-portable; `models.py` remains the
  single source of truth for the schema itself.
- **An EAV table (`task_dynamic_fields`) for component inputs.** Components
  gain and lose technical input fields frequently; storing them as key/value
  rows means adding a field is a front-end-only change — no migration, no
  model edit. The few fields reporting needs are mirrored to `project_overview`
  columns via `DYNAMIC_FIELD_OVERVIEW_MAP`.
