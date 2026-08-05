/* Card 2B — the CONSOLIDATED LEAD ASSESSMENT workspace
   (static/js/views/lead-assessment.js).

   The module is deliberately split: every RULE is a pure exported function
   (validation, the TWT<->thickness conversion, the GRV-vs-box-model method
   precedence, the save grouping, the dynamic TVDSS label, which scenarios
   show Liquid), and only the mount/wire half touches Store or the network.
   The tests follow that split — most of what is below hands a plain object to
   a pure function and asserts on the result, and only the last group mounts
   the page into a fixture with a stubbed fetch. */
import { test, assert, fixture, mockFetch, waitFor } from './harness.js';
import { Store } from '../js/state.js';
import { SCHEMA } from '../js/schema.js';
import { renderFields, getFields } from '../js/views/detail-form.js';
import {
  LEAD_ASSESSMENT_STEPS, LEGACY_LEAD_ASSESSMENT_STEPS, PRIMARY_STEP, KEY_OWNER, FOLDER_SECTION_KEY,
  MESSAGES, HELPER_TEXT, PIIP_HEADING, POLYGONS_LABEL, LABELS, DEFAULT_SCENARIO,
  numberError, tvdssError, validateThicknessSection, validateVolumeSection,
  validateLeadAssessment, firstError,
  coefficientsFor, conversionConfigured, thicknessFromTwt, twtFromThickness,
  applyConversion, resolveSourceMode, isDerivedCell, columnOf, ROW_KEYS,
  resolveCalculation, calculationPayload, calculationSignature,
  scenarioList, showsLiquid, primaryFormationName, tvdssLabel,
  buildSavePlan, earlierComments,
  workspaceMarkup, thicknessSectionMarkup, volumeSectionMarkup,
  structureSectionMarkup, piipSectionMarkup, earlierCommentsMarkup,
  isLeadAssessmentStep, leadAssessmentActive, renderLeadAssessment,
  teardownLeadAssessment, readFormValues, scheduleCalculation
} from '../js/views/lead-assessment.js';

// A calibrated pair for the conversion tests. Deliberately NOT the shipped
// value: config.TWT_THICKNESS_COEFFICIENTS ships EMPTY, so the "conversion is
// configured" mode only exists in a deployment that has been given real
// coefficients — and in these tests, which stub Store.meta with this.
var COEFFICIENTS = {
  reservoir: { m: 0.5, b: -50 },   // 500 ms -> 200 ft ; 200 ft -> 500 ms
  formation: { m: 0.4, b: -20 }    // 500 ms -> 180 ft
};
var META_WITH_COEFFICIENTS = { twt_thickness_coefficients: COEFFICIENTS };
var META_PENDING = { twt_thickness_coefficients: {} };

var SCENARIOS = [
  { id: 'dry_gas_high_pressure', label: 'Dry Gas - High Pressure Zone', resource_type: 'dry_gas' },
  { id: 'dry_gas_low_pressure', label: 'Dry Gas - Low Pressure Zone', resource_type: 'dry_gas' },
  { id: 'condensate_field_a', label: 'Condensate - Field A', resource_type: 'condensate' },
  { id: 'condensate_field_b', label: 'Condensate - Field B', resource_type: 'condensate' }
];

// A fully valid page, so a test can knock ONE value out and see only that
// rule fire.
function goodValues(overrides) {
  return Object.assign({
    twt_reservoir_ms: '1500',
    twt_formation_ms: '1800',
    reservoir_thickness_ft: '200',
    formation_thickness_ft: '500',
    p90_area_km2: '12.60',
    p10_area_km2: '17.30',
    grv_p90_thousand_acre_ft: '12.60',
    grv_p10_thousand_acre_ft: '17.30',
    top_formation_tvdss_ft: '-6500',
    polygons_surfaces_loaded: ''
  }, overrides || {});
}

/* -------------------------------------------------------------------------
   The storage contract — which task owns which key
   ------------------------------------------------------------------------- */

test('lead-assessment: one current task plus legacy rolling-deploy claims', function () {
  assert.deepEqual(LEAD_ASSESSMENT_STEPS, ['Lead Assessment']);
  LEAD_ASSESSMENT_STEPS.forEach(function (name) {
    assert.ok(isLeadAssessmentStep(name), name + ' opens the consolidated page');
  });
  LEGACY_LEAD_ASSESSMENT_STEPS.forEach(function (name) {
    assert.ok(isLeadAssessmentStep(name), name + ' remains a safe legacy claim');
  });
  ['Reservoir CoS', 'Segmentation Slides', 'SAD Model', 'Well Site Location'].forEach(function (name) {
    assert.ok(!isLeadAssessmentStep(name), name + ' keeps the generic per-step form');
  });
});

test('lead-assessment: every edited key names its owning task', function () {
  Object.keys(KEY_OWNER).forEach(function (key) {
    assert.equal(KEY_OWNER[key], 'Lead Assessment', key + ' belongs to the merged row');
  });
  assert.equal(PRIMARY_STEP, 'Lead Assessment');
  assert.equal(KEY_OWNER.polygons_surfaces_loaded, PRIMARY_STEP,
    'the polygons confirmation stores on the one lifecycle row');
  assert.equal(FOLDER_SECTION_KEY, 'polygons');
});

/* -------------------------------------------------------------------------
   Section rendering
   ------------------------------------------------------------------------- */

function renderPage(values, meta, extra) {
  return fixture(workspaceMarkup(Object.assign({
    values: values || goodValues(),
    meta: meta || META_PENDING,
    sourceMode: '',
    scenario: DEFAULT_SCENARIO,
    formations: [],
    display: { gas: { p90: '12.0', mean: '19.4', p10: '27.6' }, liquid: null },
    plots: { gas: '', liquid: '' },
    earlier: []
  }, extra || {})));
}

test('lead-assessment: the workspace renders the four numbered sections, in order', function () {
  var root = renderPage();
  var sections = Array.prototype.map.call(root.querySelectorAll('[data-la-section]'), function (el) {
    return el.getAttribute('data-la-section');
  });
  assert.deepEqual(sections, ['thickness', 'volume', 'structure', 'piip']);
  var numbers = Array.prototype.map.call(root.querySelectorAll('.la-num'), function (el) {
    return el.textContent;
  });
  assert.deepEqual(numbers, ['1', '2', '3', '4']);
  var titles = Array.prototype.map.call(root.querySelectorAll('.la-card-title'), function (el) {
    return el.textContent;
  });
  assert.deepEqual(titles, ['Thickness Estimation', 'Area and Volume Definition', PIIP_HEADING]);
});

test('lead-assessment: Section 4 carries the card\'s exact heading and helper text', function () {
  var root = renderPage();
  assert.equal(root.querySelector('.la-card-piip .la-card-title').textContent,
    'Petroleum Initially In Place - PIIP Results');
  assert.equal(root.querySelector('.la-helper').textContent,
    'PIIP results and plots update automatically when valid inputs or the selected scenario change.');
  assert.equal(HELPER_TEXT, 'PIIP results and plots update automatically when valid inputs or the selected scenario change.');
  // The manual controls are GONE — the auto-run replaces both.
  assert.equal(root.querySelector('#ra-calculate'), null, 'no Calculate button');
  assert.equal(root.querySelector('#ra-apply'), null, 'no Apply to Lead button');
});

test('lead-assessment: Sections 1 and 2 are SYMMETRIC TWINS — one shared grid class', function () {
  var root = renderPage();
  var twins = root.querySelectorAll('.la-twins > .la-card-twin');
  assert.equal(twins.length, 2, 'exactly two twin cards share the twins row');
  assert.equal(twins[0].getAttribute('data-la-section'), 'thickness');
  assert.equal(twins[1].getAttribute('data-la-section'), 'volume');
  // Identical geometry means the SAME grid class and the SAME cell count, not
  // two hand-tuned layouts that merely look alike.
  twins.forEach(function (card) {
    assert.equal(card.querySelectorAll('.la-grid').length, 1, 'one .la-grid per twin');
    assert.equal(card.querySelectorAll('.la-col-head').length, 2, 'two column headers');
    assert.equal(card.querySelectorAll('.la-row-head').length, 2, 'two row headers');
    assert.equal(card.querySelectorAll('.la-cell').length, 4, 'a 2x2 value grid');
  });
});

test('lead-assessment: Section 1 is Reservoir/Formation x TWT/Thickness', function () {
  var root = fixture(thicknessSectionMarkup(goodValues(), META_PENDING, ''));
  assert.deepEqual(
    Array.prototype.map.call(root.querySelectorAll('.la-col-head'), function (el) { return el.textContent; }),
    ['TWT (ms)', 'Thickness (ft)']);
  assert.deepEqual(
    Array.prototype.map.call(root.querySelectorAll('.la-row-head'), function (el) { return el.textContent; }),
    ['Reservoir', 'Formation']);
  assert.deepEqual(
    Array.prototype.map.call(root.querySelectorAll('[data-la-field]'), function (el) {
      return el.getAttribute('data-la-field');
    }),
    ['twt_reservoir_ms', 'reservoir_thickness_ft', 'twt_formation_ms', 'formation_thickness_ft']);
});

test('lead-assessment: Section 2 is Area/GRV x P90/P10', function () {
  var root = fixture(volumeSectionMarkup(goodValues()));
  assert.deepEqual(
    Array.prototype.map.call(root.querySelectorAll('.la-col-head'), function (el) { return el.textContent; }),
    ['P90', 'P10']);
  assert.deepEqual(
    Array.prototype.map.call(root.querySelectorAll('.la-row-head'), function (el) { return el.textContent; }),
    ['Area (km²)', 'GRV (10³ acre.ft)']);
  assert.equal(root.querySelector('[data-la-field="p90_area_km2"]').value, '12.60');
  assert.equal(root.querySelector('[data-la-field="grv_p10_thousand_acre_ft"]').value, '17.30');
});

test('lead-assessment: Section 3 pairs the TVDSS input with the exact confirmation label', function () {
  var root = fixture(structureSectionMarkup(goodValues(), []));
  assert.equal(root.querySelector('.la-polygons').textContent.trim(),
    'Polygons and surfaces are placed in the shared folder');
  assert.equal(POLYGONS_LABEL, 'Polygons and surfaces are placed in the shared folder');
  var box = root.querySelector('[data-la-field="polygons_surfaces_loaded"]');
  assert.equal(box.type, 'checkbox');
  assert.equal(box.checked, false);
  assert.equal(root.querySelector('[data-la-field="top_formation_tvdss_ft"]').value, '-6500');
});

/* -------------------------------------------------------------------------
   The dynamic Section 3 label
   ------------------------------------------------------------------------- */

test('lead-assessment: the TVDSS label names the lead\'s primary formation', function () {
  assert.equal(tvdssLabel([{ formation: 'SARH' }]), 'Top SARH Formation TVDSS (ft)');
  // The canonical trio wins over a custom name regardless of row order...
  assert.equal(primaryFormationName([{ formation: 'MAUDDUD' }, { formation: 'QASM' }]), 'QASM');
  // ...and SARH wins over the rest of the trio.
  assert.equal(primaryFormationName([{ formation: 'QWRH' }, { formation: 'SARH' }]), 'SARH');
  // A custom-only record still names itself.
  assert.equal(tvdssLabel([{ formation: 'mauddud' }]), 'Top MAUDDUD Formation TVDSS (ft)');
});

test('lead-assessment: a lead with no formation rows falls back to the generic label', function () {
  assert.equal(tvdssLabel([]), 'Top Formation TVDSS (ft)');
  assert.equal(tvdssLabel(null), 'Top Formation TVDSS (ft)');
  assert.equal(tvdssLabel([{ formation: '  ' }]), 'Top Formation TVDSS (ft)');
  var root = fixture(structureSectionMarkup(goodValues(), []));
  assert.equal(root.querySelector('[data-la-field="top_formation_tvdss_ft"]').placeholder,
    'Top Formation TVDSS (ft)');
});

/* -------------------------------------------------------------------------
   Validation
   ------------------------------------------------------------------------- */

test('lead-assessment: a blank page has no errors — blank is INCOMPLETE, not invalid', function () {
  assert.deepEqual(validateLeadAssessment({}), {});
  assert.deepEqual(validateLeadAssessment(undefined), {});
  assert.equal(firstError({}), null);
});

test('lead-assessment: every source value must be a number greater than zero', function () {
  ['twt_reservoir_ms', 'reservoir_thickness_ft', 'p90_area_km2', 'grv_p90_thousand_acre_ft']
    .forEach(function (key) {
      var expected = LABELS[key] + ' must be a number greater than 0.';
      assert.equal(numberError(key, 'abc'), expected, key + ' rejects non-numeric');
      assert.equal(numberError(key, '0'), expected, key + ' rejects zero');
      assert.equal(numberError(key, '-4'), expected, key + ' rejects negative');
      assert.equal(numberError(key, '4'), null, key + ' accepts a positive number');
      assert.equal(numberError(key, ''), null, key + ' blank is not an error');
    });
});

test('lead-assessment: Formation TWT must be GREATER than Reservoir TWT — equality rejected', function () {
  var errors = validateThicknessSection(goodValues({ twt_formation_ms: '1500' }));
  assert.equal(errors.twt_formation_ms,
    'Formation TWT (ms) must be greater than Reservoir TWT (ms).');
  assert.equal(MESSAGES.twtOrder, 'Formation TWT (ms) must be greater than Reservoir TWT (ms).');
  // Inverted is the same error, on the same field — never silently swapped.
  assert.equal(validateThicknessSection(goodValues({ twt_formation_ms: '900' })).twt_formation_ms,
    MESSAGES.twtOrder);
  assert.equal(validateThicknessSection(goodValues()).twt_formation_ms, undefined);
  // One side blank: nothing to compare yet.
  assert.equal(validateThicknessSection(goodValues({ twt_reservoir_ms: '' })).twt_formation_ms, undefined);
});

test('lead-assessment: Formation Thickness must be GREATER than Reservoir Thickness — equality rejected', function () {
  var errors = validateThicknessSection(goodValues({ formation_thickness_ft: '200' }));
  assert.equal(errors.formation_thickness_ft,
    'Formation Thickness (ft) must be greater than Reservoir Thickness (ft).');
  assert.equal(validateThicknessSection(goodValues({ formation_thickness_ft: '100' })).formation_thickness_ft,
    MESSAGES.thicknessOrder);
  assert.equal(validateThicknessSection(goodValues()).formation_thickness_ft, undefined);
});

test('lead-assessment: each P90/P10 pair needs P10 strictly greater — equality rejected', function () {
  assert.equal(validateVolumeSection(goodValues({ p10_area_km2: '12.60' })).p10_area_km2,
    'Area P10 must be greater than Area P90.');
  assert.equal(validateVolumeSection(goodValues({ p10_area_km2: '1' })).p10_area_km2,
    MESSAGES.areaOrder);
  assert.equal(validateVolumeSection(goodValues({ grv_p10_thousand_acre_ft: '12.60' })).grv_p10_thousand_acre_ft,
    'GRV P10 must be greater than GRV P90.');
  assert.deepEqual(validateVolumeSection(goodValues()), {});
});

test('lead-assessment: an unusable value reports ITSELF, not the ordering it also breaks', function () {
  // '0' fails the magnitude rule; the ordering rule stands down rather than
  // stacking a second, more confusing message onto the same cell.
  var errors = validateVolumeSection(goodValues({ p90_area_km2: '0' }));
  assert.equal(errors.p90_area_km2, 'Area P90 (km²) must be a number greater than 0.');
  assert.equal(errors.p10_area_km2, undefined);
});

// Card 3H. The TVDSS used to be numeric-parse only -- it was the page's one
// signed measure. It is now a magnitude like the rest, so a negative is
// refused; zero and large depths still pass, and it still gates nothing.
test('lead-assessment: the TVDSS is a magnitude, and still gates nothing', function () {
  assert.equal(tvdssError('-6500'), 'Top Formation TVDSS must not be negative.');
  assert.equal(tvdssError('0'), null);
  assert.equal(tvdssError('12000'), null, 'and it is exempt from the generic 9999 cap');
  assert.equal(tvdssError(''), null, 'blank is not an error -- the field is optional');
  assert.equal(tvdssError('deep'), 'Top Formation TVDSS must be numeric.');
  assert.equal(validateLeadAssessment(goodValues({ top_formation_tvdss_ft: 'deep' })).top_formation_tvdss_ft,
    MESSAGES.tvdss);
  assert.equal(validateLeadAssessment(goodValues({ top_formation_tvdss_ft: '-10' })).top_formation_tvdss_ft,
    MESSAGES.tvdssNegative);
});

test('lead-assessment: firstError reads the page in layout order', function () {
  var errors = validateLeadAssessment(goodValues({ twt_reservoir_ms: '0', p90_area_km2: '0' }));
  assert.equal(firstError(errors), 'Reservoir TWT (ms) must be a number greater than 0.');
});

test('lead-assessment: each card carries ONE error strip at its bottom, hidden while clean', function () {
  // No per-cell slots anywhere: a message under an input would grow that cell
  // and knock the twin grids out of line. Messages go to the card-bottom strip.
  var volume = fixture(volumeSectionMarkup(goodValues()));
  assert.equal(volume.querySelectorAll('.la-field-error').length, 0, 'per-cell slots are gone');
  var strip = volume.querySelector('.la-card-errors[data-la-errors="volume"]');
  assert.ok(strip, 'the volume card owns one strip');
  assert.equal(strip.getAttribute('role'), 'alert');
  assert.ok(strip.hidden, 'clean = hidden, no reserved dead space');
  assert.equal(strip.textContent, '');

  var thickness = fixture(thicknessSectionMarkup(goodValues(), META_PENDING, ''));
  assert.ok(thickness.querySelector('.la-card-errors[data-la-errors="thickness"]'));
  assert.equal(thickness.querySelectorAll('.la-field-error').length, 0);

  var structure = fixture(structureSectionMarkup(goodValues(), []));
  assert.ok(structure.querySelector('.la-card-errors[data-la-errors="structure"]'),
    'section 3 reports its TVDSS into a strip too, not under the input');
  assert.equal(structure.querySelectorAll('.la-field-error').length, 0);
});

/* -------------------------------------------------------------------------
   The TWT <-> thickness conversion
   ------------------------------------------------------------------------- */

test('lead-assessment: with NO coefficients both columns are plain manual inputs', function () {
  assert.equal(coefficientsFor(META_PENDING, 'reservoir'), null);
  assert.equal(conversionConfigured(META_PENDING), false);
  assert.equal(conversionConfigured(null), false, 'meta absent entirely is also pending');
  // No derivation in either direction...
  assert.equal(isDerivedCell(META_PENDING, 'reservoir', 'thickness', 'twt'), false);
  assert.deepEqual(applyConversion(META_PENDING, { twt_reservoir_ms: '1500' }, 'twt'),
    { twt_reservoir_ms: '1500' }, 'nothing is computed');
  // ...and the section says so, once, quietly.
  var root = fixture(thicknessSectionMarkup(goodValues(), META_PENDING, ''));
  assert.equal(root.querySelector('[data-la-note="conversion"]').textContent,
    'TWT ⇄ thickness conversion pending configuration');
  assert.equal(root.querySelectorAll('[readonly]').length, 0, 'no cell is readonly');
  assert.equal(root.querySelectorAll('.la-derived').length, 0, 'no cell is styled as derived');
});

test('lead-assessment: a malformed or slope-less coefficient pair stays PENDING', function () {
  // m = 0 has no inverse; a non-numeric entry is not a calibration. Both
  // degrade to manual rather than producing a constant or a NaN.
  assert.equal(coefficientsFor({ twt_thickness_coefficients: { reservoir: { m: 0, b: 5 } } }, 'reservoir'), null);
  assert.equal(coefficientsFor({ twt_thickness_coefficients: { reservoir: { m: 'x', b: 5 } } }, 'reservoir'), null);
  assert.equal(coefficientsFor({ twt_thickness_coefficients: { reservoir: {} } }, 'reservoir'), null);
});

test('lead-assessment: with coefficients, entering one side DERIVES the other', function () {
  assert.deepEqual(coefficientsFor(META_WITH_COEFFICIENTS, 'reservoir'), { m: 0.5, b: -50 });
  assert.equal(conversionConfigured(META_WITH_COEFFICIENTS), true);
  assert.equal(thicknessFromTwt(COEFFICIENTS.reservoir, '500'), '200');
  assert.equal(twtFromThickness(COEFFICIENTS.reservoir, '200'), '500');
  // Round trip, both rows, through the section-level helper.
  var derived = applyConversion(META_WITH_COEFFICIENTS,
    { twt_reservoir_ms: '500', twt_formation_ms: '500' }, 'twt');
  assert.equal(derived.reservoir_thickness_ft, '200');
  assert.equal(derived.formation_thickness_ft, '180');
  var back = applyConversion(META_WITH_COEFFICIENTS,
    { reservoir_thickness_ft: '200', formation_thickness_ft: '180' }, 'thickness');
  assert.equal(back.twt_reservoir_ms, '500');
  assert.equal(back.twt_formation_ms, '500');
});

test('lead-assessment: the ONE-SOURCE rule — the derived column is readonly and visually distinct', function () {
  assert.equal(isDerivedCell(META_WITH_COEFFICIENTS, 'reservoir', 'thickness', 'twt'), true);
  assert.equal(isDerivedCell(META_WITH_COEFFICIENTS, 'reservoir', 'twt', 'twt'), false,
    'the source column stays editable');
  assert.equal(isDerivedCell(META_WITH_COEFFICIENTS, 'reservoir', 'twt', 'thickness'), true,
    'and the rule reverses with the source');
  assert.equal(isDerivedCell(META_WITH_COEFFICIENTS, 'reservoir', 'thickness', ''), false,
    'an undecided section derives nothing');
  var root = fixture(thicknessSectionMarkup(
    { twt_reservoir_ms: '500', reservoir_thickness_ft: '200' }, META_WITH_COEFFICIENTS, 'twt'));
  var derived = root.querySelector('[data-la-field="reservoir_thickness_ft"]');
  assert.equal(derived.readOnly, true, 'thickness is derived, so it cannot be typed into');
  assert.ok(derived.classList.contains('la-derived'), 'and it is styled apart from a real input');
  assert.equal(root.querySelector('[data-la-field="twt_reservoir_ms"]').readOnly, false);
});

test('lead-assessment: a SECOND source is rejected with the clear-the-first message', function () {
  assert.equal(MESSAGES.secondSource(LABELS.twt_reservoir_ms, LABELS.reservoir_thickness_ft),
    'Clear Reservoir TWT (ms) before entering Reservoir Thickness (ft).');
  assert.equal(MESSAGES.secondSource(LABELS.formation_thickness_ft, LABELS.twt_formation_ms),
    'Clear Formation Thickness (ft) before entering Formation TWT (ms).');
});

test('lead-assessment: the source column is resolved from the marker, else inferred', function () {
  var values = { twt_reservoir_ms: '500', reservoir_thickness_ft: '200' };
  assert.equal(resolveSourceMode(values, 'thickness'), 'thickness', 'a stored marker wins');
  assert.equal(resolveSourceMode(values, 'twt'), 'twt');
  // No marker: whichever column actually carries data. A lead captured before
  // this page existed has thicknesses and no times.
  assert.equal(resolveSourceMode({ reservoir_thickness_ft: '200' }, ''), 'thickness');
  assert.equal(resolveSourceMode({ twt_formation_ms: '1800' }, ''), 'twt');
  // Ambiguous or empty: unset, so the user's first keystroke decides.
  assert.equal(resolveSourceMode(values, ''), '');
  assert.equal(resolveSourceMode({}, ''), '');
  assert.equal(resolveSourceMode({}, 'nonsense'), '');
});

test('lead-assessment: columnOf places a key in its column, or nowhere', function () {
  assert.equal(columnOf('twt_reservoir_ms'), 'twt');
  assert.equal(columnOf('formation_thickness_ft'), 'thickness');
  assert.equal(columnOf('p90_area_km2'), '', 'a Section 2 key is in neither column');
  assert.equal(ROW_KEYS.reservoir.twt, 'twt_reservoir_ms');
  assert.equal(ROW_KEYS.formation.thickness, 'formation_thickness_ft');
});

test('lead-assessment: a row with NO coefficients stays manual even beside one that has them', function () {
  var partial = { twt_thickness_coefficients: { reservoir: { m: 0.5, b: -50 } } };
  assert.equal(isDerivedCell(partial, 'reservoir', 'thickness', 'twt'), true);
  assert.equal(isDerivedCell(partial, 'formation', 'thickness', 'twt'), false,
    'the uncalibrated row keeps both sides editable');
  var derived = applyConversion(partial, { twt_reservoir_ms: '500', twt_formation_ms: '500' }, 'twt');
  assert.equal(derived.reservoir_thickness_ft, '200');
  assert.equal(derived.formation_thickness_ft, undefined, 'and nothing is invented for it');
});

/* -------------------------------------------------------------------------
   Section 4 — method precedence
   ------------------------------------------------------------------------- */

test('lead-assessment: a valid GRV pair wins — GRV method', function () {
  var resolved = resolveCalculation(goodValues());
  assert.equal(resolved.status, 'ready');
  assert.equal(resolved.method, 'GRV');
  assert.deepEqual(calculationPayload(resolved, 'condensate_field_a'), {
    scenario: 'condensate_field_a', method: 'GRV', grv_p90: 12.6, grv_p10: 17.3
  });
});

test('lead-assessment: GRV entirely empty + a valid Area pair and thickness — box model', function () {
  var resolved = resolveCalculation(goodValues({
    grv_p90_thousand_acre_ft: '', grv_p10_thousand_acre_ft: ''
  }));
  assert.equal(resolved.status, 'ready');
  assert.equal(resolved.method, 'Box Model');
  assert.deepEqual(calculationPayload(resolved, DEFAULT_SCENARIO), {
    scenario: DEFAULT_SCENARIO, method: 'Box Model',
    area_p90_km2: 12.6, area_p10_km2: 17.3, thickness_p50_ft: 200
  });
});

test('lead-assessment: a HALF-entered GRV pair reports itself — NEVER a silent fallback', function () {
  // The area/thickness trio below is perfectly valid, so a silent fallback
  // would compute a real-looking number from inputs the user was not editing.
  var half = goodValues({ grv_p10_thousand_acre_ft: '' });
  var resolved = resolveCalculation(half);
  assert.equal(resolved.status, 'error');
  assert.equal(resolved.message,
    'Enter both GRV P90 and GRV P10, or clear both to use Area × Thickness.');
  assert.equal(resolved.method, undefined, 'and nothing is chosen to run');
  assert.equal(resolveCalculation(goodValues({ grv_p90_thousand_acre_ft: '' })).status, 'error');
});

test('lead-assessment: an INVALID complete GRV pair reports its own validation error', function () {
  assert.equal(resolveCalculation(goodValues({ grv_p90_thousand_acre_ft: '0' })).message,
    'GRV P90 (10³ acre.ft) must be a number greater than 0.');
});

test('lead-assessment: too little input is IDLE, not an error', function () {
  var idle = resolveCalculation({});
  assert.equal(idle.status, 'idle');
  assert.equal(idle.message,
    'Enter a GRV pair, or an Area pair with a Reservoir Thickness, to calculate PIIP results.');
  // Areas without a thickness cannot run the box model either.
  assert.equal(resolveCalculation({ p90_area_km2: '10', p10_area_km2: '17' }).status, 'idle');
});

test('lead-assessment: the box model refuses a broken area pair rather than guessing', function () {
  var broken = goodValues({
    grv_p90_thousand_acre_ft: '', grv_p10_thousand_acre_ft: '', p10_area_km2: '1'
  });
  assert.equal(resolveCalculation(broken).status, 'error');
  assert.equal(resolveCalculation(broken).message, MESSAGES.areaOrder);
});

test('lead-assessment: the run signature changes with scenario, method and inputs', function () {
  var grv = resolveCalculation(goodValues());
  var box = resolveCalculation(goodValues({ grv_p90_thousand_acre_ft: '', grv_p10_thousand_acre_ft: '' }));
  var a = calculationSignature(grv, 'dry_gas_high_pressure');
  assert.equal(calculationSignature(grv, 'dry_gas_high_pressure'), a, 'identical inputs, identical signature');
  assert.ok(calculationSignature(grv, 'condensate_field_a') !== a, 'scenario is part of it');
  assert.ok(calculationSignature(box, 'dry_gas_high_pressure') !== a, 'so is the method');
  assert.ok(calculationSignature(resolveCalculation(goodValues({ grv_p10_thousand_acre_ft: '20' })),
    'dry_gas_high_pressure') !== a, 'and so are the numbers');
  assert.equal(calculationSignature({ status: 'idle' }, 'x'), '', 'nothing runnable has no signature');
});

/* -------------------------------------------------------------------------
   Scenario-conditional Liquid
   ------------------------------------------------------------------------- */

test('lead-assessment: Liquid renders for CONDENSATE scenarios only', function () {
  assert.equal(showsLiquid(SCENARIOS, 'condensate_field_a'), true);
  assert.equal(showsLiquid(SCENARIOS, 'condensate_field_b'), true);
  assert.equal(showsLiquid(SCENARIOS, 'dry_gas_high_pressure'), false);
  assert.equal(showsLiquid(SCENARIOS, 'no_such_scenario'), false);
  assert.equal(showsLiquid([], 'condensate_field_a'), false);
});

test('lead-assessment: a dry-gas page renders Gas alone; a condensate page renders both', function () {
  var base = {
    values: goodValues(), meta: { resource_scenarios: SCENARIOS }, sourceMode: '',
    formations: [], plots: { gas: '', liquid: '' }, earlier: [],
    display: { gas: { p90: '12.0', mean: '19.4', p10: '27.6' },
               liquid: { p90: '3.7', mean: '6.1', p10: '8.9' } }
  };
  var dry = fixture(piipSectionMarkup(Object.assign({}, base, { scenario: 'dry_gas_high_pressure' })));
  assert.deepEqual(
    Array.prototype.map.call(dry.querySelectorAll('[data-la-result]'), function (el) {
      return el.getAttribute('data-la-result');
    }), ['gas']);
  assert.ok(dry.querySelector('.la-results').classList.contains('la-results-gas-only'),
    'and Gas takes the full width rather than leaving a hole');
  assert.equal(dry.textContent.indexOf('MMSTB'), -1);

  var wet = fixture(piipSectionMarkup(Object.assign({}, base, { scenario: 'condensate_field_a' })));
  assert.deepEqual(
    Array.prototype.map.call(wet.querySelectorAll('[data-la-result]'), function (el) {
      return el.getAttribute('data-la-result');
    }), ['gas', 'liquid']);
  assert.equal(wet.querySelector('.la-result-liquid .la-result-heading').textContent, 'Liquid (MMSTB)');
});

test('lead-assessment: result LABELS sit outside the tinted boxes', function () {
  var root = renderPage();
  var gas = root.querySelector('.la-result-gas');
  assert.deepEqual(
    Array.prototype.map.call(gas.querySelectorAll('.la-result-label'), function (el) { return el.textContent; }),
    ['P90', 'Mean', 'P10']);
  assert.deepEqual(
    Array.prototype.map.call(gas.querySelectorAll('.la-result-box'), function (el) { return el.textContent; }),
    ['12.0', '19.4', '27.6'], 'the box holds only the number');
  // A label is a SIBLING of its box, never a child of it.
  assert.equal(gas.querySelector('.la-result-box .la-result-label'), null);
});

test('lead-assessment: scenario radios come from meta, falling back to schema.js', function () {
  assert.equal(scenarioList({ resource_scenarios: SCENARIOS }), SCENARIOS);
  assert.ok(scenarioList(null).length >= 1, 'the boot fallback is used when meta is absent');
  var root = renderPage(null, { resource_scenarios: SCENARIOS, twt_thickness_coefficients: {} });
  assert.deepEqual(
    Array.prototype.map.call(root.querySelectorAll('input[name="la-scenario"]'), function (el) {
      return el.value;
    }), SCENARIOS.map(function (s) { return s.id; }));
  assert.equal(root.querySelector('input[name="la-scenario"]:checked').value, DEFAULT_SCENARIO,
    'a page with nothing stored opens on the default scenario');
});

/* -------------------------------------------------------------------------
   The batched save plan
   ------------------------------------------------------------------------- */

test('lead-assessment: only the tasks whose values CHANGED are in the plan', function () {
  var saved = {
    'Lead Assessment': Object.assign(goodValues(), { thickness_source_mode: '' })
  };
  var values = Object.assign(goodValues(), { thickness_source_mode: '' });
  assert.deepEqual(buildSavePlan(values, saved), [], 'an untouched page writes nothing at all');

  // Tick the confirmation: exactly the merged task moves.
  var ticked = Object.assign({}, values, { polygons_surfaces_loaded: '1' });
  var plan = buildSavePlan(ticked, saved);
  assert.equal(plan.length, 1);
  assert.equal(plan[0].taskName, 'Lead Assessment');
  assert.equal(plan[0].fields.polygons_surfaces_loaded, '1');
});

test('lead-assessment: the plan writes one whole merged-task payload', function () {
  var plan = buildSavePlan(Object.assign(goodValues({ polygons_surfaces_loaded: '1' }),
                                         { thickness_source_mode: 'twt' }), {});
  assert.deepEqual(plan.map(function (entry) { return entry.taskName; }), ['Lead Assessment']);
  assert.deepEqual(Object.keys(plan[0].fields).sort(), Object.keys(KEY_OWNER).sort());
  // Every value is a string, and a missing one is '' rather than undefined.
  var sparse = buildSavePlan({ p90_area_km2: '3' }, {});
  assert.equal(sparse[0].fields.p10_area_km2, '');
});

test('lead-assessment: a roster carrying Lead Assessment keeps the single-entry plan', function () {
  // The normal (post-v7) record: the merged row is present, so the roster
  // argument changes nothing about the plan's shape.
  var roster = ['Lead Assessment', 'Reservoir CoS', 'Trap and Seal CoS'];
  var plan = buildSavePlan(Object.assign(goodValues({ polygons_surfaces_loaded: '1' }),
                                         { thickness_source_mode: 'twt' }), {}, roster);
  assert.deepEqual(plan.map(function (entry) { return entry.taskName; }), ['Lead Assessment']);
  assert.deepEqual(Object.keys(plan[0].fields).sort(), Object.keys(KEY_OWNER).sort());
  assert.deepEqual(buildSavePlan(goodValues({ thickness_source_mode: '' }),
    { 'Lead Assessment': goodValues({ thickness_source_mode: '' }) }, roster), [],
    'an untouched page still writes nothing');
});

test('lead-assessment: without a Lead Assessment row the plan groups per LEGACY owner', function () {
  // A pre-v7-shaped record served during a stale-server window: writes must
  // land on the SAME legacy rows readStoredValues hydrates from, not on the
  // taskNamed fallback row.
  var roster = LEGACY_LEAD_ASSESSMENT_STEPS.slice();
  var saved = {
    'Area Definition': { p90_area_km2: '12.60', p10_area_km2: '17.30', top_formation_tvdss_ft: '-6500' },
    'Thickness Estimation': { twt_reservoir_ms: '1500', twt_formation_ms: '1800',
                              reservoir_thickness_ft: '200', formation_thickness_ft: '500',
                              thickness_source_mode: '' },
    'GRV Inputs': { grv_p90_thousand_acre_ft: '12.60', grv_p10_thousand_acre_ft: '17.30' },
    'Resource Assessment': { polygons_surfaces_loaded: '' }
  };
  var untouched = goodValues({ thickness_source_mode: '' });
  assert.deepEqual(buildSavePlan(untouched, saved, roster), [],
    'hydrated values re-submitted verbatim are not dirty');

  // Change one Area key and one GRV key: exactly those two legacy tasks move,
  // each carrying ALL of (and only) its own keys.
  var edited = goodValues({ thickness_source_mode: '', p90_area_km2: '13.00',
                            grv_p90_thousand_acre_ft: '14.00' });
  var plan = buildSavePlan(edited, saved, roster);
  assert.deepEqual(plan.map(function (entry) { return entry.taskName; }),
    ['Area Definition', 'GRV Inputs'], 'one entry per legacy task owning a changed key');
  assert.deepEqual(Object.keys(plan[0].fields).sort(),
    ['p10_area_km2', 'p90_area_km2', 'top_formation_tvdss_ft']);
  assert.equal(plan[0].fields.p90_area_km2, '13.00');
  assert.deepEqual(Object.keys(plan[1].fields).sort(),
    ['grv_p10_thousand_acre_ft', 'grv_p90_thousand_acre_ft']);

  // The polygons tick belongs to Resource Assessment — the legacy comments/
  // PIIP owner — in the legacy grouping.
  var ticked = buildSavePlan(goodValues({ thickness_source_mode: '', polygons_surfaces_loaded: '1' }),
                             saved, roster);
  assert.deepEqual(ticked.map(function (entry) { return entry.taskName; }), ['Resource Assessment']);
  assert.deepEqual(Object.keys(ticked[0].fields), ['polygons_surfaces_loaded']);
});

/* -------------------------------------------------------------------------
   Comments provenance
   ------------------------------------------------------------------------- */

test('lead-assessment: earlier step comments are kept, attributed, and only when non-empty', function () {
  var tasks = [
    { task_name: 'Resource Assessment', comments: 'the editable one' },
    { task_name: 'GRV Inputs', comments: 'grv note' },
    { task_name: 'Area Definition', comments: '' },
    { task_name: 'Thickness Estimation', comments: 'thickness note' },
    { task_name: 'Reservoir CoS', comments: 'not this stage' }
  ];
  assert.deepEqual(earlierComments(tasks), [
    { step: 'Thickness Estimation', comments: 'thickness note' },
    { step: 'GRV Inputs', comments: 'grv note' }
  ], 'rail order, the primary step excluded, blanks and other stages dropped');
  assert.deepEqual(earlierComments([]), []);
});

test('lead-assessment: the provenance fold renders only when there is something to show', function () {
  assert.equal(earlierCommentsMarkup([]), '', 'no empty furniture');
  var root = fixture(earlierCommentsMarkup([{ step: 'GRV Inputs', comments: 'grv note' }]));
  assert.equal(root.querySelector('summary').textContent, 'Earlier step comments');
  assert.equal(root.querySelector('.la-earlier-entry b').textContent, 'GRV Inputs');
  assert.equal(root.querySelector('.la-earlier-entry p').textContent, 'grv note');
  // Read-only: the fold has no inputs of its own.
  assert.equal(root.querySelectorAll('input, textarea').length, 0);
});

/* -------------------------------------------------------------------------
   Mounted: the auto-run
   ------------------------------------------------------------------------- */

// The detail shell's own nodes, copied by id from static/index.html — the
// workspace mounts into #dynamic-fields and REUSES the comments box, the
// folder slot and the Save button rather than rendering its own.
function shellFixture() {
  return fixture(
    '<form id="component-form">' +
    '<div id="dynamic-fields" class="dynamic-fields"></div>' +
    '<label id="comments-field">Comments<textarea id="comments"></textarea></label>' +
    '<div class="action-row"><button id="save-component" type="submit">Save Updates</button></div>' +
    '</form>');
}

var RESULT = {
  gas: { p90: 12.04, mean: 19.42, p10: 27.61 },
  condensate: { p90: 3.71, mean: 6.13, p10: 8.94 },
  units: {},
  plots: { gas: 'data:image/png;base64,GAS', condensate: 'data:image/png;base64,LIQ' }
};

function jsonResponse(body) {
  return {
    ok: true, status: 200,
    headers: { get: function () { return 'application/json'; } },
    json: function () { return Promise.resolve(body); },
    text: function () { return Promise.resolve(JSON.stringify(body)); }
  };
}

// Mount the page over a stubbed fetch. Returns the recorded calls so a test can
// assert what the auto-run sent (and how often). `respond` (optional) answers
// /resource-assessment in place of the immediate stub -- returning a promise it
// holds open is how a test decides WHEN the calculation lands.
function mountPage(fields, meta, respond) {
  var calls = [];
  mockFetch(function (url, options) {
    calls.push({ url: url, body: options && options.body ? JSON.parse(options.body) : null });
    if (url.indexOf('/resource-assessment') >= 0) return respond ? respond() : jsonResponse(RESULT);
    if (url.indexOf('/folders/') >= 0) return jsonResponse({ unc_path: '\\\\share\\WWWW\\WWWW-44\\Polygons__Surfaces' });
    if (url.indexOf('/dynamic-fields') >= 0) return jsonResponse({ ok: true });
    if (url.indexOf('/detail') >= 0) return jsonResponse({ project: {}, tasks: Store.tasks, fields: Store.allFields });
    return jsonResponse({});
  });
  Store.projectId = 7;
  Store.meta = meta || { resource_scenarios: SCENARIOS, twt_thickness_coefficients: {} };
  Store.formations = [];
  Store.tasks = LEAD_ASSESSMENT_STEPS.map(function (name, index) {
    return { task_id: 100 + index, task_name: name, comments: '', priority: 'Medium', revision: 1,
             stage_group: 'Lead Assessment', status: 'In Progress' };
  });
  Store.allFields = fields || {};
  Store.pipeline = 'prospect';
  Store.project = { pipeline_type: 'prospect' };
  var root = shellFixture();
  renderLeadAssessment(root.querySelector('#dynamic-fields'), { onCopy: function () {} });
  return { root: root, calls: calls };
}

function resourceCalls(calls) {
  return calls.filter(function (call) { return call.url.indexOf('/resource-assessment') >= 0; });
}

function fieldWriteCalls(calls) {
  return calls.filter(function (call) { return call.url.indexOf('/dynamic-fields') >= 0; });
}

// A GENUINE user interaction: a real input event on a real field, which is the
// only thing that may arm the persisting auto-run (KI-005). `value` is optional
// — re-typing the value that is already there is still an interaction.
function userEdits(mounted, key, value) {
  var input = mounted.root.querySelector('[data-la-field="' + key + '"]');
  if (value != null) input.value = value;
  input.dispatchEvent(new Event('input', { bubbles: true }));
  return input;
}

// A complete, already-assessed lead: valid stored inputs AND the stored PIIP
// results a previous run wrote. This is the shape KI-005's page view rewrote.
var ASSESSED_LEAD = {
  'Lead Assessment': {
    grv_p90_thousand_acre_ft: '12.6', grv_p10_thousand_acre_ft: '17.3',
    lead_piip_gas_p90: '9.02', lead_piip_gas_mean: '13.52', lead_piip_gas_p10: '18.1',
    lead_resource_scenario: DEFAULT_SCENARIO, lead_calculation_method: 'GRV',
    polygons_surfaces_loaded: '1'
  }
};

function settle(ms) {
  return new Promise(function (resolve) { setTimeout(resolve, ms == null ? 1200 : ms); });
}

/* KI-005. The auto-run PERSISTS (POST /resource-assessment, then PATCH
   /dynamic-fields onto the Resource Assessment task), so a mount that fired it
   turned merely OPENING a lead into a write: the stored assessment was
   overwritten, the board's Total Mean OGIP tile moved, and the server's
   post-save field-completion engine reopened an Approved step. Card 2B's
   contract is "valid inputs or the SELECTED SCENARIO CHANGE" — mounting is
   neither. */

test('lead-assessment: MOUNTING an assessed lead is a READ — zero requests, stored results shown', function () {
  var mounted = mountPage(ASSESSED_LEAD);
  assert.equal(leadAssessmentActive(), true);
  // The stored numbers are on screen immediately, straight out of the stored
  // fields — no run produced them.
  var boxes = mounted.root.querySelectorAll('.la-result-gas .la-result-box');
  assert.equal(boxes[0].textContent, '9.02');
  assert.equal(boxes[1].textContent, '13.52');
  assert.equal(boxes[2].textContent, '18.1');
  // Wait well past DEBOUNCE_MS: a mount-scheduled run would have landed by now.
  return settle().then(function () {
    assert.equal(resourceCalls(mounted.calls).length, 0,
      'a page VIEW computes nothing');
    assert.equal(fieldWriteCalls(mounted.calls).length, 0,
      'and above all PERSISTS nothing — the stored assessment is untouched');
    // The stored numbers are still the stored numbers.
    assert.equal(mounted.root.querySelectorAll('.la-result-gas .la-result-box')[1].textContent, '13.52');
    teardownLeadAssessment();
  });
});

test('lead-assessment: mounting a lead with valid inputs but NO stored result still writes nothing', function () {
  var mounted = mountPage({
    'GRV Inputs': { grv_p90_thousand_acre_ft: '12.6', grv_p10_thousand_acre_ft: '17.3' }
  });
  // Nothing to display yet (the plots are figures, not stored values), and the
  // page will not go and fetch some: results appear on the user's first edit.
  assert.equal(mounted.root.querySelector('.la-result-gas .la-result-box').textContent, '—');
  return settle().then(function () {
    assert.equal(mounted.calls.filter(function (call) {
      return call.url.indexOf('/folders/') < 0;
    }).length, 0, 'the folder row is the ONLY thing a mount asks the server for');
    teardownLeadAssessment();
  });
});

test('lead-assessment: the user\'s first edit arms the auto-run — exactly one debounced run', function () {
  var mounted = mountPage(ASSESSED_LEAD);
  return settle(800).then(function () {
    assert.equal(resourceCalls(mounted.calls).length, 0, 'still nothing on the mounted page');
    userEdits(mounted, 'grv_p10_thousand_acre_ft', '19.4');
    return waitFor(function () { return resourceCalls(mounted.calls).length > 0; }, 5000);
  }).then(function () {
    var call = resourceCalls(mounted.calls)[0];
    assert.match(call.url, /\/api\/tasks\/100\/resource-assessment/,
      'addressed to the Lead Assessment task');
    assert.deepEqual(call.body, {
      scenario: DEFAULT_SCENARIO, method: 'GRV', grv_p90: 12.6, grv_p10: 19.4
    });
    // The response lands in the result boxes without any further interaction,
    // rounded by the SAME formatStored rule the persisted values use.
    return waitFor(function () {
      return mounted.root.querySelector('.la-result-gas .la-result-box').textContent === '12.0';
    }, 5000);
  }).then(function () {
    assert.equal(resourceCalls(mounted.calls).length, 1, 'one edit, one run');
    teardownLeadAssessment();
  });
});

test('lead-assessment: a scenario click is the OTHER genuine interaction', function () {
  var mounted = mountPage(ASSESSED_LEAD);
  return settle(800).then(function () {
    assert.equal(resourceCalls(mounted.calls).length, 0);
    var condensate = mounted.root.querySelector('input[name="la-scenario"][value="condensate_field_a"]');
    condensate.checked = true;
    condensate.dispatchEvent(new Event('change', { bubbles: true }));
    return waitFor(function () { return resourceCalls(mounted.calls).length > 0; }, 5000);
  }).then(function () {
    assert.equal(resourceCalls(mounted.calls)[0].body.scenario, 'condensate_field_a');
    teardownLeadAssessment();
  });
});

test('lead-assessment: an edit DEBOUNCES — four keystrokes, one request', function () {
  var mounted = mountPage({});
  var input = mounted.root.querySelector('[data-la-field="grv_p90_thousand_acre_ft"]');
  var other = mounted.root.querySelector('[data-la-field="grv_p10_thousand_acre_ft"]');
  other.value = '17.3';
  other.dispatchEvent(new Event('input', { bubbles: true }));
  ['1', '12', '12.', '12.6'].forEach(function (text) {
    input.value = text;
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  // Nothing has gone out yet: the debounce window is still open.
  assert.equal(resourceCalls(mounted.calls).length, 0, 'no request while the user is still typing');
  return waitFor(function () { return resourceCalls(mounted.calls).length > 0; }, 3000).then(function () {
    assert.equal(resourceCalls(mounted.calls).length, 1,
      'the whole burst collapses into ONE request for the final value');
    assert.deepEqual(resourceCalls(mounted.calls)[0].body, {
      scenario: DEFAULT_SCENARIO, method: 'GRV', grv_p90: 12.6, grv_p10: 17.3
    });
    teardownLeadAssessment();
  });
});

test('lead-assessment: a half-entered GRV pair shows its error and sends nothing', function () {
  var mounted = mountPage({});
  var input = mounted.root.querySelector('[data-la-field="grv_p90_thousand_acre_ft"]');
  input.value = '12.6';
  input.dispatchEvent(new Event('input', { bubbles: true }));
  return waitFor(function () {
    return mounted.root.querySelector('#la-piip-status').textContent === MESSAGES.grvPartial;
  }).then(function () {
    assert.equal(resourceCalls(mounted.calls).length, 0, 'and never silently falls back');
    teardownLeadAssessment();
  });
});

test('lead-assessment: a successful run PERSISTS the lead_piip_* keys the old Apply wrote', function () {
  var mounted = mountPage({
    'GRV Inputs': { grv_p90_thousand_acre_ft: '12.6', grv_p10_thousand_acre_ft: '17.3' },
    'Resource Assessment': { lead_resource_scenario: 'condensate_field_a' }
  });
  // The write is still a write — it just needs a user behind it now.
  userEdits(mounted, 'grv_p10_thousand_acre_ft');
  return waitFor(function () {
    return mounted.calls.some(function (call) {
      return call.url.indexOf('/dynamic-fields') >= 0 && call.body && call.body.fields;
    });
  }, 3000).then(function () {
    var write = mounted.calls.filter(function (call) {
      return call.url.indexOf('/dynamic-fields') >= 0;
    })[0];
    assert.match(write.url, /\/api\/tasks\/100\/dynamic-fields/, 'onto the Lead Assessment task');
    var fields = write.body.fields;
    // The EXACT permanent EAV contract every downstream reader resolves,
    // through the calculator's own formatStored rounding (1 decimal from 10 up,
    // 2 below it) -- byte-identical to what "Apply to Lead" used to write.
    assert.equal(fields.lead_piip_gas_mean, '19.4');
    assert.equal(fields.lead_piip_gas_p90, '12.0');
    assert.equal(fields.lead_piip_gas_p10, '27.6');
    assert.equal(fields.lead_piip_has_liquid, '1', 'a condensate scenario records its liquid');
    assert.equal(fields.lead_piip_liquid_mean, '6.13');
    assert.equal(fields.lead_resource_scenario, 'condensate_field_a');
    assert.equal(fields.lead_calculation_method, 'GRV');
    teardownLeadAssessment();
  });
});

test('lead-assessment: the polygons checkbox is stored, and pairs with the PIIP mean for completion', function () {
  var mounted = mountPage({
    'Resource Assessment': { polygons_surfaces_loaded: '1', lead_piip_gas_mean: '19.4' }
  });
  var box = mounted.root.querySelector('[data-la-field="polygons_surfaces_loaded"]');
  assert.equal(box.checked, true, 'a stored tick renders ticked');
  // Both halves of the server's Resource Assessment predicate are page state:
  // the box the user ticks here, and the mean the auto-run writes. Untick and
  // the plan carries the cleared value that reopens the item.
  box.checked = false;
  box.dispatchEvent(new Event('change', { bubbles: true }));
  var plan = buildSavePlan(readFormValues(mounted.root.querySelector('#dynamic-fields')),
                           Store.allFields);
  assert.deepEqual(plan.map(function (entry) { return entry.taskName; }), ['Lead Assessment']);
  assert.equal(plan[0].fields.polygons_surfaces_loaded, '');
  teardownLeadAssessment();
});

test('lead-assessment: switching scenario clears the stale result and re-runs', function () {
  var mounted = mountPage({
    'GRV Inputs': { grv_p90_thousand_acre_ft: '12.6', grv_p10_thousand_acre_ft: '17.3' }
  });
  userEdits(mounted, 'grv_p10_thousand_acre_ft');
  return waitFor(function () { return resourceCalls(mounted.calls).length > 0; }).then(function () {
    var condensate = mounted.root.querySelector('input[name="la-scenario"][value="condensate_field_a"]');
    condensate.checked = true;
    condensate.dispatchEvent(new Event('change', { bubbles: true }));
    // The Liquid block appears immediately, on the SELECTED scenario alone —
    // it does not wait for a run that has not happened yet.
    assert.equal(mounted.root.querySelectorAll('[data-la-result]').length, 2);
    return waitFor(function () { return resourceCalls(mounted.calls).length > 1; }, 3000);
  }).then(function () {
    assert.equal(resourceCalls(mounted.calls)[1].body.scenario, 'condensate_field_a');
    teardownLeadAssessment();
  });
});

test('lead-assessment: the folder row resolves the lead-scoped Polygons & Surfaces path', function () {
  var mounted = mountPage({});
  return waitFor(function () {
    var element = document.getElementById('la-folder-path');
    return element && element.textContent.indexOf('Polygons__Surfaces') >= 0;
  }).then(function () {
    var call = mounted.calls.filter(function (item) { return item.url.indexOf('/folders/') >= 0; })[0];
    assert.match(call.url, /\/api\/projects\/7\/folders\/polygons/);
    assert.equal(document.getElementById('copy-component-folder').disabled, false,
      'and the copy button arms once a path resolves');
    teardownLeadAssessment();
  });
});

test('lead-assessment: teardown makes a late response a no-op', function () {
  var mounted = mountPage({
    'GRV Inputs': { grv_p90_thousand_acre_ft: '12.6', grv_p10_thousand_acre_ft: '17.3' }
  });
  // Arm the auto-run, then navigate away inside the debounce window.
  userEdits(mounted, 'grv_p10_thousand_acre_ft');
  teardownLeadAssessment();
  assert.equal(leadAssessmentActive(), false);
  // The debounce timer was cancelled, so nothing is even sent.
  return new Promise(function (resolve) { setTimeout(resolve, 900); }).then(function () {
    assert.equal(resourceCalls(mounted.calls).length, 0);
  });
});

/* -------------------------------------------------------------------------
   THE AUTO-SAVE REMOUNT

   Every auto-run test above edits a grv_* key, which is why none of them ever
   saw this: an edit to ANY key in KEY_OWNER is auto-saved, and the save
   REMOUNTS this page (saveLeadAssessment -> refreshAfterRecordChange ->
   loadComponent -> renderLeadAssessment). The calc debounce is 600ms and the
   save debounce is 800ms, so the calculation is normally still in flight when
   that happens. A scenario change touches no owned key, saves nothing and
   never remounts — which is the ONLY reason it always produced a plot while an
   area edit often did not.

   These tests mount with an AREA pair (GRV empty, so the box model resolves)
   and drive the remount explicitly.
   ------------------------------------------------------------------------- */

// GRV deliberately absent: with no GRV pair, resolveCalculation falls to the
// box model, so the areas are what the auto-run reads.
var AREA_LEAD = {
  'Lead Assessment': {
    p90_area_km2: '5.72', p10_area_km2: '19.09', reservoir_thickness_ft: '144.4',
    polygons_surfaces_loaded: '1'
  }
};

// What the shell does to this page after an auto-save: the refreshed /detail
// carries the values that were just persisted, beginComponentLoad blanks
// #dynamic-fields, and renderLeadAssessment mounts the SAME component again.
// teardownLeadAssessment is NOT called — detail-form only tears down when a
// DIFFERENT page mounts — which is exactly why a remount can race a run.
function remountSameComponent(mounted) {
  var body = mounted.root.querySelector('#dynamic-fields');
  var stored = Object.assign({}, Store.allFields[PRIMARY_STEP] || {}, readFormValues(body));
  Store.allFields = Object.assign({}, Store.allFields);
  Store.allFields[PRIMARY_STEP] = stored;
  body.innerHTML = '';
  renderLeadAssessment(body, { onCopy: function () {} });
  return body;
}

// Navigation to ANOTHER lead's Lead Assessment page: a different record, a
// different task row, and no teardown in between (the same page is mounting).
function remountOtherLead(mounted) {
  var body = mounted.root.querySelector('#dynamic-fields');
  Store.projectId = 8;
  Store.tasks = LEAD_ASSESSMENT_STEPS.map(function (name, index) {
    return { task_id: 200 + index, task_name: name, comments: '', priority: 'Medium', revision: 1,
             stage_group: 'Lead Assessment', status: 'In Progress' };
  });
  Store.allFields = {};
  body.innerHTML = '';
  renderLeadAssessment(body, { onCopy: function () {} });
  return body;
}

function plotImages(mounted) {
  return mounted.root.querySelectorAll('.la-plot-slot img');
}

test('lead-assessment: an AREA edit drives the auto-run and lands its plot', function () {
  var mounted = mountPage(AREA_LEAD);
  userEdits(mounted, 'p90_area_km2', '6.25');
  return waitFor(function () { return resourceCalls(mounted.calls).length > 0; }, 3000).then(function () {
    assert.deepEqual(resourceCalls(mounted.calls)[0].body, {
      scenario: DEFAULT_SCENARIO, method: 'Box Model',
      area_p90_km2: 6.25, area_p10_km2: 19.09, thickness_p50_ft: 144.4
    }, 'the box model runs on the areas, not on a GRV pair');
    return waitFor(function () { return plotImages(mounted).length > 0; }, 3000);
  }).then(function () {
    assert.equal(plotImages(mounted)[0].getAttribute('src'), 'data:image/png;base64,GAS',
      'and the returned figure is painted into the result block\'s plot slot');
    teardownLeadAssessment();
  });
});

test('lead-assessment: the auto-save REMOUNT keeps the plot the same edit just painted', function () {
  var mounted = mountPage(AREA_LEAD);
  userEdits(mounted, 'p90_area_km2', '6.25');
  return waitFor(function () { return plotImages(mounted).length > 0; }, 3000).then(function () {
    remountSameComponent(mounted);
    // The fresh state is a fresh OBJECT, but it is the same lead's Lead
    // Assessment: what the user can see must survive the reload.
    assert.equal(plotImages(mounted).length, 1, 'the painted plot survives the remount');
    assert.equal(plotImages(mounted)[0].getAttribute('src'), 'data:image/png;base64,GAS');
    assert.equal(mounted.root.querySelector('.la-result-gas .la-result-box').textContent, '12.0',
      'and so do the numbers it was computed with');
    // The signature came across too, so re-touching the SAME value costs no
    // second request — a remount must not make the page forget what it ran.
    userEdits(mounted, 'p90_area_km2');
    return settle(1200);
  }).then(function () {
    assert.equal(resourceCalls(mounted.calls).length, 1,
      'the carried signature still short-circuits an unchanged re-run');
    teardownLeadAssessment();
  });
});

test('lead-assessment: a calculation that resolves AFTER the remount still paints', function () {
  var release = null;
  var mounted = mountPage(AREA_LEAD, null, function () {
    return new Promise(function (resolve) { release = function () { resolve(jsonResponse(RESULT)); }; });
  });
  userEdits(mounted, 'p90_area_km2', '6.25');
  return waitFor(function () { return resourceCalls(mounted.calls).length > 0 && !!release; }, 3000)
    .then(function () {
      // The save's reload wins the race — the common case, since the calc
      // debounce is shorter than the save debounce.
      remountSameComponent(mounted);
      assert.equal(plotImages(mounted).length, 0, 'nothing has landed yet');
      release();
      // The answer belongs to the component, not to the render that asked for
      // it, so the fresh mount is where it lands.
      return waitFor(function () { return plotImages(mounted).length > 0; }, 3000);
    }).then(function () {
      assert.equal(plotImages(mounted)[0].getAttribute('src'), 'data:image/png;base64,GAS');
      teardownLeadAssessment();
    });
});

test('lead-assessment: a debounce armed BEFORE the remount still runs after it', function () {
  var mounted = mountPage(AREA_LEAD);
  // Inside the 600ms window: the timer is armed, the reload arrives first.
  userEdits(mounted, 'p90_area_km2', '6.25');
  remountSameComponent(mounted);
  return waitFor(function () { return resourceCalls(mounted.calls).length > 0; }, 3000).then(function () {
    assert.equal(resourceCalls(mounted.calls)[0].body.area_p90_km2, 6.25,
      'the edit that armed the timer is still the edit that runs');
    return waitFor(function () { return plotImages(mounted).length > 0; }, 3000);
  }).then(function () {
    teardownLeadAssessment();
  });
});

/* KI-005 again, from the other side. Carrying userDirty across a remount is
   only legitimate for the SAME component, because only there was the remount
   caused by the user's own edit. Opening ANOTHER lead is a page VIEW, and a
   view must still compute nothing and — above all — persist nothing. */
test('lead-assessment: another lead\'s page starts CLEAN — no carried plots, no armed auto-run', function () {
  var mounted = mountPage(AREA_LEAD);
  userEdits(mounted, 'p90_area_km2', '6.25');
  return waitFor(function () { return plotImages(mounted).length > 0; }, 3000).then(function () {
    var before = resourceCalls(mounted.calls).length;
    remountOtherLead(mounted);
    assert.equal(plotImages(mounted).length, 0, 'the previous lead\'s figure does not follow it');
    assert.equal(mounted.root.querySelector('.la-result-gas .la-result-box').textContent, '—',
      'nor do its numbers');
    // The interaction gate is shut: the auto-run persists, so a page the user
    // has only LOOKED at may not fire even when asked to.
    scheduleCalculation(0);
    return settle(900).then(function () {
      assert.equal(resourceCalls(mounted.calls).length, before,
        'userDirty did not survive the switch — a view computes nothing');
      assert.equal(fieldWriteCalls(mounted.calls).length,
        fieldWriteCalls(mounted.calls).filter(function (call) {
          return call.url.indexOf('/tasks/100/') >= 0;
        }).length, 'and nothing was written onto the lead just opened');
      teardownLeadAssessment();
    });
  });
});

/* -------------------------------------------------------------------------
   The ALL-FIELDS editor still renders every key

   The consolidated page custom-renders these four steps, but SCHEMA remains
   the field REGISTRY (see its comment) and views/project-editor.js renders
   straight from it, knowing nothing about card 2B. So the contract to keep is:
   every new key is a type the generic renderer already handles, and a round
   trip through renderFields -> getFields returns it unchanged.
   ------------------------------------------------------------------------- */

test('lead-assessment: every new key is registered in SCHEMA on its owning step', function () {
  Object.keys(KEY_OWNER).forEach(function (key) {
    var declared = (SCHEMA[KEY_OWNER[key]] || []).some(function (field) { return field.key === key; });
    assert.ok(declared, key + ' is declared on ' + KEY_OWNER[key]);
  });
});

test('lead-assessment: the generic field renderer handles the merged step harmlessly', function () {
  var stored = {
    'Lead Assessment': Object.assign(goodValues({ polygons_surfaces_loaded: '1' }),
                                     { thickness_source_mode: 'twt' })
  };
  LEAD_ASSESSMENT_STEPS.forEach(function (step) {
    var host = fixture('<div id="pe-fields-' + step.replace(/\W/g, '') + '"></div>');
    var root = host.firstChild;
    renderFields(step, stored[step], root, function () {});
    // Every declared key produced exactly one control...
    (SCHEMA[step] || []).forEach(function (field) {
      assert.equal(root.querySelectorAll('[data-field="' + field.key + '"]').length, 1,
        step + ' renders ' + field.key);
    });
    // ...and the values round-trip unchanged, so an all-fields save of this
    // step writes back what the consolidated page stored.
    var harvested = getFields(root);
    Object.keys(stored[step]).forEach(function (key) {
      assert.equal(harvested[key], stored[step][key], step + ' round-trips ' + key);
    });
  });
});

test('lead-assessment: the source marker is a bounded select, not free text', function () {
  var field = SCHEMA['Lead Assessment'].find(function (item) {
    return item.key === 'thickness_source_mode';
  });
  assert.equal(field.type, 'select');
  assert.deepEqual(field.options, ['', 'twt', 'thickness'],
    'the three real values and nothing else');
});

test('lead-assessment: the depth-ish keys are exempt from the generic 9999 cap', function () {
  // A TVDSS and a two-way time both run past four digits in real data; the
  // generic numeric scan would otherwise reject them as "too large".
  ['top_formation_tvdss_ft'].forEach(function (key) {
    assert.equal(SCHEMA['Lead Assessment'].find(function (f) { return f.key === key; }).bigOk, true);
  });
  ['twt_reservoir_ms', 'twt_formation_ms'].forEach(function (key) {
    assert.equal(SCHEMA['Lead Assessment'].find(function (f) { return f.key === key; }).bigOk, true);
  });
  ['grv_p90_thousand_acre_ft', 'grv_p10_thousand_acre_ft'].forEach(function (key) {
    assert.equal(SCHEMA['Lead Assessment'].find(function (f) { return f.key === key; }).bigOk, true);
  });
});

/* -------------------------------------------------------------------------
   Mounted: the conversion, driven the way a user drives it

   The rules above are pure; these three drive the WIRING — typing decides the
   source column, the derived column fills itself and locks, and clearing the
   source releases the section so the other column may take over.
   ------------------------------------------------------------------------- */

function mountWithCoefficients(fields) {
  return mountPage(fields, {
    resource_scenarios: SCENARIOS,
    twt_thickness_coefficients: COEFFICIENTS
  });
}

test('lead-assessment: typing a TWT derives the thickness and LOCKS that column', function () {
  var mounted = mountWithCoefficients({});
  // Pending-configuration furniture is absent once coefficients exist.
  assert.equal(mounted.root.querySelector('[data-la-note="conversion"]'), null,
    'no "pending configuration" note when the conversion is configured');
  assert.equal(mounted.root.querySelector('[data-la-field="reservoir_thickness_ft"]').readOnly, false,
    'and neither column is locked until the user picks a side');

  var twt = mounted.root.querySelector('[data-la-field="twt_reservoir_ms"]');
  twt.value = '500';
  twt.dispatchEvent(new Event('input', { bubbles: true }));

  var derived = mounted.root.querySelector('[data-la-field="reservoir_thickness_ft"]');
  assert.equal(derived.value, '200', 'm=0.5, b=-50 over 500 ms');
  assert.equal(derived.readOnly, true, 'the derived side cannot be typed into');
  assert.ok(derived.classList.contains('la-derived'));
  // The OTHER row's derived cell locks with it (one source column per section)
  // but stays blank until its own source is entered.
  var otherDerived = mounted.root.querySelector('[data-la-field="formation_thickness_ft"]');
  assert.equal(otherDerived.readOnly, true);
  assert.equal(otherDerived.value, '');
  // The source column is still editable, and re-derives as it changes.
  assert.equal(mounted.root.querySelector('[data-la-field="twt_reservoir_ms"]').readOnly, false);
  var live = mounted.root.querySelector('[data-la-field="twt_reservoir_ms"]');
  live.value = '600';
  live.dispatchEvent(new Event('input', { bubbles: true }));
  assert.equal(mounted.root.querySelector('[data-la-field="reservoir_thickness_ft"]').value, '250');
  teardownLeadAssessment();
});

test('lead-assessment: touching the DERIVED cell is refused with the clear-the-source message', function () {
  var mounted = mountWithCoefficients({});
  var twt = mounted.root.querySelector('[data-la-field="twt_reservoir_ms"]');
  twt.value = '500';
  twt.dispatchEvent(new Event('input', { bubbles: true }));

  var derived = mounted.root.querySelector('[data-la-field="reservoir_thickness_ft"]');
  derived.dispatchEvent(new Event('click', { bubbles: true }));
  var strip = mounted.root.querySelector('.la-card-errors[data-la-errors="thickness"]');
  assert.equal(strip.textContent,
    'Clear Reservoir TWT (ms) before entering Reservoir Thickness (ft).');
  assert.ok(!strip.hidden, 'the thickness card strip shows the refusal');
  teardownLeadAssessment();
});

test('lead-assessment: an invalid value lights its card strip and the input; fixing it clears both', function () {
  var mounted = mountPage({});
  var input = userEdits(mounted, 'twt_reservoir_ms', '-5');
  var strip = mounted.root.querySelector('.la-card-errors[data-la-errors="thickness"]');
  assert.equal(strip.textContent, 'Reservoir TWT (ms) must be a number greater than 0.');
  assert.ok(!strip.hidden, 'the message shows in the owning card\'s strip');
  assert.ok(input.classList.contains('la-invalid'), 'the red border points at the field');

  // The OTHER twin errors independently, into its OWN strip, at the same time.
  var area = userEdits(mounted, 'p90_area_km2', '-2');
  var volumeStrip = mounted.root.querySelector('.la-card-errors[data-la-errors="volume"]');
  assert.equal(volumeStrip.textContent, 'Area P90 (km²) must be a number greater than 0.');
  assert.ok(!volumeStrip.hidden);
  assert.ok(area.classList.contains('la-invalid'));
  assert.ok(!strip.hidden, 'the thickness message stays put');

  // Fixing the fields clears message and border, card by card.
  userEdits(mounted, 'twt_reservoir_ms', '1500');
  assert.ok(strip.hidden, 'fixed = hidden again');
  assert.equal(strip.textContent, '');
  assert.ok(!input.classList.contains('la-invalid'));
  assert.ok(!volumeStrip.hidden, 'the volume error is still standing');
  userEdits(mounted, 'p90_area_km2', '12.6');
  assert.ok(volumeStrip.hidden);
  assert.ok(!area.classList.contains('la-invalid'));
  teardownLeadAssessment();
});

test('lead-assessment: clearing the source RELEASES the section so the other column can take over', function () {
  var mounted = mountWithCoefficients({});
  var twt = mounted.root.querySelector('[data-la-field="twt_reservoir_ms"]');
  twt.value = '500';
  twt.dispatchEvent(new Event('input', { bubbles: true }));
  assert.equal(mounted.root.querySelector('[data-la-field="reservoir_thickness_ft"]').readOnly, true);

  // Clear the last source value...
  var source = mounted.root.querySelector('[data-la-field="twt_reservoir_ms"]');
  source.value = '';
  source.dispatchEvent(new Event('input', { bubbles: true }));
  var thickness = mounted.root.querySelector('[data-la-field="reservoir_thickness_ft"]');
  assert.equal(thickness.readOnly, false, 'both columns are editable again');
  assert.equal(thickness.value, '', 'a derivation with no source is not a measurement');
  assert.equal(mounted.root.querySelector('[data-la-field="twt_reservoir_ms"]').readOnly, false);

  // ...and now the OTHER column may become the source, deriving in reverse.
  var typed = mounted.root.querySelector('[data-la-field="reservoir_thickness_ft"]');
  typed.value = '200';
  typed.dispatchEvent(new Event('input', { bubbles: true }));
  assert.equal(mounted.root.querySelector('[data-la-field="twt_reservoir_ms"]').value, '500');
  assert.equal(mounted.root.querySelector('[data-la-field="twt_reservoir_ms"]').readOnly, true);
  assert.equal(mounted.root.querySelector('[data-la-field="reservoir_thickness_ft"]').readOnly, false);
  teardownLeadAssessment();
});

test('lead-assessment: the source mode saved is the one the section is actually in', function () {
  var mounted = mountWithCoefficients({});
  var twt = mounted.root.querySelector('[data-la-field="twt_formation_ms"]');
  twt.value = '500';
  twt.dispatchEvent(new Event('input', { bubbles: true }));
  // The marker is not a form field — it is section state — so a plain harvest
  // does not carry it; saveLeadAssessment stamps it on before planning.
  var values = readFormValues(mounted.root.querySelector('#dynamic-fields'));
  assert.equal(values.thickness_source_mode, undefined);
  values.thickness_source_mode = 'twt';
  var plan = buildSavePlan(values, {});
  var thicknessEntry = plan.filter(function (e) { return e.taskName === 'Lead Assessment'; })[0];
  assert.equal(thicknessEntry.fields.thickness_source_mode, 'twt');
  assert.equal(thicknessEntry.fields.formation_thickness_ft, '180', 'the derived feet are saved too');
  teardownLeadAssessment();
});
