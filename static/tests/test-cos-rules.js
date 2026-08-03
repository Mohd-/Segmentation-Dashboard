// Tests for static/js/cos-rules.js — the client-side mirror of cos.py's Trap
// and Seal CoS formulas. The promise is FIDELITY: for the same inputs these
// return exactly what cos.calculate_trap_cos / calculate_seal_cos would store,
// so the value the form shows live is the value a save persists. The numeric
// cases below are copied from tests/test_cos.py's pins.
import { test, assert } from './harness.js';
import { calculateTrapCos, calculateSealCos, TRAP_COS_FACTORS, TRAP_COS_SCORES } from '../js/cos-rules.js';

// --- Trap: the approved threshold table, verbatim ---------------------------

test('cos-rules: the Trap threshold table matches cos.py pair for pair', function () {
  assert.deepEqual(TRAP_COS_FACTORS, [0.0, 0.036, 0.108, 0.242, 0.405, 0.554, 0.882, 2.13]);
  assert.deepEqual(TRAP_COS_SCORES, [0.7, 0.725, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0]);
});

test('cos-rules: calculateTrapCos walks the table exactly like the server (test_cos.py pins)', function () {
  // (a, b, expected) rows copied from test_trap_cos_threshold_table_examples.
  assert.equal(calculateTrapCos(100, 100), '50', 'no threshold strictly below b -> 0.5 floor');
  assert.equal(calculateTrapCos(100, 101), '70', 'exceeds only the first threshold');
  assert.equal(calculateTrapCos(100, 105), '72', '0.725 -> the same float the server rounds');
  assert.equal(calculateTrapCos(100, 130), '80');
  assert.equal(calculateTrapCos(100, 314), '100', 'exceeds every threshold, including the last');
  // String inputs (form values) behave like numbers.
  assert.equal(calculateTrapCos('100', '130'), '80');
});

test('cos-rules: calculateTrapCos returns null for missing, non-numeric or non-positive inputs', function () {
  // Mirror of test_trap_cos_none_for_missing_non_numeric_or_non_positive_inputs.
  assert.equal(calculateTrapCos('', ''), null, 'nothing entered');
  assert.equal(calculateTrapCos('120', ''), null, 'partial input');
  assert.equal(calculateTrapCos('', '250'), null, 'partial input');
  assert.equal(calculateTrapCos('abc', '250'), null, 'non-numeric a');
  assert.equal(calculateTrapCos('120', 'abc'), null, 'non-numeric b');
  assert.equal(calculateTrapCos('0', '250'), null, 'a <= 0');
  assert.equal(calculateTrapCos('-5', '250'), null, 'a <= 0');
  assert.equal(calculateTrapCos('120', '0'), null, 'b <= 0');
  assert.equal(calculateTrapCos('120', '-5'), null, 'b <= 0');
});

test('cos-rules: numeric coercion matches helpers.to_float_or_none for commas and decimal syntax', function () {
  assert.equal(calculateTrapCos('1,000', '1,300'), '80',
    'the server strips commas before float conversion');
  assert.equal(calculateTrapCos('1e2', '1.3e2'), '80',
    'Python and the client both accept decimal exponent notation');
  assert.equal(calculateTrapCos('0x64', '130'), null,
    'JavaScript-only hex syntax must not compute when Python float() would return None');
  assert.equal(calculateTrapCos('1e309', '1e309'), null,
    'overflowing exponent notation is not a finite calculator input');
});

// --- Seal: the two-branch formula -------------------------------------------

test('cos-rules: Seal activity > 0.9 ignores dip/azimuth/fault (server pin: 48)', function () {
  assert.equal(calculateSealCos({
    seal_recent_activity_age: '0.95',
    seal_fracture_permeability: '0.5',
    // The directional terms may even be absent on this branch.
    seal_dip: '', seal_azimuth_vs_shmax: '', seal_fault_level_confidence: ''
  }), '48', '0.95 x 0.5 -> 48%');
});

test('cos-rules: Seal activity <= 0.9 averages dip/azimuth/fault, times permeability', function () {
  // Mirror of test_seal_cos_activity_exactly_point_nine_uses_average_branch:
  // activity 0.9 is NOT "recently active", so the average branch runs.
  assert.equal(calculateSealCos({
    seal_recent_activity_age: '0.9',
    seal_dip: '0.3', seal_azimuth_vs_shmax: '0.6', seal_fault_level_confidence: '0.9',
    seal_fracture_permeability: '0.5'
  }), '30', 'mean(0.3, 0.6, 0.9) x 0.5 -> 30%');
});

test('cos-rules: a completely blank Seal form yields "" (clearing the inputs clears the result)', function () {
  assert.equal(calculateSealCos({}), '');
  assert.equal(calculateSealCos(null), '');
  assert.equal(calculateSealCos({
    seal_recent_activity_age: '', seal_dip: ' ', seal_fracture_permeability: ''
  }), '');
});

test('cos-rules: a PARTIAL Seal form yields null -- not computable, leave the field', function () {
  // Where cos.py raises a field-specific ValueError (a save must be refused
  // whole), the live mirror reports "cannot compute yet" so typing the first
  // input never wipes the CoS field.
  assert.equal(calculateSealCos({ seal_recent_activity_age: '0.95' }), null, 'no permeability yet');
  assert.equal(calculateSealCos({ seal_dip: '0.3' }), null, 'no activity yet');
  assert.equal(calculateSealCos({
    seal_recent_activity_age: '0.5', seal_fracture_permeability: '0.5',
    seal_dip: '0.3', seal_azimuth_vs_shmax: '0.6'
    // fault confidence missing on the average branch
  }), null);
});

test('cos-rules: non-finite Seal inputs are not computable', function () {
  assert.equal(calculateSealCos({
    seal_recent_activity_age: '1e309', seal_fracture_permeability: '0.5'
  }), null);
  assert.equal(calculateSealCos({
    seal_recent_activity_age: '0.95', seal_fracture_permeability: '1e309'
  }), null);
});

test('cos-rules: the out-of-domain product the server refuses is still COMPUTED here', function () {
  // The audit's KI-004 repro inputs: 1.33 x 0.87 -> 116%. The mirror computes
  // it faithfully; the 0-100 refusal is validation's job (validateStepFields
  // client-side, the lifecycle range guards server-side), not the formula's.
  assert.equal(calculateSealCos({
    seal_recent_activity_age: '1.33', seal_fracture_permeability: '0.87',
    seal_dip: '0.23', seal_azimuth_vs_shmax: '0.52', seal_fault_level_confidence: '0.59'
  }), '116');
});
