# ASAS Business Plan Execution Final Delivery Report

## Outcome

- The approved Business Plan Execution implementation is complete on the local branch `codex/asas-business-plan-execution`.
- The dashboard is running at `http://127.0.0.1:8022/` against the real local `pipeline_tracker.db`.
- A read-only `git fetch origin --prune` was completed. `origin/asas-redesign` is still exactly `7286f54085297ddaea0b27f90de840c78c6d8a48`, so the handoff base is the latest GitHub state for that branch.
- Nothing was pushed, merged, rebased, or opened as a pull request.

## Branch and commits

- Base branch: `asas-redesign`
- Base commit: `7286f54085297ddaea0b27f90de840c78c6d8a48` (`Default step assignments at lead creation`)
- New local branch: `codex/asas-business-plan-execution`
- `3731f34` - `Implement Business Plan Execution domain`
- `f24318a` - `Build Business Plan Execution dashboard`
- This report is stored in a final documentation-only local commit; its resulting SHA is included in the final Codex response.

Pre-existing working-tree changes were preserved and excluded from these commits:

- `reporting.py`: pre-existing Portfolio population behavior change.
- `static/js/main.js`: pre-existing startup ordering change for `wire()` and `renderUserChip()`.
- `static/js/views/autosave.js` and `static/tests/test-autosave.js`: pre-existing working-tree/index line-ending state with no content diff.
- The untracked handoff directory, browser scratch directories, runtime logs, database files, backup database, and screenshots were not committed.

## Modified files

### Shared shell/components

- `static/css/components.css` - responsive Business Plan dashboard/detail styling and state treatments.
- `static/index.html` - approved Business Plan filter/KPI shell and module registration.
- `static/js/icons.js` - dashboard action and KPI icons.

### Main page/filters/cards

- `static/js/views/business-plan.js` - five filters, four KPI displays, stage columns, cards, details, state rendering, and auto-save coordination.
- `static/js/api.js` - Business Plan dashboard, detail, field, repeatable-row, assignment, and transition clients.
- `static/js/views/pipeline.js` - routes the Business Plan tab to the approved projection instead of the legacy board renderer.

### KPI services/calculations

- `workflow/business_plan.py` - one filtered well population, Rig Inventory, Rig Target, Success Rate, Actual/Simulated Mean OGIP, source precedence, rounding, and data-quality flags.

### Pre-Drilling

- `workflow/business_plan.py` - Gate, Well Letters, GHEER Inputs, classification defaults, validation, and approval rules.
- `static/js/views/business-plan.js` - compact Gate controls and combined Pre-Drilling detail screens.

### Post-Drilling

- `workflow/business_plan.py` - Quicklook formations/Pay Intervals, AAP, SAD Model, Summary Slides, and Learning approval behavior.
- `static/js/views/business-plan.js` - Post-Drilling forms, repeatable structures, file confirmations, and SAD presentation.

### Post-Testing

- `workflow/business_plan.py` - Flowback, SAD Model Update branching, Final Summary, Final Logs, Structural MTR, and PDA & Booking.
- `static/js/views/business-plan.js` - stable Flowback stage panels and every Post-Testing detail screen.

### Cross-step automation/Audit Trail

- `main.py` - Business Plan dashboard/detail/options/save/transition/assignment routes and error contracts.
- `workflow/business_plan.py` - reversible system rules, locks, correlation IDs, field/structure/progress/approval audit events, and unchanged-replay suppression.
- `workflow/__init__.py` - exports the Business Plan workflow service.

### Models/migrations

- `migrations.py` - schema migration v10 for unambiguous legacy Business Plan values.
- `workflow/constants.py` - exact approved Fluid vocabulary and canonical formation source.
- `workflow/formations.py` - compatibility for legacy direct-formation Fluid labels.

### Tests

- `tests/test_business_plan_execution.py` - service/API, KPI, role, automation, repeatable structure, reversal, and Audit Trail coverage.
- `tests/test_bootstrap.py` - v10 preservation, mapping, and replay/idempotence coverage.
- `static/tests/test-business-plan.js` - frontend rendering, auto-save, stale-response, focus, role action, and Flowback deletion/dropdown coverage.
- `static/tests/runner.html` - registers the Business Plan frontend tests.
- `run_frontend_tests.py` - cross-platform Edge/Chrome/Firefox browser discovery and result transport.
- `scripts/capture_bpe_screenshots.py` - reproducible desktop/mobile captures and overflow/error diagnostics.

### Documentation/configuration

- `config.py` - centralized year boundary, hole-section list, VSP URL, and Structural MTR URL configuration.
- `ASAS_BUSINESS_PLAN_EXECUTION_FINAL_REPORT.md` - this report.

## Implemented behavior

- Five filters: Assignee defaults to `All Assignees` and includes `Unassigned` plus real assignees; Field defaults to `All Fields` and uses the repository's well-name field convention; Status defaults to `All Status` and offers Completed, Pending Approval, and In Progress; Business Plan Year defaults dynamically to the current year and offers 1999 through 2035; Step defaults to Business Plan Gate and offers All Steps plus the exact 18 tracking items. Active filters use AND semantics, retain each other's selections, and feed cards, counts, and KPIs from one server query.
- Four KPIs: Rig Inventory sums saved Actual Drilling Days only for filtered supervisor-approved Gates; Rig Target sums saved Actual Drilling Days for every filtered well; Success Rate uses any Productive Quicklook Pay Interval and rounds half-up to a whole percent with a zero-denominator result of zero; Total Mean OGIP sums before half-up whole-BCF rounding and displays Actual/Simulated, with zero retained as a real update value. Empty populations return `0 Days`, `0 Days`, `0%`, and `0/0 BCF`.
- Tracking structure: Pre-Drilling, Post-Drilling, and Post-Testing each expose exactly six ordered card items. A well is projected into its first incomplete stage, and progress is completed items out of six for that current card stage. Fourteen approved detail pages map onto the existing stable persisted tasks.
- Auto-save: there is no Save Updates action. Scalar edits are debounced and serialized; repeatable formations, Pay Intervals, and Flowback stages use stable draft identity. Context-bound drafts, stale-response overlays, focus restoration, retry, navigation flushes, and explicit zero-row Flowback state prevent lost or resurrected edits.
- Approval paths: employees can submit eligible approval steps. Supervisors can Return, Approve, and Reopen, with the approved action ordering. Pending and approved source fields lock until Return/Reopen. SAD branch-changing source edits are blocked while SAD Model Update is Pending Approval or Approved.
- Classification automation: Development applies Optimized Standard B, SWC 0, Pressure Points 3, Fluid Samples 3, and Coring No; Appraisal/Exploration apply Standard A, SWC 30, Pressure Points 20, Fluid Samples 5, and Coring No. Confirmed classification changes reset only classification-driven defaults. Development system-completes Well Proposal and checks PDA, but never answers the normal-path booking question.
- Fluid rule: every Quicklook Pay Interval participates. Any Gas, Gas over Water, Oil, Oil over Gas, or Oil over Water interval makes the well successful. Only a complete set of Water Bearing/Dry Hole intervals activates the non-productive cascade. The cascade gray-completes/locks the approved dependent steps, auto-answers Booking No, preserves prior manual booking values, and restores them when the Fluid outcome reverses.
- Flowback: stages support add, edit, and delete with stable IDs; Formation is always a canonical dropdown, including when blank. Required completion fields are Formation, Top MD, Base MD, Choke Size, FWHP, and the shared-folder confirmation. Optional Dynamic Area/OGIP remain optional. Deliberately deleting the final stage persists zero stages rather than recreating one after reload.
- SAD Model Update: Water Bearing/Dry Hole bypasses and gray-completes. Exactly one stage with both comparison values uses the approved OR threshold: above SAD B90 Area or SAD Mean OGIP requires manual update and approval; both at/below copy all SAD values, lock, and gray-complete. Copy mode backs up manual update values and restores them on reversal. Blank comparison values or multiple stages remain explicit, preserved, non-destructive unresolved comparison states.
- Mean OGIP: Simulated uses Pre-Drilling GeoX Assessment first and the legacy Pre-Drilling Resource Assessment fallback. Actual uses SAD Model Update when the field exists, including stored zero, then SAD Model; only wells successful by the Quicklook Fluid rule contribute. Missing simulated values and nonzero actual values on unsuccessful wells are returned as data-quality project IDs.
- PDA & Booking: completion requires the PDA checkbox plus No, or Yes with one of the current/following three years (with a retained historical value still displayed). Development auto-checks PDA. Development + No is gray; Development + Yes/year is green. Appraisal/Exploration require manual PDA and complete green. All-water/dry auto-No is locked without inventing a classification answer.
- Audit Trail: scalar edits, classification defaults, supervisor TD override provenance, assignment, formation/Pay Interval/Flowback add-update-remove events, approval transitions, effective tracking-item progress, SAD branch changes, Fluid cascade/reversal, and system copies share correlation/provenance metadata. Unchanged replays do not emit duplicate events.

## Data and migrations

- Migration order remains append-only: v9 project priority is followed by v10 `_migrate_v10_business_plan_execution`; `LATEST_SCHEMA_VERSION` is 10.
- V10 is data-only and uses existing stable project/task/dynamic-field/formation identifiers. It maps only exact `Dry` to `Dry Hole` and `Water` to `Water Bearing`, splits a stored combined AAP confirmation into both exact confirmations, merges old Flowback confirmations as true only when both were true, and upgrades Flowback rows with stable IDs and per-stage fields.
- Legacy global Dynamic Area/OGIP values move into a stage only when exactly one legacy stage exists. Multi-stage values are not guessed.
- Existing target fields win through insert-if-absent behavior. Ambiguous `Condensate`, `Liquid`, PDA `Booked`, partial file confirmations, malformed JSON, and unrelated values remain untouched.
- The migration runs transactionally through the existing bootstrap runner. The v10 test snapshots all affected rows, replays bootstrap, and proves an identical second result. Fresh-schema and existing-schema paths remain covered by the full suite.
- A pre-migration backup, `pipeline_tracker.pre_bpe_20260803.db`, was created locally and kept ignored. The live database is at schema v10 and is currently serving the dashboard.
- No mock operational well, person, field, status, year, KPI total, or image value was introduced. Screenshot state changes were made only in a copied temporary database, never in `pipeline_tracker.db`.

## Test results

### Passing tests

- Pre-change baseline: `python -m pytest -q` produced 641 passed, 6 failed, and 597 warnings before implementation. The duration line was not retained; the six failures are listed below.
- Focused service/API: `.\.venv\Scripts\python.exe -m pytest tests\test_business_plan_execution.py -q` produced 10 passed, 10 warnings in 2.12s.
- Migration v10: `.\.venv\Scripts\python.exe -m pytest tests\test_bootstrap.py -k migration_v10 -q` produced 1 passed, 35 deselected, 1 warning in 1.49s.
- Combined focused regression: `.\.venv\Scripts\python.exe -m pytest tests\test_business_plan_execution.py tests\test_bootstrap.py -k "business_plan or migration_v9 or migration_v10" -q` produced 12 passed, 34 deselected, 12 warnings in 2.41s.
- Browser component/integration: `.\.venv\Scripts\python.exe run_frontend_tests.py` produced 544 passed, 0 failed, 0 skipped; authoritative Edge driver wall time was 18.5s.
- Full backend regression: `.\.venv\Scripts\python.exe -m pytest -q` produced 652 passed, 6 failed, and 608 warnings in 65.86s.
- Syntax/build check: `.\.venv\Scripts\python.exe -m py_compile main.py migrations.py workflow\business_plan.py` passed in 0.7s.
- Whitespace checks: `git diff --cached --check` passed for each scoped implementation commit.

### Pre-existing failures reproduced before changes

- `tests/test_api_contract.py::test_component_folder_uses_leads_for_prospect_steps_and_wells_for_bp_steps` - existing Windows path-separator expectation.
- `tests/test_api_contract.py::test_section_folder_returns_resolved_link_for_a_known_section` - existing Windows path-separator expectation.
- `tests/test_api_contract.py::test_export_includes_proposed_leads_with_latest_estimates` - reproduced with the preserved `reporting.py` user change.
- `tests/test_portfolio.py::test_portfolio_scope_is_bp_enabled_only` - reproduced with the preserved `reporting.py` user change.
- `tests/test_portfolio.py::test_portfolio_membership_includes_bp_wells_and_mature_leads` - reproduced with the preserved `reporting.py` user change.
- `tests/test_promotion.py::test_recall_of_non_mature_lead_returns_it_unchanged` - reproduced with the preserved `reporting.py` user change.

### Newly introduced failures

- Zero. The same six failures appeared before and after the implementation; passing tests increased from 641 to 652.

### Environment-limited and manual checks

- A sandboxed browser run rendered the tests but could not post its loopback result beacon. The exact driver was rerun with local browser permission and returned the authoritative 544/544 result.
- The remaining manual inputs are business decisions listed in Open items, not unverified implementation behavior.

## Runtime verification

- Startup command: `.\.venv\Scripts\python.exe -m flask --app main run --host 127.0.0.1 --port 8022`
- Running URL: `http://127.0.0.1:8022/`
- Health endpoint returns HTTP 200 and identifies the real database as `Segmentation-Dashboard\pipeline_tracker.db`.
- A read-only real-data dashboard request for 2027 returned one well, `WWWW-44` (project 1), in Pre-Drilling with six tracking items. The Business Plan Execution Gate detail route returned three real Formation options.
- Routes opened and verified: `/`, `/api/health`, `/api/business-plan/dashboard`, and `/api/business-plan/wells/1/steps/business-plan-gate`. The isolated browser run also opened Gate, Well Letters, Quicklook Logs, Flowback Results, and Post-Drill Learning Review.
- Assignee, Field, Status, Year, and Step dropdowns were opened/captured; combined filter behavior is covered by service and frontend tests.
- Auto-save persistence was exercised by adding/deleting Flowback stages through the real UI/API against a copied database, waiting for `Saved`, then opening a fresh mobile page and observing the persisted stage count. Scalar sequencing, reload persistence, stale responses, and retry are covered by frontend tests.
- Employee Submit and Supervisor Return/Approve/Reopen permissions and transitions were exercised in API tests. The pending/approved supervisor states were also captured from the isolated runtime; no role claim is based on an unperformed production-data click.
- Development defaults, Water/Dry cascade and reversal, SAD manual/copy/unresolved branches, booking preservation, zero-value OGIP precedence, and audit correlation/idempotence were exercised by integration tests.
- All 12 delivery captures reported no page errors, no console errors, no incoherent clipping, and no horizontal overflow. Server requests used for verification returned HTTP 200 with no application traceback.

## Screenshots

- [Business Plan Execution default desktop](screenshots/business-plan-execution/bpe-dashboard-default-desktop.png)
- [Business Plan Execution dropdowns desktop](screenshots/business-plan-execution/bpe-dashboard-dropdowns-desktop.png)
- [Business Plan Execution Gate desktop](screenshots/business-plan-execution/bpe-gate-desktop.png)
- [Development system-completed Well Letters desktop](screenshots/business-plan-execution/bpe-system-gray-well-letters-desktop.png)
- [Post-Drilling Quicklook Logs desktop](screenshots/business-plan-execution/bpe-post-drilling-quicklook-desktop.png)
- [Flowback one stage desktop](screenshots/business-plan-execution/bpe-flowback-one-stage-desktop.png)
- [Flowback two stages desktop](screenshots/business-plan-execution/bpe-flowback-two-stages-desktop.png)
- [Pending Approval desktop](screenshots/business-plan-execution/bpe-pending-approval-desktop.png)
- [Green completion desktop](screenshots/business-plan-execution/bpe-green-completion-desktop.png)
- [Dashboard narrow viewport](screenshots/business-plan-execution/bpe-dashboard-mobile.png)
- [Gate narrow viewport](screenshots/business-plan-execution/bpe-gate-mobile.png)
- [Flowback narrow viewport](screenshots/business-plan-execution/bpe-flowback-mobile.png)

## Open items

1. **Ordered hole sections.** No authoritative list exists in the inspected schema/config. Formation options use the existing canonical `workflow.constants.FORMATIONS`; additional hole sections come only from `SEGMENT_TRACKER_BPE_HOLE_SECTIONS` through `config.BPE_HOLE_SECTIONS`. No petroleum list or order was invented. Needed later: the approved ordered hole-section values.
2. **Calculated Business Plan TD.** No authoritative repository equation/source transformation was found. The calculated field is locked, blank blocks submission, and only a Supervisor can provide an override with a required reason and audited provenance. Bulk Registration and Actual TD are not substituted. Needed later: source fields and the approved equation/transformation.
3. **Calculated Drilling Days and rounding.** No authoritative equation or rounding rule was found. The calculated field is locked and explicitly awaits configuration; direct writes are rejected. Actual Drilling Days remains editable and is the only KPI source, so no assumed rate, duration, or rounding was introduced. Needed later: the equation, dependencies, and display/rounding rule.
4. **Blank or multi-stage Flowback comparison.** No authoritative fallback or aggregation service was found. Blank values are not zero, multiple stages remain distinct, and both cases produce `unresolved_comparison` without copying or forcing manual update; Flowback completion remains independent. Water/Dry precedence and one-stage complete comparisons are implemented. Needed later: blank policy and the well-level multi-stage selection/aggregation rule.
5. **VSP destination.** No verified URL was found. The optional link reads `SEGMENT_TRACKER_BPE_VSP_URL`; blank configuration renders no fabricated destination and never affects progress. Needed later: the real URL.
6. **Structural MTR destination.** No verified URL was found. The link reads `SEGMENT_TRACKER_BPE_STRUCTURAL_MTR_URL`; completion remains checkbox/automation based. Needed later: the real URL.
7. **Years after 2035.** The approved fixed 1999-2035 options are centralized as `BPE_YEAR_MIN`/`BPE_YEAR_MAX`; out-of-range stored years are surfaced as a data notice rather than silently changed. Needed later, before 2036: extend, clamp, or replace the fixed policy.
8. **Step-filter tracking-history scope.** The inspected dashboard card contract exposes the six current-stage items and no retained-history filter service. That existing current-stage card scope is retained and explicitly returned as `scope: current-stage tracking items`; all independent AND/Status logic is complete. No all-history store was invented. Needed later: confirmation that filters should remain current-stage-only, or a precise retained-history population contract.

No newly discovered product ambiguity remains outside those eight declared items.

## Assumptions

- The repository's established `FIELD-WELL` parsing convention remains the authoritative Field source because the project schema has no independent Field column/master. This matches `folders.parse_field_and_well` and existing reporting/project behavior.
- Existing stable task names and IDs remain the storage contract; the approved 14-page/18-item experience is a projection over those records, as required by the handoff.
- No formula, URL, Flowback aggregation, or year extension was assumed.

## Final safety confirmation

- The implementation is on the new local branch `codex/asas-business-plan-execution`.
- User and unrelated working-tree changes were preserved and excluded from all delivery commits.
- No destructive Git operation occurred.
- No remote push, merge, rebase, or pull request occurred.
- No secrets, private operational exports, database files, backups, screenshots, browser profiles, or transient logs were committed.
- Unresolved business rules were isolated and reported; none was guessed.
