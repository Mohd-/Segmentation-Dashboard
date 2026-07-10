# Contributing — the cookbook

Recipes for the changes you will actually be asked to make. Read
`ARCHITECTURE.md` first if you don't yet know which module owns what.

## Rules of the road

1. **All SQL goes through the `db` helpers** (`db.fetch_one`, `db.fetch_all`,
   `db.execute`, `db.execute_many`) as plain SQL strings with **named binds**:
   `WHERE project_id = :project_id`, params as a dict. Never interpolate a
   *value* into SQL (interpolating a column name is allowed only from a fixed
   allowlist — see `update_project_overview_fields` in workflow.py for the
   pattern). For dynamic IN-lists, pass a Python list and write `IN :stages` —
   the helpers expand it (see `reconcile_project_flow`).
2. **Writes run inside `with db.write_transaction(session):`** — it takes the
   write lock up front, commits on success, rolls back on error. Don't execute
   uncommitted DML on a session before entering it (SQLite will loudly refuse).
   Reads need no transaction block.
3. **Python 3.9 syntax only.** Production runs 3.9; the dev venv is newer and
   will NOT catch violations. Banned: `match`/`case`, runtime `X | Y` type
   unions (use `Optional[...]`, `Dict[...]` from `typing`), parenthesized
   multi-line `with` groups, `tomllib`, `except*`, `typing.Self`. Every module
   starts with `from __future__ import annotations`.
4. **Never edit a shipped migration step** — databases in the field already ran
   it. Append a new numbered step instead (recipe 5).
5. **Every behavior change lands with a test** in the same change. The suite is
   the contract: `.venv/bin/pytest tests/ -q`.
6. **Domain function conventions:** `session` is always the first argument,
   `changed_by` the last; validation failures `raise ValueError("user-facing
   message.")` — never return an error dict, never catch-and-400 in a route.

---

## Recipe 1: Add a new workflow component (task)

1. Add a tuple to `PIPELINE_TEMPLATES` in `workflow.py`, at the list position
   matching its place in the workflow (list order defines `sequence_no`):

   ```python
   (33, "Fracture Modelling", "Post-Drilling", "Geologist", 3, None, "normal", "Fracture model complete"),
   ```

   The first element is `template_id`: it must be **new and never reused** —
   `project_tasks.template_id` rows reference it forever, so renumbering or
   recycling ids corrupts existing databases. Appending at the end of a stage
   is safest; inserting mid-list shifts the `sequence_no` of everything after
   it, which then also has to be re-synced by your migration (step 3).
2. New databases and newly created projects pick the component up automatically
   (`add_project` iterates the templates table). **Existing databases do not**:
   `seed_templates` only runs on an empty table, and the v15 consolidation only
   runs for pre-v15 databases. So:
3. Write a migration step (recipe 5) that upserts the template row and
   backfills one `project_tasks` row per existing project. Copy the
   `template_map` upsert + "Backfill only genuinely missing active tasks" loop
   from `_consolidate_to_v15` in `migrations.py` — it already handles the
   initial status rule (`"Not Applicable"` for Prospect-stage tasks on `bp`
   projects, else `"Not Assigned"`).
4. If users need a share folder for the component's supporting files, add its
   exact name to `COMPONENT_FILE_SECTIONS` in `config.py`.
5. If you are **renaming** an existing component instead: add the old→new pair
   to `WORKFLOW_TASK_RENAMES` in `workflow.py` AND write a migration step that
   renames live rows (the rename loop at the top of `_consolidate_to_v15` is
   the template — it preserves task ids, inputs and history).
6. Tests: assert the new component appears for a fresh project (see
   `test_new_prospect_project_has_31_tasks_all_not_assigned` — the 31 counts
   in `tests/test_workflow_lifecycle.py` will need the explicit bump to 32),
   and a migration test per recipe 5.

## Recipe 2: Add a dynamic input field to a component

For a plain input field, **no backend change is needed at all**:

1. Pick a snake_case key namespaced by component, e.g. `fracture_density_1km`
   (existing examples: `seal_dip`, `trap_cos_pct`, `lead_piip_gas_mean`).
2. Have the front-end include it in the `fields` object it already sends to
   `PATCH /api/tasks/<id>` (or `/dynamic-fields`). `save_task` /
   `save_task_dynamic_fields` upsert **any** key into `task_dynamic_fields`
   and log the change to history automatically.

You DO need backend work when:

- **The value must appear in overview/portfolio reporting** — add one entry to
  `DYNAMIC_FIELD_OVERVIEW_MAP` in `workflow.py` mapping the field key to a
  `project_overview` column, e.g. `"flowback_gas_rate_mmscfd":
  "flowback_results"`. The mirror happens on every save.
- **The field is calculated, not typed** — follow the Seal CoS pattern: the
  formula lives in `cos.py` (`calculate_seal_cos`), and both save paths in
  `workflow.py` overwrite the stored key on save (search for
  `seal_cos_pct` in `save_task` and `save_task_dynamic_fields`). If your field
  feeds Presence CoS, also look at `recalculate_presence_cos` and its trigger
  set (`{"Reservoir CoS", "Trap CoS", "Seal CoS"}`).

## Recipe 3: Change a CoS formula

1. Edit the pure function in `cos.py` (`calculate_seal_cos`,
   `calculate_reservoir_cos_rows`, `calculate_presence_cos`, or
   `segment_class`). Keep the module free of DB/Flask imports.
2. Update the pinned expectations in `tests/test_cos.py` — they encode the
   formula, so they MUST change with it (deliberately).
3. Stored values do not recompute themselves: historical rows keep the number
   calculated at save time. If business needs old records recalculated, write a
   migration step (recipe 5) that calls the calculation for every project —
   `_consolidate_to_v15`'s final loop over `recalculate_presence_cos` is
   exactly this pattern.
4. Document the change in README.md's "Version history" — users rely on those
   notes to know which formula produced which stored value.

## Recipe 4: Add an API endpoint

1. Write the domain function first, in `workflow.py` (lifecycle/writes) or
   `reporting.py` (read-only aggregates): `def my_thing(session, ...,
   changed_by="Web User")`. Raise `ValueError("...")` for anything the user did
   wrong; the message is shown verbatim.
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

## Recipe 5: Write a schema migration

1. Copy `_upgrade_to_v16` in `migrations.py` — it is the intended template:
   one focused change set, a docstring saying what/why, only idempotent
   statements.
2. Idempotency rules: column adds via `_ensure_column(...)`; backfills
   NULL-guarded (`... SET x = y WHERE x IS NULL`); row creation via
   `INSERT OR IGNORE`. A step must be safe to run twice.
3. Append `(17, _upgrade_to_v17)` to `MIGRATIONS` and bump
   `LATEST_SCHEMA_VERSION` to 17. New columns also go into `models.py`
   (appended last, so fresh `create_all` databases match ALTER-ed ones).
4. Test it like `test_migration_v15_to_v16_backfills_and_is_idempotent` in
   `tests/test_phase4.py`: bootstrap a fresh test DB, reshape it to the OLD
   form with raw sqlite3 (drop the column, set `app_settings.schema_version`
   back), then `db.reset_for_tests()` + `db.init_db(path)` and assert the
   upgrade — then bootstrap once more and assert nothing changed.

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
  Bootstrap recreates and migrates automatically.
- Tests never touch your local DB — each test gets its own file under pytest's
  tmp path (see `tests/conftest.py`).
