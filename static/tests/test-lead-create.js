// Tests for static/js/views/lead-create.js — Card 1D's Add New Lead control.
//
// Everything is driven the way a user drives it: click the button, type into
// the fields, press Enter / Escape, click outside. The module talks to the real
// API wrapper, so each test that reaches the network stubs fetch and asserts on
// what was actually POSTed.
import { test, assert, fixture, mockFetch, waitFor } from './harness.js';
import {
  initLeadCreate, openNewLead, isNewLeadOpen, newLeadValues,
  validateNewLead, isValidCoordinate, normalizeLeadName, nameKey,
  fieldForServerError, friendlyErrorText,
  DUPLICATE_MESSAGE, NAME_REQUIRED_MESSAGE, NAME_TOO_LONG_MESSAGE, X_MESSAGE, Y_MESSAGE
} from '../js/views/lead-create.js';
import { initLeadFilters, setLeadRows, leadFilterState, filteredLeads } from '../js/views/lead-filters.js';

// The Add New Lead markup exactly as static/index.html carries it, plus the
// containers the success path's board refresh + openDetail touch (so a real
// create can run end to end without exploding on a missing element).
var MARKUP = [
  '<div class="panel new-lead-panel">',
  '  <div id="new-lead-controls" class="new-lead-controls">',
  '    <button id="new-lead-open" type="button" class="new-lead-button" aria-expanded="false" aria-controls="new-lead-fields">+ Add New Lead</button>',
  '    <div id="new-lead-fields" class="new-lead-fields hidden">',
  '      <div class="nl-field">',
  '        <label class="visually-hidden" for="new-lead-name">Lead Name</label>',
  '        <input id="new-lead-name" class="nl-input" type="text" placeholder="Lead Name" aria-describedby="new-lead-name-error">',
  '        <p id="new-lead-name-error" class="nl-error" hidden></p>',
  '      </div>',
  '      <div class="nl-field">',
  '        <label class="visually-hidden" for="new-lead-x">Lead X Coordinate</label>',
  '        <input id="new-lead-x" class="nl-input" type="text" inputmode="decimal" placeholder="Lead X Coordinate" aria-describedby="new-lead-x-error">',
  '        <p id="new-lead-x-error" class="nl-error" hidden></p>',
  '      </div>',
  '      <div class="nl-field">',
  '        <label class="visually-hidden" for="new-lead-y">Lead Y Coordinate</label>',
  '        <input id="new-lead-y" class="nl-input" type="text" inputmode="decimal" placeholder="Lead Y Coordinate" aria-describedby="new-lead-y-error">',
  '        <p id="new-lead-y-error" class="nl-error" hidden></p>',
  '      </div>',
  '      <p id="new-lead-hint" class="nl-hint">Press Enter to create</p>',
  '    </div>',
  '  </div>',
  '</div>',
  // Board / detail scaffolding the post-create refresh writes into.
  '<div id="lc-filter-row"></div>',
  '<div id="prospect-pipeline"></div>',
  '<div id="bp-pipeline"></div>',
  '<select id="bp-year-filter"><option>All</option></select>',
  '<select id="bp-status-filter"><option>All</option></select>',
  '<select id="bp-assignee-filter"><option value="">All assignees</option></select>',
  '<table id="portfolio-table"></table>',
  '<section id="project-editor" class="hidden"></section>',
  '<section id="detail-shell" class="hidden"></section>'
].join('');

function mount() {
  var host = fixture(MARKUP);
  // A live Card 1C filter module underneath: the duplicate pre-check reads its
  // unfiltered rowset, and the cancel tests assert the selection survives.
  initLeadFilters({ root: 'lc-filter-row' });
  initLeadCreate();
  return host;
}

function el(id) { return document.getElementById(id); }
function fieldsBox() { return el('new-lead-fields'); }
function inputs() {
  return Array.prototype.slice.call(fieldsBox().querySelectorAll('input'));
}
function typeInto(id, value) { el(id).value = value; }
function fill(name, x, y) {
  typeInto('new-lead-name', name);
  typeInto('new-lead-x', x);
  typeInto('new-lead-y', y);
}
function press(id, key, extra) {
  var event = new KeyboardEvent('keydown', Object.assign({ key: key, bubbles: true, cancelable: true }, extra || {}));
  el(id).dispatchEvent(event);
  return event;
}
function clickOutside() {
  document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
}
function errorText(key) { return el('new-lead-' + key + '-error').textContent; }
function errorShown(key) { return el('new-lead-' + key + '-error').hidden === false; }
function toastText() {
  var el2 = document.getElementById('app-message');
  return el2 ? el2.textContent : '';
}

// A fetch stub that records every POST /api/projects body and answers the rest
// of the boot/refresh traffic with empty payloads.
function stubCreate(responder) {
  var calls = [];
  mockFetch(function (url, options) {
    var path = String(url);
    if (options && options.method === 'POST' && path.indexOf('/api/projects') >= 0) {
      calls.push(JSON.parse(options.body));
      return responder(calls.length);
    }
    var body = path.indexOf('/api/portfolio/rows') >= 0 ? { rows: [] }
      : path.indexOf('/detail') >= 0 ? { project: {}, tasks: [], fields: {} }
        : [];
    return jsonResponse(200, body);
  });
  return calls;
}
// A create is only DONE once its success handler has run (it collapses the
// control). Tests that fire one must settle it before they end, or the handler
// lands in the middle of the NEXT test's fixture.
function settled() { return waitFor(function () { return !isNewLeadOpen(); }); }

function jsonResponse(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status: status,
    headers: { get: function () { return 'application/json'; } },
    json: function () { return Promise.resolve(body); }
  };
}

/* -------------------------------------------------------------------------
   The rules, as pure functions
   ------------------------------------------------------------------------- */

test('normalizeLeadName trims but preserves internal spaces and hyphens', () => {
  assert.equal(normalizeLeadName('  WWWW-44  '), 'WWWW-44');
  assert.equal(normalizeLeadName(' North Dome A-2 '), 'North Dome A-2');
  assert.equal(normalizeLeadName('  '), '');
  assert.equal(normalizeLeadName(null), '');
  // A pasted line break cannot survive into a single-line name.
  assert.equal(normalizeLeadName('WWWW\n-44'), 'WWWW -44');
});

test('nameKey collides on case and surrounding space (the server rule, client-side)', () => {
  assert.equal(nameKey('WWWW-44'), nameKey('wwww-44'));
  assert.equal(nameKey('WWWW-44'), nameKey(' WWWW-44 '));
  assert.ok(nameKey('WWWW-44') !== nameKey('WWWW-45'), 'different leads must not collide');
});

test('isValidCoordinate accepts signed decimals and scientific notation', () => {
  ['0', '100', '-3.5', '+12', '.5', '612345.678', '1e3', '2E-2'].forEach(function (value) {
    assert.ok(isValidCoordinate(value), 'expected ' + value + ' to be a valid coordinate');
  });
});

test('isValidCoordinate rejects blanks, letters, malformed and non-finite input', () => {
  ['', '   ', 'abc', '12abc', '12.3.4', '1,5', '12 34', '-', 'NaN', 'Infinity', '0x10', null, undefined]
    .forEach(function (value) {
      assert.ok(!isValidCoordinate(value), 'expected ' + String(value) + ' to be rejected');
    });
});

test('validateNewLead reports every missing field, in field order', () => {
  var errors = validateNewLead({ name: '   ', x: '', y: '' }, []);
  assert.deepEqual(errors.map(function (e) { return e.key; }), ['name', 'x', 'y']);
  assert.equal(errors[0].message, NAME_REQUIRED_MESSAGE);
  assert.equal(errors[1].message, X_MESSAGE);
  assert.equal(errors[2].message, Y_MESSAGE);
});

test('validateNewLead pins the exact coordinate messages', () => {
  assert.equal(X_MESSAGE, 'Enter a valid Lead X Coordinate.');
  assert.equal(Y_MESSAGE, 'Enter a valid Lead Y Coordinate.');
  var errors = validateNewLead({ name: 'OK-1', x: 'abc', y: '-2' }, []);
  assert.deepEqual(errors, [{ key: 'x', message: 'Enter a valid Lead X Coordinate.' }]);
});

test('validateNewLead enforces the max-length rule and the case-insensitive duplicate rule', () => {
  var long = validateNewLead({ name: new Array(122).join('A'), x: '1', y: '2' }, []);
  assert.deepEqual(long, [{ key: 'name', message: NAME_TOO_LONG_MESSAGE }]);

  ['wwww-44', 'WWWW-44', ' WWWW-44 '].forEach(function (typed) {
    var errors = validateNewLead({ name: typed, x: '1', y: '2' }, ['WWWW-44']);
    assert.deepEqual(errors, [{ key: 'name', message: DUPLICATE_MESSAGE }],
      'expected ' + typed + ' to collide with WWWW-44');
  });
  assert.equal(DUPLICATE_MESSAGE, 'A lead with this name already exists.');
});

test('validateNewLead accepts a valid submission', () => {
  assert.deepEqual(validateNewLead({ name: ' NEW-9 ', x: '612345.678', y: '-2734567.891' }, ['OTHER-1']), []);
});

test('fieldForServerError maps a server rejection back onto its own field', () => {
  assert.deepEqual(fieldForServerError('A lead with this name already exists.'),
    { key: 'name', message: DUPLICATE_MESSAGE });
  assert.deepEqual(fieldForServerError('A lead / well with this name already exists.'),
    { key: 'name', message: DUPLICATE_MESSAGE });
  assert.deepEqual(fieldForServerError('Enter a valid Lead Y Coordinate.'), { key: 'y', message: Y_MESSAGE });
  assert.equal(fieldForServerError('Internal Server Error'), null);
});

test('friendlyErrorText never surfaces a raw fetch failure', () => {
  assert.match(friendlyErrorText(new TypeError('NetworkError when attempting to fetch resource.')),
    /Check your connection/);
  assert.match(friendlyErrorText(new Error('')), /Check your connection/);
  // A real server message is user-facing and passes through unchanged.
  assert.equal(friendlyErrorText(new Error('Select a business plan year from 1990 to 2040.')),
    'Select a business plan year from 1990 to 2040.');
});

/* -------------------------------------------------------------------------
   Collapsed / expanded
   ------------------------------------------------------------------------- */

test('collapsed state is one button and nothing else', () => {
  mount();
  assert.ok(!el('new-lead-open').classList.contains('hidden'), 'button visible');
  assert.ok(!isNewLeadOpen(), 'fields hidden');
  assert.equal(el('new-lead-open').getAttribute('aria-expanded'), 'false');
  assert.equal(el('new-lead-open').textContent.trim(), '+ Add New Lead');
  assert.equal(el('new-lead-open').tagName, 'BUTTON');
});

test('clicking the button hides it and reveals exactly three fields in card order', () => {
  mount();
  el('new-lead-open').click();
  assert.ok(isNewLeadOpen(), 'fields revealed');
  assert.ok(el('new-lead-open').classList.contains('hidden'), 'button hidden, not merely relabelled');
  assert.equal(el('new-lead-open').getAttribute('aria-expanded'), 'true');
  assert.deepEqual(inputs().map(function (input) { return input.id; }),
    ['new-lead-name', 'new-lead-x', 'new-lead-y']);
});

test('expanded state adds no Create/Cancel buttons and no fourth input', () => {
  mount();
  openNewLead();
  assert.equal(fieldsBox().querySelectorAll('button').length, 0, 'no buttons inside the field row');
  assert.equal(inputs().length, 3, 'exactly three inputs');
  assert.equal(fieldsBox().querySelectorAll('select, textarea').length, 0);
});

test('Lead Name is focused on expand', () => {
  mount();
  el('new-lead-open').click();
  assert.equal(document.activeElement.id, 'new-lead-name');
});

test('every field has a real label and an error region wired by aria-describedby', () => {
  mount();
  openNewLead();
  [['new-lead-name', 'Lead Name'], ['new-lead-x', 'Lead X Coordinate'], ['new-lead-y', 'Lead Y Coordinate']]
    .forEach(function (pair) {
      var label = fieldsBox().querySelector('label[for="' + pair[0] + '"]');
      assert.ok(label, 'missing <label for=' + pair[0] + '>');
      assert.equal(label.textContent, pair[1]);
      assert.ok(label.classList.contains('visually-hidden'), 'label is visually hidden, not absent');
      assert.equal(el(pair[0]).getAttribute('aria-describedby'), pair[0] + '-error');
    });
});

test('the helper text is present and is not a control', () => {
  mount();
  openNewLead();
  assert.equal(el('new-lead-hint').textContent, 'Press Enter to create');
  assert.equal(el('new-lead-hint').tagName, 'P');
});

/* -------------------------------------------------------------------------
   Submit on Enter
   ------------------------------------------------------------------------- */

['new-lead-name', 'new-lead-x', 'new-lead-y'].forEach(function (id) {
  test('Enter from ' + id + ' submits the lead', async () => {
    mount();
    var calls = stubCreate(function () { return jsonResponse(201, { project_id: 5 }); });
    openNewLead();
    fill('ENTER-1', '10', '20');
    press(id, 'Enter');
    await waitFor(function () { return calls.length === 1; });
    assert.equal(calls[0].project_name, 'ENTER-1');
    await settled();
  });
});

test('Enter posts the trimmed name, both coordinates and the prospect pipeline', async () => {
  mount();
  var calls = stubCreate(function () { return jsonResponse(201, { project_id: 6 }); });
  openNewLead();
  fill('  North Dome A-2  ', ' -3.5 ', '612345.678');
  press('new-lead-name', 'Enter');
  await waitFor(function () { return calls.length === 1; });
  assert.equal(calls[0].project_name, 'North Dome A-2', 'trimmed, internal spaces/hyphen preserved');
  assert.equal(calls[0].lead_x, '-3.5');
  assert.equal(calls[0].lead_y, '612345.678');
  assert.equal(calls[0].pipeline_type, 'prospect');
  await settled();
});

test('Enter is suppressed while an IME composition is active', async () => {
  mount();
  var calls = stubCreate(function () { return jsonResponse(201, { project_id: 7 }); });
  openNewLead();
  fill('IME-1', '1', '2');
  press('new-lead-name', 'Enter', { isComposing: true });
  press('new-lead-name', 'Enter', { keyCode: 229 });
  await new Promise(function (resolve) { setTimeout(resolve, 30); });
  assert.equal(calls.length, 0, 'a composition-confirming Enter must not create a lead');
  assert.ok(isNewLeadOpen(), 'and the control stays expanded');
});

test('a held / repeated Enter creates one lead, not many', async () => {
  mount();
  var calls = stubCreate(function () { return jsonResponse(201, { project_id: 8 }); });
  openNewLead();
  fill('REPEAT-1', '1', '2');
  press('new-lead-name', 'Enter');
  press('new-lead-name', 'Enter', { repeat: true });
  press('new-lead-name', 'Enter', { repeat: true });
  await waitFor(function () { return calls.length >= 1; });
  await settled();
  assert.equal(calls.length, 1);
});

test('a second Enter while the POST is in flight is locked out', async () => {
  mount();
  var release = null;
  var calls = stubCreate(function () {
    return new Promise(function (resolve) { release = function () { resolve(jsonResponse(201, { project_id: 9 })); }; });
  });
  openNewLead();
  fill('LOCK-1', '1', '2');
  press('new-lead-name', 'Enter');
  await waitFor(function () { return calls.length === 1; });
  assert.equal(fieldsBox().getAttribute('aria-busy'), 'true', 'the in-flight state is announced');
  assert.equal(el('new-lead-hint').textContent, 'Creating…');

  press('new-lead-name', 'Enter');
  press('new-lead-x', 'Enter');
  assert.equal(calls.length, 1, 'the lock holds until the request settles');
  release();
  await waitFor(function () { return !isNewLeadOpen(); });
  assert.equal(calls.length, 1);
});

test('Enter is ignored when the control is collapsed', async () => {
  mount();
  var calls = stubCreate(function () { return jsonResponse(201, { project_id: 10 }); });
  typeInto('new-lead-name', 'GHOST-1');
  press('new-lead-name', 'Enter');
  await new Promise(function (resolve) { setTimeout(resolve, 30); });
  assert.equal(calls.length, 0);
});

/* -------------------------------------------------------------------------
   Cancel
   ------------------------------------------------------------------------- */

test('Escape clears the three values, restores the button and creates nothing', async () => {
  mount();
  var calls = stubCreate(function () { return jsonResponse(201, { project_id: 11 }); });
  openNewLead();
  fill('CANCEL-1', '10', '20');
  press('new-lead-x', 'Escape');
  assert.ok(!isNewLeadOpen(), 'collapsed again');
  assert.ok(!el('new-lead-open').classList.contains('hidden'), 'button is back');
  assert.deepEqual(newLeadValues(), { name: '', x: '', y: '' });
  assert.equal(document.activeElement.id, 'new-lead-open', 'focus returns to the button');
  await new Promise(function (resolve) { setTimeout(resolve, 20); });
  assert.equal(calls.length, 0, 'cancel never posts');
});

test('a click outside cancels the same way', () => {
  mount();
  openNewLead();
  fill('CANCEL-2', '1', '2');
  clickOutside();
  assert.ok(!isNewLeadOpen());
  assert.deepEqual(newLeadValues(), { name: '', x: '', y: '' });
});

test('a click INSIDE the control does not cancel it', () => {
  mount();
  openNewLead();
  fill('KEEP-1', '1', '2');
  el('new-lead-x').dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
  assert.ok(isNewLeadOpen(), 'still expanded');
  assert.equal(newLeadValues().name, 'KEEP-1', 'values untouched');
});

test('cancelling leaves the board data and the active filters exactly as they were', () => {
  mount();
  setLeadRows([
    { project_id: 1, project_name: 'KEEP-1', display_stage: 'Lead Assessment', overall_status: 'In Progress', assignees: [], tracked_items: [], lead_priority: 'Low', field: 'KEEP' }
  ]);
  var before = leadFilterState();
  var rowsBefore = filteredLeads().length;

  openNewLead();
  fill('CANCEL-3', '1', '2');
  press('new-lead-name', 'Escape');

  assert.deepEqual(leadFilterState(), before, 'cancel is not a filter reset');
  assert.equal(filteredLeads().length, rowsBefore, 'cancel is not a refetch');
});

test('Escape during an in-flight request neither cancels nor duplicates it', async () => {
  mount();
  var release = null;
  var calls = stubCreate(function () {
    return new Promise(function (resolve) { release = function () { resolve(jsonResponse(201, { project_id: 12 })); }; });
  });
  openNewLead();
  fill('INFLIGHT-1', '1', '2');
  press('new-lead-name', 'Enter');
  await waitFor(function () { return calls.length === 1; });

  press('new-lead-name', 'Escape');
  clickOutside();
  assert.ok(isNewLeadOpen(), 'the request owns the control until it settles');
  assert.equal(el('new-lead-name').value, 'INFLIGHT-1', 'values are not cleared mid-flight');

  release();
  await waitFor(function () { return !isNewLeadOpen(); });
  assert.equal(calls.length, 1, 'exactly one lead was created');
});

/* -------------------------------------------------------------------------
   Validation in the DOM
   ------------------------------------------------------------------------- */

test('an empty submission shows three inline errors, focuses the first and posts nothing', async () => {
  mount();
  var calls = stubCreate(function () { return jsonResponse(201, { project_id: 13 }); });
  openNewLead();
  press('new-lead-name', 'Enter');

  assert.ok(errorShown('name') && errorShown('x') && errorShown('y'), 'all three errors visible');
  assert.equal(errorText('x'), 'Enter a valid Lead X Coordinate.');
  assert.equal(errorText('y'), 'Enter a valid Lead Y Coordinate.');
  assert.equal(el('new-lead-name').getAttribute('aria-invalid'), 'true');
  assert.equal(document.activeElement.id, 'new-lead-name', 'focus lands on the first invalid field');
  assert.ok(isNewLeadOpen(), 'stays expanded');
  await new Promise(function (resolve) { setTimeout(resolve, 20); });
  assert.equal(calls.length, 0);
});

test('a validation failure preserves everything the user typed', () => {
  mount();
  openNewLead();
  fill('PARTIAL-1', 'not-a-number', '');
  press('new-lead-y', 'Enter');
  assert.deepEqual(newLeadValues(), { name: 'PARTIAL-1', x: 'not-a-number', y: '' });
  assert.equal(errorText('x'), 'Enter a valid Lead X Coordinate.');
  assert.ok(!errorShown('name'), 'the valid field carries no error');
  assert.equal(document.activeElement.id, 'new-lead-x', 'first INVALID field, not the first field');
});

test('a duplicate name is caught client-side against the unfiltered board rowset', async () => {
  mount();
  var calls = stubCreate(function () { return jsonResponse(201, { project_id: 14 }); });
  setLeadRows([
    { project_id: 1, project_name: 'WWWW-44', display_stage: 'Lead Assessment', overall_status: 'In Progress', assignees: [], tracked_items: [], lead_priority: 'Low', field: 'WWWW' }
  ]);
  openNewLead();
  fill(' wwww-44 ', '1', '2');
  press('new-lead-name', 'Enter');

  assert.equal(errorText('name'), 'A lead with this name already exists.');
  assert.equal(document.activeElement.id, 'new-lead-name');
  await new Promise(function (resolve) { setTimeout(resolve, 20); });
  assert.equal(calls.length, 0, 'a known duplicate never reaches the server');
});

test('a server-side duplicate lands inline on the name field, not only in a toast', async () => {
  mount();
  stubCreate(function () { return jsonResponse(400, { detail: 'A lead with this name already exists.' }); });
  openNewLead();
  fill('RACE-1', '1', '2');
  press('new-lead-name', 'Enter');

  await waitFor(function () { return errorShown('name'); });
  assert.equal(errorText('name'), 'A lead with this name already exists.');
  assert.ok(isNewLeadOpen(), 'stays expanded');
  assert.equal(newLeadValues().name, 'RACE-1', 'values preserved');
});

test('a server coordinate rejection lands on the coordinate field', async () => {
  mount();
  stubCreate(function () { return jsonResponse(400, { detail: 'Enter a valid Lead Y Coordinate.' }); });
  openNewLead();
  fill('COORD-1', '1', '2');
  press('new-lead-name', 'Enter');
  await waitFor(function () { return errorShown('y'); });
  assert.equal(errorText('y'), 'Enter a valid Lead Y Coordinate.');
});

test('a server failure shows a toast, keeps the values and re-enables Enter', async () => {
  mount();
  var attempts = 0;
  var calls = stubCreate(function (n) {
    attempts = n;
    return n === 1 ? jsonResponse(500, { detail: 'Something went wrong.' })
      : jsonResponse(201, { project_id: 15 });
  });
  openNewLead();
  fill('RETRY-1', '1', '2');
  press('new-lead-name', 'Enter');

  await waitFor(function () { return /Something went wrong/.test(toastText()); });
  assert.ok(isNewLeadOpen(), 'stays expanded so the user can retry');
  assert.equal(newLeadValues().name, 'RETRY-1');
  assert.equal(el('new-lead-hint').textContent, 'Press Enter to create', 'busy state cleared');

  press('new-lead-name', 'Enter');
  await waitFor(function () { return calls.length === 2; });
  assert.equal(attempts, 2, 'Enter works again after a recoverable failure');
  await settled();
});

/* -------------------------------------------------------------------------
   Success
   ------------------------------------------------------------------------- */

test('a successful create clears the fields, restores the button and announces the toast', async () => {
  mount();
  var created = null;
  document.addEventListener('lead:created', function handler(event) {
    created = event.detail;
    document.removeEventListener('lead:created', handler);
  });
  // Only the POST answers here: the board refresh and openDetail that follow
  // are left hanging on purpose, so what this test reads is the create's own
  // outcome and not a later handler's message.
  var calls = [];
  mockFetch(function (url, options) {
    if (options && options.method === 'POST') {
      calls.push(JSON.parse(options.body));
      return jsonResponse(201, { project_id: 42 });
    }
    return new Promise(function () { /* never settles */ });
  });
  openNewLead();
  fill('DONE-1', '1', '2');
  press('new-lead-name', 'Enter');

  await waitFor(function () { return !isNewLeadOpen(); });
  assert.equal(calls.length, 1);
  assert.ok(!el('new-lead-open').classList.contains('hidden'), 'the button is back');
  assert.equal(el('new-lead-open').getAttribute('aria-expanded'), 'false');
  assert.deepEqual(newLeadValues(), { name: '', x: '', y: '' });
  assert.equal(toastText(), 'Lead created.');
  assert.equal(document.getElementById('app-message').getAttribute('role'), 'status',
    'the toast is a live region, so success is announced');
  assert.deepEqual(created, { project_id: 42 }, "the 'lead:created' event still fires");
});

test('a successful create refreshes the board through the Card 1C rowset', async () => {
  mount();
  var listed = 0;
  mockFetch(function (url, options) {
    var path = String(url);
    if (options && options.method === 'POST' && path.indexOf('/api/projects') >= 0) {
      return jsonResponse(201, { project_id: 43 });
    }
    if (path.indexOf('/api/projects?') >= 0) {
      listed += 1;
      return jsonResponse(200, [{
        project_id: 43, project_name: 'FRESH-1', display_stage: 'Lead Assessment',
        overall_status: 'In Progress', assignees: [], tracked_items: [], lead_priority: 'Low', field: 'FRESH'
      }]);
    }
    if (path.indexOf('/api/portfolio/rows') >= 0) return jsonResponse(200, { rows: [] });
    return jsonResponse(200, { project: {}, tasks: [], fields: {} });
  });
  openNewLead();
  fill('FRESH-1', '1', '2');
  press('new-lead-name', 'Enter');

  await waitFor(function () { return listed > 0 && filteredLeads().length === 1; });
  assert.equal(filteredLeads()[0].project_name, 'FRESH-1',
    'the new lead arrives via setLeadRows, so the active filters decide visibility');
  await settled();
});
