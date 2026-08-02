// Tests for static/js/navigation.js. activateTab queries document-wide for
// `.tab` panels and `.tabs button` — the fixture supplies both (runner.html
// itself deliberately uses neither class).
import { test, assert, fixture, mockFetch, waitFor } from './harness.js';
import { activateTab, scrollToTab } from '../js/navigation.js';
import { backFromEditor } from '../js/views/project-editor.js';
import { Store } from '../js/state.js';

function tabFixture() {
  return fixture(
    '<nav class="tabs">' +
      '<button data-tab="prospect" class="active" aria-selected="true" type="button">Prospect</button>' +
      '<button data-tab="portfolio" aria-selected="false" type="button">Portfolio</button>' +
      '<button data-tab="bp" aria-selected="false" type="button">BP</button>' +
    '</nav>' +
    '<section id="tab-prospect" class="tab active"></section>' +
    '<section id="tab-portfolio" class="tab"></section>' +
    '<section id="tab-bp" class="tab"></section>'
  );
}

test('navigation.activateTab activates the matching panel and button', function () {
  var root = tabFixture();
  activateTab('portfolio');
  assert.ok(root.querySelector('#tab-portfolio').classList.contains('active'), 'target panel active');
  assert.ok(!root.querySelector('#tab-prospect').classList.contains('active'), 'previous panel deactivated');
  assert.ok(!root.querySelector('#tab-bp').classList.contains('active'));
  var buttons = root.querySelectorAll('.tabs button');
  assert.equal(buttons[0].classList.contains('active'), false);
  assert.equal(buttons[0].getAttribute('aria-selected'), 'false');
  assert.equal(buttons[1].classList.contains('active'), true);
  assert.equal(buttons[1].getAttribute('aria-selected'), 'true');
  assert.equal(buttons[2].getAttribute('aria-selected'), 'false');
});

test('navigation.activateTab is idempotent and switches back cleanly', function () {
  var root = tabFixture();
  activateTab('bp');
  activateTab('bp');
  activateTab('prospect');
  assert.ok(root.querySelector('#tab-prospect').classList.contains('active'));
  assert.ok(!root.querySelector('#tab-bp').classList.contains('active'));
  var active = root.querySelectorAll('.tabs button.active');
  assert.equal(active.length, 1, 'exactly one active button');
  assert.equal(active[0].getAttribute('data-tab'), 'prospect');
});

test('navigation.activateTab with an unknown name deactivates everything', function () {
  var root = tabFixture();
  activateTab('nope');
  assert.equal(root.querySelectorAll('.tab.active').length, 0);
  assert.equal(root.querySelectorAll('.tabs button.active').length, 0);
  var selected = Array.prototype.map.call(root.querySelectorAll('.tabs button'),
    function (b) { return b.getAttribute('aria-selected'); });
  assert.deepEqual(selected, ['false', 'false', 'false']);
});

test('navigation.scrollToTab tolerates a missing panel and scrolls an existing one', function () {
  scrollToTab('does-not-exist'); // must not throw
  var root = tabFixture();
  var panel = root.querySelector('#tab-bp');
  var called = null;
  panel.scrollIntoView = function (opts) { called = opts; }; // instance stub, fixture-local
  scrollToTab('bp');
  assert.ok(called, 'scrollIntoView invoked');
  assert.equal(called.behavior, 'smooth');
  assert.equal(called.block, 'start');
});

/* -------------------------------------------------------------------------
   KI-003 — the all-fields editor's Back action must not lose the pipeline it
   was opened from, and must never drop the user on a Portfolio table that has
   never been fetched.

   These drive views/project-editor.js's backFromEditor directly, because the
   defect is entirely about WHERE it navigates, not about how the editor
   renders.
   ------------------------------------------------------------------------- */

// The shells + tabs both destinations touch. Deliberately a FRESH session:
// nothing has ever loaded Portfolio, which is the exact precondition of the
// bug report.
function editorFixture() {
  return fixture(
    '<nav class="tabs">' +
      '<button data-tab="prospect" type="button" aria-selected="false">Prospect</button>' +
      '<button data-tab="portfolio" type="button" aria-selected="false">Portfolio</button>' +
      '<button data-tab="bp" type="button" aria-selected="false">BP</button>' +
    '</nav>' +
    '<section id="tab-prospect" class="tab"></section>' +
    '<section id="tab-portfolio" class="tab"></section>' +
    '<section id="tab-bp" class="tab"></section>' +
    '<section id="detail-shell" class="hidden"><div id="component-list"></div>' +
      '<button id="back-to-board" class="hidden"></button><div id="rail-nav"></div>' +
      '<h3 id="detail-name"></h3><p id="detail-subtitle"></p><p id="detail-view-note"></p>' +
      '<button id="open-project-editor"></button>' +
      '<div id="summary-card-head"></div><h3 id="summary-title"></h3><div id="lead-summary"></div>' +
      '<span id="component-number"></span><h2 id="component-title"></h2>' +
      '<select id="assigned-to"></select><button id="component-priority-chip"></button>' +
      '<span id="component-status-chip"></span><form id="component-form">' +
      '<div id="dynamic-fields"></div><textarea id="comments"></textarea></form>' +
    '</section>' +
    '<section id="project-editor"></section>' +
    '<table id="portfolio-table"></table>'
  );
}

function jsonOk(body) {
  return {
    ok: true, status: 200,
    headers: { get: function () { return 'application/json'; } },
    json: function () { return Promise.resolve(body); }
  };
}

test('KI-003 the editor Back returns to the ORIGINATING record detail and pipeline', async () => {
  var root = editorFixture();
  var paths = [];
  mockFetch(function (url) {
    paths.push(String(url));
    if (String(url).indexOf('/detail') >= 0) {
      return jsonOk({
        project: { project_id: 7, project_name: 'KI3-1', pipeline_type: 'prospect', tracked_items: [] },
        tasks: [], fields: {}, overview: {}, formations: []
      });
    }
    if (String(url).indexOf('/api/portfolio/rows') >= 0) return jsonOk({ rows: [] });
    return jsonOk([]);
  });
  Store.projectId = 7;
  Store.project = { project_id: 7, project_name: 'KI3-1', pipeline_type: 'prospect' };

  backFromEditor();

  assert.ok(root.querySelector('#project-editor').classList.contains('hidden'), 'the editor closes');
  assert.ok(!root.querySelector('#detail-shell').classList.contains('hidden'),
    'the record detail it was opened from is shown again');
  await waitFor(function () { return root.querySelector('#detail-name').textContent === 'KI3-1'; });
  assert.ok(root.querySelector('#tab-prospect').classList.contains('active'),
    'the ORIGINATING pipeline is active, not Portfolio');
  assert.equal(root.querySelector('#tab-portfolio').classList.contains('active'), false);
  Store.projectId = null;
  Store.project = null;
});

test('KI-003 with no record selected, Back refreshes Portfolio before showing it', async () => {
  var root = editorFixture();
  var fetched = 0;
  mockFetch(function (url) {
    if (String(url).indexOf('/api/portfolio/rows') >= 0) { fetched += 1; return jsonOk({ rows: [] }); }
    return jsonOk([]);
  });
  Store.projectId = null;
  Store.project = null;

  backFromEditor();

  assert.ok(root.querySelector('#tab-portfolio').classList.contains('active'));
  // The whole point of the fix: a session that never opened Portfolio can no
  // longer land on a table that was never fetched.
  await waitFor(function () { return fetched > 0; });
  assert.equal(fetched, 1, 'Portfolio is fetched on the way in');
});
