// Tests for static/js/views/pipeline.js — the Card 1B lead board (three
// workflow columns + redesigned lead cards) and the BP board it must leave
// alone. Pure rendering: renderLeadBoard/renderPipeline take rows exactly as
// GET /api/projects returns them, so every fixture row below is a board
// payload row (display_stage / tracked_items / assignees / lead_priority are
// server-derived — see workflow/projects.py).
import { test, assert, fixture } from './harness.js';
import { renderLeadBoard, renderPipeline, leadDisplayStage, DISPLAY_STAGES } from '../js/views/pipeline.js';

var LEAD_ITEMS = ['Area Definition', 'Thickness Estimation', 'GRV Inputs', 'Resource Assessment'];
var RISK_ITEMS = ['Reservoir', 'Trap and Seal', 'Seismic Validation', 'Segmentation Slides'];
var WELL_ITEMS = ['Moving Tolerance', 'Approval to Stake', 'Well Site Location', 'GeoX Assessment'];

// The 12-item model the server sends, all In Progress unless `statuses` names
// an override by label.
function trackedItems(statuses) {
  var overrides = statuses || {};
  var rows = [];
  [['Lead Assessment', LEAD_ITEMS], ['Risk Analysis', RISK_ITEMS], ['Pre-Well Delivery', WELL_ITEMS]]
    .forEach(function (pair) {
      pair[1].forEach(function (label) {
        rows.push({ stage: pair[0], label: label, status: overrides[label] || 'In Progress' });
      });
    });
  return rows;
}

function lead(name, options) {
  var extra = options || {};
  return {
    project_id: extra.project_id || 1,
    project_name: name,
    pipeline_type: 'prospect',
    display_stage: extra.display_stage || 'Lead Assessment',
    assignees: extra.assignees || [],
    lead_priority: extra.lead_priority === undefined ? 'Medium' : extra.lead_priority,
    tracked_items: extra.tracked_items || trackedItems(extra.statuses)
  };
}

function board(rows) {
  var root = fixture('<div id="board" class="pipeline-board"></div>');
  var element = root.querySelector('#board');
  renderLeadBoard(element, rows);
  return element;
}

function columnNames(element) {
  return Array.prototype.map.call(element.querySelectorAll('.lead-column h3'),
    function (h) { return h.textContent; });
}

function cardNames(element, columnIndex) {
  var column = element.querySelectorAll('.lead-column')[columnIndex];
  return Array.prototype.map.call(column.querySelectorAll('.lead-card-name'),
    function (span) { return span.textContent; });
}

function itemLabels(card) {
  return Array.prototype.map.call(card.querySelectorAll('.lead-item-label'),
    function (span) { return span.textContent; });
}

// ---------------------------------------------------------------------------
// Columns
// ---------------------------------------------------------------------------

test('pipeline.renderLeadBoard renders the three display-stage columns in order', function () {
  var element = board([]);
  assert.deepEqual(columnNames(element), ['Lead Assessment', 'Risk Analysis', 'Pre-Well Delivery']);
  assert.deepEqual(DISPLAY_STAGES, ['Lead Assessment', 'Risk Analysis', 'Pre-Well Delivery']);
  // Header glyphs: checklist / gauge / rig, one per column.
  var icons = element.querySelectorAll('.lead-column-icon svg');
  assert.equal(icons.length, 3, 'every column header carries its glyph');
  assert.ok(icons[0].classList.contains('lucide-clipboard-check'));
  assert.ok(icons[1].classList.contains('lucide-gauge'));
  assert.ok(icons[2].classList.contains('lucide-rig'));
});

test('pipeline.renderLeadBoard puts a Segmentation-stage lead in Risk Analysis', function () {
  // display_stage is the server's mapping of the stored stage group; the two
  // stored stages Risking and Segmentation share one column.
  var element = board([
    lead('RISKING-1', { project_id: 1, display_stage: 'Risk Analysis' }),
    lead('SEGMENTATION-1', { project_id: 2, display_stage: 'Risk Analysis' }),
    lead('EARLY-1', { project_id: 3, display_stage: 'Lead Assessment' })
  ]);
  assert.deepEqual(cardNames(element, 0), ['EARLY-1']);
  assert.deepEqual(cardNames(element, 1), ['RISKING-1', 'SEGMENTATION-1']);
  assert.deepEqual(cardNames(element, 2), []);
});

test('pipeline.leadDisplayStage coerces an unknown stage to the FIRST column', function () {
  assert.equal(leadDisplayStage({ display_stage: 'Pre-Well Delivery' }), 'Pre-Well Delivery');
  assert.equal(leadDisplayStage({ display_stage: 'Post-Drilling' }), 'Lead Assessment');
  assert.equal(leadDisplayStage({}), 'Lead Assessment');
});

test('pipeline.renderLeadBoard badges count the filtered leads, 0 included', function () {
  var counts = function (element) {
    return Array.prototype.map.call(element.querySelectorAll('.lead-column-count'),
      function (span) { return span.textContent; });
  };
  assert.deepEqual(counts(board([])), ['0', '0', '0']);
  assert.deepEqual(counts(board([
    lead('A', { project_id: 1 }),
    lead('B', { project_id: 2 }),
    lead('C', { project_id: 3, display_stage: 'Pre-Well Delivery' })
  ])), ['2', '0', '1']);
});

// ---------------------------------------------------------------------------
// Tracked items
// ---------------------------------------------------------------------------

test('pipeline lead card shows the four tracked items of its OWN column, in order', function () {
  var element = board([
    lead('EARLY-1', { project_id: 1 }),
    lead('RISK-1', { project_id: 2, display_stage: 'Risk Analysis' }),
    lead('WELL-1', { project_id: 3, display_stage: 'Pre-Well Delivery' })
  ]);
  var cards = element.querySelectorAll('.lead-card');
  assert.deepEqual(itemLabels(cards[0]), LEAD_ITEMS);
  assert.deepEqual(itemLabels(cards[1]), RISK_ITEMS);
  assert.deepEqual(itemLabels(cards[2]), WELL_ITEMS);
  // Labels are never abbreviated — "Segmentation Slides" reads in full.
  assert.ok(itemLabels(cards[1]).indexOf('Segmentation Slides') >= 0);
});

test('pipeline tracked-item dots use a distinct GLYPH per status, not color alone', function () {
  var element = board([lead('DOTS-1', {
    display_stage: 'Risk Analysis',
    statuses: { 'Reservoir': 'Completed', 'Trap and Seal': 'Completed', 'Segmentation Slides': 'Pending Approval' }
  })]);
  var dots = element.querySelectorAll('.lead-item .lead-dot');
  assert.equal(dots.length, 4);
  var glyph = function (index) { return dots[index].querySelector('svg').getAttribute('class'); };

  assert.ok(dots[0].classList.contains('lead-dot-completed'));
  assert.ok(glyph(0).indexOf('lucide-circle-check') >= 0, 'Completed = filled check');
  // Trap and Seal is a COMBINED item: the server sends one status for the pair
  // (Completed only when Trap CoS AND Seal CoS are both Approved).
  assert.ok(dots[1].classList.contains('lead-dot-completed'));
  assert.ok(dots[2].classList.contains('lead-dot-in-progress'));
  assert.ok(glyph(2).indexOf('lucide-circle') >= 0 && glyph(2).indexOf('check') < 0, 'In Progress = empty ring');
  assert.ok(dots[3].classList.contains('lead-dot-pending'));
  assert.ok(glyph(3).indexOf('lucide-circle-minus') >= 0, 'Pending Approval = dash');
});

test('pipeline tracked-item dots are labelled for assistive tech', function () {
  var element = board([lead('A11Y-1', {
    display_stage: 'Risk Analysis',
    statuses: { 'Segmentation Slides': 'Pending Approval' }
  })]);
  var dot = element.querySelectorAll('.lead-item .lead-dot')[3];
  assert.equal(dot.getAttribute('role'), 'img');
  assert.equal(dot.getAttribute('aria-label'), 'Pending Approval');
  assert.equal(dot.getAttribute('title'), 'Segmentation Slides — Pending Approval');
});

test('pipeline renders an unknown item status as In Progress', function () {
  // "Not Assigned" is a STORED status, never a display one; anything the card
  // does not know falls back to the ongoing-work dot rather than disappearing.
  var element = board([lead('FALLBACK-1', { statuses: { 'GRV Inputs': 'Not Assigned' } })]);
  var dots = element.querySelectorAll('.lead-item .lead-dot');
  assert.ok(dots[2].classList.contains('lead-dot-in-progress'));
});

// ---------------------------------------------------------------------------
// Assignees
// ---------------------------------------------------------------------------

test('pipeline lead card lists every assignee with the person glyph', function () {
  var element = board([lead('CROX-2', { assignees: ['N. Saleh', 'R. Khalid', 'S. Ali'] })]);
  var people = element.querySelectorAll('.lead-person');
  assert.equal(people.length, 3);
  assert.deepEqual(Array.prototype.map.call(element.querySelectorAll('.lead-person-name'),
    function (span) { return span.textContent; }), ['N. Saleh', 'R. Khalid', 'S. Ali']);
  assert.equal(element.querySelectorAll('.lead-person-icon svg.lucide-user').length, 3);
});

test('pipeline lead card renders Unassigned with NO person glyph', function () {
  var element = board([lead('ORYX-2', { assignees: [] })]);
  var people = element.querySelectorAll('.lead-person');
  assert.equal(people.length, 1);
  assert.equal(people[0].textContent, 'Unassigned');
  assert.equal(element.querySelectorAll('.lead-person-icon').length, 0,
    'the absence of a person is not a person');
  // The card keeps its four item rows regardless of assignee count, so the
  // geometry (min-height + fixed item block) is identical to a 3-assignee card.
  assert.equal(element.querySelectorAll('.lead-item').length, 4);
});

test('pipeline escapes lead names and assignee names', function () {
  var element = board([lead('<b>X</b>', { assignees: ['<img src=x>'] })]);
  assert.equal(element.querySelector('.lead-card-name').textContent, '<b>X</b>');
  assert.equal(element.querySelectorAll('.lead-card-name b').length, 0);
  assert.equal(element.querySelectorAll('.lead-person-name img').length, 0);
});

// ---------------------------------------------------------------------------
// Priority: border color and column order
// ---------------------------------------------------------------------------

test('pipeline sorts each column High -> Medium -> Low, stable within a priority', function () {
  var element = board([
    lead('MED-1', { project_id: 1, lead_priority: 'Medium' }),
    lead('LOW-1', { project_id: 2, lead_priority: 'Low' }),
    lead('HIGH-1', { project_id: 3, lead_priority: 'High' }),
    lead('MED-2', { project_id: 4, lead_priority: 'Medium' }),
    lead('HIGH-2', { project_id: 5, lead_priority: 'High' }),
    lead('MED-3', { project_id: 6, lead_priority: 'Medium' })
  ]);
  // Server order is preserved inside each priority band — no name sort.
  assert.deepEqual(cardNames(element, 0),
    ['HIGH-1', 'HIGH-2', 'MED-1', 'MED-2', 'MED-3', 'LOW-1']);
});

test('pipeline drives the card border from lead_priority, not from workflow status', function () {
  var element = board([
    lead('HIGH-1', { project_id: 1, lead_priority: 'High',
                     statuses: { 'Area Definition': 'Completed', 'Thickness Estimation': 'Completed' } }),
    lead('MED-1', { project_id: 2, lead_priority: 'Medium' }),
    lead('LOW-1', { project_id: 3, lead_priority: 'Low' }),
    lead('NONE-1', { project_id: 4, lead_priority: null })
  ]);
  var classes = Array.prototype.map.call(element.querySelectorAll('.lead-card'),
    function (card) { return card.className; });
  assert.ok(classes[0].indexOf('lead-card-high') >= 0);
  assert.ok(classes[1].indexOf('lead-card-medium') >= 0);
  assert.ok(classes[2].indexOf('lead-card-low') >= 0);
  // An absent priority reads Low (gray), matching the server default.
  assert.ok(classes[3].indexOf('lead-card-low') >= 0);
  // No status-* class survives on a lead card: status lives in the dots now.
  classes.forEach(function (value) { assert.ok(value.indexOf('status-') < 0, value); });
});

test('pipeline lead card keeps the openDetail identity attributes', function () {
  var element = board([lead('IBEX-3', { project_id: 42 })]);
  var card = element.querySelector('.lead-card');
  assert.equal(card.getAttribute('data-project-id'), '42');
  assert.equal(card.getAttribute('data-pipeline'), 'prospect');
  assert.equal(card.tagName, 'BUTTON');
});

// ---------------------------------------------------------------------------
// The BP board is untouched
// ---------------------------------------------------------------------------

test('pipeline.renderPipeline still renders the BP board exactly as before', function () {
  var root = fixture('<div id="bp" class="pipeline-board"></div>');
  var element = root.querySelector('#bp');
  renderPipeline(element, [{
    project_id: 7, project_name: 'BP-WELL-1', pipeline_type: 'bp',
    current_stage: 'Post-Drilling', current_task: 'Quicklook Logs',
    current_owner: 'Employee', overall_status: 'In Progress',
    current_task_priority: 'High'
  }], ['Well Delivery', 'Post-Drilling', 'Post-Testing'], 'bp');

  assert.equal(element.querySelectorAll('.pipeline-column').length, 3);
  assert.equal(element.querySelectorAll('.lead-column').length, 0, 'no lead-board markup');
  assert.equal(element.querySelectorAll('.lead-card').length, 0);
  assert.ok(!element.classList.contains('lead-board'));

  var card = element.querySelector('.pipeline-card');
  assert.ok(card.classList.contains('status-in-progress'), 'BP cards still carry the status class');
  assert.equal(card.querySelector('strong').textContent, 'BP-WELL-1');
  assert.equal(card.querySelector('.pipeline-card-component').textContent, 'Quicklook Logs');
  assert.equal(card.querySelector('.pipeline-card-assignee').textContent, 'Employee');
  assert.equal(card.querySelector('.priority').textContent, 'High');
});
