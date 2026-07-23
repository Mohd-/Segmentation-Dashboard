// Tests for static/js/views/transitions.js — role gate plus the promote /
// recall confirmation flows against the real #app-dialog fixture (markup in
// runner.html, ids copied from index.html). Confirm paths mock fetch so the
// /flags PATCH never leaves the page.
import { test, assert, mockFetch, waitFor } from './harness.js';
import { canTransitionPhase, promoteProject, recallProject } from '../js/views/transitions.js';
import { Store } from '../js/state.js';

function byId(id) { return document.getElementById(id); }

function withUser(user, fn) {
  var saved = Store.user;
  Store.user = user;
  var result;
  try { result = fn(); } catch (err) { Store.user = saved; throw err; }
  return Promise.resolve(result).finally(function () { Store.user = saved; });
}

test('transitions.canTransitionPhase: supervisor only (anonymous counts)', function () {
  return withUser(null, function () {
    assert.equal(canTransitionPhase(), true, 'anonymous dev-mode acts as supervisor');
  }).then(function () {
    return withUser({ name: 'S', role: 'supervisor' }, function () {
      assert.equal(canTransitionPhase(), true);
    });
  }).then(function () {
    return withUser({ name: 'E', role: 'employee' }, function () {
      assert.equal(canTransitionPhase(), false);
    });
  }).then(function () {
    return withUser({ name: 'T', role: 'staff' }, function () {
      assert.equal(canTransitionPhase(), false, 'staff cannot transition');
    });
  });
});

test('transitions.promoteProject: dialog content, year options, cancel resolves null', function () {
  var dialog = byId('app-dialog');
  assert.ok(dialog, '#app-dialog fixture present');
  var tasks = [{ status: 'Approved' }, { status: 'In Progress' }, { status: 'Ready' }];
  var pending = promoteProject({ project_id: 5, business_plan_year: '2033' }, tasks, 'Tester');
  return waitFor(function () { return dialog.open; }).then(function () {
    assert.equal(byId('app-dialog-title').textContent, 'Promote to BP Well');
    var message = byId('app-dialog-message').textContent;
    assert.match(message, /1 of 3 prospect steps approved\./, 'progress line uses DONE (Approved only)');
    assert.match(message, /before maturation is complete/, 'early-promotion warning shown');
    var select = byId('app-dialog-select');
    assert.equal(select.options.length, 15, 'years 2026..2040');
    assert.equal(select.options[0].value, '2026');
    assert.equal(select.options[14].value, '2040');
    assert.equal(select.value, '2033', 'project business_plan_year preselected');
    assert.equal(byId('app-dialog-select-caption').textContent, 'Business Plan Year');
    byId('app-dialog-cancel').click();
    return pending;
  }).then(function (result) {
    assert.equal(result, null, 'cancel resolves null (no /flags call)');
  });
});

test('transitions.promoteProject clamps an out-of-range year to 2026 and omits progress lines without tasks', function () {
  var dialog = byId('app-dialog');
  var pending = promoteProject({ project_id: 6, business_plan_year: '2050' }, [], 'Tester');
  return waitFor(function () { return dialog.open; }).then(function () {
    assert.equal(byId('app-dialog-select').value, '2026');
    var message = byId('app-dialog-message').textContent;
    assert.ok(message.indexOf('prospect steps approved') < 0, 'no progress line for empty tasks');
    assert.match(message, /Lead Summary snapshot/, 'always explains the promotion effect');
    byId('app-dialog-cancel').click();
    return pending;
  }).then(function (result) {
    assert.equal(result, null);
  });
});

test('transitions.promoteProject confirm PATCHes /flags with the selected year', function () {
  var dialog = byId('app-dialog');
  var seen = null;
  mockFetch(function (url, opts) {
    seen = { url: String(url), opts: opts };
    return {
      ok: true, status: 200,
      headers: { get: function () { return 'application/json'; } },
      json: function () { return Promise.resolve({ ok: true, pipeline_type: 'bp' }); },
      text: function () { return Promise.resolve('{}'); }
    };
  });
  var pending = promoteProject({ project_id: 7, business_plan_year: '2031' }, [], 'Tester');
  return waitFor(function () { return dialog.open; }).then(function () {
    // Simulate the dialog-form confirm: method="dialog" submit sets returnValue
    // and closes; the handler resolves the select's value.
    dialog.returnValue = 'confirm';
    dialog.close();
    return pending;
  }).then(function (result) {
    assert.deepEqual(result, { ok: true, pipeline_type: 'bp' }, 'resolves the /flags response');
    assert.match(seen.url, /^\/api\/projects\/7\/flags\?_v=\d+&_t=\d+$/);
    assert.equal(seen.opts.method, 'PATCH');
    assert.deepEqual(JSON.parse(seen.opts.body), {
      business_plan_enabled: true,
      business_plan_year: '2031',
      changed_by: 'Tester'
    });
  });
});

test('transitions.recallProject: danger confirm, cancel resolves null', function () {
  var dialog = byId('app-dialog');
  var pending = recallProject({ project_id: 8, project_name: 'MDFT-3' }, 'Tester');
  return waitFor(function () { return dialog.open; }).then(function () {
    assert.equal(byId('app-dialog-title').textContent, 'Recall to Lead Phase');
    assert.match(byId('app-dialog-message').textContent, /removes "MDFT-3" from the Business Plan/);
    assert.ok(byId('app-dialog-confirm').classList.contains('danger'), 'confirm button marked danger');
    assert.equal(byId('app-dialog-confirm').textContent, 'Recall');
    byId('app-dialog-cancel').click();
    return pending;
  }).then(function (result) {
    assert.equal(result, null, 'cancel resolves null');
  });
});

test('transitions.recallProject confirm PATCHes /flags with business_plan_enabled false', function () {
  var dialog = byId('app-dialog');
  var seen = null;
  mockFetch(function (url, opts) {
    seen = { url: String(url), opts: opts };
    return {
      ok: true, status: 200,
      headers: { get: function () { return 'application/json'; } },
      json: function () { return Promise.resolve({ ok: true }); },
      text: function () { return Promise.resolve('{}'); }
    };
  });
  var pending = recallProject({ project_id: 9, project_name: 'W-1' }, 'Tester');
  return waitFor(function () { return dialog.open; }).then(function () {
    dialog.returnValue = 'confirm';
    dialog.close();
    return pending;
  }).then(function (result) {
    assert.deepEqual(result, { ok: true });
    assert.match(seen.url, /^\/api\/projects\/9\/flags\?/);
    assert.deepEqual(JSON.parse(seen.opts.body), {
      business_plan_enabled: false,
      changed_by: 'Tester'
    });
  });
});
