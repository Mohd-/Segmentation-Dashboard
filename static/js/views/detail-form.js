import { byId, all, esc, isFilled, truthy, msg } from '../dom.js';
import { API } from '../api.js';
import { ICONS } from '../icons.js';
import { currentUserName, canManageAssignments, isCurrentPipelineView, Store } from '../state.js';
import { SCHEMA, formationNames, FORMATION_METRICS, FLUID_TYPES, SEISMIC_BLOCKS, normalizeFlowbackStages, validateStepFields, numericFieldError, submitBlockedMessage } from '../schema.js';
import { calculateTrapCos, calculateSealCos } from '../cos-rules.js';
import { confirmDialog, promptDialog } from '../dialog.js';
import { renderDetail, renderRightPanel, chooseInitialTask, tasksForPipeline, parseRepeatableRows, refreshAfterRecordChange, revealTaskStage } from './detail.js';
import { refreshAllBoards } from './pipeline.js';
import { renderResourceCalculator, teardownResourceCalculator } from './resource-calculator.js';
// Card 2B: the consolidated Lead Assessment workspace, which REPLACES the
// generic per-step form for that stage's four steps on a lead page.
import {
  isLeadAssessmentStep, leadAssessmentActive, renderLeadAssessment,
  saveLeadAssessment, teardownLeadAssessment
} from './lead-assessment.js';
// Card 4B: the consolidated Staking Letters workspace -- the same arrangement
// one stage later, for Pre-Well Delivery's two letter steps.
import {
  isStakingLetterStep, stakingLettersActive, renderStakingLetters,
  saveStakingLetters, teardownStakingLetters
} from './staking-letters.js';
// Item A: prospect step pages auto-save. The controller lives in autosave.js;
// this module only (a) tells it when the mounted task changed and (b) keeps
// the focused control alive across the post-save re-render. Runtime-only
// cycle (autosave.js imports saveComponent back), same as detail.js's.
import { syncAutoSaveContext, flushAutoSave, captureEditorFocus, restoreEditorFocus } from './autosave.js';
import { applyApprovalActions, approvalContentLocked } from './approval-policy.js';
import {
  assignmentChecklistHtml, assignmentMembersHtml, wireAssignmentChecklist
} from '../ui/detail-shell.js';

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
  // Card 2A: a LEAD detail page carries no separate status badge beside the
  // step title -- its header is the assignee select, and
  // a fresh lead's badge only ever read "NOT ASSIGNED". The BP well page keeps
  // it. The class is rewritten wholesale on every render, so the visibility has
  // to be re-decided here rather than toggled once elsewhere.
  var shell = byId('detail-shell');
  var leadPage = !!shell && shell.classList.contains('detail-shell-lead');
  chip.className = 'status editor-status-chip ' + String(value).toLowerCase().replace(/\s+/g, '-') +
    (leadPage ? ' hidden' : '');
}

// Priority is a LEAD/WELL-LEVEL attribute since the ASAS redesign: the ONE
// chip lives in the detail shell header (views/detail.js renderLeadPriorityChip
// / cycleLeadPriorityChip), so the step editor header carries no per-step
// priority control any more.

// KI-002: whether the assignee control is interactive is a ROLE decision
// (plus "am I looking at this record's own pipeline"), never a side effect of
// some other sweep over the form. Both renderAssigneeSelect (async, resolves
// after /api/users) and setComponentReferenceMode (sync, runs twice per
// component load -- the second time after the fields request) call this ONE
// function, so whichever lands last still leaves the employee's control
// disabled instead of a dead, unauthorized dropdown.
function syncAssigneeGate(referenceOnly) {
  var trigger = byId('assigned-to');
  var reference = referenceOnly === undefined ? !isCurrentPipelineView() : !!referenceOnly;
  var permissions = (Store.task && Store.task.permissions) || {};
  var editable = canManageAssignments() && permissions.can_manage_assignments !== false && !reference;
  if (trigger) trigger.disabled = !editable;
  var group = byId('assignment-group');
  all('.assignee-remove', group).forEach(function (button) { button.disabled = !editable; });
  all('[data-assignment-name]', group).forEach(function (input) {
    var member = ((Store.task && Store.task.assignees) || []).filter(function (item) {
      return item.name === input.dataset.assignmentName;
    })[0];
    input.disabled = !editable || !!member && member.source === 'role';
  });
}

function renderAssigneeSelect(task, load) {
  var group = byId('assignment-group');
  var members = byId('assigned-members');
  if (!group && !members) return;
  ensureUsers().then(function (users) {
    // Component loads are asynchronous. Never let a users response for the
    // previous component repaint the shared assignee control after navigation.
    if (!componentLoadIsCurrent(load)) return;
    var assignees = (task.assignees || []).slice();
    if (!assignees.length && task.assigned_to) {
      assignees = [{ name: task.assigned_to, source: 'manual' }];
    }
    var editable = canManageAssignments() && (!task.permissions ||
      task.permissions.can_manage_assignments !== false) && isCurrentPipelineView();
    if (group) {
      group.innerHTML = '<div id="assigned-members" class="assigned-members">' +
        assignmentMembersHtml(assignees, {
        removeAttribute: 'data-assignee-name',
        editable: editable
      }) + '</div>' + assignmentChecklistHtml(users, assignees, {
        triggerId: 'assigned-to', menuId: 'assigned-to-menu', editable: editable,
        disabledReason: 'Only supervisors and staff can change assignees.'
      });
      all('.assignee-remove', group).forEach(function (button) {
        button.addEventListener('click', function () { removeManualAssignee(button.dataset.assigneeName); });
      });
      wireAssignmentChecklist(group, function (change) {
        return change.checked ? assignComponent(change.name) : removeManualAssignee(change.name);
      });
    }
    syncAssigneeGate();
  });
}

// ---------------------------------------------------------------------------
// The action row
// ---------------------------------------------------------------------------
// Three shared buttons (#return-component / #submit-component /
// #approve-component) plus the form's own Save. They are ONE set of nodes
// reused by every step, so anything a per-step override changes -- label,
// classes, disabled -- has to be restored before the next step renders:
// actionButtonDefaults captures the markup's own values once, and
// resetActionButtons puts them back on every render. That is what lets an
// override be a small declarative function instead of a growing if-soup.

var ACTION_BUTTON_IDS = ['return-component', 'submit-component', 'approve-component', 'reopen-component'];
var actionButtonDefaults = null;

function actionButtons() {
  if (!actionButtonDefaults) actionButtonDefaults = {};
  var buttons = {};
  ACTION_BUTTON_IDS.forEach(function (id) {
    var button = byId(id);
    buttons[id] = button;
    // Captured from the MARKUP, once, the first time the button is really
    // there -- never from a render that may already have overridden it.
    if (button && !actionButtonDefaults[id]) {
      actionButtonDefaults[id] = {
        text: button.textContent,
        title: button.title,
        className: button.className.replace(/\s*\bhidden\b/g, '')
      };
    }
  });
  return buttons;
}

// Back to the markup's own label/classes, hidden and enabled. Every render
// starts here, so a step with no override is never shown another step's
// relabelled button.
function resetActionButtons(buttons) {
  ACTION_BUTTON_IDS.forEach(function (id) {
    var button = buttons[id];
    var defaults = actionButtonDefaults[id];
    if (!button || !defaults) return;
    button.textContent = defaults.text;
    button.title = defaults.title;
    button.className = defaults.className + ' hidden';
    button.disabled = false;
  });
}

// Exported for the harness: the action row is a role/status decision, and the
// only honest way to test "an employee never sees Approve/Return" is to render
// it and look.
export function renderActionButtons(task) {
  var editable = isCurrentPipelineView();
  var buttons = actionButtons();
  resetActionButtons(buttons);
  // ITEM A: prospect step pages have no Save button -- persistence is the
  // auto-save controller's job (views/autosave.js). The BP well shell keeps
  // its explicit button. Gated on the VIEWED pipeline, so a reference view of
  // prospect steps reads the same as a live one: prospect pages simply carry
  // no Save button.
  var prospectView = Store.pipeline === 'prospect';
  var saveButton = byId('save-component');
  if (saveButton) saveButton.classList.toggle('hidden', prospectView);
  applyApprovalActions({
    return: buttons['return-component'],
    submit: buttons['submit-component'],
    approve: buttons['approve-component'],
    reopen: buttons['reopen-component']
  }, editable ? task.permissions : null);
}

export function setComponentReferenceMode(referenceOnly) {
  var form = byId('component-form');
  if (!form) return;
  var permissions = (Store.task && Store.task.permissions) || null;
  var contentReadOnly = referenceOnly || approvalContentLocked(permissions);
  // The sweep only touches the controls this mode OWNS. The assignee select is
  // gated by role (KI-002) and is re-applied explicitly below, so leaving
  // reference mode restores its role-based state instead of blanket-enabling
  // it. (It also sits OUTSIDE #component-form today -- the guard is here so a
  // future markup move cannot silently re-open the hole.)
  all('input, select, textarea', form).forEach(function (control) {
    if (control.id === 'assigned-to') return;
    control.disabled = contentReadOnly;
  });
  all('.add-repeatable-row, .remove-repeatable-row, .formation-remove, .pay-interval-add, .pay-interval-remove', form).forEach(function (button) {
    button.disabled = contentReadOnly;
  });
  var saveButton = byId('save-component');
  if (saveButton) saveButton.disabled = contentReadOnly;
  syncAssigneeGate(referenceOnly || !permissions || !permissions.can_manage_assignments);
  form.classList.toggle('reference-only', contentReadOnly);
}

// CONSOLIDATED PAGES (cards 2B and 4B). Some tracked items no longer have a
// form each: a group of them opens ONE workspace whose sections are those
// items, laid out the way the work is actually done. Each entry names the
// module that owns such a page.
//
// The gate is deliberately narrow and identical for both: a LEAD page, the
// record's CURRENT pipeline, and a step the page claims. A BP well, a reference
// view and every other step fall straight through to the generic form below.
//
// A workspace mounts into #dynamic-fields (the generic grid's own container),
// so the shell's comments box, folder slot and Save button sit exactly where
// they always did and need no markup of their own. `title` is what the editor
// head reads while it is mounted -- null means "hide the head entirely" (card
// 2B's page IS the whole stage, so naming one step would be a lie), a string
// means "keep the head, name the PAGE" (card 4B's two letters are one
// deliverable called Staking Letters, and a page with no title at all reads as
// a rendering bug).
var CONSOLIDATED_PAGES = [
  { claims: isLeadAssessmentStep, render: renderLeadAssessment, title: null,
    bodyClass: 'lead-assessment-body', keepsLifecycle: true },
  { claims: isStakingLetterStep, render: renderStakingLetters, title: 'Staking Letters',
    bodyClass: 'staking-letters-body' }
];

// The consolidated page that claims this task, or null for the generic form.
function consolidatedPageFor(task) {
  var shell = byId('detail-shell');
  if (!shell || !shell.classList.contains('detail-shell-lead') || !isCurrentPipelineView()) return null;
  return CONSOLIDATED_PAGES.find(function (page) { return page.claims(task.task_name); }) || null;
}

// Show/hide the per-STEP furniture a consolidated page replaces. Every class it
// touches is reset first, so switching from one workspace to the other -- or
// away to a generic form -- never leaves the previous page's chrome behind.
function setConsolidatedChrome(page) {
  var root = byId('dynamic-fields');
  if (root) {
    CONSOLIDATED_PAGES.forEach(function (entry) {
      root.classList.toggle(entry.bodyClass, !!page && page.bodyClass === entry.bodyClass);
    });
  }
  var number = byId('component-number');
  if (number) number.classList.toggle('hidden', !!page);
  var title = byId('component-title');
  // A page with its own title keeps the heading and renames it; a page without
  // one hides it. The generic form always restores the step's own name (
  // loadComponent writes it back before this runs).
  if (title) {
    title.classList.toggle('hidden', !!page && !page.title);
    if (page && page.title) title.textContent = page.title;
  }
  var save = byId('save-component');
  if (save) save.classList.toggle('save-primary', !!page);
}

// Tear down every consolidated page EXCEPT the one about to mount (or all of
// them, when a generic form is). Each teardown is a no-op on a page that was
// not mounted, so this stays a plain sweep rather than a bookkeeping exercise.
function teardownOtherConsolidatedPages(page) {
  if (!page || page.render !== renderLeadAssessment) teardownLeadAssessment();
  if (!page || page.render !== renderStakingLetters) teardownStakingLetters();
}

// One shared detail shell hosts every component. Navigation therefore begins
// by invalidating the previous load and removing ALL step-owned transient DOM,
// before deciding what the next component renders. This is the invariant that
// prevents a calculator/folder/workspace from leaking into the next step.
// The generation token also makes late fields/folder responses harmless.
var componentLoadGeneration = 0;

function teardownResourceCalculatorSection() {
  teardownResourceCalculator();
  var panel = byId('resource-calculator-panel');
  if (panel) panel.remove();
}

function beginComponentLoad(task) {
  componentLoadGeneration += 1;
  teardownResourceCalculatorSection();
  var folder = byId('component-folder-card');
  if (folder) folder.remove();
  var root = byId('dynamic-fields');
  if (root) root.innerHTML = '';
  return {
    generation: componentLoadGeneration,
    projectId: Store.projectId,
    taskId: task.task_id
  };
}

export function componentLoadIsCurrent(load) {
  return !!load && load.generation === componentLoadGeneration &&
    Store.projectId === load.projectId && !!Store.task && Store.task.task_id === load.taskId;
}

export function loadComponent(task) {
  if (!task) return;
  var load = beginComponentLoad(task);
  Store.task = task;
  // Item A: navigation to a DIFFERENT task resets the auto-save controller
  // (stale timers, queued trailing save, indicator); the post-save reload of
  // the same task keeps all three -- syncAutoSaveContext tells them apart.
  syncAutoSaveContext();
  setComponentReferenceMode(!isCurrentPipelineView());
  all('.component-item').forEach(function (button) { button.classList.toggle('active', Number(button.getAttribute('data-task-id')) === task.task_id); });
  revealTaskStage(task);
  byId('component-number').textContent = String(task.sequence_no || '');
  byId('component-title').textContent = task.task_name;
  renderStatusChip(task.status);
  renderAssigneeSelect(task, load);
  renderActionButtons(task);
  byId('comments').placeholder = commentPlaceholder(task.task_name);
  byId('comments').value = task.comments || '';
  var consolidated = consolidatedPageFor(task);
  setConsolidatedChrome(consolidated);
  teardownOtherConsolidatedPages(consolidated);
  if (consolidated) {
    // No per-step field fetch: the workspace reads every step's values out of
    // Store.allFields (already on the /detail payload) and resolves its own
    // folder row. Lead Assessment keeps the single merged row's normal
    // lifecycle controls; Staking Letters still hides them because its two
    // underlying task rows retain independent lifecycles.
    if (!consolidated.keepsLifecycle) resetActionButtons(actionButtons());
    consolidated.render(byId('dynamic-fields'), { onCopy: copyText });
    setComponentReferenceMode(!isCurrentPipelineView());
    renderRightPanel(tasksForPipeline(Store.pipeline));
    return Promise.resolve();
  }
  return Promise.all([API.fields(task.task_id), API.componentFolder(Store.projectId, task.task_id)]).then(function (results) {
    if (!componentLoadIsCurrent(load)) return;
    renderFields(task.task_name, results[0] || {});
    renderResourceCalculatorSection(task, results[0] || {});
    renderComponentFolder(results[1] || {});
    setComponentReferenceMode(!isCurrentPipelineView());
    renderRightPanel(tasksForPipeline(Store.pipeline));
  }).catch(function (error) {
    if (componentLoadIsCurrent(load)) msg(error.message, 'error');
  });
}

// Checklist changes post immediately (not deferred to Save). Adding retains
// the established scope choice: this step only, or this and later unassigned
// steps. The checkbox itself continues to describe membership on this step.
export function assignComponent(name) {
  if (!Store.task) return;
  if (!isCurrentPipelineView()) return msg('Switch back to the current pipeline to change assignments.', 'error');
  var assignee = typeof name === 'string' ? name : '';
  if (!assignee) return;
  return confirmDialog({
    title: 'Add assignee',
    message: 'Also preassign following steps to ' + assignee + '?',
    confirmLabel: 'Yes, preassign following steps',
    cancelLabel: 'Only this step'
  }).then(function (cascade) {
    return API.assign(Store.task.task_id, {
      assignee: assignee,
      cascade: !!cascade,
      revision: Store.task.revision,
      changed_by: currentUserName()
    });
  }).then(function () {
    return refreshAfterRecordChange(assignee + ' added to the assignment group.');
  }).catch(function (error) {
    msg(error.message, 'error');
    throw error;
  });
}

function removeManualAssignee(name) {
  if (!Store.task || !name || !isCurrentPipelineView()) return;
  return API.updateTaskAssignees(Store.task.task_id, {
    remove: [name],
    changed_by: currentUserName()
  }).then(function () {
    return refreshAfterRecordChange(name + ' removed from the assignment group.');
  }).catch(function (error) {
    msg(error.message, 'error');
    throw error;
  });
}

var TRANSITION_MESSAGES = {
  submit: 'Component submitted for approval.',
  approve: 'Component approved.',
  return: 'Component returned for update.',
  reopen: 'Component reopened for update.'
};

export function transitionComponent(action) {
  if (!Store.task) return;
  if (!isCurrentPipelineView()) return msg('Switch back to the current pipeline to change workflow status.', 'error');
  // Segment pages auto-save. A transition must observe those writes (and the
  // fresh revision returned by their detail refresh) before validation runs.
  return flushAutoSave().then(function (saved) {
    if (!saved) throw new Error('Save the latest changes successfully before submitting.');
    if (!Store.task || !Store.task.permissions || !Store.task.permissions['can_' + action]) {
      throw new Error('This approval action is no longer available. Refresh the step and try again.');
    }
    if (action === 'submit') {
      var blocked = submitBlockedMessage(Store.task.task_name,
                                         (Store.allFields || {})[Store.task.task_name]);
      if (blocked) throw new Error(blocked);
    }
    return API.transition(Store.task.task_id, {
      action: action,
      revision: Store.task.revision,
      changed_by: currentUserName()
    });
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

// --- Pay intervals ---------------------------------------------------------
// A formation keeps its envelope (top/base/thickness) above; the pay inside it
// is described by zero or more PAY INTERVALS -- a mini repeatable table under
// the panel, one row per interval, stored well-level in
// project_formation_pay_intervals and PUT inside the formation row's
// `pay_intervals` array (seq = row order). Only the two log-interpretation
// steps capture them, so the other two phases' panels render exactly as before.
var PAY_INTERVAL_PHASES = ['quicklook', 'final'];
var PAY_INTERVAL_COLUMNS = [
  { key: 'top_tvdss_ft', label: 'Top (ft)', type: 'number' },
  { key: 'base_tvdss_ft', label: 'Base (ft)', type: 'number' },
  { key: 'phit_pct', label: 'Phit (%)', type: 'number' },
  { key: 'swt_pct', label: 'Swt (%)', type: 'number' },
  { key: 'ngr_pct', label: 'NGR (%)', type: 'number' },
  { key: 'kint_md', label: 'Kint (mD)', type: 'number' },
  { key: 'fluid', label: 'Fluid', type: 'select', options: FLUID_TYPES }
];

function phaseHasPayIntervals(phase) { return PAY_INTERVAL_PHASES.indexOf(phase) >= 0; }

// One buffer interval from a (possibly missing) saved pay-interval record.
function makePayIntervalRow(saved) {
  var values = {};
  PAY_INTERVAL_COLUMNS.forEach(function (col) {
    var stored = saved ? saved[col.key] : null;
    values[col.key] = stored == null ? '' : String(stored);
  });
  return values;
}

// The intervals of one buffered formation row, ready to PUT: entirely blank
// rows drop (an untouched freshly-added row must not become a stored all-NULL
// interval), everything else keeps its buffer order -- the backend assigns seq
// from exactly that order.
function payIntervalsForSave(row) {
  return ((row && row.intervals) || []).filter(function (interval) {
    return PAY_INTERVAL_COLUMNS.some(function (col) { return isFilled(interval[col.key]); });
  }).map(function (interval) {
    var out = {};
    PAY_INTERVAL_COLUMNS.forEach(function (col) { out[col.key] = interval[col.key]; });
    return out;
  });
}

function rowHasPayIntervals(row) { return payIntervalsForSave(row).length > 0; }

// Custom formation names mirror the backend normalization (strip().upper(),
// <=40 chars) so what the editor shows is what gets stored.
function normalizeFormationName(name) {
  return String(name == null ? '' : name).trim().toUpperCase().slice(0, 40);
}

// One buffer row from a (possibly missing) saved formation record. `intervals`
// mirrors the saved row's pay_intervals (already ordered by seq server-side);
// it is buffered for every phase but only rendered/PUT for the phases that
// capture pay intervals.
function makeFormationRow(name, isCustom, saved) {
  var values = {};
  FORMATION_METRICS.forEach(function (metric) {
    var stored = saved ? saved[metric.key] : null;
    values[metric.key] = stored == null ? '' : String(stored);
  });
  var intervals = ((saved && saved.pay_intervals) || []).map(makePayIntervalRow);
  return { formation: name, isCustom: isCustom, values: values, intervals: intervals };
}

// Seed a phase: the canonical trio always renders (in order), each filled from
// its saved row when present, followed by any custom (non-canonical) formations
// already stored for the phase.
function seedFormationEdits(phase) {
  var saved = (Store.formations || []).filter(function (row) { return row.phase === phase; });
  var rows = [];
  var canonical = formationNames(Store.meta);
  canonical.forEach(function (name) {
    var match = saved.find(function (row) { return row.formation === name; });
    rows.push(makeFormationRow(name, false, match));
  });
  saved.forEach(function (row) {
    if (canonical.indexOf(row.formation) < 0) rows.push(makeFormationRow(row.formation, true, row));
  });
  formationEdits[phase] = rows;
  formationDirty[phase] = false;
}

// Every visible row of the phase, `{ formation, ...metrics }` (plus
// `pay_intervals` on the phases that capture them). A blank formation name
// always drops. A custom (isCustom) row is kept even when metric-less -- the
// user named a new formation and the backend stores all-NULL metrics fine. A
// canonical row with entirely blank metrics AND no pay intervals drops: that
// full-replacement gap is the designed way to delete a canonical formation's
// row (a row carrying only pay intervals therefore has to survive it).
// Deletions overall are handled by the backend's phase-scoped full replacement.
export function formationRowsForSave(phase) {
  var kept = [];
  var withIntervals = phaseHasPayIntervals(phase);
  (formationEdits[phase] || []).forEach(function (row) {
    var name = normalizeFormationName(row.formation);
    if (!isFilled(name)) return;
    var hasMetrics = FORMATION_METRICS.some(function (metric) { return isFilled(row.values[metric.key]); });
    var hasIntervals = withIntervals && rowHasPayIntervals(row);
    if (!row.isCustom && !hasMetrics && !hasIntervals) return;
    var out = { formation: name };
    FORMATION_METRICS.forEach(function (metric) { out[metric.key] = row.values[metric.key]; });
    // Only phases that edit intervals send the key at all: its ABSENCE tells
    // the backend to leave any stored intervals alone, so the post_drill /
    // resource_update panels can never clear what the log steps captured.
    if (withIntervals) out.pay_intervals = payIntervalsForSave(row);
    kept.push(out);
  });
  return kept;
}

// Pre-save guard for a phase's formation rows: returns an error string to block
// the save (surfaced via msg), or null when the rows are safe to PUT. Catches
// (a) a custom row carrying metrics but no name (would silently vanish) and
// (b) two rows normalizing to the same formation (the phase-scoped full
// replacement would collapse/delete one, losing data).
// The numeric rules over ONE formation cell. TVDSS depths are the only signed
// measure (above datum reads negative) and the only ones that outrun the
// generic 9999 cap, both of which the metric/column defs already declare --
// this is where those declarations finally get read (they were documentation
// only while this buffer had no numeric validation at all).
function formationCellError(def, raw) {
  // Card 3H: TVDSS is stored as a magnitude like every other measure here, so
  // nothing on this sheet is signed. It keeps `bigOk` though -- a depth runs
  // past four digits, which the generic 9999 sanity cap would otherwise refuse.
  var isDepth = /tvdss/.test(def.key);
  return numericFieldError(def.label, raw, !!def.bigOk || isDepth, /_pct$/.test(def.key), false);
}

export function validateFormationRows(phase) {
  var rows = formationEdits[phase] || [];
  for (var i = 0; i < rows.length; i += 1) {
    var row = rows[i];
    var hasMetrics = FORMATION_METRICS.some(function (metric) { return isFilled(row.values[metric.key]); }) ||
      (phaseHasPayIntervals(phase) && rowHasPayIntervals(row));
    if (row.isCustom && !isFilled(normalizeFormationName(row.formation)) && hasMetrics) {
      return 'Custom formation needs a name.';
    }
    // Same rules the plain step fields get: numeric, not negative, percentages
    // within 100. Named per formation, because a message that only says
    // "Porosity must not exceed 100%" is unhelpful in a four-formation sheet.
    var name = normalizeFormationName(row.formation) || 'Formation ' + (i + 1);
    for (var m = 0; m < FORMATION_METRICS.length; m += 1) {
      var metric = FORMATION_METRICS[m];
      if (metric.type !== 'number') continue;
      var metricError = formationCellError(metric, row.values[metric.key]);
      if (metricError) return name + ': ' + metricError;
    }
    var intervals = (phaseHasPayIntervals(phase) && row.intervals) || [];
    for (var v = 0; v < intervals.length; v += 1) {
      for (var c = 0; c < PAY_INTERVAL_COLUMNS.length; c += 1) {
        var col = PAY_INTERVAL_COLUMNS[c];
        if (col.type !== 'number') continue;
        var intervalError = formationCellError(col, intervals[v][col.key]);
        if (intervalError) return name + ' pay interval ' + (v + 1) + ': ' + intervalError;
      }
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
  // Card 3H: every measure here is a magnitude, TVDSS included -- above datum
  // still reads negative in the field, but ASAS stores the depth's magnitude.
  return '<label>' + esc(metric.label) + '<input type="number" step="any" min="0" ' +
    attr + ' value="' + esc(value) + '" aria-label="' + esc(metric.label) + '"></label>';
}
function formationPanelMarkup(row, index) {
  return FORMATION_GROUPS.map(function (group) {
    var colsClass = group.metrics.length > 1 ? ' cols-' + group.metrics.length : '';
    var inner = group.metrics.map(function (metric) { return formationMetricControl(metric, row, index); }).join('');
    return '<div class="field-section-label">' + esc(group.section) + '</div>' +
      '<div class="field-row' + colsClass + '">' + inner + '</div>';
  }).join('');
}

// One pay-interval row. Reuses the repeatable sheet's grid idiom (the row is
// display:contents and inherits the template declared once on .repeatable-rows)
// but carries its OWN data attributes and button classes: the generic
// repeatable machinery (getFields' [data-repeatable] harvest,
// bindRepeatableFields' .add-repeatable-row/.remove-repeatable-row handlers)
// must never see these -- intervals travel in the formations buffer, not in the
// step's dynamic fields. `formationIndex` is the selected formation's buffer
// position, `index` the interval's position within it.
function payIntervalRowMarkup(interval, index, formationIndex) {
  return '<div class="pay-interval-row">' + PAY_INTERVAL_COLUMNS.map(function (col) {
    var value = interval[col.key] == null ? '' : interval[col.key];
    var attr = 'data-pay-field="' + esc(col.key) + '" data-pay-row="' + index +
      '" data-formation-row="' + formationIndex + '" aria-label="' + esc(col.label) + '"';
    if (col.type === 'select') {
      var options = (col.options || []).map(function (option) {
        return '<option value="' + esc(option) + '" ' + (String(value) === String(option) ? 'selected' : '') +
          '>' + esc(option || 'Select') + '</option>';
      }).join('');
      return '<select ' + attr + '>' + options + '</select>';
    }
    return '<input type="number" step="any"' + (/tvdss/.test(col.key) ? '' : ' min="0"') +
      ' ' + attr + ' value="' + esc(value) + '">';
  }).join('') + '<button type="button" class="icon-btn pay-interval-remove" data-pay-row="' + index +
    '" title="Remove pay interval" aria-label="Remove pay interval">' + ICONS.x + '</button></div>';
}

// The selected formation's pay-interval sub-table (log-interpretation phases
// only; the other phases get ''). Rendered from the buffer, so add/remove is a
// buffer mutation + container re-render like everything else in this panel.
function payIntervalsMarkup(row, formationIndex, phase) {
  if (!phaseHasPayIntervals(phase)) return '';
  var intervals = (row && row.intervals) || [];
  var template = PAY_INTERVAL_COLUMNS.map(function () { return 'minmax(80px, 1fr)'; }).join(' ') + ' auto';
  var header = '<div class="repeatable-head">' + PAY_INTERVAL_COLUMNS.map(function (col) {
    return '<span class="repeatable-col-label">' + esc(col.label) + '</span>';
  }).join('') + '<span class="repeatable-col-label" aria-hidden="true"></span></div>';
  var body = intervals.length
    ? '<div class="repeatable-sheet"><div class="repeatable-rows" style="grid-template-columns:' + template + '">' +
      header + intervals.map(function (interval, index) {
        return payIntervalRowMarkup(interval, index, formationIndex);
      }).join('') + '</div></div>'
    : '<p class="pay-interval-empty">No pay intervals recorded for this formation yet.</p>';
  return '<div class="pay-intervals">' +
    '<div class="repeatable-heading"><b>Pay intervals</b>' +
    '<button type="button" class="icon-btn pay-interval-add" title="Add pay interval" aria-label="Add pay interval">' + ICONS.plus + '</button></div>' +
    body + '</div>';
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
    ? '<button type="button" class="icon-btn formation-remove" title="Remove formation" aria-label="Remove formation">' + ICONS.x + '</button>'
    : '';
  var pickerRow = '<div class="formation-picker-row">' +
    '<label class="formation-picker-label">Formation<select class="formation-picker" aria-label="Formation">' + pickerOptions + '</select></label>' +
    removeButton + '</div>';
  return '<div class="repeatable-heading"><b>' + esc(field.label) + '</b></div>' +
    pickerRow + '<div class="formation-panel">' + formationPanelMarkup(row, selected) + '</div>' +
    payIntervalsMarkup(row, selected, phase);
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
  // Pay-interval cells write through to the selected formation's own interval
  // buffer (data-formation-row is stamped at render time, so a stale selection
  // can never send an edit to the wrong formation).
  all('[data-pay-field]', container).forEach(function (element) {
    if (element.dataset.pBound) return;
    element.dataset.pBound = 'true';
    function sync() {
      var formationIndex = Number(element.getAttribute('data-formation-row'));
      var intervalIndex = Number(element.getAttribute('data-pay-row'));
      var row = formationEdits[phase][formationIndex];
      if (!row || !row.intervals[intervalIndex]) return;
      row.intervals[intervalIndex][element.getAttribute('data-pay-field')] = element.value;
      formationDirty[phase] = true;
    }
    element.addEventListener('input', sync);
    element.addEventListener('change', sync);
  });
  all('.pay-interval-add', container).forEach(function (button) {
    if (button.dataset.pBtnBound) return;
    button.dataset.pBtnBound = 'true';
    button.addEventListener('click', function () {
      var row = formationEdits[phase][selectedFormationIndex(phase)];
      if (!row) return;
      row.intervals.push(makePayIntervalRow(null));
      formationDirty[phase] = true;
      rerenderFormationContainer(container, phase);
    });
  });
  all('.pay-interval-remove', container).forEach(function (button) {
    if (button.dataset.pBtnBound) return;
    button.dataset.pBtnBound = 'true';
    button.addEventListener('click', function () {
      var row = formationEdits[phase][selectedFormationIndex(phase)];
      if (!row) return;
      row.intervals.splice(Number(button.getAttribute('data-pay-row')), 1);
      formationDirty[phase] = true;
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
  var names = formationNames(Store.meta);
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
  if (field.type === 'radio') {
    // Horizontal radio group: one input per option, all sharing a name unique
    // to the field key. No blank option -- unset/legacy '' values simply leave
    // every radio unchecked (see getFields' radio branch).
    var radioOptions = (field.options || []).map(function (option) {
      return '<label class="radio-option"><input type="radio" name="radio-' + esc(field.key) + '" data-field="' + esc(field.key) + '" value="' + esc(option) + '" ' + (String(value) === String(option) ? 'checked' : '') + '> ' + esc(option) + '</label>';
    }).join('');
    return '<div class="radio-group' + classes + '"' + showIfAttr + ' role="radiogroup" aria-labelledby="radio-label-' + esc(field.key) + '"><span class="radio-group-label" id="radio-label-' + esc(field.key) + '">' + esc(field.label) + '</span><div class="radio-options">' + radioOptions + '</div></div>';
  }
  if (field.type === 'link') {
    // Link cards never toggle; data-show-if is intentionally omitted.
    return '<div class="summary-box' + classes + '"><p><a href="' + esc(field.value || '#') + '" target="_blank" rel="noreferrer">' + esc(field.linkText || 'New Request') + '</a></p></div>';
  }
  // min="0" is the browser-level twin of numericFieldError's negative guard
  // (schema.js): SARH Prognosis TVDSS is a magnitude and keeps the generic
  // non-negative browser guard.
  return '<label class="' + classes + '"' + showIfAttr + '>' + esc(field.label) +
    '<input type="number" step="any"' + (field.allowNegative ? '' : ' min="0"') +
    ' data-field="' + esc(field.key) + '" value="' + esc(value) + '"></label>';
}
// A field standing on its own (no row grouping): visibility lives on the field.
function standaloneFieldMarkup(field, componentName, values) {
  var value = resolveFieldValue(field, values);
  if (field.type === 'formations') return renderFormationsField(field);
  if (field.type === 'repeatable') return renderRepeatableField(field, value);
  var hidden = field.showIf && !truthy(values[field.showIf]);
  // .radio-group already forces grid-column:1/-1 in CSS, so only 'text' needs
  // the wide-field class here.
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
// ---------------------------------------------------------------------------
// Live Trap / Seal CoS recompute (the "Trap and Seal CoS" step).
//
// The CoS percentages are plain editable inputs; on every change of a FORMULA
// INPUT the matching CoS field is recomputed client-side (cos-rules.js, the
// exact mirror of cos.py) and overwritten. Nothing listens on the CoS fields
// themselves, so a manually typed value persists until an input next changes
// -- the precedence rule, simple and predictable. The server hooks
// (workflow/lifecycle.py) skip their own recompute when the payload carries
// the pct explicitly, which a save from this form now always does (getFields
// harvests both), so what the user sees is exactly what is stored.
// ---------------------------------------------------------------------------

// The Seal formula's own inputs (cos.calculate_seal_cos reads exactly these;
// the pore-pressure gradient is a recorded rider, not a formula input, so a
// change to it never overwrites a manually entered Seal CoS).
var SEAL_COS_INPUT_KEYS = [
  'seal_recent_activity_age', 'seal_dip', 'seal_azimuth_vs_shmax',
  'seal_fault_level_confidence', 'seal_fracture_permeability'
];

function bindLiveCosCalculation(componentName, root) {
  if (componentName !== 'Trap and Seal CoS') return;
  function input(key) { return root.querySelector('[data-field="' + esc(key) + '"]'); }
  // null means "not computable" (an input is missing/non-numeric): leave the
  // field -- same contract as the server's None. '' (blank Seal form) and a
  // computed percent both overwrite.
  function writeCos(key, computed) {
    var element = input(key);
    if (element && computed !== null) element.value = computed;
  }
  function listen(key, recompute) {
    var element = input(key);
    if (!element) return;
    element.addEventListener('input', recompute);
    element.addEventListener('change', recompute);
  }
  listen('sarah_quwarah_thickness_ft', function () {
    // The cross-task Sarah prognosis thickness (Thickness Estimation) comes
    // from the saved field map on the /detail payload -- the same read the
    // server's hook performs against the database.
    writeCos('trap_cos_pct', calculateTrapCos(
      val('Lead Assessment', 'formation_thickness_ft') || val('Thickness Estimation', 'formation_thickness_ft'),
      (input('sarah_quwarah_thickness_ft') || {}).value
    ));
  });
  SEAL_COS_INPUT_KEYS.forEach(function (key) {
    listen(key, function () {
      writeCos('seal_cos_pct', calculateSealCos(getFields(root)));
    });
  });
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
  // Bound BEFORE the generic per-field handler so a live CoS recompute has
  // already written its value when previewSummaryInputs harvests the form.
  bindLiveCosCalculation(componentName, root);
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
// Latest mean-PIIP precedence (newest assessment first), split into its two
// halves so callers name what they want instead of slicing by index.
//
// The v4 step merges moved the two post-drill assessments onto SAD Update
// (resource_update_*) and SAD Model (post_drill_piip_*); each retired step is
// listed straight after the step that absorbed it, holding the SAME EAV key,
// so a well whose numbers were entered before the merge still resolves.
// Store.allFields carries those legacy buckets because the backend field map
// (workflow.summary.get_project_dynamic_field_map) is retired-inclusive.
export var POST_DRILL_PIIP_SOURCES = [
  ['SAD Update', 'resource_update_gas_mean'],
  ['Resource Assessment Update', 'resource_update_gas_mean'],   // pre-v4 wells
  ['SAD Model', 'post_drill_piip_gas_mean'],
  ['Post-Drilling Resource Assessment', 'post_drill_piip_gas_mean']  // pre-v4
];
// The lead phase's own mean sources -- what a LEAD value must be read from,
// never the post-drill numbers above.
//
// The v5 renames ('Pre-Drilling Resource Assessment' -> 'Pre-Drilling GeoX
// Assessment', 'Lead Resource Assessment' -> 'Resource Assessment') rewrote the
// task rows in place, so LIVE data answers to the new names only. The old
// spellings ride along right behind for the one map that still carries them:
// lead_summary_snapshots froze its buckets at promotion time and is never
// rewritten, and leadFieldSource() merges that frozen map with the live one.
export var LEAD_PIIP_SOURCES = [
  ['Pre-Drilling GeoX Assessment', 'pre_drill_piip_gas_mean'],
  ['Pre-Drilling Resource Assessment', 'pre_drill_piip_gas_mean'],   // pre-v5 snapshots
  ['Lead Assessment', 'lead_piip_gas_mean'],
  ['Resource Assessment', 'lead_piip_gas_mean'],
  ['Lead Resource Assessment', 'lead_piip_gas_mean']                 // pre-v5 snapshots
];
export var LATEST_PIIP_SOURCES = POST_DRILL_PIIP_SOURCES.concat(LEAD_PIIP_SOURCES);

// The legacy Resource Assessment calculator host. Current v7 Lead Assessment
// renders its calculator inside its dedicated workspace; GeoX is deliberately
// excluded because it only records external-software results. The centralized
// beginComponentLoad teardown above removes both this DOM and its async state
// on EVERY navigation before the next view can mount.
// Reference-mode disabling is handled by renderResourceCalculator itself
// (it disables Calculate directly; every plain input is caught by the
// generic setComponentReferenceMode sweep detail-form.js runs again right
// after loadComponent's fields fetch resolves) -- see that function's own
// comment for why Apply/View-plots need no special-casing there.
var RESOURCE_CALCULATOR_STEPS = ['Resource Assessment'];
export function stepHostsResourceCalculator(taskName) {
  return RESOURCE_CALCULATOR_STEPS.indexOf(taskName) >= 0;
}
function renderResourceCalculatorSection(task, fields) {
  teardownResourceCalculatorSection();
  if (!stepHostsResourceCalculator(task.task_name)) return;
  var panel = document.createElement('div');
  panel.id = 'resource-calculator-panel';
  panel.className = 'resource-calculator-panel';
  var anchor = byId('dynamic-fields');
  anchor.parentNode.insertBefore(panel, anchor);
  renderResourceCalculator(panel, Store.projectId, task, fields);
}
export function renderComponentFolder(info) {
  var previous = byId('component-folder-card');
  if (previous) previous.remove();
  // Card 3AB: requires_folder is 0 for a step the approved mapping does not
  // list, and that means NO component -- not a blank card, not a disabled one.
  if (!info || !Number(info.requires_folder)) return;
  var card = document.createElement('div');
  card.id = 'component-folder-card';
  // A mapped step whose record is missing a name the destination needs says
  // which name, rather than offering a link to a half-resolved location.
  if (info.blocked) {
    card.className = 'folder-card folder-card-blocked';
    card.setAttribute('role', 'status');
    card.innerHTML = '<span class="folder-glyph" aria-hidden="true">' + ICONS['folder'] + '</span>' +
      '<span class="folder-path">' + esc(info.blocked) + '</span>';
    var blockedAnchor = byId('comments-field');
    blockedAnchor.parentNode.insertBefore(card, blockedAnchor.nextSibling);
    return;
  }
  var path = info.unc_path;
  card.className = 'folder-card';
  card.innerHTML = '<span class="folder-glyph" aria-hidden="true">' + ICONS['folder'] + '</span>' +
    '<span class="folder-path" title="' + esc(path) + '">' + esc(path) + '</span>' +
    '<button type="button" class="icon-btn" id="copy-component-folder" title="Copy folder link" aria-label="Copy folder link">' + ICONS['copy'] + '</button>';
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
    var key = element.getAttribute('data-field');
    // Radio: several inputs share one data-field key (one per option), so a
    // plain last-write-wins forEach would take whichever option is LAST in the
    // DOM regardless of which is checked. Seed '' once per key (unset/legacy
    // reads as no radio checked) and only the checked input overwrites it.
    if (element.type === 'radio') {
      if (!(key in fields)) fields[key] = '';
      if (element.checked) fields[key] = element.value;
      return;
    }
    fields[key] = element.type === 'checkbox' ? (element.checked ? '1' : '') : element.value;
  });
  all('[data-repeatable]', root).forEach(function (container) {
    var key = container.getAttribute('data-repeatable');
    var rows = [];
    all('.repeatable-row', container).forEach(function (row) {
      var data = {};
      var preserved = row.getAttribute('data-repeatable-preserved');
      if (preserved) {
        try { data = JSON.parse(preserved); } catch (error) { data = {}; }
      }
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
// Exported for the harness. Saves are drafts/content writes only; lifecycle
// messages come exclusively from the transition endpoint.
export function savedMessage() {
  return 'Component saved.';
}

// Every save path resolves the SAME outcome shape so the auto-save controller
// (views/autosave.js) can drive its indicator without inspecting toasts:
//   { ok: true,  state: 'saved' | 'nochange' }
//   { ok: false, state: 'invalid' | 'error', message }
// `options.auto` marks an auto-save: success / no-change / error toasts are
// suppressed (the indicator speaks instead; inline errors keep rendering).
export function saveComponent(event, options) {
  if (event && event.preventDefault) event.preventDefault();
  options = options || {};
  var auto = !!options.auto;
  if (!Store.task || !Store.task.permissions || !Store.task.permissions.can_edit) {
    var permissionMessage = 'You do not have permission to edit this step.';
    if (!auto) msg(permissionMessage, 'error');
    return Promise.resolve({ ok: false, state: 'error', message: permissionMessage });
  }
  if (!Store.task) return Promise.resolve({ ok: false, state: 'error', message: 'No component selected.' });
  // Cards 2B / 4B: a consolidated workspace owns its own batched save (one
  // page, several owning tasks, one PATCH each). Checked FIRST because
  // everything below -- getFields, validateStepFields, the single-task PATCH --
  // is written for a one-step form and would harvest nothing from those pages'
  // markup. Only one can ever be mounted (loadComponent tears the other down).
  if (leadAssessmentActive()) return saveLeadAssessment(options);
  if (stakingLettersActive()) return saveStakingLetters(options);
  if (!isCurrentPipelineView()) {
    var pipelineMessage = 'Switch back to the current pipeline to save changes.';
    if (!auto) msg(pipelineMessage, 'error');
    return Promise.resolve({ ok: false, state: 'error', message: pipelineMessage });
  }
  var fields = getFields();
  // Generic input sanity checks (numeric/negative/max/percent, area & thickness
  // ordering, piip trio ordering -- see schema.js) run before anything hits the
  // network; same "surface the message, abort the save" shape as the formations
  // guard right below. An AUTO save shows the message in the save-state
  // indicator instead of a toast -- typing through a temporarily-invalid value
  // must not rain toasts.
  var fieldsError = validateStepFields(Store.task.task_name, fields);
  if (fieldsError) {
    if (!auto) msg(fieldsError, 'error');
    return Promise.resolve({ ok: false, state: 'invalid', message: fieldsError });
  }
  // A component with a formations mini-sheet also PUTs the touched phase's
  // well-level rows alongside the dynamic-field save.
  var formationsField = (SCHEMA[Store.task.task_name] || []).find(function (item) { return item.type === 'formations'; });
  // Guard the touched formations mini-sheet before any write: a name-less custom
  // row with metrics or two rows normalizing to the same formation would lose
  // data in the phase-scoped full replacement.
  if (formationsField && formationDirty[formationsField.phase]) {
    var formationError = validateFormationRows(formationsField.phase);
    if (formationError) {
      if (!auto) msg(formationError, 'error');
      return Promise.resolve({ ok: false, state: 'invalid', message: formationError });
    }
  }
  var submitButton = byId('save-component');
  if (submitButton) submitButton.disabled = true;
  var savedTask = null;
  // No status / assigned_to keys: Save only persists inputs. Status moves via
  // /transition and assignment via /assign; the backend preserves both when
  // the keys are absent. Priority now has its own chip/endpoint, but save_task
  // defaults an absent priority to Medium (it does not preserve it), so we echo
  // the current value to avoid clobbering it on save.
  return API.updateTask(Store.task.task_id, {
    comments: byId('comments').value,
    priority: Store.task.priority || 'Medium',
    fields: fields,
    revision: Store.task.revision,
    changed_by: currentUserName(),
    business_plan_enabled: Number(Store.project.business_plan_enabled || 0) === 1,
    business_plan_year: Store.project.business_plan_year
  }).then(function (response) {
    savedTask = (response && response.task) || null;
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
    // The re-render below replaces the form's DOM, which would steal focus and
    // discard anything typed while the PATCH was in flight. Snapshot the
    // focused control NOW (its value is the newest typing) and put it back
    // once the fresh markup is in place -- see autosave.js.
    var focusSnapshot = captureEditorFocus();
    var selectedTaskId = Store.task.task_id;
    Store.project = detail.project || {};
    Store.tasks = detail.tasks || [];
    Store.allFields = detail.fields || {};
    Store.leadSummary = detail.lead_summary || null;
    Store.overview = detail.overview || null;
    Store.formations = detail.formations || [];
    renderDetail();
    var nextTask = Store.tasks.find(function (task) { return task.task_id === selectedTaskId; }) ||
      chooseInitialTask(tasksForPipeline(Store.pipeline));
    return Promise.resolve(loadComponent(nextTask)).then(function () {
      restoreEditorFocus(focusSnapshot);
      refreshAllBoards();
      var savedNote = savedMessage(savedTask);
      if (!auto) msg(savedNote, 'success');
      return { ok: true, state: 'saved' };
    });
  }).catch(function (error) {
    if (!auto) msg(error.message, 'error');
    return { ok: false, state: 'error', message: error.message };
  }).finally(function () {
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
  // A formation select column (per-stage Flowback formation) reuses the same
  // option set as the standalone Flowback dropdown: canonical trio + this well's
  // custom formations, with the row's current value appended by
  // formationNameOptions when it is legacy/custom and otherwise absent.
  if (col.optionsFrom === 'formations') return formationNameOptions(row[col.key]);
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

function repeatablePreservedData(field, row) {
  if (field.key !== 'flowback_stages_rows') return '';
  var visible = {};
  (field.columns || []).forEach(function (col) { visible[col.key] = true; });
  var preserved = {};
  Object.keys(row || {}).forEach(function (key) {
    if (!visible[key]) preserved[key] = row[key];
  });
  return Object.keys(preserved).length ? JSON.stringify(preserved) : '';
}

export function repeatableInputMarkup(field, row, rowIndex) {
  var cols = field.columns || [];
  // No per-row column template: the row is display:contents and inherits the
  // single grid declared once on .repeatable-rows, so header labels, inputs and
  // calc chips all share one track set and stay column-aligned.
  var preserved = repeatablePreservedData(field, row);
  var preservedAttr = preserved ? ' data-repeatable-preserved="' + esc(preserved) + '"' : '';
  return '<div class="repeatable-row" data-repeatable-row="' + rowIndex + '"' + preservedAttr + '>' + cols.map(function (col) {
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
      // Honor a column-level default (col.value, e.g. Flowback formation's
      // 'SARH'): a row with no saved value renders that default selected so a
      // freshly added row shows it and a save harvests it. Only when col.value
      // is declared -- select columns without a default (seismic blocks) keep
      // their genuinely-blank rows blank.
      var selectValue = (col.value != null && !isFilled(value)) ? col.value : value;
      return '<select ' + attr + aria + '>' + repeatableSelectOptions(col, row, selectValue) + '</select>';
    }
    var ghost = col.placeholder ? ' placeholder="' + esc(col.placeholder) + '"' : '';
    // Numeric columns get the same min="0" the standalone fields do; a column
    // that is legitimately signed declares allowNegative, exactly as a field
    // does (schema.js numericFieldError reads the same flag).
    var floor = (col.type === 'number' && !col.allowNegative) ? ' min="0"' : '';
    return '<input type="' + (col.type === 'number' ? 'number' : 'text') + '" step="any"' + floor + ' ' + attr + aria + ghost + ' value="' + esc(value) + '">';
  }).join('') + '<button type="button" class="icon-btn remove-repeatable-row" data-repeatable-key="' + esc(field.key) + '" data-repeatable-row="' + rowIndex + '" title="Remove row" aria-label="Remove row">' + ICONS['x'] + '</button></div>';
}
export function renderRepeatableField(field, value) {
  var rows = parseRepeatableRows(value);
  if (field.key === 'flowback_stages_rows') rows = normalizeFlowbackStages(rows);
  if (!rows.length) rows = [{}];
  var cols = field.columns || [];
  // One muted header row of column labels (kept out of the .repeatable-row set
  // so it is not counted by getFields/bindRepeatableFields) plus a trailing
  // spacer aligned with the row-action button. The column template lives once on
  // the .repeatable-rows grid below; the header is display:contents like a row.
  var header = '<div class="repeatable-head">' + cols.map(function (col) {
    return '<span class="repeatable-col-label">' + esc(col.label) + '</span>';
  }).join('') + '<span class="repeatable-col-label" aria-hidden="true"></span></div>';
  return '<div class="repeatable-field wide-field" data-repeatable="' + esc(field.key) + '"><div class="repeatable-heading"><b>' + esc(field.label) + '</b><button type="button" class="icon-btn add-repeatable-row" data-repeatable-key="' + esc(field.key) + '" title="Add row" aria-label="Add row">' + ICONS.plus + '</button></div><div class="repeatable-sheet"><div class="repeatable-rows" style="grid-template-columns:' + repeatableTemplate(field) + '">' + header + rows.map(function (row, index) { return repeatableInputMarkup(field, row || {}, index); }).join('') + '</div></div></div>';
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
