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

- **models.py** — SQLAlchemy `declarative_base` table definitions: THE single
  authoritative schema (there is no separate production schema to mirror).
  Applied via `create_all` on every bootstrap. Pre-deployment, a schema change
  is an edit here plus deleting the dev `.db` file — no migration. No queries,
  no business logic.

- **db.py** — engine creation (SQLite pragmas, WAL), the session factory,
  Flask per-request session (`get_session` bound to `flask.g`), the one-time
  `bootstrap()`, and the shared SQL helpers `execute` / `execute_many` /
  `fetch_one` / `fetch_all` plus `write_transaction` / `begin_write`. No
  business logic or report SQL.

- **migrations.py** — schema bootstrap plus in-place upgrades: `create_all`
  (fresh DBs get the full current shape; existing DBs get newly-added tables),
  the append-only `MIGRATIONS = [(version, fn), ...]` steps dispatched against
  the stored `schema_version`, base-data seeding (users + the commitment row),
  and the current-version stamp. Databases stamped newer than the code are
  refused (clear `RuntimeError`).

- **workflow/** — the domain, a package (`import workflow` exposes the full
  public API via `__init__.py` re-exports). Every function takes a `session`
  first; no Flask imports. Modules, bottom of the dependency graph first:
  - `constants.py` — statuses, stages, `applicable_stages()`,
    `PIPELINE_TEMPLATES` (the single source of truth for the 31-step
    workflow), formation vocabulary, `StaleRevisionError`.
  - `history.py` — the append-only `task_history` writer (`log_task_event`).
  - `users.py` — login identity lookups.
  - `notifications.py` — who a transition tells (the fan-out policy) and the
    per-recipient bell feed. Its writer runs inside `transition_task`'s own
    transaction; every read is scoped by the caller's own name.
  - `projects.py` — project CRUD + the derived board state
    (`_annotate_derived_state`).
  - `lifecycle.py` — task reads/saves, assignment, submit/approve/return.
  - `promotion.py` — lead-summary snapshots, BP promotion/demotion, flags.
  - `formations.py` — well-level formation data.
  - `summary.py` — the computed overview + Total Chance of Success reads.

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
  `actor()` stamping) and the role gates (`current_role()` / `require_role()`)
  live here because they are HTTP-session state.

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
`ValueError` → 400, `PermissionError` (role gates) → 403,
`workflow.StaleRevisionError` → 409, `FileNotFoundError` → 404, everything
else → generic 500 + server-side log.

## The data model, in domain terms

- **projects** — one row per lead/well: identity, dates, flags
  (`business_plan_enabled` controls Portfolio reporting inclusion,
  `active_well_enabled`, `archived`), coordinates (`lead_x`/`lead_y`, REAL)
  and `pipeline_type` — `'prospect'` (maturing lead, works Prospect stages) or
  `'bp'` (promoted well, works Business Plan Execution stages). `revision`
  powers optimistic locking. The board pointers (current stage/task/owner,
  overall status) are NOT stored — see "Derive, don't store" below; the one
  stored completion fact is `completed_at`, a historical timestamp kept in
  sync by `_sync_completed_at` (`workflow/projects.py`) from every write that
  can change completeness (save, transition, promotion/demotion).
- **project_tasks** — the per-project instances of the 31-step workflow
  ("components" in the UI), materialized straight from
  `workflow.PIPELINE_TEMPLATES` at creation (there is no templates table).
  `UNIQUE(project_id, task_name)`; retired components would stay as
  `is_active = 0` rows so their inputs and history survive.
- **task_dynamic_fields** — key/value inputs attached to a task (the component
  form data). This EAV table is why most new inputs need no schema change.
- **task_history** — append-only audit trail of every change (`changed_by`,
  action type, old/new status, comment).
- **lead_summary_snapshots** — a frozen JSON copy of all Prospect-stage inputs,
  captured at the moment a lead is promoted to BP Execution (refreshed on
  re-promotion, kept on demotion). Deliberately stored: it is a historical
  record of what the lead looked like at promotion, not a cache.
- **users** — login identities and roles (`supervisor`/`staff`/`employee`),
  seeded idempotently from `config.SEED_USERS`; login only accepts active rows.
- **project_formations** — well-level formation interpretation values
  (formation × phase), edited via the mini-sheet on the logs components.
  Measurement columns are REAL; the API coerces input to float and rejects
  junk with a 400 naming the field.
- **business_plan_commitment / app_settings** — single-row commitment totals;
  key/value settings including `schema_version`.

Task statuses are exactly four (`Not Assigned` / `In Progress` / `Ready` /
`Approved`). There is no stored "Not Applicable": applicability is a pure
function of the pipeline — `applicable_stages(pipeline_type)` scopes every
completion/board/cascade query to the operating pipeline's stages.

## Derive, don't store

The design theme of the schema: anything that is a pure function of other
stored data is computed at read time, never persisted, so it can never go
stale or need repair machinery.

- **The workflow definition** lives in code (`PIPELINE_TEMPLATES` in
  `workflow/constants.py`), not in a table.
- **The board pointers** (current stage/task/owner, overall status,
  stage-started-at) are derived from the active task rows by
  `workflow.projects._annotate_derived_state` — one batched query for the
  whole board.
- **The project overview** shown in `/detail` is composed from
  `task_dynamic_fields` at read time via `_OVERVIEW_READ_SOURCES`
  (`workflow/constants.py`); the Total Chance of Success (`derisking`) is
  recomputed from the Reservoir/Trap/Seal CoS inputs on every read
  (`total_cos_from_fields`). There is no `project_overview` table.
- **Applicability** is `applicable_stages(pipeline_type)`, so BP
  promotion/demotion is a pure pipeline switch: it rewrites no task rows.

The two legitimate stored copies are **historical facts**, not caches:
`lead_summary_snapshots` (what the lead's inputs were at the moment of
promotion) and `projects.completed_at` (when the applicable set first became
fully approved — stamped/cleared by `_sync_completed_at` in the write paths).

## Schema bootstrap & migrations

`models.py` IS the current schema: a fresh database is created straight from
it by `create_all` and stamped `LATEST_SCHEMA_VERSION`. Existing databases
hold real lead/well data and upgrade **in place**: at startup `migrations.run`
refuses a database stamped with a NEWER `schema_version` than the code knows
(clear `RuntimeError` -- update the code, never downgrade the database), lets
`create_all` add any newly-modeled tables, applies every `MIGRATIONS` step
newer than the stored version in ascending order, seeds base data (users from
`config.SEED_USERS`, the commitment row) and stamps the current version.
Steps are append-only (never edit a shipped one) and guarded-idempotent, and
each lands with an upgrade-and-replay test (CONTRIBUTING.md recipe 5).

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
  model edit. The few values reporting needs are composed from these rows at
  read time via `_OVERVIEW_READ_SOURCES` (see "Derive, don't store").
