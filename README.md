# Segment Maturation and Execution System

A dashboard for tracking unconventional gas wells from lead assessment through
risk analysis and pre-well delivery into business-plan execution. Each
lead/well moves through a fixed 27-component workflow with per-component
technical inputs (Chance-of-Success calculations, resource estimates), an
audit trail, and portfolio-level reporting. The backend is Flask + SQLite
(SQLAlchemy underneath); the front-end is vanilla JS served from `static/`.

New to the codebase? Read `docs/ARCHITECTURE.md` for the module map and
`CONTRIBUTING.md` for step-by-step recipes for common changes. Confirmed defects
awaiting implementation are tracked in `docs/KNOWN_ISSUES.md`.

## Quickstart

Python 3.9 or newer is required (production runs 3.9 — write 3.9-compatible
code; see CONTRIBUTING.md).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

The app serves at <http://127.0.0.1:8020>. On first start it creates
`pipeline_tracker.db` beside `main.py` and seeds it from `models.py`.

**Schema note:** `models.py` is the single source of truth for the current
schema (fresh databases are created straight from it), and existing databases
are upgraded **in place** at startup by the numbered migration steps in
`migrations.py` -- no data is ever discarded on a schema change. Booting
against a database stamped with a NEWER schema version than the code knows
raises a clear `RuntimeError` (the code is older than the database; update
the code rather than downgrading the database).

## Configuration

All configuration lives in `config.py` and is driven by environment variables.
There is no other config file.

| Env var | Default | Effect |
|---|---|---|
| `SEGMENT_TRACKER_DB_PATH` | `./pipeline_tracker.db` | Path of the SQLite database file. |
| `DATABASE_URL` | unset | Full SQLAlchemy URL; when set it overrides `SEGMENT_TRACKER_DB_PATH` entirely (this is the Postgres switch — see below). |
| `SEGMENT_TRACKER_RF_MODEL_PATH` | `./RF_model.joblib` | Location of the approved Reservoir-CoS RandomForest model. |
| `SEGMENT_TRACKER_SECRET_KEY` | dev-only insecure default | Flask session-cookie signing key. Set a real value for any shared deployment. |
| `SEGMENT_TRACKER_PASSCODE` | unset | When set, `POST /api/login` requires this shared passcode. Unset = name-only login (trusted internal network). |
| `AUTH_REQUIRED` | `false` | When true (`1`/`true`/`yes`/`on`), every `/api/*` endpoint except `/api/health`, `/api/login`, `/api/logout`, `/api/me` and `/api/users` requires a logged-in session (`/api/users` stays open because the login dialog needs the name list before a session exists; names/roles are not secrets on the trusted internal network). |
| `SEGMENT_TRACKER_COOKIE_SECURE` | `false` | Marks the session cookie `Secure` (HTTPS-only). Enable once the app is served over TLS. |

## The Reservoir CoS model

Reservoir CoS is calculated by the approved `RF_model.joblib` model from three
inputs per row: Pull-up (No=0, Semi=1, Yes=2), Amplitude Ratio, and Base Tight
Sarah (BTS). Place the file beside `main.py`:

```text
segmentation-dashboard/
  RF_model.joblib
```

Or point at a custom path before launching:

```bash
export SEGMENT_TRACKER_RF_MODEL_PATH="/secure/path/RF_model.joblib"
```

The application loads the model once per server process. **Do not place
untrusted joblib files in the application folder** — loading a joblib file
executes code, so only the approved, governance-controlled model may be used.

## Authentication

By default the API is open: anyone on the network can read and write, and each
change records whatever `changed_by` name the client sends (the front-end sends
"Web User" when nobody is signed in). Known users live in the `users` table,
seeded from the `SEED_USERS` placeholder list in `config.py` (edit it before
deploying); each user has a role — `supervisor`, `staff` or `employee` — that
gates assignment and approval actions. A Ready component can be returned by a
supervisor or by the user assigned to that component.

- `POST /api/login` `{"name": "...", "passcode": "..."}` — starts a session.
  The name must match an active `users` row (case-insensitive; 401 `Unknown
  user.` otherwise); the session stores the row's canonical casing and role,
  and the response returns both. The passcode is only required when
  `SEGMENT_TRACKER_PASSCODE` is set.
- `GET /api/me` — `{"authenticated": bool, "name": ..., "role": ...}`; never
  returns 401.
- `GET /api/users` — active users as `[{name, role}]` (login and assignee
  dropdowns).
- `POST /api/logout` — ends the session; always 200.

While a session is active, every change is stamped with the session name —
the client-sent `changed_by` is ignored. Setting `AUTH_REQUIRED=1` makes a
session mandatory for all other API calls. The front-end supports this: any
401 response opens a sign-in dialog (name dropdown filled from `/api/users`,
plus an optional passcode field) and retries the failed request once after a
successful login; a "Signed in as name (role)" chip and Sign out button appear
in the header while a session is active.

## Data & backups

All data lives in one SQLite file (default `./pipeline_tracker.db`). It is
deliberately **not** in git (see `.gitignore`). To back up: stop the app, then
copy `pipeline_tracker.db` plus the `-wal` / `-shm` files if present.

## Switching to Postgres

Set `DATABASE_URL` (e.g. `postgresql+psycopg2://user:pass@host/dbname`). All
queries use dialect-portable named bind parameters, **but** a handful of
SQLite-only constructs are flagged in the code and must be addressed first:

```bash
grep -rn "PG:" --include="*.py" .
```

(covers `COLLATE NOCASE`, `substr()` month bucketing, and `lastrowid` vs
`RETURNING`; the `INSERT OR IGNORE` seeding in `migrations.py` is also
SQLite-flavored.)

## Deployment

For shared use, run behind Gunicorn/Nginx. SQLite startup bootstrap is
process-safe: workers serialize the full WAL/schema/migration/seed sequence
with a per-database sidecar lock. The simplest operational rule is still: on
the **first boot after an upgrade**, start a single instance, let it finish
bootstrapping, then scale out.

## Running tests

```bash
.venv/bin/pytest tests/ -q
```

The pytest suite covers the HTTP API contract, the workflow lifecycle,
promotion/demotion, the CoS math, schema bootstrap, error handling and identity.

## Version history

### What changed in v12

- Marking a prospect/lead as **Business Plan** now promotes it into the **Business Plan Execution** pipeline.
- The full lead-side technical summary is captured at the instant of promotion and retained as a frozen **Lead Summary** snapshot.
- In a promoted well's right-hand panel, use **Lead Summary** to show or hide that captured lead view. The normal Well Summary remains the active Business Plan summary.
- Promotion preserves all original lead tasks, task IDs, inputs, and history. BP tasks become operational and begin at **BP Execution Gate**.
- Reservoir CoS is ready for calculation with the approved `RF_model.joblib` model. Each row uses exactly:
  1. Pull-up
  2. Amplitude Ratio
  3. Base Tight Sarah (BTS)

  The model result is stored and displayed as a whole-number percent, for example `44%`.

### v13 — Seal CoS calculation

(merged into "Trap and Seal CoS" in v5 — the inputs, the formula and the stored
`seal_cos_pct` key are unchanged.)

The **Seal CoS** component now uses five technical inputs:

- Most recent age of activity
- Dip
- Azimuth vs. SHmax
- Fault Level of Confidence
- Fracture Permeability

The system calculates and stores **Seal CoS (%)** automatically when the component is saved:

- When **Most recent age of activity > 0.9**: `activity × fracture permeability`
- Otherwise (including `0.9`): `average(dip, azimuth vs. SHmax, fault level of confidence) × fracture permeability`

The result is displayed as a whole-number percentage, such as `44%`. Inputs should be entered as decimal factors used by the technical formula (for example, `0.44` for 44%).

The formula is unchanged, but since KI-004 the **result** is range-checked when it is saved: a set of inputs whose Seal CoS works out above 100% (or below 0%) is refused with a message naming the computed value and the inputs to adjust, for example *"Seal CoS computes to 116% from these inputs; adjust Most recent age of activity or Fracture Permeability."* Exactly `100%` is still accepted. Leads saved **before** this check may carry an out-of-range percentage; those keep displaying it, and their Total Chance of Success simply reads as unavailable until the Seal inputs are corrected and re-saved.

### v14 — Lead mean gas in Well Summary

- **Mean PIIP Gas (BCF) — Lead Phase** is now shown in the right-hand Well Summary as soon as it is saved in **Resource Assessment**.
- For a lead promoted to Business Plan, the same value is retained in the frozen **Lead Summary** view.

### v15 changes
- Seal and Reservoir CoS results display in calculated boxes beneath their input fields.
- Pull-up is now No / Semi / Yes. The RF model receives No=0, Semi=1, Yes=2.
- Toggling Business Plan on moves a Lead to BP Execution; toggling it off moves it back to Prospect Maturation without deleting BP work, inputs, lead summary, or history.

### v16: Automatic Presence CoS
Presence CoS is now calculated automatically and is read-only. The dashboard uses the final (last completed) Reservoir CoS row, Trap CoS, and Seal CoS (the latter two merged into "Trap and Seal CoS" in v5; both halves keep their own inputs and stored percentages):

`Presence CoS = Final Reservoir CoS × Trap CoS × Seal CoS`

Scores may be stored as decimals or whole percentages. The dashboard displays and stores the final result as a whole percentage. Source values and the calculation refresh automatically whenever Reservoir CoS, Trap CoS, or Seal CoS is saved.

v16 also ships a full backend refactor:

- The backend was restructured from one monolithic `database.py` into small single-purpose modules on SQLAlchemy (see `docs/ARCHITECTURE.md`); the SQLite data files are unchanged and upgrade in place.
- Error handling is centralized: validation errors return their message with HTTP 400, optimistic-lock conflicts 409, and internal failures a generic 500 (details go to the server log, never to the client).
- Completed wells now carry an explicit `completed_at` timestamp, so monthly "wells completed" reporting no longer shifts when a completed well is edited later.
- New identity endpoints (`/api/login`, `/api/logout`, `/api/me`) let users sign their changes; an optional `AUTH_REQUIRED` mode can enforce login API-wide.
- Removed unused API surface after auditing the web front-end (the API's sole consumer; re-verified against the redesigned `static/js/` modules): `PATCH /api/projects/<id>/archive`, `PATCH /api/projects/<id>/location`, `PATCH /api/projects/<id>/lead-folder`, `PATCH /api/projects/<id>/business-plan`, `GET /api/projects/<id>/next-task`, `GET /api/projects/<id>/overview`, `GET /api/overview/all`, `GET/POST /api/business-plan/commitment`, `GET /api/dashboard/metrics`, `GET /api/dashboard/monthly`, `GET /api/owners`.

### v17: Users, roles, and the 4-status lifecycle

- Component statuses collapse to an implicit 4-state lifecycle — **Not Assigned → In Progress** (assignment) **→ Ready** (submit) **→ Approved** (supervisor) — with Return sending Ready back to In Progress. "Not Applicable" remains internal-only. Existing databases migrate their old status vocabulary automatically.
- New `users` table with roles (`supervisor` / `staff` / `employee`), seeded from the `SEED_USERS` list in `config.py`. Login now requires a known active user (see Authentication above); approval is supervisor-only, Return is available to supervisors and the component's assignee, and an employee may only submit components assigned to them.
- Front-end: sign-in dialog with automatic 401 retry, header identity chip, assignee dropdowns and per-board assignee filters fed by `/api/users`.
- New endpoints: `GET /api/meta` (authoritative stage/status/role lists), `GET /api/users`, `POST /api/tasks/<id>/assign` (with optional cascade to later unassigned steps), `POST /api/tasks/<id>/transition` (submit/approve/return). Removed: `GET /api/open-folder` (the per-component folder card remains).

### v18: 31-step workflow

- The "Presence CoS Evaluation" step is retired as a visible component — the value is derived automatically (final Reservoir CoS × Trap CoS × Seal CoS) — and the workflow is renumbered to a contiguous 1–31. Retired rows, their inputs and history are preserved, never deleted.
- New leads capture X/Y at creation; the values prefill the Staking step's well location fields.

### v19: Formations and the Portfolio rework

- Formation interpretation values (SARH/QASM/QWRH, quicklook and final phases) move off scattered step fields into a well-level `project_formations` table, edited through a mini-sheet on the logs steps. Legacy SARH values are backfilled once. New endpoints: `GET`/`PUT /api/projects/<id>/formations`.
- The Portfolio tab becomes an 8-column mature-prospect analysis table: Well Name, Gas Field, Seismic Block (mapped via `SEISMIC_BLOCK_NAMES` in `config.py`), Classification, BP Year, Fluid, Mean OGIP, Total CoS.

### Schema v4: BP step merges (31 → 27 steps)

- Four Business Plan steps are merged away: **URED Update** folds into
  **Executive Summary**, **Post-Drilling Resource Assessment** into **SAD
  Model**, and **Resource Assessment Update** + **Executive Summary Final**
  into **SAD Update**. The workflow is now 27 steps (12 prospect + 15 BP).
- Nothing is deleted: a retired step's row becomes `is_active = 0` and keeps
  its inputs and history, the surviving step reuses the retired step's input
  keys (`post_drill_piip_*`, `resource_update_*`), and every reader stays
  retired-inclusive so a well drilled before the merge still shows its numbers.
  Applied by `migrations._migrate_v4_bp_step_merges`.

### Schema v5: the permanent 12-item prospect template

The prospect half becomes the 12 tracked items the board and detail sidebar had
been faking through a read-time adapter (still 12 prospect steps, so still 27 in
total; BP numbers 13–27 do not move). Applied by
`migrations._migrate_v5_prospect_template_restructure`:

- **Renames** (a `task_name` rewrite in place, so the row keeps its ID, inputs,
  history and folder card): Reservoir Area Definition → **Area Definition**,
  Lead Resource Assessment → **Resource Assessment**, Prospect Evaluation
  Presentation → **Segmentation Slides**, Staking Moving Tolerance → **Moving
  Tolerance**, Pre-Drilling Resource Assessment → **Pre-Drilling GeoX
  Assessment**.
- **Trap CoS** and **Seal CoS** merge into one step, **Trap and Seal CoS**,
  which keeps both halves' input keys verbatim.
- **Well Creation** is retired; its sign-off is now a checkbox on **Approval to
  Stake**.
- **GRV Inputs** and **Well Site Location** are added as tracked steps.
- The prospect stage groups are remapped onto the three board columns —
  **Lead Assessment**, **Risk Analysis**, **Pre-Well Delivery** (the old Lead
  Identification / Risking / Segmentation groups survive only as
  `LEGACY_PROSPECT_STAGE_GROUPS` in `workflow/constants.py`).

### The pre-deployment architecture reset (historical, superseded)

Because nothing was ever deployed, the legacy-data machinery the notes above
describe was deleted wholesale rather than carried forward:

- Schema migrations were dropped at the time; `models.py` remains the single
  source of truth for a fresh database. **This part is superseded:** the
  numbered-migration era is active again — the schema is at
  `LATEST_SCHEMA_VERSION = 5` (`migrations.py`) with four shipped steps, and
  existing databases upgrade in place (see the Quickstart schema note).
- The `task_templates` and `project_overview` tables are gone: the workflow is
  defined in code (`PIPELINE_TEMPLATES`), and the overview shown in the detail
  panel — including Total CoS — is composed from step inputs at read time.
- The board pointers (current stage/task/owner, overall status) are derived
  from the task rows at read time instead of being stored, and the stored
  "Not Applicable" status is gone — applicability follows the pipeline.
- Dead columns were dropped; coordinates and formation measurements became
  real numeric columns with input validation.

See `docs/ARCHITECTURE.md` ("Derive, don't store") for the design rationale.
