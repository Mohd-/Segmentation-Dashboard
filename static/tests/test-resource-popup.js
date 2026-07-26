// Tests for static/js/views/resource-popup.js's pure functions --
// validateResourceInputs, formatStored, and the Apply-to-Lead payload
// builders. These never call openResourceAssessmentDialog (the only DOM-
// touching entry point), so the module loads fine even though
// #resource-assessment-dialog is not part of the runner.html fixtures (see
// the module's own docblock).
import { test, assert, fixture } from './harness.js';
import { validateResourceInputs, formatStored, buildCalculatePayload, buildLeadApplyFields, buildPlotMarkup } from '../js/views/resource-popup.js';

// --- validateResourceInputs -------------------------------------------------

function grvState(overrides) {
  return Object.assign({ method: 'GRV', grvP90: '12.60', grvP10: '17.30' }, overrides || {});
}
function areaState(overrides) {
  return Object.assign({ method: 'Box Model', areaP90: '1.0', areaP10: '2.0', thicknessP50: '50.0' }, overrides || {});
}

test('validateResourceInputs: valid GRV inputs pass', function () {
  assert.equal(validateResourceInputs(grvState()), null);
});

test('validateResourceInputs: valid Area x Thickness inputs pass', function () {
  assert.equal(validateResourceInputs(areaState()), null);
});

test('validateResourceInputs: GRV P90 >= P10 is rejected', function () {
  assert.equal(validateResourceInputs(grvState({ grvP90: '17.30', grvP10: '17.30' })),
    'GRV P90 must be lower than GRV P10.');
  assert.equal(validateResourceInputs(grvState({ grvP90: '20', grvP10: '17.30' })),
    'GRV P90 must be lower than GRV P10.');
});

test('validateResourceInputs: Area P90 >= P10 is rejected', function () {
  assert.equal(validateResourceInputs(areaState({ areaP90: '2.0', areaP10: '2.0' })),
    'Area P90 must be lower than Area P10.');
  assert.equal(validateResourceInputs(areaState({ areaP90: '3.0', areaP10: '2.0' })),
    'Area P90 must be lower than Area P10.');
});

test('validateResourceInputs: non-numeric values are rejected per field', function () {
  assert.equal(validateResourceInputs(grvState({ grvP90: 'abc' })), 'GRV P90 must be numeric.');
  assert.equal(validateResourceInputs(grvState({ grvP10: '' })), 'GRV P10 must be numeric.');
  assert.equal(validateResourceInputs(areaState({ areaP90: 'n/a' })), 'Area P90 must be numeric.');
  assert.equal(validateResourceInputs(areaState({ thicknessP50: 'x' })), 'Reservoir Thickness P50 must be numeric.');
});

test('validateResourceInputs: negative/zero GRV values are rejected', function () {
  assert.equal(validateResourceInputs(grvState({ grvP90: '-1' })), 'GRV P90 must be positive.');
  assert.equal(validateResourceInputs(grvState({ grvP90: '0' })), 'GRV P90 must be positive.');
});

test('validateResourceInputs: negative Area P10 reports positive, not numeric', function () {
  // areaP90 (1.0) validates first and passes; areaP10 -2 is numeric but not positive.
  assert.equal(validateResourceInputs(areaState({ areaP10: '-2' })), 'Area P10 must be positive.');
});

test('validateResourceInputs: negative Reservoir Thickness P50 is rejected', function () {
  assert.equal(validateResourceInputs(areaState({ thicknessP50: '-50' })), 'Reservoir Thickness P50 must be positive.');
});

test('validateResourceInputs: GRV method ignores area/thickness fields entirely', function () {
  // Garbage area/thickness values must not block a valid GRV submission.
  assert.equal(validateResourceInputs(grvState({ areaP90: 'garbage', areaP10: '', thicknessP50: '-1' })), null);
});

test('validateResourceInputs: Area x Thickness method ignores GRV fields entirely', function () {
  assert.equal(validateResourceInputs(areaState({ grvP90: 'garbage', grvP10: '' })), null);
});

// --- formatStored ------------------------------------------------------------

test('formatStored: below 10 uses .2f', function () {
  assert.equal(formatStored(9.99), '9.99');
  assert.equal(formatStored(0), '0.00');
  assert.equal(formatStored(3.14159), '3.14');
});

test('formatStored: 10 up to (not including) 1000 uses .1f', function () {
  assert.equal(formatStored(10), '10.0');
  assert.equal(formatStored(456.78), '456.8');
  assert.equal(formatStored(999.94), '999.9');
});

test('formatStored: below-10 branch is chosen by the raw value, not the rounded one', function () {
  // 9.999 < 10 selects the .2f branch even though it then rounds up to 10.00.
  assert.equal(formatStored(9.999), '10.00');
});

test('formatStored: 1000 and above uses .0f', function () {
  assert.equal(formatStored(1000), '1000');
  assert.equal(formatStored(12345.6), '12346');
});

test('formatStored: non-numeric input returns an empty string', function () {
  assert.equal(formatStored('abc'), '');
  assert.equal(formatStored(null), '');
  assert.equal(formatStored(undefined), '');
});

// --- buildCalculatePayload ---------------------------------------------------

test('buildCalculatePayload: GRV method sends only scenario/method/grv_*', function () {
  var payload = buildCalculatePayload(grvState({ scenario: 'dry_gas_high_pressure' }));
  assert.deepEqual(payload, { scenario: 'dry_gas_high_pressure', method: 'GRV', grv_p90: 12.6, grv_p10: 17.3 });
});

test('buildCalculatePayload: Box Model method sends only scenario/method/area_*/thickness_*', function () {
  var payload = buildCalculatePayload(areaState({ scenario: 'condensate_field_a', method: 'Box Model' }));
  assert.deepEqual(payload, {
    scenario: 'condensate_field_a', method: 'Box Model',
    area_p90_km2: 1, area_p10_km2: 2, thickness_p50_ft: 50
  });
});

// --- buildLeadApplyFields: pin the exact EAV keys ----------------------------
// These key names are a permanent contract (task_dynamic_fields rows) --
// renaming any of them orphans previously-saved data.

test('buildLeadApplyFields: GRV method, gas-only result -- exact key set', function () {
  var result = { gas: { p90: 8, p50: 12, mean: 12.3, p10: 15 } };
  var fields = buildLeadApplyFields(result, grvState({ scenario: 'dry_gas_high_pressure' }));
  assert.deepEqual(fields, {
    lead_piip_gas_p90: '8.00',
    lead_piip_gas_mean: '12.3',
    lead_piip_gas_p10: '15.0',
    lead_piip_has_liquid: '',
    lead_piip_liquid_p90: '',
    lead_piip_liquid_mean: '',
    lead_piip_liquid_p10: '',
    lead_resource_scenario: 'dry_gas_high_pressure',
    lead_calculation_method: 'GRV',
    lead_grv_p90_thousand_acre_ft: '12.60',
    lead_grv_p10_thousand_acre_ft: '17.30'
  });
  assert.deepEqual(Object.keys(fields).sort(), [
    'lead_calculation_method', 'lead_grv_p10_thousand_acre_ft', 'lead_grv_p90_thousand_acre_ft',
    'lead_piip_gas_mean', 'lead_piip_gas_p10', 'lead_piip_gas_p90', 'lead_piip_has_liquid',
    'lead_piip_liquid_mean', 'lead_piip_liquid_p10', 'lead_piip_liquid_p90', 'lead_resource_scenario'
  ]);
});

test('buildLeadApplyFields: Box Model method with condensate -- exact key set, no GRV keys', function () {
  var result = {
    gas: { p90: 100, p50: 150, mean: 155.4, p10: 210 },
    condensate: { p90: 5.2, p50: 6.1, mean: 6.05, p10: 7.9 }
  };
  var fields = buildLeadApplyFields(result, areaState({ scenario: 'condensate_field_b', method: 'Box Model' }));
  assert.deepEqual(fields, {
    lead_piip_gas_p90: '100.0',
    lead_piip_gas_mean: '155.4',
    lead_piip_gas_p10: '210.0',
    lead_piip_has_liquid: '1',
    lead_piip_liquid_p90: '5.20',
    lead_piip_liquid_mean: '6.05',
    lead_piip_liquid_p10: '7.90',
    lead_resource_scenario: 'condensate_field_b',
    lead_calculation_method: 'Box Model'
  });
  assert.ok(!('lead_grv_p90_thousand_acre_ft' in fields), 'no GRV P90 key for Box Model');
  assert.ok(!('lead_grv_p10_thousand_acre_ft' in fields), 'no GRV P10 key for Box Model');
});

// --- buildPlotMarkup (plot lightbox expand affordance) -----------------------
// Pure string builder -- #ra-plots is rebuilt wholesale on every Calculate,
// and expand clicks are handled by one delegated listener (wireOnce), so this
// is the one piece of the lightbox feature worth a DOM-free-to-write test
// (parsed through a real fixture div rather than regex-matching the string).

test('buildPlotMarkup: image carries the src and alt text', function () {
  var container = fixture(buildPlotMarkup('data:image/png;base64,AAA', 'Gas exceedance plot'));
  var img = container.querySelector('.ra-plot img');
  assert.ok(img, 'image is present');
  assert.equal(img.getAttribute('src'), 'data:image/png;base64,AAA');
  assert.equal(img.getAttribute('alt'), 'Gas exceedance plot');
});

test('buildPlotMarkup: includes a keyboard-focusable expand button labelled for the plot', function () {
  var container = fixture(buildPlotMarkup('data:image/png;base64,AAA', 'Condensate exceedance plot'));
  var button = container.querySelector('.ra-plot-expand');
  assert.ok(button, 'expand button is present');
  assert.equal(button.tagName, 'BUTTON', 'a real <button> so Enter/Space activate it natively');
  assert.equal(button.getAttribute('type'), 'button', 'never submits a form');
  assert.match(button.getAttribute('aria-label') || '', /Condensate exceedance plot/);
});

test('buildPlotMarkup: missing alt text falls back to a generic label', function () {
  var container = fixture(buildPlotMarkup('data:image/png;base64,AAA'));
  assert.equal(container.querySelector('img').getAttribute('alt'), 'Exceedance plot');
});

test('buildPlotMarkup: escapes markup-significant characters in the alt text', function () {
  var container = fixture(buildPlotMarkup('data:image/png;base64,AAA', 'Gas <script> & "quotes"'));
  // A real fixture div parses the HTML, so a broken escape would show up as
  // extra/missing elements or a mangled attribute value, not just raw text.
  assert.equal(container.querySelectorAll('.ra-plot').length, 1, 'exactly one plot card, no injected markup');
  assert.equal(container.querySelector('img').getAttribute('alt'), 'Gas <script> & "quotes"');
});
