import { test, assert, fixture, mockFetch, waitFor } from './harness.js';
import { Store } from '../js/state.js';
import { calculatorMarkup, initCalculators } from '../js/views/calculators.js';

var META_PENDING = {
  twt_thickness_coefficients: {},
  resource_scenarios: [{ id: 'dry_gas_high_pressure', label: 'Dry Gas — High Pressure', resource_type: 'gas' }]
};
var META_CONFIGURED = {
  twt_thickness_coefficients: {
    reservoir: { m: 0.4, b: 0 },
    formation: { m: 0.3, b: 30 }
  },
  resource_scenarios: META_PENDING.resource_scenarios
};

function mount(meta) {
  var previous = Store.meta;
  Store.meta = meta;
  var root = fixture('<div id="calculator-workbench"></div>').firstElementChild;
  initCalculators(root);
  return { root: root, restore: function () { Store.meta = previous; } };
}

function input(element, value) {
  element.value = value;
  element.dispatchEvent(new Event('input', { bubbles: true }));
}

function jsonResponse(body) {
  return {
    ok: true, status: 200,
    headers: { get: function () { return 'application/json'; } },
    json: function () { return Promise.resolve(body); },
    text: function () { return Promise.resolve(JSON.stringify(body)); }
  };
}

test('calculators: workbench exposes exactly five collapsed technical calculators', function () {
  var root = fixture(calculatorMarkup(META_PENDING));
  var cards = root.querySelectorAll('details.calculator-card');
  assert.equal(cards.length, 5);
  assert.deepEqual(Array.from(cards).map(function (card) { return card.dataset.calculator; }),
    ['twt', 'resources', 'reservoir', 'trap', 'seal']);
  Array.from(cards).forEach(function (card) { assert.equal(card.open, false); });
});

test('calculators: empty TWT config shows the pending note and disables conversion inputs', function () {
  var root = fixture(calculatorMarkup(META_PENDING));
  var note = root.querySelector('#calc-twt-pending');
  assert.match(note.textContent, /pending configuration/);
  assert.equal(note.classList.contains('hidden'), false);
  assert.equal(root.querySelectorAll('[data-twt-row] input:disabled').length, 4);
});

test('calculators: partial TWT config keeps the pending explanation visible for disabled rows', function () {
  var meta = {
    twt_thickness_coefficients: { reservoir: { m: 0.4, b: 0 } },
    resource_scenarios: META_PENDING.resource_scenarios
  };
  var root = fixture(calculatorMarkup(meta));
  assert.equal(root.querySelector('#calc-twt-pending').classList.contains('hidden'), false);
  assert.equal(root.querySelectorAll('[data-twt-row="reservoir"] input:disabled').length, 0);
  assert.equal(root.querySelectorAll('[data-twt-row="formation"] input:disabled').length, 2);
});

test('calculators: resource scenarios fall back only when meta omits the key', function () {
  var fallbackRoot = fixture(calculatorMarkup({ twt_thickness_coefficients: {} }));
  assert.ok(fallbackRoot.querySelectorAll('#calc-resource-scenario option').length > 0);
  assert.equal(fallbackRoot.querySelector('#calc-resource-run').disabled, false);

  var unavailableRoot = fixture(calculatorMarkup({
    twt_thickness_coefficients: {}, resource_scenarios: []
  }));
  assert.equal(unavailableRoot.querySelectorAll('#calc-resource-scenario option').length, 0);
  assert.equal(unavailableRoot.querySelector('#calc-resource-run').disabled, true);
  assert.equal(unavailableRoot.querySelector('#calc-resource-unavailable').classList.contains('hidden'), false);
});

test('calculators: TWT conversion reuses configured coefficients in both directions', function () {
  var mounted = mount(META_CONFIGURED);
  try {
    var twt = mounted.root.querySelector('#calc-twt-reservoir');
    var thickness = mounted.root.querySelector('#calc-thickness-reservoir');
    input(twt, '500');
    assert.equal(thickness.value, '200');
    input(thickness, '100');
    assert.equal(twt.value, '250');
  } finally { mounted.restore(); }
});

test('calculators: Trap and Seal readouts update from the shared client formula modules', function () {
  var mounted = mount(META_PENDING);
  try {
    input(mounted.root.querySelector('#calc-trap-sarah'), '100');
    input(mounted.root.querySelector('#calc-trap-quwarah'), '130');
    assert.equal(mounted.root.querySelector('#calc-trap-result').textContent, '80');

    input(mounted.root.querySelector('#calc-seal-activity'), '0.9');
    input(mounted.root.querySelector('#calc-seal-dip'), '0.3');
    input(mounted.root.querySelector('#calc-seal-azimuth'), '0.6');
    input(mounted.root.querySelector('#calc-seal-fault'), '0.9');
    input(mounted.root.querySelector('#calc-seal-permeability'), '0.5');
    assert.equal(mounted.root.querySelector('#calc-seal-result').textContent, '30');
  } finally { mounted.restore(); }
});

test('calculators: Trap and Seal reject partial, non-finite, and out-of-range values', function () {
  var mounted = mount(META_PENDING);
  try {
    input(mounted.root.querySelector('#calc-trap-sarah'), '100');
    assert.match(mounted.root.querySelector('#calc-trap-error').textContent, /Both thickness/);
    input(mounted.root.querySelector('#calc-trap-quwarah'), '-1');
    assert.equal(mounted.root.querySelector('#calc-trap-result').textContent, '—');
    assert.match(mounted.root.querySelector('#calc-trap-error').textContent, /positive finite/);

    input(mounted.root.querySelector('#calc-seal-activity'), '2');
    input(mounted.root.querySelector('#calc-seal-permeability'), '0.5');
    assert.equal(mounted.root.querySelector('#calc-seal-result').textContent, '—');
    assert.match(mounted.root.querySelector('#calc-seal-error').textContent, /0 to 1/);

    input(mounted.root.querySelector('#calc-seal-activity'), '0.5');
    assert.match(mounted.root.querySelector('#calc-seal-error').textContent, /required when activity/);
    ['activity', 'permeability'].forEach(function (id) {
      input(mounted.root.querySelector('#calc-seal-' + id), '');
    });
    assert.equal(mounted.root.querySelector('#calc-seal-error').classList.contains('hidden'), true);
  } finally { mounted.restore(); }
});

test('calculators: dynamic result surfaces are polite live regions', function () {
  var root = fixture(calculatorMarkup(META_PENDING));
  ['calc-resource-results', 'calc-reservoir-result', 'calc-trap-result', 'calc-seal-result'].forEach(function (id) {
    assert.equal(root.querySelector('#' + id).getAttribute('aria-live'), 'polite', id);
  });
  assert.equal(root.querySelector('#calc-resource-results').closest('.calc-results').getAttribute('aria-busy'), 'false');
  assert.equal(root.querySelector('#calc-reservoir-result').closest('.calc-results').getAttribute('aria-busy'), 'false');
});

test('calculators: Monte Carlo run uses the project-free API and renders governed results', function () {
  var mounted = mount(META_PENDING);
  var seen = null;
  mockFetch(function (url, options) {
    seen = { url: String(url), body: JSON.parse(options.body) };
    return jsonResponse({
      gas: { p90: 12.2, p50: 18, mean: 19.8, p10: 28.1 },
      units: { gas: 'BCF' }, plots: { gas: 'data:image/png;base64,AAA' }
    });
  });
  mounted.root.querySelector('#calc-resource-run').click();
  return waitFor(function () {
    return mounted.root.querySelector('#calc-resource-results output');
  }).then(function () {
    assert.match(seen.url, /^\/api\/calculators\/resources\?/);
    assert.deepEqual(seen.body, {
      scenario: 'dry_gas_high_pressure', method: 'GRV', grv_p90: 12.6, grv_p10: 17.3
    });
    assert.equal(mounted.root.querySelector('#calc-resource-results output').textContent, '12.2');
    assert.equal(mounted.root.querySelectorAll('#calc-resource-plots img').length, 1);
  }).finally(mounted.restore);
});

test('calculators: Reservoir CoS uses the project-free model endpoint', function () {
  var mounted = mount(META_PENDING);
  var seen = null;
  mockFetch(function (url, options) {
    seen = { url: String(url), body: JSON.parse(options.body) };
    return jsonResponse([{ reservoir_cos_pct: '80' }]);
  });
  input(mounted.root.querySelector('#calc-reservoir-amplitude'), '0.7');
  input(mounted.root.querySelector('#calc-reservoir-bts'), '0.5');
  mounted.root.querySelector('#calc-reservoir-pullup').value = 'Yes';
  mounted.root.querySelector('#calc-reservoir-run').click();
  return waitFor(function () {
    return mounted.root.querySelector('#calc-reservoir-result').textContent === '80';
  }).then(function () {
    assert.match(seen.url, /^\/api\/calculators\/reservoir-cos\?/);
    assert.deepEqual(seen.body, { amplitude_ratio: '0.7', base_tight_sarah: '0.5', pull_up: 'Yes' });
  }).finally(mounted.restore);
});

test('calculators: editing Resource inputs invalidates an in-flight response and restores the controls', function () {
  var mounted = mount(META_PENDING);
  var resolveRequest;
  var calls = 0;
  mockFetch(function () {
    calls += 1;
    return new Promise(function (resolve) { resolveRequest = resolve; });
  });
  var button = mounted.root.querySelector('#calc-resource-run');
  button.click();
  button.click();
  assert.equal(calls, 1, 'disabled button suppresses a double click');
  assert.equal(button.disabled, true);
  assert.equal(mounted.root.querySelector('#calc-resource-results').closest('.calc-results').getAttribute('aria-busy'), 'true');

  input(mounted.root.querySelector('#calc-resource-grv-p90'), '13');
  assert.equal(button.disabled, false);
  assert.equal(mounted.root.querySelector('#calc-resource-results').closest('.calc-results').getAttribute('aria-busy'), 'false');
  resolveRequest(jsonResponse({
    gas: { p90: 99, mean: 100, p10: 101 }, units: { gas: 'BCF' }, plots: {}
  }));
  return new Promise(function (resolve) { setTimeout(resolve, 20); }).then(function () {
    assert.equal(mounted.root.querySelector('#calc-resource-results output'), null,
      'response for the previous input snapshot did not render');
    assert.equal(mounted.root.querySelectorAll('#calc-resource-plots img').length, 0);
  }).finally(mounted.restore);
});

test('calculators: a failed Resource rerun clears prior results and plots', function () {
  var mounted = mount(META_PENDING);
  var call = 0;
  mockFetch(function () {
    call += 1;
    if (call === 1) return jsonResponse({
      gas: { p90: 12.2, mean: 19.8, p10: 28.1 }, units: { gas: 'BCF' },
      plots: { gas: 'data:image/png;base64,AAA' }
    });
    throw new Error('simulation unavailable');
  });
  mounted.root.querySelector('#calc-resource-run').click();
  return waitFor(function () { return mounted.root.querySelector('#calc-resource-results output'); }).then(function () {
    input(mounted.root.querySelector('#calc-resource-grv-p90'), '13');
    mounted.root.querySelector('#calc-resource-run').click();
    return waitFor(function () {
      return mounted.root.querySelector('#calc-resource-error').textContent === 'simulation unavailable';
    });
  }).then(function () {
    assert.equal(mounted.root.querySelector('#calc-resource-results output'), null);
    assert.equal(mounted.root.querySelectorAll('#calc-resource-plots img').length, 0);
  }).finally(mounted.restore);
});

test('calculators: editing Reservoir inputs invalidates an in-flight response', function () {
  var mounted = mount(META_PENDING);
  var resolveRequest;
  mockFetch(function () {
    return new Promise(function (resolve) { resolveRequest = resolve; });
  });
  input(mounted.root.querySelector('#calc-reservoir-amplitude'), '0.7');
  input(mounted.root.querySelector('#calc-reservoir-bts'), '0.5');
  mounted.root.querySelector('#calc-reservoir-pullup').value = 'Yes';
  mounted.root.querySelector('#calc-reservoir-run').click();
  input(mounted.root.querySelector('#calc-reservoir-amplitude'), '0.8');
  resolveRequest(jsonResponse([{ reservoir_cos_pct: '80' }]));
  return new Promise(function (resolve) { setTimeout(resolve, 20); }).then(function () {
    assert.equal(mounted.root.querySelector('#calc-reservoir-result').textContent, '—');
    assert.equal(mounted.root.querySelector('#calc-reservoir-run').disabled, false);
  }).finally(mounted.restore);
});
