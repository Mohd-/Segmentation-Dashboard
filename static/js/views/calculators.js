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

function numberField(id, label, unit, attributes) {
  return '<label for="' + id + '"><span>' + esc(label) + '</span>' +
    '<span class="calc-input-wrap"><input id="' + id + '" type="number" step="any" ' +
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

function wireResources(root, renderGeneration) {
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

  method.addEventListener('change', function () {
    var grv = method.value === 'GRV';
    root.querySelector('#calc-resource-grv').classList.toggle('hidden', !grv);
    root.querySelector('#calc-resource-box').classList.toggle('hidden', grv);
  });
  var controls = root.querySelector('.calculator-card[data-calculator="resources"] .calc-controls');
  controls.addEventListener('input', invalidate);
  controls.addEventListener('change', invalidate);

  button.addEventListener('click', function () {
    if (button.disabled) return;
    var state = resourceState(root);
    var error = state.scenario ? validateResourceInputs(state) : 'A resource scenario must be selected.';
    requestGeneration += 1;
    var requestId = requestGeneration;
    clearResults();
    if (error) {
      showError(root, 'calc-resource-error', error);
      return;
    }
    showError(root, 'calc-resource-error', '');
    var snapshot = JSON.stringify(state);
    button.disabled = true;
    button.textContent = 'Running…';
    setBusy(resultsRegion, true);
    API.calculatorResources(buildCalculatePayload(state)).then(function (result) {
      if (!renderIsCurrent() || requestId !== requestGeneration ||
          snapshot !== JSON.stringify(resourceState(root))) return;
      results.className = 'ra-results-panel';
      results.innerHTML = buildResultsMarkup(resultsFromCalculation(result));
      renderResourcePlots(root, result);
    }).catch(function (requestError) {
      if (!renderIsCurrent() || requestId !== requestGeneration ||
          snapshot !== JSON.stringify(resourceState(root))) return;
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
  wireResources(root, renderGeneration);
  wireReservoir(root, renderGeneration);
  wireTrap(root);
  wireSeal(root);
  return true;
}
