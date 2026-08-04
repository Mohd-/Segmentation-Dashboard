// Client-side mirror of cos.py's Trap / Seal CoS formulas -- pure math, no DOM,
// no Store, no fetch. The SERVER (cos.calculate_trap_cos / calculate_seal_cos)
// remains the authoritative home of the formulas; this module exists so the
// "Trap and Seal CoS" step can compute LIVE as inputs change instead of only on
// save. Keep the two in lockstep: any change to cos.py's tables or branches
// must land here in the same commit.
//
// Result contract (mirrors cos.py, with one client-shaped difference):
// - a whole-number percentage STRING (e.g. "80") when the formula computes;
// - '' when the Seal form is COMPLETELY blank (cos.calculate_seal_cos's
//   blank-form rule -- clearing every input clears the stored result);
// - null meaning "not computable" -- an input is missing or non-numeric. Where
//   cos.py RAISES a field-specific ValueError for a partial Seal form (a save
//   must be refused whole), a live keystroke-by-keystroke recompute cannot
//   throw mid-typing, so the caller simply leaves the CoS field untouched
//   until the inputs are complete. Trap's null matches cos.py's None verbatim.

// Threshold table copied from cos.py (_TRAP_COS_FACTORS / _TRAP_COS_SCORES):
// for a = Sarah prognosis thickness and b = Sarah-Quwarah thickness, keep the
// score of the LARGEST factor for which a + a*factor < b (strictly less-than);
// 0.5 is the floor when no threshold is exceeded (b <= a).
export var TRAP_COS_FACTORS = [0.0, 0.036, 0.108, 0.242, 0.405, 0.554, 0.882, 2.13];
export var TRAP_COS_SCORES = [0.7, 0.725, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0];

// helpers.to_float_or_none's client twin: remove commas, then accept Python
// float()'s ordinary decimal/exponent spellings; blank/non-numeric -> null.
// Do not use Number(text) alone: it rejects a comma-formatted saved value that
// the server accepts ("1,000"), while accepting JS-only literals such as
// "0x10" that Python float() rejects.
function toNumberOrNull(value) {
  if (value == null) return null;
  var text = String(value).replace(/,/g, '').trim();
  if (!text || text === '-') return null;
  if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(text)) return null;
  var numeric = Number(text);
  return isFinite(numeric) ? numeric : null;
}

// Python's int(round(x)) rounds half to EVEN (banker's rounding), JS's
// Math.round rounds half UP -- and both sides share IEEE-754 doubles, so the
// only way the two mirrors could ever disagree is on an exact .5 product.
// Half-even here keeps the client's live value identical to what the server
// would have computed for the same inputs.
function roundHalfEven(value) {
  var floor = Math.floor(value);
  var diff = value - floor;
  if (diff > 0.5) return floor + 1;
  if (diff < 0.5) return floor;
  return floor % 2 === 0 ? floor : floor + 1;
}

// Mirror of cos.calculate_trap_cos(sarah_thickness_ft, sarah_quwarah_thickness_ft).
// `sarahThicknessFt` (a) is Thickness Estimation's formation_thickness_ft,
// fetched cross-task by the caller; `sarahQuwarahThicknessFt` (b) is the step's
// own input. Returns "50".."100" or null (missing/non-numeric/<= 0 inputs).
export function calculateTrapCos(sarahThicknessFt, sarahQuwarahThicknessFt) {
  var sarah = toNumberOrNull(sarahThicknessFt);
  var sarahQuwarah = toNumberOrNull(sarahQuwarahThicknessFt);
  if (sarah === null || sarahQuwarah === null) return null;
  if (sarah <= 0 || sarahQuwarah <= 0) return null;
  var result = 0.5;
  for (var i = 0; i < TRAP_COS_FACTORS.length; i += 1) {
    if (sarah + sarah * TRAP_COS_FACTORS[i] < sarahQuwarah) result = TRAP_COS_SCORES[i];
  }
  return String(roundHalfEven(result * 100));
}

// Mirror of cos.calculate_seal_cos(fields). Rule (verbatim from cos.py):
// - activity > 0.9: activity x fracture permeability;
// - activity <= 0.9: average(dip, azimuth vs. SHmax, fault confidence)
//   x fracture permeability.
// A completely blank form returns '' (so clearing the form clears the result);
// a PARTIAL form returns null (cos.py raises there -- see the module comment).
export function calculateSealCos(fields) {
  var values = fields || {};
  var inputs = [
    values.seal_recent_activity_age,
    values.seal_dip,
    values.seal_azimuth_vs_shmax,
    values.seal_fault_level_confidence,
    values.seal_fracture_permeability
  ];
  var anyFilled = inputs.some(function (value) {
    return String(value == null ? '' : value).trim() !== '';
  });
  if (!anyFilled) return '';
  var activity = toNumberOrNull(values.seal_recent_activity_age);
  var fracturePermeability = toNumberOrNull(values.seal_fracture_permeability);
  if (activity === null || fracturePermeability === null) return null;
  var sealCos;
  if (activity > 0.9) {
    sealCos = activity * fracturePermeability;
  } else {
    var dip = toNumberOrNull(values.seal_dip);
    var azimuth = toNumberOrNull(values.seal_azimuth_vs_shmax);
    var faultConfidence = toNumberOrNull(values.seal_fault_level_confidence);
    if (dip === null || azimuth === null || faultConfidence === null) return null;
    sealCos = ((dip + azimuth + faultConfidence) / 3.0) * fracturePermeability;
  }
  return String(roundHalfEven(sealCos * 100));
}
