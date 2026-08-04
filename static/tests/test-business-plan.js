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

function navigation() {
  return [
    { stage_key: 'pre_drilling', stage_label: 'Pre-Drilling', details: [
      { slug: 'business-plan-gate', label: 'Business Plan Execution Gate' },
      { slug: 'well-letters', label: 'Well Letters' },
      { slug: 'gheer-inputs', label: 'GHEER Inputs' }
    ] },
    { stage_key: 'post_drilling', stage_label: 'Post-Drilling', details: [
      { slug: 'quicklook-logs', label: 'Quicklook Logs' },
      { slug: 'aramco-approved-pics', label: 'Aramco Approved PICS' },
      { slug: 'sad-model', label: 'SAD Model' },
      { slug: 'summary-slides', label: 'Summary Slides' },
      { slug: 'post-drill-learning-review', label: 'Post-Drill Learning Review' }
    ] },
    { stage_key: 'post_testing', stage_label: 'Post-Testing', details: [
      { slug: 'flowback-results', label: 'Flowback Results' },
      { slug: 'sad-model-update', label: 'SAD Model Update' },
      { slug: 'final-summary-slides', label: 'Final Summary Slides' },
      { slug: 'final-log-analysis', label: 'Final Log Analysis' },
      { slug: 'structural-mtr', label: 'Structural MTR' },
      { slug: 'pda-booking', label: 'Post-Drilling Analysis & Reserves Booking' }
    ] }
  ];
}

test('business-plan renders the approved dashboard and one auto-save approval detail shell', async function () {
  // The band as index.html builds it: the five selects are the hidden STATE
  // STORE, #bpe-filter-row holds the visible triggers, #bpe-kpis the tiles.
  var host = fixture(
    '<div id="bpe-main-view" class="panel pipeline-panel"><div class="lead-controls">' +
      '<select id="bp-assignee-filter" hidden></select>' +
      '<select id="bp-field-filter" hidden></select>' +
      '<select id="bp-status-filter" hidden></select>' +
      '<select id="bp-year-filter" hidden></select>' +
      '<select id="bp-step-filter" hidden></select>' +
      '<div id="bpe-filter-row" class="lead-filter-row bpe-filter-row"></div>' +
      '<div id="bpe-kpis" class="lead-kpi-row"></div></div>' +
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
    kpis: { rig_inventory_days: 12, rig_target_days: 20, success_rate_pct: 50,
      actual_mean_ogip_bcf: 40, simulated_mean_ogip_bcf: 80 },
    data_quality: { missing_simulated_mean_project_ids: [], unsuccessful_with_actual_project_ids: [] },
    out_of_range_years: [],
    stage_counts: { pre_drilling: 1, post_drilling: 0, post_testing: 0 },
    wells: [{ project_id: 7, project_name: 'MDFT-7', field: 'MDFT', business_plan_year: currentYear,
      priority: 'High', assignees: ['Supervisor'], assignee_label: 'Supervisor', stage_key: 'pre_drilling',
      stage_label: 'Pre-Drilling', items: items, completed_count: 1, progress_percent: 17 }]
  };
  var detail = {
    role: 'supervisor',
    project: { project_id: 7, project_name: 'MDFT-7', field: 'MDFT', business_plan_year: currentYear, priority: 'High' },
    detail: { slug: 'business-plan-gate', label: 'Business Plan Execution Gate', stage_key: 'pre_drilling',
      stage_label: 'Pre-Drilling', task_name: 'BP Execution Gate' },
    task: { task_id: 13, status: 'In Progress' }, assignee: 'Supervisor', values: {},
    comments_key: 'bpe_comments_business_plan_gate', formations: [], flowback_stages: [],
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
  assert.equal(host.querySelector('#bp-step-filter').value, 'business-plan-gate');
  assert.equal(host.querySelectorAll('#bp-year-filter option').length, 37);
  // One visible trigger per hidden select, and the board in the maturation
  // board's own vocabulary: three .lead-column blocks, one .lead-card, its six
  // tracked items as dots.
  assert.equal(host.querySelectorAll('#bpe-filter-row .lf-trigger').length, 5);
  assert.equal(host.querySelectorAll('.lead-column').length, 3);
  assert.equal(host.querySelectorAll('.lead-card').length, 1);
  assert.equal(host.querySelectorAll('.lead-card .lead-dot').length, 6);
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
  fieldMenu.querySelectorAll('.lf-option')[1].click();
  assert.equal(host.querySelector('#bp-field-filter').value, 'MDFT');
  assert.equal(host.querySelector('.lead-filter[data-bp-filter="field"] .lf-menu').hidden, true,
    'a single choice is a finished choice');
  await waitFor(function () { return dashboardRequests.length === 2; });
  assert.equal(host.querySelector('#bpe-filter-row .lf-clear').disabled, false);
  host.querySelector('#bpe-filter-row .lf-clear').click();
  assert.equal(host.querySelector('#bp-field-filter').value, 'All Fields');
  assert.equal(host.querySelector('#bpe-filter-row .lf-clear').disabled, true);
  await waitFor(function () { return dashboardRequests.length === 3; });

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
  assert.equal(host.querySelector('#bp-step-filter').value, 'business-plan-gate');
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
  assert.equal(host.querySelectorAll('.bpe-nav-item').length, 14);
  assert.equal(host.querySelectorAll('.bpe-nav-item').length,
    new Set(Array.prototype.map.call(host.querySelectorAll('.bpe-nav-item'), function (button) { return button.textContent; })).size);
  // The detail page is the maturation detail shell: rail (three stage blocks,
  // the open step's one marked active) | editor | Well Summary card.
  assert.equal(host.querySelectorAll('.detail-shell.detail-shell-lead').length, 1);
  assert.equal(host.querySelectorAll('.component-rail .rail-stage-lead').length, 3);
  assert.equal(host.querySelectorAll('.rail-stage-lead.is-active').length, 1);
  assert.equal(host.querySelectorAll('.component-item.active').length, 1);
  assert.equal(host.querySelector('.component-item.active').getAttribute('data-detail-slug'), 'business-plan-gate');
  assert.equal(host.querySelectorAll('.component-editor.bpe-detail-form').length, 1);
  // Form primitives are the house ones (Well Classification is the radio
  // group; the gate's slides confirmation is the checkbox card).
  assert.equal(host.querySelectorAll('.bpe-detail-form .radio-group .radio-option').length, 3);
  assert.ok(host.querySelector('.bpe-detail-form .check-label'));
  assert.ok(host.querySelector('.folder-card #bpe-copy-folder'));
  assert.ok(host.querySelector('#bpe-save-feedback').classList.contains('save-state'));
  // The Well Summary is the Lead Summary card's anatomy, and its progress bar
  // is progressPercent() over the stage's own items (1 of 6 completed).
  assert.equal(host.querySelectorAll('.summary-panel .ls-card').length, 1);
  assert.equal(host.querySelector('.ls-title').textContent, 'Well Summary');
  assert.equal(host.querySelector('.ls-progress-figures').textContent, '17%1 / 6');
  assert.deepEqual(Array.prototype.map.call(host.querySelectorAll('.ls-col-value'), function (cell) {
    return cell.textContent;
  }), ['MDFT-7', 'MDFT', String(currentYear)]);
  assert.equal(host.querySelectorAll('.ls-items .lead-dot').length, 6);
  assert.ok(host.querySelector('.bpe-save-line').textContent.indexOf('All changes are saved automatically') >= 0);
  assert.equal(host.textContent.indexOf('Save Updates'), -1);
  assert.deepEqual(Array.prototype.map.call(host.querySelectorAll('.bpe-approval-row button'), function (button) {
    return button.textContent.trim();
  }), ['Return', 'Approve', 'Submit for Approval']);
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
  return {
    role: 'supervisor',
    project: { project_id: 7, project_name: 'MDFT-7', field: 'MDFT', business_plan_year: 2027, priority: 'High' },
    detail: { slug: slug, label: label, stage_key: slug === 'flowback-results' ? 'post_testing' : 'pre_drilling',
      stage_label: stage, task_name: slug === 'flowback-results' ? 'Flowback Results' : 'BP Execution Gate' },
    task: { task_id: 13, status: 'In Progress' }, assignee: 'Supervisor', values: values || {},
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

test('business-plan serializes auto-saves and a stale response cannot replace newer input', async function () {
  var host = fixture('<div id="bpe-main-view"></div><section id="bpe-detail-view" class="hidden"></section>');
  var base = detailPayload('business-plan-gate', {});
  // An unrecorded value is a DASH in the Well Summary, never a blank cell.
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
  assert.equal(host.querySelectorAll('.ls-col-value')[2].textContent, '—',
    'a missing Business Plan Year reads as a dash, not a blank');
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
  assert.equal(host.querySelectorAll('.bpe-flow-stage').length, 1);
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
