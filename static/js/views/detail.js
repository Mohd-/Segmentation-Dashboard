import { byId, all, esc, isFilled, range, msg } from '../dom.js';
import { API } from '../api.js';
import { currentUserName, Store, resetSelection } from '../state.js';
import { BP_STAGES, PROSPECT_STAGES, DONE, schemaIndex } from '../schema.js';
import { confirmDialog, promptDialog } from '../dialog.js';
import { loadComponent } from './detail-form.js';
import { refreshAllBoards } from './pipeline.js';
import { refreshAudit } from './audit.js';

export function tasksForPipeline(pipeline) {
  // Prefer the authoritative stage lists from /api/meta (Store.meta); the
  // schema.js arrays are only boot fallbacks.
  var bp = (Store.meta && Store.meta.bp_stages) || BP_STAGES;
  var prospect = (Store.meta && Store.meta.prospect_stages) || PROSPECT_STAGES;
  if (pipeline === 'bp') return Store.tasks.filter(function (task) { return bp.indexOf(task.stage_group) >= 0; });
  if (pipeline === 'prospect') return Store.tasks.filter(function (task) { return prospect.indexOf(task.stage_group) >= 0; });
  return Store.tasks.slice();
}
export function chooseInitialTask(tasks) {
  if (!tasks.length) return null;
  var currentName = Store.project && Store.project.current_task;
  return tasks.find(function (task) { return task.task_name === currentName; }) ||
    tasks.find(function (task) { return !DONE[task.status]; }) || tasks[0];
}
export function openDetail(projectId, pipeline) {
  Store.projectId = projectId;
  Store.pipeline = pipeline || 'prospect';
  byId('detail-shell').classList.remove('hidden');
  API.detail(projectId).then(function (detail) {
    Store.project = detail.project || {};
    Store.tasks = detail.tasks || [];
    Store.allFields = detail.fields || {};
    Store.leadSummary = detail.lead_summary || null;
    Store.overview = detail.overview || null;
    renderDetail();
    loadComponent(chooseInitialTask(tasksForPipeline(Store.pipeline)));
    byId('detail-shell').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }).catch(function (error) { msg(error.message, 'error'); });
}
export function renderDetail() {
  var tasks = tasksForPipeline(Store.pipeline);
  byId('detail-name').textContent = Store.project.project_name || 'Lead / Well';
  byId('detail-subtitle').textContent = Store.pipeline === 'bp' ? 'Business Plan Execution' : 'Prospect Maturation';
  byId('back-to-overview').textContent = '← Back to ' + (Store.pipeline === 'bp' ? 'Business Plan Execution' : 'Prospect Maturation');
  byId('component-list').innerHTML = tasks.map(function (task) {
    return '<button type="button" class="component-item ' + (DONE[task.status] ? 'done' : '') + '" data-task-id="' + task.task_id + '"><span>' + esc(task.sequence_no) + '</span><b>' + esc(task.task_name) + '</b><small>' + esc(task.status || 'Not Assigned') + '</small></button>';
  }).join('') || '<div class="empty-state">No components in this pipeline.</div>';
  all('.component-item').forEach(function (button) {
    button.addEventListener('click', function () {
      var taskId = Number(button.getAttribute('data-task-id'));
      loadComponent(Store.tasks.find(function (task) { return task.task_id === taskId; }));
    });
  });
  renderRightPanel(tasks);
}

export function summaryValue(sources, fieldMap) {
  var sourceMap = fieldMap || Store.allFields;
  for (var i = 0; i < sources.length; i += 1) {
    var component = sources[i][0];
    var key = sources[i][1];
    var componentFields = sourceMap[component] || {};
    if (isFilled(componentFields[key])) return componentFields[key];
  }
  return '';
}

export function summaryItemMarkup(label, value, component, className, valueIsHtml) {
  var source = component ? '<small>' + esc(component) + '</small>' : '';
  var classes = 'summary-item' + (className ? ' ' + className : '');
  return '<div class="' + classes + '"><div class="summary-item-label">' + source + '<span>' + esc(label) + '</span></div><div class="summary-item-value">' + (valueIsHtml ? value : esc(value)) + '</div></div>';
}
export function parseRepeatableRows(value) {
  if (Array.isArray(value)) return value;
  try {
    var parsed = JSON.parse(value || '[]');
    return Array.isArray(parsed) ? parsed : [];
  } catch (error) { return []; }
}
export function reservoirCosSummary(fieldMap) {
  var sourceMap = fieldMap || Store.allFields;
  var rows = parseRepeatableRows(((sourceMap['Reservoir CoS'] || {}).reservoir_cos_rows) || '[]');
  return rows.map(function (row) {
    var ref = row.seismic_volume_ar_number ? 'AR ' + row.seismic_volume_ar_number + ': ' : '';
    return isFilled(row.reservoir_cos_pct) ? ref + row.reservoir_cos_pct + '%' : '';
  }).filter(Boolean).join(' · ');
}
export function curatedOverviewMarkup(fieldMap) {
  var sourceMap = fieldMap || Store.allFields;
  function sourceVal(component, key) { return ((sourceMap[component] || {})[key]) || ''; }
  var rows = [];
  function add(label, value, component) {
    if (isFilled(value)) rows.push(summaryItemMarkup(label, value, component || '', '', false));
  }
  var finalOrQuick = function (finalKey, quickKey) {
    return summaryValue([['Final Log Analysis', finalKey], ['Quicklook Logs Interpretation', quickKey]], sourceMap);
  };
  add('P90 Area (km²)', sourceVal('Reservoir Area Definition', 'p90_area_km2'), 'Reservoir Area Definition');
  add('P10 Area (km²)', sourceVal('Reservoir Area Definition', 'p10_area_km2'), 'Reservoir Area Definition');
  add('Sarah Formation Thickness (ft)', sourceVal('Thickness Estimation', 'formation_thickness_ft'), 'Thickness Estimation');
  add('Reservoir Thickness (ft)', sourceVal('Thickness Estimation', 'reservoir_thickness_ft'), 'Thickness Estimation');
  add('Reservoir CoS (%)', reservoirCosSummary(sourceMap), 'Reservoir CoS');
  add('Trap CoS (%)', sourceVal('Trap CoS', 'trap_cos_pct'), 'Trap CoS');
  add('Seal CoS (%)', sourceVal('Seal CoS', 'seal_cos_pct'), 'Seal CoS');
  // v18: the derived Presence value has no step of its own. Live view reads
  // overview.derisking; a lead-summary snapshot (fieldMap passed) keeps
  // reading the legacy 'Presence CoS Evaluation' fields captured at the time.
  var totalChance = fieldMap
    ? sourceVal('Presence CoS Evaluation', 'presence_cos')
    : ((Store.overview && Store.overview.derisking) || '');
  add('Total Chance of Success (%)', totalChance, 'Derived');
  add('Mean PIIP Gas (BCF) — Lead Phase', sourceVal('Lead Resource Assessment', 'lead_piip_gas_mean'), 'Lead Resource Assessment');
  add('Mean PIIP Gas (BCF) — Pre-Drilling', sourceVal('Pre-Drilling Resource Assessment', 'pre_drill_piip_gas_mean'), 'Pre-Drilling Resource Assessment');
  add('Mean PIIP Gas (BCF) — Post-Drilling', sourceVal('Post-Drilling Resource Assessment', 'post_drill_piip_gas_mean'), 'Post-Drilling Resource Assessment');
  add('SARH Formation Prognosis — Pre-Drill', sourceVal('Well Proposal', 'sarh_formation_prognosis_pre_drill'), 'Well Proposal');
  add('SARH Formation Prognosis — Post-Drill', finalOrQuick('final_top_sarah_tvdss_ft', 'quicklook_top_sarah_tvdss_ft'), 'Final / Quicklook Logs');
  add('SARH Formation Thickness (ft) — Pre-Drill', sourceVal('Thickness Estimation', 'formation_thickness_ft'), 'Thickness Estimation');
  add('SARH Formation Thickness (ft) — Post-Drill', finalOrQuick('final_formation_thickness_ft', 'quicklook_formation_thickness_ft'), 'Final / Quicklook Logs');
  add('Pay Thickness (ft)', finalOrQuick('final_pay_thickness_ft', 'quicklook_pay_thickness_ft'), 'Final / Quicklook Logs');
  add('PHIT (%)', finalOrQuick('final_average_porosity_pct', 'quicklook_average_porosity_pct'), 'Final / Quicklook Logs');
  add('SWT (%)', finalOrQuick('final_average_swt_pct', 'quicklook_average_swt_pct'), 'Final / Quicklook Logs');
  add('Fluid Type', finalOrQuick('final_fluid_type', 'quicklook_fluid_type'), 'Final / Quicklook Logs');
  var flowback = sourceMap['Flowback Results'] || {};
  var flowbackMeta = schemaIndex('Flowback Results');
  Object.keys(flowbackMeta).forEach(function (key) {
    if (isFilled(flowback[key])) add(flowbackMeta[key].label, flowback[key], 'Flowback Results');
  });
  return rows.join('');
}

export function renderRightPanel(tasks) {
  var applicableTasks = tasks.filter(function (task) { return task.status !== 'Not Applicable'; });
  var completed = applicableTasks.filter(function (task) { return DONE[task.status] && task.status !== 'Not Applicable'; }).length;
  var percent = applicableTasks.length ? Math.round((completed / applicableTasks.length) * 100) : 0;
  byId('progress-percent').textContent = percent + '%';
  byId('progress-count').textContent = completed + ' / ' + applicableTasks.length;

  var isBP = Number(Store.project.business_plan_enabled || 0) === 1;
  var isActive = Number(Store.project.active_well_enabled || 0) === 1;
  var year = Number(Store.project.business_plan_year || new Date().getFullYear());
  if (year < 2026 || year > 2040) year = 2026;
  var items = [summaryItemMarkup('Lead / Well', Store.project.project_name || '-', '', 'summary-item-primary', false)];
  if (isBP) {
    var yearSelect = '<select id="summary-bp-year" class="summary-year" aria-label="Business Plan Year">' + range(2026, 2040).map(function (value) { return '<option ' + (Number(value) === year ? 'selected' : '') + '>' + value + '</option>'; }).join('') + '</select>';
    items.push(summaryItemMarkup('BP Year', yearSelect, '', 'summary-item-control', true));
  }
  var recordKind = String(Store.project.pipeline_type || '').toLowerCase() === 'bp' ? 'Well' : 'Lead';
  var leadSnapshotFields = (Store.leadSummary && Store.leadSummary.fields) || {};
  var hasLeadSnapshot = recordKind === 'Well' && Object.keys(leadSnapshotFields).length > 0;
  var leadSnapshotHtml = hasLeadSnapshot ?
    '<div class="lead-summary-toggle"><button id="toggle-lead-summary" type="button" class="ghost">Lead Summary</button></div>' +
    '<div id="lead-summary-snapshot" class="summary-grid hidden"><div class="summary-item summary-item-primary"><div class="summary-item-label"><span>Lead Summary at BP Promotion</span></div><div class="summary-item-value">Captured ' + esc((Store.leadSummary && Store.leadSummary.captured_at) || '') + '</div></div>' + curatedOverviewMarkup(leadSnapshotFields) + '</div>' : '';
  var summaryHtml =
    '<div class="flag-controls"><label><input id="summary-bp-flag" type="checkbox" ' + (isBP ? 'checked' : '') + '> Business Plan</label><label><input id="summary-active-flag" type="checkbox" ' + (isActive ? 'checked' : '') + '> Active Well</label></div>' +
    '<div class="summary-grid">' + items.join('') + curatedOverviewMarkup() + '</div>' +
    leadSnapshotHtml +
    '<div class="record-actions"><button id="rename-record" type="button" class="ghost">Rename ' + recordKind + '</button><button id="delete-record" type="button" class="danger">Archive ' + recordKind + '</button></div>';
  byId('summary-title').textContent = recordKind + ' Summary';
  byId('lead-summary').innerHTML = summaryHtml;

  var bpFlag = byId('summary-bp-flag');
  var activeFlag = byId('summary-active-flag');
  var bpYear = byId('summary-bp-year');
  if (bpFlag) bpFlag.addEventListener('change', function () { saveProjectFlags({ business_plan_enabled: bpFlag.checked, business_plan_year: bpFlag.checked ? year : null }); });
  if (activeFlag) activeFlag.addEventListener('change', function () { saveProjectFlags({ active_well_enabled: activeFlag.checked }); });
  if (bpYear) bpYear.addEventListener('change', function () { saveProjectFlags({ business_plan_enabled: true, business_plan_year: bpYear.value }); });
  var leadSummaryToggle = byId('toggle-lead-summary');
  if (leadSummaryToggle) leadSummaryToggle.addEventListener('click', function () {
    var panel = byId('lead-summary-snapshot');
    if (!panel) return;
    var opening = panel.classList.contains('hidden');
    panel.classList.toggle('hidden', !opening);
    leadSummaryToggle.textContent = opening ? 'Hide Lead Summary' : 'Lead Summary';
  });
  var renameButton = byId('rename-record');
  var deleteButton = byId('delete-record');
  if (renameButton) renameButton.addEventListener('click', renameSelectedProject);
  if (deleteButton) deleteButton.addEventListener('click', deleteSelectedProject);
}
export function refreshAfterRecordChange(message) {
  return API.detail(Store.projectId)
    .then(function (detail) {
      var currentTaskId = Store.task && Store.task.task_id;
      Store.project = detail.project || {};
      Store.tasks = detail.tasks || [];
      Store.allFields = detail.fields || {};
      Store.leadSummary = detail.lead_summary || null;
    Store.overview = detail.overview || null;
      renderDetail();
      loadComponent(Store.tasks.find(function (task) { return task.task_id === currentTaskId; }) || chooseInitialTask(tasksForPipeline(Store.pipeline)));
      refreshAllBoards();
      if (message) msg(message, 'success');
    });
}
export async function renameSelectedProject() {
  if (!Store.projectId || !Store.project) return;
  var recordKind = String(Store.project.pipeline_type || '').toLowerCase() === 'bp' ? 'Well' : 'Lead';
  var nextName = await promptDialog({ title: 'Rename ' + recordKind, message: '', initialValue: Store.project.project_name || '' });
  if (nextName === null) return;
  if (!nextName) return msg(recordKind + ' name is required.', 'error');
  if (nextName === String(Store.project.project_name || '').trim()) return;
  API.rename(Store.projectId, { new_name: nextName, changed_by: currentUserName() })
    .then(function () { return refreshAfterRecordChange(recordKind + ' renamed.'); })
    .catch(function (error) { msg(error.message, 'error'); });
}
export async function deleteSelectedProject() {
  if (!Store.projectId || !Store.project) return;
  var recordKind = String(Store.project.pipeline_type || '').toLowerCase() === 'bp' ? 'Well' : 'Lead';
  var name = Store.project.project_name || recordKind;
  var confirmed = await confirmDialog({
    title: 'Archive ' + recordKind,
    message: 'Archive ' + recordKind.toLowerCase() + ' "' + name + '"? Its components, saved inputs, and audit trail will be preserved.',
    confirmLabel: 'Archive',
    danger: true
  });
  if (!confirmed) return;
  API.deleteProject(Store.projectId).then(function () {
    resetSelection();
    byId('detail-shell').classList.add('hidden');
    refreshAllBoards();
    refreshAudit();
    msg(recordKind + ' archived.', 'success');
  }).catch(function (error) { msg(error.message, 'error'); });
}

export function saveProjectFlags(payload) {
  if (!Store.projectId) return;
  payload.changed_by = currentUserName();
  API.flags(Store.projectId, payload).then(function () {
    return API.detail(Store.projectId);
  }).then(function (detail) {
    Store.project = detail.project || {};
    Store.tasks = detail.tasks || [];
    Store.allFields = detail.fields || {};
    Store.leadSummary = detail.lead_summary || null;
    Store.overview = detail.overview || null;
    Store.pipeline = String(Store.project.pipeline_type || '').toLowerCase() === 'bp' ? 'bp' : 'prospect';
    renderDetail();
    loadComponent(chooseInitialTask(tasksForPipeline(Store.pipeline)));
    refreshAllBoards();
    msg('Lead / well flags updated.', 'success');
  }).catch(function (error) { msg(error.message, 'error'); });
}
