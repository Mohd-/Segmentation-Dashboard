// Tests for static/js/api.js — URL building, option shaping, response
// handling via a mocked window.fetch, and the 401 → login-dialog path (the
// #login-dialog fixture markup lives in runner.html).
import { test, assert, mockFetch, waitFor } from './harness.js';
import { requestUrl, jsonOptions, api, API } from '../js/api.js';
import { Store } from '../js/state.js';

// Minimal Response stand-ins: api.js only touches ok/status/headers.get/json/text.
function jsonResponse(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status: status,
    headers: { get: function () { return 'application/json; charset=utf-8'; } },
    json: function () { return Promise.resolve(body); },
    text: function () { return Promise.resolve(JSON.stringify(body)); }
  };
}

function textResponse(status, text) {
  return {
    ok: status >= 200 && status < 300,
    status: status,
    headers: { get: function () { return 'text/plain'; } },
    json: function () { return Promise.reject(new Error('not json')); },
    text: function () { return Promise.resolve(text); }
  };
}

// --- requestUrl / jsonOptions ---------------------------------------------

test('api.requestUrl appends _v and _t with ? on a bare path', function () {
  var url = requestUrl('/api/projects');
  assert.match(url, /^\/api\/projects\?_v=\d+&_t=\d+$/);
});

test('api.requestUrl appends with & when a query already exists', function () {
  var url = requestUrl('/api/projects?stage=Risking');
  assert.match(url, /^\/api\/projects\?stage=Risking&_v=\d+&_t=\d+$/);
});

test('api.requestUrl cache-buster moves with time', function () {
  var t = Number(requestUrl('/x').match(/_t=(\d+)$/)[1]);
  var now = Date.now();
  assert.ok(Math.abs(now - t) < 5000, '_t is a current Date.now() timestamp');
});

test('api.jsonOptions shapes method/headers/body', function () {
  var opts = jsonOptions('PATCH', { a: 1 });
  assert.equal(opts.method, 'PATCH');
  assert.deepEqual(opts.headers, { 'Content-Type': 'application/json' });
  assert.equal(opts.body, '{"a":1}');
  // Payload defaults to {}.
  assert.equal(jsonOptions('POST').body, '{}');
});

// --- api() through a mocked fetch -----------------------------------------

test('api() resolves parsed JSON on 200 and forces cache no-store', function () {
  var seen = null;
  mockFetch(function (url, opts) {
    seen = { url: url, opts: opts };
    return jsonResponse(200, { hello: 'world' });
  });
  return api('/api/thing', { method: 'GET' }).then(function (body) {
    assert.deepEqual(body, { hello: 'world' });
    assert.match(seen.url, /^\/api\/thing\?_v=\d+&_t=\d+$/);
    assert.equal(seen.opts.cache, 'no-store', 'cache: no-store is set on the options');
    assert.equal(seen.opts.method, 'GET');
  });
});

test('api() non-OK JSON error surfaces payload.detail as the Error message', function () {
  mockFetch(function () { return jsonResponse(500, { detail: 'stage gate failed' }); });
  return assert.rejects(api('/api/thing'), /^stage gate failed$/);
});

test('api() non-OK JSON without detail falls back to message, then raw JSON', function () {
  mockFetch(function () { return jsonResponse(500, { message: 'plain message' }); });
  return assert.rejects(api('/api/thing'), /^plain message$/).then(function () {
    mockFetch(function () { return jsonResponse(500, { odd: true }); });
    return assert.rejects(api('/api/thing'), /\{"odd":true\}/);
  });
});

test('api() non-OK text error surfaces the body text', function () {
  mockFetch(function () { return textResponse(500, 'Internal Server Error page'); });
  return assert.rejects(api('/api/thing'), /^Internal Server Error page$/);
});

test('API.saveFields stamps changed_by with the current user name', function () {
  var seen = null;
  var savedUser = Store.user;
  Store.user = { name: 'Field Tester', role: 'staff' };
  mockFetch(function (url, opts) {
    seen = { url: url, opts: opts };
    return jsonResponse(200, { ok: true });
  });
  return API.saveFields(9, { a: '1' }).then(function () {
    assert.match(seen.url, /^\/api\/tasks\/9\/dynamic-fields\?/);
    assert.equal(seen.opts.method, 'PATCH');
    var body = JSON.parse(seen.opts.body);
    assert.deepEqual(body, { fields: { a: '1' }, changed_by: 'Field Tester' });
  }).finally(function () { Store.user = savedUser; });
});

// --- 401 → login dialog ----------------------------------------------------

test('api() on 401 opens the login dialog; dismissal surfaces the original 401', function () {
  var dialog = document.getElementById('login-dialog');
  assert.ok(dialog, '#login-dialog fixture present in runner.html');
  mockFetch(function (url) {
    if (String(url).indexOf('/api/users') === 0) {
      return jsonResponse(200, [{ name: 'Supervisor', role: 'supervisor' }]);
    }
    return jsonResponse(401, { detail: 'login required' });
  });
  var pending = api('/api/thing');
  return waitFor(function () { return dialog.open; }).then(function () {
    var select = document.getElementById('login-name');
    return waitFor(function () { return select.options.length === 1; }).then(function () {
      assert.equal(select.options[0].textContent, 'Supervisor', 'dialog select filled from /api/users');
      dialog.close(); // Esc / dismissal path
      return assert.rejects(pending, /^login required$/);
    });
  });
});

test('api() on 401: successful login closes the dialog and retries once', function () {
  var dialog = document.getElementById('login-dialog');
  var savedUser = Store.user;
  Store.user = null;
  var thingCalls = 0;
  mockFetch(function (url, opts) {
    var path = String(url);
    if (path.indexOf('/api/users') === 0) return jsonResponse(200, [{ name: 'Supervisor', role: 'supervisor' }]);
    if (path.indexOf('/api/login') === 0) {
      var body = JSON.parse(opts.body);
      assert.equal(body.name, 'Supervisor');
      return jsonResponse(200, { ok: true, name: 'Supervisor', role: 'supervisor' });
    }
    if (path.indexOf('/api/thing') === 0) {
      thingCalls += 1;
      return thingCalls === 1 ? jsonResponse(401, { detail: 'login required' })
                              : jsonResponse(200, { granted: true });
    }
    throw new Error('unexpected fetch: ' + path);
  });
  var pending = api('/api/thing');
  return waitFor(function () { return dialog.open; }).then(function () {
    var select = document.getElementById('login-name');
    return waitFor(function () { return select.options.length === 1; });
  }).then(function () {
    document.getElementById('login-form').dispatchEvent(
      new Event('submit', { bubbles: true, cancelable: true }));
    return pending;
  }).then(function (body) {
    assert.deepEqual(body, { granted: true }, 'retry after login resolves the real payload');
    assert.equal(thingCalls, 2, 'original request retried exactly once');
    assert.equal(dialog.open, false, 'login dialog closed after success');
    assert.ok(Store.user && Store.user.name === 'Supervisor', 'performLogin set Store.user');
  }).finally(function () {
    Store.user = savedUser;
    if (dialog.open) dialog.close();
  });
});
