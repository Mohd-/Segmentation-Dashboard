/* Tests for Card 2A — the shared LEAD SUMMARY component
   (static/js/views/lead-summary.js) and the three-stage lead sidebar's grouping
   (leadStageGroups, static/js/views/detail.js).

   leadSummaryHtml is PURE by contract: every test below hands it a plain object
   and asserts on the string, with no Store, no fetch and no app boot. The
   wiring tests mount the rendered markup in a fixture and drive the gear the
   way a user does (click, Escape, click outside). */
import { test, assert, fixture } from './harness.js';
import {
  leadSummaryHtml, wireLeadSummary, closeLeadSummaryMenu, progressPercent, EM_DASH
} from '../js/views/lead-summary.js';
import { leadStageGroups } from '../js/views/detail.js';

// A fully populated lead, so a test can knock out ONE value and see only that
// cell change.
function fullData(overrides) {
  return Object.assign({
    progress: { completed: 6, total: 12 },
    gas: { p90: '3.7', mean: '10.4', p10: '19' },
    liquid: { p90: '1.2', mean: '3.4', p10: '5.6' },
    thickness: { formation: '500', reservoir: '200' },
    area: { p90: '10', p10: '17.3' },
    cos: { reservoir: '91', trap: '95', seal: '', total: '' },
    block: 'Block A',
    ar: '2525',
    canManage: true
  }, overrides || {});
}

function render(data) {
  return fixture(leadSummaryHtml(data));
}

// Section titles, in rendered order.
function sectionTitles(root) {
  return Array.prototype.map.call(root.querySelectorAll('.ls-section-title'),
    function (el) { return el.textContent; });
}
// One section's [label, value] pairs.
function sectionCells(root, title) {
  var section = Array.prototype.filter.call(root.querySelectorAll('.ls-section'), function (el) {
    return el.querySelector('.ls-section-title').textContent === title;
  })[0];
  return Array.prototype.map.call(section.querySelectorAll('.ls-col'), function (col) {
    return [col.querySelector('.ls-col-label').textContent, col.querySelector('.ls-col-value').textContent];
  });
}

/* -------------------------------------------------------------------------
   Content and order
   ------------------------------------------------------------------------- */

test('lead-summary renders the contract sections, in order, for a condensate lead', function () {
  var root = render(fullData());
  assert.equal(root.querySelector('.ls-title').textContent, 'Lead Summary');
  assert.deepEqual(sectionTitles(root),
    ['Gas (BCF)', 'Liquid (MMSTB)', 'Thickness (ft)', 'Reservoir Area (km²)', 'Chance of Success (%)']);
  assert.deepEqual(sectionCells(root, 'Gas (BCF)'), [['P90', '3.7'], ['Mean', '10.4'], ['P10', '19']]);
  assert.deepEqual(sectionCells(root, 'Thickness (ft)'), [['Formation', '500'], ['Reservoir', '200']]);
  assert.deepEqual(sectionCells(root, 'Reservoir Area (km²)'), [['P90', '10'], ['P10', '17.3']]);
});

test('lead-summary hides the WHOLE Liquid section when the lead has no condensate', function () {
  var root = render(fullData({ liquid: null }));
  assert.deepEqual(sectionTitles(root),
    ['Gas (BCF)', 'Thickness (ft)', 'Reservoir Area (km²)', 'Chance of Success (%)'],
    'the sections below simply reflow up — no empty placeholder, no dashes');
  assert.equal(root.textContent.indexOf('MMSTB'), -1);
});

test('lead-summary renders an em dash for every unavailable value, never a blank', function () {
  var root = render({});   // nothing known at all
  var values = Array.prototype.map.call(root.querySelectorAll('.ls-col-value'),
    function (el) { return el.textContent; });
  assert.equal(values.length, 11, 'four sections: 3 gas + 2 thickness + 2 area + 4 CoS');
  assert.ok(values.every(function (text) { return text === EM_DASH; }), 'all em dashes: ' + values.join(','));
  assert.equal(root.querySelector('.ls-footer').textContent.trim(), EM_DASH);
});

test('lead-summary dashes only the MISSING chance-of-success cells', function () {
  var root = render(fullData());
  assert.deepEqual(sectionCells(root, 'Chance of Success (%)'),
    [['RES.', '91'], ['Trap', '95'], ['Seal', EM_DASH], ['Total', EM_DASH]]);
});

test('lead-summary footer reads the saved block and AR, dashing either half', function () {
  assert.match(render(fullData()).querySelector('.ls-footer').textContent, /Block A/);
  assert.match(render(fullData()).querySelector('.ls-footer').textContent, /AR-2525/);
  var noAr = render(fullData({ ar: '' })).querySelector('.ls-footer').textContent;
  assert.match(noAr, /Block A/);
  assert.match(noAr, new RegExp(EM_DASH));
});

/* -------------------------------------------------------------------------
   Progress — one formula, shared with the board
   ------------------------------------------------------------------------- */

test('lead-summary progress is completed tracked items over twelve', function () {
  assert.equal(progressPercent({ completed: 6, total: 12 }), 50);
  assert.equal(progressPercent({ completed: 0, total: 12 }), 0);
  assert.equal(progressPercent({ completed: 12, total: 12 }), 100);
  assert.equal(progressPercent({ completed: 1, total: 12 }), 8, 'rounded once, at the end');
});

test('lead-summary progress can never divide by zero or exceed the limit', function () {
  assert.equal(progressPercent({ completed: 3, total: 0 }), 0);
  assert.equal(progressPercent(null), 0);
  assert.equal(progressPercent({ completed: 99, total: 12 }), 100, 'a malformed payload cannot pass 100%');
});

test('lead-summary prints the raw count beside the percentage', function () {
  var root = render(fullData());
  assert.equal(root.querySelector('.ls-progress-figures b').textContent, '50%');
  assert.equal(root.querySelector('.ls-progress-figures small').textContent, '6 / 12');
});

/* -------------------------------------------------------------------------
   The gear menu — exactly three items, closed by default
   ------------------------------------------------------------------------- */

function mountCard(data) {
  var root = render(data || fullData());
  wireLeadSummary({
    onEditAll: function () { root.dataset.fired = 'edit'; },
    onRename: function () { root.dataset.fired = 'rename'; },
    onDelete: function () { root.dataset.fired = 'delete'; }
  });
  return root;
}
function menu() { return document.getElementById('lead-summary-menu'); }
function gear() { return document.getElementById('lead-summary-gear'); }
function isOpen() { return !menu().classList.contains('hidden'); }
function clickGear() { gear().dispatchEvent(new MouseEvent('click', { bubbles: true })); }

test('lead-summary gear offers EXACTLY Edit All Inputs / Rename Lead / Delete Lead', function () {
  var root = mountCard();
  var labels = Array.prototype.map.call(root.querySelectorAll('.ls-menu-item'),
    function (el) { return el.textContent; });
  assert.deepEqual(labels, ['Edit All Inputs', 'Rename Lead', 'Delete Lead']);
  // The two relocated controls must not survive anywhere in the lead card.
  assert.equal(root.textContent.indexOf('Active Well'), -1, 'Active Well relocated to the well panel');
  assert.equal(root.textContent.indexOf('Promote'), -1, 'Promote to BP Well relocated to the well panel');
});

test('lead-summary gear is closed at render and toggles on click', function () {
  mountCard();
  assert.equal(isOpen(), false, 'closed by default');
  assert.equal(gear().getAttribute('aria-expanded'), 'false');
  clickGear();
  assert.ok(isOpen());
  assert.equal(gear().getAttribute('aria-expanded'), 'true');
  clickGear();
  assert.equal(isOpen(), false);
});

test('lead-summary gear closes on Escape and on an outside click', function () {
  mountCard();
  clickGear();
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  assert.equal(isOpen(), false, 'Escape dismisses');
  clickGear();
  document.body.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  assert.equal(isOpen(), false, 'an outside click dismisses');
});

test('lead-summary gear items run their action and close the menu first', function () {
  var root = mountCard();
  clickGear();
  document.getElementById('lead-summary-rename').dispatchEvent(new MouseEvent('click', { bubbles: true }));
  assert.equal(root.dataset.fired, 'rename');
  assert.equal(isOpen(), false, 'a confirm dialog never opens behind a menu still hanging open');
});

test('lead-summary gear is disabled — and every item with it — in a reference view', function () {
  var root = mountCard(fullData({ canManage: false }));
  assert.ok(gear().disabled);
  assert.ok(Array.prototype.every.call(root.querySelectorAll('.ls-menu-item'),
    function (el) { return el.disabled; }));
  closeLeadSummaryMenu();
});

test('lead-summary carries no collapse affordance at all', function () {
  var root = mountCard();
  assert.equal(root.querySelectorAll('.summary-fold, .summary-fold-head, .ls-fold').length, 0);
  assert.equal(root.querySelectorAll('[aria-expanded="true"]').length, 0);
});

/* -------------------------------------------------------------------------
   The three-stage sidebar's grouping (pure)
   ------------------------------------------------------------------------- */

// The server's twelve tracked items, abbreviated to what the sidebar reads.
var ITEMS = [
  { stage: 'Lead Assessment', label: 'Area Definition', status: 'Completed', steps: ['Area Definition'] },
  { stage: 'Lead Assessment', label: 'Thickness Estimation', status: 'Completed', steps: ['Thickness Estimation'] },
  { stage: 'Lead Assessment', label: 'GRV Inputs', status: 'In Progress', steps: ['GRV Inputs'] },
  { stage: 'Lead Assessment', label: 'Resource Assessment', status: 'In Progress', steps: ['Resource Assessment'] },
  { stage: 'Risk Analysis', label: 'Reservoir', status: 'In Progress', steps: ['Reservoir CoS'] },
  { stage: 'Risk Analysis', label: 'Trap and Seal', status: 'In Progress', steps: ['Trap and Seal CoS'] },
  { stage: 'Risk Analysis', label: 'Seismic Validation', status: 'In Progress', steps: ['Seismic Signature Validation'] },
  { stage: 'Risk Analysis', label: 'Segmentation Slides', status: 'Pending Approval', steps: ['Segmentation Slides'] },
  { stage: 'Pre-Well Delivery', label: 'Moving Tolerance', status: 'In Progress', steps: ['Moving Tolerance'] },
  { stage: 'Pre-Well Delivery', label: 'Approval to Stake', status: 'In Progress', steps: ['Approval to Stake'] },
  { stage: 'Pre-Well Delivery', label: 'Well Site Location', status: 'In Progress', steps: ['Well Site Location'] },
  { stage: 'Pre-Well Delivery', label: 'GeoX Assessment', status: 'In Progress', steps: ['Pre-Drilling GeoX Assessment'] }
];
// The stored 12-step prospect pipeline (v5), in sequence order. The stage
// group IS the sidebar heading now -- no display mapping.
var TASKS = [
  'Area Definition', 'Thickness Estimation', 'GRV Inputs', 'Resource Assessment',
  'Reservoir CoS', 'Trap and Seal CoS', 'Seismic Signature Validation',
  'Segmentation Slides', 'Moving Tolerance', 'Approval to Stake',
  'Well Site Location', 'Pre-Drilling GeoX Assessment'
].map(function (name, index) {
  return { task_id: index + 1, task_name: name, sequence_no: index + 1, status: 'Not Assigned',
           stage_group: index < 4 ? 'Lead Assessment'
             : index < 8 ? 'Risk Analysis' : 'Pre-Well Delivery' };
});

test('lead sidebar groups into exactly the three display stages, four items each', function () {
  var groups = leadStageGroups(ITEMS, TASKS);
  assert.deepEqual(groups.map(function (g) { return g.stage; }),
    ['Lead Assessment', 'Risk Analysis', 'Pre-Well Delivery']);
  assert.deepEqual(groups.map(function (g) { return g.total; }), [4, 4, 4]);
});

test('lead sidebar counts only COMPLETED tracked items toward x/4', function () {
  var groups = leadStageGroups(ITEMS, TASKS);
  assert.deepEqual(groups.map(function (g) { return g.done; }), [2, 0, 0],
    'Pending Approval is work still open, exactly as the board KPI treats it');
});

test('lead sidebar renders Trap and Seal as the ONE merged step, clickable', function () {
  var risk = leadStageGroups(ITEMS, TASKS)[1];
  var labels = risk.rows.map(function (row) { return row.label; });
  assert.ok(labels.indexOf('Trap and Seal CoS') >= 0,
    'the merged step is what the row opens: ' + labels.join(','));
  assert.equal(labels.indexOf('Trap CoS'), -1, 'the retired halves are gone');
  assert.equal(labels.indexOf('Seal CoS'), -1, 'the retired halves are gone');
  risk.rows.forEach(function (row) {
    assert.ok(row.task, row.label + ' resolves to a real task');
  });
});

test('lead sidebar has ZERO dimmed rows: every tracked item is a real step', function () {
  var groups = leadStageGroups(ITEMS, TASKS);
  var future = [];
  var rows = 0;
  groups.forEach(function (group) {
    group.rows.forEach(function (row) { rows += 1; if (!row.task) future.push(row.label); });
  });
  assert.deepEqual(future, [], 'v5 gave GRV Inputs and Well Site Location real steps');
  assert.equal(rows, 12, 'twelve items, twelve openable rows');
});

test('lead sidebar dims a step the record does not actually carry', function () {
  // Defensive path only: a legacy row a migration could not reach. The item
  // still shows its name instead of vanishing from the workflow.
  var thinTasks = TASKS.filter(function (task) { return task.task_name !== 'GRV Inputs'; });
  var groups = leadStageGroups(ITEMS, thinTasks);
  var future = [];
  groups.forEach(function (group) {
    group.rows.forEach(function (row) { if (!row.task) future.push(row.label); });
  });
  assert.deepEqual(future, ['GRV Inputs']);
});

test('lead sidebar never loses a real step no tracked item references', function () {
  var groups = leadStageGroups(ITEMS, TASKS);
  var rendered = [];
  groups.forEach(function (group) {
    group.rows.forEach(function (row) { if (row.task) rendered.push(row.task.task_name); });
  });
  TASKS.forEach(function (task) {
    assert.ok(rendered.indexOf(task.task_name) >= 0, task.task_name + ' is reachable from the sidebar');
  });
  // A stray extra row (none in the v5 template) still lands in its own stage
  // group rather than being dropped.
  var extra = TASKS.concat([{ task_id: 99, task_name: 'Legacy Step', sequence_no: 99,
                              status: 'Not Assigned', stage_group: 'Pre-Well Delivery' }]);
  var preWell = leadStageGroups(ITEMS, extra)[2].rows.map(function (row) { return row.label; });
  assert.ok(preWell.indexOf('Legacy Step') >= 0, preWell.join(','));
});

test('lead sidebar tolerates a lead with no tracked items at all', function () {
  assert.deepEqual(leadStageGroups(null, []), []);
  var groups = leadStageGroups([], TASKS);
  assert.equal(groups.length, 3, 'the stored steps still group under their stage groups');
  assert.deepEqual(groups.map(function (g) { return g.total; }), [0, 0, 0]);
});
