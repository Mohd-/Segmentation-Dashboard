import { byId, all, esc, isFilled, msg } from '../dom.js';
import { API } from '../api.js';
import { currentUserName, Store, resetSelection } from '../state.js';
import { BP_STAGES, PROSPECT_STAGES, DONE, SEISMIC_BLOCKS } from '../schema.js';
import { confirmDialog, promptDialog } from '../dialog.js';
import { canTransitionPhase, promoteProject, recallProject } from './transitions.js';
import { loadComponent, LATEST_PIIP_SOURCES } from './detail-form.js';
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
  // One record view at a time: the full-project editor and the pipeline detail
  // are mutually exclusive panels.
  byId('project-editor').classList.add('hidden');
  byId('detail-shell').classList.remove('hidden');
  API.detail(projectId).then(function (detail) {
    Store.project = detail.project || {};
    Store.tasks = detail.tasks || [];
    Store.allFields = detail.fields || {};
    Store.leadSummary = detail.lead_summary || null;
    Store.overview = detail.overview || null;
    Store.formations = detail.formations || [];
    renderDetail();
    loadComponent(chooseInitialTask(tasksForPipeline(Store.pipeline)));
    byId('detail-shell').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }).catch(function (error) { msg(error.message, 'error'); });
}
// Monochrome stage glyphs for the rail headers (must read at ~14px).
// \uFE0E (variation selector-15) forces text presentation so no color emoji
// sneak in. Keys match the stage_group values from workflow.py / /api/meta;
// unknown stages fall back to a plain bullet.
var STAGE_ICONS = {
  'Lead Identification': '\u25CE',      // ◎ bullseye
  'Risking': '\u2696\uFE0E',             // ⚖ scales
  'Segmentation': '\u25A6',             // ▦ grid
  'Pre-Well Delivery': '\u26F3\uFE0E',   // ⛳ flag
  'Well Delivery': '\u2692\uFE0E',       // ⚒ hammer and pick
  'Post-Drilling': '\u26CF\uFE0E',       // ⛏ pick
  'Post-Testing': '\u2713'              // ✓ check
};

// Rail accordion: exactly one stage group open at a time (zero open allowed).
// State is module-level so it survives the re-render after every save/refresh,
// and resets when the selected project changes (see renderDetail below). The
// selected task's stage is revealed after render by revealTaskStage().
var openStage = null;
var openStageProjectId = null;

// Sync the already-rendered rail to `openStage`: toggle each header's
// open/aria-expanded and each body's collapsed class. Shared by the header
// click handler and revealTaskStage so neither re-renders the whole list.
function syncStageOpenState() {
  all('.rail-stage-head').forEach(function (head) {
    var isOpen = head.getAttribute('data-stage') === openStage;
    head.classList.toggle('open', isOpen);
    head.setAttribute('aria-expanded', String(isOpen));
  });
  all('.rail-stage-body').forEach(function (body) {
    body.classList.toggle('collapsed', body.getAttribute('data-stage') !== openStage);
  });
}

// Open the stage that owns `task` and sync the rendered rail in place. Called
// from detail-form.js loadComponent (renderDetail runs before the task is
// picked, so the default-open stage is set here rather than at render time).
export function revealTaskStage(task) {
  if (!task) return;
  openStage = task.stage_group;
  openStageProjectId = Store.projectId;
  syncStageOpenState();
}

export function renderDetail() {
  var tasks = tasksForPipeline(Store.pipeline);
  byId('detail-name').textContent = Store.project.project_name || 'Lead / Well';
  byId('detail-subtitle').textContent = Store.pipeline === 'bp' ? 'Business Plan Execution' : 'Prospect Maturation';
  byId('back-to-overview').textContent = '← Back to ' + (Store.pipeline === 'bp' ? 'Business Plan Execution' : 'Prospect Maturation');
  // Accordion state is per-project: a fresh selection starts fully collapsed
  // (revealTaskStage opens the selected task's stage right after this render).
  if (Store.projectId !== openStageProjectId) { openStage = null; openStageProjectId = Store.projectId; }
  // Tasks arrive ordered by sequence_no, so a new stage group begins wherever
  // stage_group changes between consecutive items.
  var groups = [];
  tasks.forEach(function (task) {
    var group = groups[groups.length - 1];
    if (!group || group.stage !== task.stage_group) { group = { stage: task.stage_group, tasks: [] }; groups.push(group); }
    group.tasks.push(task);
  });
  byId('component-list').innerHTML = groups.map(function (group) {
    var approved = group.tasks.filter(function (task) { return DONE[task.status]; }).length;
    var isOpen = group.stage === openStage;
    var items = group.tasks.map(function (task) {
      // status-<slug> colours the number badge (see components.css); same slug
      // the status chips use, so the token trios line up.
      var slug = String(task.status || 'Not Assigned').toLowerCase().replace(/\s+/g, '-');
      return '<button type="button" class="component-item status-' + slug + '" data-task-id="' + task.task_id + '"><span class="component-num">' + esc(task.sequence_no) + '</span><b>' + esc(task.task_name) + '</b></button>';
    }).join('');
    return '<div class="rail-stage">' +
      '<button type="button" class="rail-stage-head' + (isOpen ? ' open' : '') + '" data-stage="' + esc(group.stage) + '" aria-expanded="' + isOpen + '">' +
      '<span class="stage-icon" aria-hidden="true">' + (STAGE_ICONS[group.stage] || '•') + '</span>' +
      '<span class="rail-stage-name">' + esc(group.stage) + '</span>' +
      '<span class="rail-stage-count">' + approved + '/' + group.tasks.length + '</span>' +
      '<span class="rail-stage-chevron" aria-hidden="true"></span></button>' +
      '<div class="rail-stage-body' + (isOpen ? '' : ' collapsed') + '" data-stage="' + esc(group.stage) + '">' + items + '</div></div>';
  }).join('') || '<div class="empty-state">No components in this pipeline.</div>';
  all('.rail-stage-head').forEach(function (head) {
    head.addEventListener('click', function () {
      var stage = head.getAttribute('data-stage');
      // Toggle: clicking the open stage collapses it; else open it (and the
      // single-open sync closes whichever was open before).
      openStage = (openStage === stage) ? null : stage;
      openStageProjectId = Store.projectId;
      syncStageOpenState();
    });
  });
  all('.component-item').forEach(function (button) {
    button.addEventListener('click', function () {
      var taskId = Number(button.getAttribute('data-task-id'));
      loadComponent(Store.tasks.find(function (task) { return task.task_id === taskId; }));
    });
  });
  renderRightPanel(tasks);
}

export function parseRepeatableRows(value) {
  if (Array.isArray(value)) return value;
  try {
    var parsed = JSON.parse(value || '[]');
    return Array.isArray(parsed) ? parsed : [];
  } catch (error) { return []; }
}
// Reverse-lookup a block name from an AR number using the seismic map (meta,
// or the schema.js fallback). Used when a legacy row stored only the AR.
function blockForAr(map, ar) {
  if (!isFilled(ar) || !map) return '';
  var names = Object.keys(map);
  for (var i = 0; i < names.length; i += 1) {
    if ((map[names[i]] || []).map(String).indexOf(String(ar)) >= 0) return names[i];
  }
  return '';
}
export function reservoirCosSummary(fieldMap) {
  var sourceMap = fieldMap || Store.allFields;
  var rows = parseRepeatableRows(((sourceMap['Reservoir CoS'] || {}).reservoir_cos_rows) || '[]');
  var blocks = (Store.meta && Store.meta.seismic_blocks) || SEISMIC_BLOCKS;
  return rows.map(function (row) {
    if (!isFilled(row.reservoir_cos_pct)) return '';
    // Prefer the row's stored block; else reverse-lookup the AR in the map.
    // Degrade to AR-only, then to bare percent, when the block is unknown.
    var block = row.seismic_block || blockForAr(blocks, row.seismic_volume_ar_number);
    var parts = [];
    if (isFilled(block)) parts.push(block);
    if (isFilled(row.seismic_volume_ar_number)) parts.push('AR ' + row.seismic_volume_ar_number);
    var ref = parts.length ? parts.join(' · ') + ': ' : '';
    return ref + row.reservoir_cos_pct + '%';
  }).filter(Boolean).join(' · ');
}
// Latest gas P90/P10 pair. Same source precedence as LATEST_PIIP_SOURCES
// (newest assessment first), but reading each step's <prefix>_gas_p90 /
// <prefix>_gas_p10 keys (derived from the mean key). The first step with
// either filled supplies both values.
function latestGasPair() {
  for (var i = 0; i < LATEST_PIIP_SOURCES.length; i += 1) {
    var fields = Store.allFields[LATEST_PIIP_SOURCES[i][0]] || {};
    var p90 = fields[LATEST_PIIP_SOURCES[i][1].replace('_gas_mean', '_gas_p90')];
    var p10 = fields[LATEST_PIIP_SOURCES[i][1].replace('_gas_mean', '_gas_p10')];
    if (isFilled(p90) || isFilled(p10)) return { p90: p90, p10: p10, source: LATEST_PIIP_SOURCES[i][0] };
  }
  return { p90: '', p10: '', source: '' };
}

// One compact metric row: label (+ optional source note) and its value (— when
// blank). Kept tiny so the summary card stays far denser than the old tiles.
function metricRow(label, value, note) {
  var small = note ? '<small>' + esc(note) + '</small>' : '';
  return '<div class="summary-metric"><div class="summary-metric-label"><span>' + esc(label) + '</span>' + small + '</div><div class="summary-metric-value">' + (isFilled(value) ? esc(value) : '—') + '</div></div>';
}

// The gear popover, its outside-click/Escape dismissal, and the toggle button
// (static in index.html) are wired once — the button and document persist
// across re-renders, so byId resolves the freshly rendered popover at call
// time and no listeners stack.
var summarySettingsWired = false;
function wireSummarySettings() {
  if (summarySettingsWired) return;
  summarySettingsWired = true;
  var toggle = byId('summary-settings-toggle');
  function close() {
    var popover = byId('summary-settings');
    if (popover) popover.classList.add('hidden');
    if (toggle) toggle.setAttribute('aria-expanded', 'false');
  }
  if (toggle) toggle.addEventListener('click', function (event) {
    event.stopPropagation();
    var popover = byId('summary-settings');
    if (!popover) return;
    var opening = popover.classList.contains('hidden');
    popover.classList.toggle('hidden', !opening);
    toggle.setAttribute('aria-expanded', String(opening));
  });
  document.addEventListener('click', function (event) {
    var popover = byId('summary-settings');
    if (!popover || popover.classList.contains('hidden')) return;
    if (popover.contains(event.target) || (toggle && toggle.contains(event.target))) return;
    close();
  });
  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape') return;
    var popover = byId('summary-settings');
    if (!popover || popover.classList.contains('hidden')) return;
    close();
    if (toggle) toggle.focus();
  });
}

export function renderRightPanel(tasks) {
  // `tasks` is already scoped to the operating pipeline's stages (see
  // tasksForPipeline), so every row counts toward progress.
  var completed = tasks.filter(function (task) { return DONE[task.status]; }).length;
  var percent = tasks.length ? Math.round((completed / tasks.length) * 100) : 0;

  var isBP = Number(Store.project.business_plan_enabled || 0) === 1;
  var isActive = Number(Store.project.active_well_enabled || 0) === 1;
  var year = Number(Store.project.business_plan_year || new Date().getFullYear());
  if (year < 2026 || year > 2040) year = 2026;
  var recordKind = String(Store.project.pipeline_type || '').toLowerCase() === 'bp' ? 'Well' : 'Lead';

  var gas = latestGasPair();
  var trapCos = (Store.allFields['Trap CoS'] || {}).trap_cos_pct;
  var sealCos = (Store.allFields['Seal CoS'] || {}).seal_cos_pct;

  var progressHtml =
    '<div class="summary-progress"><div class="summary-progress-bar"><span style="width:' + percent + '%"></span></div>' +
    '<div class="summary-progress-figures"><b>' + percent + '%</b><small>' + completed + ' / ' + tasks.length + '</small></div></div>';
  // Phase row: where the record sits (Lead vs BP Well · year) plus the
  // supervisor-only transition action (transitions.js owns the confirm + PATCH).
  var phaseButtonHtml = '';
  if (canTransitionPhase()) {
    phaseButtonHtml = isBP
      ? '<button id="summary-phase-action" type="button" class="ghost danger-outline summary-phase-btn">Recall to Lead Phase…</button>'
      : '<button id="summary-phase-action" type="button" class="ghost summary-phase-btn">Promote to BP Well…</button>';
  }
  var phaseHtml = '<div class="summary-phase"><span class="summary-phase-label">' +
    (isBP ? 'BP Well · ' + esc(Store.project.business_plan_year || year) : 'Lead') +
    '</span>' + phaseButtonHtml + '</div>';
  var metricsHtml = '<div class="summary-metrics">' +
    metricRow('P90 Gas (BCF)', gas.p90, gas.source) +
    metricRow('P10 Gas (BCF)', gas.p10, gas.source) +
    metricRow('Reservoir CoS (%)', reservoirCosSummary(), 'Reservoir CoS') +
    metricRow('Trap CoS (%)', trapCos, 'Trap CoS') +
    metricRow('Seal CoS (%)', sealCos, 'Seal CoS') +
    '</div>';
  // Popover: what the compact card dropped but still needs a home. Phase moves
  // (promote/recall) live on the visible phase row, not here.
  var popoverHtml =
    '<div id="summary-settings" class="summary-popover hidden" role="dialog" aria-label="Manage ' + recordKind.toLowerCase() + '">' +
    '<label class="summary-popover-check"><input id="summary-active-flag" type="checkbox" ' + (isActive ? 'checked' : '') + '> Active Well</label>' +
    '<div class="summary-popover-actions"><button id="rename-record" type="button" class="ghost">Rename ' + recordKind + '</button><button id="delete-record" type="button" class="danger">Archive ' + recordKind + '</button></div></div>';

  byId('summary-title').textContent = recordKind + ' Summary';
  byId('lead-summary').innerHTML = progressHtml + phaseHtml + metricsHtml + popoverHtml;

  var activeFlag = byId('summary-active-flag');
  if (activeFlag) activeFlag.addEventListener('change', function () { saveProjectFlags({ active_well_enabled: activeFlag.checked }); });
  var phaseAction = byId('summary-phase-action');
  if (phaseAction) phaseAction.addEventListener('click', function () {
    var actor = currentUserName();
    var transition = isBP
      ? recallProject(Store.project, actor)
      : promoteProject(Store.project, tasksForPipeline('prospect'), actor);
    transition.then(function (result) {
      if (result === null) return; // dialog cancelled
      return refreshAfterFlagsChange(isBP ? 'Recalled to lead phase.' : 'Promoted to BP well.');
    }).catch(function (error) { msg(error.message, 'error'); });
  });
  var renameButton = byId('rename-record');
  var deleteButton = byId('delete-record');
  if (renameButton) renameButton.addEventListener('click', renameSelectedProject);
  if (deleteButton) deleteButton.addEventListener('click', deleteSelectedProject);
  wireSummarySettings();
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
    Store.formations = detail.formations || [];
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

// Post-flags refresh, shared by the Active Well checkbox (saveProjectFlags)
// and the phase-row promote/recall actions: re-fetch the detail payload, adopt
// whichever pipeline the record now belongs to, and re-render everything.
function refreshAfterFlagsChange(message) {
  return API.detail(Store.projectId).then(function (detail) {
    Store.project = detail.project || {};
    Store.tasks = detail.tasks || [];
    Store.allFields = detail.fields || {};
    Store.leadSummary = detail.lead_summary || null;
    Store.overview = detail.overview || null;
    Store.formations = detail.formations || [];
    Store.pipeline = String(Store.project.pipeline_type || '').toLowerCase() === 'bp' ? 'bp' : 'prospect';
    renderDetail();
    loadComponent(chooseInitialTask(tasksForPipeline(Store.pipeline)));
    refreshAllBoards();
    msg(message, 'success');
  });
}

export function saveProjectFlags(payload) {
  if (!Store.projectId) return;
  payload.changed_by = currentUserName();
  API.flags(Store.projectId, payload).then(function () {
    return refreshAfterFlagsChange('Lead / well flags updated.');
  }).catch(function (error) { msg(error.message, 'error'); });
}
