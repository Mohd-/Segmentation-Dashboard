import { byId, all, esc, isFilled, truthy, msg, fillSelect } from '../dom.js';
import { API } from '../api.js';
import { currentUserName, Store } from '../state.js';
import { SCHEMA, STATUSES } from '../schema.js';
import { renderDetail, renderRightPanel, chooseInitialTask, tasksForPipeline, parseRepeatableRows } from './detail.js';
import { refreshAllBoards } from './pipeline.js';

export function loadComponent(task) {
  if (!task) return;
  Store.task = task;
  all('.component-item').forEach(function (button) { button.classList.toggle('active', Number(button.getAttribute('data-task-id')) === task.task_id); });
  byId('component-number').textContent = String(task.sequence_no || '');
  byId('component-title').textContent = task.task_name;
  byId('component-stage').textContent = task.stage_group;
  byId('assigned-to').value = task.assigned_to || '';
  fillSelect(byId('component-status'), STATUSES, false);
  byId('component-status').value = task.status || 'Not Assigned';
  byId('component-priority').value = task.priority || 'Medium';
  byId('comments').placeholder = commentPlaceholder(task.task_name);
  byId('comments').value = task.comments || '';
  Promise.all([API.fields(task.task_id), API.componentFolder(Store.projectId, task.task_id)]).then(function (results) {
    renderFields(task.task_name, results[0] || {});
    renderComponentFolder(results[1] || {});
    renderRightPanel(tasksForPipeline(Store.pipeline));
  }).catch(function (error) { msg(error.message, 'error'); });
}
export function commentPlaceholder(componentName) {
  if (componentName === 'Approval To Drill') return 'Include the requirement for the Approval to Drill letter';
  return 'Comments, assumptions, rationale, or required notes...';
}
export function renderFields(componentName, values) {
  var fields = SCHEMA[componentName] || [];
  var html = '';
  fields.forEach(function (field) {
    var value = values[field.key] != null ? values[field.key] : (field.value || '');
    var hidden = field.showIf && !truthy(values[field.showIf]);
    var classes = (hidden ? ' conditional hidden' : ' conditional') + (field.type === 'text' ? ' wide-field' : '');
    if (field.type === 'repeatable') {
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
      html += '<div class="summary-box' + classes + '"><b>' + esc(field.label) + '</b><p><a href="' + esc(field.value || '#') + '" target="_blank" rel="noreferrer">New Request</a></p></div>';
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
  updateConditionalVisibility();
}
export function val(component, key) {
  var value = ((Store.allFields || {})[component] || {})[key];
  return isFilled(value) ? value : '';
}
export function autoSummaryHtml(componentName) {
  if (componentName !== 'Resource Assessment Update') return '';
  var rows = [];
  function add(label, value) {
    if (isFilled(value)) rows.push('<li><span>' + esc(label) + '</span><b>' + esc(value) + '</b></li>');
  }
  add('Dynamic OGIP (BCF)', val('Flowback Results', 'flowback_dynamic_ogip_bcf'));
  add('Post-Drilling Mean PIIP Gas (BCF)', val('Post-Drilling Resource Assessment', 'post_drill_piip_gas_mean'));
  return rows.length ? '<ul class="summary-list">' + rows.join('') + '</ul>' : '';
}

export function renderComponentFolder(info) {
  var previous = byId('component-folder-card');
  if (previous) previous.remove();
  if (!info || !Number(info.requires_folder)) return;
  var card = document.createElement('div');
  card.id = 'component-folder-card';
  card.className = 'folder-card';
  card.innerHTML = '<b>Component File Location</b><p>' + esc(info.unc_path || 'Folder path placeholder not configured.') + '</p><button type="button" class="secondary" id="copy-component-folder">Copy Folder Link</button>';
  var container = byId('dynamic-fields');
  container.parentNode.insertBefore(card, container.nextSibling);
  byId('copy-component-folder').addEventListener('click', function () { copyText(info.unc_path || ''); });
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
  var submitButton = event.target.querySelector('button[type="submit"]');
  if (submitButton) submitButton.disabled = true;
  API.updateTask(Store.task.task_id, {
    status: byId('component-status').value,
    assigned_to: byId('assigned-to').value,
    comments: byId('comments').value,
    priority: byId('component-priority').value,
    fields: fields,
    revision: Store.task.revision,
    changed_by: currentUserName(),
    business_plan_enabled: Number(Store.project.business_plan_enabled || 0) === 1,
    business_plan_year: Store.project.business_plan_year
  }).then(function () {
    return API.detail(Store.projectId);
  }).then(function (detail) {
    var selectedTaskId = Store.task.task_id;
    Store.project = detail.project || {};
    Store.tasks = detail.tasks || [];
    Store.allFields = detail.fields || {};
    Store.leadSummary = detail.lead_summary || null;
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
