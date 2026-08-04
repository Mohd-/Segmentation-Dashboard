import { byId, all, esc, msg, statusChip } from '../dom.js';
import { ICONS } from '../icons.js';
import { API } from '../api.js';
import { currentUserName, Store, resetSelection } from '../state.js';
import { activateTab, scrollToTab } from '../navigation.js';
import { SCHEMA, validateStepFields } from '../schema.js';
import { confirmDialog } from '../dialog.js';
import { canTransitionPhase, promoteProject, recallProject } from './transitions.js';
import {
  renderFields, getFields, updateConditionalVisibility,
  formationRowsForSave, formationPhaseDirty, clearFormationPhaseDirty
} from './detail-form.js';
import { openDetail, tasksForPipeline } from './detail.js';
import { refreshAllBoards } from './pipeline.js';
import { refreshPortfolio } from './portfolio.js';
import { refreshAudit } from './audit.js';

// The full-project editor: one flat page exposing EVERY field of a project --
// its properties plus every component's schema fields + comments. It is a
// secondary action from pipeline detail (Portfolio names now open the correct
// pipeline directly). It shares the same single Store as the detail view; only
// one record workspace is visible at a time.
export function openProjectEditor(projectId) {
  Store.projectId = projectId;
  byId('detail-shell').classList.add('hidden');
  var shell = byId('project-editor');
  shell.classList.remove('hidden');
  API.detail(projectId).then(function (detail) {
    adoptDetail(detail);
    renderEditor();
    shell.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }).catch(function (error) { msg(error.message, 'error'); });
}

// Same Store fields openDetail adopts from a /detail payload. Factored out so
// the initial open and every post-save re-fetch stay in lockstep.
function adoptDetail(detail) {
  Store.project = detail.project || {};
  Store.tasks = detail.tasks || [];
  Store.allFields = detail.fields || {};
  Store.leadSummary = detail.lead_summary || null;
  Store.overview = detail.overview || null;
  Store.formations = detail.formations || [];
}

// Head: the back control, the project name + subtitle, and a jump into the
// pipeline detail view for the same record.
//
// KI-003: the back control used to say "Back to Portfolio" and go there
// unconditionally, even though this editor is only ever opened FROM a record's
// pipeline detail (the Lead Summary gear's "Edit All Inputs", or the BP well
// shell's "Edit all project fields"). If Portfolio had never been visited its
// data had never been fetched and the user landed on an empty table, having
// also lost the pipeline/detail context they came from. It now returns to the
// originating record's detail view in its own pipeline -- the acceptance
// criterion's preferred destination -- and only falls back to Portfolio when
// there is no record to go back to, refreshing it first so the table is never
// empty on arrival (see backFromEditor).
function headMarkup(project) {
  var isBP = String(project.pipeline_type || '').toLowerCase() === 'bp';
  var pipelineLabel = isBP ? 'Business Plan Execution' : 'Prospect Maturation';
  return '<div class="pe-head">' +
    '<button id="pe-back" type="button" class="ghost">' + ICONS['arrow-left'] +
    ' Back to ' + (isBP ? 'Well' : 'Lead') + '</button>' +
    '<div class="pe-head-titles">' +
    '<h2 id="pe-name">' + esc(project.project_name || 'Lead / Well') + '</h2>' +
    '<p class="pe-subtitle">All project fields</p></div>' +
    '<button id="pe-open-pipeline" type="button" class="ghost" title="Open in ' + esc(pipelineLabel) + '">Open in pipeline view</button>' +
    '</div>';
}

// Phase row inside the Properties card: a chip naming the current phase plus
// the supervisor-only transition action. Re-rendered in place (see
// syncPhaseRow) after a properties save or a phase transition.
function phaseRowMarkup(project) {
  var isBP = Number(project.business_plan_enabled || 0) === 1;
  var chip = isBP
    ? 'BP Well' + (project.business_plan_year ? ' · ' + esc(project.business_plan_year) : '')
    : 'Lead';
  var action = '';
  if (canTransitionPhase()) {
    action = isBP
      ? '<button id="pe-phase-action" type="button" class="ghost danger-outline pe-phase-btn">Recall to Lead Phase…</button>'
      : '<button id="pe-phase-action" type="button" class="ghost pe-phase-btn">Promote to BP Well…</button>';
  }
  return '<span class="pe-phase-chip">' + chip + '</span>' + action;
}

// Properties card: the project-level columns (name, lead X/Y, active well)
// saved via PATCH /rename + PATCH /flags, the phase row (promote/recall), and
// the Delete danger action (the backend keeps its recoverable soft-delete).
function propertiesMarkup(project) {
  var isActive = Number(project.active_well_enabled || 0) === 1;
  return '<div class="pe-component pe-properties">' +
    '<div class="pe-component-head"><h3>Properties</h3></div>' +
    '<div id="pe-phase-row" class="pe-phase-row">' + phaseRowMarkup(project) + '</div>' +
    '<div class="pe-properties-grid">' +
    '<label>Name<input id="pe-prop-name" value="' + esc(project.project_name || '') + '"></label>' +
    '<label>Lead X<input id="pe-prop-x" type="number" step="any" value="' + esc(project.lead_x == null ? '' : project.lead_x) + '"></label>' +
    '<label>Lead Y<input id="pe-prop-y" type="number" step="any" value="' + esc(project.lead_y == null ? '' : project.lead_y) + '"></label>' +
    '<label class="check-label"><input id="pe-prop-active" type="checkbox" ' + (isActive ? 'checked' : '') + '> Active Well</label>' +
    '</div>' +
    '<div class="pe-properties-actions">' +
    '<button id="pe-save-props" type="button">Save Properties</button>' +
    '<button id="pe-archive" type="button" class="danger">Delete</button>' +
    '</div></div>';
}

// One component card: a display-only header (sequence badge, name, status chip),
// its schema fields grid (filled by renderFields after innerHTML is set), a
// comments textarea, and a per-component Save. Components with an empty schema
// still render (just comments + Save).
function componentMarkup(task) {
  return '<div class="pe-component" data-task-id="' + task.task_id + '">' +
    '<div class="pe-component-head">' +
    '<span class="component-number">' + esc(task.sequence_no || '') + '</span>' +
    '<b class="pe-component-name">' + esc(task.task_name) + '</b>' +
    statusChip(task.status) + '</div>' +
    '<div class="dynamic-fields" id="pe-fields-' + task.task_id + '"></div>' +
    '<label class="wide-field">Comments<textarea id="pe-comments-' + task.task_id + '">' + esc(task.comments || '') + '</textarea></label>' +
    '<div class="pe-component-actions"><button type="button" class="pe-save" data-task-id="' + task.task_id + '">Save</button></div>' +
    '</div>';
}

// Group the (sequence-ordered) tasks by stage_group, same trick as the rail:
// a new stage begins wherever stage_group changes between consecutive tasks.
function groupByStage(tasks) {
  var groups = [];
  tasks.forEach(function (task) {
    var group = groups[groups.length - 1];
    if (!group || group.stage !== task.stage_group) { group = { stage: task.stage_group, tasks: [] }; groups.push(group); }
    group.tasks.push(task);
  });
  return groups;
}

// One card's fields grid, rendered into its own root with an onInput that only
// refreshes that card's conditional visibility -- no live summary preview in
// this view (the summary panel belongs to the pipeline detail). Called for
// every card on first render and for JUST the saved card after a save.
function renderCardFields(task) {
  var root = byId('pe-fields-' + task.task_id);
  if (!root) return;
  renderFields(task.task_name, (Store.allFields || {})[task.task_name] || {}, root, function () {
    updateConditionalVisibility(root);
  });
}

function renderEditor() {
  var project = Store.project || {};
  var groups = groupByStage(Store.tasks || []);
  var sections = groups.map(function (group) {
    return '<div class="pe-stage-head">' + esc(group.stage) + '</div>' +
      group.tasks.map(componentMarkup).join('');
  }).join('');
  byId('project-editor').innerHTML =
    headMarkup(project) + propertiesMarkup(project) + sections;

  (Store.tasks || []).forEach(renderCardFields);

  bindEditor();
}

function bindEditor() {
  byId('pe-back').addEventListener('click', backFromEditor);
  byId('pe-open-pipeline').addEventListener('click', function () {
    openDetail(Store.projectId, String((Store.project || {}).pipeline_type || '').toLowerCase() === 'bp' ? 'bp' : 'prospect');
  });
  bindPhaseAction();
  byId('pe-save-props').addEventListener('click', saveProperties);
  byId('pe-archive').addEventListener('click', archiveProject);
  all('.pe-save', byId('project-editor')).forEach(function (button) {
    button.addEventListener('click', function () {
      var taskId = Number(button.getAttribute('data-task-id'));
      var task = (Store.tasks || []).find(function (item) { return item.task_id === taskId; });
      if (task) saveComponentCard(task, button);
    });
  });
}

/* KI-003's fix. Leaving the all-fields editor returns to where it was opened
   from: the same record's detail view, in the record's OWN pipeline (openDetail
   activates that tab, hides this editor's sibling shell and re-fetches the
   record, so nothing is stale). Only a stateless editor -- no selected record
   at all -- falls through to Portfolio, and that fallback now REFRESHES the
   portfolio before showing it, so a session that has never opened the tab can
   no longer land on an empty table. Exported so the regression test can drive
   both paths directly. */
export function backFromEditor() {
  byId('project-editor').classList.add('hidden');
  if (Store.projectId) {
    openDetail(Store.projectId, String((Store.project || {}).pipeline_type || '').toLowerCase() === 'bp' ? 'bp' : 'prospect');
    return;
  }
  activateTab('portfolio');
  scrollToTab('portfolio');
  refreshPortfolio();
}

// Re-render the phase row in place (chip + action button) and rebind its
// handler. Deliberately NOT a full renderEditor: like syncPropertiesInputs, a
// phase change must not wipe unsaved typing in the component cards.
function syncPhaseRow() {
  var row = byId('pe-phase-row');
  if (!row) return;
  row.innerHTML = phaseRowMarkup(Store.project || {});
  bindPhaseAction();
}

function bindPhaseAction() {
  var button = byId('pe-phase-action');
  if (button) button.addEventListener('click', transitionPhase);
}

// Promote/recall from the Properties card. transitions.js owns the confirm
// dialog + PATCH /flags; on success re-fetch and sync the head, properties
// inputs and phase row (component cards keep their unsaved typing).
function transitionPhase() {
  var project = Store.project || {};
  var isBP = Number(project.business_plan_enabled || 0) === 1;
  var actor = currentUserName();
  var transition = isBP
    ? recallProject(project, actor)
    : promoteProject(project, tasksForPipeline('prospect'), actor);
  transition.then(function (result) {
    if (result === null) return null; // dialog cancelled
    return API.detail(Store.projectId).then(function (detail) {
      adoptDetail(detail);
      syncPropertiesInputs();
      syncPhaseRow();
      refreshAllBoards();
      msg(isBP ? 'Recalled to lead phase.' : 'Promoted to BP well.', 'success');
    });
  }).catch(function (error) { msg(error.message, 'error'); });
}

// Per-component save. Comments and priority MUST be echoed: save_task clears an
// absent comments key and defaults an absent priority to Medium. A component
// carrying a formations mini-sheet also PUTs its phase's rows when dirty.
//
// On success only the SAVED card is refreshed in place: Store is re-fetched
// (so the next save of any card reads a fresh revision -- the .pe-save handler
// resolves its task from Store.tasks at click time), then this card's fields
// grid re-renders so recomputed readonly outputs (Reservoir/Seal CoS) appear
// and its status chip updates. Every other card's DOM -- including unsaved
// typing -- is left untouched, and the comments textarea stays as typed (it
// was just saved).
function saveComponentCard(task, button) {
  var root = byId('pe-fields-' + task.task_id);
  var comments = byId('pe-comments-' + task.task_id);
  var fields = getFields(root);
  // Same generic sanity checks as the pipeline detail view's saveComponent
  // (schema.js's validateStepFields) -- this card saves the identical
  // task_name/fields shape through the identical PATCH /api/tasks/<id> path,
  // so it needs the identical guard before hitting the network.
  var fieldsError = validateStepFields(task.task_name, fields);
  if (fieldsError) return msg(fieldsError, 'error');
  button.disabled = true;
  API.updateTask(task.task_id, {
    fields: fields,
    comments: comments.value,
    priority: task.priority || 'Medium',
    revision: task.revision,
    changed_by: currentUserName()
  }).then(function () {
    var formationsField = (SCHEMA[task.task_name] || []).find(function (item) { return item.type === 'formations'; });
    if (formationsField && formationPhaseDirty(formationsField.phase)) {
      return API.saveFormations(Store.projectId, {
        phase: formationsField.phase,
        rows: formationRowsForSave(formationsField.phase),
        changed_by: currentUserName(),
        source_task_id: task.task_id
      }).then(function () { clearFormationPhaseDirty(formationsField.phase); });
    }
    return null;
  }).then(function () {
    return API.detail(Store.projectId);
  }).then(function (detail) {
    adoptDetail(detail);
    var fresh = (Store.tasks || []).find(function (item) { return item.task_id === task.task_id; }) || task;
    renderCardFields(fresh);
    var chip = root.closest('.pe-component').querySelector('.pe-component-head .status');
    if (chip) chip.outerHTML = statusChip(fresh.status);
    refreshAllBoards();
    msg('Component saved.', 'success');
  }).catch(function (error) {
    // Leave the form as typed so the save can be retried.
    msg(error.message, 'error');
  }).finally(function () {
    button.disabled = false;
  });
}

// Sync the head title and the properties card's own inputs from the fresh
// Store.project after a properties save. Deliberately NOT a re-render of the
// component cards: a properties save changes nothing task-side, and rebuilding
// the cards would wipe any unsaved typing in them.
function syncPropertiesInputs() {
  var project = Store.project || {};
  byId('pe-name').textContent = project.project_name || 'Lead / Well';
  byId('pe-prop-name').value = project.project_name || '';
  byId('pe-prop-x').value = project.lead_x == null ? '' : project.lead_x;
  byId('pe-prop-y').value = project.lead_y == null ? '' : project.lead_y;
  byId('pe-prop-active').checked = Number(project.active_well_enabled || 0) === 1;
}

// Save the record columns (name, lead X/Y) in one PATCH /rename. The Active
// Well checkbox goes through PATCH /flags instead — and only when it actually
// changed, chained after the rename in the same click. Phase (Business Plan)
// is NOT saved here; that's the phase row's promote/recall. One re-fetch at
// the end; only the head title and the properties inputs are updated in the
// DOM (see syncPropertiesInputs).
function saveProperties() {
  var button = byId('pe-save-props');
  var payload = {
    new_name: byId('pe-prop-name').value,
    lead_x: byId('pe-prop-x').value,
    lead_y: byId('pe-prop-y').value,
    changed_by: currentUserName()
  };
  var activeChecked = byId('pe-prop-active').checked;
  var activeChanged = activeChecked !== (Number((Store.project || {}).active_well_enabled || 0) === 1);
  button.disabled = true;
  API.rename(Store.projectId, payload).then(function () {
    if (!activeChanged) return null;
    return API.flags(Store.projectId, { active_well_enabled: activeChecked, changed_by: currentUserName() });
  }).then(function () {
    return API.detail(Store.projectId);
  }).then(function (detail) {
    adoptDetail(detail);
    syncPropertiesInputs();
    refreshAllBoards();
    msg('Properties saved.', 'success');
  }).catch(function (error) {
    msg(error.message, 'error');
  }).finally(function () {
    button.disabled = false;
  });
}

// The editor's own Delete action: same confirm copy as detail.js's version, but it
// hides the editor (not the detail shell) and returns to the portfolio.
// refreshAllBoards covers the portfolio table too, so the archived record
// drops out of every view.
async function archiveProject() {
  var recordKind = String((Store.project || {}).pipeline_type || '').toLowerCase() === 'bp' ? 'Well' : 'Lead';
  var name = (Store.project || {}).project_name || recordKind;
  var confirmed = await confirmDialog({
    title: 'Delete ' + recordKind,
    message: 'Delete ' + recordKind.toLowerCase() + ' "' + name + '"? Its components, saved inputs, and audit trail will be preserved.',
    confirmLabel: 'Delete',
    danger: true
  });
  if (!confirmed) return;
  API.deleteProject(Store.projectId).then(function () {
    resetSelection();
    byId('project-editor').classList.add('hidden');
    activateTab('portfolio');
    scrollToTab('portfolio');
    refreshAllBoards();
    refreshAudit();
    msg(recordKind + ' deleted.', 'success');
  }).catch(function (error) { msg(error.message, 'error'); });
}
