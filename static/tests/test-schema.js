// Tests for static/js/schema.js — piip() factory shape and whole-SCHEMA
// integrity (key/label/type presence, known types, reference wiring).
import { test, assert } from './harness.js';
import {
  piip, SCHEMA, PROSPECT_STAGES, BP_STAGES, STATUSES, DONE,
  SEISMIC_BLOCKS, FLUID_TYPES, FORMATIONS, FORMATION_METRICS,
  RESERVOIR_COS_COLUMNS, FLOWBACK_STAGE_COLUMNS, FLOWBACK_RATE_FIELDS,
  RESOURCE_SCENARIOS, validateStepFields, numericFieldError,
  SAD_FORMATION_COLUMNS, REQUIRED_FIELDS_FOR_SUBMIT, submitBlockedMessage
} from '../js/schema.js';

// Types actually used by field definitions in schema.js; detail-form renders
// these. 'index' appears only as a repeatable-column type (display-only).
var KNOWN_FIELD_TYPES = ['number', 'text', 'checkbox', 'select', 'radio', 'link', 'repeatable', 'formations'];
var KNOWN_COLUMN_TYPES = ['number', 'text', 'select', 'index'];

// --- piip() ----------------------------------------------------------------

test('schema.piip returns 7 entries with prefixed keys', function () {
  var fields = piip('xx');
  assert.equal(fields.length, 7);
  var keys = fields.map(function (f) { return f.key; });
  assert.deepEqual(keys, [
    'xx_gas_p90', 'xx_gas_mean', 'xx_gas_p10',
    'xx_has_liquid',
    'xx_liquid_p90', 'xx_liquid_mean', 'xx_liquid_p10'
  ]);
  keys.forEach(function (key) { assert.match(key, /^xx_/, 'key is prefixed'); });
});

test('schema.piip wires showIf onto the liquid trio only', function () {
  var fields = piip('pfx');
  fields.forEach(function (field) {
    if (/_liquid_(p90|mean|p10)$/.test(field.key)) {
      assert.equal(field.showIf, 'pfx_has_liquid', field.key + ' shows behind the checkbox');
    } else {
      assert.equal(field.showIf, undefined, field.key + ' has no showIf');
    }
  });
  var checkbox = fields[3];
  assert.equal(checkbox.type, 'checkbox');
  assert.equal(checkbox.key, 'pfx_has_liquid');
});

test('schema.piip: gas trio is numbers grouped in one row, Mean between P90 and P10', function () {
  var fields = piip('p');
  var gas = fields.slice(0, 3);
  gas.forEach(function (f) {
    assert.equal(f.type, 'number');
    assert.equal(f.row, 'p_gas');
  });
  assert.deepEqual(gas.map(function (f) { return f.label; }), ['P90', 'Mean', 'P10']);
  var liquid = fields.slice(4);
  liquid.forEach(function (f) { assert.equal(f.row, 'p_liquid'); });
});

// --- SCHEMA integrity ------------------------------------------------------

function checkFieldList(stepName, fields, knownTypes, context) {
  var keys = {};
  fields.forEach(function (field) {
    var where = context + ' "' + stepName + '" field ' + JSON.stringify(field.key);
    assert.ok(field && typeof field === 'object', where + ' is an object');
    assert.ok(typeof field.key === 'string' && field.key.length, where + ' has a key');
    assert.ok(typeof field.label === 'string' && field.label.length, where + ' has a label');
    assert.ok(typeof field.type === 'string' && field.type.length, where + ' has a type');
    assert.ok(knownTypes.indexOf(field.type) >= 0, where + ' type "' + field.type + '" is known');
    assert.ok(!keys[field.key], where + ' key is unique within the step');
    keys[field.key] = true;
  });
  return keys;
}

test('schema.SCHEMA: every field has key/label/type and a known type; keys unique per step', function () {
  var stepNames = Object.keys(SCHEMA);
  assert.ok(stepNames.length > 20, 'SCHEMA has a meaningful number of steps');
  stepNames.forEach(function (stepName) {
    var fields = SCHEMA[stepName];
    assert.ok(Array.isArray(fields), 'step "' + stepName + '" is an array');
    checkFieldList(stepName, fields, KNOWN_FIELD_TYPES, 'step');
  });
});

test('schema.SCHEMA: every repeatable has non-empty columns with valid column defs', function () {
  Object.keys(SCHEMA).forEach(function (stepName) {
    SCHEMA[stepName].forEach(function (field) {
      if (field.type !== 'repeatable') return;
      var where = 'step "' + stepName + '" repeatable ' + field.key;
      assert.ok(Array.isArray(field.columns) && field.columns.length, where + ' has columns');
      checkFieldList(stepName + '/' + field.key, field.columns, KNOWN_COLUMN_TYPES, 'columns of');
    });
  });
});

test('schema.SCHEMA: selects carry an options array (or a named optionsFrom map)', function () {
  function checkSelect(where, field) {
    if (field.type !== 'select') return;
    if (field.optionsFrom !== undefined) {
      assert.ok(typeof field.optionsFrom === 'string' && field.optionsFrom.length,
        where + ' optionsFrom is a non-empty string');
      return;
    }
    assert.ok(Array.isArray(field.options), where + ' options is an array');
    assert.ok(field.options.length, where + ' options is non-empty');
  }
  Object.keys(SCHEMA).forEach(function (stepName) {
    SCHEMA[stepName].forEach(function (field) {
      checkSelect('step "' + stepName + '" ' + field.key, field);
      (field.columns || []).forEach(function (col) {
        checkSelect('step "' + stepName + '" column ' + col.key, col);
      });
    });
  });
});

test('schema.SCHEMA: radio fields carry options too', function () {
  Object.keys(SCHEMA).forEach(function (stepName) {
    SCHEMA[stepName].forEach(function (field) {
      if (field.type !== 'radio') return;
      assert.ok(Array.isArray(field.options) && field.options.length,
        'radio ' + field.key + ' in "' + stepName + '" has options');
    });
  });
});

test('schema.SCHEMA: showIf references an existing key in the same step', function () {
  Object.keys(SCHEMA).forEach(function (stepName) {
    var keys = {};
    SCHEMA[stepName].forEach(function (field) { keys[field.key] = true; });
    SCHEMA[stepName].forEach(function (field) {
      if (field.showIf === undefined) return;
      assert.ok(keys[field.showIf],
        'step "' + stepName + '" ' + field.key + ' showIf → "' + field.showIf + '" exists in the step');
    });
  });
});

test('schema.SCHEMA: dependsOn in repeatable columns names a sibling column', function () {
  Object.keys(SCHEMA).forEach(function (stepName) {
    SCHEMA[stepName].forEach(function (field) {
      if (!Array.isArray(field.columns)) return;
      var colKeys = {};
      field.columns.forEach(function (col) { colKeys[col.key] = true; });
      field.columns.forEach(function (col) {
        if (col.dependsOn === undefined) return;
        assert.ok(colKeys[col.dependsOn],
          'column ' + col.key + ' of ' + field.key + ' dependsOn → "' + col.dependsOn + '" is a sibling column');
      });
    });
  });
});

test('schema.SCHEMA: row grouping ids are non-empty strings shared by at least two fields', function () {
  // `row` is a layout grouping token (not a key reference): fields carrying the
  // same row id render side by side. A row id used only once is suspicious.
  Object.keys(SCHEMA).forEach(function (stepName) {
    var counts = {};
    SCHEMA[stepName].forEach(function (field) {
      if (field.row === undefined) return;
      assert.ok(typeof field.row === 'string' && field.row.length,
        'step "' + stepName + '" ' + field.key + ' row id is a non-empty string');
      counts[field.row] = (counts[field.row] || 0) + 1;
    });
    Object.keys(counts).forEach(function (rowId) {
      assert.ok(counts[rowId] >= 2, 'row id "' + rowId + '" in "' + stepName + '" groups >= 2 fields');
    });
  });
});

// --- supporting vocab ------------------------------------------------------

test('schema boot fallbacks: stages, statuses, DONE', function () {
  assert.deepEqual(PROSPECT_STAGES, ['Lead Identification', 'Risking', 'Segmentation', 'Pre-Well Delivery']);
  assert.deepEqual(BP_STAGES, ['Well Delivery', 'Post-Drilling', 'Post-Testing']);
  assert.equal(STATUSES.length, 4);
  assert.deepEqual(DONE, { 'Approved': 1 });
  STATUSES.forEach(function (status) {
    assert.ok(typeof status === 'string' && status.length, 'status is a non-empty string');
  });
});

test('schema.SEISMIC_BLOCKS fallback maps block → array of AR strings', function () {
  var blocks = Object.keys(SEISMIC_BLOCKS);
  assert.ok(blocks.length >= 1);
  blocks.forEach(function (block) {
    assert.ok(Array.isArray(SEISMIC_BLOCKS[block]), block + ' maps to an array');
    SEISMIC_BLOCKS[block].forEach(function (ar) {
      assert.equal(typeof ar, 'string', 'AR numbers are strings');
    });
  });
});

test('schema.FLOWBACK_RATE_FIELDS keys exist as flowback stage columns', function () {
  var colKeys = {};
  FLOWBACK_STAGE_COLUMNS.forEach(function (col) { colKeys[col.key] = true; });
  Object.keys(FLOWBACK_RATE_FIELDS).forEach(function (fluid) {
    var entry = FLOWBACK_RATE_FIELDS[fluid];
    assert.ok(colKeys[entry.key], 'rate key for "' + fluid + '" (' + entry.key + ') is a stage column');
    assert.ok(FLUID_TYPES.indexOf(fluid) >= 0, '"' + fluid + '" is a known fluid type');
    assert.ok(typeof entry.unit === 'string' && entry.unit.length, 'unit present for ' + fluid);
  });
});

// GET /api/meta's resource_scenarios boot fallback: labels are verbatim
// copies of resource-assessment/config/scenarios.yaml display_name entries
// (views/resource-calculator.js's scenario segmented control) -- pinned here
// so a drifting label is caught immediately.
test('schema.RESOURCE_SCENARIOS: all four configured scenarios, exact labels', function () {
  assert.deepEqual(RESOURCE_SCENARIOS, [
    { id: 'dry_gas_high_pressure', label: 'Dry Gas - High Pressure Zone', resource_type: 'dry_gas' },
    { id: 'dry_gas_low_pressure', label: 'Dry Gas - Low Pressure Zone', resource_type: 'dry_gas' },
    { id: 'condensate_field_a', label: 'Condensate - Field A', resource_type: 'condensate' },
    { id: 'condensate_field_b', label: 'Condensate - Field B', resource_type: 'condensate' }
  ]);
  RESOURCE_SCENARIOS.forEach(function (scenario) {
    assert.ok(['dry_gas', 'condensate'].indexOf(scenario.resource_type) >= 0,
      scenario.id + ' resource_type is dry_gas or condensate');
  });
});

// --- v4 BP step merges ----------------------------------------------------
// Four steps were merged away (URED Update, Post-Drilling Resource Assessment,
// Resource Assessment Update, Executive Summary Final). Their EAV keys had to
// survive the move onto the steps that absorbed them -- renaming a key orphans
// every stored value -- and the retired names must no longer render as steps.

test('schema.SCHEMA: the four v4-retired steps are gone from SCHEMA', function () {
  ['URED Update', 'Post-Drilling Resource Assessment',
   'Resource Assessment Update', 'Executive Summary Final'].forEach(function (name) {
    assert.ok(!(name in SCHEMA), name + ' is no longer a rendered step');
  });
});

test('schema.SCHEMA: merged steps keep the retired steps\' EAV keys', function () {
  function keys(step) { return SCHEMA[step].map(function (f) { return f.key; }); }
  var sadModel = keys('SAD Model');
  // post_drill_piip_* trio + the kept liquid/fluid select.
  assert.ok(sadModel.indexOf('post_drill_piip_gas_mean') >= 0, 'SAD Model keeps post_drill_piip_gas_mean');
  assert.ok(sadModel.indexOf('post_drill_fluid_type') >= 0, 'SAD Model keeps the fluid select');
  // The formations PICKER was dropped in favour of the optional table.
  assert.ok(sadModel.indexOf('post_drill_formations') < 0, 'SAD Model dropped the formations picker');
  assert.ok(sadModel.indexOf('sad_formation_rows') >= 0, 'SAD Model gained the optional formation table');

  var sadUpdate = keys('SAD Update');
  assert.ok(sadUpdate.indexOf('resource_update_gas_mean') >= 0, 'SAD Update keeps resource_update_gas_mean');
  assert.ok(sadUpdate.indexOf('resource_update_fluid_type') >= 0, 'SAD Update keeps the fluid select');
  assert.ok(sadUpdate.indexOf('resource_update_formations') < 0, 'SAD Update dropped the formations picker');
  assert.ok(sadUpdate.indexOf('sad_update_formation_rows') >= 0, 'SAD Update gained the optional formation table');

  assert.deepEqual(keys('Executive Summary'), ['exec_summary_loaded', 'ured_update_loaded']);
  SCHEMA['Executive Summary'].forEach(function (field) {
    assert.equal(field.type, 'checkbox', field.key + ' is a checkbox');
    assert.equal(field.row, 'exec_summary_docs', field.key + ' shares one row');
  });
});

test('schema.SAD_FORMATION_COLUMNS: one row per formation, valid column defs', function () {
  checkFieldList('SAD_FORMATION_COLUMNS', SAD_FORMATION_COLUMNS, KNOWN_COLUMN_TYPES, 'columns');
  var keys = SAD_FORMATION_COLUMNS.map(function (col) { return col.key; });
  assert.deepEqual(keys, ['sad_formation', 'sad_top_tvdss_ft', 'sad_base_tvdss_ft',
    'sad_thickness_ft', 'sad_phit_pct', 'sad_swt_pct', 'sad_ngr_pct', 'sad_fluid']);
  // Both merged SAD steps render the SAME column set (one shared definition).
  ['SAD Model', 'SAD Update'].forEach(function (step) {
    var table = SCHEMA[step].filter(function (f) { return f.type === 'repeatable'; })[0];
    assert.equal(table.columns, SAD_FORMATION_COLUMNS, step + ' reuses SAD_FORMATION_COLUMNS');
  });
});

// --- submit gating (mirror of workflow/constants.py) -----------------------

test('schema.REQUIRED_FIELDS_FOR_SUBMIT: SAD Update declares both sign-offs', function () {
  assert.deepEqual(REQUIRED_FIELDS_FOR_SUBMIT['SAD Update'], [
    ['sad_update_done', 'SAD Update'],
    ['final_exec_summary_done', 'Final Executive Summary']
  ]);
  // Every gated key must actually exist as a checkbox on its step, or the UI
  // would demand something the form cannot offer.
  Object.keys(REQUIRED_FIELDS_FOR_SUBMIT).forEach(function (step) {
    var byKey = {};
    (SCHEMA[step] || []).forEach(function (field) { byKey[field.key] = field; });
    REQUIRED_FIELDS_FOR_SUBMIT[step].forEach(function (entry) {
      assert.ok(byKey[entry[0]], step + ' renders ' + entry[0]);
      assert.equal(byKey[entry[0]].type, 'checkbox', entry[0] + ' is a checkbox');
      assert.equal(byKey[entry[0]].label, entry[1], entry[0] + ' label matches the gate message');
    });
  });
});

test('schema.submitBlockedMessage names every unmet checkbox', function () {
  assert.equal(submitBlockedMessage('SAD Update', {}),
    'Cannot submit until these are checked: SAD Update, Final Executive Summary.');
  assert.equal(submitBlockedMessage('SAD Update', { sad_update_done: '1' }),
    'Cannot submit until these are checked: Final Executive Summary.');
  // Falsy stored values do not count; the app's truthy vocabulary does.
  assert.equal(submitBlockedMessage('SAD Update', { sad_update_done: '1', final_exec_summary_done: '0' }),
    'Cannot submit until these are checked: Final Executive Summary.');
  assert.equal(submitBlockedMessage('SAD Update', { sad_update_done: '1', final_exec_summary_done: '' }),
    'Cannot submit until these are checked: Final Executive Summary.');
  assert.equal(submitBlockedMessage('SAD Update', { sad_update_done: 'true', final_exec_summary_done: 'yes' }), null);
});

test('schema.submitBlockedMessage lets ungated steps through', function () {
  assert.equal(submitBlockedMessage('SAD Model', {}), null);
  assert.equal(submitBlockedMessage('Executive Summary', {}), null);
  assert.equal(submitBlockedMessage('Nope', null), null);
});

test('schema formations vocabulary', function () {
  assert.deepEqual(FORMATIONS, ['SARH', 'QASM', 'QWRH']);
  checkFieldList('FORMATION_METRICS', FORMATION_METRICS, KNOWN_COLUMN_TYPES, 'metrics');
  checkFieldList('RESERVOIR_COS_COLUMNS', RESERVOIR_COS_COLUMNS, KNOWN_COLUMN_TYPES, 'columns');
});

// --- validateStepFields (generic step-form sanity checks) -------------------
// Mirrors views/resource-calculator.js's validateResourceInputs, generalized
// to every SCHEMA step. Wired into saveComponent (detail-form.js) and
// saveComponentCard (project-editor.js) ahead of the save API call.

test('validateStepFields: a wholly blank step always passes (every field is optional)', function () {
  assert.equal(validateStepFields('Reservoir Area Definition', {}), null);
  assert.equal(validateStepFields('Seal CoS', {}), null);
  assert.equal(validateStepFields('Reservoir Area Definition', undefined), null, 'missing fields object defaults to {}');
});

test('validateStepFields: (a) non-numeric value is rejected by field label', function () {
  assert.equal(validateStepFields('Reservoir Area Definition', { p90_area_km2: 'abc' }),
    'P90 Area (km²) must be numeric.');
});

test('validateStepFields: (b) a negative value is rejected', function () {
  assert.equal(validateStepFields('Reservoir Area Definition', { p90_area_km2: '-5' }),
    'P90 Area (km²) must not be negative.');
});

test('validateStepFields: (c) a value over the 9999 cap is rejected on a plain (non-bigOk) field', function () {
  assert.equal(validateStepFields('Reservoir Area Definition', { p90_area_km2: '10000' }),
    'P90 Area (km²) looks too large (max 9999).');
  // 9999 itself is still in range (p10 left blank so the ordering rule
  // below doesn't also fire).
  assert.equal(validateStepFields('Reservoir Area Definition', { p90_area_km2: '9999' }), null);
});

test('validateStepFields: bigOk-flagged fields are exempt from the 9999 cap', function () {
  // staking_well_x is a UTM coordinate (bigOk: true in schema.js); every
  // other field on the step is left blank so only this field is exercised.
  assert.equal(validateStepFields('Staking Moving Tolerance', { staking_well_x: '650000' }), null);
});

// No *writable* field ending in `_pct` exists in SCHEMA today (every current
// one -- Reservoir/Trap/Seal CoS -- is readonly:true, so validateStepFields
// never reaches it), so rule (d) is exercised on the exported
// numericFieldError helper directly -- see its doc comment in schema.js.
test('validateStepFields: (d) numericFieldError rejects an out-of-range percentage', function () {
  assert.equal(numericFieldError('Porosity (%)', '150', false, true), 'Porosity (%) must not exceed 100%.');
  assert.equal(numericFieldError('Porosity (%)', '100', false, true), null, '100% itself is in range');
  assert.equal(numericFieldError('Porosity (%)', '45.5', false, true), null);
});

test('validateStepFields: readonly:true fields are never checked (Trap CoS output is calculated, not typed)', function () {
  // trap_cos_pct is readonly; a value that would otherwise fail every rule
  // must not block the save of its own step.
  assert.equal(validateStepFields('Trap CoS', { trap_cos_pct: '-1234567' }), null);
});

test('validateStepFields: repeatable numeric columns are checked (cheap parse-back of the JSON rows)', function () {
  var rows = JSON.stringify([{ amplitude_ratio: 'not-a-number' }]);
  assert.equal(validateStepFields('Reservoir CoS', { reservoir_cos_rows: rows }),
    'Amplitude Ratio must be numeric.');
  // base_tight_sarah is bigOk (a depth) -- large values pass.
  var deepRow = JSON.stringify([{ amplitude_ratio: '1.2', base_tight_sarah: '12000' }]);
  assert.equal(validateStepFields('Reservoir CoS', { reservoir_cos_rows: deepRow }), null);
  // A readonly repeatable column (reservoir_cos_pct) is never checked either.
  var readonlyRow = JSON.stringify([{ reservoir_cos_pct: '999' }]);
  assert.equal(validateStepFields('Reservoir CoS', { reservoir_cos_rows: readonlyRow }), null);
});

test('validateStepFields: cross-field -- Reservoir Area Definition P90 must be lower than P10', function () {
  assert.equal(validateStepFields('Reservoir Area Definition', { p90_area_km2: '2', p10_area_km2: '1' }),
    'Area P90 must be lower than Area P10.');
  assert.equal(validateStepFields('Reservoir Area Definition', { p90_area_km2: '2', p10_area_km2: '2' }),
    'Area P90 must be lower than Area P10.', 'equal values still fail -- strictly lower, matching the popup');
  assert.equal(validateStepFields('Reservoir Area Definition', { p90_area_km2: '1', p10_area_km2: '2' }), null);
  assert.equal(validateStepFields('Reservoir Area Definition', { p90_area_km2: '2' }), null, 'only one side filled: no comparison');
});

test('validateStepFields: cross-field -- Thickness Estimation reservoir must not exceed Sarah formation thickness', function () {
  assert.equal(validateStepFields('Thickness Estimation', { reservoir_thickness_ft: '60', formation_thickness_ft: '50' }),
    'Reservoir Thickness must not exceed Sarah Formation Thickness.');
  // Equal is allowed ("<=" is the valid condition) -- permissive on purpose.
  assert.equal(validateStepFields('Thickness Estimation', { reservoir_thickness_ft: '50', formation_thickness_ft: '50' }), null);
  assert.equal(validateStepFields('Thickness Estimation', { reservoir_thickness_ft: '40', formation_thickness_ft: '50' }), null);
});

test('validateStepFields: piip trio ordering -- P90 must not exceed Mean, Mean must not exceed P10', function () {
  assert.equal(validateStepFields('Pre-Drilling Resource Assessment', { pre_drill_piip_gas_p90: '10', pre_drill_piip_gas_mean: '5' }),
    'Gas P90 must not exceed Mean.');
  assert.equal(validateStepFields('Pre-Drilling Resource Assessment', { pre_drill_piip_gas_mean: '20', pre_drill_piip_gas_p10: '10' }),
    'Gas Mean must not exceed P10.');
});

test('validateStepFields: piip trio -- equal values are permitted (manual deterministic entry)', function () {
  assert.equal(validateStepFields('Pre-Drilling Resource Assessment', {
    pre_drill_piip_gas_p90: '10', pre_drill_piip_gas_mean: '10', pre_drill_piip_gas_p10: '10'
  }), null);
});

test('validateStepFields: piip trio -- the liquid trio is checked too, independent of the gas trio', function () {
  assert.equal(validateStepFields('Pre-Drilling Resource Assessment', {
    pre_drill_piip_liquid_p90: '9', pre_drill_piip_liquid_mean: '3'
  }), 'Liquid P90 must not exceed Mean.');
});

// Lead Resource Assessment's SCHEMA entry is now empty (the Resource
// Assessment calculator, views/resource-calculator.js, is the step's whole
// body; Apply writes lead_piip_* via a direct API.saveFields PATCH that
// never goes through getFields()/validateStepFields) -- so in practice this
// step's own save path can never populate lead_piip_* keys, and the
// lead_piip trio rule can never fire through it. The rule itself is left in
// PIIP_PREFIXES (schema.js) regardless -- proven still correct here by
// constructing the fields object directly, independent of any form.
test('validateStepFields: Lead Resource Assessment has no editable fields; the lead_piip rule still holds if ever exercised directly', function () {
  assert.deepEqual(SCHEMA['Lead Resource Assessment'], []);
  assert.equal(validateStepFields('Lead Resource Assessment', {}), null);
  assert.equal(validateStepFields('Lead Resource Assessment', { lead_piip_gas_p90: '10', lead_piip_gas_mean: '5' }),
    'Gas P90 must not exceed Mean.');
});
