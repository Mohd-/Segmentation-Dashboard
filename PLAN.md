> **HISTORICAL — SUPERSEDED. Do not use this document as a specification.**
>
> Every track in this plan (A through E) has shipped, and a large redesign has
> landed on top of them. The file is kept only as a record of the original
> handoff: its line references, file layouts and counts no longer match the
> code, and its "Ground rules" section in particular is out of date — the
> schema is NO LONGER pre-deployment. Databases hold real data and upgrade in
> place through numbered migrations; the schema is at
> `LATEST_SCHEMA_VERSION = 5`.
>
> For current facts read `README.md` (what the system is, version history),
> `ARCHITECTURE.md` (module map, data model, derive-don't-store) and
> `CONTRIBUTING.md` (the recipes, including the migration recipe). Nothing
> below is maintained.

# Implementation Plan: Seed Data, Login Page, Compact UI, Portfolio, Dark Theme

Handoff plan for an implementing agent. Each track is independent; suggested
order: A (seed data) first — every other track needs a populated DB to verify
against — then B, C, D, E in any order.

## Ground rules (from ARCHITECTURE.md / CONTRIBUTING.md — read both first)

- Backend: Flask routes in `main.py` stay thin; domain logic in `workflow/`,
  read-only aggregates in `reporting.py`. Writes go through
  `db.write_transaction`; no try/except in routes.
- **Derive, don't store**: never persist anything computable from other rows.
- Schema is pre-deployment: a schema change = edit `models.py`, delete the dev
  `.db` (+`-shm`/`-wal`), restart. No migrations. (None of these tracks should
  need a schema change.)
- Frontend: no framework, no build step — ES modules under `static/js/`,
  design tokens in `static/css/base.css`, component styles in
  `static/css/components.css`. Every color/spacing must trace to a token.
- Tests: `.venv/bin/python -m pytest -q` (system python is absent). Run the
  app with `.venv/bin/python main.py` → http://127.0.0.1:8020. Point
  `SEGMENT_TRACKER_DB_PATH` at a scratch path for end-to-end runs.
- `static/index.html` bumps `?v=` cache-busters on CSS/JS when they change;
  `API_VERSION` in `static/js/api.js` likewise if API shapes change.

---

## Track A — Synthetic seed data for testing

**Goal**: one command fills a dev DB with realistic synthetic data so every
screen (boards, detail, portfolio, audit) has content.

**New file**: `seed_dev.py` (repo root, next to `main.py`).

Design:
- Go through the domain layer, not raw SQL, so derived state and history stay
  consistent: `workflow.add_project`, `workflow.assign_task`,
  `workflow.save_task`, `workflow.transition_task`,
  `workflow.save_task_dynamic_fields`, `workflow.upsert_project_formations`,
  and the promotion path (`workflow/promotion.py`) for BP wells. Open a
  session via `db.session_factory` / the same bootstrap `main.py` uses
  (`db.init_db()` first).
- Seed a handful of extra synthetic users directly (idempotent insert into
  `users`, mirroring `migrations.py`'s seeding idiom) so assignees vary:
  e.g. 2 supervisors, 2 staff, 4 employees with obviously-fake names
  ("Test Supervisor A", …). Do NOT touch `config.SEED_USERS` (that's the
  owner's placeholder to fill with real names).
- Content mix (~20–25 projects, deterministic via `random.seed(42)`):
  - Prospect leads spread across all three `PROSPECT_STAGES` — achieve stage
    placement by assigning + approving the right prefix of the 27 steps
    (`PIPELINE_TEMPLATES` in `workflow/constants.py`); the board derives
    current stage from task rows.
  - A mix of task statuses (Not Assigned / In Progress / Ready / Approved)
    and priorities (Low/Medium/High) so chips and filters all light up.
  - 8–10 BP wells (`pipeline_type='bp'`, `business_plan_enabled=1`, years
    spread over 2026–2032, some `active_well_enabled`). Fill the fields the
    Portfolio composes from (`_BP_TASK_FIELD_KEYS` in `reporting.py`):
    `reservoir_cos_rows` (JSON rows with `seismic_volume_ar_number` like
    "AR-0000001" and `reservoir_cos_pct`), `trap_cos_pct`, `seal_cos_pct`,
    `gheer_classification`, the four PIIP means, fluid types. Well names
    shaped `FIELD-N` (e.g. "MDFT-3") — `gas_field` is the prefix before the
    first hyphen.
  - Formation rows (SARH/QASM/QWRH × quicklook/final) for a few drilled wells
    via `upsert_project_formations`.
  - Comments and a few submit/approve/return cycles so the Audit Trail is
    non-trivial. Vary `changed_by` across the seeded users.
- Safety: refuse to run if the target DB already has projects (print the
  count and exit non-zero) unless `--force` (which then only ADDS, never
  deletes). Print the DB path being seeded.
- Usage line in the module docstring:
  `SEGMENT_TRACKER_DB_PATH=/tmp/seed.db .venv/bin/python seed_dev.py`

**Verify**: run it on a fresh scratch DB, start the app, eyeball all six
tabs; run the full pytest suite (must stay green — the script must not import
side effects into the app).

---

## Track B — Login page

**Current state**: auth is complete server-side (`POST /api/login`,
`/api/logout`, `/api/me`, `AUTH_REQUIRED` gate in `main.py`). The frontend
only has a modal `<dialog id="login-dialog">` that pops on the first 401
(`static/js/api.js` + `loginDialog()` in `static/js/dialog.js`). With
`AUTH_REQUIRED` on, the user briefly sees the empty app behind a modal.

**Goal**: a real full-page login screen in front of the app.

Steps:
1. `main.py` — add `"auth_required": config.AUTH_REQUIRED` to the `/api/me`
   response (read at request time, like the before_request hook, so tests can
   monkeypatch). Update the `/api/me` docstring and, if
   `tests/test_api_contract.py` pins the shape, the test.
2. `static/index.html` — add a `<section id="login-page" class="login-page hidden">`
   before `<main>`: brand lockup, a name `<select>`, passcode input, error
   line, sign-in button. Give `<main>`, the header actions, and the tab bar a
   single wrapper or a shared class so they can be hidden/shown as one unit
   (e.g. add class `app-chrome` to header/nav/main).
3. `static/js/main.js` boot sequence — after the existing
   `Promise.all([API.meta(), API.me()])`: if `me.auth_required && !me.authenticated`,
   show the login page and do NOT call `boot()`'s data loads yet; on
   successful login (reuse the fetch logic from `dialog.js` — extract a shared
   `performLogin(name, passcode)` helper into `dialog.js` or a new
   `static/js/auth.js` consumed by both), hide the login page and continue
   boot. Note: `API.meta()` 401s under AUTH_REQUIRED and currently opens the
   modal — reorder so `/api/me` (never 401s) is probed first and the meta
   call is deferred until after login when `auth_required` is set.
4. Keep the existing modal as-is for mid-session expiry (the 401-retry path
   in `api.js` still works).
5. CSS — `.login-page` in `components.css`: centered card
   (`min-height: 100vh; display:grid; place-items:center`), reusing panel and
   dialog-form tokens. Must look right in dark theme (Track E).

**Verify**: `AUTH_REQUIRED=true SEGMENT_TRACKER_DB_PATH=/tmp/seed.db .venv/bin/python main.py`
→ full-page login appears before any app chrome; sign in as each seeded role;
sign out returns to the login page (the existing reload-on-signout in
`main.js` handles this). Also verify dev mode (AUTH_REQUIRED off) boots
straight into the app with no flash of the login page.

---

## Track C — Compact, coherent UI

All frontend-only. Files: `static/index.html`, `static/js/views/detail.js`,
`static/js/views/detail-form.js`, `static/js/views/pipeline.js`,
`static/css/base.css`, `static/css/components.css`.

### C1. Tighten vertical space globally
- `base.css`: reduce `.app-header` height (72px → ~56px) and `.tabs` height
  (54px → ~44px); update every dependent offset — sticky `top` values on
  `.tabs`, `.component-rail`, `.summary-panel`, `.detail-shell`
  scroll-margin-top (comments in `components.css:110-124` and `:286-292` name
  the arithmetic), and the 640px-breakpoint variants.
- Reduce `.panel` padding (`--space-5` → `--space-4`) and panel margins;
  tighten `.component-form` gap and `.dynamic-fields` gap one step.

### C2. Compact step editor: remove Assignee row, color-coded status
- `index.html`: delete the `.form-row` containing the Assignee and Priority
  selects (lines 117–128).
- **Assignment must remain possible** (it's the only way a step leaves
  Not Assigned — see `TASK_TRANSITIONS`). Move it into the editor head as a
  compact control: a small borderless `<select id="assigned-to">` (or chip
  that reveals a select on click) rendered next to the status chip, styled
  like a chip (`padding: 4px 10px`, `--text-xs`). All the existing logic in
  `detail-form.js` (`renderAssigneeSelect`, `assignComponent`, the
  `canManageAssignments()` disable) keeps working — only placement/markup
  changes. Keep the id `assigned-to` so `main.js`'s `safeOn('assigned-to', ...)`
  wiring is untouched.
- Color coding for status: already exists for the editor chip
  (`renderStatusChip` → `.status.*` classes). Extend to the left rail: in
  `detail.js` `renderDetail()`, replace the plain
  `<small>status</small>` on `.component-item` with the shared `statusChip()`
  from `dom.js` (or a colored dot + text), and shrink `.component-item`
  padding (10px → 6px 8px) and its number badge (28px → 22px).

### C3. Priority becomes a clickable chip next to status
- `index.html`: `#component-priority` select is gone (C2). Add
  `<button id="component-priority-chip" type="button">` in `.editor-head`
  next to `#component-status-chip`.
- `detail-form.js`: render it in `loadComponent()` with the existing
  `priority-low/medium/high` classes (`components.css:21-34`); on click cycle
  Low → Medium → High → Low and call the **existing**
  `PATCH /api/tasks/<id>/priority` endpoint (`API.priority`, already in
  `api.js` but currently unused), then `refreshAfterRecordChange`.
- `saveComponent()` currently sends `priority: byId('component-priority').value`
  — remove that key from the save payload (the backend's `save_task` preserves
  absent keys; confirm in `workflow/lifecycle.py`, else send
  `Store.task.priority`).
- Title the button ("Priority: Medium — click to change") for discoverability.

### C4 + C5. Comments above a compact inline file-location box
- Current order (see `renderComponentFolder` in `detail-form.js:292-304`):
  dynamic-fields → folder card → comments → actions. Wanted: dynamic-fields →
  comments → folder card → actions. Change the insertion point: insert the
  card after the comments `<label class="wide-field">` (give it
  `id="comments-field"` in `index.html`) instead of after `#dynamic-fields`.
- Compact card: drop the `<b>Component File Location</b>` title; render one
  row: a folder glyph, the UNC path in `--font-mono` at `--text-xs`
  (`white-space: nowrap; overflow: hidden; text-overflow: ellipsis;`
  `title` attr = full path), and the existing copy icon-button. Restyle
  `.folder-card` in `components.css` accordingly (`display:flex; align-items:
  center; gap; padding: 6px 10px`); keep the copy handler.

### C6. Remove panel page-intros
- `index.html`: delete all four `<div class="panel page-intro">…</div>`
  blocks (prospect, bp, portfolio, audit). Delete the now-dead `.page-intro`
  rules in `base.css`.

### C7. Remove the Add Well panel from BP Execution
- `index.html`: delete the panel containing `#add-well-form` (lines 60–68).
- `static/js/views/pipeline.js`: delete `addWell` and its export;
  `static/js/main.js`: delete the `add-well-form` wiring and the
  `new-well-bp-year` fill in `boot()`.
- Wells still enter BP Execution via lead promotion (the Business Plan flag
  in the summary panel / `workflow/promotion.py`) — unchanged. Leave the
  backend `POST /api/projects` accepting `pipeline_type='bp'` (API contract
  + tests + seed script use it).

### C8. Collapse New Lead into a "+" disclosure
- `index.html`: replace the New Lead panel heading with a toggle row —
  `<button id="new-lead-toggle" type="button" class="ghost">+ New Lead</button>`
  — and wrap the existing `#create-lead-form` in a hidden container
  (`id="new-lead-body"`, class `hidden`).
- `main.js` `wire()`: toggle `#new-lead-body` on click (swap label to
  "− New Lead" when open; focus `#new-lead-name` on open). Collapse again
  after successful create (`createLead` already resets the form; also re-add
  `hidden`). Keep it a slim panel so the collapsed state costs ~40px.

---

## Track D — Portfolio: sortable/filterable table + clickable well pages

Files: `static/js/views/portfolio.js` (rewrite, it's 37 lines),
`static/js/dom.js` (leave `table()` alone — build portfolio-specific
rendering in portfolio.js), `static/css/components.css`.

### D1. Excel-like sorting + filtering inside the table
Client-side is the right call: the dataset is all BP wells (tens of rows),
already fully delivered by `GET /api/portfolio/rows`. No backend change.

- Keep one module-level state object:
  `{ rows: [], sortKey: null, sortDir: 1, filters: {} }`; fetch once per
  `refreshPortfolio()`, re-render locally on sort/filter changes.
- **Sorting**: every `<th>` clickable, cycling asc → desc → none. Numeric
  columns (`BP Year`, `Mean OGIP`, `Total CoS`) compare with
  `Number()` (blanks last); text columns `localeCompare`. Show ▲/▼ affordance
  in the header (CSS `::after` on `th.sorted-asc/.sorted-desc`).
- **Filtering**: a second header row of compact controls under the `<th>`s —
  text `<input>` (substring, case-insensitive) for Well Name / Seismic Block,
  `<select>` of distinct values for Gas Field / Classification / BP Year /
  Fluid. This is the "common, professional" pattern and needs no popover
  machinery. Recompute the visible rowset as: activity+year server filters →
  column filters → sort.
- Keep the existing toolbar `#portfolio-year-filter` /
  `#portfolio-activity-filter` selects working as-is (activity isn't a
  column), or fold year into the column filter and drop the toolbar year
  select — implementer's choice; don't duplicate year filtering in both
  places.
- **Stats must follow the visible rows**: compute the Wells count and
  Cumulative OGIP client-side from the filtered rowset instead of
  `payload.summary`, so the tiles agree with what's displayed (mirror the
  rounding in `reporting.py:266-271`).

### D2. Well name links to the well page
- `get_portfolio_rows` already returns `project_id` (`reporting.py:248`) —
  the current renderer just drops it.
- Render Well Name as a link/button carrying `data-project-id`; on click call
  `openDetail(row.project_id, 'bp')` (import from `./detail.js`; note
  `pipeline.js` already imports `refreshPortfolio` from portfolio.js — import
  `openDetail` directly from `detail.js`, NOT via pipeline.js, to avoid a
  cycle). The detail shell is the well page: it already provides full
  view/edit of every component, flags, rename/archive. `back-to-overview`
  labels itself from `Store.pipeline`, which `openDetail` sets to `'bp'` —
  acceptable; optionally special-case a "Back to Portfolio" label when opened
  from the portfolio tab by remembering the originating tab in `Store`.
- Style: `.portfolio-table .well-link` using `--brand-700`, underline on
  hover; row hover already exists.

**Verify**: with seeded data — sort each column both directions (numeric
columns must sort numerically), stack filters, confirm stats tiles track the
filtered set, click a well name → detail opens on the BP pipeline, edit a
field, return, portfolio reflects it after refresh.

---

## Track E — Dark theme

Files: `static/css/base.css`, `static/css/components.css`,
`static/index.html`, `static/js/main.js`.

- `base.css` already funnels every color through `:root` tokens — that's the
  whole mechanism. Add a `:root[data-theme="dark"]` block overriding the
  token set: surfaces (`--bg`, `--surface`, `--surface-sunken`), borders,
  text (`--text*`), the ink/brand/gray ramps, and the semantic `-100` tints
  (they're used as chip backgrounds — in dark theme make them
  low-alpha e.g. `rgba(47,143,87,.16)` so chips stay legible), plus
  `--shadow-*` (darker, subtler). Set `color-scheme: dark` there (the
  `html { color-scheme: light; }` rule at `base.css:89` must move into the
  token blocks or be keyed off `data-theme`).
- Audit `components.css` for hardcoded colors that bypass tokens (e.g. the
  `.app-dialog::backdrop` rgba at line 386, gradient buttons) — acceptable to
  leave gradients, but backgrounds/hover states must come from tokens.
- Toggle: an `.icon-btn` in `.header-actions` (`index.html`), showing ☾/☀.
  In `main.js`: on click flip `document.documentElement.dataset.theme`
  between `'dark'`/`'light'`, persist to `localStorage('theme')`. Apply
  before first paint with a tiny inline `<script>` in `<head>` (read
  localStorage, fall back to `prefers-color-scheme`) to avoid a flash of the
  wrong theme — this is the one justified inline script.
- Sweep every view in dark mode with seeded data: boards, chips, dialogs,
  login page (Track B), the folder card, portfolio filter row, focus rings.

---

## Cross-cutting

- **Order within tracks**: Track C changes `index.html` heavily; land C
  before D's portfolio work only if touching shared CSS; otherwise
  independent.
- Bump `?v=` query strings in `index.html` for changed CSS/JS files.
- Tests to run after each track: `.venv/bin/python -m pytest -q` (524 backend
  tests) and `.venv/bin/python run_frontend_tests.py` (413 front-end harness
  tests) — plus manual verification per track's Verify section, ideally with a
  browser screenshot pass at desktop and ~640px widths.
- `tests/test_api_contract.py` pins JSON shapes — Track B's `/api/me`
  addition is the only API-shape change; extend the test, don't weaken it.
- Nothing here should touch `models.py` — if an implementation seems to need
  a schema change, stop and reconsider (it doesn't).

## Decisions already made (don't re-litigate, but flag if they prove wrong)

1. **Assignee control is moved, not deleted** (C2): removing assignment
   entirely would strand steps in Not Assigned. The vertical space win is
   achieved by killing the form-row; assignment survives as a chip-sized
   control in the editor head.
2. **Portfolio "well page" = the existing detail shell** (D2), not a new
   route. It already supports full view/edit; building a second well page
   would duplicate it.
3. **Portfolio sort/filter is client-side** (D1): dataset is small and fully
   delivered; server-side would add API surface for nothing.
4. **Add Well removal keeps the backend path** (C7): the API/tests/seed
   script still create BP wells; only the UI panel goes.
5. **Login modal stays for mid-session 401s** (B4); the new page fronts the
   app only at boot.
