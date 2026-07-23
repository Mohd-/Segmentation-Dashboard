// Tiny zero-dependency test harness for the segments dashboard front-end.
// Native ES module; no external libraries. Loaded by runner.html, which
// dynamically imports every test module and then calls run().
//
// Surface:
//   test(name, fn)            register a test (fn may be async)
//   skip(name, reason)        register a test reported as skipped
//   assert.*                  equal / deepEqual / ok / match / throws / rejects
//   fixture(html)             a container div, auto-removed after each test
//   mockFetch(handler)        stub window.fetch; auto-restored after each test
//   restoreFetch()            restore window.fetch explicitly
//   waitFor(cond[, timeout])  poll until cond() is truthy
//   run()                     execute everything, render results, set the
//                             title, expose window.__testResults, and POST the
//                             JSON to http://127.0.0.1:<port>/results when the
//                             page URL carries ?post=<port>.

var registry = [];
var fixtures = [];
var savedFetch = null;

export function test(name, fn) { registry.push({ name: name, fn: fn }); }

export function skip(name, reason) { registry.push({ name: name, skipReason: reason || 'skipped' }); }

export function fixture(html) {
  var el = document.createElement('div');
  el.className = 'test-fixture';
  if (html) el.innerHTML = html;
  document.body.appendChild(el);
  fixtures.push(el);
  return el;
}

export function mockFetch(handler) {
  if (!savedFetch) savedFetch = window.fetch;
  window.fetch = function () {
    try {
      return Promise.resolve(handler.apply(null, arguments));
    } catch (err) {
      return Promise.reject(err);
    }
  };
}

export function restoreFetch() {
  if (savedFetch) {
    window.fetch = savedFetch;
    savedFetch = null;
  }
}

export function waitFor(cond, timeout) {
  var limit = timeout || 2000;
  var started = Date.now();
  return new Promise(function (resolve, reject) {
    (function poll() {
      var value;
      try { value = cond(); } catch (err) { reject(err); return; }
      if (value) { resolve(value); return; }
      if (Date.now() - started > limit) { reject(new Error('waitFor: condition not met within ' + limit + 'ms')); return; }
      setTimeout(poll, 10);
    })();
  });
}

function fmt(value) {
  if (value === undefined) return 'undefined';
  try {
    var text = JSON.stringify(value);
    return text === undefined ? String(value) : text;
  } catch (err) {
    return String(value);
  }
}

function fail(message) { throw new Error(message); }

function deepEq(a, b) {
  if (a === b) return true;
  if (typeof a !== typeof b) return false;
  if (a === null || b === null || typeof a !== 'object') return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  var keysA = Object.keys(a);
  var keysB = Object.keys(b);
  if (keysA.length !== keysB.length) return false;
  return keysA.every(function (key) {
    return Object.prototype.hasOwnProperty.call(b, key) && deepEq(a[key], b[key]);
  });
}

export var assert = {
  ok: function (value, message) {
    if (!value) fail((message || 'assert.ok failed') + ' (got ' + fmt(value) + ')');
  },
  equal: function (actual, expected, message) {
    if (actual !== expected) {
      fail((message || 'assert.equal failed') + ' — expected ' + fmt(expected) + ' but got ' + fmt(actual));
    }
  },
  deepEqual: function (actual, expected, message) {
    if (!deepEq(actual, expected)) {
      fail((message || 'assert.deepEqual failed') + ' — expected ' + fmt(expected) + ' but got ' + fmt(actual));
    }
  },
  match: function (value, regex, message) {
    if (!regex.test(String(value))) {
      fail((message || 'assert.match failed') + ' — ' + fmt(String(value)) + ' does not match ' + String(regex));
    }
  },
  throws: function (fn, matcher, message) {
    var caught = null;
    try { fn(); } catch (err) { caught = err || new Error('(falsy throw)'); }
    if (!caught) fail((message || 'assert.throws failed') + ' — function did not throw');
    if (matcher && !matcher.test(String(caught.message || caught))) {
      fail((message || 'assert.throws failed') + ' — thrown ' + fmt(String(caught.message || caught)) + ' does not match ' + String(matcher));
    }
    return caught;
  },
  rejects: function (promise, matcher, message) {
    return promise.then(function (value) {
      fail((message || 'assert.rejects failed') + ' — promise resolved with ' + fmt(value));
    }, function (err) {
      var caught = err || new Error('(falsy rejection)');
      if (matcher && !matcher.test(String(caught.message || caught))) {
        fail((message || 'assert.rejects failed') + ' — rejection ' + fmt(String(caught.message || caught)) + ' does not match ' + String(matcher));
      }
      return caught;
    });
  }
};

function renderRow(list, result) {
  var li = document.createElement('li');
  li.className = 'result ' + result.status;
  var badge = document.createElement('span');
  badge.className = 'badge';
  badge.textContent = result.status.toUpperCase();
  var name = document.createElement('span');
  name.className = 'name';
  name.textContent = result.name;
  li.appendChild(badge);
  li.appendChild(name);
  if (result.message) {
    var detail = document.createElement('pre');
    detail.className = 'detail';
    detail.textContent = result.message;
    li.appendChild(detail);
  }
  list.appendChild(li);
}

export async function run() {
  var list = document.getElementById('results');
  var summaryEl = document.getElementById('summary');
  var results = [];

  for (var i = 0; i < registry.length; i += 1) {
    var entry = registry[i];
    var result;
    if (entry.skipReason) {
      result = { name: entry.name, status: 'skip', message: entry.skipReason };
    } else {
      try {
        await entry.fn();
        result = { name: entry.name, status: 'pass', message: '' };
      } catch (err) {
        result = { name: entry.name, status: 'fail', message: String(err && (err.message || err)) };
      } finally {
        // Per-test cleanup: any stubbed fetch is restored and every fixture
        // container created during the test is removed.
        restoreFetch();
        fixtures.splice(0).forEach(function (el) { el.remove(); });
      }
    }
    results.push(result);
    if (list) renderRow(list, result);
  }

  var passed = results.filter(function (r) { return r.status === 'pass'; }).length;
  var failed = results.filter(function (r) { return r.status === 'fail'; }).length;
  var skipped = results.filter(function (r) { return r.status === 'skip'; }).length;
  var ran = passed + failed;
  var verdict = failed ? 'FAIL' : 'PASS';
  var headline = verdict + ' ' + passed + '/' + ran + (skipped ? ' (' + skipped + ' skipped)' : '');

  document.title = headline;
  if (summaryEl) {
    summaryEl.textContent = headline;
    summaryEl.className = 'summary ' + (failed ? 'fail' : 'pass');
  }

  var payload = {
    verdict: verdict,
    total: results.length,
    passed: passed,
    failed: failed,
    skipped: skipped,
    tests: results
  };
  window.__testResults = payload;

  // Beacon: report back to the python driver when ?post=<port> is present.
  var postPort = new URLSearchParams(window.location.search).get('post');
  if (postPort) {
    try {
      await fetch('http://127.0.0.1:' + postPort + '/results', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
    } catch (err) {
      // The receiver may be gone (manual runs); results stay on the page.
    }
  }
  return payload;
}
