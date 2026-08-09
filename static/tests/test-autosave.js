/* Tests for static/js/views/autosave.js — Item A's prospect auto-save.
 *
 * The controller replaces the Save button on prospect step pages: a burst of
 * inputs collapses into one debounced save, Enter saves immediately, saves
 * are strictly serialized against the optimistic revision lock (never two
 * in-flight PATCHes; exactly one trailing save), auto saves are silent (the
 * #save-state indicator speaks, never a toast) while the manual path keeps
 * its toasts, and the post-save re-render must not steal the user's focus,
 * caret, or mid-flight typing.
 *
 * These drive the REAL pipeline — initAutoSave's delegated listeners, real
 * input/keydown events, loadComponent, saveComponent, renderDetail — over a
 * mocked fetch that records every PATCH and can hold one open to create a
 * genuine overlap.
 */
import { test, assert, fixture, mockFetch, waitFor } from './harness.js';
import { Store } from '../js/state.js';
import { loadComponent, saveComponent, transitionComponent } from '../js/views/detail-form.js';
import { initAutoSave, configureAutoSaveDelay, resetAutoSave } from '../js/views/autosave.js';
import { teardownStakingLetters } from '../js/views/staking-letters.js';

// The lead detail shell with every id the save→refresh→re-render chain
// touches (renderDetail + loadComponent + renderRightPanel), plus the board
// chrome refreshAllBoards reads after a save. Ids copied from index.html.
var SHELL =
  '<div id="detail-shell" class="detail-shell panel detail-shell-lead">' +
  '<button id="back-to-board" class="hidden" type="button"></button>' +
  '<div id="rail-nav"><button id="back-to-overview" type="button"></button>' +
  '<button id="switch-pipeline-view" type="button"></button></div>' +
  '<div class="detail-title-row"><h3 id="detail-name"></h3>' +
  '<button id="lead-priority-chip" type="button" class="priority priority-low lead-priority-chip hidden">Low</button></div>' +
  '<p id="detail-subtitle"></p><p id="detail-view-note" class="hidden"></p>' +
  '<button id="open-project-editor" type="button"></button>' +
  '<div id="component-list"></div>' +
  '<div class="editor-head"><span id="component-number"></span><h2 id="component-title"></h2>' +
  '<span id="save-state" class="save-state" role="status"></span>' +
  '<select id="assigned-to"></select><span id="component-status-chip"></span></div>' +
  '<form id="component-form"><div id="dynamic-fields"></div>' +
  '<label id="comments-field">Comments<textarea id="comments"></textarea></label>' +
  '<div class="action-row">' +
  '<button id="return-component" type="button" class="ghost hidden">Return for Update</button>' +
  '<button id="submit-component" type="button" class="hidden">Submit for Approval</button>' +
  '<button id="approve-component" type="button" class="hidden">Approve</button>' +
  '<button id="save-component" type="submit">Save Updates</button>' +
  '</div></form>' +
  '<div id="summary-card-head"></div><div id="lead-summary"></div>' +
  '</div>' +
  '<select id="bp-status-filter"><option>All</option></select>' +
  '<select id="bp-year-filter"><option>All</option></select>' +
  '<select id="bp-assignee-filter"><option value="">All assignees</option></select>' +
  '<div id="prospect-pipeline"></div><div id="bp-pipeline"></div>' +
  '<div id="lead-filter-row"></div>';

function jsonResponse(body) {
  return {
    ok: true, status: 200,
    headers: { get: function () { return 'application/json'; } },
    json: function () { return Promise.resolve(body); },
    text: function () { return Promise.resolve(JSON.stringify(body)); }
  };
}

function settle(ms) {
  return new Promise(function (resolve) { setTimeout(resolve, ms == null ? 120 : ms); });
}

function byId(id) { return document.getElementById(id); }
function indicator() { return byId('save-state'); }

function type(input, value) {
  input.value = value;
  input.dispatchEvent(new Event('input', { bubbles: true }));
}

function pressEnter(element) {
  element.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
}

function clearToast() {
  var el = byId('app-message');
  if (el) { el.textContent = ''; el.className = 'app-message'; }
}

function toastText() {
  var el = byId('app-message');
  return el ? el.textContent : '';
}

// --- the generic GeoX step ---------------------------------------------------

function geoxTask(state) {
  return { task_id: 91, task_name: 'Pre-Drilling GeoX Assessment', sequence_no: 12,
           stage_group: 'Pre-Well Delivery', status: 'In Progress', priority: 'Medium',
           revision: state.revision, comments: state.comments, assigned_to: '',
           permissions: { approval_required: false, approval_locked: false, can_edit: true,
             can_submit: false, can_approve: false, can_return: false, can_reopen: false,
             can_manage_assignments: true } };
}

// One mocked backend: fields + comments are real state, every task PATCH bumps
// the revision (the optimistic lock), and the tracker records payloads plus the
// maximum number of SIMULTANEOUSLY unresolved PATCHes. `tracker.holdPatch`
// (set to []) parks PATCH responses until the test releases them.
function mockGeoxBackend(state) {
  var tracker = { patches: [], concurrent: 0, maxConcurrent: 0, holdPatch: null };
  mockFetch(function (url, options) {
    var path = String(url);
    var method = (options && options.method) || 'GET';
    if (/\/api\/tasks\/91\/dynamic-fields(\?|$)/.test(path)) return jsonResponse(state.fields);
    if (path.indexOf('/api/projects/44/component-folder/91') >= 0) {
      return jsonResponse({ requires_folder: 0 });
    }
    if (/\/api\/tasks\/91(\?|$)/.test(path) && method === 'PATCH') {
      var body = JSON.parse(options.body);
      tracker.patches.push(body);
      tracker.concurrent += 1;
      if (tracker.concurrent > tracker.maxConcurrent) tracker.maxConcurrent = tracker.concurrent;
      var respond = function () {
        tracker.concurrent -= 1;
        state.revision += 1;
        state.fields = Object.assign({}, state.fields, body.fields);
        state.comments = body.comments;
        return jsonResponse({ task: geoxTask(state) });
      };
      if (tracker.holdPatch) {
        return new Promise(function (resolve) {
          tracker.holdPatch.push(function () { resolve(respond()); });
        });
      }
      return Promise.resolve(respond());
    }
    if (path.indexOf('/api/projects/44/detail') >= 0) {
      return jsonResponse({
        project: { project_id: 44, project_name: 'Autosave Lead', pipeline_type: 'prospect' },
        tasks: [geoxTask(state)],
        fields: { 'Pre-Drilling GeoX Assessment': state.fields },
        overview: {}, formations: []
      });
    }
    if (path.indexOf('/api/portfolio/rows') >= 0) return jsonResponse({ rows: [], summary: {} });
    if (path.indexOf('/api/projects') >= 0) return jsonResponse([]);
    if (path.indexOf('/api/users') >= 0) return jsonResponse([]);
    throw new Error('Unexpected request: ' + method + ' ' + path);
  });
  return tracker;
}

// Store scaffolding + fast debounce, restored (and the controller reset) even
// when the test fails mid-flight.
async function withAutoSave(fn) {
  var saved = {
    user: Store.user, users: Store.users, meta: Store.meta, projectId: Store.projectId,
    project: Store.project, tasks: Store.tasks, task: Store.task,
    allFields: Store.allFields, leadSummary: Store.leadSummary,
    overview: Store.overview, formations: Store.formations, pipeline: Store.pipeline
  };
  configureAutoSaveDelay(25);
  try {
    Store.user = { name: 'Supervisor', role: 'supervisor' };
    Store.users = [];
    Store.meta = null;
    Store.projectId = 44;
    Store.project = { project_id: 44, project_name: 'Autosave Lead', pipeline_type: 'prospect' };
    Store.tasks = [];
    Store.task = null;
    Store.allFields = {};
    Store.leadSummary = null;
    Store.overview = {};
    Store.formations = [];
    Store.pipeline = 'prospect';
    await fn();
  } finally {
    resetAutoSave();
    configureAutoSaveDelay(null);
    Object.keys(saved).forEach(function (key) { Store[key] = saved[key]; });
  }
}

async function mountGeox(state) {
  fixture(SHELL);
  var tracker = mockGeoxBackend(state);
  Store.tasks = [geoxTask(state)];
  Store.allFields = { 'Pre-Drilling GeoX Assessment': state.fields };
  initAutoSave();
  await loadComponent(Store.tasks[0]);
  return tracker;
}

test('autosave: a burst of inputs schedules ONE debounced save carrying the final value', function () {
  return withAutoSave(async function () {
    var state = { revision: 1, comments: '', fields: { pre_drill_piip_gas_p90: '' } };
    var tracker = await mountGeox(state);
    var input = document.querySelector('[data-field="pre_drill_piip_gas_p90"]');
    type(input, '1');
    type(input, '12');
    type(input, '125');
    assert.equal(tracker.patches.length, 0, 'nothing fires mid-burst');
    await waitFor(function () { return tracker.patches.length === 1; });
    await waitFor(function () { return indicator().textContent === 'Saved'; });
    await settle();
    assert.equal(tracker.patches.length, 1, 'the whole burst is one PATCH');
    assert.equal(tracker.patches[0].fields.pre_drill_piip_gas_p90, '125', 'carrying the final value');
    assert.equal(tracker.patches[0].revision, 1, 'with the current revision');
    assert.equal(document.querySelector('[data-field="pre_drill_piip_gas_p90"]').value, '125',
      'and the re-rendered form shows it');
  });
});

test('autosave: Enter (outside a textarea) saves immediately; Enter in the comments textarea does not', function () {
  return withAutoSave(async function () {
    var state = { revision: 1, comments: '', fields: { pre_drill_piip_gas_p90: '' } };
    var tracker = await mountGeox(state);
    // A long debounce proves Enter did not just ride the timer.
    configureAutoSaveDelay(5000);
    var input = document.querySelector('[data-field="pre_drill_piip_gas_p90"]');
    type(input, '77');
    assert.equal(tracker.patches.length, 0, 'the 5s debounce is still pending');
    pressEnter(input);
    await waitFor(function () { return tracker.patches.length === 1; });
    assert.equal(tracker.patches[0].fields.pre_drill_piip_gas_p90, '77');
    await waitFor(function () { return indicator().textContent === 'Saved'; });
    // Enter inside the textarea is a newline, never a save.
    pressEnter(byId('comments'));
    await settle();
    assert.equal(tracker.patches.length, 1, 'no save from Enter in a textarea');
  });
});

test('autosave: saves are SERIALIZED — never two in-flight PATCHes, exactly one trailing save on fresh revisions', function () {
  return withAutoSave(async function () {
    var state = { revision: 1, comments: '', fields: { pre_drill_piip_gas_p90: '' } };
    var tracker = await mountGeox(state);
    var input = document.querySelector('[data-field="pre_drill_piip_gas_p90"]');
    tracker.holdPatch = [];              // park the first PATCH in flight
    input.focus();
    type(input, '111');
    await waitFor(function () { return tracker.patches.length === 1; });
    assert.equal(indicator().textContent, 'Saving…');
    // Three more edits WHILE the first save is in flight: they must queue
    // exactly one trailing save, not fire concurrently (the revision lock
    // would 409 it).
    type(input, '2');
    type(input, '22');
    type(input, '222');
    await settle(150);
    assert.equal(tracker.patches.length, 1, 'the trailing save waits for the in-flight one');
    tracker.holdPatch.shift()();         // release the parked response
    tracker.holdPatch = null;
    await waitFor(function () { return tracker.patches.length === 2; });
    await waitFor(function () { return indicator().textContent === 'Saved'; });
    await settle();
    assert.equal(tracker.patches.length, 2, 'burst-during-flight = exactly one trailing save');
    assert.equal(tracker.maxConcurrent, 1, 'never two unresolved PATCHes');
    assert.equal(tracker.patches[1].revision, 2,
      'the trailing save runs on the REFRESHED revision, not the stale one');
    assert.equal(tracker.patches[1].fields.pre_drill_piip_gas_p90, '222',
      'and carries the mid-flight typing (focus preservation kept it alive)');
  });
});

test('autosave: focus, caret and mid-flight typing survive the post-save re-render', function () {
  return withAutoSave(async function () {
    var state = { revision: 1, comments: '', fields: { pre_drill_piip_gas_p90: '' } };
    var tracker = await mountGeox(state);
    // A number input: the re-render replaces the node; focus must land on its
    // successor with the value intact.
    var input = document.querySelector('[data-field="pre_drill_piip_gas_p90"]');
    input.focus();
    type(input, '42');
    await waitFor(function () { return indicator().textContent === 'Saved'; });
    var successor = document.querySelector('[data-field="pre_drill_piip_gas_p90"]');
    assert.equal(document.activeElement, successor,
      'focus lands on the re-rendered control');
    assert.equal(successor.value, '42');
    // The comments textarea: value AND caret survive.
    var comments = byId('comments');
    comments.focus();
    comments.value = 'hello world';
    comments.setSelectionRange(5, 5);
    comments.dispatchEvent(new Event('input', { bubbles: true }));
    await waitFor(function () { return tracker.patches.length === 2; });
    await waitFor(function () { return indicator().textContent === 'Saved'; });
    assert.equal(tracker.patches[1].comments, 'hello world');
    assert.equal(document.activeElement, byId('comments'), 'focus stays in the comments box');
    assert.equal(byId('comments').value, 'hello world');
    assert.equal(byId('comments').selectionStart, 5, 'the caret did not move');
    assert.equal(byId('comments').selectionEnd, 5);
  });
});

test('autosave: auto saves are silent (indicator only); the manual path still toasts', function () {
  return withAutoSave(async function () {
    var state = { revision: 1, comments: '', fields: { pre_drill_piip_gas_p90: '' } };
    var tracker = await mountGeox(state);
    clearToast();
    type(document.querySelector('[data-field="pre_drill_piip_gas_p90"]'), '9');
    await waitFor(function () { return indicator().textContent === 'Saved'; });
    await settle();
    assert.equal(toastText().indexOf('Component saved'), -1,
      'an auto save never toasts success');
    // The manual path (the BP pages' Save button submits through exactly this
    // call) keeps its toast.
    clearToast();
    var outcome = await saveComponent({ preventDefault: function () {} });
    assert.equal(outcome.ok, true);
    assert.equal(toastText(), 'Component saved.', 'a manual save still announces itself');
    assert.equal(tracker.patches.length, 2);
  });
});

test('autosave: the indicator walks Saving… → Saved, and a blocked save shows the message without a toast or a PATCH', function () {
  return withAutoSave(async function () {
    var state = { revision: 1, comments: '', fields: { pre_drill_piip_gas_p90: '' } };
    var tracker = await mountGeox(state);
    tracker.holdPatch = [];
    var input = document.querySelector('[data-field="pre_drill_piip_gas_p90"]');
    type(input, '50');
    await waitFor(function () { return tracker.patches.length === 1; });
    assert.equal(indicator().textContent, 'Saving…');
    assert.ok(indicator().classList.contains('is-saving'));
    tracker.holdPatch.shift()();
    tracker.holdPatch = null;
    await waitFor(function () { return indicator().textContent === 'Saved'; });
    assert.ok(indicator().classList.contains('is-saved'));
    await settle();
    // Client validation fails: inline message in the indicator, no toast, no
    // network write.
    clearToast();
    var invalid = document.querySelector('[data-field="pre_drill_piip_gas_p90"]');
    type(invalid, '-5');
    await waitFor(function () { return indicator().classList.contains('is-error'); });
    assert.match(indicator().textContent, /must not be negative/);
    assert.equal(toastText(), '', 'no toast-spam while typing through an invalid value');
    await settle();
    assert.equal(tracker.patches.length, 1, 'nothing was PATCHed while invalid');
  });
});

test('autosave: Segmentation Slides submit waits for its pending draft save and uses the fresh revision', function () {
  return withAutoSave(async function () {
    fixture(SHELL);
    configureAutoSaveDelay(5000);
    var state = { revision: 1, status: 'In Progress', fields: {} };
    var calls = [];
    var releasePatch = null;

    function task() {
      var locked = state.status !== 'In Progress';
      return {
        task_id: 92, task_name: 'Segmentation Slides', sequence_no: 9,
        stage_group: 'Risk Analysis', status: state.status, priority: 'Medium',
        revision: state.revision, comments: '', assigned_to: 'Employee',
        permissions: {
          approval_required: true, approval_locked: locked, can_edit: !locked,
          can_submit: !locked, can_approve: false, can_return: false,
          can_reopen: false, can_manage_assignments: true
        }
      };
    }

    mockFetch(function (url, options) {
      var path = String(url);
      var method = (options && options.method) || 'GET';
      if (/\/api\/tasks\/92\/dynamic-fields(\?|$)/.test(path)) return jsonResponse(state.fields);
      if (path.indexOf('/api/projects/44/component-folder/92') >= 0) {
        return jsonResponse({ requires_folder: 0 });
      }
      if (/\/api\/tasks\/92(\?|$)/.test(path) && method === 'PATCH') {
        var body = JSON.parse(options.body);
        calls.push({ kind: 'save', revision: body.revision });
        return new Promise(function (resolve) {
          releasePatch = function () {
            state.revision += 1;
            state.fields = Object.assign({}, state.fields, body.fields);
            resolve(jsonResponse({ ok: true, task: task() }));
          };
        });
      }
      if (path.indexOf('/api/tasks/92/transition') >= 0 && method === 'POST') {
        var transitionBody = JSON.parse(options.body);
        calls.push({ kind: 'transition', revision: transitionBody.revision });
        state.revision += 1;
        state.status = 'Ready';
        return jsonResponse({ ok: true, task: task() });
      }
      if (path.indexOf('/api/projects/44/detail') >= 0) {
        return jsonResponse({
          project: { project_id: 44, project_name: 'Autosave Lead', pipeline_type: 'prospect' },
          tasks: [task()], fields: { 'Segmentation Slides': state.fields },
          overview: {}, formations: []
        });
      }
      if (path.indexOf('/api/portfolio/rows') >= 0) return jsonResponse({ rows: [], summary: {} });
      if (path.indexOf('/api/projects') >= 0) return jsonResponse([]);
      if (path.indexOf('/api/users') >= 0) return jsonResponse([]);
      throw new Error('Unexpected request: ' + method + ' ' + path);
    });

    Store.tasks = [task()];
    Store.allFields = { 'Segmentation Slides': state.fields };
    initAutoSave();
    await loadComponent(Store.tasks[0]);
    var checkbox = document.querySelector('[data-field="segmentation_slides_loaded"]');
    checkbox.checked = true;
    checkbox.dispatchEvent(new Event('change', { bubbles: true }));

    var pending = transitionComponent('submit');
    await waitFor(function () { return calls.length === 1; });
    assert.deepEqual(calls, [{ kind: 'save', revision: 1 }],
      'submit is held while the draft PATCH is unresolved');
    releasePatch();
    await pending;

    assert.deepEqual(calls, [
      { kind: 'save', revision: 1 },
      { kind: 'transition', revision: 2 }
    ], 'the transition follows the save and uses its refreshed revision');
  });
});

// --- the consolidated Staking Letters page rides the same controller ---------

test('autosave: typing the staked Well Name auto-saves the Staking Letters plan (Item B end to end)', function () {
  return withAutoSave(async function () {
    fixture(SHELL);
    var tasks = [
      { task_id: 201, task_name: 'Approval to Stake', sequence_no: 10,
        stage_group: 'Pre-Well Delivery', status: 'In Progress', priority: 'Medium',
        revision: 3, comments: '', assigned_to: '', permissions: {
          approval_required: false, approval_locked: false, can_edit: true,
          can_submit: false, can_approve: false, can_return: false, can_reopen: false,
          can_manage_assignments: true } },
      { task_id: 202, task_name: 'Well Site Location', sequence_no: 11,
        stage_group: 'Pre-Well Delivery', status: 'In Progress', priority: 'Medium',
        revision: 5, comments: '', assigned_to: '', permissions: {
          approval_required: false, approval_locked: false, can_edit: true,
          can_submit: false, can_approve: false, can_return: false, can_reopen: false,
          can_manage_assignments: true } }
    ];
    var fields = {
      'Approval to Stake': { staking_well_created: '1', approval_stake_letter_loaded: '1' },
      'Well Site Location': { wellsite_letter_loaded: '1', staked_x: '532100.5',
                              staked_y: '2895120.1', staked_well_name: '' }
    };
    var patches = [];
    mockFetch(function (url, options) {
      var path = String(url);
      var method = (options && options.method) || 'GET';
      if (path.indexOf('/component-folder/') >= 0) return jsonResponse({ requires_folder: 0 });
      if (/\/api\/tasks\/(201|202)(\?|$)/.test(path) && method === 'PATCH') {
        var body = JSON.parse(options.body);
        var id = Number(path.match(/\/api\/tasks\/(\d+)/)[1]);
        patches.push({ taskId: id, body: body });
        var task = tasks.filter(function (item) { return item.task_id === id; })[0];
        task.revision += 1;
        fields[task.task_name] = Object.assign({}, fields[task.task_name], body.fields);
        return jsonResponse({ task: task });
      }
      if (path.indexOf('/api/projects/44/detail') >= 0) {
        return jsonResponse({
          project: { project_id: 44, project_name: 'Autosave Lead', pipeline_type: 'prospect' },
          tasks: tasks, fields: fields, overview: {}, formations: []
        });
      }
      if (path.indexOf('/api/portfolio/rows') >= 0) return jsonResponse({ rows: [], summary: {} });
      if (path.indexOf('/api/projects') >= 0) return jsonResponse([]);
      if (path.indexOf('/api/users') >= 0) return jsonResponse([]);
      throw new Error('Unexpected request: ' + method + ' ' + path);
    });
    Store.tasks = tasks;
    Store.allFields = fields;
    initAutoSave();
    await loadComponent(tasks[0]);   // mounts the consolidated page
    clearToast();
    var name = document.querySelector('[data-sl-field="staked_well_name"]');
    assert.ok(name, 'the Well Name input is on the page');
    name.focus();
    type(name, 'SARH-101');
    await waitFor(function () { return indicator().textContent === 'Saved'; });
    assert.equal(patches.length, 1, 'only the owning task is PATCHed');
    assert.equal(patches[0].taskId, 202, 'the Well Site Location row — the same task as staked_x/staked_y');
    assert.equal(patches[0].body.fields.staked_well_name, 'SARH-101');
    assert.equal(patches[0].body.fields.staked_x, '532100.5', 'coordinates travel back unchanged');
    assert.equal(patches[0].body.revision, 5, 'that task\'s own revision');
    assert.equal(toastText().indexOf('Staking letters saved'), -1, 'silent, like every auto save');
    // The re-rendered workspace kept the typing and the focus.
    var successor = document.querySelector('[data-sl-field="staked_well_name"]');
    assert.equal(successor.value, 'SARH-101');
    assert.equal(document.activeElement, successor);
    teardownStakingLetters();
  });
});
