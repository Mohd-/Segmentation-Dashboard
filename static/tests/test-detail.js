// Tests for static/js/views/detail.js — the LEAD-LEVEL priority chip and the
// Well Summary's two-decimal reservoir-property formatting.
//
// Priority is a record attribute (stored projects.priority, delivered on the
// detail payload as project.priority): ONE chip beside the record name in the
// shell header, for both the lead and BP well shells. A supervisor cycles it
// Low → Medium → High → Low through PATCH /api/projects/<id>/priority;
// everyone else sees a static, disabled chip.
import { test, assert, fixture, mockFetch } from './harness.js';
import { renderLeadPriorityChip, cycleLeadPriorityChip, nextLeadPriority, fmt2, renderRightPanel } from '../js/views/detail.js';
import { Store } from '../js/state.js';

// The chip exactly as index.html ships it (hidden until a record renders).
var CHIP =
  '<div class="detail-title-row"><h3 id="detail-name"></h3>' +
  '<button id="lead-priority-chip" type="button" class="priority priority-low lead-priority-chip hidden" title="Priority: Low">Low</button></div>';

function chip() { return document.getElementById('lead-priority-chip'); }

function withStore(fn) {
  var saved = {
    user: Store.user, projectId: Store.projectId, project: Store.project,
    tasks: Store.tasks, task: Store.task, allFields: Store.allFields,
    leadSummary: Store.leadSummary, overview: Store.overview,
    formations: Store.formations, pipeline: Store.pipeline
  };
  try { return fn(); } finally {
    Object.keys(saved).forEach(function (key) { Store[key] = saved[key]; });
  }
}

// Same shape, but restores AFTER the returned promise settles.
function withStoreAsync(fn) {
  var saved = {
    user: Store.user, projectId: Store.projectId, project: Store.project,
    tasks: Store.tasks, task: Store.task, allFields: Store.allFields,
    leadSummary: Store.leadSummary, overview: Store.overview,
    formations: Store.formations, pipeline: Store.pipeline
  };
  return Promise.resolve().then(fn).finally(function () {
    Object.keys(saved).forEach(function (key) { Store[key] = saved[key]; });
  });
}

test('detail lead-priority chip renders from project.priority and is revealed', function () {
  withStore(function () {
    fixture(CHIP);
    Store.user = { name: 'Supervisor', role: 'supervisor' };
    Store.project = { project_id: 9, priority: 'High' };
    renderLeadPriorityChip();
    assert.equal(chip().textContent, 'High');
    assert.ok(chip().classList.contains('priority'), 'keeps the shared chip class');
    assert.ok(chip().classList.contains('priority-high'), 'variant class follows the value');
    assert.ok(!chip().classList.contains('hidden'), 'rendering reveals the chip');
    assert.equal(chip().disabled, false, 'a supervisor can click it');
    assert.match(chip().title, /click to change/);
  });
});

test('detail lead-priority chip falls back to Low when the record has none', function () {
  withStore(function () {
    fixture(CHIP);
    Store.user = { name: 'Supervisor', role: 'supervisor' };
    Store.project = { project_id: 9 };
    renderLeadPriorityChip();
    assert.equal(chip().textContent, 'Low');
    assert.ok(chip().classList.contains('priority-low'));
  });
});

test('detail lead-priority chip is static for a non-supervisor', function () {
  withStore(function () {
    fixture(CHIP);
    Store.user = { name: 'Employee', role: 'employee' };
    Store.project = { project_id: 9, priority: 'Medium' };
    renderLeadPriorityChip();
    assert.equal(chip().disabled, true, 'not a toggle for an employee');
    assert.ok(chip().classList.contains('lead-priority-chip-static'));
    assert.equal(chip().textContent, 'Medium', 'the value still reads');
    assert.match(chip().title, /set by a supervisor/);
  });
});

test('detail lead-priority cycle order is Low -> Medium -> High -> Low', function () {
  assert.equal(nextLeadPriority('Low'), 'Medium');
  assert.equal(nextLeadPriority('Medium'), 'High');
  assert.equal(nextLeadPriority('High'), 'Low');
  // Absent/unknown values read Low, so the first click escalates to Medium.
  assert.equal(nextLeadPriority(undefined), 'Medium');
  assert.equal(nextLeadPriority('Bogus'), 'Low');
});

test('detail cycleLeadPriorityChip PATCHes the project priority endpoint with the next value', function () {
  return withStoreAsync(function () {
    fixture(CHIP);
    var calls = [];
    mockFetch(function (url, options) {
      calls.push({ url: String(url), options: options || {} });
      // The record refresh that follows the PATCH will fail on the missing
      // detail-shell DOM; that path is views/detail.js's own error toast and
      // is not under test here — the request contract is.
      return {
        ok: true, status: 200,
        headers: { get: function () { return 'application/json'; } },
        json: function () { return Promise.resolve({ ok: true, priority: 'High' }); },
        text: function () { return Promise.resolve('{}'); }
      };
    });
    Store.user = { name: 'Supervisor', role: 'supervisor' };
    Store.projectId = 9;
    Store.project = { project_id: 9, priority: 'Medium' };
    return cycleLeadPriorityChip().then(function () {
      var patches = calls.filter(function (call) { return call.url.indexOf('/api/projects/9/priority') >= 0; });
      assert.equal(patches.length, 1, 'exactly one priority PATCH');
      assert.equal(patches[0].options.method, 'PATCH');
      var body = JSON.parse(patches[0].options.body);
      assert.equal(body.priority, 'High', 'Medium cycles to High');
      assert.equal(body.changed_by, 'Supervisor');
    });
  });
});

test('detail cycleLeadPriorityChip is a no-op for a non-supervisor', function () {
  return withStoreAsync(function () {
    fixture(CHIP);
    var calls = [];
    mockFetch(function (url) { calls.push(String(url)); throw new Error('no request expected'); });
    Store.user = { name: 'Employee', role: 'employee' };
    Store.projectId = 9;
    Store.project = { project_id: 9, priority: 'Medium' };
    return cycleLeadPriorityChip().then(function () {
      assert.equal(calls.length, 0, 'nothing is sent');
    });
  });
});

/* Reservoir Properties are read to the hundredth -- a water saturation of 0.92
   or a porosity of 21.35 loses a digit that matters under fmtNum's single
   decimal, which is why this card has its own formatter. Percentages are NOT
   converted: the value is shown exactly as it is stored and entered. */
test('detail fmt2 renders reservoir properties to two decimals', function () {
  assert.equal(fmt2(0.92), '0.92');
  assert.equal(fmt2('21.352'), '21.35');
  assert.equal(fmt2(74), '74.00', 'a whole number still shows both places');
  assert.equal(fmt2('20.8'), '20.80');
  // Rounds, never truncates.
  assert.equal(fmt2(9.999), '10.00');
});

test('detail fmt2 leaves blanks and non-numbers to the caller', function () {
  assert.equal(fmt2(''), '', 'blank stays blank so the caller can render its dash');
  assert.equal(fmt2(null), '');
  assert.equal(fmt2(undefined), '');
  assert.equal(fmt2('n/a'), 'n/a', 'text passes through untouched rather than becoming NaN');
});

/* ---------------------------------------------------------------------------
   Card 3E -- the Well Summary's content contract
   ---------------------------------------------------------------------------
   These drive renderRightPanel over a Store shaped like a drilled BP well, so
   they pin what the card SHOWS rather than how any one helper formats. */

function mountWellCard(fields, leadSnapshot) {
  var host = fixture(
    '<div id="summary-card-head" class="hidden"><h3 id="summary-title"></h3>' +
    '<button id="summary-settings-toggle"></button></div>' +
    '<div id="lead-summary"></div>');
  Store.user = { name: 'Supervisor', role: 'supervisor' };
  Store.projectId = 42;
  Store.pipeline = 'bp';
  Store.project = { project_id: 42, project_name: 'MDFT-9', pipeline_type: 'bp',
    business_plan_enabled: 1, business_plan_year: 2027, priority: 'Medium' };
  Store.allFields = fields || {};
  Store.leadSummary = leadSnapshot ? { fields: leadSnapshot } : null;
  Store.overview = {};
  renderRightPanel([{ status: 'Approved' }, { status: 'In Progress' }]);
  return host;
}

function foldTitles(host) {
  return Array.prototype.map.call(host.querySelectorAll('.summary-fold-title'),
    function (element) { return element.textContent; });
}
// The card's blocks are .ls-section now -- the Lead Summary card's own
// anatomy, which is what "consistent with the lead summary card" means.
// Scoped to the card's OWN blocks: the folds' bodies use the same anatomy, so
// an unscoped query would count what is inside them too.
function sectionTitles(host) {
  return Array.prototype.map.call(
    host.querySelectorAll('#lead-summary > .ls-section > .ls-section-title'),
    function (element) { return element.textContent; });
}
function metricPairs(host, sectionTitle) {
  var section = Array.prototype.filter.call(host.querySelectorAll('.summary-section'),
    function (element) {
      var title = element.querySelector('.ls-section-title');
      return title && title.textContent === sectionTitle;
    })[0];
  if (!section) return null;
  return Array.prototype.map.call(section.querySelectorAll('.summary-metric'), function (row) {
    return [row.querySelector('.summary-metric-label span').textContent,
      row.querySelector('.summary-metric-value').textContent];
  });
}


// The card asks for these rows "when values exist" -- so nothing entered at
// the gate means no section at all, not a section full of dashes.


test('detail the well card carries exactly two folds, both collapsed on arrival', function () {
  var host = mountWellCard({});
  assert.deepEqual(foldTitles(host), ['Simulated Vs Actual Delta', 'Lead Summary']);
  Array.prototype.forEach.call(host.querySelectorAll('.summary-fold-head'), function (head) {
    assert.equal(head.getAttribute('aria-expanded'), 'false');
  });
  Array.prototype.forEach.call(host.querySelectorAll('.summary-fold-body'), function (body) {
    assert.ok(body.classList.contains('collapsed'));
  });
});

test('detail the delta fold compares Area bound against matching bound', function () {
  // Area is a P90/P10 PAIR on both sides with no mean between them, so each
  // bound meets its own counterpart. Averaging them into a single "area" would
  // invent a number the data does not carry.
  var host = mountWellCard({
    'Lead Assessment': { p90_area_km2: '4', p10_area_km2: '10' },
    'SAD Model': { sad_area_km2_p90: '5', sad_area_km2_p10: '9' }
  });
  var rows = Array.prototype.map.call(host.querySelectorAll('.summary-pva-row'), function (row) {
    return [row.querySelector('.summary-pva-label').textContent,
      row.querySelectorAll('.summary-pva-cell')[0].textContent,
      row.querySelectorAll('.summary-pva-cell')[1].textContent,
      row.querySelector('.summary-pva-delta').textContent];
  });
  var area = rows.filter(function (row) { return row[0].indexOf('Area') === 0; });
  assert.deepEqual(area, [
    ['Area P90 (km²)', '4', '5', 'Δ +1'],
    ['Area P10 (km²)', '10', '9', 'Δ -1']
  ]);
});

test('detail SAD Update supersedes SAD Model as the actual area', function () {
  var host = mountWellCard({
    'Lead Assessment': { p90_area_km2: '4' },
    'SAD Model': { sad_area_km2_p90: '5' },
    'SAD Update': { sad_update_area_km2_p90: '7' }
  });
  var row = Array.prototype.filter.call(host.querySelectorAll('.summary-pva-row'), function (element) {
    var label = element.querySelector('.summary-pva-label');
    return label && label.textContent === 'Area P90 (km²)';
  })[0];
  assert.equal(row.querySelectorAll('.summary-pva-cell')[1].textContent, '7');
});

test('detail porosity and water saturation print bare, to two decimals', function () {
  // Card 3E: no % sign and no unit conversion -- the stored value, rounded at
  // presentation only. Pay thickness keeps its unit; it is a length.
  var host = mountWellCard({
    'Quicklook Logs': {},
    formations: {}
  });
  Store.formations = [{ formation: 'SARH', phase: 'final', pay_ft: '60.5',
    porosity_pct: '8.523', swt_pct: '35', thickness_ft: '120' }];
  renderRightPanel([{ status: 'Approved' }]);
  var cells = Array.prototype.map.call(
    host.querySelectorAll('.summary-props-row:not(.summary-props-row-empty) span'),
    function (element) { return element.textContent; });
  assert.deepEqual(cells, ['SARH', '60.50 ft', '8.52', '35.00'],
    'two decimals, no percent sign, no conversion');
});

test('detail Well Summary projects BPE pay intervals and generic Flowback stages', function () {
  var host = mountWellCard({
    'Flowback Results': { flowback_stages_rows: JSON.stringify([
      { formation: 'SARH', top_md: '11200', base_md: '11450' },
      { formation: 'SARH', gas_rate_mmscfd: '6.1', liquid_rate_bpd: '325',
        choke_size_in: '0.5', fwhp_psi: '2100' }
    ]) }
  });
  Store.formations = [{
    formation: 'SARH', phase: 'quicklook', top_tvdss_ft: '1000', base_tvdss_ft: '1120',
    thickness_ft: '120', pay_intervals: [
      { top_tvdss_ft: '1010', base_tvdss_ft: '1050', phit_pct: '10', swt_pct: '30', fluid: 'Oil' },
      { top_tvdss_ft: '1050', base_tvdss_ft: '1110', phit_pct: '20', swt_pct: '50', fluid: 'Oil' }
    ]
  }];
  renderRightPanel([{ status: 'Approved' }]);
  var cells = Array.prototype.map.call(
    host.querySelectorAll('.summary-props-row:not(.summary-props-row-empty) span'),
    function (element) { return element.textContent; });
  assert.deepEqual(cells, ['SARH', '100.00 ft', '16.00', '42.00']);
  assert.deepEqual(metricPairs(host, 'Flowback Results'), [
    ['Liquid Rate', '325 BPD'], ['Flowing Wellhead Pressure (FWHP)', '2100 psi'], ['Choke Size', '0.5 in']
  ]);
});

test('detail Well Summary retains Reservoir Area Definition for legacy snapshots', function () {
  var host = mountWellCard({
    'Reservoir Area Definition': { p90_area_km2: '4', p10_area_km2: '10' },
    'SAD Model': { sad_area_km2_p90: '5', sad_area_km2_p10: '9' }
  });
  var rows = Array.prototype.map.call(host.querySelectorAll('.summary-pva-row'), function (row) {
    return [row.querySelector('.summary-pva-label').textContent,
      row.querySelectorAll('.summary-pva-cell')[0].textContent];
  });
  assert.deepEqual(rows.filter(function (row) { return row[0].indexOf('Area') === 0; }), [
    ['Area P90 (km²)', '4'], ['Area P10 (km²)', '10']
  ]);
});

test('detail the well card is built from the Lead Summary card anatomy', function () {
  // "Consistent with the segment maturation lead summary card" is a structural
  // claim, not a resemblance: the blocks ARE .ls-section with .ls-section-title
  // headings, and the Gas trio is the same .ls-grid of label-over-value columns
  // the lead card uses for its own trios.
  var host = mountWellCard({
    'SAD Model': { post_drill_piip_gas_p90: '12', post_drill_piip_gas_mean: '20',
                   post_drill_piip_gas_p10: '31' }
  });
  assert.deepEqual(sectionTitles(host),
    ['Gas (BCF)', 'Flowback Results', 'Reservoir Properties']);

  var gas = host.querySelectorAll('#lead-summary > .ls-section')[0];
  assert.deepEqual(Array.prototype.map.call(gas.querySelectorAll('.ls-col-label'),
    function (element) { return element.textContent; }), ['P90', 'Mean', 'P10']);
  assert.deepEqual(Array.prototype.map.call(gas.querySelectorAll('.ls-col-value'),
    function (element) { return element.textContent; }), ['12', '20', '31']);
});

test('detail the two expandable sections are the last thing on the card', function () {
  var host = mountWellCard({});
  var blocks = host.querySelectorAll('#lead-summary > .ls-section, #lead-summary > .summary-fold');
  var last = Array.prototype.slice.call(blocks, -2);
  assert.deepEqual(last.map(function (element) {
    return element.querySelector('.summary-fold-title').textContent;
  }), ['Simulated Vs Actual Delta', 'Lead Summary']);
});
