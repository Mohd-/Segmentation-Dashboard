import { byId, all, esc, isFilled, truthy, msg } from '../dom.js';
import { API } from '../api.js';
import { currentUserName, currentRole, canManageAssignments, Store } from '../state.js';
import { SCHEMA, FORMATIONS, FORMATION_METRICS, SEISMIC_BLOCKS } from '../schema.js';
import { confirmDialog } from '../dialog.js';
import { renderDetail, renderRightPanel, chooseInitialTask, tasksForPipeline, parseRepeatableRows, refreshAfterRecordChange, revealTaskStage } from './detail.js';
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

// Only supervisors set priority (anonymous dev mode acts as supervisor,
// matching the backend's current_role()); everyone else sees a read-only chip.
function canSetPriority() {
  return currentRole() === 'supervisor';
}

function renderPriorityChip(task) {
  var chip = byId('component-priority-chip');
  if (!chip) return;
  var value = task.priority || 'Medium';
  var editable = canSetPriority();
  chip.textContent = value;
  chip.className = 'priority editor-priority-chip priority-' + String(value).toLowerCase() +
    (editable ? '' : ' editor-priority-chip-static');
  chip.title = 'Priority: ' + value + (editable ? ' — click to change' : ' — set by a supervisor');
}

// Cycles Low -> Medium -> High -> Low via the dedicated priority endpoint
// (PATCH /api/tasks/<id>/priority; no revision check server-side), then
// refreshes so the chip and boards adopt the new value + task revision.
export function cyclePriorityChip() {
  if (!Store.task || !canSetPriority()) return;
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
  revealTaskStage(task);
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

// Dirty-flag accessors so the project editor's per-component save can decide
// whether to PUT a phase's formation rows (and clear the flag afterward)
// without reaching into the module-private buffers directly.
export function formationPhaseDirty(phase) { return !!formationDirty[phase]; }
export function clearFormationPhaseDirty(phase) { formationDirty[phase] = false; }

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

// `root` defaults to the step editor's #dynamic-fields; the project editor
// passes each component card's own .dynamic-fields root instead.
function bindFormationFields(root) {
  root = root || byId('dynamic-fields');
  all('.formations-field', root).forEach(function (container) {
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

// Precedence: saved value ?? Store.project[field.defaultFrom] ?? field.value ?? ''.
// defaultFrom prefills from a project column (e.g. Staking well X/Y from
// lead_x/lead_y); the prefill persists as a normal dynamic field on save.
function resolveFieldValue(field, values) {
  var fallback = field.value || '';
  if (field.defaultFrom && Store.project && Store.project[field.defaultFrom] != null) {
    fallback = Store.project[field.defaultFrom];
  }
  return values[field.key] != null ? values[field.key] : fallback;
}
// Markup for one field's control element, given a pre-built class string and
// data-show-if attribute (so the caller can put visibility on the field itself
// or hoist it onto a grouping wrapper).
function fieldMarkup(field, value, classes, showIfAttr) {
  if (field.readonly) {
    return '<label class="calculated-output' + classes + '"' + showIfAttr + '>' + esc(field.label) + '<output>' + (isFilled(value) ? esc(value) + '%' : 'Calculated on save') + '</output></label>';
  }
  if (field.type === 'select') {
    return '<label class="' + classes + '"' + showIfAttr + '>' + esc(field.label) + '<select data-field="' + esc(field.key) + '">' + (field.options || []).map(function (option) { return '<option ' + (String(value) === String(option) ? 'selected' : '') + '>' + esc(option) + '</option>'; }).join('') + '</select></label>';
  }
  if (field.type === 'checkbox') {
    return '<label class="check-label' + classes + '"' + showIfAttr + '><input type="checkbox" data-field="' + esc(field.key) + '" ' + (truthy(value) ? 'checked' : '') + '> ' + esc(field.label) + '</label>';
  }
  if (field.type === 'text') {
    return '<label class="' + classes + '"' + showIfAttr + '>' + esc(field.label) + '<input data-field="' + esc(field.key) + '" value="' + esc(value) + '"></label>';
  }
  if (field.type === 'link') {
    // Link cards never toggle; data-show-if is intentionally omitted.
    return '<div class="summary-box' + classes + '"><b>' + esc(field.label) + '</b><p><a href="' + esc(field.value || '#') + '" target="_blank" rel="noreferrer">' + esc(field.linkText || 'New Request') + '</a></p></div>';
  }
  return '<label class="' + classes + '"' + showIfAttr + '>' + esc(field.label) + '<input type="number" step="any" data-field="' + esc(field.key) + '" value="' + esc(value) + '"></label>';
}
// A field standing on its own (no row grouping): visibility lives on the field.
function standaloneFieldMarkup(field, componentName, values) {
  var value = resolveFieldValue(field, values);
  if (field.type === 'formations') return renderFormationsField(field);
  if (field.type === 'repeatable') return renderRepeatableField(field, value);
  if (field.type === 'summary') {
    var summaryHtml = autoSummaryHtml(componentName);
    return summaryHtml ? '<div class="summary-box conditional">' + summaryHtml + '</div>' : '';
  }
  var hidden = field.showIf && !truthy(values[field.showIf]);
  var classes = (hidden ? ' conditional hidden' : ' conditional') + (field.type === 'text' ? ' wide-field' : '');
  return fieldMarkup(field, value, classes, ' data-show-if="' + esc(field.showIf || '') + '"');
}
// Consecutive fields sharing a row id render inside one full-width .field-row of
// equal columns. If every field in the row shares one showIf, the wrapper (not
// the inner controls) carries data-show-if so the whole row hides as a unit
// without leaving a gap; otherwise each control keeps its own visibility.
function rowGroupMarkup(group, values) {
  var shared = group[0].showIf || '';
  var allShared = shared && group.every(function (item) { return item.showIf === shared; });
  var hidden = allShared && !truthy(values[shared]);
  var wrapClasses = 'field-row cols-' + group.length + ' conditional' + (hidden ? ' hidden' : '');
  var wrapShowIf = allShared ? ' data-show-if="' + esc(shared) + '"' : '';
  var inner = group.map(function (item) {
    var value = resolveFieldValue(item, values);
    if (allShared) return fieldMarkup(item, value, '', '');
    var itemHidden = item.showIf && !truthy(values[item.showIf]);
    return fieldMarkup(item, value, itemHidden ? ' conditional hidden' : ' conditional', ' data-show-if="' + esc(item.showIf || '') + '"');
  }).join('');
  return '<div class="' + wrapClasses + '"' + wrapShowIf + '>' + inner + '</div>';
}
// Section heading emitted before a field that opens a new section. The heading
// carries a showIf only when every field of the section shares it (so it hides
// with the section it labels).
function sectionLabelMarkup(fields, startIndex, values) {
  var section = fields[startIndex].section;
  var shared = fields[startIndex].showIf || '';
  for (var k = startIndex; k < fields.length; k += 1) {
    if (k > startIndex && fields[k].section && fields[k].section !== section) break;
    if (fields[k].showIf !== shared) { shared = ''; break; }
  }
  var hidden = shared && !truthy(values[shared]);
  var cls = 'field-section-label conditional' + (hidden ? ' hidden' : '');
  var attr = shared ? ' data-show-if="' + esc(shared) + '"' : '';
  return '<div class="' + cls + '"' + attr + '>' + esc(section) + '</div>';
}
// `root` (default #dynamic-fields) is the container to render into, so the
// project editor can drive many component grids from this one renderer.
// `onInput` (default: the step editor's live conditional-visibility + summary
// preview) fires on every field change; the project editor passes a callback
// that only refreshes its card's conditional visibility (no summary preview).
export function renderFields(componentName, values, root, onInput) {
  root = root || byId('dynamic-fields');
  var fields = SCHEMA[componentName] || [];
  var html = '';
  var lastSection = null;
  var i = 0;
  while (i < fields.length) {
    var field = fields[i];
    if (field.section && field.section !== lastSection) {
      html += sectionLabelMarkup(fields, i, values);
    }
    if (field.section) lastSection = field.section;
    if (field.row) {
      var group = [];
      var j = i;
      while (j < fields.length && fields[j].row === field.row) { group.push(fields[j]); j += 1; }
      html += rowGroupMarkup(group, values);
      i = j;
    } else {
      html += standaloneFieldMarkup(field, componentName, values);
      i += 1;
    }
  }
  root.innerHTML = html;
  var handler = onInput || function () {
    updateConditionalVisibility(root);
    previewSummaryInputs();
  };
  all('[data-field], [data-repeatable-input]', root).forEach(function (element) {
    element.addEventListener('change', handler);
    element.addEventListener('input', handler);
  });
  bindRepeatableFields(root, handler);
  bindFormationFields(root);
  updateConditionalVisibility(root);
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
export function updateConditionalVisibility(root) {
  root = root || byId('dynamic-fields');
  var fields = getFields(root);
  all('[data-show-if]', root).forEach(function (element) {
    var key = element.getAttribute('data-show-if');
    if (key) element.classList.toggle('hidden', !truthy(fields[key]));
  });
}
export function getFields(root) {
  root = root || byId('dynamic-fields');
  var fields = {};
  all('[data-field]', root).forEach(function (element) {
    fields[element.getAttribute('data-field')] = element.type === 'checkbox' ? (element.checked ? '1' : '') : element.value;
  });
  all('[data-repeatable]', root).forEach(function (container) {
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
// Shared grid template so the header row and every data row line up: each
// editable column is a flexible min-90px track, each readonly (calculated)
// column a compact auto track, plus a trailing auto track for the row action.
function repeatableTemplate(field) {
  return (field.columns || []).map(function (col) { return col.readonly ? 'auto' : 'minmax(90px, 1fr)'; }).join(' ') + ' auto';
}
// The runtime map of seismic block -> AR list. Prefer /api/meta (Store.meta),
// which is production-swappable; fall back to the schema.js boot map when meta
// is absent (e.g. the meta call failed or predates the seismic_blocks key).
function seismicBlocksMap() {
  return (Store.meta && Store.meta.seismic_blocks) || SEISMIC_BLOCKS || {};
}
// Options for a `optionsFrom`-driven select column, scoped to the current row.
// A column with `dependsOn` (the AR column) yields only its sibling block's AR
// list; without it (the block column) it yields the block names. Both lead with
// a blank option. `row` may be a plain object (initial render) or a lookup of
// the sibling's current value.
function repeatableColumnOptions(col, row) {
  var map = seismicBlocksMap();
  if (col.dependsOn) {
    var block = row[col.dependsOn];
    return [''].concat((block && map[block]) || []);
  }
  return [''].concat(Object.keys(map));
}
// Build the <option> list for a select column, appending the stored value as an
// extra option when it is legacy data no longer present in the map (so old rows
// render selected instead of silently blanking).
function repeatableSelectOptions(col, row, value) {
  var options = col.optionsFrom ? repeatableColumnOptions(col, row) : (col.options || []);
  if (isFilled(value) && options.map(String).indexOf(String(value)) < 0) options = options.concat([value]);
  return options.map(function (option) {
    return '<option value="' + esc(option) + '" ' + (String(value) === String(option) ? 'selected' : '') + '>' + esc(option || 'Select') + '</option>';
  }).join('');
}
export function repeatableInputMarkup(field, row, rowIndex) {
  var cols = field.columns || [];
  var style = ' style="grid-template-columns:' + repeatableTemplate(field) + '"';
  return '<div class="repeatable-row" data-repeatable-row="' + rowIndex + '"' + style + '>' + cols.map(function (col) {
    var value = row[col.key] == null ? '' : row[col.key];
    // A dependent column (AR) carries data-depends-on so bindRepeatableFields can
    // rebuild its options when the sibling (block) select changes.
    var dep = col.dependsOn ? ' data-depends-on="' + esc(col.dependsOn) + '"' : '';
    var attr = 'data-repeatable-input="' + esc(field.key) + '" data-repeatable-row="' + rowIndex + '" data-repeatable-column="' + esc(col.key) + '"' + dep;
    var aria = ' aria-label="' + esc(col.label) + '"';
    // Readonly calculated column: a compact brand-tinted value chip at the right
    // end of the row (server computes it on save; not harvested by getFields).
    if (col.readonly) {
      return '<span class="repeatable-calc" title="' + esc(col.label) + '">' + (isFilled(value) ? esc(value) + '%' : '—') + '</span>';
    }
    if (col.type === 'select') {
      return '<select ' + attr + aria + '>' + repeatableSelectOptions(col, row, value) + '</select>';
    }
    return '<input type="' + (col.type === 'number' ? 'number' : 'text') + '" step="any" ' + attr + aria + ' value="' + esc(value) + '">';
  }).join('') + '<button type="button" class="icon-btn remove-repeatable-row" data-repeatable-key="' + esc(field.key) + '" data-repeatable-row="' + rowIndex + '" title="Remove row" aria-label="Remove row">✕</button></div>';
}
export function renderRepeatableField(field, value) {
  var rows = parseRepeatableRows(value);
  if (!rows.length) rows = [{}];
  var cols = field.columns || [];
  // One muted header row of column labels (kept out of the .repeatable-row set
  // so it is not counted by getFields/bindRepeatableFields) plus a trailing
  // spacer aligned with the row-action button.
  var header = '<div class="repeatable-head" style="grid-template-columns:' + repeatableTemplate(field) + '">' + cols.map(function (col) {
    return '<span class="repeatable-col-label">' + esc(col.label) + '</span>';
  }).join('') + '<span class="repeatable-col-label" aria-hidden="true"></span></div>';
  return '<div class="repeatable-field wide-field" data-repeatable="' + esc(field.key) + '"><div class="repeatable-heading"><b>' + esc(field.label) + '</b><button type="button" class="icon-btn add-repeatable-row" data-repeatable-key="' + esc(field.key) + '" title="Add row" aria-label="Add row">+</button></div><div class="repeatable-sheet"><div class="repeatable-rows">' + header + rows.map(function (row, index) { return repeatableInputMarkup(field, row || {}, index); }).join('') + '</div></div></div>';
}
// Repeatable field keys are globally unique across SCHEMA, so the "add row"
// def resolves by key search rather than through Store.task.task_name -- the
// project editor binds several components' repeatables at once, none of which
// is the (step editor's) Store.task.
function repeatableFieldDef(key) {
  var names = Object.keys(SCHEMA);
  for (var i = 0; i < names.length; i += 1) {
    var field = (SCHEMA[names[i]] || []).find(function (item) { return item.key === key; });
    if (field) return field;
  }
  return null;
}
export function bindRepeatableFields(root, onInput) {
  root = root || byId('dynamic-fields');
  var handler = onInput || previewSummaryInputs;

  all('.add-repeatable-row', root).forEach(function (button) {
    if (button.dataset.bound) return;
    button.dataset.bound = 'true';

    button.addEventListener('click', function () {
      var key = button.getAttribute('data-repeatable-key');
      var field = repeatableFieldDef(key);
      var parent = button.closest('[data-repeatable]');
      var rows = parent.querySelector('.repeatable-rows');
      rows.insertAdjacentHTML('beforeend', repeatableInputMarkup(field, {}, rows.querySelectorAll('.repeatable-row').length));
      bindRepeatableFields(root, handler);
      handler();
    });
  });

  all('.remove-repeatable-row', root).forEach(function (button) {
    if (button.dataset.bound) return;
    button.dataset.bound = 'true';

    button.addEventListener('click', function () {
      var parent = button.closest('[data-repeatable]');
      var rows = parent.querySelectorAll('.repeatable-row');
      if (rows.length === 1) { all('input,select', rows[0]).forEach(function (element) { element.value = ''; }); }
      else { button.closest('.repeatable-row').remove(); }
      handler();
    });
  });

  // Dependent selects (AR Number depends on Seismic Block): when the controlling
  // select in the same row changes, rebuild the dependent's options from the
  // seismic map and reset its value (the old AR belongs to another block), then
  // run the usual handler. Guard is per-dependent so re-binds after add-row only
  // wire freshly added rows. Works identically in the project editor (same
  // render/bind path); no schema lookup here -- the column metadata was stamped
  // onto data-depends-on / data-repeatable-column at render time.
  all('[data-depends-on]', root).forEach(function (dependent) {
    if (dependent.dataset.depBound) return;
    dependent.dataset.depBound = 'true';
    var row = dependent.closest('.repeatable-row');
    if (!row) return;
    var controlKey = dependent.getAttribute('data-depends-on');
    var control = row.querySelector('[data-repeatable-column="' + controlKey + '"]');
    if (!control) return;
    control.addEventListener('change', function () {
      var map = seismicBlocksMap();
      var options = [''].concat((control.value && map[control.value]) || []);
      dependent.innerHTML = options.map(function (option) {
        return '<option value="' + esc(option) + '">' + esc(option || 'Select') + '</option>';
      }).join('');
      dependent.value = '';
      handler();
    });
  });
}
