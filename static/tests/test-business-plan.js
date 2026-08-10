import { test, assert, fixture, mockFetch, waitFor } from './harness.js';
import {
  refreshBusinessPlan, syncBusinessPlanPromotion, openBusinessPlanDetail, businessPlanTestHooks
} from '../js/views/business-plan.js';

function response(payload) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'Content-Type': 'application/json' }
  });
}

function stageItems() {
  return [
    ['business-plan-gate', 'Business Plan Gate', 'business-plan-gate'],
    ['well-proposal', 'Well Proposal', 'well-letters'],
    ['site-preparation', 'Site Preparation', 'well-letters'],
    ['approval-to-drill', 'Approval to Drill', 'well-letters'],
    ['gheer-geophysics', 'GHEER: Geophysics', 'gheer-inputs'],
    ['gheer-geomechanics', 'GHEER: Geomechanics', 'gheer-inputs']
  ].map(function (row, index) {
    return {
      key: row[0], label: row[1], detail_slug: row[2],
      status: index === 1 ? 'Completed' : 'In Progress',
      color: index === 1 ? 'gray' : 'empty', source: index === 1 ? 'system' : 'manual',
      locked: index === 1
    };
  });
}

// Each navigation entry carries its own rolled-up status (workflow/
// business_plan.py _navigation), which is what tints the rail badges.
function navigation() {
  return [
    { stage_key: 'pre_drilling', stage_label: 'Pre-Drilling', details: [
      { slug: 'business-plan-gate', label: 'Business Plan Execution Gate', status: 'In Progress' },
      { slug: 'well-letters', label: 'Well Letters', status: 'Pending Approval' },
      { slug: 'gheer-inputs', label: 'GHEER Inputs', status: 'Completed' }
    ] },
    { stage_key: 'post_drilling', stage_label: 'Post-Drilling', details: [
      { slug: 'quicklook-logs', label: 'Quicklook Logs', status: 'In Progress' },
      { slug: 'aramco-approved-pics', label: 'Aramco Approved Picks', status: 'In Progress' },
      { slug: 'sad-model', label: 'SAD Model', status: 'In Progress' },
      { slug: 'summary-slides', label: 'Summary Slides', status: 'In Progress' },
      { slug: 'post-drill-learning-review', label: 'Post-Drill Learning Review', status: 'In Progress' }
    ] },
    { stage_key: 'post_testing', stage_label: 'Post-Testing', details: [
      { slug: 'flowback-results', label: 'Flowback Results', status: 'In Progress' },
      { slug: 'sad-model-update', label: 'SAD Model Update', status: 'In Progress' },
      { slug: 'final-summary-slides', label: 'Final Summary Slides', status: 'In Progress' },
      { slug: 'final-log-analysis', label: 'Final Log Analysis', status: 'In Progress' },
      { slug: 'structural-mtr', label: 'Structural MTR', status: 'In Progress' },
      { slug: 'pda-booking', label: 'Post-Drilling Analysis & Reserves Booking', status: 'In Progress' }
    ] }
  ];
}

test('business-plan renders the approved dashboard and one auto-save approval detail shell', async function () {
  // The band as index.html builds it: the five selects are the hidden STATE
  // STORE, #bpe-filter-row holds the visible triggers, #bpe-kpis the tiles.
  var host = fixture(
    '<div id="bpe-main-view" class="panel pipeline-panel"><div class="lead-controls lead-controls-bp">' +
      '<select id="bp-assignee-filter" hidden></select>' +
      '<select id="bp-field-filter" hidden></select>' +
      '<select id="bp-status-filter" hidden></select>' +
      '<select id="bp-year-filter" hidden></select>' +
      '<select id="bp-step-filter" hidden></select>' +
      '<div id="bpe-filter-row" class="lead-filter-row bpe-filter-row"></div>' +
      '<div id="bpe-kpis" class="lead-kpi-row lead-kpi-row-bp"></div></div>' +
      '<div id="bpe-data-notice" class="hidden"></div>' +
      '<div id="bp-pipeline" class="pipeline-board lead-board"></div></div>' +
      '<section id="bpe-detail-view" class="hidden"></section>'
  );
  var currentYear = new Date().getFullYear();
  var years = [];
  for (var year = 1999; year <= 2035; year += 1) years.push(year);
  var items = stageItems();
  var dashboard = {
    role: 'supervisor',
    options: {
      assignees: ['All Assignees', 'Unassigned', 'Supervisor'],
      fields: ['All Fields', 'MDFT'],
      statuses: ['All Status', 'Completed', 'Pending Approval', 'In Progress'],
      years: years,
      steps: [{ value: 'all', label: 'All Steps' }, { value: 'business-plan-gate', label: 'Business Plan Gate' }]
    },
    kpis: { rig_inventory_days: 12, rig_target_days: 20, success_rate_pct: 100,
      classified_rate: 1, actual_mean_ogip_bcf: 40, simulated_mean_ogip_bcf: 80 },
    data_quality: { missing_simulated_mean_project_ids: [], unsuccessful_with_actual_project_ids: [] },
    out_of_range_years: [],
    stage_counts: { pre_drilling: 1, post_drilling: 0, post_testing: 0 },
    wells: [{ project_id: 7, project_name: 'MDFT-7', field: 'MDFT', business_plan_year: currentYear,
      priority: 'High', assignees: ['Supervisor'], assignee_label: 'Supervisor', stage_key: 'pre_drilling',
      stage_label: 'Pre-Drilling', items: items, completed_count: 1, progress_percent: 17,
      bp_gate_status: 'Pending Approval' }]
  };
  var detail = {
    role: 'supervisor',
    project: { project_id: 7, project_name: 'MDFT-7', field: 'MDFT', business_plan_year: currentYear, priority: 'High' },
    detail: { slug: 'business-plan-gate', label: 'Business Plan Execution Gate', stage_key: 'pre_drilling',
      stage_label: 'Pre-Drilling', task_name: 'BP Execution Gate' },
    task: { task_id: 13, status: 'In Progress', revision: 0 }, assignee: 'Supervisor', values: {},
    permissions: { approval_required: true, approval_locked: false, can_edit: true,
      can_submit: true, can_approve: false, can_return: false, can_reopen: false,
      can_manage_assignments: true },
    comments_key: 'bpe_comments_business_plan_gate', formations: [], flowback_stages: [],
    // Card 3E: the record-level bundle the Well Summary is built from. It
    // carries EVERY formation phase (unlike `formations` above, which is this
    // step's own phase) plus the frozen lead snapshot and the server's Total
    // CoS -- the same four inputs the maturation shell hands the same builder.
    well_summary: {
      fields: {
        'SAD Model': { post_drill_piip_gas_p90: 90, post_drill_piip_gas_mean: 116, post_drill_piip_gas_p10: 140 },
        'Flowback Results': { flowback_stages_rows: JSON.stringify([
          { id: 'bpe-stage-1', formation: 'SARH', gas_rate_mmscfd: 12.5,
            fwhp_psi: 3200, choke_size_in: 0.5 }
        ]) },
        'Well Proposal': { sarh_formation_prognosis_pre_drill: 8000 }
      },
      formations: [{ formation: 'SARH', phase: 'final', top_tvdss_ft: 8120, thickness_ft: 96,
                     pay_ft: 74, porosity_pct: 0.214, swt_pct: 0.209, fluid: 'Gas' }],
      lead_summary: { captured_at: '2026-01-09T00:00:00', captured_by: 'Supervisor',
                      fields: { 'Lead Assessment': { reservoir_thickness_ft: 88, lead_piip_gas_mean: 101 } } },
      derisking: '42'
    },
    fluid_state: { decision: 'incomplete', successful: false, fluids: [] }, sad_update_branch: 'blocked_fluid',
    tracking: [{ key: 'business-plan-gate', status: 'In Progress', color: 'empty', source: 'manual', locked: false }],
    stage_items: items, navigation: navigation(), links: { vsp: '', structural_mtr: '' },
    hole_sections: [], formation_options: ['SARH', 'QASM', 'QWRH'], booking_years: [currentYear, currentYear + 1, currentYear + 2, currentYear + 3],
    folder: { path: '\\\\share\\MDFT\\MDFT-7', file_url: 'file://share/MDFT/MDFT-7' }
  };
  var dashboardRequests = [];
  mockFetch(function (url) {
    var path = String(url);
    if (path.indexOf('/api/business-plan/dashboard') >= 0) {
      dashboardRequests.push(path);
      return response(dashboard);
    }
    if (path.indexOf('/api/business-plan/wells/7/steps/business-plan-gate') >= 0) return response(detail);
    if (path.indexOf('/api/users') >= 0) return response([{ name: 'Supervisor', role: 'supervisor' }]);
    throw new Error('Unexpected request: ' + path);
  });

  await refreshBusinessPlan();
  assert.equal(host.querySelector('#bp-assignee-filter').value, 'All Assignees');
  assert.equal(host.querySelector('#bp-status-filter').value, 'All Status');
  assert.equal(host.querySelector('#bp-year-filter').value, String(currentYear));
  // The STEP filter opens on All Steps: it filters by tracking item, and
  // defaulting it to the gate used to restrict the whole board to Pre-Drilling.
  // Narrowing to the gate is the Pre-Drilling column's own toggle now.
  assert.equal(host.querySelector('#bp-step-filter').value, 'all');
  // 1999-2035 plus "All Years", which is a real option rather than a cleared
  // filter, so it lives in the same list as the years and leads it.
  assert.equal(host.querySelectorAll('#bp-year-filter option').length, 38);
  assert.equal(host.querySelector('#bp-year-filter option').value, 'all');
  assert.equal(host.querySelector('#bp-year-filter option').textContent, 'All Years');
  // The default is still the current calendar year, not All Years.
  assert.equal(host.querySelector('.lead-filter[data-bp-filter="year"] .lf-value').textContent,
    String(currentYear));
  // One visible trigger per hidden select, and the board in the maturation
  // board's own vocabulary: three .lead-column blocks, one .lead-card, its six
  // tracked items as dots.
  assert.equal(host.querySelectorAll('#bpe-filter-row .lf-trigger').length, 5);
  assert.equal(host.querySelector('.lead-filter[data-bp-filter="step"] .lf-trigger')
    .getAttribute('aria-label'), 'Filter by step');
  // A filter resting on its default shows its caption, not the option text
  // that repeats it (the maturation row's own rule).
  assert.equal(host.querySelector('.lead-filter[data-bp-filter="step"] .lf-value').textContent, 'Step');
  assert.equal(host.querySelector('.lead-filter[data-bp-filter="year"] .lf-value').textContent, String(currentYear));
  assert.equal(host.querySelector('.lead-filter[data-bp-filter="field"] .lf-trigger')
    .getAttribute('aria-label'), 'Filter by field');
  // Visible left-to-right order is the BP_FILTERS array, NOT the hidden select
  // order: the step filter leads the row and the assignee filter closes it.
  assert.deepEqual(
    Array.prototype.map.call(host.querySelectorAll('#bpe-filter-row .lead-filter'),
      function (group) { return group.getAttribute('data-bp-filter'); }),
    ['step', 'field', 'status', 'year', 'assignee']);
  assert.equal(host.querySelectorAll('.lead-column').length, 3);
  assert.equal(host.querySelectorAll('.lead-card').length, 1);
  assert.equal(host.querySelectorAll('.lead-card .lead-dot').length, 6);
  // A step the workflow closed on the user's behalf (source 'system') keeps
  // the check glyph but drops the green, and says so when read aloud.
  var systemDots = host.querySelectorAll('.lead-card .lead-dot-completed-system');
  assert.equal(systemDots.length, 1, 'exactly the one system-completed item is muted');
  assert.equal(systemDots[0].getAttribute('aria-label'), 'Completed automatically');
  assert.equal(systemDots[0].getAttribute('title'), 'Well Proposal — Completed automatically');
  assert.ok(systemDots[0].classList.contains('lead-dot-completed'),
    'it is still a Completed dot -- the muting is an extra class, not a different state');
  // Every other completed dot stays the ordinary green one.
  assert.equal(host.querySelectorAll('.lead-card .lead-dot-completed').length, 1);
  assert.equal(host.querySelectorAll('#bpe-kpis .kpi-tile').length, 3);
  assert.equal(host.querySelectorAll('#bpe-kpis .kpi-donut').length, 1);
  assert.equal(host.querySelector('#bpe-kpis').textContent.indexOf('40/80 BCF') >= 0, true);

  // The trigger state machine: a menu opens on its trigger, its options are
  // the hidden select's own options, and choosing one writes THE SELECT — the
  // select's 'change' is still the single refresh path. Clear resets all five
  // and refreshes once.
  host.querySelector('.lead-filter[data-bp-filter="field"] .lf-trigger').click();
  var fieldMenu = host.querySelector('.lead-filter[data-bp-filter="field"] .lf-menu');
  assert.equal(fieldMenu.hidden, false);
  assert.equal(fieldMenu.querySelectorAll('.lf-option').length, 2);
  var fieldTrigger = host.querySelector('.lead-filter[data-bp-filter="field"] .lf-trigger');
  fieldMenu.querySelectorAll('.lf-option')[1].click();
  assert.equal(host.querySelector('#bp-field-filter').value, 'MDFT');
  // The row is SYNCED, not rebuilt: the trigger the user activated survives
  // the pick and keeps the focus.
  assert.equal(host.querySelector('.lead-filter[data-bp-filter="field"] .lf-trigger'), fieldTrigger,
    'picking an option does not rebuild the row');
  assert.equal(document.activeElement, fieldTrigger, 'focus returns to the trigger');
  assert.equal(fieldTrigger.querySelector('.lf-value').textContent, 'MDFT');
  assert.ok(fieldTrigger.classList.contains('is-active'));
  assert.equal(host.querySelector('.lead-filter[data-bp-filter="field"] .lf-menu').hidden, true,
    'a single choice is a finished choice');
  await waitFor(function () { return dashboardRequests.length === 2; });
  assert.equal(host.querySelector('#bpe-filter-row .lf-clear').disabled, false);
  host.querySelector('#bpe-filter-row .lf-clear').click();
  assert.equal(host.querySelector('#bp-field-filter').value, 'All Fields');
  assert.equal(host.querySelector('#bpe-filter-row .lf-clear').disabled, true);
  await waitFor(function () { return dashboardRequests.length === 3; });

  // All Years is a SELECTION, so it counts as an active filter and Clear puts
  // the board back on the current year rather than leaving it spanning all of
  // them. It travels to the server as the literal `all`.
  host.querySelector('.lead-filter[data-bp-filter="year"] .lf-trigger').click();
  var yearMenu = host.querySelector('.lead-filter[data-bp-filter="year"] .lf-menu');
  assert.equal(yearMenu.querySelector('.lf-option').getAttribute('data-value'), 'all');
  yearMenu.querySelector('.lf-option').click();
  assert.equal(host.querySelector('#bp-year-filter').value, 'all');
  assert.equal(host.querySelector('.lead-filter[data-bp-filter="year"] .lf-value').textContent, 'All Years');
  assert.equal(host.querySelector('#bpe-filter-row .lf-clear').disabled, false);
  await waitFor(function () { return dashboardRequests.length === 4; });
  assert.match(dashboardRequests[dashboardRequests.length - 1], /year=all/);
  host.querySelector('#bpe-filter-row .lf-clear').click();
  assert.equal(host.querySelector('#bp-year-filter').value, String(currentYear));
  assert.equal(host.querySelector('#bpe-filter-row .lf-clear').disabled, true);
  await waitFor(function () { return dashboardRequests.length === 5; });

  host.querySelector('#bp-assignee-filter').value = 'Supervisor';
  host.querySelector('#bp-field-filter').value = 'MDFT';
  host.querySelector('#bp-status-filter').value = 'Completed';
  host.querySelector('#bp-step-filter').value = 'all';
  await syncBusinessPlanPromotion(2027);
  assert.equal(host.querySelector('#bp-year-filter').value, '2027',
    'Portfolio promotion selects its target BP year');
  assert.equal(host.querySelector('#bp-assignee-filter').value, 'All Assignees');
  assert.equal(host.querySelector('#bp-field-filter').value, 'All Fields');
  assert.equal(host.querySelector('#bp-status-filter').value, 'All Status');
  assert.equal(host.querySelector('#bp-step-filter').value, 'all');
  assert.match(dashboardRequests[dashboardRequests.length - 1], /year=2027/,
    'the synchronized dashboard fetches the promoted year immediately');

  // The card is ONE target and opens the first item that is not Completed —
  // here the Business Plan Gate (Well Proposal, the only completed one, is
  // second). The mock serves that one step and nothing else.
  assert.equal(host.querySelector('.lead-card').getAttribute('data-step'), 'business-plan-gate');
  host.querySelector('.lead-card').click();
  await waitFor(function () { return host.querySelector('.bpe-detail-form'); });
  assert.equal(host.querySelectorAll('#bpe-back').length, 1);
  assert.equal(host.querySelector('#bpe-back').textContent.trim(), 'Back to Business Plan Execution');
  assert.ok(host.querySelector('.component-rail .rail-head > #bpe-back'),
    'the back control sits in the rail head, where the maturation detail keeps its own');
  // Priority is a RECORD attribute: one click-to-cycle chip beside the well
  // name (the maturation shell's exact placement), and no per-step dropdown.
  var chip = host.querySelector('.rail-head .detail-title-row #bpe-priority-chip');
  assert.ok(chip, 'the priority chip sits beside the record name');
  assert.equal(chip.textContent, 'High');
  assert.ok(chip.classList.contains('priority-high'));
  assert.equal(chip.disabled, false, 'a supervisor can cycle it');
  assert.equal(host.querySelectorAll('#bpe-priority').length, 0,
    'the old per-step priority select is gone');
  assert.equal(host.querySelectorAll('.bpe-nav-item').length, 14);
  assert.equal(host.querySelectorAll('.bpe-nav-item').length,
    new Set(Array.prototype.map.call(host.querySelectorAll('.bpe-nav-item'), function (button) { return button.textContent; })).size);
  // The detail page is the maturation detail shell: rail (three stage blocks,
  // the open step's one marked active) | editor | Well Summary card.
  assert.equal(host.querySelectorAll('.detail-shell.detail-shell-lead').length, 1);
  assert.equal(host.querySelectorAll('.component-rail .rail-stage-lead').length, 3);
  // The rail is the house ACCORDION: exactly one stage expanded, and it is the
  // one owning the open step. All fourteen items stay in the DOM.
  assert.equal(host.querySelectorAll('.rail-stage-lead.is-active').length, 1);
  assert.equal(host.querySelector('.rail-stage-lead.is-active').getAttribute('data-stage'), 'pre_drilling');
  assert.equal(host.querySelectorAll('.rail-stage-head.open').length, 1);
  assert.equal(host.querySelectorAll('.rail-stage-body:not(.collapsed)').length, 1);
  assert.equal(host.querySelectorAll('.rail-stage-head').length, 3);
  assert.equal(host.querySelector('.rail-stage-head').tagName, 'BUTTON');
  // Opening another stage collapses the one that was open.
  host.querySelector('.rail-stage-head[data-stage="post_testing"]').click();
  assert.equal(host.querySelector('.rail-stage-lead.is-active').getAttribute('data-stage'), 'post_testing');
  assert.equal(host.querySelectorAll('.rail-stage-head.open').length, 1);
  assert.equal(host.querySelector('.rail-stage-body[data-stage="pre_drilling"]').classList.contains('collapsed'), true);
  assert.equal(host.querySelectorAll('.bpe-nav-item').length, 14, 'collapsing hides items, never removes them');
  // Clicking the open head folds it away, exactly as the maturation rail does.
  host.querySelector('.rail-stage-head[data-stage="post_testing"]').click();
  assert.equal(host.querySelectorAll('.rail-stage-head.open').length, 0);
  host.querySelector('.rail-stage-head[data-stage="pre_drilling"]').click();
  assert.equal(host.querySelectorAll('.component-item.active').length, 1);
  assert.equal(host.querySelector('.component-item.active').getAttribute('data-detail-slug'), 'business-plan-gate');
  // Each rail badge is tinted by its step's status, mapped onto the house
  // rail's four task-lifecycle slugs (a raw 'status-completed' has no rule).
  assert.equal(host.querySelector('[data-detail-slug="gheer-inputs"]').className,
    'component-item status-approved bpe-nav-item');
  assert.equal(host.querySelector('[data-detail-slug="well-letters"]').className,
    'component-item status-ready bpe-nav-item');
  assert.equal(host.querySelector('[data-detail-slug="business-plan-gate"]').className,
    'component-item status-in-progress active bpe-nav-item');
  assert.equal(host.querySelectorAll('.component-editor.bpe-detail-form').length, 1);
  // Form primitives are the house ones (Well Classification is the radio
  // group; the gate's slides confirmation is the checkbox card).
  assert.equal(host.querySelectorAll('.bpe-detail-form .radio-group .radio-option').length, 3);
  assert.ok(host.querySelector('.bpe-detail-form .check-label'));
  assert.equal(host.querySelector('[data-bpe-output="bp_gate_calculated_td_ft_md"]').parentElement.querySelector('span').textContent,
    'Calculated BP TD');
  assert.equal(host.querySelector('[data-bpe-field="bp_gate_actual_td_ft_md"]').parentElement.querySelector('span').textContent,
    'Actual BP TD (ft MD)*');
  assert.equal(host.querySelector('[data-bpe-field="bp_gate_pressure_points"]').parentElement.querySelector('span').textContent,
    'Pressure');
  assert.equal(host.querySelector('[data-bpe-field="bp_gate_fluid_samples"]').parentElement.querySelector('span').textContent,
    'Fluid');
  assert.ok(host.querySelector('.folder-card #bpe-copy-folder'));
  assert.ok(host.querySelector('#bpe-save-feedback').classList.contains('save-state'));
  // The Well Summary is the Lead Summary card's anatomy, and its progress bar
  // is progressPercent() over the stage's own items (1 of 6 completed).
  assert.equal(host.querySelectorAll('.summary-panel .ls-card').length, 1);
  assert.equal(host.querySelector('.ls-title').textContent, 'Well Summary');
  assert.equal(host.querySelector('.ls-progress-figures').textContent, '17%1 / 6');
  assert.equal(host.querySelector('.summary-phase-label').textContent.trim(), 'BP Well · ' + currentYear);
  /* Card 3E: the panel is the WELL SUMMARY CARD, not a fact sheet -- the same
     builder the maturation shell calls, over this payload's own bundle. Its
     sections, in the drawn order, and its trio read as the Lead Summary card's
     do (label over value, "P90 Mean P10"). */
  assert.deepEqual(Array.prototype.map.call(host.querySelectorAll('.summary-panel .ls-card > .ls-section > .ls-section-title'), function (title) {
    return title.textContent;
  }), ['Gas (BCF)', 'Flowback Results', 'Reservoir Properties']);
  assert.deepEqual(Array.prototype.map.call(host.querySelectorAll('.summary-panel .ls-card > .ls-section .ls-col-label'), function (cell) {
    return cell.textContent;
  }), ['P90', 'Mean', 'P10']);
  assert.deepEqual(Array.prototype.map.call(host.querySelectorAll('.summary-panel .ls-card > .ls-section .ls-col-value'), function (cell) {
    return cell.textContent;
  }), ['90', '116', '140']);
  // The reservoir row is the drilled formation, its two percentages printed to
  // exactly two decimals with no % suffix.
  assert.equal(host.querySelector('.summary-props-row .summary-props-name').textContent, 'SARH');
  assert.deepEqual(Array.prototype.map.call(host.querySelectorAll('.summary-props-row span:not(.summary-props-name)'), function (cell) {
    return cell.textContent;
  }), ['74.00 ft', '0.21', '0.21']);
  // The old fact rows and item list are gone: the well's names are in the rail
  // head, the year is in the phase row, and each item's state is on its page.
  assert.equal(host.querySelectorAll('.bpe-summary-facts').length, 0);
  assert.equal(host.querySelectorAll('.summary-panel .ls-items').length, 0);
  // EXACTLY two expandable sections, the Delta first and both collapsed.
  var folds = host.querySelectorAll('.summary-panel .summary-fold-head');
  assert.equal(folds.length, 2);
  assert.deepEqual(Array.prototype.map.call(folds, function (head) {
    return head.querySelector('.summary-fold-title').textContent;
  }), ['Simulated Vs Actual Delta', 'Lead Summary']);
  assert.deepEqual(Array.prototype.map.call(folds, function (head) {
    return head.getAttribute('aria-expanded');
  }), ['false', 'false']);
  // Ids are namespaced: both detail shells live in one document, and the
  // maturation card renders the same fold keys.
  assert.equal(folds[0].id, 'bpe-summary-fold-pva');
  assert.equal(folds[0].getAttribute('aria-controls'), 'bpe-summary-fold-pva-body');
  // Opening one is a pure view change -- it toggles in place and fires no
  // request (the mock throws on anything unexpected).
  folds[0].click();
  assert.equal(folds[0].getAttribute('aria-expanded'), 'true');
  assert.equal(host.querySelector('#bpe-summary-fold-pva-body').classList.contains('collapsed'), false);
  assert.equal(host.querySelector('#bpe-summary-fold-lead-body').classList.contains('collapsed'), true,
    'the folds are independent');
  // The Delta compares the frozen lead snapshot with the drilled result.
  assert.deepEqual(Array.prototype.map.call(host.querySelectorAll('#bpe-summary-fold-pva-body .summary-pva-label'), function (cell) {
    return cell.textContent;
  }), ['', 'Top SARH', 'Thickness (ft)', 'Area P90 (km²)', 'Area P10 (km²)', 'Mean (BCF)']);
  folds[0].click();
  assert.equal(folds[0].getAttribute('aria-expanded'), 'false');
  assert.ok(host.querySelector('.bpe-save-line').textContent.indexOf('All changes are saved automatically') >= 0);
  assert.equal(host.textContent.indexOf('Save Updates'), -1);
  assert.deepEqual(Array.prototype.map.call(host.querySelectorAll('.bpe-approval-row button'), function (button) {
    return button.textContent.trim();
  }), ['Submit for Approval']);
  assert.ok(host.querySelector('#bpe-summary-gear').classList.contains('ls-gear'));
  assert.ok(host.querySelector('#bpe-summary-menu').classList.contains('ls-menu'));
  assert.ok(host.querySelector('#bpe-edit-all').classList.contains('ls-menu-item'));
  // The gear still opens and dismisses its own menu (only the classes moved).
  assert.equal(host.querySelector('#bpe-summary-menu').classList.contains('hidden'), true);
  host.querySelector('#bpe-summary-gear').click();
  assert.equal(host.querySelector('#bpe-summary-menu').classList.contains('hidden'), false);
  document.body.click();
  assert.equal(host.querySelector('#bpe-summary-menu').classList.contains('hidden'), true);
});

function detailPayload(slug, values) {
  var label = slug === 'flowback-results' ? 'Flowback Results' : 'Business Plan Execution Gate';
  var key = slug === 'flowback-results' ? 'flowback' : 'business-plan-gate';
  var stage = slug === 'flowback-results' ? 'Post-Testing' : 'Pre-Drilling';
  var required = slug === 'business-plan-gate';
  var permissions = { approval_required: required, approval_locked: false, can_edit: true,
    can_submit: required, can_approve: false, can_return: false, can_reopen: false,
    can_manage_assignments: true };
  return {
    role: 'supervisor',
    project: { project_id: 7, project_name: 'MDFT-7', field: 'MDFT', business_plan_year: 2027, priority: 'High' },
    detail: { slug: slug, label: label, stage_key: slug === 'flowback-results' ? 'post_testing' : 'pre_drilling',
      stage_label: stage, task_name: slug === 'flowback-results' ? 'Flowback Results' : 'BP Execution Gate' },
    task: { task_id: 13, status: 'In Progress', revision: 0, permissions: permissions },
    permissions: permissions, assignee: 'Supervisor', values: values || {},
    comments_key: 'bpe_comments_' + slug.replace(/-/g, '_'), formations: [], flowback_stages: [],
    flowback_initialized: false,
    fluid_state: { decision: 'incomplete', successful: false, fluids: [] }, sad_update_branch: 'blocked_fluid',
    tracking: [{ key: key, status: 'In Progress', color: 'empty', source: 'manual', locked: false }],
    stage_items: [{ key: key, label: label, detail_slug: slug,
      status: 'In Progress', color: 'empty', source: 'manual', locked: false }],
    navigation: navigation(), links: { vsp: '', structural_mtr: '' },
    hole_sections: [], formation_options: ['SARH', 'QASM', 'QWRH'], booking_years: [2027, 2028, 2029, 2030],
    folder: { path: '\\\\share\\MDFT\\MDFT-7', file_url: 'file://share/MDFT/MDFT-7' }
  };
}

test('business-plan detail renders source-free chips and the shared assignment checklist', async function () {
  var host = fixture('<div id="bpe-main-view"></div><section id="bpe-detail-view" class="hidden"></section>');
  var hooks = businessPlanTestHooks();
  var savedUsers = hooks.state.users;
  var detail = detailPayload('business-plan-gate', {});
  detail.task.assignees = [
    { name: 'Employee', source: 'role', notified: true },
    { name: 'Staff Member', source: 'creator', notified: true },
    { name: 'Supervisor', source: 'manual', notified: true }
  ];
  detail.assignee = 'Employee';
  hooks.state.users = [
    { name: 'Employee' }, { name: 'Staff Member' }, { name: 'Supervisor' }, { name: 'Available User' }
  ];
  var assignmentPayloads = [];
  mockFetch(function (url, options) {
    if (String(url).indexOf('/api/business-plan/wells/7/steps/business-plan-gate') >= 0) {
      if (options && options.method === 'POST') assignmentPayloads.push(JSON.parse(options.body));
      return response(detail);
    }
    throw new Error('Unexpected request: ' + url);
  });
  try {
    await openBusinessPlanDetail(7, 'business-plan-gate');
    var chips = host.querySelectorAll('#bpe-assignment-group .assignee-chip');
    assert.equal(chips.length, 3);
    assert.equal(chips[0].querySelector('.assignee-remove'), null,
      'role assignment remains protected in the BPE editor');
    assert.ok(chips[1].querySelector('.assignee-remove'));
    assert.equal(host.querySelectorAll('.assignee-chip-source').length, 0);
    assert.equal(chips[1].textContent.replace(/\s+/g, ' ').trim(), 'Staff Member');
    assert.ok(chips[2].querySelector('.assignee-remove'));
    var roleBox = host.querySelector('[data-assignment-name="Employee"]');
    var creatorBox = host.querySelector('[data-assignment-name="Staff Member"]');
    var availableBox = host.querySelector('[data-assignment-name="Available User"]');
    assert.equal(roleBox.checked, true);
    assert.equal(roleBox.disabled, true);
    assert.equal(availableBox.checked, false);
    host.querySelector('#bpe-assignee').click();
    assert.equal(host.querySelector('#bpe-assignee-menu').hidden, false);
    availableBox.checked = true;
    availableBox.dispatchEvent(new Event('change', { bubbles: true }));
    creatorBox.checked = false;
    creatorBox.dispatchEvent(new Event('change', { bubbles: true }));
    await waitFor(function () { return assignmentPayloads.length === 2; });
    assert.deepEqual(assignmentPayloads[0].add, ['Available User']);
    assert.deepEqual(assignmentPayloads[1].remove, ['Staff Member']);
  } finally {
    hooks.state.users = savedUsers;
  }
});

test('business-plan assignment checklist is read-only for employees', async function () {
  var host = fixture('<div id="bpe-main-view"></div><section id="bpe-detail-view" class="hidden"></section>');
  var hooks = businessPlanTestHooks();
  var savedUsers = hooks.state.users;
  var detail = detailPayload('business-plan-gate', {});
  detail.role = 'employee';
  detail.permissions.can_manage_assignments = false;
  detail.task.permissions.can_manage_assignments = false;
  detail.task.assignees = [{ name: 'Employee', source: 'manual' }];
  hooks.state.users = [{ name: 'Employee' }, { name: 'Supervisor' }];
  mockFetch(function (url) {
    if (String(url).indexOf('/api/business-plan/wells/7/steps/business-plan-gate') >= 0) return response(detail);
    throw new Error('Unexpected request: ' + url);
  });
  try {
    await openBusinessPlanDetail(7, 'business-plan-gate');
    assert.equal(host.querySelector('#bpe-assignee').disabled, true);
    Array.from(host.querySelectorAll('[data-assignment-name]')).forEach(function (input) {
      assert.equal(input.disabled, true);
    });
  } finally {
    hooks.state.users = savedUsers;
  }
});

test('business-plan serializes auto-saves and a stale response cannot replace newer input', async function () {
  var host = fixture('<div id="bpe-main-view"></div><section id="bpe-detail-view" class="hidden"></section>');
  var base = detailPayload('business-plan-gate', {});
  base.project.business_plan_year = null;
  var payloads = [];
  var releases = [];
  var concurrent = 0;
  var maxConcurrent = 0;
  mockFetch(function (url, options) {
    var path = String(url);
    var method = (options && options.method) || 'GET';
    if (path.indexOf('/api/business-plan/wells/7/steps/business-plan-gate') >= 0 && method === 'GET') {
      return response(base);
    }
    if (path.indexOf('/api/business-plan/wells/7/steps/business-plan-gate/field') >= 0 && method === 'PATCH') {
      var payload = JSON.parse(options.body);
      payloads.push(payload);
      concurrent += 1;
      maxConcurrent = Math.max(maxConcurrent, concurrent);
      return new Promise(function (resolve) {
        releases.push(function () {
          concurrent -= 1;
          var next = detailPayload('business-plan-gate', { bp_gate_logging_program: payload.value });
          resolve(response({ ok: true, detail: next }));
        });
      });
    }
    if (path.indexOf('/api/users') >= 0) return response([]);
    throw new Error('Unexpected request: ' + method + ' ' + path);
  });

  await openBusinessPlanDetail(7, 'business-plan-gate');
  // A record with nothing recorded still renders every section, each value as
  // the one "no value" glyph -- a blank cell reads as a layout bug.
  assert.deepEqual(Array.prototype.map.call(host.querySelectorAll('.summary-panel .ls-card > .ls-section .ls-col-value'), function (cell) {
    return cell.textContent;
  }), ['—', '—', '—']);
  assert.equal(host.querySelector('.summary-props-row-empty .summary-props-note').textContent, '—');
  assert.equal(host.querySelectorAll('.summary-panel .summary-fold-head').length, 2);
  var program = host.querySelector('[data-bpe-field="bp_gate_logging_program"]');
  program.value = 'Standard A';
  program.dispatchEvent(new Event('change', { bubbles: true }));
  await waitFor(function () { return payloads.length === 1; });
  program.value = 'Standard B';
  program.dispatchEvent(new Event('change', { bubbles: true }));
  await new Promise(function (resolve) { setTimeout(resolve, 40); });
  assert.equal(payloads.length, 1, 'the second PATCH waits for the first');

  releases.shift()();
  await waitFor(function () { return payloads.length === 2; });
  assert.equal(host.querySelector('[data-bpe-field="bp_gate_logging_program"]').value, 'Standard B',
    'the first response did not repaint the newer selection');
  assert.equal(maxConcurrent, 1, 'only one save is in flight');
  releases.shift()();
  await waitFor(function () { return host.querySelector('#bpe-save-feedback').textContent === 'Saved'; });
  assert.deepEqual(payloads.map(function (payload) { return payload.value; }), ['Standard A', 'Standard B']);
  businessPlanTestHooks().configureSaveDelay(null);
});

test('business-plan asks before a Well Classification change resets its defaults', async function () {
  var host = fixture('<div id="bpe-main-view"></div><section id="bpe-detail-view" class="hidden"></section>');
  var stored = detailPayload('business-plan-gate', { bp_gate_classification: 'Development' });
  var payloads = [];
  mockFetch(function (url, options) {
    var path = String(url);
    var method = (options && options.method) || 'GET';
    if (path.indexOf('/steps/business-plan-gate/field') >= 0 && method === 'PATCH') {
      payloads.push(JSON.parse(options.body));
      return response({ ok: true, detail: stored });
    }
    if (path.indexOf('/steps/business-plan-gate') >= 0) return response(stored);
    if (path.indexOf('/api/users') >= 0) return response([]);
    throw new Error('Unexpected request: ' + method + ' ' + path);
  });

  function classification(option) {
    return host.querySelector('[data-bpe-field="bp_gate_classification"][value="' + option + '"]');
  }
  await openBusinessPlanDetail(7, 'business-plan-gate');
  var dialog = document.getElementById('app-dialog');

  // Cancelling restores the STORED classification and saves nothing.
  classification('Appraisal').click();
  await waitFor(function () { return dialog.open; });
  assert.equal(document.getElementById('app-dialog-title').textContent, 'Change Well Classification');
  assert.match(document.getElementById('app-dialog-message').textContent,
    /from "Development" to "Appraisal"/);
  document.getElementById('app-dialog-cancel').click();
  await waitFor(function () { return classification('Development').checked; });
  assert.equal(payloads.length, 0, 'a cancelled change queues no save');

  // Confirming carries confirm_reset, which is what lets the server clear the
  // classification-driven defaults.
  classification('Exploration').click();
  await waitFor(function () { return dialog.open; });
  dialog.returnValue = 'confirm';
  dialog.close();
  await waitFor(function () { return payloads.length === 1; });
  assert.equal(payloads[0].value, 'Exploration');
  assert.equal(payloads[0].confirm_reset, true);
});

test('business-plan keeps zero Flowback panels after the final stage is deleted', async function () {
  var host = fixture('<div id="bpe-main-view"></div><section id="bpe-detail-view" class="hidden"></section>');
  var detail = detailPayload('flowback-results', {});
  detail.flowback_initialized = true;
  detail.flowback_stages = [{
    id: 'stable-stage', formation: 'SARH', top_md: '1000', base_md: '1100',
    dynamic_area_km2: '', dynamic_ogip_bcf: '', gas_rate_mmscfd: '', water_rate_bwpd: '',
    liquid_rate_bpd: '', choke_size_in: '0.5', fwhp_psi: '1500'
  }];
  var puts = [];
  mockFetch(function (url, options) {
    var path = String(url);
    var method = (options && options.method) || 'GET';
    if (path.indexOf('/api/business-plan/wells/7/steps/flowback-results') >= 0 && method === 'GET') {
      return response(detail);
    }
    if (path.indexOf('/api/business-plan/wells/7/flowback-stages') >= 0 && method === 'PUT') {
      var rows = JSON.parse(options.body).rows;
      puts.push(rows);
      var next = detailPayload('flowback-results', {});
      next.flowback_initialized = true;
      next.flowback_stages = rows;
      return response({ ok: true, detail: next });
    }
    if (path.indexOf('/api/users') >= 0) return response([]);
    throw new Error('Unexpected request: ' + method + ' ' + path);
  });

  await openBusinessPlanDetail(7, 'flowback-results');
  // The rail follows the STEP that is open: loading a Post-Testing step
  // expands Post-Testing, never a remembered fold or the first group.
  assert.equal(host.querySelector('.rail-stage-lead.is-active').getAttribute('data-stage'), 'post_testing');
  assert.equal(host.querySelectorAll('.rail-stage-head.open').length, 1);
  assert.equal(host.querySelectorAll('.bpe-flow-stage').length, 1);
  assert.equal(host.querySelector('[data-flow-field="top_md"]').parentElement.querySelector('span').textContent,
    'Top (MD)*');
  assert.equal(host.querySelector('[data-flow-field="base_md"]').parentElement.querySelector('span').textContent,
    'Base (MD)*');
  assert.equal(host.querySelector('[data-flow-field="formation"]').tagName, 'SELECT',
    'Formation remains a dropdown even when its current value is blank');
  // Removals are confirmed through the app dialog (#app-dialog in
  // runner.html), never window.confirm.
  var dialog = document.getElementById('app-dialog');
  host.querySelector('.bpe-remove-flow').click();
  await waitFor(function () { return dialog.open; });
  assert.equal(document.getElementById('app-dialog-title').textContent, 'Delete Flowback Stage');
  assert.match(document.getElementById('app-dialog-message').textContent, /Delete Stage 1\?/);
  assert.ok(document.getElementById('app-dialog-confirm').classList.contains('danger'));
  // The dialog form's own confirm path: method="dialog" submit sets
  // returnValue and closes (same simulation test-transitions.js uses).
  dialog.returnValue = 'confirm';
  dialog.close();
  await waitFor(function () { return puts.length === 1; });
  await waitFor(function () { return host.querySelectorAll('.bpe-flow-stage').length === 0; });
  assert.deepEqual(puts[0], []);
  assert.ok(host.querySelector('#bpe-add-flow'), 'the add control remains available');
});

/* This form had NO client-side numeric validation at all: a negative rate or a
   140% saturation travelled to the server, which refused the whole sheet with
   a message that arrived after the round trip and named a storage key rather
   than a cell. The guard below stops the edit at the input. */
test('business-plan refuses a negative measurement before it is queued', async function () {
  var host = fixture('<div id="bpe-main-view"></div><section id="bpe-detail-view" class="hidden"></section>');
  var detail = detailPayload('flowback-results', {});
  detail.flowback_initialized = true;
  detail.flowback_stages = [{
    id: 'stage-1', formation: 'SARH', top_md: '1000', base_md: '1100',
    dynamic_area_km2: '', dynamic_ogip_bcf: '', gas_rate_mmscfd: '', water_rate_bwpd: '',
    liquid_rate_bpd: '', choke_size_in: '', fwhp_psi: ''
  }];
  var puts = [];
  mockFetch(function (url, options) {
    var path = String(url);
    var method = (options && options.method) || 'GET';
    if (path.indexOf('/api/business-plan/wells/7/steps/flowback-results') >= 0 && method === 'GET') {
      return response(detail);
    }
    if (path.indexOf('/api/business-plan/wells/7/flowback-stages') >= 0 && method === 'PUT') {
      puts.push(JSON.parse(options.body).rows);
      return response({ ok: true, detail: detail });
    }
    if (path.indexOf('/api/users') >= 0) return response([]);
    throw new Error('Unexpected request: ' + method + ' ' + path);
  });

  await openBusinessPlanDetail(7, 'flowback-results');
  var rate = host.querySelector('[data-flow-field="gas_rate_mmscfd"]');
  assert.equal(rate.getAttribute('min'), '0', 'the browser-level floor is emitted too');

  rate.value = '-5';
  rate.dispatchEvent(new Event('input', { bubbles: true }));
  assert.ok(rate.classList.contains('bpe-invalid'), 'the offending cell is marked');
  assert.equal(rate.getAttribute('aria-invalid'), 'true');
  var feedback = host.querySelector('#bpe-save-feedback');
  assert.match(feedback.textContent, /must not be negative/);
  assert.ok(feedback.classList.contains('is-error'));

  // Correcting the value clears the mark and lets the edit through.
  rate.value = '5';
  rate.dispatchEvent(new Event('input', { bubbles: true }));
  assert.equal(rate.classList.contains('bpe-invalid'), false);
  assert.equal(rate.hasAttribute('aria-invalid'), false);
  await waitFor(function () { return puts.length === 1; });
  assert.equal(puts[0][0].gas_rate_mmscfd, '5');
  assert.equal(puts.length, 1, 'the refused value was never sent');
});

/* Percentages have an upper bound as well as a lower one, and TVDSS is the
   one measure that is signed on purpose. */
test('business-plan bounds percentages and refuses a negative TVDSS', async function () {
  var host = fixture('<div id="bpe-main-view"></div><section id="bpe-detail-view" class="hidden"></section>');
  var detail = detailPayload('quicklook-logs', {});
  detail.formations = [{
    id: 1, formation: 'SARH', top_tvdss_ft: '', base_tvdss_ft: '', thickness_ft: '',
    pay_intervals: [{ id: 11, top_tvdss_ft: '', base_tvdss_ft: '', phit_pct: '',
      swt_pct: '', ngr_pct: '', kint_md: '', fluid: 'Gas' }]
  }];
  var puts = [];
  mockFetch(function (url, options) {
    var path = String(url);
    var method = (options && options.method) || 'GET';
    if (path.indexOf('/api/business-plan/wells/7/steps/quicklook-logs') >= 0 && method === 'GET') {
      return response(detail);
    }
    if (path.indexOf('/formations') >= 0 && method === 'PUT') {
      puts.push(JSON.parse(options.body).rows);
      return response({ ok: true, detail: detail });
    }
    if (path.indexOf('/api/users') >= 0) return response([]);
    throw new Error('Unexpected request: ' + method + ' ' + path);
  });

  await openBusinessPlanDetail(7, 'quicklook-logs');
  var swt = host.querySelector('[data-pay-field="swt_pct"]');
  swt.value = '140';
  swt.dispatchEvent(new Event('input', { bubbles: true }));
  assert.ok(swt.classList.contains('bpe-invalid'));
  assert.match(host.querySelector('#bpe-save-feedback').textContent, /100%/);

  // Card 3H: TVDSS is a magnitude now, so it carries the same floor as every
  // other measurement on the sheet.
  var top = host.querySelector('[data-formation-field="top_tvdss_ft"]');
  assert.equal(top.getAttribute('min'), '0', 'no field here is signed any more');
  top.value = '-120';
  top.dispatchEvent(new Event('input', { bubbles: true }));
  assert.ok(top.classList.contains('bpe-invalid'));
  // A real depth still passes, including one past the generic 9999 cap.
  top.value = '11500';
  top.dispatchEvent(new Event('input', { bubbles: true }));
  assert.equal(top.classList.contains('bpe-invalid'), false);
});

// ---------------------------------------------------------------------------
// A refused approval must not trap the user on the step (the "BPE 6" bug)
// ---------------------------------------------------------------------------
//
// Every navigation entry point -- the back button, the rail, another well's
// card, and Submit itself -- goes through flushPendingSaves(), which refuses
// to let go while something is unsaved. A REFUSED transition saves nothing and
// leaves nothing pending, so it must not hold the page; a FAILED field draft
// is the user's own typing and must.

// Tests before these leave a pending formation draft on purpose (they type
// into a cell and assert the inline validation without ever flushing). That
// draft is module state, and it would fire its PUT into whichever mock is
// installed next -- so start from a clean slate rather than teaching every
// mock below to answer for someone else's step.
function resetBusinessPlanState() {
  var state = businessPlanTestHooks().state;
  Object.keys(state.timers).forEach(function (key) {
    clearTimeout(state.timers[key]);
    delete state.timers[key];
  });
  state.fieldDrafts = {};
  state.structureDrafts = { formations: null, flowback: null };
  state.retryCommand = null;
  state.detail = null;
}

function errorResponse(status, detail) {
  return new Response(JSON.stringify({ detail: detail }), {
    status: status,
    headers: { 'Content-Type': 'application/json' }
  });
}

test('business-plan a refused Submit for Approval shows why and leaves navigation working', async function () {
  var host = fixture('<div id="bpe-main-view"></div><section id="bpe-detail-view" class="hidden"></section>');
  resetBusinessPlanState();
  var loads = [];
  var refusal = 'Well Classification is required. Logging Program is required.';
  mockFetch(function (url, options) {
    var path = String(url);
    var method = (options && options.method) || 'GET';
    if (/\/api\/tasks\/13\/transition/.test(path) && method === 'POST') {
      return errorResponse(400, refusal);
    }
    var match = path.match(/\/steps\/([a-z-]+)(\?|$)/);
    if (match && method === 'GET') {
      loads.push(match[1]);
      return response(detailPayload(match[1], {}));
    }
    if (path.indexOf('/api/users') >= 0) return response([]);
    throw new Error('Unexpected request: ' + method + ' ' + path);
  });

  await openBusinessPlanDetail(7, 'business-plan-gate');
  assert.deepEqual(loads, ['business-plan-gate']);

  host.querySelector('[data-bpe-transition="submit"]').click();
  await waitFor(function () {
    return host.querySelector('#bpe-save-feedback').classList.contains('is-error');
  });
  // The server's own reason, not a generic "Save failed" -- nothing failed to
  // save, the submission was refused.
  assert.equal(host.querySelector('#bpe-save-feedback').textContent, refusal);
  // No Retry: re-sending the identical payload would be refused identically.
  assert.equal(host.querySelector('#bpe-retry-save').classList.contains('hidden'), true);

  // THE BUG: this click used to be a silent no-op.
  host.querySelector('[data-detail-slug="gheer-inputs"]').click();
  await waitFor(function () { return loads.length === 2; });
  assert.deepEqual(loads, ['business-plan-gate', 'gheer-inputs'],
    'the rail still navigates after a refused submission');
});

test('business-plan a failed field save DOES still hold the page', async function () {
  var host = fixture('<div id="bpe-main-view"></div><section id="bpe-detail-view" class="hidden"></section>');
  resetBusinessPlanState();
  var loads = [];
  mockFetch(function (url, options) {
    var path = String(url);
    var method = (options && options.method) || 'GET';
    if (path.indexOf('/field') >= 0 && method === 'PATCH') throw new Error('network down');
    var match = path.match(/\/steps\/([a-z-]+)(\?|$)/);
    if (match && method === 'GET') {
      loads.push(match[1]);
      return response(detailPayload(match[1], {}));
    }
    if (path.indexOf('/api/users') >= 0) return response([]);
    throw new Error('Unexpected request: ' + method + ' ' + path);
  });

  await openBusinessPlanDetail(7, 'business-plan-gate');
  var program = host.querySelector('[data-bpe-field="bp_gate_logging_program"]');
  program.value = 'Standard A';
  program.dispatchEvent(new Event('change', { bubbles: true }));
  await waitFor(function () {
    return host.querySelector('#bpe-save-feedback').textContent === 'Save failed';
  });
  // Retry IS offered here: the request never landed, so sending it again is
  // exactly the right move.
  assert.equal(host.querySelector('#bpe-retry-save').classList.contains('hidden'), false);

  host.querySelector('[data-detail-slug="gheer-inputs"]').click();
  await new Promise(function (resolve) { setTimeout(resolve, 60); });
  assert.deepEqual(loads, ['business-plan-gate'],
    'unsaved typing still blocks navigation -- leaving would discard it');
});

test('business-plan a transition that never reached the server offers Retry and still lets go', async function () {
  var host = fixture('<div id="bpe-main-view"></div><section id="bpe-detail-view" class="hidden"></section>');
  resetBusinessPlanState();
  var loads = [];
  mockFetch(function (url, options) {
    var path = String(url);
    var method = (options && options.method) || 'GET';
    if (/\/api\/tasks\/13\/transition/.test(path) && method === 'POST') throw new Error('network down');
    var match = path.match(/\/steps\/([a-z-]+)(\?|$)/);
    if (match && method === 'GET') {
      loads.push(match[1]);
      return response(detailPayload(match[1], {}));
    }
    if (path.indexOf('/api/users') >= 0) return response([]);
    throw new Error('Unexpected request: ' + method + ' ' + path);
  });

  await openBusinessPlanDetail(7, 'business-plan-gate');
  host.querySelector('[data-bpe-transition="submit"]').click();
  await waitFor(function () {
    return host.querySelector('#bpe-save-feedback').textContent === 'Save failed';
  });
  // The request never landed, so re-sending it is the right offer -- unlike a
  // refusal, where the payload itself is the problem.
  assert.equal(host.querySelector('#bpe-retry-save').classList.contains('hidden'), false);

  // But a transition that did not happen leaves NOTHING unsaved, so it must
  // not hold the page hostage the way a failed field draft does.
  host.querySelector('[data-detail-slug="gheer-inputs"]').click();
  await waitFor(function () { return loads.length === 2; });
  assert.deepEqual(loads, ['business-plan-gate', 'gheer-inputs']);
});


// ---------------------------------------------------------------------------
// Card 3T -- Coring Formations is a checkbox dropdown
// ---------------------------------------------------------------------------
//
// The VALUE was always a JSON array; only the control was wrong. A native
// <select multiple> needs Ctrl-click for a second pick, which nothing else in
// this application asks for.

function gateDetailWithCoring(selected, options) {
  var detail = detailPayload('business-plan-gate', {
    bp_gate_coring_program: 'Yes',
    bp_gate_coring_formations: selected
  });
  detail.formation_options = options || ['SARH', 'QASM', 'QWRH'];
  return detail;
}

function mountGate(detail, onPatch) {
  var host = fixture('<div id="bpe-main-view"></div><section id="bpe-detail-view" class="hidden"></section>');
  resetBusinessPlanState();
  mockFetch(function (url, options) {
    var path = String(url);
    var method = (options && options.method) || 'GET';
    if (path.indexOf('/field') >= 0 && method === 'PATCH') {
      if (onPatch) onPatch(JSON.parse(options.body));
      return response({ ok: true, detail: detail });
    }
    if (/\/steps\/[a-z-]+(\?|$)/.test(path) && method === 'GET') return response(detail);
    if (path.indexOf('/api/users') >= 0) return response([]);
    throw new Error('Unexpected request: ' + method + ' ' + path);
  });
  return host;
}

test('business-plan calculated BP values render as governed outputs, never editable inputs', async function () {
  var detail = detailPayload('business-plan-gate', {
    bp_gate_calculated_td_ft_md: '1351',
    bp_gate_calculated_drilling_days: '137'
  });
  detail.calculations = {
    bp_gate_calculated_td_ft_md: {
      status: 'calculated',
      formula: 'TD base + SARH thickness at well X/Y + digital elevation at well X/Y'
    },
    bp_gate_calculated_drilling_days: {
      status: 'calculated',
      formula: 'classification baseline + coring uplift when Coring Program is Yes'
    }
  };
  var host = mountGate(detail);
  await openBusinessPlanDetail(7, 'business-plan-gate');

  assert.equal(host.querySelector('[data-bpe-field="bp_gate_calculated_td_ft_md"]'), null);
  assert.equal(host.querySelector('[data-bpe-field="bp_gate_calculated_drilling_days"]'), null);
  assert.equal(host.querySelector('[data-bpe-field="bp_gate_calculated_td_override_reason"]'), null);
  assert.equal(host.querySelector('[data-bpe-output="bp_gate_calculated_td_ft_md"]').textContent, '1351 ft MD');
  assert.equal(host.querySelector('[data-bpe-output="bp_gate_calculated_drilling_days"]').textContent, '137 days');
  assert.match(host.querySelector('.bpe-calculated-output .bpe-field-hint').textContent,
    /SARH thickness/);
});

test('business-plan calculated BP outputs explain unavailable dependencies', async function () {
  var detail = detailPayload('business-plan-gate', {});
  detail.calculations = {
    bp_gate_calculated_td_ft_md: {
      status: 'unavailable', unavailable_reason: 'SARH thickness surface'
    },
    bp_gate_calculated_drilling_days: {
      status: 'unavailable', unavailable_reason: 'well classification'
    }
  };
  var host = mountGate(detail);
  await openBusinessPlanDetail(7, 'business-plan-gate');
  assert.equal(host.querySelector('[data-bpe-output="bp_gate_calculated_td_ft_md"]').textContent,
    'Calculation unavailable');
  assert.match(host.textContent, /Unavailable: SARH thickness surface/);
  assert.match(host.textContent, /Unavailable: well classification/);
});

function coringOptionLabels(host) {
  return Array.prototype.map.call(host.querySelectorAll('#bpe-coring-menu .lf-option'),
    function (option) {
      return [option.getAttribute('data-value'), option.getAttribute('aria-checked')];
    });
}

test('business-plan Coring Formations opens a checkbox list and multi-selects', async function () {
  var patches = [];
  var host = mountGate(gateDetailWithCoring([]), function (body) { patches.push(body.value); });
  await openBusinessPlanDetail(7, 'business-plan-gate');

  var trigger = host.querySelector('#bpe-coring-trigger');
  var menu = host.querySelector('#bpe-coring-menu');
  assert.ok(trigger, 'the native select is gone');
  assert.equal(host.querySelector('select[data-bpe-field="bp_gate_coring_formations"]'), null);
  assert.equal(trigger.getAttribute('aria-labelledby'), 'bpe-coring-label');
  assert.equal(host.querySelector('#bpe-coring-label').textContent.replace('*', ''), 'Coring Formations');
  assert.equal(trigger.querySelector('.lf-value').textContent, 'None selected');
  assert.equal(menu.hidden, true, 'C: closed on arrival');
  assert.equal(menu.getAttribute('role'), 'listbox');
  assert.equal(menu.getAttribute('aria-multiselectable'), 'true');

  trigger.click();
  assert.equal(host.querySelector('#bpe-coring-trigger'), trigger, 'the form was not re-rendered');
  assert.equal(trigger.getAttribute('aria-expanded'), 'true', 'handler ran to completion');
  assert.equal(menu.querySelectorAll('.lf-option').length, 3, 'options were rendered');
  assert.equal(menu.hidden, false);
  // Checkboxes, not radios -- the mark is what says "you may pick several".
  assert.equal(menu.querySelector('.lf-option').getAttribute('role'), 'checkbox');
  assert.ok(menu.querySelector('.lf-mark-box'));
  assert.deepEqual(coringOptionLabels(host),
    [['SARH', 'false'], ['QASM', 'false'], ['QWRH', 'false']]);

  // Clicking an option selects it WITHOUT closing -- three formations should
  // be three clicks, not three trips through the trigger.
  menu.querySelector('[data-value="SARH"]').click();
  assert.equal(host.querySelector('#bpe-coring-menu').hidden, false, 'B: menu stays open across a toggle');
  menu = host.querySelector('#bpe-coring-menu');
  menu.querySelector('[data-value="QWRH"]').click();
  assert.deepEqual(coringOptionLabels(host),
    [['SARH', 'true'], ['QASM', 'false'], ['QWRH', 'true']]);
  assert.equal(host.querySelector('#bpe-coring-trigger .lf-value').textContent, '2 Formations');

  await waitFor(function () { return patches.length === 2; });
  assert.deepEqual(patches, [['SARH'], ['SARH', 'QWRH']], 'the whole array is saved each time');
});

test('business-plan clicking a selected Coring Formation deselects it', async function () {
  var patches = [];
  var host = mountGate(gateDetailWithCoring(['SARH', 'QASM']),
    function (body) { patches.push(body.value); });
  await openBusinessPlanDetail(7, 'business-plan-gate');

  assert.equal(host.querySelector('#bpe-coring-trigger .lf-value').textContent, '2 Formations');
  host.querySelector('#bpe-coring-trigger').click();
  host.querySelector('#bpe-coring-menu [data-value="SARH"]').click();
  assert.deepEqual(coringOptionLabels(host),
    [['SARH', 'false'], ['QASM', 'true'], ['QWRH', 'false']]);
  // One left, so the trigger shows the value itself -- the Assignee filter's
  // own convention.
  assert.equal(host.querySelector('#bpe-coring-trigger .lf-value').textContent, 'QASM');
  await waitFor(function () { return patches.length === 1; });
  assert.deepEqual(patches[0], ['QASM']);
});

test('business-plan a stored formation no longer offered stays listed and stays checked', async function () {
  // Dropping a name from config/lists.yaml must not silently unpick a well
  // that was planned around it.
  var host = mountGate(gateDetailWithCoring(['UNAYZAH', 'SARH'], ['SARH', 'QASM']));
  await openBusinessPlanDetail(7, 'business-plan-gate');
  host.querySelector('#bpe-coring-trigger').click();
  assert.deepEqual(coringOptionLabels(host),
    [['SARH', 'true'], ['QASM', 'false'], ['UNAYZAH', 'true']],
    'the retired value follows the offered ones rather than vanishing');
});

test('business-plan Coring Formations is disabled when the Coring Program is No', async function () {
  var detail = detailPayload('business-plan-gate', {
    bp_gate_coring_program: 'No', bp_gate_coring_formations: ['SARH']
  });
  detail.formation_options = ['SARH', 'QASM'];
  var host = mountGate(detail);
  await openBusinessPlanDetail(7, 'business-plan-gate');
  var trigger = host.querySelector('#bpe-coring-trigger');
  assert.equal(trigger.disabled, true);
  // The historical selection is preserved, not cleared -- it is still what the
  // well was planned with.
  assert.equal(trigger.querySelector('.lf-value').textContent, 'SARH');
});

// ---------------------------------------------------------------------------
// Card 3AB -- only a mapped step shows a folder component
// ---------------------------------------------------------------------------

test('business-plan an unmapped step renders no folder component at all', async function () {
  // The server sends no `folder` for a step the approved mapping omits (Aramco
  // Picks is BP 5, intentionally absent). Nothing renders -- not a blank card,
  // not a disabled one, not a placeholder destination.
  var detail = detailPayload('aramco-approved-pics', {});
  delete detail.folder;
  var host = mountGate(detail);
  await openBusinessPlanDetail(7, 'aramco-approved-pics');
  assert.equal(host.querySelectorAll('.folder-card').length, 0);
  assert.equal(host.textContent.indexOf('N/A'), -1);
  assert.equal(host.textContent.indexOf('Coming'), -1);
});

test('business-plan a mapped step missing a required name says so instead of linking', async function () {
  var detail = detailPayload('quicklook-logs', {});
  detail.folder = {
    requires_folder: 1,
    blocked: 'This step’s folder needs Field, Well Name on the record before it can be opened.',
    path: '', unc_path: '', file_url: ''
  };
  var host = mountGate(detail);
  await openBusinessPlanDetail(7, 'quicklook-logs');
  var card = host.querySelector('.folder-card');
  assert.ok(card.classList.contains('folder-card-blocked'));
  assert.match(card.textContent, /needs Field, Well Name/);
  // Nothing to open and nothing to copy: a half-resolved UNC path points
  // somewhere real and wrong.
  assert.equal(card.querySelector('a'), null);
  assert.equal(card.querySelector('#bpe-copy-folder'), null);
});

// ---------------------------------------------------------------------------
// Card 3X -- the Active Drilling border
// ---------------------------------------------------------------------------
//
// Shown only when the well is FLAGGED and its card sits under Post-Drilling.
// Outside that stage the flag is preserved but the card wears its ordinary
// priority border -- and the border keeps the priority colour either way, so
// the animation never replaces a state signal, it rides one.

test('business-plan only a flagged well under Post-Drilling gets the drilling border', async function () {
  var host = fixture(
    '<div id="bpe-main-view" class="panel"><div class="lead-controls">' +
      '<select id="bp-assignee-filter" hidden></select>' +
      '<select id="bp-field-filter" hidden></select>' +
      '<select id="bp-status-filter" hidden></select>' +
      '<select id="bp-year-filter" hidden></select>' +
      '<div id="bpe-filter-row"></div><div id="bpe-kpis"></div></div>' +
      '<div id="bpe-data-notice" class="hidden"></div>' +
      '<div id="bp-pipeline"></div></div>' +
      '<section id="bpe-detail-view" class="hidden"></section>');
  resetBusinessPlanState();
  var year = new Date().getFullYear();
  function well(id, name, stageKey, drilling, priority) {
    return { project_id: id, project_name: name, field: 'MDFT', business_plan_year: year,
      priority: priority || 'Medium', assignees: [], assignee_label: 'Not Assigned',
      stage_key: stageKey, stage_label: stageKey, items: stageItems(),
      completed_count: 0, progress_percent: 0,
      active_drilling: drilling ? 1 : 0 };
  }
  mockFetch(function (url) {
    var path = String(url);
    if (path.indexOf('/api/business-plan/dashboard') >= 0) {
      return response({
        role: 'supervisor',
        options: { assignees: ['All Assignees'], fields: ['All Fields'],
          statuses: ['All Status'], years: [year] },
        kpis: { rig_inventory_days: 0, rig_target_days: 0, success_rate_pct: null,
          classified_rate: 0, actual_mean_ogip_bcf: 0, simulated_mean_ogip_bcf: 0 },
        data_quality: { missing_simulated_mean_project_ids: [], unsuccessful_with_actual_project_ids: [] },
        out_of_range_years: [],
        wells: [
          well(1, 'DRILLING-NOW', 'post_drilling', true, 'High'),
          well(2, 'FLAGGED-ELSEWHERE', 'pre_drilling', true),
          well(3, 'QUIET', 'post_drilling', false)
        ]
      });
    }
    if (path.indexOf('/api/users') >= 0) return response([]);
    throw new Error('Unexpected request: ' + path);
  });

  await refreshBusinessPlan();
  var animated = Array.prototype.map.call(
    host.querySelectorAll('.lead-card.is-active-drilling .lead-card-name'),
    function (element) { return element.textContent; });
  assert.deepEqual(animated, ['DRILLING-NOW'],
    'flagged AND in Post-Drilling -- not flagged elsewhere, not unflagged here');

  // The priority class is still on the card, so the animation rides the
  // priority colour rather than replacing it.
  var card = host.querySelector('.lead-card.is-active-drilling');
  assert.ok(card.classList.contains('lead-card-high'));
});

// ---------------------------------------------------------------------------
// Card 3I -- the detail shell matches Segment Maturation's
// ---------------------------------------------------------------------------
//
// The BPE page had its own rail head, its own editor head and a summary panel
// missing the maturation card's opening elements. These pin the pieces that
// were structurally different, using the SHARED class names -- if the shells
// drift apart again, they drift here first.

test('business-plan the detail shell uses the maturation shell anatomy', async function () {
  var detail = detailPayload('gheer-inputs', {});
  detail.project.lead_name = 'MDFT-7';
  detail.project.project_name = 'MDFT-7ST2';
  var host = mountGate(detail);
  await openBusinessPlanDetail(7, 'gheer-inputs');

  // Rail head: exactly the maturation shell's visible anatomy -- one back
  // control and one title/priority row, without a BPE-only subtitle or link.
  var rail = host.querySelector('.component-rail .rail-head');
  assert.ok(rail.querySelector('#bpe-back'));
  assert.ok(rail.querySelector('.detail-title-row #bpe-priority-chip'));
  assert.equal(rail.children.length, 2);
  assert.equal(rail.querySelector('#bpe-detail-subtitle'), null);
  assert.equal(rail.querySelector('.bpe-rail-eyebrow'), null);
  assert.equal(rail.querySelector('#bpe-rail-edit-all'), null);

  // Editor head: numbered chip, title, assignment group -- the maturation
  // order, with the rail's own numbering (GHEER Inputs is 3).
  var head = host.querySelector('.component-editor .editor-head');
  assert.equal(head.querySelector('.component-number').textContent, '3');
  assert.ok(head.querySelector('h2'));
  assert.equal(head.querySelector('.bpe-detail-status'), null);
  assert.ok(head.querySelector('#bpe-assignment-group'));

  // Summary panel: progress bar and phase row, which it had neither of.
  var summary = host.querySelector('.summary-panel .ls-card');
  assert.ok(summary.querySelector('.ls-progress .ls-progress-track span'));
  assert.match(summary.querySelector('.summary-phase-label').textContent, /BP Well/);
  assert.equal(summary.querySelector('.summary-phase-well').textContent, 'MDFT-7',
    'the lead name rides opposite the phase, as it does on the maturation card');
});

test('business-plan editor head stays aligned with maturation when tracking is absent', async function () {
  var detail = detailPayload('gheer-inputs', {});
  detail.tracking = [];
  var host = mountGate(detail);
  await openBusinessPlanDetail(7, 'gheer-inputs');
  var head = host.querySelector('.editor-head');
  assert.equal(head.querySelector('.bpe-detail-status'), null);
  assert.ok(head.querySelector('.component-number'));
  assert.ok(head.querySelector('.editor-title'));
  assert.ok(head.querySelector('.assignment-group'));
});

test('business-plan refreshes the Well Summary alone when a saved value changes it', async function () {
  /* Card 3E: the panel reads the RECORD'S VALUES, not just its statuses. The
     page as a whole is rebuilt only when a status moves (rebuilding the editor
     mid-edit is disruptive), so a save that changes only values has to refresh
     this one node -- otherwise the card sits behind the form that produced the
     number. The editor, the focus and the open fold all have to survive it. */
  var host = fixture('<div id="bpe-main-view"></div><section id="bpe-detail-view" class="hidden"></section>');
  var stored = detailPayload('business-plan-gate', {});
  stored.well_summary = {
    fields: { 'SAD Model': { post_drill_piip_gas_p90: 90, post_drill_piip_gas_mean: 116, post_drill_piip_gas_p10: 140 } },
    formations: [], lead_summary: null, derisking: ''
  };
  // The server's answer to the save: the same step, one volume revised. No
  // tracking or stage_items change, so the page's own signature is unmoved.
  var saved = detailPayload('business-plan-gate', { bp_gate_actual_drilling_days: '31.5' });
  saved.well_summary = {
    fields: { 'SAD Model': { post_drill_piip_gas_p90: 90, post_drill_piip_gas_mean: 121, post_drill_piip_gas_p10: 140 } },
    formations: [], lead_summary: null, derisking: ''
  };
  mockFetch(function (url, options) {
    var path = String(url);
    var method = (options && options.method) || 'GET';
    if (path.indexOf('/steps/business-plan-gate/field') >= 0 && method === 'PATCH') {
      return response({ ok: true, detail: saved });
    }
    if (path.indexOf('/steps/business-plan-gate') >= 0) return response(stored);
    if (path.indexOf('/api/users') >= 0) return response([]);
    throw new Error('Unexpected request: ' + method + ' ' + path);
  });

  await openBusinessPlanDetail(7, 'business-plan-gate');
  var means = function () {
    return host.querySelectorAll('.summary-panel .ls-card > .ls-section .ls-col-value')[1].textContent;
  };
  assert.equal(means(), '116');
  // Open a fold and remember the editor node, so we can tell a panel refresh
  // from a whole-page rebuild.
  host.querySelector('#bpe-summary-fold-pva').click();
  var editor = host.querySelector('.component-editor');
  // A plain numeric input, deliberately: a conditional field or a select
  // rebuilds the page by design (it can change which controls exist), and this
  // test is about the save that does NOT.
  var program = host.querySelector('[data-bpe-field="bp_gate_actual_drilling_days"]');
  program.focus();
  program.value = '31.5';
  program.dispatchEvent(new Event('input', { bubbles: true }));

  await waitFor(function () { return means() === '121'; });
  assert.equal(host.querySelector('.component-editor'), editor, 'the editor was not rebuilt');
  assert.equal(document.activeElement, program, 'and the field kept the focus');
  assert.equal(host.querySelector('#bpe-summary-fold-pva').getAttribute('aria-expanded'), 'true',
    'the open fold survives the refresh');
  // The refreshed panel is live, not a detached copy: its gear still opens.
  host.querySelector('#bpe-summary-gear').click();
  assert.equal(host.querySelector('#bpe-summary-menu').classList.contains('hidden'), false);
});

// ---------------------------------------------------------------------------
// The Pre-Drilling column's own BP Gate toggle
// ---------------------------------------------------------------------------
//
// It reaches only the column it sits in and filters from the payload already in
// hand, which is what separates it from the global Step filter: no round trip,
// the other two columns never move, and the KPIs -- computed over the fetched
// population -- never move either.

test('business-plan the BP Gate toggle narrows only the Pre-Drilling column', async function () {
  var host = fixture(
    '<div id="bpe-main-view" class="panel"><div class="lead-controls">' +
      '<select id="bp-assignee-filter" hidden></select>' +
      '<select id="bp-field-filter" hidden></select>' +
      '<select id="bp-status-filter" hidden></select>' +
      '<select id="bp-year-filter" hidden></select>' +
      '<select id="bp-step-filter" hidden></select>' +
      '<div id="bpe-filter-row"></div><div id="bpe-kpis"></div></div>' +
      '<div id="bpe-data-notice" class="hidden"></div>' +
      '<div id="bp-pipeline"></div></div>' +
      '<section id="bpe-detail-view" class="hidden"></section>'
  );
  resetBusinessPlanState();
  var currentYear = new Date().getFullYear();
  function well(id, name, stageKey, gateStatus) {
    return { project_id: id, project_name: name, field: 'MDFT', business_plan_year: currentYear,
      priority: 'Medium', assignees: [], assignee_label: 'Not Assigned', stage_key: stageKey,
      stage_label: stageKey, items: stageItems(), completed_count: 0, progress_percent: 0,
      bp_gate_status: gateStatus };
  }
  var fetches = [];
  mockFetch(function (url) {
    var path = String(url);
    if (path.indexOf('/api/business-plan/dashboard') >= 0) {
      fetches.push(path);
      return response({
        role: 'supervisor',
        options: { assignees: ['All Assignees'], fields: ['All Fields'],
          statuses: ['All Status'], years: [currentYear],
          steps: [{ value: 'all', label: 'All Steps' }] },
        kpis: { rig_inventory_days: 12, rig_target_days: 20, success_rate_pct: 50,
          actual_mean_ogip_bcf: 40, simulated_mean_ogip_bcf: 80 },
        data_quality: { missing_simulated_mean_project_ids: [], unsuccessful_with_actual_project_ids: [] },
        out_of_range_years: [],
        wells: [
          // An approved gate, one waiting on a supervisor, and one still being
          // filled in -- the toggle admits the first two.
          well(1, 'GATE-APPROVED', 'pre_drilling', 'Completed'),
          well(2, 'GATE-PENDING', 'pre_drilling', 'Pending Approval'),
          well(3, 'GATE-OPEN', 'pre_drilling', 'In Progress'),
          well(4, 'DRILLED-1', 'post_drilling', 'Completed')
        ]
      });
    }
    if (path.indexOf('/api/users') >= 0) return response([]);
    throw new Error('Unexpected request: ' + path);
  });

  await refreshBusinessPlan();
  // The board is fetched unnarrowed: the Step filter opens on All Steps, so the
  // gate is not a condition on the request.
  assert.match(fetches[0], /step=all/);
  var toggle = host.querySelector('#bpe-gate-toggle');
  assert.ok(toggle, 'the toggle lives in the Pre-Drilling column header');
  assert.match(toggle.getAttribute('title'), /approved or awaiting approval/,
    'the control says what it filters, since "BP Gate" alone does not');
  assert.equal(host.querySelectorAll('.lead-column')[0].querySelector('#bpe-gate-toggle'), toggle,
    'and only in that one');
  assert.equal(host.querySelectorAll('.lead-column')[1].querySelector('#bpe-gate-toggle'), null);
  assert.equal(toggle.getAttribute('role'), 'switch');

  function columnNames(index) {
    return Array.prototype.map.call(
      host.querySelectorAll('.lead-column')[index].querySelectorAll('.lead-card-name'),
      function (element) { return element.textContent; });
  }
  function columnCount(index) {
    return host.querySelectorAll('.lead-column')[index].querySelector('.lead-column-count').textContent;
  }

  // Selected by default: Pre-Drilling shows the wells whose gate is approved
  // or awaiting approval, and NOT the one still being filled in.
  assert.equal(toggle.getAttribute('aria-checked'), 'true');
  assert.deepEqual(columnNames(0), ['GATE-APPROVED', 'GATE-PENDING']);
  assert.equal(columnCount(0), '2', 'the column count follows the toggle');
  // The other columns are fully populated -- the old global default emptied them.
  assert.deepEqual(columnNames(1), ['DRILLED-1']);

  var kpisBefore = host.querySelector('#bpe-kpis').textContent;
  var fetchesBefore = fetches.length;
  toggle.click();
  assert.equal(host.querySelector('#bpe-gate-toggle').getAttribute('aria-checked'), 'false');
  assert.deepEqual(columnNames(0), ['GATE-APPROVED', 'GATE-PENDING', 'GATE-OPEN']);
  assert.equal(columnCount(0), '3');
  assert.deepEqual(columnNames(1), ['DRILLED-1'], 'the other columns still do not move');
  assert.equal(fetches.length, fetchesBefore, 'toggling repaints from data already in hand');
  assert.equal(host.querySelector('#bpe-kpis').textContent, kpisBefore,
    'global KPIs are not a function of this toggle');
  // Back on, so the next test starts where the board opens.
  host.querySelector('#bpe-gate-toggle').click();
  assert.equal(host.querySelector('#bpe-gate-toggle').getAttribute('aria-checked'), 'true');
});

// ---------------------------------------------------------------------------
// Card 3X -- Active Drilling, on the step page's own gear
// ---------------------------------------------------------------------------

test('business-plan the step gear marks a post-drilling well as actively drilling', async function () {
  var host = fixture('<div id="bpe-main-view"></div><section id="bpe-detail-view" class="hidden"></section>');
  resetBusinessPlanState();
  var stored = detailPayload('business-plan-gate', {});
  stored.project.stage_key = 'post_drilling';
  stored.project.active_drilling_allowed = true;
  stored.project.active_drilling = 0;
  var flagPayloads = [];
  mockFetch(function (url, options) {
    var path = String(url);
    var method = (options && options.method) || 'GET';
    if (path.indexOf('/api/projects/7/flags') >= 0 && method === 'PATCH') {
      flagPayloads.push(JSON.parse(options.body));
      return response({ ok: true });
    }
    if (path.indexOf('/steps/business-plan-gate') >= 0) return response(stored);
    if (path.indexOf('/api/users') >= 0) return response([]);
    throw new Error('Unexpected request: ' + method + ' ' + path);
  });

  await openBusinessPlanDetail(7, 'business-plan-gate');
  var box = host.querySelector('#bpe-active-drilling');
  assert.ok(box, 'the checkbox is in the Well Summary gear menu');
  assert.ok(host.querySelector('#bpe-summary-menu #bpe-active-drilling'));
  assert.equal(box.disabled, false);
  assert.equal(box.checked, false);

  box.checked = true;
  box.dispatchEvent(new Event('change', { bubbles: true }));
  await waitFor(function () { return flagPayloads.length === 1; });
  assert.equal(flagPayloads[0].active_drilling, true,
    'it writes through the same per-well flags endpoint the maturation gear uses');
  // The panel repaints from the new state and the menu closes behind it.
  await waitFor(function () { return host.querySelector('#bpe-active-drilling').checked; });
  assert.equal(host.querySelector('#bpe-summary-menu').classList.contains('hidden'), true);
});

test('business-plan a well outside Post-Drilling cannot be marked as drilling', async function () {
  var host = fixture('<div id="bpe-main-view"></div><section id="bpe-detail-view" class="hidden"></section>');
  resetBusinessPlanState();
  var stored = detailPayload('business-plan-gate', {});
  stored.project.stage_key = 'pre_drilling';
  stored.project.active_drilling_allowed = false;
  stored.project.active_drilling = 0;
  mockFetch(function (url, options) {
    var path = String(url);
    var method = (options && options.method) || 'GET';
    // No PATCH is expected at all: a disabled control makes no request, and
    // this mock throws if one is attempted.
    if (path.indexOf('/steps/business-plan-gate') >= 0) return response(stored);
    if (path.indexOf('/api/users') >= 0) return response([]);
    throw new Error('Unexpected request: ' + method + ' ' + path);
  });

  await openBusinessPlanDetail(7, 'business-plan-gate');
  var box = host.querySelector('#bpe-active-drilling');
  assert.ok(box, 'the control is present but unavailable, not hidden');
  assert.equal(box.disabled, true);
  // The label says WHY rather than greying out silently.
  assert.match(box.closest('label').getAttribute('title'), /Post-Drilling stage/);
  assert.ok(box.closest('label').classList.contains('is-disabled'));
});

test('business-plan the board draws the drilling indicator only under Post-Drilling', async function () {
  var host = fixture(
    '<div id="bpe-main-view" class="panel"><div class="lead-controls">' +
      '<select id="bp-assignee-filter" hidden></select>' +
      '<select id="bp-field-filter" hidden></select>' +
      '<select id="bp-status-filter" hidden></select>' +
      '<select id="bp-year-filter" hidden></select>' +
      '<select id="bp-step-filter" hidden></select>' +
      '<div id="bpe-filter-row"></div><div id="bpe-kpis"></div></div>' +
      '<div id="bpe-data-notice" class="hidden"></div>' +
      '<div id="bp-pipeline"></div></div>' +
      '<section id="bpe-detail-view" class="hidden"></section>'
  );
  resetBusinessPlanState();
  var currentYear = new Date().getFullYear();
  function well(id, name, stageKey, drilling) {
    return { project_id: id, project_name: name, field: 'MDFT', business_plan_year: currentYear,
      priority: 'Medium', assignees: [], assignee_label: 'Not Assigned', stage_key: stageKey,
      stage_label: stageKey, items: stageItems(), completed_count: 0, progress_percent: 0,
      bp_gate_status: 'Completed', active_drilling: drilling };
  }
  mockFetch(function (url) {
    var path = String(url);
    if (path.indexOf('/api/business-plan/dashboard') >= 0) {
      return response({
        role: 'supervisor',
        options: { assignees: ['All Assignees'], fields: ['All Fields'],
          statuses: ['All Status'], years: [currentYear], steps: [{ value: 'all', label: 'All Steps' }] },
        kpis: { rig_inventory_days: 0, rig_target_days: 0, success_rate_pct: null,
          classified_rate: 0, actual_mean_ogip_bcf: 0, simulated_mean_ogip_bcf: 0 },
        data_quality: { missing_simulated_mean_project_ids: [], unsuccessful_with_actual_project_ids: [] },
        out_of_range_years: [],
        wells: [
          well(1, 'DRILLING-NOW', 'post_drilling', 1),
          well(2, 'NOT-DRILLING', 'post_drilling', 0),
          // Flagged, but the flag is preserved rather than drawn outside the
          // stage it means something in.
          well(3, 'FLAGGED-EARLY', 'pre_drilling', 1)
        ]
      });
    }
    if (path.indexOf('/api/users') >= 0) return response([]);
    throw new Error('Unexpected request: ' + path);
  });

  await refreshBusinessPlan();
  var lit = Array.prototype.map.call(host.querySelectorAll('.lead-card.is-active-drilling'),
    function (card) { return card.querySelector('.lead-card-name').textContent; });
  assert.deepEqual(lit, ['DRILLING-NOW']);
});

test('business-plan success rate renders N/A when no classified wells', async function () {
  var host = fixture(
    '<div id="bpe-main-view" class="panel"><div class="lead-controls">' +
      '<select id="bp-assignee-filter" hidden></select>' +
      '<select id="bp-field-filter" hidden></select>' +
      '<select id="bp-status-filter" hidden></select>' +
      '<select id="bp-year-filter" hidden></select>' +
      '<select id="bp-step-filter" hidden></select>' +
      '<div id="bpe-filter-row"></div><div id="bpe-kpis"></div></div>' +
      '<div id="bpe-data-notice" class="hidden"></div>' +
      '<div id="bp-pipeline"></div></div>' +
      '<section id="bpe-detail-view" class="hidden"></section>'
  );
  resetBusinessPlanState();
  var currentYear = new Date().getFullYear();
  mockFetch(function (url) {
    var path = String(url);
    if (path.indexOf('/api/business-plan/dashboard') >= 0) {
      return response({
        role: 'supervisor',
        options: { assignees: ['All Assignees'], fields: ['All Fields'],
          statuses: ['All Status'], years: [currentYear],
          steps: [{ value: 'all', label: 'All Steps' }] },
        kpis: { rig_inventory_days: 0, rig_target_days: 0, success_rate_pct: null,
          classified_rate: 0, actual_mean_ogip_bcf: 0, simulated_mean_ogip_bcf: 0 },
        data_quality: { missing_simulated_mean_project_ids: [], unsuccessful_with_actual_project_ids: [] },
        out_of_range_years: [],
        wells: []
      });
    }
    if (path.indexOf('/api/users') >= 0) return response([]);
    throw new Error('Unexpected request: ' + path);
  });

  await refreshBusinessPlan();
  var kpis = host.querySelector('#bpe-kpis');
  assert.equal(kpis.textContent.indexOf('N/A') >= 0, true,
    'success rate shows N/A when there are no classified wells');
  assert.equal(kpis.querySelectorAll('.kpi-donut').length, 1,
    'the N/A slot still occupies the donut position');
});

test('business-plan success rate renders the donut when classified wells exist', async function () {
  var host = fixture(
    '<div id="bpe-main-view" class="panel"><div class="lead-controls">' +
      '<select id="bp-assignee-filter" hidden></select>' +
      '<select id="bp-field-filter" hidden></select>' +
      '<select id="bp-status-filter" hidden></select>' +
      '<select id="bp-year-filter" hidden></select>' +
      '<select id="bp-step-filter" hidden></select>' +
      '<div id="bpe-filter-row"></div><div id="bpe-kpis"></div></div>' +
      '<div id="bpe-data-notice" class="hidden"></div>' +
      '<div id="bp-pipeline"></div></div>' +
      '<section id="bpe-detail-view" class="hidden"></section>'
  );
  resetBusinessPlanState();
  var currentYear = new Date().getFullYear();
  mockFetch(function (url) {
    var path = String(url);
    if (path.indexOf('/api/business-plan/dashboard') >= 0) {
      return response({
        role: 'supervisor',
        options: { assignees: ['All Assignees'], fields: ['All Fields'],
          statuses: ['All Status'], years: [currentYear],
          steps: [{ value: 'all', label: 'All Steps' }] },
        kpis: { rig_inventory_days: 0, rig_target_days: 0, success_rate_pct: 80,
          classified_rate: 10, actual_mean_ogip_bcf: 0, simulated_mean_ogip_bcf: 0 },
        data_quality: { missing_simulated_mean_project_ids: [], unsuccessful_with_actual_project_ids: [] },
        out_of_range_years: [],
        wells: []
      });
    }
    if (path.indexOf('/api/users') >= 0) return response([]);
    throw new Error('Unexpected request: ' + path);
  });

  await refreshBusinessPlan();
  var kpis = host.querySelector('#bpe-kpis');
  assert.equal(kpis.textContent.indexOf('80%') >= 0, true,
    'success rate shows the computed percentage when classified wells exist');
  assert.equal(kpis.querySelectorAll('.kpi-donut').length, 1);
  assert.equal(kpis.querySelectorAll('.kpi-donut-arc').length, 1,
    'the donut arc is drawn when the rate is a number');
});
