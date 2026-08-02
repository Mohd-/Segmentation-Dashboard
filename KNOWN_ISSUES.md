# Known Issues

These are confirmed defects found during review of the stage-aware Portfolio
navigation and cross-pipeline reference workflow. Each item should remain open
until its acceptance criteria and regression coverage are complete.

## KI-001: Reference view can open an editor that mutates inactive components

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
