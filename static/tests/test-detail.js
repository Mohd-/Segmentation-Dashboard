// Tests for static/js/views/detail.js — the LEAD-LEVEL priority chip and the
// Well Summary's two-decimal reservoir-property formatting.
//
// Priority is a record attribute (stored projects.priority, delivered on the
// detail payload as project.priority): ONE chip beside the record name in the
// shell header, for both the lead and BP well shells. A supervisor cycles it
// Low → Medium → High → Low through PATCH /api/projects/<id>/priority;
// everyone else sees a static, disabled chip.
import { test, assert, fixture, mockFetch } from './harness.js';
import { renderLeadPriorityChip, cycleLeadPriorityChip, nextLeadPriority, fmt2 } from '../js/views/detail.js';
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
