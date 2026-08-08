# Merge Plan — codex/asas-business-plan-execution → asas-redesign

Status: reviewed 2026-08-04. Backend domain review, frontend/style review, and a full
backend test run on this machine (Linux) are the evidence base.

## Ground truth

- The branch is based exactly on the current `asas-redesign` tip (`7286f54`), so git-wise
  this is a **fast-forward with zero textual conflicts**. All conflicts are semantic:
  theme drift, duplicated infrastructure, vocabulary changes, and one bad commit.
- Verified on this machine: merge base passes **647/647** backend tests; the branch tip
  fails **4** (`test_portfolio.py` ×2, `test_promotion.py` ×1, `test_api_contract.py`
  export ×1). All 4 are caused by the `reporting.py` hunk in the final commit `a7648e6`
  — they are branch-introduced regressions, not "pre-existing" as the branch report claims.
  Everything else (655 tests) is green; `py_compile` clean; Python 3.9-compatible; no new
  third-party deps.

## Merge mechanics

```
git checkout -b bpe-integration asas-redesign
git merge --no-ff 0e31191        # everything EXCEPT a7648e6 — clean (fast-forward content)
# ... fix commits (Phase 1) ...
# verify (Phase 3), then merge bpe-integration into asas-redesign
```

`a7648e6 "Save local Portfolio and startup updates"` is **excluded entirely**. It contains
only (a) the `reporting.py` portfolio-scope removal that breaks the 4 tests and is
referenced nowhere by the BPE feature, and (b) a `main.js` boot-order change
(`wire()`/`renderUserChip()` moved first). Both were pre-existing local working-tree edits
on the author's machine that got committed at the end — see Decision points 1–2.

Before first boot after merge: back up the local DB
(`cp pipeline_tracker.db pipeline_tracker.pre_bpe.db`). Migration v10 is append-only,
data-only, transactional, and replay-tested, but it writes real rows.

## Phase 1 — Blocking fixes (on bpe-integration, before it reaches asas-redesign)

### Backend

1. **Fluid vocabulary fan-out** (the biggest real conflict). The branch renames fluids in
   `workflow/constants.py:113` (`Dry`→`Dry Hole`, `Water`→`Water Bearing`, adds
   `Gas over Water` etc.) but leaves the other copies untouched, so legacy writers undo
   migration v10 at runtime and the non-prospective cascade goes dead for new data:
   - `static/js/schema.js:62` `FLUID_TYPES` — still offers retired labels.
   - `static/js/schema.js:70` `FLOWBACK_RATE_FIELDS` — keyed by old strings; an Oil well
     falls back to the Gas/MMSCFD unit (`detail.js:1073`).
   - `detail.js:684` `if (fluid === 'Dry')` — "tight" flag never fires for `Dry Hole`.
   - `constants.py:291` `NON_PROSPECTIVE_FLUIDS = {"water","dry"}` — extend to new labels.
   - `import_excel.py:92` `_FLUID_CANONICAL` and `seed_dev.py:115` — write retired labels.
   - `workflow/formations.py:165` back-compat aliases map `water`/`dry` **backward** to the
     retired labels — invert to map forward to `Water Bearing`/`Dry Hole`.
   - BPE's strict validator (`business_plan.py` `_clean_formation_payload`/`_fluid_state`)
     rejects legacy `Condensate`/`Liquid` on save while treating them as "incomplete" —
     which blocks SAD Model approval with no way to fix through the BPE editor. The editor
     must allow replacing a legacy value even if it won't accept writing one.
   - Update affected tests: `static/tests/test-schema.js:194`, `tests/test_formations.py:279`,
     `tests/test_import.py:647`.

2. **`reopen` exposed over HTTP.** `asas-redesign` deliberately keeps `reopen` out of
   `TASK_TRANSITIONS` (`constants.py:857`) so no HTTP caller can reach it; the BPE
   transition route reintroduces it with no route-level gate. Add
   `require_role('supervisor')` on the transition route and route reopen through a
   sanctioned, audited path.

3. **`completed_at` desync.** BPE transitions bypass `lifecycle.transition_task`, so
   `_sync_completed_at` and `notify_transition` never run for BP wells; `promotion.py:144`
   depends on `completed_at`. Either route BPE approve/return through lifecycle or call
   `_sync_completed_at` + notifications from the BPE service.

4. **Gate approval dead-end.** `bp_gate_calculated_drilling_days` is required for Gate
   approval but every write to it is rejected and nothing computes it — the BP Gate can
   never be approved as shipped. Drop it from the required set (or allow the supervisor
   override path) until the equation is configured (branch open item #3).

### Frontend

5. **BP filter ownership** (visible bug: wrong option sets flash on first BP-tab open, and
   an async race lets `ensureUsers()` clobber the assignee select). Delete the two
   `fillSelect(bp-status-filter …)` / `fillSelect(bp-year-filter …)` lines in `main.js
   boot()`, and move the three `wire()` filter bindings + assignee fill ownership into
   `business-plan.js initialize()`, which already populates all 5 filters.

6. **Undefined CSS variables** — 3 rules currently render with no color at all:
   `var(--gray-400)` (doesn't exist; use `--gray-300` for the border, `--text-faint` for
   the icon) and `var(--warning-600)` (use `--warning-500`).

7. **Dark-mode breakage from hardcoded colors**: `.bpe-copy-button`
   (`#b9e2e8/#e6f7f9/#164e63`) → `var(--border)/var(--surface-sunken)/var(--text-muted)`
   (or reuse `.icon-btn`); `.bpe-folder-icon` (`#d7a600/#f5cf52`) →
   `var(--warning-500)/var(--warning-100)`.

8. **Summary gear menu**: add the `:root[data-theme="dark"] .bpe-summary-menu` border
   override (mirroring `.lf-menu`) and outside-click + Escape dismissal (regression vs.
   `.ls-menu` behavior).

9. **Cache-busting after fixes**: re-bump `components.css?v=42→43` and `main.js?v=39→40`
   (the branch already bumped past ours; our fix commits bump again).

## Phase 2 — Strongly recommended fast-follows (can land right after merge)

- **Optimistic locking**: BPE writes bump `revision` without checking `expected_revision`
  (`StaleRevisionError` is imported and never used), while the Component editor guards the
  same `project_tasks` rows — concurrent edits silently clobber. Thread
  `expected_revision` through the BPE routes → 409, like `lifecycle.py` does.
- Rename env vars `ASAS_BPE_*` → `SEGMENT_TRACKER_BPE_*` (repo convention; do it before
  anyone configures the old names).
- Replace 4 `window.confirm()` calls in `business-plan.js` with `dialog.js confirmDialog`.
- Well Summary presentation alignment: em-dash (`EM_DASH`/`isFilled`) for empty values
  instead of `'-'`, `progressPercent()` instead of the hardcoded `/6`.
- Dashboard N+1: `get_dashboard` issues ~4 queries per project in a loop; batch like
  `projects.py`/`summary.py` readers.

## Phase 3 — Deferred cleanups (tracked, not gating)

- CSS token conformance: off-token radii/spacing/font-sizes; retarget new breakpoints
  1260/900/600 to the app's 1100/840/640; KPI colors via modifier classes instead of
  `:nth-child`.
- Dedupe reimplemented components: `.bpe-well-card`≈`.lead-card`, `.bpe-stage`≈
  `.lead-column`, `.bpe-progress`≈`.ls-progress-track`, `.bpe-kpi`≈Card 1E tiles.
- Autosave dedupe: `business-plan.js` fully reimplements `views/autosave.js`
  (debounce/serialize/context-guard/focus-restore). They can't collide at runtime
  (different DOM roots, autosave.js is prospect-only), so extract the shared core later.
- Duplicated service logic: `_set_field` vs `lifecycle._apply_dynamic_fields`,
  `assign_detail` vs `lifecycle.assign_task`, `save_formations` vs
  `formations.upsert_project_formations` (same phases, divergent validation + audit names).
- Prune now-dead legacy BP board paths in `pipeline.js`; header comment for
  `test-business-plan.js`.

## Phase 4 — Verification gate (must pass before bpe-integration → asas-redesign)

1. `python -m pytest -q` → **0 failures** (the 4 regressions disappear with `a7648e6`
   excluded; new fluid-fan-out tests green).
2. Frontend suite: the branch's rewritten `run_frontend_tests.py` is now cross-platform —
   try `python run_frontend_tests.py --browser chromium` on this machine (headless Firefox
   is known to wedge here; the runner now has a 60s hard timeout either way). Expect 544+.
3. Manual smoke, both themes: BP tab first-open (no filter flash), promote from Portfolio
   → lands in BPE for the chosen year, gear menu dismissal, Flowback add/edit/delete,
   dark-mode pass over every new .bpe-* surface.

## Decision points (user)

1. **`reporting.py` portfolio-scope change** (in excluded `a7648e6`): removes the
   BP-wells + matured-leads membership gate so Portfolio/Staking export shows every
   non-archived record. It breaks 4 tests and is unused by BPE. It looks like a local
   experiment that got swept in — confirm it should stay out (default: out).
2. **`main.js` boot reorder** (`wire()`/`renderUserChip()` first) — also from `a7648e6`,
   also pre-existing local edit. Default: out; say if it was intentional.
3. **Legacy `Condensate`/`Liquid` intervals**: v10 deliberately doesn't map them and BPE
   treats them as blocking-incomplete. What should they become (business call)?
4. **Year policy**: promotion range changed 2026–2040 → 1999–2035. Rest of the repo still
   assumes a 2040 ceiling (`reporting.py:442`, `import_excel.py:352`) and `import_excel`
   classifies pre-current years as `historical`. Confirm 1999–2035 is the intended policy.
5. The branch's own 8 open items (TD equation, drilling-days equation, hole sections,
   VSP/MTR URLs, >2035 years, flowback comparison policy, step-filter history scope)
   remain open — none block the merge.

## What we keep unreservedly

The core deliverables are sound and worth taking as-is: migration v10 (append-only,
idempotent, replay-tested), the `workflow/business_plan.py` pipeline restructuring
(task-name mapping against `PIPELINE_TEMPLATES` is exact), the new BP board
(5 filters + 4 KPIs + stage columns + 14 detail pages), the additive `api.js` client,
additive icons, the append-only CSS block (zero existing selectors touched), the
conformant frontend test suite, and the cross-platform `run_frontend_tests.py` rewrite
(which likely fixes our Linux harness gap).
