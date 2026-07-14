import { byId, all, esc, isFilled, truthy, msg } from '../dom.js';
import { API } from '../api.js';
import { currentUserName, currentRole, canManageAssignments, Store } from '../state.js';
import { SCHEMA, FORMATIONS, FORMATION_METRICS, SEISMIC_BLOCKS } from '../schema.js';
import { confirmDialog, promptDialog } from '../dialog.js';
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
// Formations editor (type: 'formations') -- picker + grouped panel
// ---------------------------------------------------------------------------
// Well-level formation values (Store.formations) edited per phase. Instead of a
// wide all-formations-visible sheet, the editor is a formation PICKER (a select
// of the canonical trio + any stored custom formations + "Add custom
// formation…") above a grouped panel showing only the SELECTED formation's
// fields (no horizontal scrolling). The per-phase buffer still holds ALL rows;
// switching the picker just swaps which buffered row's inputs render. Edits are
// write-through on input and PUT to /api/projects/<id>/formations on Save when
// the phase was touched. The PUT is a phase-scoped full replacement server-side,
// so removing a custom formation drops it from the payload and deletes it.

var formationEdits = {}; // phase -> [ { formation, isCustom, values: {metric: value} }, ... ]
var formationDirty = {}; // phase -> true when any input changed since load
var formationSelected = {}; // phase -> buffer index currently shown in the panel

// The panel groups FORMATION_METRICS into labelled rows (reuses the .field-row /
// .field-section-label idiom -- see rowGroupMarkup). Concise per-metric labels
// live here; the fluid select's type/options are looked up from FORMATION_METRICS.
var FORMATION_GROUPS = [
  { section: 'Formation', metrics: [
    { key: 'top_tvdss_ft', label: 'Top (ft TVDSS)' },
    { key: 'base_tvdss_ft', label: 'Base (ft TVDSS)' },
    { key: 'thickness_ft', label: 'Formation Thickness (ft)' } ] },
  { section: 'Pay', metrics: [
    { key: 'pay_ft', label: 'Pay Thickness (ft)' },
    { key: 'porosity_pct', label: 'Porosity (%)' },
    { key: 'swt_pct', label: 'Swt (%)' },
    { key: 'ngr_pct', label: 'NGR (%)' } ] },
  { section: 'Fluid', metrics: [
    { key: 'fluid', label: '' } ] }
];
var FORMATION_METRIC_BY_KEY = {};
FORMATION_METRICS.forEach(function (metric) { FORMATION_METRIC_BY_KEY[metric.key] = metric; });

// Custom formation names mirror the backend normalization (strip().upper(),
// <=40 chars) so what the editor shows is what gets stored.
function normalizeFormationName(name) {
  return String(name == null ? '' : name).trim().toUpperCase().slice(0, 40);
}

// One buffer row from a (possibly missing) saved formation record.
function makeFormationRow(name, isCustom, saved) {
  var values = {};
  FORMATION_METRICS.forEach(function (metric) {
    var stored = saved ? saved[metric.key] : null;
    values[metric.key] = stored == null ? '' : String(stored);
  });
  return { formation: name, isCustom: isCustom, values: values };
}

// Seed a phase: the canonical trio always renders (in order), each filled from
// its saved row when present, followed by any custom (non-canonical) formations
// already stored for the phase.
function seedFormationEdits(phase) {
  var saved = (Store.formations || []).filter(function (row) { return row.phase === phase; });
  var rows = [];
  FORMATIONS.forEach(function (name) {
    var match = saved.find(function (row) { return row.formation === name; });
    rows.push(makeFormationRow(name, false, match));
  });
  saved.forEach(function (row) {
    if (FORMATIONS.indexOf(row.formation) < 0) rows.push(makeFormationRow(row.formation, true, row));
  });
  formationEdits[phase] = rows;
  formationDirty[phase] = false;
}

// Every visible row of the phase, `{ formation, ...metrics }`. A blank formation
// name always drops. A custom (isCustom) row is kept even when metric-less --
// the user named a new formation and the backend stores all-NULL metrics fine.
// A canonical row with entirely blank metrics drops: that full-replacement gap
// is the designed way to delete a canonical formation's row. Deletions overall
// are handled by the backend's phase-scoped full replacement.
export function formationRowsForSave(phase) {
  var kept = [];
  (formationEdits[phase] || []).forEach(function (row) {
    var name = normalizeFormationName(row.formation);
    if (!isFilled(name)) return;
    var hasMetrics = FORMATION_METRICS.some(function (metric) { return isFilled(row.values[metric.key]); });
    if (!row.isCustom && !hasMetrics) return;
    var out = { formation: name };
    FORMATION_METRICS.forEach(function (metric) { out[metric.key] = row.values[metric.key]; });
    kept.push(out);
  });
  return kept;
}

// Pre-save guard for a phase's formation rows: returns an error string to block
// the save (surfaced via msg), or null when the rows are safe to PUT. Catches
// (a) a custom row carrying metrics but no name (would silently vanish) and
// (b) two rows normalizing to the same formation (the phase-scoped full
// replacement would collapse/delete one, losing data).
export function validateFormationRows(phase) {
  var rows = formationEdits[phase] || [];
  for (var i = 0; i < rows.length; i += 1) {
    var row = rows[i];
    var hasMetrics = FORMATION_METRICS.some(function (metric) { return isFilled(row.values[metric.key]); });
    if (row.isCustom && !isFilled(normalizeFormationName(row.formation)) && hasMetrics) {
      return 'Custom formation needs a name.';
    }
  }
  var seen = {};
  var kept = formationRowsForSave(phase);
  for (var k = 0; k < kept.length; k += 1) {
    if (seen[kept[k].formation]) {
      return 'Duplicate formation "' + kept[k].formation + '" — each formation may appear only once.';
    }
    seen[kept[k].formation] = true;
  }
  return null;
}

// Dirty-flag accessors so the project editor's per-component save can decide
// whether to PUT a phase's formation rows (and clear the flag afterward)
// without reaching into the module-private buffers directly.
export function formationPhaseDirty(phase) { return !!formationDirty[phase]; }
export function clearFormationPhaseDirty(phase) { formationDirty[phase] = false; }

// The formations widget with a formations field is unique per phase, so the
// add/remove re-render can re-find its schema field by phase alone.
function formationFieldForPhase(phase) {
  var names = Object.keys(SCHEMA);
  for (var i = 0; i < names.length; i += 1) {
    var field = (SCHEMA[names[i]] || []).find(function (item) {
      return item.type === 'formations' && (item.phase || 'quicklook') === phase;
    });
    if (field) return field;
  }
  return { phase: phase, label: 'Formations' };
}

// Clamp (and memoize) the selected buffer index for a phase; defaults to 0 --
// SARH, always the first canonical row. Guards against a stale index after a
// remove or a reseed.
function selectedFormationIndex(phase) {
  var rows = formationEdits[phase] || [];
  var selected = formationSelected[phase];
  if (selected == null || selected < 0 || selected >= rows.length) selected = 0;
  formationSelected[phase] = selected;
  return selected;
}

// One grouped panel of the selected formation's inputs: each FORMATION_GROUPS
// section is a `.field-section-label` heading + a `.field-row` (cols-N for
// multi-field groups) of labelled inputs, so nothing scrolls horizontally.
// Inputs carry data-formation-metric/data-formation-row so the buffer sync can
// address the row (`index` is the selected buffer position).
function formationMetricControl(metric, row, index) {
  var value = (row && row.values[metric.key]) || '';
  var def = FORMATION_METRIC_BY_KEY[metric.key] || {};
  var attr = 'data-formation-metric="' + esc(metric.key) + '" data-formation-row="' + index + '"';
  if (def.type === 'select') {
    var options = (def.options || []).map(function (option) {
      return '<option ' + (String(value) === String(option) ? 'selected' : '') + '>' + esc(option) + '</option>';
    }).join('');
    return '<label>' + esc(metric.label) + '<select ' + attr + ' aria-label="' + esc(metric.label) + '">' + options + '</select></label>';
  }
  return '<label>' + esc(metric.label) + '<input type="number" step="any" ' + attr + ' value="' + esc(value) + '" aria-label="' + esc(metric.label) + '"></label>';
}
function formationPanelMarkup(row, index) {
  return FORMATION_GROUPS.map(function (group) {
    var colsClass = group.metrics.length > 1 ? ' cols-' + group.metrics.length : '';
    var inner = group.metrics.map(function (metric) { return formationMetricControl(metric, row, index); }).join('');
    return '<div class="field-section-label">' + esc(group.section) + '</div>' +
      '<div class="field-row' + colsClass + '">' + inner + '</div>';
  }).join('');
}

// Container inner markup: heading, the formation picker (canonical trio + stored
// customs + "Add custom formation…") with a remove button on custom formations,
// then the selected formation's grouped panel. Rebuilt whenever the picker
// selection or the row set changes.
function buildFormationsInner(field) {
  var phase = field.phase || 'quicklook';
  var rows = formationEdits[phase] || [];
  var selected = selectedFormationIndex(phase);
  var row = rows[selected];
  var pickerOptions = rows.map(function (item, index) {
    return '<option value="' + index + '"' + (index === selected ? ' selected' : '') + '>' + esc(item.formation) + '</option>';
  }).join('') + '<option value="__add__">Add custom formation&hellip;</option>';
  var removeButton = (row && row.isCustom)
    ? '<button type="button" class="icon-btn formation-remove" title="Remove formation" aria-label="Remove formation">&#10005;</button>'
    : '';
  var pickerRow = '<div class="formation-picker-row">' +
    '<label class="formation-picker-label">Formation<select class="formation-picker" aria-label="Formation">' + pickerOptions + '</select></label>' +
    removeButton + '</div>';
  return '<div class="repeatable-heading"><b>' + esc(field.label) + '</b></div>' +
    pickerRow + '<div class="formation-panel">' + formationPanelMarkup(row, selected) + '</div>';
}

function renderFormationsField(field) {
  var phase = field.phase || 'quicklook';
  seedFormationEdits(phase);
  formationSelected[phase] = 0; // SARH selected by default
  return '<div class="repeatable-field wide-field formations-field" data-formations-phase="' + esc(phase) + '">' +
    buildFormationsInner(field) + '</div>';
}

// Rebuild a container in place (keeping the node + its phase attr) after the
// picker selection or row set changes, then rewire its fresh inputs.
function rerenderFormationContainer(container, phase) {
  container.innerHTML = buildFormationsInner(formationFieldForPhase(phase));
  bindFormationContainer(container);
}

// Prompt for a custom formation name, guard duplicates against the whole buffer
// (canonical + custom), then append the new row, select it, and re-render. The
// picker is snapped back to the current selection first because the prompt is
// async (a cancel must leave the visible selection unchanged).
function addCustomFormation(container, phase, picker) {
  picker.value = String(selectedFormationIndex(phase));
  promptDialog({ title: 'Add custom formation', message: 'Formation name', initialValue: '' }).then(function (name) {
    if (name === null) return; // cancelled
    var normalized = normalizeFormationName(name);
    if (!isFilled(normalized)) return msg('Custom formation needs a name.', 'error');
    var rows = formationEdits[phase] || [];
    var duplicate = rows.some(function (item) { return normalizeFormationName(item.formation) === normalized; });
    if (duplicate) return msg('Duplicate formation "' + normalized + '" — each formation may appear only once.', 'error');
    rows.push(makeFormationRow(normalized, true, null));
    formationSelected[phase] = rows.length - 1;
    formationDirty[phase] = true;
    rerenderFormationContainer(container, phase);
  });
}

// Wire one formations container: the panel's metric inputs write through to the
// selected buffer row; the picker either swaps which row's panel renders or (on
// "Add custom formation…") prompts for a new one; the remove button (custom
// formations only) drops the row and snaps the picker back to SARH. Per-element
// `fBound`/`fBtnBound` guards make re-binds after a re-render (which replaces the
// inner DOM with unmarked elements) wire only the new nodes.
function bindFormationContainer(container) {
  var phase = container.getAttribute('data-formations-phase');
  all('[data-formation-metric]', container).forEach(function (element) {
    if (element.dataset.fBound) return;
    element.dataset.fBound = 'true';
    function sync() {
      var index = Number(element.getAttribute('data-formation-row'));
      formationEdits[phase][index].values[element.getAttribute('data-formation-metric')] = element.value;
      formationDirty[phase] = true;
    }
    element.addEventListener('input', sync);
    element.addEventListener('change', sync);
  });
  all('.formation-picker', container).forEach(function (picker) {
    if (picker.dataset.fBound) return;
    picker.dataset.fBound = 'true';
    picker.addEventListener('change', function () {
      if (picker.value === '__add__') { addCustomFormation(container, phase, picker); return; }
      formationSelected[phase] = Number(picker.value);
      rerenderFormationContainer(container, phase);
    });
  });
  all('.formation-remove', container).forEach(function (button) {
    if (button.dataset.fBtnBound) return;
    button.dataset.fBtnBound = 'true';
    button.addEventListener('click', function () {
      formationEdits[phase].splice(selectedFormationIndex(phase), 1);
      formationSelected[phase] = 0; // back to SARH
      formationDirty[phase] = true;
      rerenderFormationContainer(container, phase);
    });
  });
}

// `root` defaults to the step editor's #dynamic-fields; the project editor
// passes each component card's own .dynamic-fields root instead.
function bindFormationFields(root) {
  root = root || byId('dynamic-fields');
  all('.formations-field', root).forEach(function (container) { bindFormationContainer(container); });
}

// Options for a standalone select with optionsFrom:'formations' (the Flowback
// Results formation dropdown): the canonical trio plus any custom formations
// already on the well (Store.formations, any phase, deduped), with a stored
// value no longer in that set appended so old rows render selected instead of
// silently blanking (same courtesy repeatableSelectOptions extends).
function formationNameOptions(current) {
  var names = FORMATIONS.slice();
  (Store.formations || []).forEach(function (row) {
    if (row && row.formation && names.indexOf(row.formation) < 0) names.push(row.formation);
  });
  if (isFilled(current) && names.map(String).indexOf(String(current)) < 0) names.push(current);
  return names;
}
// Precedence: saved value ?? Store.project[field.defaultFrom] ?? field.value ?? ''.
// defaultFrom prefills from a project column (e.g. Staking well X/Y from
// lead_x/lead_y); the prefill persists as a normal dynamic field on save.
// The Flowback formation select rides the field.value rung for its SARH
// default.
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
    var options = field.optionsFrom === 'formations' ? formationNameOptions(value) : (field.options || []);
    return '<label class="' + classes + '"' + showIfAttr + '>' + esc(field.label) + '<select data-field="' + esc(field.key) + '">' + options.map(function (option) { return '<option ' + (String(value) === String(option) ? 'selected' : '') + '>' + esc(option) + '</option>'; }).join('') + '</select></label>';
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
  // Guard the touched formations mini-sheet before any write: a name-less custom
  // row with metrics or two rows normalizing to the same formation would lose
  // data in the phase-scoped full replacement.
  if (formationsField && formationDirty[formationsField.phase]) {
    var formationError = validateFormationRows(formationsField.phase);
    if (formationError) return msg(formationError, 'error');
  }
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
// or index column a compact auto track, plus a trailing auto track for the
// row action.
function repeatableTemplate(field) {
  return (field.columns || []).map(function (col) { return (col.readonly || col.type === 'index') ? 'auto' : 'minmax(90px, 1fr)'; }).join(' ') + ' auto';
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
    // Index column: a display-only row-number chip (#1..#n, e.g. flowback
    // stages). No data-repeatable-input attr, so getFields never harvests it;
    // renumberIndexColumns re-stamps it after a mid-list removal.
    if (col.type === 'index') {
      return '<span class="repeatable-calc repeatable-index" title="' + esc(col.label) + '">#' + (rowIndex + 1) + '</span>';
    }
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
// Re-stamp a container's display-only index chips (#1..#n) after a mid-list
// removal so row numbers stay contiguous (rows without index columns are a
// harmless no-op).
function renumberIndexColumns(parent) {
  all('.repeatable-row', parent).forEach(function (row, index) {
    all('.repeatable-index', row).forEach(function (span) { span.textContent = '#' + (index + 1); });
  });
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
      renumberIndexColumns(parent);
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
