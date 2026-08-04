// Tests for static/js/views/resource-calculator.js's pure functions --
// validateResourceInputs, formatStored, the Apply-to-Lead payload builders,
// buildPlotMarkup, and the read-only results-display builders
// (resultsFromStoredFields/resultsFromCalculation/buildResultsMarkup). These
// never call renderResourceCalculator (the only DOM-touching entry point),
// so the module loads fine even though #resource-calculator-panel /
// #resource-assessment-dialog are not part of the runner.html fixtures (see
// the module's own docblock).
import { test, assert, fixture } from './harness.js';
import {
  validateResourceInputs, formatStored, buildCalculatePayload, buildLeadApplyFields, buildPlotMarkup,
  buildResultsMarkup, resultsFromStoredFields, resultsFromCalculation,
  fieldPrefixForStep, FIELD_PREFIX_BY_STEP
} from '../js/views/resource-calculator.js';

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

// Same generic 9999 sanity cap as the regular step forms' validateStepFields
// (schema.js) -- none of GRV/area/thickness are exempt (no bigOk-equivalent
// here; all are well under 9999 in real use).
test('validateResourceInputs: a value over the 9999 cap is rejected (GRV)', function () {
  assert.equal(validateResourceInputs(grvState({ grvP90: '10000' })),
    'GRV P90 looks too large (max 9999).');
  assert.equal(validateResourceInputs(grvState({ grvP90: '9999', grvP10: '10000' })),
    'GRV P10 looks too large (max 9999).');
});

test('validateResourceInputs: a value over the 9999 cap is rejected (Area x Thickness)', function () {
  assert.equal(validateResourceInputs(areaState({ areaP90: '10000' })),
    'Area P90 looks too large (max 9999).');
  assert.equal(validateResourceInputs(areaState({ thicknessP50: '10000' })),
    'Reservoir Thickness P50 looks too large (max 9999).');
});

test('validateResourceInputs: exactly 9999 is still in range', function () {
  assert.equal(validateResourceInputs(grvState({ grvP90: '9998', grvP10: '9999' })), null);
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
// renaming any of them orphans previously-saved data. Unchanged by the
// pop-up -> inline-calculator move: Apply still writes them the same way.

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
// and expand clicks are handled by one delegated listener
// (wirePlotsDialogOnce), so this is the one piece of the lightbox feature
// worth a DOM-free-to-write test (parsed through a real fixture div rather
// than regex-matching the string).

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

// --- resultsFromStoredFields / resultsFromCalculation / buildResultsMarkup --
// The read-only PIIP results display (step body, not the plots dialog): its
// value-shape builders and markup builder are all pure, so they're covered
// without touching the DOM.

test('resultsFromStoredFields: gas trio, no liquid when lead_piip_has_liquid is unset', function () {
  var values = resultsFromStoredFields({
    lead_piip_gas_p90: '12.0', lead_piip_gas_mean: '19.4', lead_piip_gas_p10: '27.6'
  });
  assert.deepEqual(values, { gas: { p90: '12.0', mean: '19.4', p10: '27.6' }, liquid: null });
});

test('resultsFromStoredFields: liquid trio included when lead_piip_has_liquid is truthy', function () {
  var values = resultsFromStoredFields({
    lead_piip_gas_p90: '12.0', lead_piip_gas_mean: '19.4', lead_piip_gas_p10: '27.6',
    lead_piip_has_liquid: '1',
    lead_piip_liquid_p90: '1.50', lead_piip_liquid_mean: '2.00', lead_piip_liquid_p10: '2.60'
  });
  assert.deepEqual(values, {
    gas: { p90: '12.0', mean: '19.4', p10: '27.6' },
    liquid: { p90: '1.50', mean: '2.00', p10: '2.60' }
  });
});

test('resultsFromStoredFields: a blank/never-calculated task yields empty strings, not undefined', function () {
  assert.deepEqual(resultsFromStoredFields({}), { gas: { p90: '', mean: '', p10: '' }, liquid: null });
  assert.deepEqual(resultsFromStoredFields(undefined), { gas: { p90: '', mean: '', p10: '' }, liquid: null });
});

test('resultsFromCalculation: gas only, formats every value with formatStored', function () {
  var values = resultsFromCalculation({ gas: { p90: 12, p50: 18.8, mean: 19.39, p10: 27.56 } });
  assert.deepEqual(values, { gas: { p90: '12.0', mean: '19.4', p10: '27.6' }, liquid: null });
});

test('resultsFromCalculation: condensate present adds the liquid trio', function () {
  var values = resultsFromCalculation({
    gas: { p90: 4.5, p50: 10, mean: 11.47, p10: 20.16 },
    condensate: { p90: 1.57, p50: 3.5, mean: 4.02, p10: 7.12 }
  });
  assert.deepEqual(values, {
    gas: { p90: '4.50', mean: '11.5', p10: '20.2' },
    liquid: { p90: '1.57', mean: '4.02', p10: '7.12' }
  });
});

test('buildResultsMarkup: gas-only renders one section, no Liquid heading', function () {
  var container = fixture(buildResultsMarkup({ gas: { p90: '12.0', mean: '19.4', p10: '27.6' }, liquid: null }));
  var headings = container.querySelectorAll('.field-section-label');
  assert.equal(headings.length, 1);
  assert.equal(headings[0].textContent, 'Gas (BCF)');
  var outputs = container.querySelectorAll('output');
  assert.equal(outputs.length, 3);
  assert.deepEqual(Array.from(outputs).map(function (o) { return o.textContent; }), ['12.0', '19.4', '27.6']);
});

test('buildResultsMarkup: liquid trio adds a second section', function () {
  var container = fixture(buildResultsMarkup({
    gas: { p90: '100.0', mean: '155.4', p10: '210.0' },
    liquid: { p90: '5.20', mean: '6.05', p10: '7.90' }
  }));
  var headings = container.querySelectorAll('.field-section-label');
  assert.deepEqual(Array.from(headings).map(function (h) { return h.textContent; }), ['Gas (BCF)', 'Liquid (MMSTB)']);
  var outputs = container.querySelectorAll('output');
  assert.equal(outputs.length, 6);
});

test('buildResultsMarkup: an empty/never-calculated value shows a placeholder dash, not a blank output', function () {
  var container = fixture(buildResultsMarkup({ gas: { p90: '', mean: '', p10: '' }, liquid: null }));
  var outputs = container.querySelectorAll('output');
  Array.from(outputs).forEach(function (o) { assert.equal(o.textContent, '—'); });
});

test('buildResultsMarkup: values are display-only <output>s, not editable inputs', function () {
  var container = fixture(buildResultsMarkup({ gas: { p90: '1', mean: '2', p10: '3' }, liquid: null }));
  assert.equal(container.querySelectorAll('input').length, 0);
  assert.equal(container.querySelectorAll('.calculated-output').length, 3);
});


// --- calculator ownership ---------------------------------------------------

test('fieldPrefixForStep: only Lead Assessment owns the in-app calculator', function () {
  assert.equal(fieldPrefixForStep('Resource Assessment'), 'lead');
  assert.equal(FIELD_PREFIX_BY_STEP['Pre-Drilling GeoX Assessment'], undefined,
    'GeoX records external software results and is deliberately not a host');
  // Anything else (and a missing name) falls back to the lead family, so no
  // caller can silently write an un-prefixed key.
  assert.equal(fieldPrefixForStep('Pre-Drilling GeoX Assessment'), 'lead');
  assert.equal(fieldPrefixForStep('Reservoir CoS'), 'lead');
  assert.equal(fieldPrefixForStep(undefined), 'lead');
});

test('buildLeadApplyFields: an explicit legacy prefix is still mechanically supported', function () {
  var result = { gas: { p90: 1.5, mean: 2.5, p10: 3.5 } };
  var fields = buildLeadApplyFields(result, { method: 'GRV', grvP90: '1', grvP10: '2',
                                              scenario: 'dry_gas_high_pressure' }, 'pre_drill');
  assert.equal(fields.pre_drill_piip_gas_mean, '2.50');
  assert.equal(fields.pre_drill_calculation_method, 'GRV');
  assert.equal(fields.pre_drill_grv_p90_thousand_acre_ft, '1');
  // ... and NOTHING lands in the lead family, which a different step owns.
  assert.equal(fields.lead_piip_gas_mean, undefined);
});

test('resultsFromStoredFields: reads the prefix it is given, lead by default', function () {
  var stored = { lead_piip_gas_mean: '9.00', pre_drill_piip_gas_mean: '12.00' };
  assert.equal(resultsFromStoredFields(stored).gas.mean, '9.00');
  assert.equal(resultsFromStoredFields(stored, 'pre_drill').gas.mean, '12.00');
});
