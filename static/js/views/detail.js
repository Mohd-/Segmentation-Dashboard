import { byId, all, esc, isFilled, truthy, msg, fmtNum } from '../dom.js';
import { ICONS } from '../icons.js';
import { API } from '../api.js';
import { currentUserName, currentProjectPipeline, isCurrentPipelineView, Store, resetSelection } from '../state.js';
import { activateTab } from '../navigation.js';
import { BP_STAGES, PROSPECT_STAGES, DONE, SEISMIC_BLOCKS, FLOWBACK_RATE_FIELDS } from '../schema.js';
import { confirmDialog, promptDialog } from '../dialog.js';
import { canTransitionPhase, promoteProject, recallProject } from './transitions.js';
import { loadComponent, LATEST_PIIP_SOURCES, POST_DRILL_PIIP_SOURCES, LEAD_PIIP_SOURCES, copyText } from './detail-form.js';
import { refreshAllBoards } from './pipeline.js';
import { refreshAudit } from './audit.js';
// Card 2A: the shared Lead Summary block. It is PURE -- this module resolves
// every value out of Store and hands it one plain object (see leadSummaryData).
import { leadSummaryHtml, wireLeadSummary, closeLeadSummaryMenu } from './lead-summary.js';
// The board's own completion arithmetic, imported rather than re-derived: the
// Lead Summary progress bar and the board KPI donut must read one formula over
// one dataset (the lead's 12 tracked items).
import { completedItemCount, TRACKED_ITEM_COUNT } from './lead-kpis.js';

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

/* The stored stage group -> the board's three DISPLAY stages. TRANSITIONAL: an
   exact mirror of workflow/projects.py's _DISPLAY_STAGE_BY_STAGE (the Card 1B
   presentation adapter), needed here only to place a step under the right
   sidebar heading; it disappears with the same permanent step migration that
   deletes the server-side adapter. BP stage groups are absent on purpose. */
var LEAD_DISPLAY_STAGE_BY_STAGE = {
  'Lead Identification': 'Lead Assessment',
  'Risking': 'Risk Analysis',
  'Segmentation': 'Risk Analysis',
  'Pre-Well Delivery': 'Pre-Well Delivery'
};
// Same glyphs the board columns use, so a stage reads identically on both
// surfaces.
var LEAD_STAGE_ICONS = {
  'Lead Assessment': 'clipboard-check',
  'Risk Analysis': 'gauge',
  'Pre-Well Delivery': 'rig'
};

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
    // The detail shell is the page's last visible section, so aligning its
    // TOP under the sticky header is often unreachable (not enough document
    // below it) and the scroll stalls partway. Scroll to the document bottom
    // instead: the shell fills the viewport from below. loadComponent fills
    // the form fields ASYNCHRONOUSLY (fetches fields + folder info), which
    // grows the document after the scroll target would otherwise be computed
    // -- so wait for that render to settle (Promise.resolve tolerates
    // loadComponent returning undefined when there's no task) and scroll on
    // the next frame, once the grown document's height is final.
    Promise.resolve(loadComponent(chooseInitialTask(tasksForPipeline(Store.pipeline)))).then(function () {
      requestAnimationFrame(function () {
        window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'smooth' });
      });
    });
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
  // The lead sidebar's headings are the three DISPLAY stages, not the stored
  // stage groups, so the key has to be translated there (see
  // LEAD_DISPLAY_STAGE_BY_STAGE). The BP rail is keyed by the stored group.
  openStage = isLeadView()
    ? (LEAD_DISPLAY_STAGE_BY_STAGE[task.stage_group] || task.stage_group)
    : task.stage_group;
  openStageProjectId = Store.projectId;
  syncStageOpenState();
  syncActiveStage();
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

   Under an expanded stage sit that stage's REAL steps, regrouped under the
   three headings -- this is presentation only, the stored 12-step prospect
   pipeline is untouched. Three transitional details, all of which disappear
   with the permanent step migration:
     * a tracked item maps to its source steps via the `steps` list the server
       now sends with each item, so the mapping is never duplicated here;
     * "Trap and Seal" therefore renders as its TWO real steps (Trap CoS,
       Seal CoS) -- both must stay reachable;
     * "GRV Inputs" and "Well Site Location" have no backing step at all yet
       and render as dimmed, non-clickable rows.
   Any prospect step no tracked item references (today: "Well Creation") is
   appended to its own display stage, so regrouping can never hide a step.
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
    var sources = item.steps || [];
    if (!sources.length) {
      // No stored step behind this item yet -- a dimmed placeholder, never a
      // click target and never counted as work that can be opened.
      group.rows.push({ label: item.label, task: null });
      return;
    }
    sources.forEach(function (name) {
      var task = taskByName[name];
      // A source the project does not carry (a legacy/retired row) still shows
      // its name rather than vanishing, dimmed like the not-yet-built items.
      group.rows.push({ label: name, task: task || null });
      if (task) task._leadRailPlaced = true;
    });
  });
  // Steps no tracked item references (e.g. "Well Creation") keep their place in
  // the workflow: appended to whichever display stage their stored group maps
  // to, so the regrouped sidebar never loses a real step.
  (tasks || []).forEach(function (task) {
    if (task._leadRailPlaced) { delete task._leadRailPlaced; return; }
    var stage = LEAD_DISPLAY_STAGE_BY_STAGE[task.stage_group] || task.stage_group;
    if (!byStage[stage]) { byStage[stage] = { stage: stage, done: 0, total: 0, rows: [] }; order.push(stage); }
    byStage[stage].rows.push({ label: task.task_name, task: task });
  });
  return order.map(function (stage) { return byStage[stage]; });
}

function leadRailRowHtml(row) {
  if (!row.task) {
    return '<div class="component-item component-item-future"' +
      ' title="(coming with a later step migration)" aria-disabled="true">' +
      '<span class="component-num" aria-hidden="true">·</span><b>' + esc(row.label) + '</b></div>';
  }
  var slug = String(row.task.status || 'Not Assigned').toLowerCase().replace(/\s+/g, '-');
  return '<button type="button" class="component-item status-' + slug + '" data-task-id="' + row.task.task_id + '">' +
    '<span class="component-num">' + esc(row.task.sequence_no) + '</span><b>' + esc(row.label) + '</b></button>';
}

function renderLeadRail(tasks) {
  var groups = leadStageGroups((Store.project || {}).tracked_items, tasks);
  byId('component-list').innerHTML = groups.map(function (group) {
    var isOpen = group.stage === openStage;
    var icon = ICONS[LEAD_STAGE_ICONS[group.stage]] || '';
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
  byId('detail-name').textContent = Store.project.project_name || 'Lead / Well';
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
      '<span class="stage-icon" aria-hidden="true">' + (STAGE_ICONS[group.stage] || '•') + '</span>' +
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

// Collapse Store.formations to one row per formation name, each taken at its
// highest-precedence phase. Names compare upper-cased (custom names are stored
// upper-cased; the canonical trio already is). Returns { NAME: {row, rank} }.
function dedupeFormationsByPhase() {
  var byName = {};
  (Store.formations || []).forEach(function (row) {
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
// SARH's fluid at a specific phase, read straight from Store.formations (not the
// newest-phase dedupe): the well's fluid ladder blends these per-phase SARH
// fluids with the legacy step-level *_fluid_type fields.
function sarhFluidAtPhase(phase) {
  var rows = (Store.formations || []).filter(function (row) {
    return String(row.formation || '').trim().toUpperCase() === 'SARH' && row.phase === phase;
  });
  for (var i = 0; i < rows.length; i += 1) { if (isFilled(rows[i].fluid)) return rows[i].fluid; }
  return '';
}
// "tight" is DERIVED, never a default: the formation row must EXIST (it was
// penetrated/logged in a BP step) AND read as non-pay — fluid 'Dry', or a blank
// fluid with zero pay. A missing row (no BP data) is NOT tight; it renders as a
// dash. Generic across formations so any barren reservoir can read "tight".
function formationIsTight(row) {
  if (!row) return false;
  var fluid = String(row.fluid || '').trim();
  if (fluid === 'Dry') return true;
  return fluid === '' && (row.pay_ft === 0 || String(row.pay_ft).trim() === '0');
}
// One compact reservoir line: formation name + its filled metrics (thickness,
// porosity, Sw, pay) and a fluid tag when present. A row that reads as non-pay
// renders "<name>: tight"; a missing/empty row renders an em dash (—) — SARH
// defaults to a dash, not "tight", unless the data derives tight.
function formationLine(name, row) {
  var tight = formationIsTight(row);
  if (tight || !formationHasData(row)) {
    return '<div class="summary-formation summary-formation-empty"><span class="summary-formation-name">' + esc(name) + '</span><span class="summary-formation-note">' + (tight ? 'tight' : '—') + '</span></div>';
  }
  var bits = [];
  if (isFilled(row.thickness_ft)) bits.push(fmtNum(row.thickness_ft) + ' ft');
  if (isFilled(row.porosity_pct)) bits.push(fmtNum(row.porosity_pct) + '% φ');
  if (isFilled(row.swt_pct)) bits.push(fmtNum(row.swt_pct) + '% Sw');
  if (isFilled(row.pay_ft)) bits.push(fmtNum(row.pay_ft) + ' ft pay');
  var fluidTag = isFilled(row.fluid) ? '<span class="summary-formation-fluid">' + esc(row.fluid) + '</span>' : '';
  return '<div class="summary-formation"><span class="summary-formation-name">' + esc(name) + '</span><span class="summary-formation-metrics">' + esc(bits.join(' · ')) + '</span>' + fluidTag + '</div>';
}

// Well-card fold open state, keyed by fold id ('pva', 'lead'). Module-level so
// it survives the re-render after each save; reset when the selected project
// changes (mirrors the rail-stage accordion's per-project guard).
var openFolds = {};
var foldProjectId = null;

// One collapsible section of the well card: chevron header + collapsed body.
// `id` is the fold key; the rendered ids are summary-fold-<id>[-body], which
// wireFolds() binds after the card is written.
function foldSection(id, title, bodyHtml) {
  var isOpen = !!openFolds[id];
  return '<div class="summary-fold">' +
    '<button id="summary-fold-' + id + '" type="button" class="summary-fold-head' + (isOpen ? ' open' : '') +
    '" data-fold="' + id + '" aria-expanded="' + isOpen + '" aria-controls="summary-fold-' + id + '-body">' +
    '<span class="summary-fold-title">' + esc(title) + '</span>' +
    '<span class="summary-fold-chevron" aria-hidden="true"></span></button>' +
    '<div id="summary-fold-' + id + '-body" class="summary-fold-body' + (isOpen ? '' : ' collapsed') + '">' + bodyHtml + '</div></div>';
}
// Toggle each rendered fold in place so the surrounding card isn't re-rendered.
function wireFolds() {
  all('.summary-fold-head').forEach(function (head) {
    head.addEventListener('click', function () {
      var id = head.getAttribute('data-fold');
      var isOpen = !openFolds[id];
      openFolds[id] = isOpen;
      foldProjectId = Store.projectId;
      head.classList.toggle('open', isOpen);
      head.setAttribute('aria-expanded', String(isOpen));
      var body = byId('summary-fold-' + id + '-body');
      if (body) body.classList.toggle('collapsed', !isOpen);
    });
  });
}

// Folders fold: one row per WELL_OVERVIEW_DIRECTORY_MAP section key (see
// folders.get_section_folder_link). Rendered synchronously as a loading
// placeholder -- the summary card itself never waits on the network -- then
// filled in by wireFolderLinks() once each lazy fetch resolves. Mirrors the
// component-folder card's glyph/path/copy-button markup (renderComponentFolder
// in detail-form.js) so both folder-link styles read as one pattern.
function folderRowHtml(sectionKey) {
  return '<div class="folder-card" data-folder-key="' + esc(sectionKey) + '">' +
    '<span class="folder-glyph" aria-hidden="true">📁</span>' +
    '<span class="folder-path" id="summary-folder-path-' + esc(sectionKey) + '">Loading…</span>' +
    '<button type="button" class="icon-btn" id="summary-folder-copy-' + esc(sectionKey) +
    '" title="Copy folder link" aria-label="Copy folder link" disabled>⧉</button></div>';
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

// A stat cluster: a quiet group label over a row of sub-label/value columns
// (sub-label sits above its value, columns aligned). `cols` is a list of
// { label, value }; empty values render as — in place, so a cluster with no
// values at all still renders once with all dashes (it is never hidden). An
// optional `context` line (e.g. the Reservoir CoS "Block · AR n" reference)
// sits quietly below the grid. Numbers read larger than the sub-labels.
function statCluster(label, cols, context) {
  var cells = cols.map(function (col) {
    return '<div class="summary-cluster-col"><span class="summary-cluster-sub">' + esc(col.label) + '</span>' +
      '<span class="summary-cluster-val">' + (isFilled(col.value) ? esc(fmtNum(col.value)) : '—') + '</span></div>';
  }).join('');
  var contextHtml = isFilled(context) ? '<div class="summary-cluster-context">' + esc(context) + '</div>' : '';
  return '<div class="summary-cluster"><div class="summary-cluster-label">' + esc(label) + '</div>' +
    '<div class="summary-cluster-grid" style="grid-template-columns:repeat(' + cols.length + ',minmax(0,1fr))">' + cells + '</div>' +
    contextHtml + '</div>';
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
function leadFieldSource() {
  var frozen = (Store.leadSummary && Store.leadSummary.fields) || {};
  var live = Store.allFields || {};
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
// /detail's overview (derived at read time from the still-active lead CoS
// steps), never recomputed here.
function leadMetricsHtml(fieldMap, gasSources) {
  var resCos = reservoirCosPrimary(fieldMap);
  var trio = gasTrio(gasSources, fieldMap);
  return '<div class="summary-metrics">' +
    statCluster('Gas (BCF)', [
      { label: 'P90', value: trio.p90 },
      { label: 'Mean', value: trio.mean },
      { label: 'P10', value: trio.p10 }
    ]) +
    metricRow('Reservoir Thickness (ft)', (fieldMap['Thickness Estimation'] || {}).reservoir_thickness_ft) +
    statCluster('Chance of Success (%)', [
      { label: 'Res', value: resCos.pct },
      { label: 'Trap', value: (fieldMap['Trap CoS'] || {}).trap_cos_pct },
      { label: 'Seal', value: (fieldMap['Seal CoS'] || {}).seal_cos_pct },
      { label: 'Total', value: (Store.overview || {}).derisking }
    ], resCos.ref) +
    '</div>';
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
  var thickness = fields['Thickness Estimation'] || {};
  var area = fields['Reservoir Area Definition'] || {};
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
    area: { p90: area.p90_area_km2, p10: area.p10_area_km2 },
    cos: {
      reservoir: resCos.pct,
      trap: (fields['Trap CoS'] || {}).trap_cos_pct,
      seal: (fields['Seal CoS'] || {}).seal_cos_pct,
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
  var phaseHtml = '<div class="summary-phase"><span class="summary-phase-label">' +
    (isBP ? 'BP Well · ' + esc(Store.project.business_plan_year || year) : 'Lead') +
    '</span></div>';
  // Phase-specific body: leads show volumetrics + chance-of-success; drilled BP
  // wells show post-drill results per formation plus a predicted-vs-actual
  // comparison against the frozen lead snapshot.
  var bodyHtml;
  if (viewingBP) {
    // ---- Well card ----------------------------------------------------------
    var deduped = dedupeFormationsByPhase();
    var sarh = deduped['SARH'] ? deduped['SARH'].row : null;
    // Post-Drill Gas: source-consistent P90/Mean/P10 from resource_update else
    // post_drill (the drilled results only). Mean feeds Prediction vs Actual.
    var postDrillTrio = gasTrio(POST_DRILL_PIIP_SOURCES);
    var meanPostDrill = postDrillTrio.mean;
    var prognosis = (Store.allFields['Well Proposal'] || {}).sarh_formation_prognosis_pre_drill;
    // SARH top prefers the formation row; legacy wells stored the top at step
    // level (final then quicklook), so fall back there before blanking.
    var topSarh = sarh ? sarh.top_tvdss_ft : '';
    if (!isFilled(topSarh)) {
      topSarh = firstFilledValue([
        (Store.allFields['Final Log Analysis'] || {}).final_top_reservoir_tvdss_ft,
        (Store.allFields['Quicklook Logs'] || {}).quicklook_top_reservoir_tvdss_ft
      ]);
    }
    var metricsHtml = '<div class="summary-metrics">' +
      statCluster('Post-Drill Gas (BCF)', [
        { label: 'P90', value: postDrillTrio.p90 },
        { label: 'Mean', value: postDrillTrio.mean },
        { label: 'P10', value: postDrillTrio.p10 }
      ]) +
      metricRow('SARH Prognosis', prognosis) +
      metricRow('Top SARH (ft TVDSS)', topSarh) +
      '</div>';

    // Reservoirs: SARH always first (barren → "tight"); every other formation
    // with any data follows, custom/non-gas included (fluid tag distinguishes).
    var reservoirLines = [formationLine('SARH', sarh)];
    Object.keys(deduped).sort().forEach(function (name) {
      if (name === 'SARH' || !formationHasData(deduped[name].row)) return;
      reservoirLines.push(formationLine(name, deduped[name].row));
    });
    var reservoirsHtml = '<div class="summary-section"><div class="summary-section-title">Reservoirs</div>' +
      '<div class="summary-formations">' + reservoirLines.join('') + '</div></div>';

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
      sarhFluidAtPhase('final'),
      (Store.allFields['Final Log Analysis'] || {}).final_fluid_type,
      (Store.allFields['SAD Update'] || {}).resource_update_fluid_type,
      (Store.allFields['Resource Assessment Update'] || {}).resource_update_fluid_type,
      (Store.allFields['SAD Model'] || {}).post_drill_fluid_type,
      (Store.allFields['Post-Drilling Resource Assessment'] || {}).post_drill_fluid_type,
      sarhFluidAtPhase('quicklook'),
      (Store.allFields['Quicklook Logs'] || {}).quicklook_fluid_type
    ]);
    var flowEntry = FLOWBACK_RATE_FIELDS[fluid] || FLOWBACK_RATE_FIELDS['Gas'];
    // Primary flowback values are stage #1 -- the first non-empty row of the
    // Flowback Results stages mini-sheet (whose column keys reuse the retired
    // flat key names). Only when NO stage row exists does the read fall back
    // to the retired step-level flat key, so a stage row and legacy flat data
    // never mix (single-vintage rule, like the Reservoir CoS primary row).
    var flowbackFields = Store.allFields['Flowback Results'] || {};
    var primaryStage = null;
    parseRepeatableRows(flowbackFields.flowback_stages_rows || '[]').forEach(function (stage) {
      if (primaryStage || !stage) return;
      if (Object.keys(stage).some(function (key) { return isFilled(stage[key]); })) primaryStage = stage;
    });
    var flowValue = primaryStage ? primaryStage[flowEntry.key] : flowbackFields[flowEntry.key];
    var flowbackHtml = '<div class="summary-metrics summary-section">' +
      metricRow('Flowback Rate', isFilled(flowValue) ? fmtNum(flowValue) + ' ' + flowEntry.unit : '', isFilled(fluid) ? fluid : 'Gas') +
      '</div>';

    // Folds are per-project, like the rail accordion: a fresh selection starts
    // with every fold collapsed.
    if (Store.projectId !== foldProjectId) { openFolds = {}; foldProjectId = Store.projectId; }

    // Prediction vs Actual: predicted values read the frozen lead snapshot,
    // falling back to live fields where the plan allows.
    var snap = (Store.leadSummary && Store.leadSummary.fields) || {};
    var predThickness = (snap['Thickness Estimation'] || {}).reservoir_thickness_ft;
    if (!isFilled(predThickness)) predThickness = (Store.allFields['Thickness Estimation'] || {}).reservoir_thickness_ft;
    var predMean = '';
    for (var si = 0; si < LEAD_PIIP_SOURCES.length && !isFilled(predMean); si += 1) predMean = (snap[LEAD_PIIP_SOURCES[si][0]] || {})[LEAD_PIIP_SOURCES[si][1]] || '';
    for (var li = 0; li < LEAD_PIIP_SOURCES.length && !isFilled(predMean); li += 1) predMean = (Store.allFields[LEAD_PIIP_SOURCES[li][0]] || {})[LEAD_PIIP_SOURCES[li][1]] || '';
    var pvaHtml = foldSection('pva', 'Prediction vs Actual',
      '<div class="summary-pva-head-row"><span class="summary-pva-label"></span><span class="summary-pva-cell summary-pva-colhead">Predicted</span><span class="summary-pva-cell summary-pva-colhead">Actual</span><span class="summary-pva-delta"></span></div>' +
      pvaRow('Top SARH', prognosis, topSarh) +
      pvaRow('Thickness (ft)', predThickness, sarh ? sarh.thickness_ft : '') +
      pvaRow('Mean (BCF)', predMean, meanPostDrill));

    // Lead Summary: the same card the lead phase shows, over the frozen
    // snapshot (see leadFieldSource) and the lead's own gas sources -- the
    // volumetrics and chance-of-success this well was drilled on. Kept folded
    // so the well card still opens on its post-drill results.
    var captured = Store.leadSummary && Store.leadSummary.captured_at;
    var capturedNote = captured
      ? '<div class="summary-fold-note">Captured at promotion · ' + esc(String(captured).slice(0, 10)) +
        (Store.leadSummary.captured_by ? ' · ' + esc(Store.leadSummary.captured_by) : '') + '</div>'
      : '';
    var leadHtml = foldSection('lead', 'Lead Summary',
      leadMetricsHtml(leadFieldSource(), LEAD_PIIP_SOURCES) + capturedNote);

    // Folder links: the well's own shared folders (Well/MTR/PDA), not the
    // lead's -- those already live inside the folded Lead Summary above via
    // the pipeline itself, and get_section_folder_link resolves them from
    // config.WELL_OVERVIEW_DIRECTORY_MAP by these same keys.
    var folderSectionKeys = ['well', 'mtr', 'pda'];
    var foldersFoldHtml = foldSection('folders', 'Folders', foldersHtml(folderSectionKeys));

    bodyHtml = metricsHtml + reservoirsHtml + flowbackHtml + pvaHtml + leadHtml + foldersFoldHtml;
  } else {
    // ---- Lead card ----------------------------------------------------------
    // Res CoS is the primary first-row percent; its "Block · AR n" reference
    // rides along as the cluster's quiet context line (no bulky <small> label).
    var folderSectionKeys = ['lead'];
    var foldersFoldHtml = foldSection('folders', 'Folders', foldersHtml(folderSectionKeys));
    bodyHtml = leadMetricsHtml(Store.allFields, LATEST_PIIP_SOURCES) + foldersFoldHtml;
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
    relocatedHtml =
      '<label class="summary-popover-check"><input id="summary-active-flag" type="checkbox" ' +
      (isActive ? 'checked' : '') + '> Active Well</label>' + phaseButtonHtml;
  }
  var popoverHtml =
    '<div id="summary-settings" class="summary-popover hidden" role="dialog" aria-label="Manage ' + recordKind.toLowerCase() + '">' +
    relocatedHtml +
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
