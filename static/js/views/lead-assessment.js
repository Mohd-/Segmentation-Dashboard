/* Card 2B -- the CONSOLIDATED LEAD ASSESSMENT workspace.
 *
 * The Lead Assessment stage has ONE workflow task and four derived checkpoints
 * (Area Definition, Thickness Estimation, GRV Inputs, Resource Assessment).
 * The board still shows four checkpoint dots and x/4 progress, while clicking
 * the single stage row opens this page, whose four numbered sections are laid
 * out the way a geologist actually
 * fills them -- thickness and volume side by side, structure across the middle,
 * volumetrics computing themselves underneath.
 *
 * WHERE IT MOUNTS. Into the shared detail shell's centre column, replacing the
 * generic per-step form's #dynamic-fields body (detail-form.js's
 * renderLeadAssessmentSection). Everything else in that shell is REUSED rather
 * than rebuilt: the comments textarea, the folder card slot, and the action
 * row's Save Updates button are the shell's own nodes. Every other stage keeps
 * the generic form untouched.
 *
 * THE ACTIVE-STEP HIGHLIGHT. Deliberately the SIMPLE option: the rail keeps
 * highlighting whichever of the four rows the user clicked (loadComponent's
 * existing .component-item.active sweep), and the stage header carries its
 * usual is-active accent. Nothing tracks "which section am I scrolled to" --
 * that would be a scroll-spy whose only job is to fight the click the user just
 * made, and the four rows are visibly one page either way.
 *
 * WHAT IS PURE. Every rule -- validation, the TWT<->thickness conversion, the
 * GRV-vs-box-model method precedence, which scenario shows a Liquid block, how
 * a page's values group back onto their owning tasks -- is a pure exported
 * function tested without a DOM (static/tests/test-lead-assessment.js). The
 * render/wire half below is the only part that touches Store or the network.
 */
import { byId, all, esc, isFilled, truthy, msg } from '../dom.js';
import { API } from '../api.js';
import { Store, currentUserName, isCurrentPipelineView } from '../state.js';
import { RESOURCE_SCENARIOS, FORMATIONS } from '../schema.js';
import {
  buildCalculatePayload, buildLeadApplyFields, buildPlotMarkup,
  resultsFromStoredFields, resultsFromCalculation, openPlotLightbox
} from './resource-calculator.js';
import { refreshAfterRecordChange, renderDetail } from './detail.js';

// ---------------------------------------------------------------------------
// The contract: steps, keys, and which task owns each key
// ---------------------------------------------------------------------------

// One current workflow task. The legacy names remain accepted by the page
// claim so a pre-v7 payload can still be opened safely during a rolling deploy.
export var LEAD_ASSESSMENT_STEPS = ['Lead Assessment'];
export var LEGACY_LEAD_ASSESSMENT_STEPS = [
  'Area Definition', 'Thickness Estimation', 'GRV Inputs', 'Resource Assessment'
];

// The step whose task carries the page's ONE comments box and its PIIP results
// (see the comments note on buildSavePlan below).
export var PRIMARY_STEP = 'Lead Assessment';

// Every key now belongs to the single merged task. Keys are the EAV contract --
// never rename one, because a rename orphans stored data.
export var KEY_OWNER = {
  // Section 1 -- Thickness Estimation. The two *_thickness_ft keys are the
  // pre-existing canonical reads (Lead Summary, portfolio, the box model);
  // the twt_* pair and the source marker are new alongside them.
  twt_reservoir_ms: PRIMARY_STEP,
  twt_formation_ms: PRIMARY_STEP,
  reservoir_thickness_ft: PRIMARY_STEP,
  formation_thickness_ft: PRIMARY_STEP,
  thickness_source_mode: PRIMARY_STEP,
  // Section 2 left -- Area Definition (existing keys, reused verbatim).
  p90_area_km2: PRIMARY_STEP,
  p10_area_km2: PRIMARY_STEP,
  // Section 3's TVDSS rides on Area Definition: it is a structural reading of
  // the same mapped surface the areas come off, and it must NOT gate that
  // step's completion (the server's FIELD_COMPLETION entry omits it).
  top_formation_tvdss_ft: PRIMARY_STEP,
  // Section 2 right -- GRV Inputs (new keys).
  grv_p90_thousand_acre_ft: PRIMARY_STEP,
  grv_p10_thousand_acre_ft: PRIMARY_STEP,
  // Section 3's confirmation lives on Resource Assessment because it gates THAT
  // item's completion, not Area Definition's.
  polygons_surfaces_loaded: PRIMARY_STEP
};

// Read-only rolling-deploy fallback. Migration v7 moves these EAV rows onto
// Lead Assessment, but a frontend served just before migration completes must
// still hydrate the page without losing values.
var LEGACY_KEY_OWNER = {
  twt_reservoir_ms: 'Thickness Estimation', twt_formation_ms: 'Thickness Estimation',
  reservoir_thickness_ft: 'Thickness Estimation', formation_thickness_ft: 'Thickness Estimation',
  thickness_source_mode: 'Thickness Estimation', p90_area_km2: 'Area Definition',
  p10_area_km2: 'Area Definition', top_formation_tvdss_ft: 'Area Definition',
  grv_p90_thousand_acre_ft: 'GRV Inputs', grv_p10_thousand_acre_ft: 'GRV Inputs',
  polygons_surfaces_loaded: 'Resource Assessment'
};

// The folder section key card 2B's folder row resolves
// (config.WELL_OVERVIEW_DIRECTORY_MAP / folders.LEAD_COMPONENT_SECTION_KEYS):
// <leads share>\<field>\<lead>\Polygons__Surfaces.
export var FOLDER_SECTION_KEY = 'polygons';

// The scenario a page with nothing stored opens on -- same default the
// standalone calculator uses.
export var DEFAULT_SCENARIO = 'dry_gas_high_pressure';

// ---------------------------------------------------------------------------
// Message strings
// ---------------------------------------------------------------------------
// Named constants, not inline literals: these are the words the card specifies,
// the words the tests pin, and the words a user reads -- one definition each.

export var MESSAGES = {
  // A value that is present but not a usable magnitude. Every input on this
  // page except the TVDSS is a physical quantity that cannot be zero or less.
  number: function (label) { return label + ' must be a number greater than 0.'; },
  // The TVDSS is the one exception: numeric parse only, sign and magnitude free
  // (a subsea depth is legitimately negative on some datums).
  tvdss: 'Top Formation TVDSS must be numeric.',
  // Ordering. Rejected on EQUALITY too, in both directions -- a pair whose two
  // sides are the same number is a mis-entry, not a degenerate distribution --
  // and never silently swapped: swapping would quietly rewrite what the user
  // measured.
  twtOrder: 'Formation TWT (ms) must be greater than Reservoir TWT (ms).',
  thicknessOrder: 'Formation Thickness (ft) must be greater than Reservoir Thickness (ft).',
  areaOrder: 'Area P10 must be greater than Area P90.',
  grvOrder: 'GRV P10 must be greater than GRV P90.',
  // The one-source rule (only when a row has configured coefficients): the
  // derived side is readonly, and trying to type into it says why.
  secondSource: function (sourceLabel, derivedLabel) {
    return 'Clear ' + sourceLabel + ' before entering ' + derivedLabel + '.';
  },
  // Section 1's quiet note while config.TWT_THICKNESS_COEFFICIENTS has no entry
  // for a row -- the mode in which both columns are plain manual inputs.
  conversionPending: 'TWT ⇄ thickness conversion pending configuration',
  // Section 4's method precedence, surfaced rather than silently resolved.
  grvPartial: 'Enter both GRV P90 and GRV P10, or clear both to use Area × Thickness.',
  piipIdle: 'Enter a GRV pair, or an Area pair with a Reservoir Thickness, to calculate PIIP results.',
  piipRunning: 'Calculating…'
};

export var HELPER_TEXT =
  'PIIP results and plots update automatically when valid inputs or the selected scenario change.';
export var PIIP_HEADING = 'Petroleum Initially In Place - PIIP Results';
export var POLYGONS_LABEL = 'Polygons and surfaces are placed in the shared folder';

// Input labels, by key. Used by the validators (so an error names the field the
// user is looking at) and by the aria-labels.
export var LABELS = {
  twt_reservoir_ms: 'Reservoir TWT (ms)',
  twt_formation_ms: 'Formation TWT (ms)',
  reservoir_thickness_ft: 'Reservoir Thickness (ft)',
  formation_thickness_ft: 'Formation Thickness (ft)',
  p90_area_km2: 'Area P90 (km²)',
  p10_area_km2: 'Area P10 (km²)',
  grv_p90_thousand_acre_ft: 'GRV P90 (10³ acre.ft)',
  grv_p10_thousand_acre_ft: 'GRV P10 (10³ acre.ft)',
  top_formation_tvdss_ft: 'Top Formation TVDSS (ft)'
};

// ---------------------------------------------------------------------------
// Pure: validation
// ---------------------------------------------------------------------------

// A blank value is NEVER an error here -- it is an INCOMPLETE section, which
// the server's FIELD_COMPLETION already expresses by leaving the item open.
// Only a value the user actually typed is checked, exactly like the generic
// step forms' validateStepFields.
export function numberError(key, raw) {
  if (!isFilled(raw)) return null;
  var value = Number(raw);
  if (isNaN(value) || value <= 0) return MESSAGES.number(LABELS[key] || key);
  return null;
}

// Section 3's TVDSS: numeric parse only. No positivity rule (see MESSAGES),
// no completion effect.
export function tvdssError(raw) {
  if (!isFilled(raw)) return null;
  return isNaN(Number(raw)) ? MESSAGES.tvdss : null;
}

// Both sides filled, both individually valid, and hi strictly greater than lo?
// Returns the message on the HI key (where the user fixes it) or null.
function orderError(values, hiKey, loKey, message) {
  if (!isFilled(values[hiKey]) || !isFilled(values[loKey])) return null;
  if (numberError(hiKey, values[hiKey]) || numberError(loKey, values[loKey])) return null;
  return Number(values[hiKey]) > Number(values[loKey]) ? null : message;
}

// Section 1 -> { fieldKey: message }. Both rows' four inputs plus the two
// ordering rules (times and thicknesses each ordered within their own column,
// because the two columns are two measurements of the same pair of surfaces).
export function validateThicknessSection(values) {
  values = values || {};
  var errors = {};
  ['twt_reservoir_ms', 'twt_formation_ms', 'reservoir_thickness_ft', 'formation_thickness_ft']
    .forEach(function (key) {
      var error = numberError(key, values[key]);
      if (error) errors[key] = error;
    });
  var twt = orderError(values, 'twt_formation_ms', 'twt_reservoir_ms', MESSAGES.twtOrder);
  if (twt && !errors.twt_formation_ms) errors.twt_formation_ms = twt;
  var thickness = orderError(values, 'formation_thickness_ft', 'reservoir_thickness_ft',
                             MESSAGES.thicknessOrder);
  if (thickness && !errors.formation_thickness_ft) errors.formation_thickness_ft = thickness;
  return errors;
}

// Section 2 -> { fieldKey: message }. Two P90/P10 pairs, same rule twice.
export function validateVolumeSection(values) {
  values = values || {};
  var errors = {};
  ['p90_area_km2', 'p10_area_km2', 'grv_p90_thousand_acre_ft', 'grv_p10_thousand_acre_ft']
    .forEach(function (key) {
      var error = numberError(key, values[key]);
      if (error) errors[key] = error;
    });
  var area = orderError(values, 'p10_area_km2', 'p90_area_km2', MESSAGES.areaOrder);
  if (area && !errors.p10_area_km2) errors.p10_area_km2 = area;
  var grv = orderError(values, 'grv_p10_thousand_acre_ft', 'grv_p90_thousand_acre_ft',
                       MESSAGES.grvOrder);
  if (grv && !errors.grv_p10_thousand_acre_ft) errors.grv_p10_thousand_acre_ft = grv;
  return errors;
}

// The whole page's errors in one map. Section 3's checkbox has no rule of its
// own; its TVDSS twin has the loosest one on the page.
export function validateLeadAssessment(values) {
  values = values || {};
  var errors = Object.assign({}, validateThicknessSection(values), validateVolumeSection(values));
  var tvdss = tvdssError(values.top_formation_tvdss_ft);
  if (tvdss) errors.top_formation_tvdss_ft = tvdss;
  return errors;
}

// The first error in a stable, reading order -- what a blocked Save toasts.
var ERROR_ORDER = [
  'twt_reservoir_ms', 'twt_formation_ms', 'reservoir_thickness_ft', 'formation_thickness_ft',
  'p90_area_km2', 'p10_area_km2', 'grv_p90_thousand_acre_ft', 'grv_p10_thousand_acre_ft',
  'top_formation_tvdss_ft'
];
export function firstError(errors) {
  errors = errors || {};
  for (var i = 0; i < ERROR_ORDER.length; i += 1) {
    if (errors[ERROR_ORDER[i]]) return errors[ERROR_ORDER[i]];
  }
  return null;
}

// ---------------------------------------------------------------------------
// Pure: the TWT <-> thickness conversion
// ---------------------------------------------------------------------------
// y = m*x + b, per ROW, with the coefficients served by GET /api/meta from
// config.TWT_THICKNESS_COEFFICIENTS (which SHIPS EMPTY -- see that constant).
// A row with no usable entry stays in MANUAL mode: two plain inputs, no
// derivation, no one-source rule, and the section shows the pending note.

export var CONVERSION_ROWS = ['reservoir', 'formation'];

// The TWT and thickness key of one row, so the row logic never spells a key.
export var ROW_KEYS = {
  reservoir: { twt: 'twt_reservoir_ms', thickness: 'reservoir_thickness_ft', label: 'Reservoir' },
  formation: { twt: 'twt_formation_ms', thickness: 'formation_thickness_ft', label: 'Formation' }
};

// `meta` is Store.meta (or any stand-in) -- passed in rather than read, so the
// whole conversion is testable without touching global state. A row is only
// convertible when BOTH coefficients parse finitely AND m is non-zero (m = 0 is
// a constant thickness, which has no inverse and is not a calibration).
export function coefficientsFor(meta, row) {
  var map = (meta && meta.twt_thickness_coefficients) || {};
  var entry = map[row];
  if (!entry) return null;
  var m = Number(entry.m);
  var b = Number(entry.b);
  if (!isFinite(m) || !isFinite(b) || m === 0) return null;
  return { m: m, b: b };
}

// Is ANY row convertible? Decides whether the section runs in conversion mode
// at all (and therefore whether the pending note shows).
export function conversionConfigured(meta) {
  return CONVERSION_ROWS.some(function (row) { return !!coefficientsFor(meta, row); });
}

// Two decimals, trailing zeros trimmed ('210.00' -> '210'), so a derived cell
// reads like something a person typed. '' for anything non-finite.
export function roundForDisplay(value) {
  var number = Number(value);
  if (!isFinite(number)) return '';
  return String(Math.round(number * 100) / 100);
}

export function thicknessFromTwt(coefficients, twt) {
  if (!coefficients || !isFilled(twt) || isNaN(Number(twt))) return '';
  return roundForDisplay(coefficients.m * Number(twt) + coefficients.b);
}

export function twtFromThickness(coefficients, thickness) {
  if (!coefficients || !isFilled(thickness) || isNaN(Number(thickness))) return '';
  return roundForDisplay((Number(thickness) - coefficients.b) / coefficients.m);
}

// Apply the section's source mode to a whole values object: every CONVERTIBLE
// row's derived side is recomputed from its source side. Rows without
// coefficients are left exactly as the user typed them, in both columns.
// `mode` is 'twt' | 'thickness' | '' (''-> nothing is derived).
export function applyConversion(meta, values, mode) {
  var out = Object.assign({}, values || {});
  if (mode !== 'twt' && mode !== 'thickness') return out;
  CONVERSION_ROWS.forEach(function (row) {
    var coefficients = coefficientsFor(meta, row);
    if (!coefficients) return;
    var keys = ROW_KEYS[row];
    if (mode === 'twt') {
      out[keys.thickness] = thicknessFromTwt(coefficients, out[keys.twt]);
    } else {
      out[keys.twt] = twtFromThickness(coefficients, out[keys.thickness]);
    }
  });
  return out;
}

// Which column is the SOURCE, given what is stored. The marker wins when it is
// one of the two real values; otherwise it is inferred from which column
// actually carries data (a lead captured before this page existed has
// thicknesses and no times), and an empty section stays unset so the user's
// first keystroke chooses.
export function resolveSourceMode(values, marker) {
  if (marker === 'twt' || marker === 'thickness') return marker;
  values = values || {};
  var hasTwt = CONVERSION_ROWS.some(function (row) { return isFilled(values[ROW_KEYS[row].twt]); });
  var hasThickness = CONVERSION_ROWS.some(function (row) {
    return isFilled(values[ROW_KEYS[row].thickness]);
  });
  if (hasTwt && !hasThickness) return 'twt';
  if (hasThickness && !hasTwt) return 'thickness';
  return '';
}

// Is a given cell the DERIVED (readonly) one right now? Only ever true for a
// convertible row inside a section whose source is decided.
export function isDerivedCell(meta, row, column, mode) {
  if (mode !== 'twt' && mode !== 'thickness') return false;
  if (!coefficientsFor(meta, row)) return false;
  return column !== mode;
}

// ---------------------------------------------------------------------------
// Pure: Section 4's method precedence
// ---------------------------------------------------------------------------
// DETERMINISTIC, and it never falls back silently. The user's GRV pair is the
// lead's own measured volume; the box model is what you compute when there
// isn't one. A HALF-entered GRV pair is neither -- it is a form in progress --
// so it reports itself rather than quietly computing something else and
// letting the user believe the number came from the GRV they were typing.
//
// Returns one of:
//   { status: 'ready',   method, inputs }   -> run this
//   { status: 'error',   message }          -> show it, run nothing
//   { status: 'idle',    message }          -> not enough input yet
//
// NOTE (test placement): this precedence is CLIENT-SIDE by construction -- the
// server's /resource-assessment endpoint is told which method to run and has no
// view of the page's other sections -- so it is unit-tested here, not in pytest.
export function resolveCalculation(values) {
  values = values || {};
  var grvP90 = values.grv_p90_thousand_acre_ft;
  var grvP10 = values.grv_p10_thousand_acre_ft;
  var grvFilled = [grvP90, grvP10].filter(isFilled).length;
  if (grvFilled === 1) return { status: 'error', message: MESSAGES.grvPartial };
  if (grvFilled === 2) {
    var grvErrors = validateVolumeSection(values);
    var grvMessage = grvErrors.grv_p90_thousand_acre_ft || grvErrors.grv_p10_thousand_acre_ft;
    if (grvMessage) return { status: 'error', message: grvMessage };
    return {
      status: 'ready',
      method: 'GRV',
      inputs: { grvP90: String(grvP90), grvP10: String(grvP10) }
    };
  }
  // GRV entirely empty -> the box model, but only from a COMPLETE, valid trio.
  var areaP90 = values.p90_area_km2;
  var areaP10 = values.p10_area_km2;
  var thickness = values.reservoir_thickness_ft;
  if (![areaP90, areaP10, thickness].every(isFilled)) {
    return { status: 'idle', message: MESSAGES.piipIdle };
  }
  var errors = validateVolumeSection(values);
  var areaMessage = errors.p90_area_km2 || errors.p10_area_km2;
  if (areaMessage) return { status: 'error', message: areaMessage };
  var thicknessMessage = numberError('reservoir_thickness_ft', thickness);
  if (thicknessMessage) return { status: 'error', message: thicknessMessage };
  return {
    status: 'ready',
    method: 'Box Model',
    inputs: { areaP90: String(areaP90), areaP10: String(areaP10), thicknessP50: String(thickness) }
  };
}

// The request body for a resolved calculation. Delegates to the calculator's
// own buildCalculatePayload so the two pages can never send different shapes.
export function calculationPayload(resolved, scenario) {
  return buildCalculatePayload(Object.assign({ scenario: scenario, method: resolved.method },
                                             resolved.inputs));
}

// The signature an auto-run compares against the last one it ACTUALLY sent, so
// a keystroke that changes nothing the engine reads (a comment, the TVDSS, a
// re-typed identical number) costs no request.
export function calculationSignature(resolved, scenario) {
  if (!resolved || resolved.status !== 'ready') return '';
  return JSON.stringify([scenario, resolved.method, resolved.inputs]);
}

// ---------------------------------------------------------------------------
// Pure: scenarios
// ---------------------------------------------------------------------------

// GET /api/meta (Store.meta) is authoritative; schema.js is the boot fallback.
export function scenarioList(meta) {
  return (meta && meta.resource_scenarios) || RESOURCE_SCENARIOS;
}

// Liquid (MMSTB) renders for CONDENSATE scenarios only -- decided by the
// SELECTED scenario, so switching to a dry-gas scenario hides the block
// immediately rather than waiting for a run that will not produce condensate.
export function showsLiquid(scenarios, scenarioId) {
  var match = (scenarios || []).find(function (item) { return item.id === scenarioId; });
  return !!match && match.resource_type === 'condensate';
}

// ---------------------------------------------------------------------------
// Pure: the dynamic Section 3 label
// ---------------------------------------------------------------------------
// "Top <FORMATION> Formation TVDSS (ft)" when the lead names a primary
// formation, else the generic label. The primary formation is the lead's own
// interpretation: the canonical trio in order (SARH first -- the reservoir this
// workflow is built around) and then any custom formation the record carries.
// A lead with no formation rows at all -- the common case this early in the
// workflow -- gets the generic label rather than an invented one.
export function primaryFormationName(formations) {
  var rows = formations || [];
  var names = rows.map(function (row) {
    return String((row && row.formation) || '').trim().toUpperCase();
  }).filter(function (name) { return !!name; });
  var canonical = FORMATIONS.find(function (name) { return names.indexOf(name) >= 0; });
  return canonical || names[0] || '';
}

export function tvdssLabel(formations) {
  var name = primaryFormationName(formations);
  return name ? 'Top ' + name + ' Formation TVDSS (ft)' : 'Top Formation TVDSS (ft)';
}

// ---------------------------------------------------------------------------
// Pure: the batched save plan
// ---------------------------------------------------------------------------

// The page's values -> [{ taskName, fields }] for the tasks whose stored values
// actually CHANGED, in LEAD_ASSESSMENT_STEPS order. One PATCH per dirty task
// (each carries its own revision and optimistic lock), never a blanket
// four-task write: an untouched step must not collect a history entry, and must
// not 409 on a revision somebody else legitimately moved.
//
// `saved` is the {taskName: {key: value}} map of what the server currently
// holds (Store.allFields). A task appears in the plan when ANY of its keys
// differs, and then carries ALL of its keys -- a partial field payload is fine
// for the server (it upserts what it is given) but a whole-task payload is what
// makes the write idempotent and readable in the audit trail.
export function buildSavePlan(values, saved) {
  values = values || {};
  saved = saved || {};
  var byTask = {};
  Object.keys(KEY_OWNER).forEach(function (key) {
    var taskName = KEY_OWNER[key];
    if (!byTask[taskName]) byTask[taskName] = {};
    byTask[taskName][key] = values[key] == null ? '' : String(values[key]);
  });
  return LEAD_ASSESSMENT_STEPS.filter(function (taskName) {
    var fields = byTask[taskName] || {};
    var stored = saved[taskName] || {};
    return Object.keys(fields).some(function (key) {
      var storedValue = stored[key];
      if (storedValue == null) storedValue = (saved[LEGACY_KEY_OWNER[key]] || {})[key];
      return String(storedValue == null ? '' : storedValue) !== fields[key];
    });
  }).map(function (taskName) {
    return { taskName: taskName, fields: byTask[taskName] };
  });
}

// The three NON-primary steps' comments, non-empty ones only, in rail order.
// COMMENTS DECISION (stated, because the card asks): the four tasks keep their
// own comments columns -- provenance is not something a layout change gets to
// delete -- but the page surfaces ONE editable box, bound to the Resource
// Assessment task (the item the page's headline output belongs to). Whatever
// the other three already carry is shown READ-ONLY in a small fold, attributed
// to the step that recorded it, and only when there is something to show. So a
// legacy comment stays legible and stays attributed, and a new comment has one
// obvious home instead of four ambiguous ones.
export function earlierComments(tasks) {
  return (tasks || []).filter(function (task) {
    return task && task.task_name !== PRIMARY_STEP && task.task_name !== 'Resource Assessment' &&
      LEGACY_LEAD_ASSESSMENT_STEPS.indexOf(task.task_name) >= 0 && isFilled(task.comments);
  }).sort(function (a, b) {
    return LEGACY_LEAD_ASSESSMENT_STEPS.indexOf(a.task_name) - LEGACY_LEAD_ASSESSMENT_STEPS.indexOf(b.task_name);
  }).map(function (task) {
    return { step: task.task_name, comments: String(task.comments) };
  });
}

// ---------------------------------------------------------------------------
// Markup
// ---------------------------------------------------------------------------

function numberInput(key, value, options) {
  options = options || {};
  return '<input type="number" step="any" data-la-field="' + esc(key) + '"' +
    ' value="' + esc(value == null ? '' : value) + '"' +
    ' aria-label="' + esc(options.label || LABELS[key] || key) + '"' +
    (options.placeholder ? ' placeholder="' + esc(options.placeholder) + '"' : '') +
    (options.readonly ? ' readonly class="la-derived"' : '') + '>';
}

// Which fields report into which card's error strip, in stable reading order
// (the same order ERROR_ORDER walks). One strip per card, at the card's
// BOTTOM — below the grid, so a message can appear and wrap without moving a
// single input row (the owner's "consistent location below", not per-cell).
var SECTION_FIELDS = {
  thickness: ['twt_reservoir_ms', 'twt_formation_ms', 'reservoir_thickness_ft', 'formation_thickness_ft'],
  volume: ['p90_area_km2', 'p10_area_km2', 'grv_p90_thousand_acre_ft', 'grv_p10_thousand_acre_ft'],
  structure: ['top_formation_tvdss_ft']
};

// The card-bottom strip itself: hidden (and costing no height) while clean.
function errorStrip(section) {
  return '<p class="la-card-errors" data-la-errors="' + esc(section) + '" role="alert" hidden></p>';
}

// One cell of a twin card's value grid. The cell holds ONLY the input — error
// messages go to the card's strip, and .la-invalid on the input points at the
// offending field — so a message can never grow a cell and shift the twin
// grids out of line.
function cell(key, value, options) {
  return '<div class="la-cell">' + numberInput(key, value, options) + '</div>';
}

function cardHead(number, title) {
  return '<div class="la-card-head"><span class="la-num" aria-hidden="true">' + number + '</span>' +
    '<h3 class="la-card-title">' + esc(title) + '</h3></div>';
}

// Section 1. Rows Reservoir/Formation x columns TWT (ms)/Thickness (ft).
// `.la-grid` is the SHARED twin-geometry class -- Section 2 uses the identical
// grid so the two cards are the same size with the same column tracks and gaps,
// which is the whole point of the symmetric layout.
export function thicknessSectionMarkup(values, meta, mode) {
  var pending = !conversionConfigured(meta);
  var rows = CONVERSION_ROWS.map(function (row) {
    var keys = ROW_KEYS[row];
    return '<span class="la-row-head">' + esc(keys.label) + '</span>' +
      cell(keys.twt, values[keys.twt], { readonly: isDerivedCell(meta, row, 'twt', mode) }) +
      cell(keys.thickness, values[keys.thickness],
           { readonly: isDerivedCell(meta, row, 'thickness', mode) });
  }).join('');
  return '<section class="la-card la-card-twin" data-la-section="thickness">' +
    cardHead(1, 'Thickness Estimation') +
    '<div class="la-grid">' +
    '<span class="la-corner" aria-hidden="true"></span>' +
    '<span class="la-col-head">TWT (ms)</span>' +
    '<span class="la-col-head">Thickness (ft)</span>' + rows +
    '</div>' +
    (pending ? '<p class="la-note" data-la-note="conversion">' + esc(MESSAGES.conversionPending) + '</p>' : '') +
    errorStrip('thickness') +
    '</section>';
}

// Section 2. Rows Area/GRV x columns P90/P10 -- the SAME two-column,
// two-row `.la-grid` as Section 1, deliberately.
export function volumeSectionMarkup(values) {
  var rows = [
    { label: 'Area (km²)', p90: 'p90_area_km2', p10: 'p10_area_km2' },
    { label: 'GRV (10³ acre.ft)', p90: 'grv_p90_thousand_acre_ft', p10: 'grv_p10_thousand_acre_ft' }
  ].map(function (row) {
    return '<span class="la-row-head">' + row.label + '</span>' +
      cell(row.p90, values[row.p90]) + cell(row.p10, values[row.p10]);
  }).join('');
  return '<section class="la-card la-card-twin" data-la-section="volume">' +
    cardHead(2, 'Area and Volume Definition') +
    '<div class="la-grid">' +
    '<span class="la-corner" aria-hidden="true"></span>' +
    '<span class="la-col-head">P90</span>' +
    '<span class="la-col-head">P10</span>' + rows +
    '</div>' + errorStrip('volume') + '</section>';
}

// Section 3. The TVDSS input (label is the placeholder, as in the mockup --
// the field is self-describing and the row has no room for a caption) and the
// polygons confirmation, side by side across the full width.
export function structureSectionMarkup(values, formations) {
  var label = tvdssLabel(formations);
  return '<section class="la-card la-card-wide" data-la-section="structure">' +
    '<div class="la-wide-row">' +
    '<span class="la-num" aria-hidden="true">3</span>' +
    '<div class="la-tvdss">' +
    numberInput('top_formation_tvdss_ft', values.top_formation_tvdss_ft,
                { label: label, placeholder: label }) +
    '</div>' +
    '<label class="check-label la-polygons"><input type="checkbox" data-la-field="polygons_surfaces_loaded"' +
    (truthy(values.polygons_surfaces_loaded) ? ' checked' : '') + '> ' + esc(POLYGONS_LABEL) + '</label>' +
    '</div>' +
    errorStrip('structure') +
    '</section>';
}

// One PIIP figure: label OUTSIDE the tinted box (card 2B is explicit about it).
function resultBox(label, value) {
  return '<div class="la-result-col"><span class="la-result-label">' + esc(label) + '</span>' +
    '<span class="la-result-box">' + (isFilled(value) ? esc(value) : '—') + '</span></div>';
}

// One resource block: heading, the P90/Mean/P10 trio, and its plot slot.
// `tone` is the block's colour family ('gas' -> light red, 'liquid' -> light
// green, per the card).
function resultBlock(tone, heading, trio, plot) {
  trio = trio || {};
  return '<div class="la-result-block la-result-' + tone + '" data-la-result="' + tone + '">' +
    '<div class="la-result-heading">' + esc(heading) + '</div>' +
    '<div class="la-result-cols">' +
    resultBox('P90', trio.p90) + resultBox('Mean', trio.mean) + resultBox('P10', trio.p10) +
    '</div>' +
    '<div class="la-plot-slot" data-la-plot="' + tone + '">' + (plot || '') + '</div></div>';
}

// Section 4. Heading + helper text + scenario radios + the result blocks.
// There is NO Calculate button and NO Apply button: the card removes both, and
// the auto-run replaces them (see scheduleCalculation).
export function piipSectionMarkup(state) {
  var scenarios = scenarioList(state.meta);
  var radios = scenarios.map(function (scenario) {
    return '<label class="radio-option la-scenario"><input type="radio" name="la-scenario" value="' +
      esc(scenario.id) + '"' + (scenario.id === state.scenario ? ' checked' : '') + '> ' +
      esc(scenario.label) + '</label>';
  }).join('');
  var liquid = showsLiquid(scenarios, state.scenario);
  return '<section class="la-card la-card-piip" data-la-section="piip">' +
    '<div class="la-card-head">' +
    '<span class="la-num" aria-hidden="true">4</span>' +
    '<h3 class="la-card-title">' + esc(PIIP_HEADING) + '</h3>' +
    '<p class="la-helper">' + esc(HELPER_TEXT) + '</p></div>' +
    '<div class="radio-options la-scenarios" role="radiogroup" aria-label="Scenario">' + radios + '</div>' +
    '<p class="la-piip-status" id="la-piip-status" role="status"></p>' +
    '<div class="la-results' + (liquid ? '' : ' la-results-gas-only') + '" id="la-results">' +
    resultBlock('gas', 'Gas (BCF)', state.display.gas, state.plots.gas) +
    (liquid ? resultBlock('liquid', 'Liquid (MMSTB)', state.display.liquid, state.plots.liquid) : '') +
    '</div></section>';
}

// The read-only provenance fold (see earlierComments). Rendered only when the
// other three steps actually carry comments -- an always-present empty fold
// would be furniture.
export function earlierCommentsMarkup(entries) {
  if (!entries || !entries.length) return '';
  return '<details class="la-earlier"><summary>Earlier step comments</summary>' +
    entries.map(function (entry) {
      return '<div class="la-earlier-entry"><b>' + esc(entry.step) + '</b>' +
        '<p>' + esc(entry.comments) + '</p></div>';
    }).join('') + '</details>';
}

export function workspaceMarkup(state) {
  return '<div class="la-workspace">' +
    '<div class="la-twins">' +
    thicknessSectionMarkup(state.values, state.meta, state.sourceMode) +
    volumeSectionMarkup(state.values) +
    '</div>' +
    structureSectionMarkup(state.values, state.formations) +
    piipSectionMarkup(state) +
    earlierCommentsMarkup(state.earlier) +
    '</div>';
}

// ---------------------------------------------------------------------------
// Render generation + wiring
// ---------------------------------------------------------------------------
// Same guard idiom as views/resource-calculator.js: async handlers capture
// their own `state` and compare it back against this module-level one before
// touching the DOM, so a debounced calculation that resolves after the user has
// navigated to another step (or another lead) is a safe no-op.

var state = null;
var debounceTimer = null;

export function teardownLeadAssessment() {
  if (debounceTimer) { clearTimeout(debounceTimer); debounceTimer = null; }
  state = null;
}

export function isLeadAssessmentStep(taskName) {
  return LEAD_ASSESSMENT_STEPS.indexOf(taskName) >= 0 ||
    LEGACY_LEAD_ASSESSMENT_STEPS.indexOf(taskName) >= 0;
}

// Is the consolidated page the thing currently mounted? detail-form.js's save
// handler asks before doing anything else.
export function leadAssessmentActive() {
  return !!state;
}

function taskNamed(name) {
  var exact = (Store.tasks || []).find(function (task) { return task.task_name === name; });
  if (exact || name !== PRIMARY_STEP) return exact || null;
  // Rolling-deploy fallback: prefer the old output-owning row if v7 has not
  // run yet, then any legacy row that can at least keep the workspace usable.
  return (Store.tasks || []).find(function (task) { return task.task_name === 'Resource Assessment'; }) ||
    (Store.tasks || []).find(function (task) {
      return LEGACY_LEAD_ASSESSMENT_STEPS.indexOf(task.task_name) >= 0;
    }) || null;
}

// Every stored value the page edits, resolved from the per-task field map.
function readStoredValues(allFields) {
  var values = {};
  Object.keys(KEY_OWNER).forEach(function (key) {
    var stored = (allFields[KEY_OWNER[key]] || {})[key];
    if (stored == null) stored = (allFields[LEGACY_KEY_OWNER[key]] || {})[key];
    values[key] = stored == null ? '' : String(stored);
  });
  return values;
}

// The live values as typed. Checkboxes normalize to the same '1'/'' the rest of
// the app stores.
export function readFormValues(root) {
  var values = {};
  all('[data-la-field]', root || document).forEach(function (element) {
    var key = element.getAttribute('data-la-field');
    values[key] = element.type === 'checkbox' ? (element.checked ? '1' : '') : element.value;
  });
  return values;
}

// Messages go to each card's ONE bottom strip, grouped by section in stable
// field order; the red border (.la-invalid) on the input is what points at the
// offending field. The strip sits BELOW the card's grid, so showing or growing
// it never moves an input row — the twin grids stay aligned.
function renderErrors(errors) {
  errors = errors || {};
  all('.la-card-errors').forEach(function (strip) {
    var keys = SECTION_FIELDS[strip.getAttribute('data-la-errors')] || [];
    var messages = keys.map(function (key) { return errors[key] || ''; })
      .filter(function (message) { return !!message; });
    strip.textContent = messages.join(' ');
    strip.hidden = !messages.length;
  });
  Object.keys(SECTION_FIELDS).forEach(function (section) {
    SECTION_FIELDS[section].forEach(function (key) {
      var input = document.querySelector('[data-la-field="' + key + '"]');
      if (input) input.classList.toggle('la-invalid', !!errors[key]);
    });
  });
}

function setStatus(message, tone) {
  var element = byId('la-piip-status');
  if (!element) return;
  element.textContent = message || '';
  element.className = 'la-piip-status' + (message ? ' is-shown' : '') + (tone ? ' la-status-' + tone : '');
}

// Re-render Section 4's result blocks in place (scenario switches and finished
// runs both land here). Everything else on the page keeps its DOM, and with it
// the user's cursor.
function renderResults() {
  var container = byId('la-results');
  if (!container || !state) return;
  var liquid = showsLiquid(scenarioList(state.meta), state.scenario);
  container.className = 'la-results' + (liquid ? '' : ' la-results-gas-only');
  container.innerHTML =
    resultBlock('gas', 'Gas (BCF)', state.display.gas, state.plots.gas) +
    (liquid ? resultBlock('liquid', 'Liquid (MMSTB)', state.display.liquid, state.plots.liquid) : '');
}

// ---------------------------------------------------------------------------
// The auto-run
// ---------------------------------------------------------------------------

export var DEBOUNCE_MS = 600;

// THE INTERACTION GATE (KI-005). The auto-run PERSISTS -- it POSTs
// /resource-assessment and then PATCHes the lead_piip_* keys onto the Resource
// Assessment task -- so it may only ever fire in response to something the USER
// DID on this page. Card 2B's contract is "PIIP results and plots update
// automatically when valid inputs or the SELECTED SCENARIO CHANGE"; MOUNTING is
// neither. Firing on mount made merely CLICKING A LEAD CARD rewrite its stored
// assessment (the board's Total Mean OGIP tile moved because somebody looked at
// a lead) and, through the server's post-save field-completion engine, reopen an
// Approved step that predates the confirmations -- a write the user never made.
//
// Exactly two things set this flag, both of them DOM event handlers:
// onFieldInput (input/change on a Section 1/2 field) and the scenario radio's
// change listener. Hydration cannot: renderLeadAssessment bakes stored values
// into the markup's value= attributes, rerenderThicknessSection rebuilds the
// section with outerHTML, and syncDerivedInputs assigns input.value directly --
// none of which dispatches an input/change event.
function markUserEdit() {
  if (state) state.userDirty = true;
}

// Schedule a calculation for the CURRENT inputs. Called on every input change
// and on every scenario change -- NEVER on mount. The debounce is what makes
// typing "1250" one request instead of four; the signature check is what makes
// an edit that changes nothing the engine reads zero requests; the userDirty
// gate is what makes a page VIEW zero requests.
export function scheduleCalculation(delay) {
  if (!state || !state.userDirty) return;
  if (debounceTimer) clearTimeout(debounceTimer);
  var wait = delay == null ? DEBOUNCE_MS : delay;
  debounceTimer = setTimeout(function () {
    debounceTimer = null;
    runCalculation();
  }, wait);
}

function runCalculation() {
  var activeState = state;
  if (!activeState) return;
  // Belt-and-braces: the gate is re-checked at FIRE time, not only at schedule
  // time, so no future caller can arm the timer around it.
  if (!activeState.userDirty) return;
  var values = readFormValues(byId('dynamic-fields'));
  var resolved = resolveCalculation(values);
  if (resolved.status === 'error') { setStatus(resolved.message, 'error'); return; }
  if (resolved.status === 'idle') { setStatus(resolved.message, 'idle'); return; }
  var signature = calculationSignature(resolved, activeState.scenario);
  if (signature === activeState.signature) { setStatus('', ''); return; }
  activeState.signature = signature;
  setStatus(MESSAGES.piipRunning, 'running');
  var task = activeState.resourceTask;
  if (!task) { setStatus('Lead Assessment component not found.', 'error'); return; }
  API.resourceAssessment(task.task_id, calculationPayload(resolved, activeState.scenario))
    .then(function (result) {
      if (state !== activeState) return null;
      activeState.display = resultsFromCalculation(result);
      var plots = result.plots || {};
      activeState.plots = {
        gas: plots.gas ? buildPlotMarkup(plots.gas, 'Gas exceedance plot') : '',
        liquid: plots.condensate ? buildPlotMarkup(plots.condensate, 'Condensate exceedance plot') : ''
      };
      renderResults();
      setStatus('', '');
      return persistResults(activeState, result, resolved);
    })
    .catch(function (error) {
      if (state !== activeState) return;
      // A rejected signature must not be remembered: the same inputs have to be
      // retried on the next edit rather than silently skipped as "already run".
      activeState.signature = '';
      setStatus(error.message, 'error');
    });
}

// Persist a fresh result exactly the way the calculator's "Apply to Lead" did:
// the SAME lead_piip_* / lead_resource_scenario / lead_calculation_method keys,
// on the SAME task, through the SAME endpoint -- so every existing reader (the
// Lead Summary trio, the board's Mean OGIP tile, the portfolio, the promotion
// snapshot) keeps resolving without knowing this page exists.
//
// Independent of Save by design: the card removes the Apply button, and a
// result the user can see but has not "applied" is exactly the stale-preview
// problem the old flow had. It is NOT independent of the USER, though -- this
// is the write KI-005 caught firing on a page view, and it is now reachable
// only from behind the interaction gate (see markUserEdit).
//
// The write carries no revision (PATCH /dynamic-fields is the fields-only
// endpoint and takes no optimistic lock), so it cannot 409 against the user's
// in-progress typing.
function persistResults(activeState, result, resolved) {
  var fields = buildLeadApplyFields(result, {
    scenario: activeState.scenario,
    method: resolved.method,
    grvP90: resolved.inputs.grvP90,
    grvP10: resolved.inputs.grvP10
  }, 'lead');
  return API.saveFields(activeState.resourceTask.task_id, fields).then(function () {
    if (state !== activeState) return null;
    // The write may have completed the Resource Assessment item (its server-side
    // predicate is this mean PLUS the polygons box). Refresh the rail, the dots
    // and the Lead Summary from the server -- renderDetail rebuilds only the
    // sidebar and the summary panel, never this centre column, so the user's
    // half-typed inputs are untouched.
    return API.detail(Store.projectId).then(function (detail) {
      if (state !== activeState || Store.projectId !== activeState.projectId) return;
      Store.project = detail.project || {};
      Store.tasks = detail.tasks || [];
      Store.allFields = detail.fields || {};
      Store.overview = detail.overview || null;
      activeState.resourceTask = taskNamed(PRIMARY_STEP) || activeState.resourceTask;
      renderDetail();
    });
  }).catch(function () {
    // A failed persist is not a failed calculation: the numbers on screen are
    // still correct and the next edit retries. Stay quiet rather than toasting
    // over the user's typing.
    return null;
  });
}

// ---------------------------------------------------------------------------
// Conversion wiring
// ---------------------------------------------------------------------------

// Recompute the derived column in place after an edit to the source column.
function syncDerivedInputs() {
  if (!state) return;
  var root = byId('dynamic-fields');
  var values = readFormValues(root);
  var converted = applyConversion(state.meta, values, state.sourceMode);
  CONVERSION_ROWS.forEach(function (row) {
    if (!coefficientsFor(state.meta, row)) return;
    var derivedKey = state.sourceMode === 'twt' ? ROW_KEYS[row].thickness : ROW_KEYS[row].twt;
    var input = root.querySelector('[data-la-field="' + derivedKey + '"]');
    if (input) input.value = converted[derivedKey];
  });
}

// Which column a key belongs to ('twt' | 'thickness'), or '' for a key outside
// Section 1.
export function columnOf(key) {
  var row = CONVERSION_ROWS.find(function (name) {
    return ROW_KEYS[name].twt === key || ROW_KEYS[name].thickness === key;
  });
  if (!row) return '';
  return ROW_KEYS[row].twt === key ? 'twt' : 'thickness';
}

// Does any convertible row still carry a value in the source column? Clearing
// the last one is what RELEASES the section so the other column can become the
// source -- the card's "switching source requires clearing the prior source".
function sourceColumnHasValue(values, mode) {
  return CONVERSION_ROWS.some(function (row) {
    if (!coefficientsFor(state.meta, row)) return false;
    var key = mode === 'twt' ? ROW_KEYS[row].twt : ROW_KEYS[row].thickness;
    return isFilled(values[key]);
  });
}

// Re-render the two twin cards after a source-mode change (readonly flags move
// between columns, so the inputs themselves have to be rebuilt).
function rerenderThicknessSection() {
  var root = byId('dynamic-fields');
  var current = root.querySelector('[data-la-section="thickness"]');
  if (!current) return;
  current.outerHTML = thicknessSectionMarkup(state.values, state.meta, state.sourceMode);
  wireInputs(root);
  renderErrors(state.errors);
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------

function onFieldInput(element) {
  if (!state) return;
  // A real input/change event on a real field: from here on the auto-run is
  // allowed to persist (see markUserEdit).
  markUserEdit();
  var key = element.getAttribute('data-la-field');
  var column = columnOf(key);
  // Section 1, conversion mode: the first value typed into a convertible row
  // DECIDES the source column, and the opposite column becomes derived.
  if (column && conversionConfigured(state.meta) && !state.sourceMode && isFilled(element.value)) {
    var row = CONVERSION_ROWS.find(function (name) {
      return ROW_KEYS[name].twt === key || ROW_KEYS[name].thickness === key;
    });
    if (coefficientsFor(state.meta, row)) {
      state.sourceMode = column;
      state.values = readFormValues(byId('dynamic-fields'));
      rerenderThicknessSection();
      syncDerivedInputs();
    }
  } else if (column === state.sourceMode && state.sourceMode) {
    syncDerivedInputs();
    // Clearing the last source value releases the section: the derived column
    // is emptied too (a derivation with no source is not a measurement) and
    // either column may be typed into next.
    if (!sourceColumnHasValue(readFormValues(byId('dynamic-fields')), state.sourceMode)) {
      state.sourceMode = '';
      state.values = readFormValues(byId('dynamic-fields'));
      CONVERSION_ROWS.forEach(function (name) {
        if (!coefficientsFor(state.meta, name)) return;
        state.values[ROW_KEYS[name].twt] = '';
        state.values[ROW_KEYS[name].thickness] = '';
      });
      rerenderThicknessSection();
    }
  }
  state.values = readFormValues(byId('dynamic-fields'));
  state.errors = validateLeadAssessment(state.values);
  renderErrors(state.errors);
  scheduleCalculation();
}

// The one-source rule's visible half: a derived (readonly) input rejects a
// typing attempt with the message naming what to clear first, instead of
// silently swallowing the keystroke.
function onDerivedAttempt(element) {
  if (!state || !state.sourceMode) return;
  var key = element.getAttribute('data-la-field');
  var row = CONVERSION_ROWS.find(function (name) {
    return ROW_KEYS[name].twt === key || ROW_KEYS[name].thickness === key;
  });
  if (!row) return;
  var sourceKey = state.sourceMode === 'twt' ? ROW_KEYS[row].twt : ROW_KEYS[row].thickness;
  var errors = Object.assign({}, state.errors);
  errors[key] = MESSAGES.secondSource(LABELS[sourceKey], LABELS[key]);
  renderErrors(errors);
}

function wireInputs(root) {
  all('[data-la-field]', root).forEach(function (element) {
    if (element.dataset.laBound) return;
    element.dataset.laBound = 'true';
    if (element.readOnly) {
      element.addEventListener('focus', function () { onDerivedAttempt(element); });
      element.addEventListener('click', function () { onDerivedAttempt(element); });
      element.addEventListener('keydown', function () { onDerivedAttempt(element); });
      return;
    }
    element.addEventListener('input', function () { onFieldInput(element); });
    element.addEventListener('change', function () { onFieldInput(element); });
  });
}

function wireScenarios(root) {
  all('input[name="la-scenario"]', root).forEach(function (input) {
    if (input.dataset.laBound) return;
    input.dataset.laBound = 'true';
    input.addEventListener('change', function () {
      if (!state || !input.checked) return;
      // The card's OTHER named trigger: "or the selected scenario change".
      markUserEdit();
      state.scenario = input.value;
      // A scenario change invalidates the previous run outright: the displayed
      // numbers belong to the OLD scenario, and a condensate/dry-gas switch
      // changes which blocks exist at all.
      state.signature = '';
      state.display = { gas: { p90: '', mean: '', p10: '' }, liquid: null };
      state.plots = { gas: '', liquid: '' };
      renderResults();
      scheduleCalculation();
    });
  });
}

// Inline plots reuse the calculator's own lightbox (#ra-plot-lightbox). One
// delegated listener on the workspace rather than per-image bindings, because
// the result blocks are rebuilt on every run.
function wirePlots(root) {
  if (root.dataset.laPlotsBound) return;
  root.dataset.laPlotsBound = 'true';
  root.addEventListener('click', function (event) {
    var trigger = event.target.closest ? event.target.closest('.ra-plot-expand, .ra-plot img') : null;
    if (!trigger) return;
    var image = trigger.closest('.ra-plot').querySelector('img');
    openPlotLightbox(image.src, image.alt);
  });
}

// ---------------------------------------------------------------------------
// Folder row
// ---------------------------------------------------------------------------

// The lead's Polygons & Surfaces share row, in the shell's own folder-card slot
// (directly under the comments box), with the same glyph/path/copy markup every
// other folder card uses.
function renderFolderRow(onCopy) {
  var previous = byId('component-folder-card');
  if (previous) previous.remove();
  var anchor = byId('comments-field');
  if (!anchor) return;
  var card = document.createElement('div');
  card.id = 'component-folder-card';
  card.className = 'folder-card';
  card.innerHTML = '<span class="folder-glyph" aria-hidden="true">📁</span>' +
    '<span class="folder-path" id="la-folder-path">Loading…</span>' +
    '<button type="button" class="icon-btn" id="copy-component-folder" title="Copy folder link" aria-label="Copy folder link" disabled>⧉</button>';
  anchor.parentNode.insertBefore(card, anchor.nextSibling);
  var forProjectId = Store.projectId;
  API.sectionFolder(forProjectId, FOLDER_SECTION_KEY).then(function (info) {
    if (Store.projectId !== forProjectId) return;
    var path = (info && info.unc_path) || '';
    var pathElement = byId('la-folder-path');
    var button = byId('copy-component-folder');
    if (!pathElement) return;
    pathElement.textContent = path || 'Folder path placeholder not configured.';
    pathElement.title = path;
    if (button && path) {
      button.disabled = false;
      button.addEventListener('click', function () { onCopy(path); });
    }
  }).catch(function () {
    if (Store.projectId !== forProjectId) return;
    var pathElement = byId('la-folder-path');
    if (pathElement) pathElement.textContent = 'Folder link unavailable.';
  });
}

// ---------------------------------------------------------------------------
// Mount
// ---------------------------------------------------------------------------

// Render the whole workspace into the shell's #dynamic-fields body. `task` is
// whichever of the four rail rows the user clicked -- it decides nothing about
// the layout, only which row stays highlighted. `onCopy` is detail-form.js's
// copyText (passed rather than imported, so this module does not take a
// dependency back on the form it mounts inside).
export function renderLeadAssessment(root, options) {
  options = options || {};
  var allFields = Store.allFields || {};
  var values = readStoredValues(allFields);
  var thicknessFields = allFields[PRIMARY_STEP] || allFields['Thickness Estimation'] || {};
  var resourceFields = allFields[PRIMARY_STEP] || allFields['Resource Assessment'] || {};
  var storedScenario = resourceFields.lead_resource_scenario;
  var stored = resultsFromStoredFields(resourceFields, 'lead');
  state = {
    projectId: Store.projectId,
    meta: Store.meta,
    formations: Store.formations || [],
    resourceTask: taskNamed(PRIMARY_STEP),
    values: values,
    errors: {},
    sourceMode: conversionConfigured(Store.meta)
      ? resolveSourceMode(values, thicknessFields.thickness_source_mode)
      : '',
    scenario: isFilled(storedScenario) ? storedScenario : DEFAULT_SCENARIO,
    // The stored PIIP numbers are what the page opens on; the plots are not
    // stored (they are rendered figures), so they arrive with the first run.
    display: stored,
    plots: { gas: '', liquid: '' },
    signature: '',
    // FALSE until a DOM event handler says otherwise -- see markUserEdit.
    // Mounting, hydrating and re-rendering this page never set it, and the
    // persisting auto-run cannot fire while it is false.
    userDirty: false,
    earlier: earlierComments(Store.tasks)
  };
  root.innerHTML = workspaceMarkup(state);
  wireInputs(root);
  wireScenarios(root);
  wirePlots(root);
  state.errors = validateLeadAssessment(state.values);
  renderErrors(state.errors);

  // The page's ONE comments box is the shell's own textarea, bound to the
  // Resource Assessment task (see earlierComments).
  var comments = byId('comments');
  if (comments) {
    comments.value = (state.resourceTask && state.resourceTask.comments) || '';
    comments.placeholder = 'Comments, assumptions, rationale, or required notes...';
  }
  renderFolderRow(options.onCopy || function () {});
  // MOUNT IS A READ (KI-005). The results the page opens on are the STORED
  // lead_piip_* values, already rendered by workspaceMarkup above via
  // resultsFromStoredFields -- no request, no recompute, no write. The plots
  // are rendered figures rather than stored values, so their slots stay empty
  // until the user's first edit or scenario change produces a fresh run.
  //
  // The only mount-time work left is the STATUS line, and it is display-only:
  // resolveCalculation is a pure function of the inputs on screen, so the page
  // can still say "enter a GRV pair..." or name a half-entered pair without
  // touching the network.
  renderMountStatus();
}

// The idle/error hint for the inputs as they arrived, with no run behind it.
function renderMountStatus() {
  if (!state) return;
  var resolved = resolveCalculation(state.values);
  if (resolved.status === 'ready') { setStatus('', ''); return; }
  setStatus(resolved.message, resolved.status === 'error' ? 'error' : 'idle');
}

// ---------------------------------------------------------------------------
// Save
// ---------------------------------------------------------------------------

// ONE navy button for the whole page. It validates everything, groups the
// values back onto their four owning tasks, and PATCHes only the dirty ones --
// each with its OWN revision, sequentially, so the existing optimistic lock and
// its 409 toast keep working per task exactly as they do for a single-step
// form. The auto-run's PIIP write is NOT part of this: it has already
// persisted itself.
export function saveLeadAssessment() {
  if (!state) return Promise.resolve(false);
  if (!isCurrentPipelineView()) {
    msg('Switch back to the current pipeline to save changes.', 'error');
    return Promise.resolve(false);
  }
  var root = byId('dynamic-fields');
  var values = readFormValues(root);
  // The stored source marker travels with the values it explains.
  values.thickness_source_mode = state.sourceMode || '';
  state.values = values;
  var errors = validateLeadAssessment(values);
  state.errors = errors;
  renderErrors(errors);
  var blocking = firstError(errors);
  if (blocking) { msg(blocking, 'error'); return Promise.resolve(false); }

  var plan = buildSavePlan(values, Store.allFields || {});
  var comments = byId('comments');
  var commentsValue = comments ? comments.value : '';
  var primaryTask = taskNamed(PRIMARY_STEP);
  var commentsChanged = !!primaryTask && String(primaryTask.comments || '') !== String(commentsValue);
  if (commentsChanged && !plan.some(function (entry) { return entry.taskName === PRIMARY_STEP; })) {
    plan.push({ taskName: PRIMARY_STEP, fields: {} });
  }
  if (!plan.length) { msg('No changes to save.', 'success'); return Promise.resolve(true); }

  var saveButton = byId('save-component');
  if (saveButton) saveButton.disabled = true;
  var chain = Promise.resolve();
  plan.forEach(function (entry) {
    chain = chain.then(function () {
      var task = taskNamed(entry.taskName);
      if (!task) throw new Error(entry.taskName + ' component not found.');
      // comments/priority are ECHOED per task: save_task clears an absent
      // comments key and defaults an absent priority to Medium, so a batched
      // write that omitted them would quietly wipe three steps' notes.
      return API.updateTask(task.task_id, {
        fields: entry.fields,
        comments: entry.taskName === PRIMARY_STEP ? commentsValue : (task.comments || ''),
        priority: task.priority || 'Medium',
        revision: task.revision,
        changed_by: currentUserName()
      }).catch(function (error) {
        throw new Error(entry.taskName + ': ' + error.message);
      });
    });
  });
  return chain.then(function () {
    return refreshAfterRecordChange('Lead assessment saved.');
  }).then(function () {
    return true;
  }).catch(function (error) {
    msg(error.message, 'error');
    return false;
  }).finally(function () {
    var button = byId('save-component');
    if (button) button.disabled = false;
  });
}
