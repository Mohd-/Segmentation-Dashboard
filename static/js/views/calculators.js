// Project-free technical calculators. Formula ownership stays in the same
// governed modules used by the lead workflow: lead-assessment.js for TWT,
// resource-calculator.js/resource_calc.py for Monte Carlo resources, cos.py
// for Reservoir CoS, and cos-rules.js (the tested cos.py mirror) for Trap/Seal.
import { byId, esc } from '../dom.js';
import { Store } from '../state.js';
import { API } from '../api.js';
import {
  coefficientsFor, thicknessFromTwt, twtFromThickness
} from './lead-assessment.js';
import {
  validateResourceInputs, buildCalculatePayload, resultsFromCalculation,
  buildResultsMarkup
} from './resource-calculator.js';
import { calculateTrapCos, calculateSealCos } from '../cos-rules.js';
import { RESOURCE_SCENARIOS } from '../schema.js';

var DEFAULT_SCENARIO = 'dry_gas_high_pressure';

// Every calculator input is a physical magnitude, so min="0" is unconditional
// here (callers that need a tighter floor pass their own min in `attributes`,
// which lands after this one and wins).
function numberField(id, label, unit, attributes) {
  return '<label for="' + id + '"><span>' + esc(label) + '</span>' +
    '<span class="calc-input-wrap"><input id="' + id + '" type="number" step="any" min="0" ' +
    (attributes || '') + '><span class="calc-unit">' + esc(unit) + '</span></span></label>';
}

function readout(id, label, unit) {
  return '<div class="calc-readout"><span>' + esc(label) + '</span><output id="' + id +
    '" role="status" aria-live="polite" aria-atomic="true">—</output>' +
    '<small>' + esc(unit) + '</small></div>';
}

function disclosure(key, mark, title, description, body) {
  return '<details class="calculator-card" data-calculator="' + key + '">' +
    '<summary><span class="calculator-mark" aria-hidden="true">' + mark + '</span>' +
      '<span class="calculator-summary-copy"><strong>' + esc(title) + '</strong><small>' + esc(description) + '</small></span>' +
      '<span class="calculator-chevron" aria-hidden="true"></span></summary>' +
    '<div class="calculator-body">' + body + '</div></details>';
}

function twtMarkup(meta) {
  var pending = false;
  var rows = ['reservoir', 'formation'].map(function (row) {
    var coefficients = coefficientsFor(meta, row);
    if (!coefficients) pending = true;
    var disabled = coefficients ? '' : 'disabled aria-describedby="calc-twt-pending"';
    var title = row === 'reservoir' ? 'Reservoir interval' : 'Formation interval';
    return '<div class="calc-conversion-row" data-twt-row="' + row + '">' +
      '<div class="calc-row-label"><strong>' + title + '</strong>' +
        (coefficients ? '<small>thickness = ' + esc(coefficients.m) + ' × TWT + ' + esc(coefficients.b) + '</small>' : '<small>Calibration unavailable</small>') +
      '</div>' +
      numberField('calc-twt-' + row, 'Two-way time', 'ms', disabled) +
      '<span class="calc-swap" aria-hidden="true">⇄</span>' +
      numberField('calc-thickness-' + row, 'Thickness', 'ft', disabled) +
    '</div>';
  }).join('');
  return '<p class="calc-guidance">Enter either side of a calibrated row; the reciprocal value updates immediately.</p>' +
    '<p id="calc-twt-pending" class="calc-notice ' + (pending ? '' : 'hidden') + '">TWT ⇄ thickness conversion pending configuration</p>' +
    '<div class="calc-conversion-grid">' + rows + '</div>';
}

function resourceScenarios(meta) {
  if (meta && Object.prototype.hasOwnProperty.call(meta, 'resource_scenarios')) {
    return Array.isArray(meta.resource_scenarios) ? meta.resource_scenarios : [];
  }
  return RESOURCE_SCENARIOS;
}

function scenarioOptions(scenarios) {
  return scenarios.map(function (scenario) {
    return '<option value="' + esc(scenario.id) + '"' + (scenario.id === DEFAULT_SCENARIO ? ' selected' : '') + '>' +
      esc(scenario.label) + '</option>';
  }).join('');
}

/* ---------------------------------------------------------------------------
   Advanced settings: the scenario's own petrophysical distributions

   Porosity, Sg, NGR, the geometric factor and 1/Bg are the approved
   per-scenario assumptions in config/scenarios.yaml. Until now they were
   invisible -- a run's answer depended on five numbers the user could not see,
   let alone question. This panel shows what the selected scenario WOULD use
   and lets one run substitute its own values.

   The rows are rendered from /api/meta's `resource_parameters`, so this file
   never carries a second copy of the numbers. Only rows the user actually
   CHANGED are sent: an untouched panel means the run is byte-identical to one
   made with the panel closed, which is what makes opening it safe.
   --------------------------------------------------------------------------- */

// The three the sampler implements. Anything else is refused server-side, so
// the select never offers it.
var ADVANCED_DISTRIBUTIONS = ['constant', 'normal', 'lognormal'];

function advancedMarkup() {
  return '<details id="calc-advanced" class="calc-advanced">' +
    '<summary>Advanced settings' +
      '<span class="calc-advanced-note">Scenario assumptions — porosity, Sg, NGR, geometric factor, 1/Bg</span>' +
      '<span class="calc-advanced-count hidden"></span></summary>' +
    '<div id="calc-advanced-rows" class="calc-advanced-rows"></div>' +
    '<p class="calc-guidance">Changes apply to the next run only. Nothing here is saved, and lead assessments always use the scenario as configured.</p>' +
    '</details>';
}

// The engine reads a normal from mean+stddev when it has both, and otherwise
// from P90/P10. The panel always speaks in percentiles -- one idea of "range"
// across all three distributions -- so an edited normal is sent as P90/P10 and
// the engine derives its moments. That is only ever applied to a row the user
// edited, so a scenario configured with explicit moments keeps them untouched.
function advancedFieldsFor(distribution) {
  if (distribution === 'constant') return [{ key: 'value', label: 'Value' }];
  return [{ key: 'p90', label: 'P90' }, { key: 'p10', label: 'P10' }];
}

function advancedDefaults(parameter) {
  var distribution = parameter.distribution || 'normal';
  var defaults = { distribution: distribution };
  advancedFieldsFor(distribution).forEach(function (field) {
    var raw = parameter[field.key];
    // A normal configured with moments only still needs a percentile pair to
    // show; the served payload carries whichever the scenario declares.
    defaults[field.key] = raw == null ? '' : String(raw);
  });
  return defaults;
}

function boundsNote(parameter) {
  var bits = [];
  if (parameter.minimum != null) bits.push('min ' + parameter.minimum);
  if (parameter.maximum != null) bits.push('max ' + parameter.maximum);
  if (!bits.length) return '';
  return '<span class="calc-adv-bounds">' + esc(bits.join(' · ')) + '</span>';
}

function advancedRowMarkup(parameter) {
  var defaults = advancedDefaults(parameter);
  var fields = advancedFieldsFor(defaults.distribution).map(function (field) {
    return '<label><span>' + esc(field.label) + '</span>' +
      '<input type="number" step="any" min="0" data-adv-field="' + esc(field.key) + '"' +
      ' value="' + esc(defaults[field.key]) + '" aria-label="' +
      esc(parameter.label + ' ' + field.label) + '"></label>';
  }).join('');
  var options = ADVANCED_DISTRIBUTIONS.map(function (name) {
    return '<option' + (name === defaults.distribution ? ' selected' : '') + '>' + name + '</option>';
  }).join('');
  return '<div class="calc-adv-row" data-adv="' + esc(parameter.name) + '">' +
    '<div class="calc-adv-head">' +
      '<span class="calc-adv-label">' + esc(parameter.label) +
        '<small>' + esc(parameter.unit || '') + '</small></span>' +
      boundsNote(parameter) +
      '<span class="calc-adv-modified hidden">modified</span>' +
      '<button type="button" class="ghost calc-adv-reset" hidden>Reset</button>' +
    '</div>' +
    '<div class="calc-adv-fields">' +
      '<label><span>Distribution</span><select data-adv-field="distribution">' + options + '</select></label>' +
      fields +
    '</div></div>';
}

function resourceMarkup(meta) {
  var scenarios = resourceScenarios(meta);
  var unavailable = scenarios.length === 0;
  var unavailableAttributes = unavailable ? ' disabled aria-describedby="calc-resource-unavailable"' : '';
  return '<div class="calc-two-pane">' +
    '<div class="calc-controls">' +
      '<p id="calc-resource-unavailable" class="calc-notice ' + (unavailable ? '' : 'hidden') + '">No configured resource scenarios are available.</p>' +
      '<div class="calc-field-grid cols-2">' +
        '<label for="calc-resource-scenario">Scenario<select id="calc-resource-scenario"' + unavailableAttributes + '>' + scenarioOptions(scenarios) + '</select></label>' +
        '<label for="calc-resource-method">Method<select id="calc-resource-method"><option>GRV</option><option>Box Model</option></select></label>' +
      '</div>' +
      '<div id="calc-resource-grv" class="calc-field-grid cols-2">' +
        numberField('calc-resource-grv-p90', 'GRV P90', '10³ acre-ft', 'value="12.60"') +
        numberField('calc-resource-grv-p10', 'GRV P10', '10³ acre-ft', 'value="17.30"') +
      '</div>' +
      '<div id="calc-resource-box" class="calc-field-grid cols-3 hidden">' +
        numberField('calc-resource-area-p90', 'Area P90', 'km²', '') +
        numberField('calc-resource-area-p10', 'Area P10', 'km²', '') +
        numberField('calc-resource-thickness', 'Thickness P50', 'ft', '') +
      '</div>' +
      advancedMarkup() +
      '<p id="calc-resource-error" class="calc-error hidden" role="alert"></p>' +
      '<button id="calc-resource-run" type="button"' + unavailableAttributes + '>Run simulation</button>' +
    '</div>' +
    '<div class="calc-results" aria-busy="false"><div class="calc-results-title">PIIP results</div>' +
      '<div id="calc-resource-results" class="calc-empty" role="status" aria-live="polite" aria-atomic="true">Run the simulation to calculate P90, mean, and P10.</div>' +
      '<div id="calc-resource-plots" class="calc-resource-plots"></div>' +
    '</div>' +
  '</div>';
}

function reservoirMarkup() {
  return '<div class="calc-two-pane"><div class="calc-controls">' +
    '<div class="calc-field-grid cols-3">' +
      numberField('calc-reservoir-amplitude', 'Amplitude ratio', 'ratio', '') +
      numberField('calc-reservoir-bts', 'Base Tight Sarah', 'score', '') +
      '<label for="calc-reservoir-pullup">Pull-up<select id="calc-reservoir-pullup"><option value="">Select…</option><option>No</option><option>Semi</option><option>Yes</option></select></label>' +
    '</div><p id="calc-reservoir-error" class="calc-error hidden" role="alert"></p>' +
    '<button id="calc-reservoir-run" type="button">Calculate Reservoir CoS</button></div>' +
    '<div class="calc-results" aria-busy="false">' + readout('calc-reservoir-result', 'Reservoir CoS', '%') + '</div></div>';
}

function trapMarkup() {
  return '<div class="calc-two-pane"><div class="calc-controls"><div class="calc-field-grid cols-2">' +
    numberField('calc-trap-sarah', 'Sarah prognosis thickness', 'ft', 'min="0"') +
    numberField('calc-trap-quwarah', 'Sarah–Quwarah thickness', 'ft', 'min="0"') +
    '</div><p class="calc-guidance">The governed threshold table updates the result as you type.</p>' +
    '<p id="calc-trap-error" class="calc-error hidden" role="alert"></p></div>' +
    '<div class="calc-results">' + readout('calc-trap-result', 'Trap CoS', '%') + '</div></div>';
}

function sealMarkup() {
  return '<div class="calc-two-pane"><div class="calc-controls"><div class="calc-field-grid cols-3">' +
    numberField('calc-seal-activity', 'Recent activity age', 'score', 'min="0" max="1" step="0.01"') +
    numberField('calc-seal-dip', 'Dip', 'score', 'min="0" max="1" step="0.01"') +
    numberField('calc-seal-azimuth', 'Azimuth vs SHmax', 'score', 'min="0" max="1" step="0.01"') +
    numberField('calc-seal-fault', 'Fault confidence', 'score', 'min="0" max="1" step="0.01"') +
    numberField('calc-seal-permeability', 'Fracture permeability', 'score', 'min="0" max="1" step="0.01"') +
    '</div><p class="calc-guidance">Scores are entered as decimal probabilities from 0 to 1. Directional terms apply when activity is 0.9 or lower.</p>' +
    '<p id="calc-seal-error" class="calc-error hidden" role="alert"></p></div>' +
    '<div class="calc-results">' + readout('calc-seal-result', 'Seal CoS', '%') + '</div></div>';
}

export function calculatorMarkup(meta) {
  return disclosure('twt', 'TWT', 'TWT ⇄ thickness', 'Convert calibrated time and thickness pairs.', twtMarkup(meta)) +
    disclosure('resources', 'MC', 'Monte Carlo resources', 'Estimate gas and condensate PIIP percentiles.', resourceMarkup(meta)) +
    disclosure('reservoir', 'R', 'Reservoir CoS', 'Score reservoir chance using the approved RF model.', reservoirMarkup()) +
    disclosure('trap', 'T', 'Trap CoS', 'Evaluate trap closure from Sarah thicknesses.', trapMarkup()) +
    disclosure('seal', 'S', 'Seal CoS', 'Combine activity, orientation, confidence, and permeability.', sealMarkup());
}

function value(root, id) { return root.querySelector('#' + id).value; }

function showError(root, id, message) {
  var element = root.querySelector('#' + id);
  element.textContent = message || '';
  element.classList.toggle('hidden', !message);
}

function blank(value) { return String(value == null ? '' : value).trim() === ''; }

function finiteNumber(value) {
  if (blank(value)) return null;
  var number = Number(value);
  return isFinite(number) ? number : null;
}

function setBusy(element, busy) {
  element.setAttribute('aria-busy', busy ? 'true' : 'false');
}

function wireTwt(root, meta) {
  ['reservoir', 'formation'].forEach(function (row) {
    var coefficients = coefficientsFor(meta, row);
    if (!coefficients) return;
    var twt = root.querySelector('#calc-twt-' + row);
    var thickness = root.querySelector('#calc-thickness-' + row);
    twt.addEventListener('input', function () { thickness.value = thicknessFromTwt(coefficients, twt.value); });
    thickness.addEventListener('input', function () { twt.value = twtFromThickness(coefficients, thickness.value); });
  });
}

function resourceState(root) {
  return {
    scenario: value(root, 'calc-resource-scenario'),
    method: value(root, 'calc-resource-method'),
    grvP90: value(root, 'calc-resource-grv-p90'),
    grvP10: value(root, 'calc-resource-grv-p10'),
    areaP90: value(root, 'calc-resource-area-p90'),
    areaP10: value(root, 'calc-resource-area-p10'),
    thicknessP50: value(root, 'calc-resource-thickness')
  };
}

// A result computed on substituted assumptions is not the scenario's answer,
// and the panel that changed them may be collapsed by the time it lands. The
// server reports back which parameters it actually overrode (never the
// client's own idea of it), so the banner cannot drift from what was run.
function renderOverrideNotice(root, result) {
  var results = root.querySelector('#calc-resource-results');
  var names = (result && result.overridden_inputs) || [];
  if (!names.length) return;
  var labels = names.map(function (name) {
    var row = root.querySelector('.calc-adv-row[data-adv="' + name + '"] .calc-adv-label');
    return row ? row.firstChild.textContent.trim() : name;
  });
  results.insertAdjacentHTML('afterbegin',
    '<p class="calc-override-banner">Run with overridden assumptions: ' + esc(labels.join(', ')) + '</p>');
}

function renderResourcePlots(root, result) {
  var plots = root.querySelector('#calc-resource-plots');
  plots.innerHTML = '';
  [['gas', 'Gas exceedance plot'], ['condensate', 'Condensate exceedance plot']].forEach(function (entry) {
    var src = result.plots && result.plots[entry[0]];
    if (!src) return;
    var img = document.createElement('img');
    img.src = src;
    img.alt = entry[1];
    plots.appendChild(img);
  });
}

/* The Advanced panel's live state, rebuilt whenever the scenario changes.
   `defaults` is what the scenario configures; the controls start there, and a
   row counts as OVERRIDDEN only once its values differ from it. */
function advancedController(root, meta) {
  var host = root.querySelector('#calc-advanced-rows');
  var details = root.querySelector('#calc-advanced');
  var parametersByScenario = (meta && meta.resource_parameters) || {};
  var defaults = {};

  function rows() { return Array.prototype.slice.call(host.querySelectorAll('.calc-adv-row')); }

  function readRow(row) {
    var spec = {};
    Array.prototype.forEach.call(row.querySelectorAll('[data-adv-field]'), function (control) {
      spec[control.getAttribute('data-adv-field')] = control.value;
    });
    return spec;
  }

  function isModified(row) {
    var name = row.getAttribute('data-adv');
    var current = readRow(row);
    var base = defaults[name] || {};
    return Object.keys(current).some(function (key) {
      return String(current[key]) !== String(base[key] == null ? '' : base[key]);
    });
  }

  // Re-render a row's numeric inputs when its distribution changes: a constant
  // takes one value, the other two take a percentile pair, and leaving the old
  // inputs behind would send fields the chosen distribution has no use for
  // (which the server refuses by name).
  function refreshFields(row) {
    var name = row.getAttribute('data-adv');
    var parameter = (currentParameters() || []).filter(function (p) { return p.name === name; })[0];
    if (!parameter) return;
    var distribution = row.querySelector('[data-adv-field="distribution"]').value;
    var base = defaults[name] || {};
    var fields = advancedFieldsFor(distribution).map(function (field) {
      // Keep the configured value where the new shape still has that field.
      var value = base[field.key] == null ? '' : base[field.key];
      return '<label><span>' + esc(field.label) + '</span>' +
        '<input type="number" step="any" min="0" data-adv-field="' + esc(field.key) + '"' +
        ' value="' + esc(value) + '" aria-label="' + esc(parameter.label + ' ' + field.label) + '"></label>';
    }).join('');
    var container = row.querySelector('.calc-adv-fields');
    var select = container.querySelector('label');
    container.innerHTML = '';
    container.appendChild(select);
    container.insertAdjacentHTML('beforeend', fields);
  }

  function markRow(row) {
    var modified = isModified(row);
    row.classList.toggle('is-modified', modified);
    row.querySelector('.calc-adv-modified').classList.toggle('hidden', !modified);
    row.querySelector('.calc-adv-reset').hidden = !modified;
  }

  function sync() {
    var modifiedCount = rows().filter(isModified).length;
    rows().forEach(markRow);
    if (details) details.classList.toggle('has-overrides', modifiedCount > 0);
    var note = details && details.querySelector('.calc-advanced-count');
    if (note) {
      note.textContent = modifiedCount ? modifiedCount + ' overridden' : '';
      note.classList.toggle('hidden', !modifiedCount);
    }
  }

  function currentParameters() {
    var selected = root.querySelector('#calc-resource-scenario');
    return parametersByScenario[selected ? selected.value : ''] || [];
  }

  function render() {
    var parameters = currentParameters();
    defaults = {};
    parameters.forEach(function (parameter) { defaults[parameter.name] = advancedDefaults(parameter); });
    host.innerHTML = parameters.length
      ? parameters.map(advancedRowMarkup).join('')
      : '<p class="calc-empty">This scenario publishes no adjustable parameters.</p>';
    applyMethodVisibility();
    sync();
  }

  // The geometric factor only participates in the Box Model, so it is hidden
  // (not disabled and not sent) while GRV is the selected method.
  function applyMethodVisibility() {
    var methodSelect = root.querySelector('#calc-resource-method');
    var boxModel = methodSelect && methodSelect.value === 'Box Model';
    currentParameters().forEach(function (parameter) {
      if (parameter.method !== 'area_thickness') return;
      var row = host.querySelector('.calc-adv-row[data-adv="' + parameter.name + '"]');
      if (row) row.classList.toggle('hidden', !boxModel);
    });
  }

  // Only MODIFIED, visible rows travel. An untouched panel sends nothing, so
  // the run is identical to one made with the panel never opened.
  function payload() {
    var overrides = {};
    rows().forEach(function (row) {
      if (row.classList.contains('hidden') || !isModified(row)) return;
      var spec = readRow(row);
      var entry = { distribution: spec.distribution };
      Object.keys(spec).forEach(function (key) {
        if (key !== 'distribution' && spec[key] !== '') entry[key] = Number(spec[key]);
      });
      overrides[row.getAttribute('data-adv')] = entry;
    });
    return Object.keys(overrides).length ? overrides : null;
  }

  // Client-side sanity so the common mistakes are named before a round trip;
  // resource_engine/overrides.py remains the authority.
  function validate() {
    var message = null;
    rows().forEach(function (row) {
      if (message || row.classList.contains('hidden') || !isModified(row)) return;
      var label = row.querySelector('.calc-adv-label').firstChild.textContent.trim();
      var spec = readRow(row);
      var numbers = {};
      Object.keys(spec).forEach(function (key) {
        if (key === 'distribution') return;
        if (spec[key] === '') { message = message || (label + ': ' + key.toUpperCase() + ' is required.'); return; }
        var parsed = Number(spec[key]);
        if (isNaN(parsed)) { message = message || (label + ': ' + key.toUpperCase() + ' must be numeric.'); return; }
        if (parsed < 0) { message = message || (label + ': ' + key.toUpperCase() + ' must not be negative.'); return; }
        numbers[key] = parsed;
      });
      if (!message && numbers.p90 != null && numbers.p10 != null && numbers.p90 >= numbers.p10) {
        message = label + ': P90 must be lower than P10.';
      }
    });
    return message;
  }

  host.addEventListener('change', function (event) {
    var control = event.target;
    if (control.getAttribute && control.getAttribute('data-adv-field') === 'distribution') {
      refreshFields(control.closest('.calc-adv-row'));
    }
    sync();
  });
  host.addEventListener('input', sync);
  host.addEventListener('click', function (event) {
    var reset = event.target.closest && event.target.closest('.calc-adv-reset');
    if (!reset) return;
    var row = reset.closest('.calc-adv-row');
    var name = row.getAttribute('data-adv');
    var base = defaults[name] || {};
    row.querySelector('[data-adv-field="distribution"]').value = base.distribution;
    refreshFields(row);
    Object.keys(base).forEach(function (key) {
      var control = row.querySelector('[data-adv-field="' + key + '"]');
      if (control) control.value = base[key];
    });
    sync();
  });

  render();
  return { render: render, payload: payload, validate: validate, applyMethodVisibility: applyMethodVisibility };
}

function wireResources(root, renderGeneration, meta) {
  var method = root.querySelector('#calc-resource-method');
  var scenario = root.querySelector('#calc-resource-scenario');
  var button = root.querySelector('#calc-resource-run');
  var results = root.querySelector('#calc-resource-results');
  var plots = root.querySelector('#calc-resource-plots');
  var resultsRegion = results.closest('.calc-results');
  var scenariosUnavailable = scenario.options.length === 0;
  var requestGeneration = 0;

  function renderIsCurrent() {
    return root.__calculatorRenderGeneration === renderGeneration;
  }

  function clearResults() {
    results.className = 'calc-empty';
    results.textContent = 'Run the simulation to calculate P90, mean, and P10.';
    plots.textContent = '';
  }

  function invalidate() {
    requestGeneration += 1;
    clearResults();
    showError(root, 'calc-resource-error', '');
    setBusy(resultsRegion, false);
    button.disabled = scenariosUnavailable;
    button.textContent = 'Run simulation';
  }

  var advanced = advancedController(root, meta);

  method.addEventListener('change', function () {
    var grv = method.value === 'GRV';
    root.querySelector('#calc-resource-grv').classList.toggle('hidden', !grv);
    root.querySelector('#calc-resource-box').classList.toggle('hidden', grv);
    advanced.applyMethodVisibility();
  });
  // A different scenario means different approved assumptions, so the panel is
  // rebuilt from that scenario's own values -- and any override in flight is
  // dropped with the rows it belonged to.
  scenario.addEventListener('change', function () { advanced.render(); });
  var controls = root.querySelector('.calculator-card[data-calculator="resources"] .calc-controls');
  controls.addEventListener('input', invalidate);
  controls.addEventListener('change', invalidate);

  button.addEventListener('click', function () {
    if (button.disabled) return;
    var state = resourceState(root);
    var error = state.scenario ? validateResourceInputs(state) : 'A resource scenario must be selected.';
    if (!error) error = advanced.validate();
    requestGeneration += 1;
    var requestId = requestGeneration;
    clearResults();
    if (error) {
      showError(root, 'calc-resource-error', error);
      return;
    }
    showError(root, 'calc-resource-error', '');
    var overrides = advanced.payload();
    // The staleness guard covers the advanced rows too: editing one after
    // pressing Run must discard the in-flight answer, not label it.
    var snapshot = JSON.stringify([state, overrides]);
    var currentSnapshot = function () {
      return JSON.stringify([resourceState(root), advanced.payload()]);
    };
    button.disabled = true;
    button.textContent = 'Running…';
    setBusy(resultsRegion, true);
    var body = buildCalculatePayload(state);
    if (overrides) body.overrides = overrides;
    API.calculatorResources(body).then(function (result) {
      if (!renderIsCurrent() || requestId !== requestGeneration || snapshot !== currentSnapshot()) return;
      results.className = 'ra-results-panel';
      results.innerHTML = buildResultsMarkup(resultsFromCalculation(result));
      renderOverrideNotice(root, result);
      renderResourcePlots(root, result);
    }).catch(function (requestError) {
      if (!renderIsCurrent() || requestId !== requestGeneration || snapshot !== currentSnapshot()) return;
      clearResults();
      showError(root, 'calc-resource-error', requestError.message);
    }).finally(function () {
      if (!renderIsCurrent() || requestId !== requestGeneration) return;
      setBusy(resultsRegion, false);
      button.disabled = scenariosUnavailable;
      button.textContent = 'Run simulation';
    });
  });
}

function wireReservoir(root, renderGeneration) {
  var button = root.querySelector('#calc-reservoir-run');
  var output = root.querySelector('#calc-reservoir-result');
  var resultsRegion = output.closest('.calc-results');
  var requestGeneration = 0;

  function renderIsCurrent() {
    return root.__calculatorRenderGeneration === renderGeneration;
  }

  function invalidate() {
    requestGeneration += 1;
    output.textContent = '—';
    showError(root, 'calc-reservoir-error', '');
    setBusy(resultsRegion, false);
    button.disabled = false;
    button.textContent = 'Calculate Reservoir CoS';
  }

  root.querySelector('.calculator-card[data-calculator="reservoir"] .calc-controls')
    .addEventListener('input', invalidate);
  root.querySelector('.calculator-card[data-calculator="reservoir"] .calc-controls')
    .addEventListener('change', invalidate);

  button.addEventListener('click', function () {
    if (button.disabled) return;
    var payload = {
      amplitude_ratio: value(root, 'calc-reservoir-amplitude'),
      base_tight_sarah: value(root, 'calc-reservoir-bts'),
      pull_up: value(root, 'calc-reservoir-pullup')
    };
    requestGeneration += 1;
    var requestId = requestGeneration;
    output.textContent = '—';
    if (blank(payload.amplitude_ratio) || blank(payload.base_tight_sarah) || !payload.pull_up) {
      showError(root, 'calc-reservoir-error', 'Amplitude ratio, Base Tight Sarah, and Pull-up are required.');
      return;
    }
    if (finiteNumber(payload.amplitude_ratio) === null || finiteNumber(payload.base_tight_sarah) === null) {
      showError(root, 'calc-reservoir-error', 'Amplitude ratio and Base Tight Sarah must be finite numbers.');
      return;
    }
    showError(root, 'calc-reservoir-error', '');
    var snapshot = JSON.stringify(payload);
    button.disabled = true;
    button.textContent = 'Calculating…';
    setBusy(resultsRegion, true);
    API.calculatorReservoirCos(payload).then(function (rows) {
      if (!renderIsCurrent() || requestId !== requestGeneration) return;
      var currentPayload = {
        amplitude_ratio: value(root, 'calc-reservoir-amplitude'),
        base_tight_sarah: value(root, 'calc-reservoir-bts'),
        pull_up: value(root, 'calc-reservoir-pullup')
      };
      if (snapshot !== JSON.stringify(currentPayload)) return;
      var row = Array.isArray(rows) ? rows[0] : rows;
      var result = row && row.reservoir_cos_pct;
      if (result === null || result === undefined || result === '') {
        throw new Error('Reservoir CoS result was not returned.');
      }
      output.textContent = result;
    }).catch(function (requestError) {
      if (!renderIsCurrent() || requestId !== requestGeneration) return;
      output.textContent = '—';
      showError(root, 'calc-reservoir-error', requestError.message);
    }).finally(function () {
      if (!renderIsCurrent() || requestId !== requestGeneration) return;
      setBusy(resultsRegion, false);
      button.disabled = false;
      button.textContent = 'Calculate Reservoir CoS';
    });
  });
}

function wireTrap(root) {
  function update() {
    var sarah = value(root, 'calc-trap-sarah');
    var quwarah = value(root, 'calc-trap-quwarah');
    var output = root.querySelector('#calc-trap-result');
    if (blank(sarah) && blank(quwarah)) {
      output.textContent = '—';
      showError(root, 'calc-trap-error', '');
      return;
    }
    if (blank(sarah) || blank(quwarah)) {
      output.textContent = '—';
      showError(root, 'calc-trap-error', 'Both thickness inputs are required.');
      return;
    }
    if (finiteNumber(sarah) === null || finiteNumber(quwarah) === null ||
        finiteNumber(sarah) <= 0 || finiteNumber(quwarah) <= 0) {
      output.textContent = '—';
      showError(root, 'calc-trap-error', 'Thickness inputs must be positive finite numbers.');
      return;
    }
    showError(root, 'calc-trap-error', '');
    output.textContent = calculateTrapCos(sarah, quwarah) || '—';
  }
  root.querySelector('#calc-trap-sarah').addEventListener('input', update);
  root.querySelector('#calc-trap-quwarah').addEventListener('input', update);
}

function wireSeal(root) {
  var ids = ['activity', 'dip', 'azimuth', 'fault', 'permeability'];
  function update() {
    var fields = {
      seal_recent_activity_age: value(root, 'calc-seal-activity'),
      seal_dip: value(root, 'calc-seal-dip'),
      seal_azimuth_vs_shmax: value(root, 'calc-seal-azimuth'),
      seal_fault_level_confidence: value(root, 'calc-seal-fault'),
      seal_fracture_permeability: value(root, 'calc-seal-permeability')
    };
    var inputs = Object.keys(fields).map(function (key) { return fields[key]; });
    var output = root.querySelector('#calc-seal-result');
    if (inputs.every(blank)) {
      output.textContent = '—';
      showError(root, 'calc-seal-error', '');
      return;
    }
    var invalid = inputs.some(function (raw) {
      if (blank(raw)) return false;
      var number = finiteNumber(raw);
      return number === null || number < 0 || number > 1;
    });
    if (invalid) {
      output.textContent = '—';
      showError(root, 'calc-seal-error', 'Seal scores must be finite numbers from 0 to 1.');
      return;
    }
    var activity = finiteNumber(fields.seal_recent_activity_age);
    if (activity === null || finiteNumber(fields.seal_fracture_permeability) === null) {
      output.textContent = '—';
      showError(root, 'calc-seal-error', 'Recent activity age and Fracture permeability are required.');
      return;
    }
    if (activity <= 0.9 && (blank(fields.seal_dip) || blank(fields.seal_azimuth_vs_shmax) ||
        blank(fields.seal_fault_level_confidence))) {
      output.textContent = '—';
      showError(root, 'calc-seal-error', 'Dip, Azimuth vs SHmax, and Fault confidence are required when activity is 0.9 or lower.');
      return;
    }
    showError(root, 'calc-seal-error', '');
    var result = calculateSealCos(fields);
    output.textContent = result === null || result === '' ? '—' : result;
  }
  ids.forEach(function (id) { root.querySelector('#calc-seal-' + id).addEventListener('input', update); });
}

export function initCalculators(container) {
  var root = container || byId('calculator-workbench');
  if (!root) return false;
  var meta = Store.meta || {};
  var renderGeneration = (root.__calculatorRenderGeneration || 0) + 1;
  root.__calculatorRenderGeneration = renderGeneration;
  root.innerHTML = calculatorMarkup(meta);
  wireTwt(root, meta);
  wireResources(root, renderGeneration, meta);
  wireReservoir(root, renderGeneration);
  wireTrap(root);
  wireSeal(root);
  return true;
}
