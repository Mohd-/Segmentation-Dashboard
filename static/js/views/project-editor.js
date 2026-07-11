import { byId, all, esc, range, msg, statusChip } from '../dom.js';
import { API } from '../api.js';
import { currentUserName, Store, resetSelection } from '../state.js';
import { SCHEMA } from '../schema.js';
import { confirmDialog } from '../dialog.js';
import {
  renderFields, getFields, updateConditionalVisibility,
  formationRowsForSave, formationPhaseDirty, clearFormationPhaseDirty
} from './detail-form.js';
import { openDetail } from './detail.js';
import { refreshAllBoards } from './pipeline.js';
import { refreshAudit } from './audit.js';

// The full-project editor: one flat page exposing EVERY field of a project --
// its properties plus every component's schema fields + comments -- opened by
// clicking a well name in the Portfolio table. It shares the same single Store
// as the pipeline detail view (one record open at a time); openProjectEditor
// hides #detail-shell, and both showTab and openDetail hide #project-editor, so
// the three never show together.
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

// Head: back-to-portfolio, the project name + subtitle, and a jump into the
// pipeline detail view for the same record.
function headMarkup(project) {
  var pipelineLabel = String(project.pipeline_type || '').toLowerCase() === 'bp'
    ? 'Business Plan Execution' : 'Prospect Maturation';
  return '<div class="pe-head">' +
    '<button id="pe-back" type="button" class="ghost">&larr; Back to Portfolio</button>' +
    '<div class="pe-head-titles">' +
    '<h2 id="pe-name">' + esc(project.project_name || 'Lead / Well') + '</h2>' +
    '<p class="pe-subtitle">All project fields</p></div>' +
    '<button id="pe-open-pipeline" type="button" class="ghost" title="Open in ' + esc(pipelineLabel) + '">Open in pipeline view</button>' +
    '</div>';
}

// Properties card: the project-level columns (name, lead X/Y, BP + year,
// active well) saved in one PATCH /rename, plus the Archive danger action.
function propertiesMarkup(project) {
  var isBP = Number(project.business_plan_enabled || 0) === 1;
  var isActive = Number(project.active_well_enabled || 0) === 1;
  var year = Number(project.business_plan_year || new Date().getFullYear());
  if (year < 2026 || year > 2040) year = 2026;
  var yearOptions = range(2026, 2040).map(function (value) {
    return '<option ' + (Number(value) === year ? 'selected' : '') + '>' + value + '</option>';
  }).join('');
  return '<div class="pe-component pe-properties">' +
    '<div class="pe-component-head"><h3>Properties</h3></div>' +
    '<div class="pe-properties-grid">' +
    '<label>Name<input id="pe-prop-name" value="' + esc(project.project_name || '') + '"></label>' +
    '<label>Lead X<input id="pe-prop-x" type="number" step="any" value="' + esc(project.lead_x == null ? '' : project.lead_x) + '"></label>' +
    '<label>Lead Y<input id="pe-prop-y" type="number" step="any" value="' + esc(project.lead_y == null ? '' : project.lead_y) + '"></label>' +
    '<label class="check-label"><input id="pe-prop-bp" type="checkbox" ' + (isBP ? 'checked' : '') + '> Business Plan</label>' +
    '<label id="pe-prop-year-field" class="pe-prop-year' + (isBP ? '' : ' hidden') + '">BP Year<select id="pe-prop-year">' + yearOptions + '</select></label>' +
    '<label class="check-label"><input id="pe-prop-active" type="checkbox" ' + (isActive ? 'checked' : '') + '> Active Well</label>' +
    '</div>' +
    '<div class="pe-properties-actions">' +
    '<button id="pe-save-props" type="button">Save Properties</button>' +
    '<button id="pe-archive" type="button" class="danger">Archive</button>' +
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
  byId('pe-back').addEventListener('click', backToPortfolio);
  byId('pe-open-pipeline').addEventListener('click', function () {
    openDetail(Store.projectId, String((Store.project || {}).pipeline_type || '').toLowerCase() === 'bp' ? 'bp' : 'prospect');
  });
  var bpFlag = byId('pe-prop-bp');
  bpFlag.addEventListener('change', function () {
    byId('pe-prop-year-field').classList.toggle('hidden', !bpFlag.checked);
  });
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

function backToPortfolio() {
  byId('project-editor').classList.add('hidden');
  byId('tab-portfolio').scrollIntoView({ behavior: 'smooth', block: 'start' });
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
  button.disabled = true;
  API.updateTask(task.task_id, {
    fields: getFields(root),
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
  var isBP = Number(project.business_plan_enabled || 0) === 1;
  byId('pe-prop-bp').checked = isBP;
  byId('pe-prop-year-field').classList.toggle('hidden', !isBP);
  if (project.business_plan_year) byId('pe-prop-year').value = String(project.business_plan_year);
  byId('pe-prop-active').checked = Number(project.active_well_enabled || 0) === 1;
}

// Save every project property in one PATCH /rename (the endpoint applies them
// all). BP year is only sent when Business Plan is on. Re-fetch so derived
// values and the boards adopt the change; only the head title and the
// properties inputs are updated in the DOM (see syncPropertiesInputs).
function saveProperties() {
  var button = byId('pe-save-props');
  var bpOn = byId('pe-prop-bp').checked;
  var payload = {
    new_name: byId('pe-prop-name').value,
    lead_x: byId('pe-prop-x').value,
    lead_y: byId('pe-prop-y').value,
    business_plan_enabled: bpOn,
    active_well_enabled: byId('pe-prop-active').checked,
    changed_by: currentUserName()
  };
  if (bpOn) payload.business_plan_year = byId('pe-prop-year').value;
  button.disabled = true;
  API.rename(Store.projectId, payload).then(function () {
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

// The editor's own Archive: same confirm copy as detail.js's version, but it
// hides the editor (not the detail shell) and returns to the portfolio.
// refreshAllBoards covers the portfolio table too, so the archived record
// drops out of every view.
async function archiveProject() {
  var recordKind = String((Store.project || {}).pipeline_type || '').toLowerCase() === 'bp' ? 'Well' : 'Lead';
  var name = (Store.project || {}).project_name || recordKind;
  var confirmed = await confirmDialog({
    title: 'Archive ' + recordKind,
    message: 'Archive ' + recordKind.toLowerCase() + ' "' + name + '"? Its components, saved inputs, and audit trail will be preserved.',
    confirmLabel: 'Archive',
    danger: true
  });
  if (!confirmed) return;
  API.deleteProject(Store.projectId).then(function () {
    resetSelection();
    byId('project-editor').classList.add('hidden');
    byId('tab-portfolio').scrollIntoView({ behavior: 'smooth', block: 'start' });
    refreshAllBoards();
    refreshAudit();
    msg(recordKind + ' archived.', 'success');
  }).catch(function (error) { msg(error.message, 'error'); });
}
