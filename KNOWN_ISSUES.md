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

**Status:** RESOLVED — fixed on `asas-redesign` after the final cross-workflow
audit. See "Resolution" below; the report of the defect is kept verbatim as the
record of what was wrong.

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

### Resolution

Two layers, because the defect is a DISAGREEMENT between a write and a read and
each half has to be answered on its own terms. The formula is deliberately
unchanged — clamping it would turn a mis-entry into a plausible 100%, and
changing a CoS formula is CONTRIBUTING recipe 3's own procedure, not a bug fix's.

1. **The save refuses.** `workflow/lifecycle.py::_guard_seal_cos_range`, called
   from `_apply_seal_cos_calculation` (the single hook both `save_task` and
   `save_task_dynamic_fields` run), rejects a recomputed Seal CoS outside
   `[0, 100]` with a `ValueError` naming the value and the inputs the offending
   branch multiplied: *"Seal CoS computes to 116% from these inputs; adjust Most
   recent age of activity or Fracture Permeability."* → HTTP 400. No partial
   write: `save_task_dynamic_fields` runs the hook before it opens its
   transaction, and `save_task` runs it inside one, before the first DML
   statement. 100.0 exactly is accepted — the boundary is inclusive, matching
   `_cos_probability`. The Trap and Reservoir hooks need no equivalent: their
   results come from a fixed score table and a model probability respectively,
   so they cannot leave the domain by construction.
2. **The read degrades.** `workflow/summary.py::total_cos_from_fields` catches
   the `ValueError` `cos.calculate_presence_cos` raises on an already-stored
   out-of-domain input, logs one warning through the module logger, and returns
   `""` — the Total reads as unavailable (the UI's em-dash) instead of failing
   the request. Every read-only surface that resolves a Total goes through that
   one function, so `GET /api/projects/<id>/detail`, the board, the portfolio
   rows and the Excel export all degrade identically. The offending
   `seal_cos_pct` is still returned in `/detail`'s `fields`, so the user can see
   what to fix; repairing it is a write, and the write is where it is refused.
3. **The seed stops producing them.** `seed_dev.py::_seal_fields` draws
   `seal_recent_activity_age` from `random.uniform(0.1, 1.0)` (was `0.1, 1.4`).
   Above 0.9 the formula takes the `activity x fracture_permeability` branch, so
   the old ceiling times the 0.9 permeability ceiling was 126%; the new one is
   90%, and ~11% of draws still exercise that branch.

**Covering tests** (`tests/test_cos.py`):
`test_seal_cos_above_100_is_refused_by_the_dynamic_fields_save`,
`test_seal_cos_above_100_is_refused_by_the_full_save_too`,
`test_refused_seal_cos_save_writes_nothing_at_all`,
`test_seal_cos_of_exactly_100_is_accepted`,
`test_seal_guard_names_the_average_branch_inputs_when_that_branch_overflows`,
`test_trap_and_reservoir_cos_cannot_leave_the_cos_domain`,
`test_detail_endpoint_survives_a_poisoned_seal_cos` (poisons the row with raw
SQL, exactly as a pre-guard legacy row would look, and asserts 200 +
`derisking == ""`), `test_poisoned_seal_cos_degrades_everywhere_the_total_is_read`.

## KI-005: Opening a lead rewrites its PIIP and reopens a grandfathered step

**Status:** RESOLVED — fixed on `asas-redesign` after the final cross-workflow
audit. See "Resolution" below; the report of the defect is kept verbatim as the
record of what was wrong.

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

### Resolution

The engine's reasoning was never wrong — "the reopen branch can only ever fire
as a response to THE USER'S OWN SAVE OF THIS TASK" is the right rule, and
`workflow/lifecycle.py` is unchanged. What broke it was a CLIENT issuing a save
the user never made, so that is what was fixed.

**The interaction gate** (`static/js/views/lead-assessment.js`). Card 2B's
contract is that "PIIP results and plots update automatically when valid inputs
or the SELECTED SCENARIO CHANGE". MOUNTING is neither, so the auto-run no longer
fires on it:

- `renderLeadAssessment` no longer ends in `scheduleCalculation(0)`. A mount is
  a READ: the stored `lead_piip_*` values are already on screen (rendered by
  `workspaceMarkup` through `resultsFromStoredFields`), and the only mount-time
  work left is `renderMountStatus`, which runs the PURE `resolveCalculation` to
  show the idle/error hint. Zero requests, zero writes.
- `state.userDirty` starts false and is set by exactly two DOM event handlers:
  `onFieldInput` (an `input`/`change` on a Section 1/2 field) and the scenario
  radio's `change` listener, both via `markUserEdit`. Hydration cannot set it —
  `renderLeadAssessment` bakes stored values into the markup's `value=`
  attributes, `rerenderThicknessSection` rebuilds with `outerHTML`, and
  `syncDerivedInputs` assigns `input.value` directly; none of the three
  dispatches an event.
- `scheduleCalculation` returns early without arming the timer while
  `userDirty` is false, and `runCalculation` re-checks the flag at FIRE time, so
  no future caller can arm the debounce around the gate.

`persistResults` is otherwise untouched: a genuine edit still recomputes and
still persists, so the Resource Assessment item still completes itself.

**Covering tests.** Front-end harness
(`static/tests/test-lead-assessment.js`, fetch stubbed, calls counted):
`MOUNTING an assessed lead is a READ — zero requests, stored results shown`,
`mounting a lead with valid inputs but NO stored result still writes nothing`,
`the user's first edit arms the auto-run — exactly one debounced run`,
`a scenario click is the OTHER genuine interaction`. End-to-end
(`scripts/e2e_card_2b.py`, step 8, real browser + real server): with the lead's
Resource Assessment saved and Approved, both the audit's own repro (click the
card, touch nothing) and the stronger case (mount the consolidated page and idle
past five debounce windows) leave the stored `lead_piip_gas_mean`, the Approved
status and the history row count byte-identical, with no `Field Reopen` event —
and a subsequent real edit still recomputes and persists.
