import { byId, all, esc, isFilled, truthy, msg } from '../dom.js';
import { API } from '../api.js';
import { currentUserName, currentRole, canManageAssignments, Store } from '../state.js';
import { SCHEMA, FORMATIONS, FORMATION_METRICS } from '../schema.js';
import { confirmDialog } from '../dialog.js';
import { renderDetail, renderRightPanel, chooseInitialTask, tasksForPipeline, parseRepeatableRows, refreshAfterRecordChange } from './detail.js';
import { refreshAllBoards } from './pipeline.js';

export function ensureUsers() {
  if (Store.users) return Promise.resolve(Store.users);
  return API.users().then(function (users) {
    Store.users = users || [];
    return Store.users;
  }).catch(function () { return []; });
}

function renderStatusChip(status) {
  var chip = byId('component-status-chip');
  if (!chip) return;
  var value = status || 'Not Assigned';
  chip.textContent = value;
  chip.className = 'status editor-status-chip ' + String(value).toLowerCase().replace(/\s+/g, '-');
}

var PRIORITY_CYCLE = { Low: 'Medium', Medium: 'High', High: 'Low' };

function renderPriorityChip(task) {
  var chip = byId('component-priority-chip');
  if (!chip) return;
  var value = task.priority || 'Medium';
  chip.textContent = value;
  chip.className = 'priority editor-priority-chip priority-' + String(value).toLowerCase();
  chip.title = 'Priority: ' + value + ' — click to change';
}

// Cycles Low -> Medium -> High -> Low via the dedicated priority endpoint
// (PATCH /api/tasks/<id>/priority; no revision check server-side), then
// refreshes so the chip and boards adopt the new value + task revision.
export function cyclePriorityChip() {
  if (!Store.task) return;
  var next = PRIORITY_CYCLE[Store.task.priority || 'Medium'] || 'Medium';
  API.priority(Store.task.task_id, { priority: next, changed_by: currentUserName() })
    .then(function () { return refreshAfterRecordChange('Priority set to ' + next + '.'); })
    .catch(function (error) { msg(error.message, 'error'); });
}

function renderAssigneeSelect(task) {
  var select = byId('assigned-to');
  if (!select) return;
  ensureUsers().then(function (users) {
    var current = task.assigned_to || '';
    var names = users.map(function (user) { return user.name; });
    // Keep a legacy/deactivated assignee visible even if no longer selectable
    // as a new choice.
    if (current && names.indexOf(current) < 0) names.push(current);
    select.innerHTML = '<option value="">Unassigned</option>' + names.map(function (name) {
      return '<option ' + (name === current ? 'selected' : '') + '>' + esc(name) + '</option>';
    }).join('');
    select.value = current;
    // Only supervisors/staff assign (anonymous dev mode acts as supervisor,
    // matching the backend's current_role()).
    select.disabled = !canManageAssignments();
  });
}

function renderActionButtons(task) {
  var status = task.status || 'Not Assigned';
  var role = currentRole();
  var manage = canManageAssignments();
  var isAssignee = !!(Store.user && Store.user.name &&
    String(Store.user.name).toLowerCase() === String(task.assigned_to || '').toLowerCase());
  var submitButton = byId('submit-component');
  var approveButton = byId('approve-component');
  var returnButton = byId('return-component');
  if (submitButton) submitButton.classList.toggle('hidden', !(status === 'In Progress' && (manage || isAssignee)));
  if (approveButton) approveButton.classList.toggle('hidden', !(status === 'Ready' && role === 'supervisor'));
  if (returnButton) returnButton.classList.toggle('hidden', !(status === 'Ready' && role === 'supervisor'));
}

export function loadComponent(task) {
  if (!task) return;
  Store.task = task;
  all('.component-item').forEach(function (button) { button.classList.toggle('active', Number(button.getAttribute('data-task-id')) === task.task_id); });
  byId('component-number').textContent = String(task.sequence_no || '');
  byId('component-title').textContent = task.task_name;
  renderStatusChip(task.status);
  renderAssigneeSelect(task);
  renderActionButtons(task);
  renderPriorityChip(task);
  byId('comments').placeholder = commentPlaceholder(task.task_name);
  byId('comments').value = task.comments || '';
  Promise.all([API.fields(task.task_id), API.componentFolder(Store.projectId, task.task_id)]).then(function (results) {
    renderFields(task.task_name, results[0] || {});
    renderComponentFolder(results[1] || {});
    renderRightPanel(tasksForPipeline(Store.pipeline));
  }).catch(function (error) { msg(error.message, 'error'); });
}

// Assignment posts immediately on select change (not deferred to Save): the
// confirm dialog decides whether the same assignee also cascades to every
// later still-unassigned step of this pipeline.
export function assignComponent() {
  if (!Store.task) return;
  var select = byId('assigned-to');
  var assignee = select.value;
  var previous = Store.task.assigned_to || '';
  if (!assignee || assignee === previous) {
    select.value = previous; // clearing is not an /assign action; snap back
    return;
  }
  confirmDialog({
    title: 'Assign component',
    message: 'Assign remaining steps to ' + assignee + ' as well?',
    confirmLabel: 'Yes, assign following steps',
    cancelLabel: 'Only this step'
  }).then(function (cascade) {
    return API.assign(Store.task.task_id, {
      assignee: assignee,
      cascade: !!cascade,
      revision: Store.task.revision,
      changed_by: currentUserName()
    });
  }).then(function () {
    return refreshAfterRecordChange('Component assigned to ' + assignee + '.');
  }).catch(function (error) {
    select.value = previous;
    msg(error.message, 'error');
  });
}

var TRANSITION_MESSAGES = {
  submit: 'Component submitted for approval.',
  approve: 'Component approved.',
  return: 'Component returned for update.'
};

export function transitionComponent(action) {
  if (!Store.task) return;
  API.transition(Store.task.task_id, {
    action: action,
    revision: Store.task.revision,
    changed_by: currentUserName()
  }).then(function () {
    return refreshAfterRecordChange(TRANSITION_MESSAGES[action] || 'Component updated.');
  }).catch(function (error) { msg(error.message, 'error'); });
}
export function commentPlaceholder(componentName) {
  if (componentName === 'Approval To Drill') return 'Include the requirement for the Approval to Drill letter';
  return 'Comments, assumptions, rationale, or required notes...';
}
// ---------------------------------------------------------------------------
// Formations mini-sheet (type: 'formations')
// ---------------------------------------------------------------------------
// Well-level formation values (Store.formations) edited per phase through a
// formation dropdown + one aligned sheet of metric inputs. Edits are kept
// per-formation in a local buffer (write-through on input) and PUT to
// /api/projects/<id>/formations on Save when the phase was touched.

var formationEdits = {}; // phase -> { SARH: {metric: value}, ... }
var formationDirty = {}; // phase -> true when any input changed since load

function seedFormationEdits(phase) {
  var buffer = {};
  FORMATIONS.forEach(function (name) {
    var saved = (Store.formations || []).find(function (row) {
      return row.phase === phase && row.formation === name;
    }) || {};
    var values = {};
    FORMATION_METRICS.forEach(function (metric) {
      values[metric.key] = saved[metric.key] == null ? '' : String(saved[metric.key]);
    });
    buffer[name] = values;
  });
  formationEdits[phase] = buffer;
  formationDirty[phase] = false;
}

export function formationRowsForSave(phase) {
  var buffer = formationEdits[phase] || {};
  return FORMATIONS.map(function (name) {
    return Object.assign({ formation: name }, buffer[name] || {});
  });
}

function renderFormationsField(field) {
  var phase = field.phase || 'quicklook';
  seedFormationEdits(phase);
  var picker = '<select data-formation-picker aria-label="Formation">' + FORMATIONS.map(function (name) {
    return '<option>' + esc(name) + '</option>';
  }).join('') + '</select>';
  var sheet = FORMATION_METRICS.map(function (metric) {
    if (metric.type === 'select') {
      return '<label>' + esc(metric.label) + '<select data-formation-metric="' + esc(metric.key) + '">' +
        (metric.options || []).map(function (option) { return '<option>' + esc(option) + '</option>'; }).join('') +
        '</select></label>';
    }
    return '<label>' + esc(metric.label) + '<input type="number" step="any" data-formation-metric="' + esc(metric.key) + '"></label>';
  }).join('');
  return '<div class="repeatable-field wide-field formations-field" data-formations-phase="' + esc(phase) + '" data-current-formation="' + esc(FORMATIONS[0]) + '">' +
    '<div class="repeatable-heading"><b>' + esc(field.label) + '</b>' + picker + '</div>' +
    '<div class="repeatable-row formation-sheet">' + sheet + '</div></div>';
}

function loadFormationIntoInputs(container, formation) {
  var phase = container.getAttribute('data-formations-phase');
  var values = (formationEdits[phase] || {})[formation] || {};
  all('[data-formation-metric]', container).forEach(function (element) {
    element.value = values[element.getAttribute('data-formation-metric')] || '';
  });
  container.setAttribute('data-current-formation', formation);
}

function bindFormationFields() {
  all('.formations-field', byId('dynamic-fields')).forEach(function (container) {
    var phase = container.getAttribute('data-formations-phase');
    var picker = container.querySelector('[data-formation-picker]');
    loadFormationIntoInputs(container, picker.value || FORMATIONS[0]);
    picker.addEventListener('change', function () {
      // Write-through editing keeps the buffer current, so switching just
      // loads the chosen formation's values.
      loadFormationIntoInputs(container, picker.value);
    });
    all('[data-formation-metric]', container).forEach(function (element) {
      function sync() {
        var current = container.getAttribute('data-current-formation');
        formationEdits[phase][current][element.getAttribute('data-formation-metric')] = element.value;
        formationDirty[phase] = true;
      }
      element.addEventListener('input', sync);
      element.addEventListener('change', sync);
    });
  });
}

export function renderFields(componentName, values) {
  var fields = SCHEMA[componentName] || [];
  var html = '';
  fields.forEach(function (field) {
    // Precedence: saved value ?? Store.project[field.defaultFrom] ?? field.value ?? ''.
    // defaultFrom prefills from a project column (e.g. Staking well X/Y from
    // lead_x/lead_y); the prefill persists as a normal dynamic field on save.
    var fallback = field.value || '';
    if (field.defaultFrom && Store.project && Store.project[field.defaultFrom] != null) {
      fallback = Store.project[field.defaultFrom];
    }
    var value = values[field.key] != null ? values[field.key] : fallback;
    var hidden = field.showIf && !truthy(values[field.showIf]);
    var classes = (hidden ? ' conditional hidden' : ' conditional') + (field.type === 'text' ? ' wide-field' : '');
    if (field.type === 'formations') {
      html += renderFormationsField(field);
    } else if (field.type === 'repeatable') {
      html += renderRepeatableField(field, value);
    } else if (field.readonly) {
      html += '<label class="calculated-output' + classes + '" data-show-if="' + esc(field.showIf || '') + '">' + esc(field.label) + '<output>' + (isFilled(value) ? esc(value) + '%' : 'Calculated on save') + '</output></label>';
    } else if (field.type === 'select') {
      html += '<label class="' + classes + '" data-show-if="' + esc(field.showIf || '') + '">' + esc(field.label) + '<select data-field="' + esc(field.key) + '">' + (field.options || []).map(function (option) { return '<option ' + (String(value) === String(option) ? 'selected' : '') + '>' + esc(option) + '</option>'; }).join('') + '</select></label>';
    } else if (field.type === 'checkbox') {
      html += '<label class="check-label' + classes + '" data-show-if="' + esc(field.showIf || '') + '"><input type="checkbox" data-field="' + esc(field.key) + '" ' + (truthy(value) ? 'checked' : '') + '> ' + esc(field.label) + '</label>';
    } else if (field.type === 'text') {
      html += '<label class="' + classes + '" data-show-if="' + esc(field.showIf || '') + '">' + esc(field.label) + '<input data-field="' + esc(field.key) + '" value="' + esc(value) + '"></label>';
    } else if (field.type === 'link') {
      html += '<div class="summary-box' + classes + '"><b>' + esc(field.label) + '</b><p><a href="' + esc(field.value || '#') + '" target="_blank" rel="noreferrer">' + esc(field.linkText || 'New Request') + '</a></p></div>';
    } else if (field.type === 'summary') {
      var summaryHtml = autoSummaryHtml(componentName);
      if (summaryHtml) html += '<div class="summary-box' + classes + '">' + summaryHtml + '</div>';
    } else {
      html += '<label class="' + classes + '" data-show-if="' + esc(field.showIf || '') + '">' + esc(field.label) + '<input type="number" step="any" data-field="' + esc(field.key) + '" value="' + esc(value) + '"></label>';
    }
  });
  byId('dynamic-fields').innerHTML = html;
  all('[data-field], [data-repeatable-input]', byId('dynamic-fields')).forEach(function (element) {
    function syncPreview() {
      updateConditionalVisibility();
      previewSummaryInputs();
    }
    element.addEventListener('change', syncPreview);
    element.addEventListener('input', syncPreview);
  });
  bindRepeatableFields();
  bindFormationFields();
  updateConditionalVisibility();
}
export function val(component, key) {
  var value = ((Store.allFields || {})[component] || {})[key];
  return isFilled(value) ? value : '';
}
// Latest mean-PIIP precedence (newest assessment first). Shared with the well
// summary in detail.js -- keep the two lists in sync.
export var LATEST_PIIP_SOURCES = [
  ['Resource Assessment Update', 'resource_update_gas_mean'],
  ['Post-Drilling Resource Assessment', 'post_drill_piip_gas_mean'],
  ['Pre-Drilling Resource Assessment', 'pre_drill_piip_gas_mean'],
  ['Lead Resource Assessment', 'lead_piip_gas_mean']
];

export function autoSummaryHtml(componentName) {
  if (componentName !== 'Resource Assessment Update') return '';
  var rows = [];
  function add(label, value) {
    if (isFilled(value)) rows.push('<li><span>' + esc(label) + '</span><b>' + esc(value) + '</b></li>');
  }
  add('Dynamic OGIP (BCF)', val('Flowback Results', 'flowback_dynamic_ogip_bcf'));
  // Same latest-first precedence as the well summary.
  for (var i = 0; i < LATEST_PIIP_SOURCES.length; i += 1) {
    var latest = val(LATEST_PIIP_SOURCES[i][0], LATEST_PIIP_SOURCES[i][1]);
    if (isFilled(latest)) {
      add('Latest Mean PIIP Gas (BCF) — ' + LATEST_PIIP_SOURCES[i][0], latest);
      break;
    }
  }
  return rows.length ? '<ul class="summary-list">' + rows.join('') + '</ul>' : '';
}

export function renderComponentFolder(info) {
  var previous = byId('component-folder-card');
  if (previous) previous.remove();
  if (!info || !Number(info.requires_folder)) return;
  var path = info.unc_path || 'Folder path placeholder not configured.';
  var card = document.createElement('div');
  card.id = 'component-folder-card';
  card.className = 'folder-card';
  card.innerHTML = '<span class="folder-glyph" aria-hidden="true">📁</span>' +
    '<span class="folder-path" title="' + esc(path) + '">' + esc(path) + '</span>' +
    '<button type="button" class="icon-btn" id="copy-component-folder" title="Copy folder link" aria-label="Copy folder link">⧉</button>';
  // Comments-above-file-location: the card sits directly after the comments
  // field instead of after the dynamic-fields grid.
  var anchor = byId('comments-field');
  anchor.parentNode.insertBefore(card, anchor.nextSibling);
  byId('copy-component-folder').addEventListener('click', function () { copyText(path); });
}
export function updateConditionalVisibility() {
  var fields = getFields();
  all('[data-show-if]', byId('dynamic-fields')).forEach(function (element) {
    var key = element.getAttribute('data-show-if');
    if (key) element.classList.toggle('hidden', !truthy(fields[key]));
  });
}
export function getFields() {
  var fields = {};
  all('[data-field]', byId('dynamic-fields')).forEach(function (element) {
    fields[element.getAttribute('data-field')] = element.type === 'checkbox' ? (element.checked ? '1' : '') : element.value;
  });
  all('[data-repeatable]', byId('dynamic-fields')).forEach(function (container) {
    var key = container.getAttribute('data-repeatable');
    var rows = [];
    all('.repeatable-row', container).forEach(function (row) {
      var data = {};
      all('[data-repeatable-input]', row).forEach(function (element) {
        data[element.getAttribute('data-repeatable-column')] = element.value;
      });
      if (Object.keys(data).some(function (column) { return isFilled(data[column]); })) rows.push(data);
    });
    fields[key] = JSON.stringify(rows);
  });
  return fields;
}
export function previewSummaryInputs() {
  if (!Store.task) return;
  var saved = Store.allFields[Store.task.task_name] || {};
  Store.allFields[Store.task.task_name] = Object.assign({}, saved, getFields());
  renderRightPanel(tasksForPipeline(Store.pipeline));
}
export function copyText(text) {
  if (!text) return msg('No folder path to copy.', 'error');
  if (navigator.clipboard) {
    navigator.clipboard.writeText(text).then(function () { msg('Folder link copied.', 'success'); }).catch(function () { fallbackCopy(text); });
  } else {
    fallbackCopy(text);
  }
}
export function fallbackCopy(text) {
  var area = document.createElement('textarea');
  area.value = text;
  document.body.appendChild(area);
  area.select();
  document.execCommand('copy');
  area.remove();
  msg('Folder link copied.', 'success');
}
export function saveComponent(event) {
  event.preventDefault();
  if (!Store.task) return;
  var fields = getFields();
  // A component with a formations mini-sheet also PUTs the touched phase's
  // well-level rows alongside the dynamic-field save.
  var formationsField = (SCHEMA[Store.task.task_name] || []).find(function (item) { return item.type === 'formations'; });
  var submitButton = event.target.querySelector('button[type="submit"]');
  if (submitButton) submitButton.disabled = true;
  // No status / assigned_to keys: Save only persists inputs. Status moves via
  // /transition and assignment via /assign; the backend preserves both when
  // the keys are absent. Priority now has its own chip/endpoint, but save_task
  // defaults an absent priority to Medium (it does not preserve it), so we echo
  // the current value to avoid clobbering it on save.
  API.updateTask(Store.task.task_id, {
    comments: byId('comments').value,
    priority: Store.task.priority || 'Medium',
    fields: fields,
    revision: Store.task.revision,
    changed_by: currentUserName(),
    business_plan_enabled: Number(Store.project.business_plan_enabled || 0) === 1,
    business_plan_year: Store.project.business_plan_year
  }).then(function () {
    if (formationsField && formationDirty[formationsField.phase]) {
      return API.saveFormations(Store.projectId, {
        phase: formationsField.phase,
        rows: formationRowsForSave(formationsField.phase),
        changed_by: currentUserName(),
        source_task_id: Store.task.task_id
      });
    }
    return null;
  }).then(function () {
    return API.detail(Store.projectId);
  }).then(function (detail) {
    var selectedTaskId = Store.task.task_id;
    Store.project = detail.project || {};
    Store.tasks = detail.tasks || [];
    Store.allFields = detail.fields || {};
    Store.leadSummary = detail.lead_summary || null;
    Store.overview = detail.overview || null;
    Store.formations = detail.formations || [];
    renderDetail();
    loadComponent(Store.tasks.find(function (task) { return task.task_id === selectedTaskId; }) || chooseInitialTask(tasksForPipeline(Store.pipeline)));
    refreshAllBoards();
    msg('Component saved.', 'success');
  }).catch(function (error) { msg(error.message, 'error'); }).finally(function () {
    if (submitButton) submitButton.disabled = false;
  });
}
export function repeatableInputMarkup(field, row, rowIndex) {
  var cols = field.columns || [];
  return '<div class="repeatable-row" data-repeatable-row="' + rowIndex + '">' + cols.map(function (col) {
    var value = row[col.key] == null ? '' : row[col.key];
    var attr = 'data-repeatable-input="' + esc(field.key) + '" data-repeatable-row="' + rowIndex + '" data-repeatable-column="' + esc(col.key) + '"';
    if (col.readonly) {
      return '<label class="calculated-output">' + esc(col.label) + '<output>' + (isFilled(value) ? esc(value) + '%' : 'Calculated on save') + '</output></label>';
    }
    if (col.type === 'select') {
      return '<label>' + esc(col.label) + '<select ' + attr + '>' + (col.options || []).map(function (option) { return '<option value="' + esc(option) + '" ' + (String(value) === String(option) ? 'selected' : '') + '>' + esc(option || 'Select') + '</option>'; }).join('') + '</select></label>';
    }
    return '<label>' + esc(col.label) + '<input type="' + (col.type === 'number' ? 'number' : 'text') + '" step="any" ' + attr + ' value="' + esc(value) + '"></label>';
  }).join('') + '<button type="button" class="ghost remove-repeatable-row" data-repeatable-key="' + esc(field.key) + '" data-repeatable-row="' + rowIndex + '">Remove row</button></div>';
}
export function renderRepeatableField(field, value) {
  var rows = parseRepeatableRows(value);
  if (!rows.length) rows = [{}];
  return '<div class="repeatable-field wide-field" data-repeatable="' + esc(field.key) + '"><div class="repeatable-heading"><b>' + esc(field.label) + '</b><button type="button" class="secondary add-repeatable-row" data-repeatable-key="' + esc(field.key) + '">Add row</button></div><div class="repeatable-rows">' + rows.map(function (row, index) { return repeatableInputMarkup(field, row || {}, index); }).join('') + '</div></div>';
}
export function bindRepeatableFields() {
  all('.add-repeatable-row', byId('dynamic-fields')).forEach(function (button) {
    button.addEventListener('click', function () {
      var key = button.getAttribute('data-repeatable-key');
      var field = (SCHEMA[Store.task.task_name] || []).find(function (item) { return item.key === key; });
      var parent = button.closest('[data-repeatable]');
      var rows = parent.querySelector('.repeatable-rows');
      rows.insertAdjacentHTML('beforeend', repeatableInputMarkup(field, {}, rows.querySelectorAll('.repeatable-row').length));
      bindRepeatableFields();
      previewSummaryInputs();
    });
  });
  all('.remove-repeatable-row', byId('dynamic-fields')).forEach(function (button) {
    button.addEventListener('click', function () {
      var parent = button.closest('[data-repeatable]');
      var rows = parent.querySelectorAll('.repeatable-row');
      if (rows.length === 1) { all('input,select', rows[0]).forEach(function (element) { element.value = ''; }); }
      else { button.closest('.repeatable-row').remove(); }
      previewSummaryInputs();
    });
  });
}
