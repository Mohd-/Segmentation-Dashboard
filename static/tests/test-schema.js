// Tests for static/js/schema.js — piip() factory shape and whole-SCHEMA
// integrity (key/label/type presence, known types, reference wiring).
import { test, assert } from './harness.js';
import {
  piip, SCHEMA, PROSPECT_STAGES, BP_STAGES, STATUSES, DONE,
  SEISMIC_BLOCKS, FLUID_TYPES, FORMATIONS, FORMATION_METRICS,
  RESERVOIR_COS_COLUMNS, FLOWBACK_STAGE_COLUMNS, FLOWBACK_RATE_FIELDS,
  RESOURCE_SCENARIOS
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
// (views/resource-popup.js's scenario segmented control) -- pinned here so a
// drifting label is caught immediately.
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

test('schema formations vocabulary', function () {
  assert.deepEqual(FORMATIONS, ['SARH', 'QASM', 'QWRH']);
  checkFieldList('FORMATION_METRICS', FORMATION_METRICS, KNOWN_COLUMN_TYPES, 'metrics');
  checkFieldList('RESERVOIR_COS_COLUMNS', RESERVOIR_COS_COLUMNS, KNOWN_COLUMN_TYPES, 'columns');
});
