# Known Issues

These are confirmed defects found during review of the stage-aware Portfolio
navigation and cross-pipeline reference workflow. Each item should remain open
until its acceptance criteria and regression coverage are complete.

## KI-001: Reference view can open an editor that mutates inactive components

**Status:** STILL OPEN — re-verified against `asas-redesign` @ 07d1ce9 during the
final cross-workflow audit. Neither acceptance criterion is met:

- *Client half.* `static/js/views/detail.js:257` hides the control for LEAD
  views only — `byId('open-project-editor').classList.toggle('hidden', leadView)`.
  A reference view has `leadView === false`, so **Edit all project fields**
  is still rendered and clickable there, exactly as reported.
- *Backend half.* Task saves still do not enforce pipeline applicability. On a
  freshly created PROSPECT lead, writing to a BP-stage task succeeds:
  `PATCH /api/tasks/<id>` on "BP Execution Gate" (stage group `Well Delivery`)
  returns **200**, as does `PATCH /api/tasks/<id>/dynamic-fields`.

**Priority:** P1
**Affected files:** `static/index.html`, `static/js/views/detail.js`,
`static/js/views/project-editor.js`

The opposite pipeline is presented as reference-only, but **Edit all project
fields** remains available. The editor renders all project tasks and can save
inactive Prospect or Business Plan components because the task-save backend
does not enforce pipeline applicability.

### Reproduction

1. Open a record in its current pipeline.
2. Switch to the opposite pipeline reference view.
3. Click **Edit all project fields**.
4. Edit and save a component belonging to the inactive pipeline.

### Acceptance criteria

- A reference view provides no path that mutates inactive-pipeline components.
- Either hide/disable the all-fields action in reference mode or enforce task
  applicability in the editor and backend.
- Add regression coverage proving an inactive component cannot be changed from
  the reference workflow.

## KI-002: Reference-mode reset can enable the assignee control for employees

**Status:** RESOLVED (Card 1C) — closed with all three acceptance criteria met.
`setComponentReferenceMode` no longer sweeps `#assigned-to`; both it and
`renderAssigneeSelect` now call one `syncAssigneeGate(referenceOnly)` helper, so
whichever of the sync and async paths lands last still leaves the control in its
role-based state. Regression coverage: *"detail-form leaving reference mode does
NOT enable the assignee select for an employee"* in
`static/tests/test-lead-filters.js`, which drives the employee role through the
second (post-async) reference-mode call with `#assigned-to` deliberately placed
INSIDE `#component-form`.

**Priority:** P2
**Affected file:** `static/js/views/detail-form.js`

`setComponentReferenceMode(false)` enables every input, select and textarea.
Its second invocation runs after the asynchronous component-field request and
can override `renderAssigneeSelect()`, making the assignee dropdown interactive
for an employee. The backend rejects the assignment, leaving a dead and
unauthorized control in the UI.

### Reproduction

1. Sign in as an employee.
2. Open a component after using the cross-pipeline reference view.
3. Wait for the component fields and folder link to finish loading.
4. Observe that the assignee dropdown can become enabled; an attempted change
   then fails authorization.

### Acceptance criteria

- Leaving reference mode restores each control's role-based state.
- Reference-mode code does not indiscriminately enable controls it does not
  own.
- Add an employee-role regression test covering the asynchronous render path.

## KI-003: The all-fields Back action loses its originating pipeline context

**Status:** RESOLVED (Card 2A) — closed with all three acceptance criteria met.
`backToPortfolio` is now `backFromEditor` (`static/js/views/project-editor.js`):
it returns to the ORIGINATING record's detail view in that record's own
pipeline via `openDetail`, which is the acceptance criterion's preferred
destination — the editor is only ever opened from a record (the Lead Summary
gear's "Edit All Inputs" on a lead page, the rail's "Edit all project fields"
on a BP well page). Only a stateless editor with no selected record falls
through to Portfolio, and that fallback now calls `refreshPortfolio()` before
showing the tab, so a session that has never opened Portfolio can no longer
land on a table that was never fetched. The button's label follows the
destination ("Back to Lead" / "Back to Well"). Regression coverage: *"KI-003
the editor Back returns to the ORIGINATING record detail and pipeline"* and
*"KI-003 with no record selected, Back refreshes Portfolio before showing it"*
in `static/tests/test-navigation.js`, both driven from a FRESH fixture in which
Portfolio has never been loaded — the exact precondition of the report.

**Priority:** P2
**Affected files:** `static/js/views/project-editor.js`, `static/js/main.js`

The all-fields editor is now opened from pipeline detail, but its Back button
still navigates to Portfolio. If Portfolio has not been visited in the current
session, its data has not been fetched and the user can land on an empty table.
The action also discards the pipeline/detail context from which the editor was
opened.

### Reproduction

1. Load the application and remain on Prospect Maturation or Business Plan
   Execution.
2. Open a pipeline card and click **Edit all project fields**.
3. Click **Back to Portfolio** without saving.

### Acceptance criteria

- Prefer returning to the originating project detail and pipeline.
- If Portfolio remains the intended destination, refresh it before displaying
  the tab.
- Add a navigation regression covering an editor opened before Portfolio has
  ever been loaded.

## KI-004: A Seal CoS above 100% is storable and then 400s the detail endpoint

**Status:** OPEN — found by the final cross-workflow audit (`asas-redesign` @
07d1ce9) while auditing a freshly seeded database.

**Priority:** P1 — the affected lead's detail page cannot be opened at all, and
the only recovery is a write to a form the UI can no longer render.

**Affected files:** `cos.py` (`calculate_seal_cos`, `_seal_number`,
`_cos_probability`), `seed_dev.py:304`

The write path and the read path disagree about the domain of a Seal CoS input.
`cos.calculate_seal_cos` multiplies `seal_recent_activity_age` by
`seal_fracture_permeability` whenever the activity value exceeds 0.9, and
neither `_seal_number` (which validates each input) nor the function itself
range-checks the result — so an activity value above 1.0 yields a stored
`seal_cos_pct` greater than 100. At READ time `_cos_probability` (`cos.py:144`)
rejects exactly that value, and the read is the Total Chance of Success
recomputation that `GET /api/projects/<id>/detail` performs on every call.

The result is stored-data poisoning: a value the API accepted with 200 makes a
later, unrelated, read-only request fail permanently.

### Reproduction (entirely through the public API, no raw SQL)

1. Create a lead and save a Reservoir CoS row, e.g.
   `PATCH /api/tasks/<reservoir>/dynamic-fields` with
   `{"reservoir_cos_rows": "[{\"reservoir_cos_pct\": \"70\"}]"}` → **200**.
2. Save the merged CoS step:
   `PATCH /api/tasks/<trap and seal>/dynamic-fields` with
   `{"trap_cos_pct": "50", "seal_recent_activity_age": "1.33",`
   `"seal_fracture_permeability": "0.87", "seal_dip": "0.23",`
   `"seal_azimuth_vs_shmax": "0.52", "seal_fault_level_confidence": "0.59"}`
   → **200**, and `GET /api/tasks/<id>/dynamic-fields` now reports
   `seal_cos_pct = 116`.
3. `GET /api/projects/<id>/detail` → **400**
   `{"detail": "Seal CoS must be between 0 and 100%."}` — permanently.

`GET /api/projects`, `/api/portfolio/rows` and `/api/export/excel` all stay 200
(they never resolve Total CoS for that lead), so the board looks healthy and
only the detail page is dead.

### How it reaches a fresh developer database

`seed_dev.py:304` draws `seal_recent_activity_age` from
`random.uniform(0.1, 1.4)`. Any draw above 1.0 takes the `activity > 0.9`
branch and produces a percentage above 100, so roughly one seeded lead per run
is born with an un-openable detail page (observed: `CROX-1`, `seal_cos_pct` 116,
from activity 1.33 x permeability 0.87).

### Acceptance criteria

- One decision, applied consistently: either the SAVE rejects an out-of-domain
  Seal CoS input with the same message the read uses, or `calculate_seal_cos`
  clamps/normalizes its result so a read can never fail on a value the write
  accepted. A read-only endpoint must not be able to 400 on stored data.
- `seed_dev.py` must not generate inputs outside the domain the formula accepts.
- Regression coverage: a save that would produce >100% is handled at the write
  boundary, and `GET /api/projects/<id>/detail` stays 200 for every lead in a
  freshly seeded database.

## KI-005: Opening a lead rewrites its PIIP and reopens a grandfathered step

**Status:** OPEN — found by the final cross-workflow audit (`asas-redesign` @
07d1ce9). Reproducible against the repository seed database.

**Priority:** P1 — a passive page view mutates the record, its completion, its
audit trail and a headline KPI, with no user action and no Save.

**Affected files:** `static/js/views/lead-assessment.js` (the Card 2B auto-run),
`workflow/lifecycle.py` (`apply_field_completion`, the reopen branch)

Card 2B replaced the old Calculate / Apply-to-Lead buttons with an auto-run, and
the auto-run PERSISTS. Clicking a lead card is enough to fire it: the detail
shell auto-selects the lead's current task, and for any lead whose current step
is in Lead Assessment that renders the consolidated page, which immediately
issues

    POST  /api/tasks/<resource assessment>/resource-assessment
    PATCH /api/tasks/<resource assessment>/dynamic-fields

Two consequences, both observed on `ORYX-1` in `pipeline_tracker.db`:

1. **The saved assessment is silently overwritten.** `lead_piip_gas_mean` went
   from the stored `13.52` to a recomputed `146.2` — a value nobody asked for
   and nobody approved. That key is the first rung of `LATEST_MEAN_GAS_SOURCES`
   for a lead, so the board's **Total Mean OGIP** tile changes because somebody
   looked at a lead.
2. **A grandfathered Approved step is reopened.** `Resource Assessment` was
   Approved the manual way (assign → submit → approve) and carries no
   `polygons_surfaces_loaded` confirmation. The auto-run's write triggers
   `apply_field_completion`, the predicate reads "not met", and the step is
   driven **Approved → In Progress** with a `Field Reopen` history row. The lead
   dropped from 6/12 to 5/12, and the Lead Assessment sidebar counter from 3/4
   to 2/4, on a page view.

This contradicts the engine's own stated contract
(`workflow/lifecycle.py`, `apply_field_completion` docstring): *"The reopen
branch can only ever fire as a response to THE USER'S OWN SAVE OF THIS TASK …
Legacy steps that were Approved before these checkboxes existed are therefore
NEVER touched — not when the project is read."* The engine's reasoning is sound;
what broke it is a CLIENT that now issues a save the user never made. The
grandfather tests (`tests/test_field_completion.py:477`,
`tests/test_field_completion.py:508`) still pass because no test models the
auto-run.

**Blast radius on the seed database:** 63 Approved prospect steps across 19
leads currently fail their own field predicate and are therefore reopen-eligible
— `Resource Assessment` 15, `Seismic Signature Validation` 13, `Reservoir CoS`
12, `Trap and Seal CoS` 11, `GRV Inputs` 4, `Approval to Stake` 4,
`Well Site Location` 4. The 15 `Resource Assessment` rows are the ones a mere
page view can flip.

### Reproduction

1. Boot the app against a copy of `pipeline_tracker.db`.
2. `GET /api/projects/<ORYX-1>` — `Resource Assessment` reads `Completed`,
   the lead reads 6/12.
3. In a browser, click the `ORYX-1` card. Open nothing else, type nothing,
   save nothing.
4. `GET /api/projects/<ORYX-1>` — `Resource Assessment` now reads
   `In Progress`, the lead reads 5/12, and
   `GET /api/tasks/<id>/dynamic-fields` shows `lead_piip_gas_mean` replaced.

### Acceptance criteria

- Rendering a detail page performs no writes. The auto-run may COMPUTE and
  DISPLAY freely; persisting belongs to an explicit Save (or, at minimum, must
  be limited to filling a value that is genuinely absent, never to replacing a
  stored one).
- The reopen branch is grandfather-safe: a step Approved without the new
  confirmations is not reopened by a write the user did not make. Consider
  gating the reopen on the save actually having touched a key the predicate
  reads.
- Regression coverage on both halves: a test that opens a lead detail and
  asserts zero mutating requests, and an engine test that a field write which
  does not touch the predicate's keys leaves a grandfathered Approved step
  alone.
