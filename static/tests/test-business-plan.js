import { test, assert, fixture, mockFetch, waitFor } from './harness.js';
import {
  refreshBusinessPlan, openBusinessPlanDetail, businessPlanTestHooks
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
  var host = fixture(
    '<div id="bpe-main-view"><div class="bpe-filter-strip">' +
      '<label>Assignee<select id="bp-assignee-filter"></select></label>' +
      '<label>Field<select id="bp-field-filter"></select></label>' +
      '<label>Status<select id="bp-status-filter"></select></label>' +
      '<label>Business Plan Year<select id="bp-year-filter"></select></label>' +
      '<label>BP Gate<select id="bp-step-filter"></select></label></div>' +
      '<div id="bpe-kpis"></div><div id="bpe-data-notice" class="hidden"></div>' +
      '<div id="bp-pipeline"></div></div><section id="bpe-detail-view" class="hidden"></section>'
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
  mockFetch(function (url) {
    var path = String(url);
    if (path.indexOf('/api/business-plan/dashboard') >= 0) return response(dashboard);
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
  assert.equal(host.querySelectorAll('.bpe-stage').length, 3);
  assert.equal(host.querySelectorAll('.bpe-well-card').length, 1);
  assert.equal(host.querySelectorAll('.bpe-well-card .bpe-tracking-item').length, 6);
  assert.equal(host.querySelectorAll('.bpe-kpi').length, 4);
  assert.equal(host.querySelector('.bpe-kpis, #bpe-kpis').textContent.indexOf('40/80 BCF') >= 0, true);

  host.querySelector('.bpe-tracking-item').click();
  await waitFor(function () { return host.querySelector('.bpe-detail-form'); });
  assert.equal(host.querySelectorAll('#bpe-back').length, 1);
  assert.equal(host.querySelector('#bpe-back').textContent.trim(), 'Back to Business Plan Execution');
  assert.equal(host.querySelectorAll('.bpe-nav-item').length, 14);
  assert.equal(host.querySelectorAll('.bpe-nav-item').length,
    new Set(Array.prototype.map.call(host.querySelectorAll('.bpe-nav-item'), function (button) { return button.textContent; })).size);
  assert.ok(host.querySelector('.bpe-save-line').textContent.indexOf('All changes are saved automatically') >= 0);
  assert.equal(host.textContent.indexOf('Save Updates'), -1);
  assert.deepEqual(Array.prototype.map.call(host.querySelectorAll('.bpe-approval-row button'), function (button) {
    return button.textContent.trim();
  }), ['Return', 'Approve', 'Submit for Approval']);
  assert.ok(host.querySelector('#bpe-summary-gear'));
  assert.ok(host.querySelector('#bpe-edit-all'));
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
  var originalConfirm = window.confirm;
  window.confirm = function () { return true; };
  try {
    host.querySelector('.bpe-remove-flow').click();
    await waitFor(function () { return puts.length === 1; });
    await waitFor(function () { return host.querySelectorAll('.bpe-flow-stage').length === 0; });
  } finally {
    window.confirm = originalConfirm;
  }
  assert.deepEqual(puts[0], []);
  assert.ok(host.querySelector('#bpe-add-flow'), 'the add control remains available');
});
