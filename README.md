# Segment Maturation and Execution System

A dashboard for tracking unconventional gas wells from lead identification
through risking, segmentation and business-plan execution. Each lead/well moves
through a fixed 32-component workflow with per-component technical inputs
(Chance-of-Success calculations, resource estimates), an audit trail, and
portfolio-level reporting. The backend is Flask + SQLite (SQLAlchemy underneath);
the front-end is vanilla JS served from `static/`.

New to the codebase? Read `ARCHITECTURE.md` for the module map and
`CONTRIBUTING.md` for step-by-step recipes for common changes.

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
`pipeline_tracker.db` beside `main.py` and runs any pending schema migrations
automatically.

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
| `AUTH_REQUIRED` | `false` | When true (`1`/`true`/`yes`/`on`), every `/api/*` endpoint except `/api/health`, `/api/login`, `/api/logout` and `/api/me` requires a logged-in session. |
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
"Web User"). Optionally, users can identify themselves:

- `POST /api/login` `{"name": "...", "passcode": "..."}` — starts a session;
  the passcode is only required when `SEGMENT_TRACKER_PASSCODE` is set.
- `GET /api/me` — `{"authenticated": bool, "name": ...}`; never returns 401.
- `POST /api/logout` — ends the session; always 200.

While a session is active, every change is stamped with the session name —
the client-sent `changed_by` is ignored. Setting `AUTH_REQUIRED=1` makes a
session mandatory for all other API calls. Note: the front-end has no login
screen yet, so enforcement is intended for after that lands.

## Data & backups

All data lives in one SQLite file (default `./pipeline_tracker.db`). It is
deliberately **not** in git (see `.gitignore`). To back up: stop the app, then
copy `pipeline_tracker.db` plus the `-wal` / `-shm` files if present. Schema
migrations run automatically at startup — an old database file is upgraded in
place the first time a newer app version boots against it.

## Switching to Postgres

Set `DATABASE_URL` (e.g. `postgresql+psycopg2://user:pass@host/dbname`). All
queries use dialect-portable named bind parameters, **but** a handful of
SQLite-only constructs are flagged in the code and must be addressed first:

```bash
grep -rn "PG:" *.py
```

(covers `COLLATE NOCASE`, `substr()` month bucketing, `lastrowid` vs
`RETURNING`, `INSERT OR IGNORE`, `PRAGMA table_info`, and SQLite's relaxed
`GROUP BY`.)

## Deployment

For shared use, run behind Gunicorn/Nginx. Startup migrations are process-safe
(each migration step takes the database write lock up front), but the simplest
operational rule is: on the **first boot after an upgrade**, start a single
instance, let it finish migrating, then scale out.

## Running tests

```bash
.venv/bin/pytest tests/ -q
```

The suite (98 tests) covers the HTTP API contract, the workflow lifecycle,
promotion/demotion, the CoS math, migrations, error handling and identity.

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

### v14 — Lead mean gas in Well Summary

- **Mean PIIP Gas (BCF) — Lead Phase** is now shown in the right-hand Well Summary as soon as it is saved in **Lead Resource Assessment**.
- For a lead promoted to Business Plan, the same value is retained in the frozen **Lead Summary** view.

### v15 changes
- Seal and Reservoir CoS results display in calculated boxes beneath their input fields.
- Pull-up is now No / Semi / Yes. The RF model receives No=0, Semi=1, Yes=2.
- Toggling Business Plan on moves a Lead to BP Execution; toggling it off moves it back to Prospect Maturation without deleting BP work, inputs, lead summary, or history.

### v16: Automatic Presence CoS
Presence CoS is now calculated automatically and is read-only. The dashboard uses the final (last completed) Reservoir CoS row, Trap CoS, and Seal CoS:

`Presence CoS = Final Reservoir CoS × Trap CoS × Seal CoS`

Scores may be stored as decimals or whole percentages. The dashboard displays and stores the final result as a whole percentage. Source values and the calculation refresh automatically whenever Reservoir CoS, Trap CoS, or Seal CoS is saved.

v16 also ships a full backend refactor:

- The backend was restructured from one monolithic `database.py` into small single-purpose modules on SQLAlchemy (see `ARCHITECTURE.md`); the SQLite data files are unchanged and upgrade in place.
- Error handling is centralized: validation errors return their message with HTTP 400, optimistic-lock conflicts 409, and internal failures a generic 500 (details go to the server log, never to the client).
- Completed wells now carry an explicit `completed_at` timestamp, so monthly "wells completed" reporting no longer shifts when a completed well is edited later.
- New identity endpoints (`/api/login`, `/api/logout`, `/api/me`) let users sign their changes; an optional `AUTH_REQUIRED` mode can enforce login API-wide.
- Removed unused API surface after auditing the web front-end (the API's sole consumer; re-verified against the redesigned `static/js/` modules): `PATCH /api/projects/<id>/archive`, `PATCH /api/projects/<id>/location`, `PATCH /api/projects/<id>/lead-folder`, `PATCH /api/projects/<id>/business-plan`, `GET /api/projects/<id>/next-task`, `GET /api/projects/<id>/overview`, `GET /api/overview/all`, `GET/POST /api/business-plan/commitment`, `GET /api/dashboard/metrics`, `GET /api/dashboard/monthly`, `GET /api/owners`.
