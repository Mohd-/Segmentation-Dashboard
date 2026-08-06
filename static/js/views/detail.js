import { byId, all, esc, isFilled, truthy, msg, fmtNum } from '../dom.js';
import { ICONS } from '../icons.js';
import { API } from '../api.js';
import { currentUserName, currentRole, currentProjectPipeline, isCurrentPipelineView, Store, resetSelection } from '../state.js';
import { activateTab } from '../navigation.js';
import { BP_STAGES, PROSPECT_STAGES, STATUSES, DONE, SEISMIC_BLOCKS, FLOWBACK_RATE_FIELDS } from '../schema.js';
import { confirmDialog, promptDialog } from '../dialog.js';
import { canTransitionPhase, promoteProject, recallProject } from './transitions.js';
import { loadComponent, LATEST_PIIP_SOURCES, POST_DRILL_PIIP_SOURCES, LEAD_PIIP_SOURCES, copyText } from './detail-form.js';
// Item A: keep the focused control (and its as-typed value/caret) alive across
// the post-save re-render this module's refresh performs. Runtime-only cycle
// (autosave.js -> detail-form.js -> here), same guarantee as the others above.
import { captureEditorFocus, restoreEditorFocus } from './autosave.js';
import { refreshAllBoards } from './pipeline.js';
import { refreshAudit } from './audit.js';
// Card 4B: the two task rows the consolidated Staking Letters page claims.
// Imported (not restated) so the sidebar's merged entry and the page that
// opens for it can never disagree about which steps are the package. The
// import is circular with staking-letters.js (it imports
// refreshAfterRecordChange from here), which is safe: both sides touch the
// other's bindings only inside functions called long after module evaluation.
import { STAKING_LETTER_STEPS } from './staking-letters.js';
// Card 2A: the shared Lead Summary block. It is PURE -- this module resolves
// every value out of Store and hands it one plain object (see leadSummaryData).
import { leadSummaryHtml, wireLeadSummary, closeLeadSummaryMenu, EM_DASH } from './lead-summary.js';
// The board's own completion arithmetic, imported rather than re-derived: the
// Lead Summary progress bar and the board KPI donut must read one formula over
// one dataset (the lead's 12 tracked items).
import { completedItemCount, TRACKED_ITEM_COUNT } from './lead-kpis.js';
import { applyPriorityChip, nextLeadPriority } from './board-widgets.js';

// A LEAD detail page is the redesigned Card 2A shell (single back control, big
// name, three-stage sidebar, wide Lead Summary). EVERY branch below guards on
// this, because two shells are deliberately out of scope for it:
//   - a BP WELL's detail page keeps its original shell, untouched;
//   - a REFERENCE view (either record seen through the opposite pipeline) keeps
//     the original shell too -- its "← Back to <current pipeline>" control is
//     the only way out of a reference view, and the single Card 2A back control
//     does not replace it.
function isLeadView() {
  return currentProjectPipeline() === 'prospect' && isCurrentPipelineView();
}

// Same glyphs the board columns use, so a stage reads identically on both
// surfaces. Keyed by the STORED stage group, which since v5 is the board
// column itself -- there is no display mapping left to mirror.
var LEAD_STAGE_ICONS = {
  'Lead Assessment': 'clipboard-check',
  'Risk Analysis': 'gauge',
  'Pre-Well Delivery': 'rig'
};

/* -------------------------------------------------------------------------
   Lead-level priority — ONE chip for the whole record

   Priority is a stored lead/well attribute (projects.priority), not a per-step
   value: the chip sits next to the record name in the shell header for BOTH
   shells (lead and BP well), renders from Store.project.priority (Low when
   unset — the creation default), and only a supervisor can cycle it
   Low → Medium → High → Low (anonymous dev mode acts as supervisor, matching
   the backend's current_role()). Everyone else sees a static, disabled chip.
   ------------------------------------------------------------------------- */

// The vocabulary, the cycle order and the chip markup are shared with the
// Business Plan Execution shell (views/board-widgets.js) so both sides of the
// app present the same record-level attribute identically. Re-exported here
// because this module has always been the chip's public face.
export { nextLeadPriority };

function canSetLeadPriority() {
  return currentRole() === 'supervisor';
}

export function renderLeadPriorityChip() {
  applyPriorityChip(byId('lead-priority-chip'),
    (Store.project && Store.project.priority) || 'Low', canSetLeadPriority());
}

// PATCH the record's stored priority, then run the standard record refresh so
// the chip, sidebar, summary and every board re-render from the new payload.
// Wired once in main.js (the chip is static markup in index.html).
export function cycleLeadPriorityChip() {
  if (!Store.projectId || !Store.project || !canSetLeadPriority()) return Promise.resolve();
  var next = nextLeadPriority(Store.project.priority);
  return API.projectPriority(Store.projectId, { priority: next, changed_by: currentUserName() })
    .then(function () { return refreshAfterRecordChange('Priority set to ' + next + '.'); })
    .catch(function (error) { msg(error.message, 'error'); });
}

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
  Store.pipeline = pipeline === 'bp' ? 'bp' : 'prospect';
  // Portfolio opens a record from a different top-level tab. Activate the
  // operating pipeline immediately so the tab highlight, board context and
  // detail panel all agree; card clicks are harmlessly idempotent here.
  activateTab(Store.pipeline);
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
    // Reconcile against the fresh server-side project. The Portfolio payload
    // is only a navigation hint and may have been rendered before a supervisor
    // promoted/recalled the record in another session.
    Store.pipeline = currentProjectPipeline();
    activateTab(Store.pipeline);
    renderDetail();
    // Land the detail shell's TOP just under the sticky chrome: the shell
    // scrolls into view block-start, and .detail-shell's scroll-margin-top
    // (components.css) is sized to clear the sticky header + tabs, so the
    // page opens reading the shell from its head. loadComponent fills the
    // form fields ASYNCHRONOUSLY (fetches fields + folder info), which grows
    // the document after the scroll target would otherwise be computed -- so
    // wait for that render to settle (Promise.resolve tolerates loadComponent
    // returning undefined when there's no task) and scroll on the next frame,
    // once the grown document's height is final. Same variant
    // switchPipelineView uses, so opening and switching land identically.
    Promise.resolve(loadComponent(chooseInitialTask(tasksForPipeline(Store.pipeline)))).then(function () {
      requestAnimationFrame(function () {
        byId('detail-shell').scrollIntoView({ behavior: 'smooth', block: 'start' });
      });
    });
  }).catch(function (error) { msg(error.message, 'error'); });
}
// Card 3Y: these were Unicode characters with a variation selector forcing
// text presentation -- a workaround for the fact that they were emoji at all.
// They are approved SVGs now, the SAME ones both boards already use for the
// same stages (pipeline.js STAGE_HEADER_ICONS, business-plan.js STAGE_META),
// so a stage reads identically wherever it appears.
var STAGE_ICONS = {
  'Lead Assessment': 'clipboard-check',
  'Risk Analysis': 'gauge',
  'Pre-Well Delivery': 'rig',
  'Well Delivery': 'clipboard-steps',
  'Post-Drilling': 'rig',
  'Post-Testing': 'gauge'
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
    if (head.hasAttribute('data-task-id')) head.setAttribute('aria-current', isOpen ? 'step' : 'false');
    else head.setAttribute('aria-expanded', String(isOpen));
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
  // Both rails are keyed by the stored stage group (v5 made the prospect
  // groups the three sidebar headings).
  openStage = task.stage_group;
  openStageProjectId = Store.projectId;
  syncStageOpenState();
  syncActiveStage();
  syncMergedRowActive(task);
}

// A merged rail row (Card 4B's "Staking Letters") stands for several task
// rows and lists their ids in data-task-ids. detail-form's own active toggle
// matches only the button's primary data-task-id, so when the loaded task is
// one of the OTHER rows the entry answers for (e.g. Well Site Location is the
// project's current task), re-assert the highlight here -- this runs right
// after that toggle inside loadComponent.
function syncMergedRowActive(task) {
  all('.component-item[data-task-ids]').forEach(function (button) {
    var ids = (button.getAttribute('data-task-ids') || '').split(',').map(Number);
    if (ids.indexOf(task.task_id) >= 0) button.classList.add('active');
  });
}

// The lead sidebar marks its OPEN stage as the active one (navy accent +
// underline). Applied in place, alongside syncStageOpenState, so toggling a
// stage never re-renders the list.
function syncActiveStage() {
  all('.rail-stage-lead').forEach(function (stage) {
    stage.classList.toggle('is-active', stage.getAttribute('data-stage') === openStage);
  });
}

/* -------------------------------------------------------------------------
   Card 2A — the three-stage LEAD sidebar

   Exactly three rows: LEAD ASSESSMENT / RISK ANALYSIS / PRE-WELL DELIVERY,
   each with the x/4 counter of the lead's TRACKED ITEMS (derived server-side,
   the same twelve the board cards and the KPI donut read) and a chevron
   (down = expanded, right = collapsed).

   Under an expanded stage sit that stage's REAL steps. Since v5 that is a
   straight 1:1 listing: each tracked item names exactly ONE stored step in its
   `steps` list, and the item's own stage IS that step's stored stage group, so
   nothing is regrouped, faked or dimmed here any more. Two details survive:
     * the step name still comes off the item (`steps[0]`) rather than being
       re-derived, so the server stays the single source of the mapping;
     * a step a record does not actually carry -- a legacy row a migration
       could not reach -- still shows its name, dimmed and unclickable, instead
       of vanishing from the workflow.
   Any prospect task row no tracked item names (there is none today) is
   appended to its own stage, so the sidebar can never hide a real step.

   Card 4B EXCEPTION: "Approval to Stake" and "Well Site Location" are two
   letters in ONE staking package, and both already open the same consolidated
   Staking Letters page. So the sidebar shows them as ONE entry -- "Staking
   Letters", in Approval to Stake's slot, clicking through to exactly what
   clicking Approval to Stake opened -- while the stage's x/4 counter (and the
   board card) keep counting the two tracked items separately. The merged
   entry wears the LEAST-advanced of the two statuses, so it reads completed
   only when BOTH letters are, consistent with the counter. See
   mergeStakingRows below.
   ------------------------------------------------------------------------- */

// [{ stage, done, total, rows: [{ task | label }] }] in board order, built from
// the server's tracked_items plus the project's own task rows.
export function leadStageGroups(trackedItems, tasks) {
  var items = trackedItems || [];
  var taskByName = {};
  (tasks || []).forEach(function (task) { taskByName[task.task_name] = task; });
  var order = [];
  var byStage = {};
  items.forEach(function (item) {
    var stage = item.stage;
    if (!byStage[stage]) { byStage[stage] = { stage: stage, done: 0, total: 0, rows: [] }; order.push(stage); }
    var group = byStage[stage];
    group.total += 1;
    if (item.status === 'Completed') group.done += 1;
    (item.steps || []).forEach(function (name) {
      var task = taskByName[name];
      group.rows.push({ label: name, task: task || null });
      if (task) task._leadRailPlaced = true;
    });
  });
  // v7 turns Lead Assessment into one real workflow row while retaining four
  // board checkpoints. Those checkpoint entries only drive done/total here;
  // the stage heading itself is the one navigation target, with no sub-rows.
  var assessmentGroup = byStage['Lead Assessment'];
  if (assessmentGroup) {
    var preferred = ['Lead Assessment', 'Resource Assessment', 'Area Definition',
                     'Thickness Estimation', 'GRV Inputs'];
    assessmentGroup.task = null;
    for (var p = 0; p < preferred.length && !assessmentGroup.task; p += 1) {
      assessmentGroup.task = taskByName[preferred[p]] || null;
    }
    assessmentGroup.rows = [];
    assessmentGroup.single = true;
    (tasks || []).forEach(function (task) {
      if (task.stage_group === 'Lead Assessment') task._leadRailPlaced = true;
    });
  }
  // Task rows no tracked item names keep their place in the workflow: appended
  // to their own stage, so the sidebar never loses a real step.
  (tasks || []).forEach(function (task) {
    if (task._leadRailPlaced) { delete task._leadRailPlaced; return; }
    var stage = task.stage_group;
    if (!byStage[stage]) { byStage[stage] = { stage: stage, done: 0, total: 0, rows: [] }; order.push(stage); }
    byStage[stage].rows.push({ label: task.task_name, task: task });
  });
  if (byStage['Lead Assessment'] && !byStage['Lead Assessment'].single) {
    assessmentGroup = byStage['Lead Assessment'];
    assessmentGroup.task = taskByName['Lead Assessment'] || taskByName['Resource Assessment'] ||
      taskByName['Area Definition'] || taskByName['Thickness Estimation'] || taskByName['GRV Inputs'] || null;
    assessmentGroup.rows = [];
    assessmentGroup.single = true;
  }
  return order.map(function (stage) { return mergeStakingRows(byStage[stage]); });
}

// A task status's position on the 4-state ladder (Not Assigned -> In Progress
// -> Ready -> Approved). Unknown statuses rank least-advanced, so a merged
// entry can never read further along than its data supports.
function statusRank(status) {
  var order = (Store.meta && Store.meta.statuses) || STATUSES;
  var rank = order.indexOf(String(status || 'Not Assigned'));
  return rank < 0 ? 0 : rank;
}

// Card 4B: collapse the two staking task rows into ONE "Staking Letters"
// entry, in place, in Approval to Stake's slot. The merged row's `task` is
// the Approval to Stake task -- clicking it does exactly what clicking that
// row did (loadComponent -> the consolidated page) -- and `tasks` carries
// both underlying rows so the active highlight can answer for either (see
// syncMergedRowActive). Its status is the LEAST-advanced of the two, so the
// glyph reads completed only when BOTH letters are, the same way the stage
// counter treats them as two items. Rows numbered after the absorbed slot in
// THIS stage slide down by one so the stage reads consecutively; no other
// stage is renumbered. Defensive: if either row is missing or dimmed (a
// legacy record a migration could not reach), nothing merges and both rows
// render as they are.
function mergeStakingRows(group) {
  var first = -1;
  var second = -1;
  group.rows.forEach(function (row, index) {
    if (!row.task) return;
    if (row.task.task_name === STAKING_LETTER_STEPS[0]) first = index;
    else if (row.task.task_name === STAKING_LETTER_STEPS[1]) second = index;
  });
  if (first < 0 || second < 0) return group;
  var primary = group.rows[first].task;
  var partner = group.rows[second].task;
  var laggard = statusRank(partner.status) < statusRank(primary.status) ? partner : primary;
  group.rows[first] = {
    label: 'Staking Letters',
    task: primary,
    tasks: [primary, partner],
    status: laggard.status,
    num: Math.min(Number(primary.sequence_no), Number(partner.sequence_no))
  };
  group.rows.splice(second, 1);
  var absorbed = Math.max(Number(primary.sequence_no), Number(partner.sequence_no));
  group.rows.forEach(function (row) {
    if (row.task && Number(row.task.sequence_no) > absorbed) row.num = Number(row.task.sequence_no) - 1;
  });
  return group;
}

function leadRailRowHtml(row) {
  if (!row.task) {
    // Defensive only: every tracked item has a real step since v5, so this
    // renders solely for a record missing a row the workflow says it should
    // have (a legacy row a migration could not reach).
    return '<div class="component-item component-item-future"' +
      ' title="This step is not on this record." aria-disabled="true">' +
      '<span class="component-num" aria-hidden="true">·</span><b>' + esc(row.label) + '</b></div>';
  }
  // A merged row (Card 4B) overrides the status and slot number it renders
  // under; a plain row reads both straight off its task. `data-task-ids`
  // lists every task a merged row answers for, so the active highlight can
  // match whichever of them is actually loaded (syncMergedRowActive).
  var slug = String((row.status || row.task.status) || 'Not Assigned').toLowerCase().replace(/\s+/g, '-');
  var num = row.num != null ? row.num : row.task.sequence_no;
  var idsAttr = row.tasks
    ? ' data-task-ids="' + esc(row.tasks.map(function (task) { return task.task_id; }).join(',')) + '"'
    : '';
  return '<button type="button" class="component-item status-' + slug + '" data-task-id="' + row.task.task_id + '"' + idsAttr + '>' +
    '<span class="component-num">' + esc(num) + '</span><b>' + esc(row.label) + '</b></button>';
}

function renderLeadRail(tasks) {
  var groups = leadStageGroups((Store.project || {}).tracked_items, tasks);
  byId('component-list').innerHTML = groups.map(function (group) {
    var isOpen = group.stage === openStage;
    var icon = ICONS[LEAD_STAGE_ICONS[group.stage]] || '';
    if (group.single) {
      var enabled = !!group.task;
      return '<div class="rail-stage rail-stage-lead rail-stage-single' + (isOpen ? ' is-active' : '') + '" data-stage="' + esc(group.stage) + '">' +
        (enabled ? '<button type="button" class="rail-stage-head' + (isOpen ? ' open' : '') + '" data-stage="' + esc(group.stage) +
          '" data-task-id="' + group.task.task_id + '" aria-current="' + (isOpen ? 'step' : 'false') + '">' :
          '<div class="rail-stage-head" aria-disabled="true">') +
        '<span class="stage-icon" aria-hidden="true">' + icon + '</span>' +
        '<span class="rail-stage-name">' + esc(group.stage) + '</span>' +
        '<span class="rail-stage-count">' + group.done + '/' + group.total + '</span>' +
        (enabled ? '</button>' : '</div>') + '</div>';
    }
    return '<div class="rail-stage rail-stage-lead' + (isOpen ? ' is-active' : '') + '" data-stage="' + esc(group.stage) + '">' +
      '<button type="button" class="rail-stage-head' + (isOpen ? ' open' : '') + '" data-stage="' + esc(group.stage) +
      '" aria-expanded="' + isOpen + '">' +
      '<span class="stage-icon" aria-hidden="true">' + icon + '</span>' +
      '<span class="rail-stage-name">' + esc(group.stage) + '</span>' +
      '<span class="rail-stage-count">' + group.done + '/' + group.total + '</span>' +
      '<span class="rail-stage-chevron" aria-hidden="true"></span></button>' +
      '<div class="rail-stage-body' + (isOpen ? '' : ' collapsed') + '" data-stage="' + esc(group.stage) + '">' +
      group.rows.map(leadRailRowHtml).join('') + '</div></div>';
  }).join('') || '<div class="empty-state">No components in this pipeline.</div>';
}

export function renderDetail() {
  var tasks = tasksForPipeline(Store.pipeline);
  var currentPipeline = currentProjectPipeline();
  var currentLabel = currentPipeline === 'bp' ? 'Business Plan Execution' : 'Prospect Maturation';
  var viewLabel = Store.pipeline === 'bp' ? 'Business Plan Execution' : 'Prospect Maturation';
  var otherPipeline = Store.pipeline === 'bp' ? 'prospect' : 'bp';
  var otherLabel = otherPipeline === 'bp' ? 'Business Plan Execution' : 'Prospect Maturation';
  var isReference = !isCurrentPipelineView();
  var leadView = isLeadView();
  // WHAT THIS RECORD IS CALLED HERE. Segment Maturation always shows the lead
  // name -- that is the pipeline the segment is being matured in, and renaming
  // it mid-pipeline because a well name was chosen would lose the thread. The
  // Business Plan view shows the name it was STAKED under once there is one
  // (workflow.display_record_name is the server's twin of this rule); the lead
  // name is not lost, it moves to the summary card's phase row.
  byId('detail-name').textContent = displayRecordName() || 'Lead / Well';
  // The chip ships hidden in index.html (no record selected yet); the render
  // rewrites its className wholesale, which is also what reveals it.
  renderLeadPriorityChip();
  byId('detail-subtitle').textContent = viewLabel + (isReference ? ' · Reference view' : ' · Current phase');
  byId('back-to-overview').textContent = '← Back to ' + currentLabel;
  /* Card 2A shell swap. The LEAD page shows one outlined back control and an
     enlarged lead name; the "Prospect Maturation · Current phase" subtitle and
     the visible "Edit all project fields" link are gone (the latter is
     relocated into the Lead Summary gear as "Edit All Inputs" and is clicked
     through this still-wired button). The BP well page and every reference
     view keep the original two controls, subtitle and link. */
  byId('detail-shell').classList.toggle('detail-shell-lead', leadView);
  byId('back-to-board').classList.toggle('hidden', !leadView);
  byId('rail-nav').classList.toggle('hidden', leadView);
  byId('detail-subtitle').classList.toggle('hidden', leadView);
  byId('open-project-editor').classList.toggle('hidden', leadView);
  var switchButton = byId('switch-pipeline-view');
  switchButton.textContent = isReference ? '← Back to ' + currentLabel : 'View ' + otherLabel + ' →';
  switchButton.setAttribute('aria-label', switchButton.textContent + ' for ' + (Store.project.project_name || 'this record'));
  var viewNote = byId('detail-view-note');
  viewNote.textContent = isReference
    ? 'Reference only — switch back to ' + currentLabel + ' to edit components or change workflow status.'
    : '';
  viewNote.classList.toggle('hidden', !isReference);
  switchButton.onclick = function () { switchPipelineView(); };
  // Accordion state is per-project: a fresh selection starts fully collapsed
  // (revealTaskStage opens the selected task's stage right after this render).
  if (Store.projectId !== openStageProjectId) { openStage = null; openStageProjectId = Store.projectId; }
  if (leadView) {
    renderLeadRail(tasks);
    wireRailHandlers();
    renderRightPanel(tasks);
    return;
  }
  // ---- BP well / reference view: the original stage rail, untouched --------
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
      '<span class="stage-icon" aria-hidden="true">' + (ICONS[STAGE_ICONS[group.stage]] || '') + '</span>' +
      '<span class="rail-stage-name">' + esc(group.stage) + '</span>' +
      '<span class="rail-stage-count">' + approved + '/' + group.tasks.length + '</span>' +
      '<span class="rail-stage-chevron" aria-hidden="true"></span></button>' +
      '<div class="rail-stage-body' + (isOpen ? '' : ' collapsed') + '" data-stage="' + esc(group.stage) + '">' + items + '</div></div>';
  }).join('') || '<div class="empty-state">No components in this pipeline.</div>';
  wireRailHandlers();
  renderRightPanel(tasks);
}

// Stage headers toggle in place; component rows load their step. Shared by the
// lead sidebar and the BP rail -- both render the same class contract, so one
// wiring pass serves either. (`.component-item-future` rows are <div>s with no
// data-task-id, so they are inert by construction.)
function wireRailHandlers() {
  all('.rail-stage-head').forEach(function (head) {
    head.addEventListener('click', function () {
      if (head.hasAttribute('data-task-id')) {
        var taskId = Number(head.getAttribute('data-task-id'));
        loadComponent(Store.tasks.find(function (task) { return task.task_id === taskId; }));
        return;
      }
      var stage = head.getAttribute('data-stage');
      // Toggle: clicking the open stage collapses it; else open it (and the
      // single-open sync closes whichever was open before).
      openStage = (openStage === stage) ? null : stage;
      openStageProjectId = Store.projectId;
      syncStageOpenState();
      syncActiveStage();
    });
  });
  all('.component-item[data-task-id]').forEach(function (button) {
    button.addEventListener('click', function () {
      var taskId = Number(button.getAttribute('data-task-id'));
      loadComponent(Store.tasks.find(function (task) { return task.task_id === taskId; }));
    });
  });
}

// Review the same record's other phase without changing its persisted phase.
// The non-operating pipeline is deliberately read-only (detail-form.js) so a
// historical/future check cannot assign or advance inactive components.
export function switchPipelineView() {
  if (!Store.projectId || !Store.project) return;
  Store.pipeline = Store.pipeline === 'bp' ? 'prospect' : 'bp';
  Store.task = null;
  activateTab(Store.pipeline);
  renderDetail();
  Promise.resolve(loadComponent(chooseInitialTask(tasksForPipeline(Store.pipeline)))).then(function () {
    requestAnimationFrame(function () {
      byId('detail-shell').scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  });
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
/* Ordered task-name ladders for the lead-phase field map, and the tiny reader
   over them. A plain `fields['<step>']` lookup is no longer enough, for two
   independent reasons -- both about buckets that still answer to a PRE-v5 name:
     * the v5 MERGE created a new "Trap and Seal CoS" row; a lead scored before
       it still holds trap_cos_pct / seal_cos_pct under the now-retired
       "Trap CoS" / "Seal CoS" buckets (the backend field map is
       retired-inclusive, so they are on the payload);
     * lead_summary_snapshots froze their {task_name: {key: value}} JSON at
       PROMOTION time and are never rewritten -- they are a historical record --
       so a well promoted before v5 carries the pre-RENAME bucket names for
       ever, and leadFieldSource() merges exactly that map with the live one.
   Surviving name first, legacy second: first non-blank wins, so re-entering a
   value on the current step always supersedes the frozen/retired one. */
var AREA_STEPS = ['Lead Assessment', 'Area Definition', 'Reservoir Area Definition'];
var THICKNESS_STEPS = ['Lead Assessment', 'Thickness Estimation'];
// Card 3E's Area delta. Both sides are stored as a P90/P10 PAIR with no mean
// between them (schema.js RANGE_PAIRS), so there is no single "area" to
// compare -- each bound is compared against its own counterpart. Ladders read
// newest authority first, the same rule POST_DRILL_PIIP_SOURCES follows: SAD
// Update supersedes SAD Model, and each merged step is followed by the retired
// step it absorbed so wells written before the merge still resolve.
var AREA_STEPS = ['Lead Assessment', 'Area Definition'];
var SAD_AREA_SOURCES = [
  ['SAD Update', 'sad_update_area_km2_'],
  ['Resource Assessment Update', 'sad_update_area_km2_'],
  ['SAD Model', 'sad_area_km2_'],
  ['Post-Drilling Resource Assessment', 'sad_area_km2_']
];
var TRAP_STEPS = ['Trap and Seal CoS', 'Trap CoS'];
var SEAL_STEPS = ['Trap and Seal CoS', 'Seal CoS'];

export function fieldFrom(map, taskNames, key) {
  var source = map || {};
  for (var i = 0; i < taskNames.length; i += 1) {
    var value = (source[taskNames[i]] || {})[key];
    if (isFilled(value)) return value;
  }
  return '';
}

// Primary Reservoir CoS row, split into its percent and a "Block · AR n"
// reference. The primary is the FIRST non-empty row (the global first-row
// semantic — backend Total CoS and the portfolio read the same row). Prefer the
// row's stored block; else reverse-lookup the AR in the seismic map; degrade to
// AR-only, then no reference. `{ pct, ref }` with empty strings when unresolved.
export function reservoirCosPrimary(fieldMap) {
  var sourceMap = fieldMap || Store.allFields;
  var rows = parseRepeatableRows(((sourceMap['Reservoir CoS'] || {}).reservoir_cos_rows) || '[]');
  var blocks = (Store.meta && Store.meta.seismic_blocks) || SEISMIC_BLOCKS;
  for (var i = 0; i < rows.length; i += 1) {
    var row = rows[i];
    if (!isFilled(row.reservoir_cos_pct)) continue;
    var block = row.seismic_block || blockForAr(blocks, row.seismic_volume_ar_number);
    var parts = [];
    if (isFilled(block)) parts.push(block);
    if (isFilled(row.seismic_volume_ar_number)) parts.push('AR ' + row.seismic_volume_ar_number);
    // `block` / `ar` are the same two values `ref` joins, kept separate for the
    // Card 2A Lead Summary footer, which lays them out itself ("Block A | AR-n").
    return { pct: String(row.reservoir_cos_pct), ref: parts.join(' · '),
             block: block || '', ar: row.seismic_volume_ar_number || '' };
  }
  return { pct: '', ref: '', block: '', ar: '' };
}
// Legacy one-string form ("Block · AR n: NN%"), kept for back-compat callers.
export function reservoirCosSummary(fieldMap) {
  var primary = reservoirCosPrimary(fieldMap);
  if (!isFilled(primary.pct)) return '';
  return (primary.ref ? primary.ref + ': ' : '') + primary.pct + '%';
}
// Source-consistent P90/Mean/P10 gas trio. Picks the newest source step (same
// LATEST_PIIP_SOURCES precedence used for the mean) whose `_gas_mean` is filled,
// then reads that SAME step's p90/mean/p10 so the trio never mixes assessments.
// `sources` is a [taskName, '<prefix>_gas_mean'] list; the trio keys share the
// prefix. Returns empty strings when no source has a filled mean.
function gasTrio(sources, fieldMap) {
  var sourceMap = fieldMap || Store.allFields;
  for (var i = 0; i < sources.length; i += 1) {
    var fields = sourceMap[sources[i][0]] || {};
    var meanKey = sources[i][1];
    if (!isFilled(fields[meanKey])) continue;
    var prefix = meanKey.replace(/_gas_mean$/, '');
    return { p90: fields[prefix + '_gas_p90'], mean: fields[meanKey], p10: fields[prefix + '_gas_p10'] };
  }
  return { p90: '', mean: '', p10: '' };
}

// The liquid (condensate) trio, or NULL when the lead has none.
//
// `<prefix>_has_liquid` IS the saved "this was a Condensate scenario" marker:
// the calculator writes it '1' exactly when the run produced condensate, i.e.
// when the selected scenario's resource_type is condensate (see
// views/resource-calculator.js buildLeadApplyFields), and '' otherwise. So this
// reads SAVED DATA, never the scenario dropdown's current position. Same source
// ladder and same precedence as gasTrio, so gas and liquid can only ever come
// from a step the lead actually recorded; null hides the section outright.
function liquidTrio(sources, fieldMap) {
  var sourceMap = fieldMap || Store.allFields;
  for (var i = 0; i < sources.length; i += 1) {
    var fields = sourceMap[sources[i][0]] || {};
    var prefix = sources[i][1].replace(/_gas_mean$/, '');
    if (!truthy(fields[prefix + '_has_liquid'])) continue;
    return { p90: fields[prefix + '_liquid_p90'], mean: fields[prefix + '_liquid_mean'],
             p10: fields[prefix + '_liquid_p10'] };
  }
  return null;
}

// "Actual" formation data resolves across phases newest-first: a formation's
// row is taken from the latest phase that has it (final > resource_update >
// post_drill > quicklook), matching the plan's flagged precedence.
var FORMATION_ACTUAL_PHASES = ['final', 'resource_update', 'post_drill', 'quicklook'];
var FORMATION_VALUE_KEYS = ['top_tvdss_ft', 'base_tvdss_ft', 'thickness_ft', 'porosity_pct', 'swt_pct', 'pay_ft', 'ngr_pct', 'fluid'];

// Collapse a formation list to one row per formation name, each taken at its
// highest-precedence phase. Names compare upper-cased (custom names are stored
// upper-cased; the canonical trio already is). Returns { NAME: {row, rank} }.
function dedupeFormationsByPhase(formations) {
  var byName = {};
  (formations || []).forEach(function (row) {
    var name = String(row.formation || '').trim().toUpperCase();
    if (!name) return;
    var rank = FORMATION_ACTUAL_PHASES.indexOf(row.phase);
    if (rank < 0) rank = FORMATION_ACTUAL_PHASES.length;
    if (!byName[name] || rank < byName[name].rank) byName[name] = { row: row, rank: rank };
  });
  return byName;
}
function formationHasData(row) {
  return !!row && FORMATION_VALUE_KEYS.some(function (key) { return isFilled(row[key]); });
}
// First filled value from a plain list (used for the well's fluid/tops ladders,
// which mix formation-derived values with legacy step-level fields).
function firstFilledValue(values) {
  for (var i = 0; i < values.length; i += 1) { if (isFilled(values[i])) return values[i]; }
  return '';
}
// SARH's fluid at a specific phase, read straight from the formation list (not
// the newest-phase dedupe): the well's fluid ladder blends these per-phase SARH
// fluids with the legacy step-level *_fluid_type fields.
function sarhFluidAtPhase(formations, phase) {
  var rows = (formations || []).filter(function (row) {
    return String(row.formation || '').trim().toUpperCase() === 'SARH' && row.phase === phase;
  });
  for (var i = 0; i < rows.length; i += 1) { if (isFilled(rows[i].fluid)) return rows[i].fluid; }
  return '';
}
// "tight" is DERIVED, never a default: the formation row must EXIST (it was
// penetrated/logged in a BP step) AND read as non-pay — fluid 'Dry Hole' (or
// its pre-v10 spelling 'Dry'), or a blank fluid with zero pay. A missing row
// (no BP data) is NOT tight; it renders as a dash. Generic across formations so
// any barren reservoir can read "tight".
function formationIsTight(row) {
  if (!row) return false;
  var fluid = String(row.fluid || '').trim();
  if (fluid === 'Dry' || fluid === 'Dry Hole') return true;
  return fluid === '' && (row.pay_ft === 0 || String(row.pay_ft).trim() === '0');
}
// The staked well name, or '' -- captured at Well Site Location and read back
// from the record's own fields.
export function stakedWellName() {
  return (Store.allFields['Well Site Location'] || {}).staked_well_name || '';
}

// Card 3V: ONE canonical name, decided by the SERVER (workflow.
// annotate_canonical_names) and published as project_name on every payload,
// with the lead name alongside as lead_name. The client used to re-derive this
// and carve out Segment Maturation; it no longer does, because two places
// deciding what a record is called is exactly how surfaces come to disagree.
export function displayRecordName() {
  return (Store.project && Store.project.project_name) || '';
}

// The name the record was matured under. Never lost -- it rides every payload
// that carries the canonical one.
export function leadRecordName() {
  var project = Store.project || {};
  return project.lead_name || project.project_name || '';
}

/* Reservoir Properties uses TWO decimals, not fmtNum's one: a water saturation
   or a porosity is read to the hundredth (0.92, 21.35) and rounding it to one
   place throws away a digit that matters. Percentages stay percentages -- the
   value is shown exactly as it is stored and entered, so a number on this card
   can be compared with the field it came from without a mental conversion. */
export function fmt2(raw) {
  if (!isFilled(raw)) return '';
  var numeric = Number(raw);
  if (!isFinite(numeric)) return String(raw);
  return numeric.toFixed(2);
}

// One row of the Reservoir Properties table: the formation name, then pay
// thickness, porosity and water saturation. A row with nothing recorded (or a
// barren one) collapses to a single note spanning the value columns, so an
// empty formation never reads as three missing measurements.
function formationPropertyRow(name, row) {
  var tight = formationIsTight(row);
  if (tight || !formationHasData(row)) {
    return '<div class="summary-props-row summary-props-row-empty">' +
      '<span class="summary-props-name">' + esc(name) + '</span>' +
      '<span class="summary-props-note">' + (tight ? 'tight' : EM_DASH) + '</span></div>';
  }
  var cell = function (value, suffix) {
    var text = fmt2(value);
    return '<span>' + (text ? esc(text) + (suffix || '') : EM_DASH) + '</span>';
  };
  // Card 3E: porosity and water saturation print as bare two-decimal numbers.
  // The stored value is unchanged and unconverted -- only the % sign goes, and
  // the column heading carries the measure. Pay thickness keeps its unit; it
  // is a length, and the card's rule is about the two percentages.
  return '<div class="summary-props-row">' +
    '<span class="summary-props-name">' + esc(name) + '</span>' +
    cell(row.pay_ft, ' ft') + cell(row.porosity_pct) + cell(row.swt_pct) +
    '</div>';
}

// Well-card fold open state, keyed by fold id ('pva', 'lead'). Module-level so
// it survives the re-render after each save; reset when the selected project
// changes (mirrors the rail-stage accordion's per-project guard). It is THIS
// shell's state only -- the Business Plan Execution shell renders the same card
// (see wellSummaryBodyHtml) and keeps its own map, so opening a fold on one
// page does not silently open it on the other.
var openFolds = {};
var foldProjectId = null;

// One collapsible section of the well card: chevron header + collapsed body.
// `id` is the fold key and `folds` the open-state map it reads; the rendered
// ids are <prefix>summary-fold-<id>[-body]. The prefix exists because both
// shells can be mounted at once (they are two tabs of one document), and two
// nodes cannot share an id.
function foldSection(id, title, bodyHtml, folds, prefix) {
  var isOpen = !!(folds || openFolds)[id];
  var domId = (prefix || '') + 'summary-fold-' + id;
  return '<div class="summary-fold">' +
    '<button id="' + domId + '" type="button" class="summary-fold-head' + (isOpen ? ' open' : '') +
    '" data-fold="' + id + '" aria-expanded="' + isOpen + '" aria-controls="' + domId + '-body">' +
    '<span class="summary-fold-title">' + esc(title) + '</span>' +
    '<span class="summary-fold-chevron" aria-hidden="true"></span></button>' +
    '<div id="' + domId + '-body" class="summary-fold-body' + (isOpen ? '' : ' collapsed') + '">' + bodyHtml + '</div></div>';
}

/* Toggle each rendered fold in place so the surrounding card isn't re-rendered
   -- Card 3E's rule that opening a fold must not write or mutate anything. The
   body is found as the head's own sibling rather than by id, so a shell that
   prefixes its ids needs no second lookup rule. `onToggle` lets a caller stamp
   whatever it uses to decide when the state is stale. */
export function wireWellSummaryFolds(root, folds, onToggle) {
  var state = folds || openFolds;
  all('.summary-fold-head', root || document).forEach(function (head) {
    head.addEventListener('click', function () {
      var id = head.getAttribute('data-fold');
      var isOpen = !state[id];
      state[id] = isOpen;
      if (onToggle) onToggle(id, isOpen);
      head.classList.toggle('open', isOpen);
      head.setAttribute('aria-expanded', String(isOpen));
      var body = head.parentNode && head.parentNode.querySelector('.summary-fold-body');
      if (body) body.classList.toggle('collapsed', !isOpen);
    });
  });
}
// Scoped to THIS card, not the document: the Business Plan Execution shell has
// folds of its own (its Well Summary and its Formation Interpretation sheet),
// and a document-wide bind would hand them a second handler that undoes the
// first one's toggle.
function wireFolds() {
  wireWellSummaryFolds(byId('lead-summary'), openFolds, function () { foldProjectId = Store.projectId; });
}

// Folders fold: one row per WELL_OVERVIEW_DIRECTORY_MAP section key (see
// folders.get_section_folder_link). Rendered synchronously as a loading
// placeholder -- the summary card itself never waits on the network -- then
// filled in by wireFolderLinks() once each lazy fetch resolves. Mirrors the
// component-folder card's glyph/path/copy-button markup (renderComponentFolder
// in detail-form.js) so both folder-link styles read as one pattern.
function folderRowHtml(sectionKey) {
  return '<div class="folder-card" data-folder-key="' + esc(sectionKey) + '">' +
    '<span class="folder-glyph" aria-hidden="true">' + ICONS['folder'] + '</span>' +
    '<span class="folder-path" id="summary-folder-path-' + esc(sectionKey) + '">Loading…</span>' +
    '<button type="button" class="icon-btn" id="summary-folder-copy-' + esc(sectionKey) +
    '" title="Copy folder link" aria-label="Copy folder link" disabled>' + ICONS['copy'] + '</button></div>';
}
function foldersHtml(sectionKeys) {
  return '<div class="summary-folders">' + sectionKeys.map(folderRowHtml).join('') + '</div>';
}
// Fetches each section's folder link after the card is already on screen and
// fills the placeholder row in place. A 404/failure (unmounted share, unknown
// section) degrades to a quiet inline message -- never a thrown console error
// -- and a stale response for a project the user has since navigated away
// from is dropped rather than overwriting the now-current card.
function wireFolderLinks(sectionKeys) {
  var forProjectId = Store.projectId;
  sectionKeys.forEach(function (sectionKey) {
    API.sectionFolder(forProjectId, sectionKey).then(function (info) {
      if (Store.projectId !== forProjectId) return;
      var pathEl = byId('summary-folder-path-' + sectionKey);
      var copyBtn = byId('summary-folder-copy-' + sectionKey);
      if (!pathEl) return;
      var path = (info && info.unc_path) || '';
      var label = (info && info.section) || sectionKey;
      pathEl.textContent = label + ': ' + (path || 'Not configured.');
      pathEl.title = path || '';
      if (copyBtn && path) {
        copyBtn.disabled = false;
        copyBtn.addEventListener('click', function () { copyText(path); });
      }
    }).catch(function () {
      if (Store.projectId !== forProjectId) return;
      var pathEl = byId('summary-folder-path-' + sectionKey);
      if (pathEl) pathEl.textContent = 'Folder link unavailable.';
    });
  });
}

function numOrNull(value) {
  if (!isFilled(value)) return null;
  var n = Number(value);
  return isNaN(n) ? null : n;
}
// One predicted|actual comparison row. Δ appears only when both sides parse as
// finite numbers (the Top-SARH predicted value is free text and often won't).
function pvaRow(label, predicted, actual) {
  var predHtml = isFilled(predicted) ? esc(fmtNum(predicted)) : '—';
  var actualHtml = isFilled(actual) ? esc(fmtNum(actual)) : '—';
  var pn = numOrNull(predicted), an = numOrNull(actual);
  var deltaHtml = '<span class="summary-pva-delta"></span>';
  if (pn !== null && an !== null) {
    var delta = Math.round((an - pn) * 10) / 10;
    deltaHtml = '<span class="summary-pva-delta ' + (delta >= 0 ? 'pos' : 'neg') + '">Δ ' + (delta > 0 ? '+' : '') + delta + '</span>';
  }
  return '<div class="summary-pva-row"><span class="summary-pva-label">' + esc(label) + '</span>' +
    '<span class="summary-pva-cell">' + predHtml + '</span>' +
    '<span class="summary-pva-cell">' + actualHtml + '</span>' + deltaHtml + '</div>';
}

// One compact metric row: label (+ optional source note) and its value (— when
// blank). Kept tiny so the summary card stays far denser than the old tiles.
// Numeric values render rounded to 1 decimal (fmtNum passes text through).
function metricRow(label, value, note) {
  var small = note ? '<small>' + esc(note) + '</small>' : '';
  return '<div class="summary-metric"><div class="summary-metric-label"><span>' + esc(label) + '</span>' + small + '</div><div class="summary-metric-value">' + (isFilled(value) ? esc(fmtNum(value)) : '—') + '</div></div>';
}

/* One titled block of the well card, in the Lead Summary card's own anatomy
   (.ls-section / .ls-section-title): a title-case navy heading over its
   content, separated from the block above by a hairline. The two cards sit in
   the same slot on the same page, so they read as one component with two
   contents rather than two components. */
function summarySection(title, bodyHtml) {
  return '<section class="ls-section summary-section">' +
    '<h4 class="ls-section-title">' + esc(title) + '</h4>' + bodyHtml + '</section>';
}

// A row of aligned label-over-value columns -- the Lead Summary's own grid, so
// the well card's Gas trio lines up exactly as the lead card's does.
function columnsHtml(columns) {
  var cells = columns.map(function (column) {
    return '<div class="ls-col"><span class="ls-col-label">' + esc(column.label) + '</span>' +
      '<span class="ls-col-value">' + (isFilled(column.value) ? esc(fmtNum(column.value)) : EM_DASH) +
      '</span></div>';
  }).join('');
  return '<div class="ls-grid" style="grid-template-columns:repeat(' +
    columns.length + ',minmax(0,1fr))">' + cells + '</div>';
}

// A stat cluster: a quiet group label over a row of sub-label/value columns
// (sub-label sits above its value, columns aligned). `cols` is a list of
// { label, value }; empty values render as — in place, so a cluster with no
// values at all still renders once with all dashes (it is never hidden). An
// optional `context` line (e.g. the Reservoir CoS "Block · AR n" reference)
// sits quietly below the grid. Numbers read larger than the sub-labels.
// It renders through the SAME section/grid helpers the rest of the card now
// uses, so the Lead Summary fold's trios read as "P90 Mean P10" like the trios
// above them rather than "P90 MEAN P10" -- one card, one voice, open or shut.
function statCluster(label, cols, context) {
  var contextHtml = isFilled(context) ? '<div class="summary-cluster-context">' + esc(context) + '</div>' : '';
  return summarySection(label, columnsHtml(cols) + contextHtml);
}

// LEAD_PIIP_SOURCES (the lead half of LATEST_PIIP_SOURCES) and
// POST_DRILL_PIIP_SOURCES (the post-drill half, merged steps first and their
// pre-v4 retired twins right behind) are imported from detail-form.js by name
// rather than sliced out by index -- the two halves changed length when the v4
// step merges added the legacy fallback entries.

// The lead-phase field map as seen from the well card: the snapshot frozen at
// promotion wins field by field, with the live lead tasks (which stay active
// after promotion) filling any gap -- the same snapshot-then-live precedence
// Prediction vs Actual uses for its predicted column. A record with no
// snapshot (never promoted through the lead phase) reads entirely live.
function leadFieldSource(leadSummary, allFields) {
  var frozen = (leadSummary && leadSummary.fields) || {};
  var live = allFields || {};
  var merged = {};
  Object.keys(live).concat(Object.keys(frozen)).forEach(function (task) {
    if (merged[task]) return;
    var liveFields = live[task] || {};
    var frozenFields = frozen[task] || {};
    var fields = {};
    Object.keys(liveFields).concat(Object.keys(frozenFields)).forEach(function (key) {
      fields[key] = isFilled(frozenFields[key]) ? frozenFields[key] : liveFields[key];
    });
    merged[task] = fields;
  });
  return merged;
}

// The lead card's body: volumetrics + chance-of-success, over a given field map
// and gas-mean source list. Rendered on its own for a lead record and inside
// the well card's Lead Summary fold for a promoted one. Total CoS comes from
// the payload's overview (derived at read time from the still-active lead CoS
// steps), never recomputed here -- it is handed in as `derisking` because the
// well card renders in two shells, each with its own payload.
function leadMetricsHtml(fieldMap, gasSources, derisking) {
  var resCos = reservoirCosPrimary(fieldMap);
  var trio = gasTrio(gasSources, fieldMap);
  return '<div class="summary-metrics">' +
    statCluster('Gas (BCF)', [
      { label: 'P90', value: trio.p90 },
      { label: 'Mean', value: trio.mean },
      { label: 'P10', value: trio.p10 }
    ]) +
    metricRow('Reservoir Thickness (ft)', fieldFrom(fieldMap, THICKNESS_STEPS, 'reservoir_thickness_ft')) +
    statCluster('Chance of Success (%)', [
      { label: 'Res', value: resCos.pct },
      { label: 'Trap', value: fieldFrom(fieldMap, TRAP_STEPS, 'trap_cos_pct') },
      { label: 'Seal', value: fieldFrom(fieldMap, SEAL_STEPS, 'seal_cos_pct') },
      { label: 'Total', value: derisking }
    ], resCos.ref) +
    '</div>';
}

/* =========================================================================
   THE WELL SUMMARY CARD'S BODY — Card 3E, rendered in BOTH shells.

   The Segment Maturation detail page and the Business Plan Execution detail
   page each show a Well Summary beside the step they are working, and Card 3E
   describes ONE card: Gas, how the well flowed, what the rock turned out to
   be, then exactly two expandable sections (Simulated Vs Actual Delta, then
   Lead Summary). So there is one builder, called from both, rather than a
   second visually similar component on the BPE side.

   It reads NO module state. Everything comes in through `source`:

     {
       fields:      { <task name>: { <field key>: value } },  // retired-inclusive
       formations:  [ <project_formations row>, ... ],        // EVERY phase
       leadSummary: { fields, captured_at, captured_by } | null,
       derisking:   '<Total CoS %>'   // computed server-side, never here
     }

   `folds` is the caller's own open-state map (fold id -> bool) and `prefix`
   namespaces the rendered ids, because both shells can be mounted in one
   document. Rendering never writes: a fold's state lives in the caller's map,
   which is why toggling one saves nothing.
   ========================================================================= */
export function wellSummaryBodyHtml(source, folds, prefix) {
  var data = source || {};
  var fields = data.fields || {};
  var formations = data.formations || [];
  var leadSummary = data.leadSummary || null;
  var deduped = dedupeFormationsByPhase(formations);
  var sarh = deduped['SARH'] ? deduped['SARH'].row : null;
  // Post-Drill Gas: source-consistent P90/Mean/P10 from resource_update else
  // post_drill (the drilled results only). Mean feeds Prediction vs Actual.
  var postDrillTrio = gasTrio(POST_DRILL_PIIP_SOURCES, fields);
  var meanPostDrill = postDrillTrio.mean;
  var prognosis = (fields['Well Proposal'] || {}).sarh_formation_prognosis_pre_drill;
  // SARH top prefers the formation row; legacy wells stored the top at step
  // level (final then quicklook), so fall back there before blanking.
  var topSarh = sarh ? sarh.top_tvdss_ft : '';
  if (!isFilled(topSarh)) {
    topSarh = firstFilledValue([
      (fields['Final Log Analysis'] || {}).final_top_reservoir_tvdss_ft,
      (fields['Quicklook Logs'] || {}).quicklook_top_reservoir_tvdss_ft
    ]);
  }
  // "Gas (BCF)", not "Post-Drill Gas": on a well card every figure is a
  // post-drill figure, and the qualifier only made the heading longer than
  // the three numbers under it.
  // The card face carries the RESULTS: volumes, how the well flowed, and
  // what the reservoir turned out to be. SARH Prognosis and Top SARH used to
  // sit here as two loose rows; they are predictions, and both already
  // appear -- against their actuals, which is the only way they mean
  // anything -- inside the Simulated vs Actual Delta fold below.
  // Card 3E, as drawn: Gas leads the card as a titled section with the
  // trio beneath it. Built from the SAME markup the Segment Maturation Lead
  // Summary uses for its own trios (.ls-section / .ls-grid / .ls-col), so
  // the two cards are one visual language rather than two that resemble
  // each other.
  var metricsHtml = summarySection('Gas (BCF)', columnsHtml([
    { label: 'P90', value: postDrillTrio.p90 },
    { label: 'Mean', value: postDrillTrio.mean },
    { label: 'P10', value: postDrillTrio.p10 }
  ]));

  // Reservoir Properties: a small table -- one header row over one row per
  // formation, name on the left. It replaces the run-on "120 ft · 8.5% φ ·
  // 35% Sw · 60 ft pay" line, which forced the reader to parse each value's
  // unit to know which measure it was. SARH always leads (barren → "tight");
  // every other formation with data follows.
  var reservoirRows = [formationPropertyRow('SARH', sarh)];
  Object.keys(deduped).sort().forEach(function (name) {
    if (name === 'SARH' || !formationHasData(deduped[name].row)) return;
    reservoirRows.push(formationPropertyRow(name, deduped[name].row));
  });
  var reservoirsHtml = summarySection('Reservoir Properties',
    '<div class="summary-props">' +
    '<div class="summary-props-head">' +
      '<span class="summary-props-name"></span>' +
      '<span>Pay Thickness</span><span>Porosity (φ)</span><span>Water Saturation (Sw)</span>' +
    '</div>' + reservoirRows.join('') + '</div>');

  // Flowback rate: the headline rate lives in a fluid-specific EAV field.
  // Petrophysical fluid precedence (newest authority first), mirroring the
  // formations phase precedence and the backend portfolio status order.
  // Well fluid ladder (newest authority first), matching the backend: SARH's
  // final-phase formation fluid, then legacy final_fluid_type, the two
  // resource assessments' fluid, SARH's quicklook-phase fluid, then legacy
  // quicklook_fluid_type. Each resource-assessment rung is read from the
  // step that owns it since the v4 merges (SAD Update / SAD Model) with the
  // retired step it absorbed right behind it, for wells written before.
  var fluid = firstFilledValue([
    sarhFluidAtPhase(formations, 'final'),
    (fields['Final Log Analysis'] || {}).final_fluid_type,
    (fields['SAD Update'] || {}).resource_update_fluid_type,
    (fields['Resource Assessment Update'] || {}).resource_update_fluid_type,
    (fields['SAD Model'] || {}).post_drill_fluid_type,
    (fields['Post-Drilling Resource Assessment'] || {}).post_drill_fluid_type,
    sarhFluidAtPhase(formations, 'quicklook'),
    (fields['Quicklook Logs'] || {}).quicklook_fluid_type
  ]);
  var flowEntry = FLOWBACK_RATE_FIELDS[fluid] || FLOWBACK_RATE_FIELDS['Gas'];
  // Primary flowback values are stage #1 -- the first non-empty row of the
  // Flowback Results stages mini-sheet (whose column keys reuse the retired
  // flat key names). Only when NO stage row exists does the read fall back
  // to the retired step-level flat key, so a stage row and legacy flat data
  // never mix (single-vintage rule, like the Reservoir CoS primary row).
  var flowbackFields = fields['Flowback Results'] || {};
  var primaryStage = null;
  parseRepeatableRows(flowbackFields.flowback_stages_rows || '[]').forEach(function (stage) {
    if (primaryStage || !stage) return;
    if (Object.keys(stage).some(function (key) { return isFilled(stage[key]); })) primaryStage = stage;
  });
  // Flowback Results: the headline rate plus the two figures that say under
  // what conditions it was measured. A rate without its wellhead pressure
  // and choke size is not a comparable number, and both were already
  // recorded on the stage row -- they simply had no surface here.
  var flowRead = function (key) {
    return primaryStage ? primaryStage[key] : flowbackFields[key];
  };
  var flowValue = flowRead(flowEntry.key);
  var fwhp = flowRead('flowback_fwhp_psi');
  var choke = flowRead('flowback_choke_size_in');
  var flowbackHtml = summarySection('Flowback Results',
    '<div class="summary-metrics">' +
    // The rate's label follows the well's fluid, so an oil well does not
    // read "Gas Rate" (the fluid itself rides along as the row's context).
    metricRow(flowEntry.label || 'Gas Rate',
      isFilled(flowValue) ? fmtNum(flowValue) + ' ' + flowEntry.unit : '',
      isFilled(fluid) ? fluid : 'Gas') +
    metricRow('Flowing Wellhead Pressure (FWHP)', isFilled(fwhp) ? fmtNum(fwhp) + ' psi' : '') +
    metricRow('Choke Size', isFilled(choke) ? fmtNum(choke) + ' in' : '') +
    '</div>');

  // Prediction vs Actual: predicted values read the frozen lead snapshot,
  // falling back to live fields where the plan allows.
  var snap = (leadSummary && leadSummary.fields) || {};
  var predThickness = fieldFrom(snap, THICKNESS_STEPS, 'reservoir_thickness_ft');
  if (!isFilled(predThickness)) predThickness = fieldFrom(fields, THICKNESS_STEPS, 'reservoir_thickness_ft');
  var predMean = '';
  for (var si = 0; si < LEAD_PIIP_SOURCES.length && !isFilled(predMean); si += 1) predMean = (snap[LEAD_PIIP_SOURCES[si][0]] || {})[LEAD_PIIP_SOURCES[si][1]] || '';
  for (var li = 0; li < LEAD_PIIP_SOURCES.length && !isFilled(predMean); li += 1) predMean = (fields[LEAD_PIIP_SOURCES[li][0]] || {})[LEAD_PIIP_SOURCES[li][1]] || '';
  // Card 3E adds Area. It is stored as a P90/P10 pair on BOTH sides with no
  // mean between them, so there is no single area to compare: each bound
  // meets its own counterpart through the existing delta mechanism. Reading
  // one bound alone, or averaging the two into an invented mean, would both
  // be claims the data does not make.
  function areaBound(bound) {
    var predicted = fieldFrom(snap, AREA_STEPS, 'p' + bound + '_area_km2');
    if (!isFilled(predicted)) predicted = fieldFrom(fields, AREA_STEPS, 'p' + bound + '_area_km2');
    var actual = '';
    for (var ai = 0; ai < SAD_AREA_SOURCES.length && !isFilled(actual); ai += 1) {
      actual = (fields[SAD_AREA_SOURCES[ai][0]] || {})[SAD_AREA_SOURCES[ai][1] + 'p' + bound] || '';
    }
    return pvaRow('Area P' + bound + ' (km²)', predicted, actual);
  }
  var pvaHtml = foldSection('pva', 'Simulated Vs Actual Delta',
    '<div class="summary-pva-head-row"><span class="summary-pva-label"></span><span class="summary-pva-cell summary-pva-colhead">Predicted</span><span class="summary-pva-cell summary-pva-colhead">Actual</span><span class="summary-pva-delta"></span></div>' +
    pvaRow('Top SARH', prognosis, topSarh) +
    pvaRow('Thickness (ft)', predThickness, sarh ? sarh.thickness_ft : '') +
    areaBound('90') + areaBound('10') +
    pvaRow('Mean (BCF)', predMean, meanPostDrill), folds, prefix);

  // Lead Summary: the same card the lead phase shows, over the frozen
  // snapshot (see leadFieldSource) and the lead's own gas sources -- the
  // volumetrics and chance-of-success this well was drilled on. Kept folded
  // so the well card still opens on its post-drill results.
  var captured = leadSummary && leadSummary.captured_at;
  var capturedNote = captured
    ? '<div class="summary-fold-note">Captured at promotion · ' + esc(String(captured).slice(0, 10)) +
      (leadSummary.captured_by ? ' · ' + esc(leadSummary.captured_by) : '') + '</div>'
    : '';
  var leadHtml = foldSection('lead', 'Lead Summary',
    leadMetricsHtml(leadFieldSource(leadSummary, fields), LEAD_PIIP_SOURCES, data.derisking) + capturedNote,
    folds, prefix);

  // The well card ends here: TWO folds, Simulated vs Actual Delta and Lead
  // Summary. The Folders fold is deliberately gone -- every step still
  // carries its own shared-folder card (renderComponentFolder in
  // detail-form.js), which is where a folder link is actually wanted while
  // working a step.
  // The card's order, as drawn: Gas, how it flowed, what the rock turned out
  // to be, then the two expandable sections.
  return metricsHtml + flowbackHtml + reservoirsHtml + pvaHtml + leadHtml;
}

// The gear popover, its outside-click/Escape dismissal, and the toggle button
// (static in index.html) are wired once — the button and document persist
// across re-renders, so byId resolves the freshly rendered popover at call
// time and no listeners stack.
function closeSummarySettings() {
  var popover = byId('summary-settings');
  if (popover) popover.classList.add('hidden');
  var toggle = byId('summary-settings-toggle');
  if (toggle) toggle.setAttribute('aria-expanded', 'false');
}
var summarySettingsWired = false;
function wireSummarySettings() {
  if (summarySettingsWired) return;
  summarySettingsWired = true;
  var toggle = byId('summary-settings-toggle');
  var close = closeSummarySettings;
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

/* Card 2A: resolve the shared Lead Summary's ONE data object out of Store.
   Everything the component renders is decided here; leadSummaryHtml itself
   reads no state. Kept exported so a test can assert the SHAPE without the
   markup. */
export function leadSummaryData() {
  var fields = Store.allFields || {};
  var resCos = reservoirCosPrimary(fields);
  var thickness = fields['Lead Assessment'] || fields['Thickness Estimation'] || {};
  return {
    // The lead's twelve TRACKED ITEMS, derived server-side and already on the
    // /detail payload's project row (get_project runs the same annotation the
    // board rows get). Counted with the board's own helper, so the card's
    // "NN% n / 12" and the board donut can never disagree. A record without
    // tracked items (a BP well) simply reads 0 / 12.
    progress: {
      completed: completedItemCount(Store.project || {}),
      total: TRACKED_ITEM_COUNT
    },
    gas: gasTrio(LATEST_PIIP_SOURCES, fields),
    liquid: liquidTrio(LATEST_PIIP_SOURCES, fields),
    // The FINAL calculated thicknesses in feet. The two-way TWT (ms) inputs the
    // Card 2B form will carry are a different pair of keys entirely and are
    // deliberately never read here.
    thickness: {
      formation: thickness.formation_thickness_ft,
      reservoir: thickness.reservoir_thickness_ft
    },
    area: {
      p90: fieldFrom(fields, AREA_STEPS, 'p90_area_km2'),
      p10: fieldFrom(fields, AREA_STEPS, 'p10_area_km2')
    },
    cos: {
      reservoir: resCos.pct,
      trap: fieldFrom(fields, TRAP_STEPS, 'trap_cos_pct'),
      seal: fieldFrom(fields, SEAL_STEPS, 'seal_cos_pct'),
      // Derived at read time (Reservoir x Trap x Seal) and delivered by
      // /detail's overview -- never recomputed on the client.
      total: (Store.overview || {}).derisking
    },
    block: resCos.block,
    ar: resCos.ar,
    canManage: isCurrentPipelineView()
  };
}

export function renderRightPanel(tasks) {
  var staticHead = byId('summary-card-head');
  if (isLeadView()) {
    /* ---- Card 2A: the shared LEAD SUMMARY component ----------------------
       It renders its own header + gear, so the card's static header is hidden
       here; it is restored for the well card below. The three gear items are
       exactly Edit All Inputs / Rename Lead / Delete Lead -- "Edit All Inputs"
       triggers the SAME #open-project-editor action the (now hidden) rail link
       used to expose, clicked through rather than imported so this module does
       not take a circular dependency on views/project-editor.js. */
    if (staticHead) staticHead.classList.add('hidden');
    byId('lead-summary').innerHTML = leadSummaryHtml(leadSummaryData());
    wireLeadSummary({
      onEditAll: function () {
        var button = byId('open-project-editor');
        if (button) button.click();
      },
      onRename: renameSelectedProject,
      onDelete: deleteSelectedProject
    });
    return;
  }
  if (staticHead) staticHead.classList.remove('hidden');
  closeLeadSummaryMenu();
  // `tasks` is already scoped to the operating pipeline's stages (see
  // tasksForPipeline), so every row counts toward progress.
  var completed = tasks.filter(function (task) { return DONE[task.status]; }).length;
  var percent = tasks.length ? Math.round((completed / tasks.length) * 100) : 0;

  var isBP = Number(Store.project.business_plan_enabled || 0) === 1;
  var viewingBP = Store.pipeline === 'bp';
  var referenceOnly = !isCurrentPipelineView();
  var isActive = Number(Store.project.active_well_enabled || 0) === 1;
  var year = Number(Store.project.business_plan_year || new Date().getFullYear());
  if (year < 2026 || year > 2040) year = 2026;
  var recordKind = String(Store.project.pipeline_type || '').toLowerCase() === 'bp' ? 'Well' : 'Lead';

  var progressHtml =
    '<div class="summary-progress"><div class="summary-progress-bar"><span style="width:' + percent + '%"></span></div>' +
    '<div class="summary-progress-figures"><b>' + percent + '%</b><small>' + completed + ' / ' + tasks.length + '</small></div></div>';
  // Phase row: where the record sits (Lead vs BP Well · year). The phase MOVE
  // itself is a gear-popover action (see popoverHtml) -- rare, irreversible
  // without a counter-move, and supervisor-only, so it stays off the card face.
  //
  // The OTHER name rides here, opposite the phase. A staked record is titled
  // by the name it is KNOWN by, so this carries the lead it was matured as --
  // the pairing stays visible once the title has changed, in both pipelines.
  var leadName = leadRecordName();
  var canonical = displayRecordName();
  var stakedHtml = (leadName && leadName !== canonical)
    ? '<span class="summary-phase-well" title="Lead name">' + esc(leadName) + '</span>'
    : '';
  var phaseHtml = '<div class="summary-phase"><span class="summary-phase-label">' +
    (isBP ? 'BP Well · ' + esc(Store.project.business_plan_year || year) : 'Lead') +
    '</span>' + stakedHtml + '</div>';
  // Phase-specific body: leads show volumetrics + chance-of-success; drilled BP
  // wells show post-drill results per formation plus a predicted-vs-actual
  // comparison against the frozen lead snapshot.
  var bodyHtml;
  // Which section folders the card lazily resolves after it is written. Only
  // the LEAD card has a Folders fold; the well card's was removed (each step
  // carries its own folder card instead). Declared here rather than inside the
  // lead branch so the well card hands wireFolderLinks an empty list instead
  // of `undefined` -- the latter threw, and the throw took wireSummarySettings
  // down with it, leaving the well card's gear popover dead.
  var folderSectionKeys = [];
  if (viewingBP) {
    // ---- Well card ----------------------------------------------------------
    // Folds are per-project, like the rail accordion: a fresh selection starts
    // with every fold collapsed.
    if (Store.projectId !== foldProjectId) { openFolds = {}; foldProjectId = Store.projectId; }
    // Card 3E's one card, built by the shared builder this shell and the
    // Business Plan Execution shell both call. Store is unwrapped HERE -- the
    // builder itself reads no state, so the other shell can hand it the same
    // four inputs off its own payload.
    bodyHtml = wellSummaryBodyHtml({
      fields: Store.allFields,
      formations: Store.formations,
      leadSummary: Store.leadSummary,
      derisking: (Store.overview || {}).derisking
    }, openFolds);
  } else {
    // ---- Lead card ----------------------------------------------------------
    // Res CoS is the primary first-row percent; its "Block · AR n" reference
    // rides along as the cluster's quiet context line (no bulky <small> label).
    folderSectionKeys = ['lead'];
    var foldersFoldHtml = foldSection('folders', 'Folders', foldersHtml(folderSectionKeys));
    bodyHtml = leadMetricsHtml(Store.allFields, LATEST_PIIP_SOURCES,
                               (Store.overview || {}).derisking) + foldersFoldHtml;
  }
  /* Popover: what the compact card dropped but still needs a home -- the Active
     Well flag, the phase move, and rename/delete. The phase button is
     supervisor-only (canTransitionPhase) and, like every action in here, is
     withheld from the reference view; transitions.js owns the confirm + PATCH.

     Card 2A RELOCATION: the Active Well checkbox and the phase move are WELL
     concerns, so they now render only in this (well / reference) popover --
     the redesigned Lead Summary gear carries exactly three lead actions and
     neither of these. Nothing was removed from the app: for a BP well both
     controls are exactly where they were, and for a LEAD both remain reachable
     through "Edit All Inputs" -> the all-fields editor's Properties card
     (its Active Well checkbox and its supervisor-gated "Promote to BP Well…"
     phase row), plus this popover whenever the lead is opened through the
     Business Plan Execution reference view. */
  var relocatedHtml = '';
  if (viewingBP) {
    var phaseButtonHtml = '';
    if (canTransitionPhase() && !referenceOnly) {
      phaseButtonHtml = '<div class="summary-popover-actions">' + (isBP
        ? '<button id="summary-phase-action" type="button" class="ghost danger-outline">Recall to Lead Phase…</button>'
        : '<button id="summary-phase-action" type="button" class="ghost">Promote to BP Well…</button>') + '</div>';
    }
    // Card 3X. Exact copy, no article. The checkbox IS the accessible state:
    // the animated border is a second, redundant signal, never the only one.
    var isDrilling = Number(Store.project.active_drilling || 0) === 1;
    relocatedHtml =
      '<label class="summary-popover-check"><input id="summary-active-flag" type="checkbox" ' +
      (isActive ? 'checked' : '') + '> Active Well</label>' +
      '<label class="summary-popover-check"><input id="summary-active-drilling" type="checkbox" ' +
      (isDrilling ? 'checked' : '') + '> Active Drilling</label>' + phaseButtonHtml;
  }
  var popoverHtml =
    '<div id="summary-settings" class="summary-popover hidden" role="dialog" aria-label="Manage ' + recordKind.toLowerCase() + '">' +
    relocatedHtml +
    // The two coming-soon exports used to sit here. Card 3B puts them in the
    // page's own gear menu instead, beside Dark Mode and Export to Excel
    // (views/header-menus.js) -- they are app-wide exports, not per-record
    // actions, so this popover was the wrong shelf for them.
    '<div class="summary-popover-actions"><button id="rename-record" type="button" class="ghost">Rename ' + recordKind + '</button><button id="delete-record" type="button" class="danger">Delete ' + recordKind + '</button></div></div>';

  byId('summary-title').textContent = viewingBP ? 'Well Summary' : 'Lead Summary';
  byId('lead-summary').innerHTML = progressHtml + phaseHtml + bodyHtml + popoverHtml;

  // Record-level actions stay with the operating pipeline too. This avoids a
  // "reference only" view exposing a hidden phase/flag mutation through the
  // summary gear while still leaving the explicit all-fields editor available
  // as a separate, deliberate workflow.
  var settingsToggle = byId('summary-settings-toggle');
  if (settingsToggle) {
    settingsToggle.disabled = referenceOnly;
    settingsToggle.title = referenceOnly ? 'Return to the current pipeline to manage this record' : 'Manage lead / well';
    settingsToggle.setAttribute('aria-label', settingsToggle.title);
    settingsToggle.setAttribute('aria-expanded', 'false');
  }

  var activeFlag = byId('summary-active-flag');
  if (activeFlag) activeFlag.addEventListener('change', function () { saveProjectFlags({ active_well_enabled: activeFlag.checked }); });
  var drillingFlag = byId('summary-active-drilling');
  if (drillingFlag) drillingFlag.addEventListener('change', function () {
    saveProjectFlags({ active_drilling: drillingFlag.checked });
  });
  var phaseAction = byId('summary-phase-action');
  if (phaseAction) phaseAction.addEventListener('click', function () {
    // Hand off to the confirm dialog with the popover already dismissed, so a
    // cancelled move doesn't leave the gear menu hanging open behind it.
    closeSummarySettings();
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
  // Prediction-vs-Actual / Lead Summary folds (well card only; the lead card
  // renders none, so this is a no-op there).
  wireFolds();
  wireFolderLinks(folderSectionKeys);
  wireSummarySettings();
}
export function refreshAfterRecordChange(message) {
  return API.detail(Store.projectId)
    .then(function (detail) {
      var currentTaskId = Store.task && Store.task.task_id;
      // Item A: an auto-save lands here mid-typing. Snapshot the focused form
      // control before the re-render replaces it, restore it after -- a no-op
      // for every non-form flow (transition, rename, priority) that also
      // refreshes through here.
      var focusSnapshot = captureEditorFocus();
      Store.project = detail.project || {};
      Store.tasks = detail.tasks || [];
      Store.allFields = detail.fields || {};
      Store.leadSummary = detail.lead_summary || null;
      Store.overview = detail.overview || null;
      Store.formations = detail.formations || [];
      renderDetail();
      var nextTask = Store.tasks.find(function (task) { return task.task_id === currentTaskId; }) ||
        chooseInitialTask(tasksForPipeline(Store.pipeline));
      return Promise.resolve(loadComponent(nextTask)).then(function () {
        restoreEditorFocus(focusSnapshot);
        refreshAllBoards();
        if (message) msg(message, 'success');
      });
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
    title: 'Delete ' + recordKind,
    message: 'Delete ' + recordKind.toLowerCase() + ' "' + name + '"? Its components, saved inputs, and audit trail will be preserved.',
    confirmLabel: 'Delete',
    danger: true
  });
  if (!confirmed) return;
  API.deleteProject(Store.projectId).then(function () {
    resetSelection();
    byId('detail-shell').classList.add('hidden');
    refreshAllBoards();
    refreshAudit();
    msg(recordKind + ' deleted.', 'success');
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
