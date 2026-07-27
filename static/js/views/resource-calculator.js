// Resource Assessment calculator -- mirrors the standalone Streamlit PIIP
// calculator (resource-assessment/app.py): scenario + method segmented
// controls, conditional GRV / Area x Thickness inputs, Calculate -> POST
// /api/tasks/<leadTaskId>/resource-assessment, Apply to Lead.
//
// Renders INLINE in the Lead Resource Assessment step body (detail-form.js's
// renderResourceCalculatorSection), not a dialog -- only the exceedance
// plots still live behind a dialog (#resource-assessment-dialog, now a
// plots-only viewer) because stored PIIP values alone have no rendered
// figures; a "View exceedance plots" trigger only appears after a Calculate
// in the current session.
//
// The calculator's own inputs/results are rebuilt from scratch on every
// renderResourceCalculator call (detail-form.js tears down and recreates the
// container on every loadComponent), so THEIR listeners are simply (re)bound
// every render -- no persistence to guard. The plots dialog + lightbox DO
// persist across renders (static index.html elements, shown/hidden via
// showModal/close), so THEIR wiring keeps the lazy wireOnce-once pattern.
// Either way nothing runs at module-eval time, so importing this module for
// its pure functions (validateResourceInputs, formatStored,
// buildCalculatePayload, buildLeadApplyFields, buildPlotMarkup,
// buildResultsMarkup, resultsFromStoredFields, resultsFromCalculation) never
// touches the DOM -- none of this markup exists in the test harness fixtures
// (static/tests/runner.html), only in the real app shell.
import { byId, all, esc, isFilled, truthy, msg } from '../dom.js';
import { API } from '../api.js';
import { Store, isCurrentPipelineView } from '../state.js';
import { RESOURCE_SCENARIOS } from '../schema.js';
import { refreshAfterRecordChange } from './detail.js';

// Defaults mirror app.py's _scenario_selector/_method_selector/_positive_float_input.
var DEFAULT_SCENARIO = 'dry_gas_high_pressure';
var DEFAULT_METHOD = 'GRV';
var DEFAULT_GRV_P90 = '12.60';
var DEFAULT_GRV_P10 = '17.30';

// The current render generation's state, or null once torn down (switching
// away from the Lead RA step). Async handlers (onCalculate/onApply) capture
// their OWN `state` reference at call time and compare it back against this
// module-level one before touching the DOM, so a response that resolves
// after the panel has been rebuilt (new render) or removed (different step)
// is a safe no-op instead of writing into gone/stale elements -- a real risk
// now that the calculator is inline (not a blocking modal like before).
var state = null;
var plotsDialogWired = false;

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
// Pure functions (exported for unit tests -- static/tests/test-resource-calculator.js)
// ---------------------------------------------------------------------------

// Same generic sanity cap as the regular step forms' validateStepFields
// (schema.js) -- GRV/area/thickness inputs are all well under this in
// practice, so no bigOk-style exemption is needed here.
var MAX_NUMBER = 9999;

function numericError(label, raw) {
  if (!isFilled(raw) || isNaN(Number(raw))) return label + ' must be numeric.';
  if (Number(raw) <= 0) return label + ' must be positive.';
  if (Number(raw) > MAX_NUMBER) return label + ' looks too large (max ' + MAX_NUMBER + ').';
  return null;
}

// state -> error string, or null when the method-relevant inputs are all
// valid. Mirrors app.py's _positive_float_input (numeric, then positive) plus
// the P90 < P10 ordering app.py leaves to the resource engine's own
// validation -- checked here so the calculator can surface it before
// Calculate.
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

// Markup for one plot card: the image plus its hover/focus-reveal expand
// button (see .ra-plot-expand in components.css). Pure string builder --
// exported for a DOM-free unit test. `#ra-plots` is rebuilt wholesale on
// every Calculate, so the expand click handling is delegated once on the
// container (see wirePlotsDialogOnce) rather than bound per-image here.
export function buildPlotMarkup(src, altText) {
  var label = altText || 'Exceedance plot';
  return '<div class="ra-plot">' +
    '<img alt="' + esc(label) + '" src="' + esc(src) + '">' +
    '<button type="button" class="ra-plot-expand icon-btn" title="Enlarge plot" aria-label="Enlarge ' + esc(label) + '">&#x26F6;</button>' +
    '</div>';
}

// --- Read-only PIIP results (step-body display, not the dialog) ------------

// One display value: reuses the .calculated-output label+<output> idiom
// already used for Trap/Seal CoS's readonly computed percentage, minus the
// '%' suffix (these are BCF/MMSTB volumes, not percentages -- the section
// heading names the unit).
function resultOutput(label, value) {
  return '<label class="calculated-output">' + esc(label) + '<output>' +
    (isFilled(value) ? esc(value) : '—') + '</output></label>';
}

// Pure: `values` -> the results panel's markup. `values.gas` is always
// rendered; `values.liquid` (or its absence) decides whether the Liquid
// (MMSTB) row renders at all -- mirrors piip()'s showIf-gated liquid trio,
// just non-editable. Exported for a DOM-free unit test.
export function buildResultsMarkup(values) {
  values = values || {};
  var gas = values.gas || {};
  var html = '<div class="field-section-label">Gas (BCF)</div>' +
    '<div class="field-row cols-3">' +
    resultOutput('P90', gas.p90) + resultOutput('Mean', gas.mean) + resultOutput('P10', gas.p10) +
    '</div>';
  if (values.liquid) {
    var liquid = values.liquid;
    html += '<div class="field-section-label">Liquid (MMSTB)</div>' +
      '<div class="field-row cols-3">' +
      resultOutput('P90', liquid.p90) + resultOutput('Mean', liquid.mean) + resultOutput('P10', liquid.p10) +
      '</div>';
  }
  return html;
}

// Pure: the Lead RA task's persisted fields -> the results-display value
// shape buildResultsMarkup renders. Stored values are already
// formatStored()-formatted strings (Apply is their only writer) -- shown
// verbatim, no re-formatting. Exported for a DOM-free unit test.
export function resultsFromStoredFields(fields) {
  fields = fields || {};
  return {
    gas: { p90: fields.lead_piip_gas_p90 || '', mean: fields.lead_piip_gas_mean || '', p10: fields.lead_piip_gas_p10 || '' },
    liquid: truthy(fields.lead_piip_has_liquid)
      ? { p90: fields.lead_piip_liquid_p90 || '', mean: fields.lead_piip_liquid_mean || '', p10: fields.lead_piip_liquid_p10 || '' }
      : null
  };
}

// Pure: a fresh Calculate response -> the same value shape, running every
// number through formatStored (the same rounding Apply itself stores) so
// the in-session preview matches what Apply would persist. Exported for a
// DOM-free unit test.
export function resultsFromCalculation(result) {
  result = result || {};
  var gas = result.gas || {};
  var out = {
    gas: { p90: formatStored(gas.p90), mean: formatStored(gas.mean), p10: formatStored(gas.p10) },
    liquid: null
  };
  if (result.condensate) {
    var c = result.condensate;
    out.liquid = { p90: formatStored(c.p90), mean: formatStored(c.mean), p10: formatStored(c.p10) };
  }
  return out;
}

// ---------------------------------------------------------------------------
// Inline calculator markup + per-render wiring
// ---------------------------------------------------------------------------

// The two-column inner content of the container detail-form.js creates
// (.resource-calculator-panel): calculator controls, then the results panel.
// IDs are unique in the document at any moment (only one Lead RA panel ever
// exists at a time), so plain byId() lookups below work exactly as they did
// for the old dialog markup.
function calculatorMarkup() {
  return (
    '<div class="resource-calculator-inputs">' +
      '<div class="ra-inline-heading">Resource Assessment Calculator</div>' +
      '<div class="radio-group">' +
        '<span class="radio-group-label" id="ra-scenario-label">Scenario</span>' +
        '<div class="radio-options" id="ra-scenario-options" role="radiogroup" aria-labelledby="ra-scenario-label"></div>' +
      '</div>' +
      '<div class="radio-group">' +
        '<span class="radio-group-label" id="ra-method-label">Calculation Method</span>' +
        '<div class="radio-options" role="radiogroup" aria-labelledby="ra-method-label">' +
          '<label class="radio-option"><input type="radio" name="ra-method" value="GRV"> GRV</label>' +
          '<label class="radio-option"><input type="radio" name="ra-method" value="Box Model"> Area x Thickness</label>' +
        '</div>' +
      '</div>' +
      '<div id="ra-grv-fields" class="field-row cols-2">' +
        '<label>GRV P90 [10&sup3; acre-ft]<input type="number" step="any" id="ra-grv-p90"></label>' +
        '<label>GRV P10 [10&sup3; acre-ft]<input type="number" step="any" id="ra-grv-p10"></label>' +
      '</div>' +
      '<div id="ra-area-fields" class="field-row cols-3 hidden">' +
        '<label>Area P90 [km&sup2;]<input type="number" step="any" id="ra-area-p90"></label>' +
        '<label>Area P10 [km&sup2;]<input type="number" step="any" id="ra-area-p10"></label>' +
        '<label>Reservoir Thickness P50 [ft]<input type="number" step="any" id="ra-thickness-p50"></label>' +
      '</div>' +
      '<p id="ra-error" class="ra-error hidden"></p>' +
      '<div class="ra-actions">' +
        '<button type="button" id="ra-calculate">Calculate</button>' +
        '<button type="button" id="ra-apply" class="ra-apply-btn" disabled>Apply to Lead</button>' +
        '<button type="button" id="ra-view-plots" class="ghost hidden">View exceedance plots</button>' +
      '</div>' +
    '</div>' +
    '<div class="resource-calculator-results">' +
      '<div class="ra-inline-heading">PIIP Results</div>' +
      '<div id="ra-results-panel" class="ra-results-panel"></div>' +
    '</div>'
  );
}

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
function setApplyEnabled(enabled) {
  byId('ra-apply').disabled = !enabled;
}
function showPlotsLink() {
  var button = byId('ra-view-plots');
  if (button) button.classList.remove('hidden');
}
function hidePlotsLink() {
  var button = byId('ra-view-plots');
  if (button) button.classList.add('hidden');
  var plots = byId('ra-plots');
  if (plots) plots.innerHTML = '';
}
function renderResultsPanel() {
  byId('ra-results-panel').innerHTML = buildResultsMarkup(state.displayValues);
}
// Any input/scenario/method change after a successful Calculate invalidates
// the stale in-session result: Apply must not write back a calculation that
// no longer matches the visible inputs, the plots link must not point at a
// figure for a request that's no longer current, and the results display
// reverts to what's actually stored (not the just-abandoned preview).
function invalidateResult() {
  if (!state) return;
  state.result = null;
  state.displayValues = state.storedValues;
  renderResultsPanel();
  hidePlotsLink();
  setApplyEnabled(false);
}

function renderScenarioOptions() {
  var container = byId('ra-scenario-options');
  container.innerHTML = resourceScenarios().map(function (scenario) {
    return '<label class="radio-option"><input type="radio" name="ra-scenario" value="' +
      esc(scenario.id) + '"> ' + esc(scenario.label) + '</label>';
  }).join('');
  all('input[name="ra-scenario"]', container).forEach(function (input) {
    input.checked = input.value === state.scenario;
    input.addEventListener('change', function () {
      if (!state) return;
      state.scenario = input.value;
      invalidateResult();
    });
  });
}
function bindMethodRadios() {
  all('input[name="ra-method"]').forEach(function (input) {
    input.checked = input.value === state.method;
    input.addEventListener('change', function () {
      if (!state) return;
      state.method = input.value;
      updateMethodVisibility();
      invalidateResult();
    });
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

function renderPlots(result) {
  var plots = result.plots || {};
  var html = buildPlotMarkup(plots.gas, 'Gas exceedance plot');
  if (result.condensate && plots.condensate) {
    html += buildPlotMarkup(plots.condensate, 'Condensate exceedance plot');
  }
  byId('ra-plots').innerHTML = html;
}

// Lightbox (#ra-plot-lightbox): set the image via DOM properties (not
// innerHTML -- no escaping needed) and showModal() it on top of the
// already-open #resource-assessment-dialog. Nested native <dialog> modals
// stack correctly in the top layer, so this renders above the plots viewer
// and Esc closes the lightbox first, the plots viewer on a second press.
function openLightbox(src, altText) {
  var img = byId('ra-lightbox-img');
  img.src = src;
  img.alt = altText || 'Enlarged exceedance plot';
  byId('ra-plot-lightbox').showModal();
}

// Bound once (guarded by plotsDialogWired): #resource-assessment-dialog,
// #ra-plots and #ra-plot-lightbox are static index.html elements that
// persist across every renderResourceCalculator call, unlike the calculator
// panel itself.
function wirePlotsDialogOnce() {
  if (plotsDialogWired) return;
  plotsDialogWired = true;
  var dialog = byId('resource-assessment-dialog');
  byId('ra-plots-close').addEventListener('click', function () { dialog.close(); });

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

  // Safety net: the lightbox must never survive the plots viewer closing.
  // In practice the lightbox being modal-on-top already blocks interaction
  // with the dialog underneath it, but this guards any programmatic
  // dialog.close() too.
  dialog.addEventListener('close', function () {
    if (lightbox.open) lightbox.close();
  });
}

function onCalculate() {
  var activeState = state;
  if (!activeState) return;
  if (!isCurrentPipelineView()) return msg('Switch back to the current pipeline to use the calculator.', 'error');
  readInputsIntoState();
  var error = validateResourceInputs(activeState);
  if (error) { showError(error); return; }
  clearError();
  var button = byId('ra-calculate');
  button.disabled = true;
  API.resourceAssessment(activeState.task.task_id, buildCalculatePayload(activeState)).then(function (result) {
    if (state !== activeState) return; // this render generation is gone; nothing to update
    activeState.result = result;
    activeState.displayValues = resultsFromCalculation(result);
    renderResultsPanel();
    renderPlots(result);
    showPlotsLink();
    setApplyEnabled(true);
  }).catch(function (error) {
    if (state !== activeState) return;
    showError(error.message);
  }).finally(function () {
    if (state === activeState) byId('ra-calculate').disabled = false;
  });
}

// Changed-only write-back of the area/thickness inputs to their owning tasks
// (Reservoir Area Definition / Thickness Estimation), sequential (one save
// resolves before the next starts) so both never race the same project's
// write lock. Untouched inputs (unchanged from the prefill, or never shown
// because the method never revealed them) produce an empty diff and are
// skipped entirely -- no spurious history entries.
function writeBackChangedFields(activeState) {
  var chain = Promise.resolve();
  var areaChanged = {};
  if (String(activeState.areaP90) !== String(activeState.initialAreaP90)) areaChanged.p90_area_km2 = activeState.areaP90;
  if (String(activeState.areaP10) !== String(activeState.initialAreaP10)) areaChanged.p10_area_km2 = activeState.areaP10;
  if (Object.keys(areaChanged).length) {
    if (!activeState.areaTask) return Promise.reject(new Error('Reservoir Area Definition component not found.'));
    chain = chain.then(function () {
      return API.saveFields(activeState.areaTask.task_id, areaChanged).catch(function (error) {
        throw new Error('Failed to save Reservoir Area Definition: ' + error.message);
      });
    });
  }
  var thicknessChanged = {};
  if (String(activeState.thicknessP50) !== String(activeState.initialThickness)) thicknessChanged.reservoir_thickness_ft = activeState.thicknessP50;
  if (Object.keys(thicknessChanged).length) {
    if (!activeState.thicknessTask) return Promise.reject(new Error('Thickness Estimation component not found.'));
    chain = chain.then(function () {
      return API.saveFields(activeState.thicknessTask.task_id, thicknessChanged).catch(function (error) {
        throw new Error('Failed to save Thickness Estimation: ' + error.message);
      });
    });
  }
  return chain;
}

function onApply() {
  var activeState = state;
  if (!activeState || !activeState.result) return;
  if (!isCurrentPipelineView()) return msg('Switch back to the current pipeline to apply this assessment.', 'error');
  var applyButton = byId('ra-apply');
  applyButton.disabled = true;
  clearError();
  var leadFields = buildLeadApplyFields(activeState.result, activeState);
  API.saveFields(activeState.task.task_id, leadFields).catch(function (error) {
    throw new Error('Failed to save Lead Resource Assessment: ' + error.message);
  }).then(function () {
    return writeBackChangedFields(activeState);
  }).then(function () {
    var dialog = byId('resource-assessment-dialog');
    if (dialog.open) dialog.close(); // defensive: Apply can't normally fire while it's modal-open
    return refreshAfterRecordChange('Resource assessment applied to lead.');
  }).catch(function (error) {
    if (state !== activeState) return; // this render generation is gone; nothing to show the error on
    showError(error.message);
    applyButton.disabled = false;
  });
}

// Called by detail-form.js whenever it removes the calculator panel from the
// DOM without immediately rendering a fresh one (leaving the Lead Resource
// Assessment step for a different component). Nulls the render generation so
// a stray async Calculate/Apply response that resolves afterward is a no-op
// (see the `state`/`activeState` doc comment above) instead of touching gone
// elements. Not needed when staying on the SAME step (a post-Apply refresh,
// or simply reopening it) -- renderResourceCalculator already replaces
// `state` wholesale on every call.
export function teardownResourceCalculator() {
  state = null;
}

// Entry point: called by detail-form.js's renderResourceCalculatorSection
// (Lead Resource Assessment component only) with a freshly created container
// to render into. `fields` is that task's current dynamic fields (the same
// object passed to renderFields); area/thickness prefill from the sibling
// tasks via Store.allFields/Store.tasks -- the same mechanism detail-form.js/
// detail.js already use for cross-task reads.
export function renderResourceCalculator(container, projectId, task, fields) {
  wirePlotsDialogOnce();
  fields = fields || {};
  var areaFields = (Store.allFields || {})['Reservoir Area Definition'] || {};
  var thicknessFields = (Store.allFields || {})['Thickness Estimation'] || {};
  var initialAreaP90 = isFilled(areaFields.p90_area_km2) ? areaFields.p90_area_km2 : '';
  var initialAreaP10 = isFilled(areaFields.p10_area_km2) ? areaFields.p10_area_km2 : '';
  var initialThickness = isFilled(thicknessFields.reservoir_thickness_ft) ? thicknessFields.reservoir_thickness_ft : '';
  var storedValues = resultsFromStoredFields(fields);
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
    storedValues: storedValues,
    displayValues: storedValues,
    result: null
  };

  container.innerHTML = calculatorMarkup();
  hidePlotsLink(); // also drops any stale plot markup left over from a previous visit to this step

  renderScenarioOptions();
  bindMethodRadios();
  updateMethodVisibility();
  syncInputsFromState();
  renderResultsPanel();

  // Reference mode: every plain <input> (GRV/area/thickness + the scenario/
  // method radios) is already caught by loadComponent's generic
  // setComponentReferenceMode sweep (all input/select/textarea in
  // #component-form), which detail-form.js runs again right after this
  // function returns -- these fresh nodes just need to exist by then, which
  // they do. Calculate has no such generic coverage (it's a <button>, and
  // unlike Apply/View-plots it has no independent enabled-state of its own
  // to protect), so it's disabled explicitly here. Apply/View-plots need NO
  // special-casing: both already start disabled/hidden until a same-session
  // Calculate succeeds, which itself can't happen while Calculate is
  // disabled -- so they can never end up wrongly enabled in reference mode.
  byId('ra-calculate').disabled = !isCurrentPipelineView();

  bindNumberInput('ra-grv-p90', 'grvP90');
  bindNumberInput('ra-grv-p10', 'grvP10');
  bindNumberInput('ra-area-p90', 'areaP90');
  bindNumberInput('ra-area-p10', 'areaP10');
  bindNumberInput('ra-thickness-p50', 'thicknessP50');
  byId('ra-calculate').addEventListener('click', onCalculate);
  byId('ra-apply').addEventListener('click', onApply);
  byId('ra-view-plots').addEventListener('click', function () {
    byId('resource-assessment-dialog').showModal();
  });
}
