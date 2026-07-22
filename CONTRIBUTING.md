# Contributing — the cookbook

Recipes for the changes you will actually be asked to make. Read
`ARCHITECTURE.md` first if you don't yet know which module owns what.

## Rules of the road

1. **All SQL goes through the `db` helpers** (`db.fetch_one`, `db.fetch_all`,
   `db.execute`, `db.execute_many`) as plain SQL strings with **named binds**:
   `WHERE project_id = :project_id`, params as a dict. Never interpolate a
   *value* into SQL (interpolating a column name is allowed only from a fixed
   allowlist — see `update_project_name` in `workflow/projects.py` for the
   pattern). For dynamic IN-lists, pass a Python list and write `IN :stages` —
   the helpers expand it (see `_annotate_derived_state`).
2. **Writes run inside `with db.write_transaction(session):`** — it takes the
   write lock up front, commits on success, rolls back on error. Don't execute
   uncommitted DML on a session before entering it (SQLite will loudly refuse).
   Reads need no transaction block.
3. **Python 3.9 syntax only.** Production runs 3.9; the dev venv is newer and
   will NOT catch violations. Banned: `match`/`case`, runtime `X | Y` type
   unions (use `Optional[...]`, `Dict[...]` from `typing`), parenthesized
   multi-line `with` groups, `tomllib`, `except*`, `typing.Self`. Every module
   starts with `from __future__ import annotations`.
4. **Schema changes ship as numbered migrations** — databases now hold real
   lead/well data that must be carried forward. A schema change is an edit to
   `models.py` PLUS a guarded, append-only `(version, fn)` step in
   `migrations.py` with `LATEST_SCHEMA_VERSION` bumped (recipe 5). Never edit
   a shipped step — append a new numbered one.
5. **Every behavior change lands with a test** in the same change. The suite is
   the contract: `.venv/bin/pytest tests/ -q`.
6. **Domain function conventions:** `session` is always the first argument,
   `changed_by` the last; validation failures `raise ValueError("user-facing
   message.")` — never return an error dict, never catch-and-400 in a route.

---

## Recipe 1: Add a new workflow component (task)

`PIPELINE_TEMPLATES` in `workflow/constants.py` is the single source of truth
for the workflow — there is no templates table.

1. Add a `(sequence_no, task_name, stage_group)` tuple at the list position
   matching its place in the workflow, and renumber the tuples after it so
   `sequence_no` stays contiguous:

   ```python
   (25, "Fracture Modelling", "Post-Drilling"),
   ```

2. Pre-deployment that is the whole backend change: delete your dev `.db`
   (plus `-shm`/`-wal`) and restart — newly created projects materialize one
   `project_tasks` row per tuple (`_insert_project_with_tasks`).
   POST-deployment, changing this list will additionally require a numbered
   data migration for existing `project_tasks` rows (resequencing by
   task_name, deactivating retired steps with `is_active = 0`) — see the note
   above the list.
3. Give the component a form: add its field array to `SCHEMA` in
   `static/js/schema.js` (see `static/README.md` §4).
4. If users need a share folder for the component's supporting files, add its
   exact name to `COMPONENT_FILE_SECTIONS` in `config.py`.
5. **Renaming** a component pre-deployment is the same edit — change the name
   in the tuple (and in `schema.js`, `_OVERVIEW_READ_SOURCES` and
   `COMPONENT_FILE_SECTIONS` if it appears there), then regenerate the dev DB.
6. Tests: the task-count pins must move with you — the `31` counts in
   `tests/test_workflow_lifecycle.py`
   (`test_new_prospect_project_has_31_tasks_all_not_assigned`,
   `test_new_bp_project_seeds_all_31_tasks_not_assigned`) and
   `tests/test_bootstrap.py` (`test_new_project_gets_31_active_tasks`), plus
   the completion-percent arithmetic tests if the new step lands in a
   Prospect stage.

## Recipe 2: Add a dynamic input field to a component

For a plain input field, **no backend change is needed at all**:

1. Pick a snake_case key namespaced by component, e.g. `fracture_density_1km`
   (existing examples: `seal_dip`, `trap_cos_pct`, `lead_piip_gas_mean`).
2. Have the front-end include it in the `fields` object it already sends to
   `PATCH /api/tasks/<id>` (or `/dynamic-fields`). `save_task` /
   `save_task_dynamic_fields` upsert **any** key into `task_dynamic_fields`
   and log the change to history automatically.

You DO need backend work when:

- **The value must appear in the overview / portfolio reporting** — add one
  entry to `_OVERVIEW_READ_SOURCES` in `workflow/constants.py` mapping the
  overview key to its ordered `(task_name, field_key)` sources, e.g.
  `"flowback_results": [("Flowback Results", "flowback_gas_rate_mmscfd")]`.
  There is no stored mirror: the overview is composed from these sources at
  read time (`get_project_overview`), so a missed entry shows a blank — never
  silently stale data.
- **The field is calculated, not typed** — follow the Seal CoS pattern: the
  formula lives in `cos.py` (`calculate_seal_cos`), and both save paths in
  `workflow/lifecycle.py` overwrite the stored key on save (search for
  `seal_cos_pct` in `save_task` and `save_task_dynamic_fields`). The Total
  Chance of Success needs no save-time trigger: it is recomputed from the
  Reservoir/Trap/Seal CoS inputs on every read (`total_cos_from_fields` in
  `workflow/summary.py`).

## Recipe 3: Change a CoS formula

1. Edit the pure function in `cos.py` (`calculate_seal_cos`,
   `calculate_reservoir_cos_rows`, `calculate_presence_cos`, or
   `segment_class`). Keep the module free of DB/Flask imports.
2. Update the pinned expectations in `tests/test_cos.py` — they encode the
   formula, so they MUST change with it (deliberately).
3. Know which kind of value you changed. The Total Chance of Success
   (`calculate_presence_cos`, wrapped by `total_cos_from_fields`) is computed
   at read time, so every project shows the new formula immediately. Seal CoS
   and Reservoir CoS results are STORED at save time (`seal_cos_pct`,
   `reservoir_cos_rows`): existing rows keep the old number until re-saved.
   Pre-deployment, regenerating the dev `.db` is the reset; in production this
   would need a numbered migration recomputing stored values.
4. Document the change in README.md's "Version history" — users rely on those
   notes to know which formula produced which stored value.

## Recipe 4: Add an API endpoint

1. Write the domain function first, in the matching `workflow/` module
   (projects/lifecycle/promotion/formations/summary — each module's docstring
   says what belongs there; re-export the new name from
   `workflow/__init__.py`) or in `reporting.py` (read-only aggregates):
   `def my_thing(session, ..., changed_by="Web User")`. Raise
   `ValueError("...")` for anything the user did wrong; the message is shown
   verbatim.
2. Add a thin route in `main.py` — parse, call, jsonify, nothing else:

   ```python
   @app.get("/api/projects/<int:project_id>/my-thing")
   def my_thing(project_id):
       session = db.get_session()
       return json_response(workflow.my_thing(session, project_id))
   ```

   No try/except — the centralized handlers map ValueError→400,
   `StaleRevisionError`→409, FileNotFoundError→404, anything else→generic 500.
   For write endpoints take the actor name via `actor(payload)`, never from the
   payload directly.
3. Add a contract test in `tests/test_api_contract.py` style: status code,
   JSON shape, and the exact error `detail` for the failure path.

## Recipe 5: Change the schema

`models.py` stays the single source of truth for the CURRENT shape (fresh
databases are created straight from it), but existing databases hold real
lead/well data and upgrade **in place** at startup:

1. Edit the model in `models.py` (new column, new table, changed type or
   constraint).
2. Append a `(version, fn)` step to `MIGRATIONS` in `migrations.py` with the
   next integer version and bump `LATEST_SCHEMA_VERSION` to match. Steps are
   append-only (never edit a shipped one) and guarded-idempotent (check
   before altering, so a database already carrying the change passes through
   unchanged). Exception: a purely **additive table** needs no step —
   `create_all` creates missing tables on every bootstrap.
3. Update whatever reads/writes the column (`workflow/` SQL, `export_excel.py`
   column lists) and the tests that pin the schema
   (`tests/test_bootstrap.py` compares `sqlite_master` against
   `models.Base.metadata`, so a drifting model/DB pair fails loudly).
4. Land an upgrade-and-replay test with the step (see
   `test_migration_v2_upgrades_a_v1_database_in_place`): bootstrap a fresh
   DB, reshape it to the OLD form with raw sqlite3, re-bootstrap, assert the
   upgrade and that existing rows survived — then bootstrap once more and
   assert nothing changed.

## Recipe 6: Run and debug locally

```bash
.venv/bin/pytest tests/ -q                 # the whole suite
.venv/bin/pytest tests/ -q -k "promotion"  # just tests matching a keyword
python main.py                             # live server on 127.0.0.1:8020
```

- Unexpected 500s: the traceback is on the server's stderr —
  `handle_unexpected_error` in `main.py` logs via `app.logger.exception`
  before returning the generic response. The client never sees the detail.
- Fresh database: stop the app and delete `pipeline_tracker.db` (plus
  `-wal`/`-shm`), or point `SEGMENT_TRACKER_DB_PATH` at a scratch path.
  Bootstrap recreates and reseeds automatically from `models.py`.
- Tests never touch your local DB — each test gets its own file under pytest's
  tmp path (see `tests/conftest.py`).
