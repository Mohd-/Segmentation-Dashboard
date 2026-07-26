// Resource Assessment popup (#resource-assessment-dialog in index.html) --
// mirrors the standalone Streamlit PIIP calculator (resource-assessment/app.py):
// scenario + method segmented controls, conditional GRV / Area x Thickness
// inputs, Calculate -> POST /api/tasks/<leadTaskId>/resource-assessment,
// side-by-side exceedance plots, Apply to Lead.
//
// Opened only from the Lead Resource Assessment component view
// (detail-form.js renders the trigger). All DOM wiring is lazy (wireOnce,
// called from openResourceAssessmentDialog) so importing this module for its
// pure functions (validateResourceInputs, formatStored, buildLeadApplyFields,
// buildPlotMarkup) never touches the DOM -- the popup markup (including the
// plot lightbox, #ra-plot-lightbox) does not exist in the test harness
// fixtures (static/tests/runner.html), only in the real app shell.
import { byId, all, esc, isFilled } from '../dom.js';
import { API } from '../api.js';
import { Store } from '../state.js';
import { RESOURCE_SCENARIOS } from '../schema.js';
import { refreshAfterRecordChange } from './detail.js';

// Defaults mirror app.py's _scenario_selector/_method_selector/_positive_float_input.
var DEFAULT_SCENARIO = 'dry_gas_high_pressure';
var DEFAULT_METHOD = 'GRV';
var DEFAULT_GRV_P90 = '12.60';
var DEFAULT_GRV_P10 = '17.30';

// Module-level popup state; null whenever the dialog is not open (reset on
// the native 'close' event so a stray async response after close is a no-op).
var state = null;
var wired = false;

// Scenarios: GET /api/meta's resource_scenarios (Store.meta) is authoritative
// at runtime, production-swappable like seismic_blocks; RESOURCE_SCENARIOS
// (schema.js) is the boot fallback.
function resourceScenarios() {
  return (Store.meta && Store.meta.resource_scenarios) || RESOURCE_SCENARIOS;
}

function findTaskByName(name) {
  return (Store.tasks || []).find(function (task) { return task.task_name === name; });
}

// ---------------------------------------------------------------------------
// Pure functions (exported for unit tests -- static/tests/test-resource-popup.js)
// ---------------------------------------------------------------------------

function numericError(label, raw) {
  if (!isFilled(raw) || isNaN(Number(raw))) return label + ' must be numeric.';
  if (Number(raw) <= 0) return label + ' must be positive.';
  return null;
}

// state -> error string, or null when the method-relevant inputs are all
// valid. Mirrors app.py's _positive_float_input (numeric, then positive) plus
// the P90 < P10 ordering app.py leaves to the resource engine's own
// validation -- checked here so the popup can surface it before Calculate.
export function validateResourceInputs(state) {
  var s = state || {};
  if (s.method === 'GRV') {
    var grvP90Error = numericError('GRV P90', s.grvP90);
    if (grvP90Error) return grvP90Error;
    var grvP10Error = numericError('GRV P10', s.grvP10);
    if (grvP10Error) return grvP10Error;
    if (Number(s.grvP90) >= Number(s.grvP10)) return 'GRV P90 must be lower than GRV P10.';
    return null;
  }
  var areaP90Error = numericError('Area P90', s.areaP90);
  if (areaP90Error) return areaP90Error;
  var areaP10Error = numericError('Area P10', s.areaP10);
  if (areaP10Error) return areaP10Error;
  if (Number(s.areaP90) >= Number(s.areaP10)) return 'Area P90 must be lower than Area P10.';
  var thicknessError = numericError('Reservoir Thickness P50', s.thicknessP50);
  if (thicknessError) return thicknessError;
  return null;
}

// Display-side rounding for the values written back to the Lead RA task's
// PIIP fields: .2f under 10, .1f from 10 up to (not including) 1000, .0f at
// 1000 and above. Non-numeric input returns '' (never throws -- callers only
// ever pass calculator result numbers, but stay defensive).
export function formatStored(v) {
  if (v === null || v === undefined || v === '') return '';
  var n = Number(v);
  if (!isFinite(n)) return '';
  var abs = Math.abs(n);
  if (abs < 10) return n.toFixed(2);
  if (abs < 1000) return n.toFixed(1);
  return n.toFixed(0);
}

// The request body for POST /api/tasks/<leadTaskId>/resource-assessment --
// only the method-relevant numeric fields are included, per the backend
// contract.
export function buildCalculatePayload(state) {
  var s = state || {};
  var payload = { scenario: s.scenario, method: s.method };
  if (s.method === 'GRV') {
    payload.grv_p90 = Number(s.grvP90);
    payload.grv_p10 = Number(s.grvP10);
  } else {
    payload.area_p90_km2 = Number(s.areaP90);
    payload.area_p10_km2 = Number(s.areaP10);
    payload.thickness_p50_ft = Number(s.thicknessP50);
  }
  return payload;
}

// The Lead RA task's dynamic-fields save payload for "Apply to Lead". Key
// names are the permanent EAV contract -- do not rename (renaming orphans
// stored data, same rule as every other piip()-family field). GRV inputs are
// only persisted for the GRV method (Area x Thickness has no GRV of its own).
export function buildLeadApplyFields(result, state) {
  var s = state || {};
  var hasCondensate = !!(result && result.condensate);
  var fields = {
    lead_piip_gas_p90: formatStored(result.gas.p90),
    lead_piip_gas_mean: formatStored(result.gas.mean),
    lead_piip_gas_p10: formatStored(result.gas.p10),
    lead_piip_has_liquid: hasCondensate ? '1' : '',
    lead_piip_liquid_p90: hasCondensate ? formatStored(result.condensate.p90) : '',
    lead_piip_liquid_mean: hasCondensate ? formatStored(result.condensate.mean) : '',
    lead_piip_liquid_p10: hasCondensate ? formatStored(result.condensate.p10) : '',
    lead_resource_scenario: s.scenario,
    lead_calculation_method: s.method
  };
  if (s.method === 'GRV') {
    fields.lead_grv_p90_thousand_acre_ft = s.grvP90;
    fields.lead_grv_p10_thousand_acre_ft = s.grvP10;
  }
  return fields;
}

// ---------------------------------------------------------------------------
// DOM wiring (lazy -- see the module docblock)
// ---------------------------------------------------------------------------

function showError(message) {
  var el = byId('ra-error');
  el.textContent = message;
  el.classList.remove('hidden');
}
function clearError() {
  var el = byId('ra-error');
  el.textContent = '';
  el.classList.add('hidden');
}
function hideResults() {
  byId('ra-results').classList.add('hidden');
  byId('ra-plots').innerHTML = '';
}
function setApplyEnabled(enabled) {
  byId('ra-apply').disabled = !enabled;
}
// Any input change after a successful Calculate invalidates the stale result
// -- Apply must not write back a calculation that no longer matches the
// visible inputs.
function invalidateResult() {
  if (!state) return;
  state.result = null;
  hideResults();
  setApplyEnabled(false);
}

function renderScenarioOptions() {
  var container = byId('ra-scenario-options');
  container.innerHTML = resourceScenarios().map(function (scenario) {
    return '<label class="radio-option"><input type="radio" name="ra-scenario" value="' +
      esc(scenario.id) + '"> ' + esc(scenario.label) + '</label>';
  }).join('');
  all('input[name="ra-scenario"]', container).forEach(function (input) {
    input.addEventListener('change', function () {
      if (!state) return;
      state.scenario = input.value;
      invalidateResult();
    });
  });
}

function syncScenarioRadios() {
  all('input[name="ra-scenario"]').forEach(function (input) {
    input.checked = input.value === state.scenario;
  });
}
function syncMethodRadios() {
  all('input[name="ra-method"]').forEach(function (input) {
    input.checked = input.value === state.method;
  });
}
function updateMethodVisibility() {
  var isGrv = state.method === 'GRV';
  byId('ra-grv-fields').classList.toggle('hidden', !isGrv);
  byId('ra-area-fields').classList.toggle('hidden', isGrv);
}
function syncInputsFromState() {
  byId('ra-grv-p90').value = state.grvP90;
  byId('ra-grv-p10').value = state.grvP10;
  byId('ra-area-p90').value = state.areaP90;
  byId('ra-area-p10').value = state.areaP10;
  byId('ra-thickness-p50').value = state.thicknessP50;
}
function readInputsIntoState() {
  state.grvP90 = byId('ra-grv-p90').value;
  state.grvP10 = byId('ra-grv-p10').value;
  state.areaP90 = byId('ra-area-p90').value;
  state.areaP10 = byId('ra-area-p10').value;
  state.thicknessP50 = byId('ra-thickness-p50').value;
}
function bindNumberInput(id, key) {
  byId(id).addEventListener('input', function () {
    if (!state) return;
    state[key] = byId(id).value;
    invalidateResult();
  });
}

// Markup for one plot card: the image plus its hover/focus-reveal expand
// button (see .ra-plot-expand in components.css). Pure string builder --
// exported for a DOM-free unit test. `#ra-plots` is rebuilt wholesale on
// every Calculate, so the expand click handling is delegated once on the
// container (see wireOnce) rather than bound per-image here.
export function buildPlotMarkup(src, altText) {
  var label = altText || 'Exceedance plot';
  return '<div class="ra-plot">' +
    '<img alt="' + esc(label) + '" src="' + esc(src) + '">' +
    '<button type="button" class="ra-plot-expand icon-btn" title="Enlarge plot" aria-label="Enlarge ' + esc(label) + '">&#x26F6;</button>' +
    '</div>';
}

function renderPlots(result) {
  var plots = result.plots || {};
  var html = buildPlotMarkup(plots.gas, 'Gas exceedance plot');
  if (result.condensate && plots.condensate) {
    html += buildPlotMarkup(plots.condensate, 'Condensate exceedance plot');
  }
  byId('ra-plots').innerHTML = html;
  byId('ra-results').classList.remove('hidden');
}

// Lightbox (#ra-plot-lightbox): set the image via DOM properties (not
// innerHTML -- no escaping needed) and showModal() it on top of the
// already-open #resource-assessment-dialog. Nested native <dialog> modals
// stack correctly in the top layer, so this renders above the popup and Esc
// closes the lightbox first, the popup on a second press.
function openLightbox(src, altText) {
  var img = byId('ra-lightbox-img');
  img.src = src;
  img.alt = altText || 'Enlarged exceedance plot';
  byId('ra-plot-lightbox').showModal();
}

function onCalculate() {
  if (!state) return;
  readInputsIntoState();
  var error = validateResourceInputs(state);
  if (error) { showError(error); return; }
  clearError();
  var button = byId('ra-calculate');
  button.disabled = true;
  API.resourceAssessment(state.task.task_id, buildCalculatePayload(state)).then(function (result) {
    if (!state) return;
    state.result = result;
    renderPlots(result);
    setApplyEnabled(true);
  }).catch(function (error) {
    showError(error.message);
  }).finally(function () {
    button.disabled = false;
  });
}

// Changed-only write-back of the area/thickness inputs to their owning tasks
// (Reservoir Area Definition / Thickness Estimation), sequential (one save
// resolves before the next starts) so both never race the same project's
// write lock. Untouched inputs (unchanged from the prefill, or never shown
// because the method never revealed them) produce an empty diff and are
// skipped entirely -- no spurious history entries.
function writeBackChangedFields() {
  var chain = Promise.resolve();
  var areaChanged = {};
  if (String(state.areaP90) !== String(state.initialAreaP90)) areaChanged.p90_area_km2 = state.areaP90;
  if (String(state.areaP10) !== String(state.initialAreaP10)) areaChanged.p10_area_km2 = state.areaP10;
  if (Object.keys(areaChanged).length) {
    if (!state.areaTask) return Promise.reject(new Error('Reservoir Area Definition component not found.'));
    chain = chain.then(function () {
      return API.saveFields(state.areaTask.task_id, areaChanged).catch(function (error) {
        throw new Error('Failed to save Reservoir Area Definition: ' + error.message);
      });
    });
  }
  var thicknessChanged = {};
  if (String(state.thicknessP50) !== String(state.initialThickness)) thicknessChanged.formation_thickness_ft = state.thicknessP50;
  if (Object.keys(thicknessChanged).length) {
    if (!state.thicknessTask) return Promise.reject(new Error('Thickness Estimation component not found.'));
    chain = chain.then(function () {
      return API.saveFields(state.thicknessTask.task_id, thicknessChanged).catch(function (error) {
        throw new Error('Failed to save Thickness Estimation: ' + error.message);
      });
    });
  }
  return chain;
}

function onApply() {
  if (!state || !state.result) return;
  var applyButton = byId('ra-apply');
  applyButton.disabled = true;
  clearError();
  var leadFields = buildLeadApplyFields(state.result, state);
  API.saveFields(state.task.task_id, leadFields).catch(function (error) {
    throw new Error('Failed to save Lead Resource Assessment: ' + error.message);
  }).then(function () {
    return writeBackChangedFields();
  }).then(function () {
    byId('resource-assessment-dialog').close();
    return refreshAfterRecordChange('Resource assessment applied to lead.');
  }).catch(function (error) {
    showError(error.message);
    applyButton.disabled = false;
  });
}

function wireOnce() {
  if (wired) return;
  wired = true;
  var dialog = byId('resource-assessment-dialog');
  renderScenarioOptions();
  all('input[name="ra-method"]').forEach(function (input) {
    input.addEventListener('change', function () {
      if (!state) return;
      state.method = input.value;
      updateMethodVisibility();
      invalidateResult();
    });
  });
  bindNumberInput('ra-grv-p90', 'grvP90');
  bindNumberInput('ra-grv-p10', 'grvP10');
  bindNumberInput('ra-area-p90', 'areaP90');
  bindNumberInput('ra-area-p10', 'areaP10');
  bindNumberInput('ra-thickness-p50', 'thicknessP50');
  byId('ra-calculate').addEventListener('click', onCalculate);
  byId('ra-apply').addEventListener('click', onApply);
  byId('ra-close').addEventListener('click', function () { dialog.close(); });

  // Expand-to-lightbox: one delegated listener on the (repeatedly rebuilt)
  // #ra-plots container, rather than per-image bindings that renderPlots
  // would need to re-wire on every Calculate. Triggers on either the image
  // itself (cursor: zoom-in) or its corner expand button.
  byId('ra-plots').addEventListener('click', function (event) {
    var trigger = event.target.closest('.ra-plot-expand, .ra-plot img');
    if (!trigger) return;
    var img = trigger.closest('.ra-plot').querySelector('img');
    openLightbox(img.src, img.alt);
  });
  // Light-dismiss: a click ANYWHERE inside the lightbox dialog (backdrop,
  // the image, or the close button) bubbles to this one listener and closes
  // it -- native <dialog> backdrop clicks target the dialog element itself,
  // and content clicks bubble up to it the same way.
  var lightbox = byId('ra-plot-lightbox');
  lightbox.addEventListener('click', function () { lightbox.close(); });

  dialog.addEventListener('close', function () {
    state = null;
    // Safety net: the lightbox must never survive the popup closing (Close,
    // Apply-success, or an Esc that reaches the popup). In practice the
    // lightbox being modal-on-top already blocks interaction with the popup
    // underneath it, but this guards any programmatic dialog.close() too.
    if (lightbox.open) lightbox.close();
  });
}

// Entry point: called by detail-form.js's "Open Resource Assessment" trigger
// (Lead Resource Assessment component only). `fields` is that task's current
// dynamic fields (Store.allFields['Lead Resource Assessment']); area/
// thickness prefill from the sibling tasks via Store.allFields/Store.tasks --
// the same mechanism detail-form.js/detail.js already use for cross-task
// reads (e.g. _apply_stub_cos_calculations' front-end counterpart).
export function openResourceAssessmentDialog(projectId, task, fields) {
  wireOnce();
  fields = fields || {};
  var areaFields = (Store.allFields || {})['Reservoir Area Definition'] || {};
  var thicknessFields = (Store.allFields || {})['Thickness Estimation'] || {};
  var initialAreaP90 = isFilled(areaFields.p90_area_km2) ? areaFields.p90_area_km2 : '';
  var initialAreaP10 = isFilled(areaFields.p10_area_km2) ? areaFields.p10_area_km2 : '';
  var initialThickness = isFilled(thicknessFields.formation_thickness_ft) ? thicknessFields.formation_thickness_ft : '';
  state = {
    projectId: projectId,
    task: task,
    scenario: isFilled(fields.lead_resource_scenario) ? fields.lead_resource_scenario : DEFAULT_SCENARIO,
    method: isFilled(fields.lead_calculation_method) ? fields.lead_calculation_method : DEFAULT_METHOD,
    grvP90: isFilled(fields.lead_grv_p90_thousand_acre_ft) ? fields.lead_grv_p90_thousand_acre_ft : DEFAULT_GRV_P90,
    grvP10: isFilled(fields.lead_grv_p10_thousand_acre_ft) ? fields.lead_grv_p10_thousand_acre_ft : DEFAULT_GRV_P10,
    areaP90: initialAreaP90,
    areaP10: initialAreaP10,
    thicknessP50: initialThickness,
    initialAreaP90: initialAreaP90,
    initialAreaP10: initialAreaP10,
    initialThickness: initialThickness,
    areaTask: findTaskByName('Reservoir Area Definition'),
    thicknessTask: findTaskByName('Thickness Estimation'),
    result: null
  };
  syncScenarioRadios();
  syncMethodRadios();
  syncInputsFromState();
  updateMethodVisibility();
  clearError();
  hideResults();
  setApplyEnabled(false);
  byId('resource-assessment-dialog').showModal();
}
