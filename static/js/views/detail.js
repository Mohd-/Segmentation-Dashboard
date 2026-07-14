import { byId, all, esc, isFilled, msg } from '../dom.js';
import { API } from '../api.js';
import { currentUserName, Store, resetSelection } from '../state.js';
import { BP_STAGES, PROSPECT_STAGES, DONE, SEISMIC_BLOCKS, FLOWBACK_RATE_FIELDS } from '../schema.js';
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
    return { pct: String(row.reservoir_cos_pct), ref: parts.join(' · ') };
  }
  return { pct: '', ref: '' };
}
// Legacy one-string form ("Block · AR n: NN%"), kept for back-compat callers.
export function reservoirCosSummary(fieldMap) {
  var primary = reservoirCosPrimary(fieldMap);
  if (!isFilled(primary.pct)) return '';
  return (primary.ref ? primary.ref + ': ' : '') + primary.pct + '%';
}
// First filled value across a [taskName, fieldKey] precedence list (newest
// assessment first). Drives Mean Gas (lead) and Mean Post-Drill (well) — both
// read the `_gas_mean` keys, differing only in how far down LATEST_PIIP_SOURCES
// they look.
function firstFilledField(sources) {
  for (var i = 0; i < sources.length; i += 1) {
    var value = (Store.allFields[sources[i][0]] || {})[sources[i][1]];
    if (isFilled(value)) return value;
  }
  return '';
}

// Source-consistent P90/Mean/P10 gas trio. Picks the newest source step (same
// LATEST_PIIP_SOURCES precedence used for the mean) whose `_gas_mean` is filled,
// then reads that SAME step's p90/mean/p10 so the trio never mixes assessments.
// `sources` is a [taskName, '<prefix>_gas_mean'] list; the trio keys share the
// prefix. Returns empty strings when no source has a filled mean.
function gasTrio(sources) {
  for (var i = 0; i < sources.length; i += 1) {
    var fields = Store.allFields[sources[i][0]] || {};
    var meanKey = sources[i][1];
    if (!isFilled(fields[meanKey])) continue;
    var prefix = meanKey.replace(/_gas_mean$/, '');
    return { p90: fields[prefix + '_gas_p90'], mean: fields[meanKey], p10: fields[prefix + '_gas_p10'] };
  }
  return { p90: '', mean: '', p10: '' };
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
  if (isFilled(row.thickness_ft)) bits.push(row.thickness_ft + ' ft');
  if (isFilled(row.porosity_pct)) bits.push(row.porosity_pct + '% φ');
  if (isFilled(row.swt_pct)) bits.push(row.swt_pct + '% Sw');
  if (isFilled(row.pay_ft)) bits.push(row.pay_ft + ' ft pay');
  var fluidTag = isFilled(row.fluid) ? '<span class="summary-formation-fluid">' + esc(row.fluid) + '</span>' : '';
  return '<div class="summary-formation"><span class="summary-formation-name">' + esc(name) + '</span><span class="summary-formation-metrics">' + esc(bits.join(' · ')) + '</span>' + fluidTag + '</div>';
}

// Prediction-vs-Actual accordion open state. Module-level so it survives the
// re-render after each save; reset when the selected project changes (mirrors
// the rail-stage accordion's per-project guard).
var pvaOpen = false;
var pvaProjectId = null;

function numOrNull(value) {
  if (!isFilled(value)) return null;
  var n = Number(value);
  return isNaN(n) ? null : n;
}
// One predicted|actual comparison row. Δ appears only when both sides parse as
// finite numbers (the Top-SARH predicted value is free text and often won't).
function pvaRow(label, predicted, actual) {
  var predHtml = isFilled(predicted) ? esc(predicted) : '—';
  var actualHtml = isFilled(actual) ? esc(actual) : '—';
  var pn = numOrNull(predicted), an = numOrNull(actual);
  var deltaHtml = '<span class="summary-pva-delta"></span>';
  if (pn !== null && an !== null) {
    var delta = Math.round((an - pn) * 100) / 100;
    deltaHtml = '<span class="summary-pva-delta ' + (delta >= 0 ? 'pos' : 'neg') + '">Δ ' + (delta > 0 ? '+' : '') + delta + '</span>';
  }
  return '<div class="summary-pva-row"><span class="summary-pva-label">' + esc(label) + '</span>' +
    '<span class="summary-pva-cell">' + predHtml + '</span>' +
    '<span class="summary-pva-cell">' + actualHtml + '</span>' + deltaHtml + '</div>';
}

// One compact metric row: label (+ optional source note) and its value (— when
// blank). Kept tiny so the summary card stays far denser than the old tiles.
function metricRow(label, value, note) {
  var small = note ? '<small>' + esc(note) + '</small>' : '';
  return '<div class="summary-metric"><div class="summary-metric-label"><span>' + esc(label) + '</span>' + small + '</div><div class="summary-metric-value">' + (isFilled(value) ? esc(value) : '—') + '</div></div>';
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
      '<span class="summary-cluster-val">' + (isFilled(col.value) ? esc(col.value) : '—') + '</span></div>';
  }).join('');
  var contextHtml = isFilled(context) ? '<div class="summary-cluster-context">' + esc(context) + '</div>' : '';
  return '<div class="summary-cluster"><div class="summary-cluster-label">' + esc(label) + '</div>' +
    '<div class="summary-cluster-grid" style="grid-template-columns:repeat(' + cols.length + ',minmax(0,1fr))">' + cells + '</div>' +
    contextHtml + '</div>';
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
  // Phase-specific body: leads show volumetrics + chance-of-success; drilled BP
  // wells show post-drill results per formation plus a predicted-vs-actual
  // comparison against the frozen lead snapshot.
  var bodyHtml;
  if (isBP) {
    // ---- Well card ----------------------------------------------------------
    var deduped = dedupeFormationsByPhase();
    var sarh = deduped['SARH'] ? deduped['SARH'].row : null;
    // Post-Drill Gas: source-consistent P90/Mean/P10 from resource_update else
    // post_drill (the drilled results only). Mean feeds Prediction vs Actual.
    var postDrillTrio = gasTrio(LATEST_PIIP_SOURCES.slice(0, 2));
    var meanPostDrill = postDrillTrio.mean;
    var prognosis = (Store.allFields['Well Proposal'] || {}).sarh_formation_prognosis_pre_drill;
    var topSarh = sarh ? sarh.top_tvdss_ft : '';
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
    var fluid = firstFilledField([['Final Log Analysis', 'final_fluid_type'], ['Resource Assessment Update', 'resource_update_fluid_type'], ['Post-Drilling Resource Assessment', 'post_drill_fluid_type'], ['Quicklook Logs Interpretation', 'quicklook_fluid_type']]);
    var flowEntry = FLOWBACK_RATE_FIELDS[fluid] || FLOWBACK_RATE_FIELDS['Gas'];
    var flowValue = (Store.allFields['Flowback Results'] || {})[flowEntry.key];
    var flowbackHtml = '<div class="summary-metrics summary-section">' +
      metricRow('Flowback Rate', isFilled(flowValue) ? flowValue + ' ' + flowEntry.unit : '', isFilled(fluid) ? fluid : 'Gas') +
      '</div>';

    // Prediction vs Actual: predicted values read the frozen lead snapshot,
    // falling back to live fields where the plan allows.
    if (Store.projectId !== pvaProjectId) { pvaOpen = false; pvaProjectId = Store.projectId; }
    var snap = (Store.leadSummary && Store.leadSummary.fields) || {};
    var predThickness = (snap['Thickness Estimation'] || {}).reservoir_thickness_ft;
    if (!isFilled(predThickness)) predThickness = (Store.allFields['Thickness Estimation'] || {}).reservoir_thickness_ft;
    var predMeanSources = [['Pre-Drilling Resource Assessment', 'pre_drill_piip_gas_mean'], ['Lead Resource Assessment', 'lead_piip_gas_mean']];
    var predMean = '';
    for (var si = 0; si < predMeanSources.length && !isFilled(predMean); si += 1) predMean = (snap[predMeanSources[si][0]] || {})[predMeanSources[si][1]] || '';
    for (var li = 0; li < predMeanSources.length && !isFilled(predMean); li += 1) predMean = (Store.allFields[predMeanSources[li][0]] || {})[predMeanSources[li][1]] || '';
    var pvaBodyHtml =
      '<div class="summary-pva-head-row"><span class="summary-pva-label"></span><span class="summary-pva-cell summary-pva-colhead">Predicted</span><span class="summary-pva-cell summary-pva-colhead">Actual</span><span class="summary-pva-delta"></span></div>' +
      pvaRow('Top SARH', prognosis, topSarh) +
      pvaRow('Thickness (ft)', predThickness, sarh ? sarh.thickness_ft : '') +
      pvaRow('Mean (BCF)', predMean, meanPostDrill);
    var pvaHtml = '<div class="summary-pva">' +
      '<button id="summary-pva-head" type="button" class="summary-pva-head' + (pvaOpen ? ' open' : '') + '" aria-expanded="' + pvaOpen + '"><span class="summary-pva-title">Prediction vs Actual</span><span class="summary-pva-chevron" aria-hidden="true"></span></button>' +
      '<div id="summary-pva-body" class="summary-pva-body' + (pvaOpen ? '' : ' collapsed') + '">' + pvaBodyHtml + '</div></div>';

    bodyHtml = metricsHtml + reservoirsHtml + flowbackHtml + pvaHtml;
  } else {
    // ---- Lead card ----------------------------------------------------------
    var trapCos = (Store.allFields['Trap CoS'] || {}).trap_cos_pct;
    var sealCos = (Store.allFields['Seal CoS'] || {}).seal_cos_pct;
    var leadGasTrio = gasTrio(LATEST_PIIP_SOURCES);
    // Res CoS is the primary first-row percent; its "Block · AR n" reference
    // rides along as the cluster's quiet context line (no bulky <small> label).
    var resCos = reservoirCosPrimary();
    bodyHtml = '<div class="summary-metrics">' +
      statCluster('Gas (BCF)', [
        { label: 'P90', value: leadGasTrio.p90 },
        { label: 'Mean', value: leadGasTrio.mean },
        { label: 'P10', value: leadGasTrio.p10 }
      ]) +
      metricRow('Reservoir Thickness (ft)', (Store.allFields['Thickness Estimation'] || {}).reservoir_thickness_ft) +
      statCluster('Chance of Success (%)', [
        { label: 'Res', value: resCos.pct },
        { label: 'Trap', value: trapCos },
        { label: 'Seal', value: sealCos },
        { label: 'Total', value: (Store.overview || {}).derisking }
      ], resCos.ref) +
      '</div>';
  }
  // Popover: what the compact card dropped but still needs a home. Phase moves
  // (promote/recall) live on the visible phase row, not here.
  var popoverHtml =
    '<div id="summary-settings" class="summary-popover hidden" role="dialog" aria-label="Manage ' + recordKind.toLowerCase() + '">' +
    '<label class="summary-popover-check"><input id="summary-active-flag" type="checkbox" ' + (isActive ? 'checked' : '') + '> Active Well</label>' +
    '<div class="summary-popover-actions"><button id="rename-record" type="button" class="ghost">Rename ' + recordKind + '</button><button id="delete-record" type="button" class="danger">Archive ' + recordKind + '</button></div></div>';

  byId('summary-title').textContent = recordKind + ' Summary';
  byId('lead-summary').innerHTML = progressHtml + phaseHtml + bodyHtml + popoverHtml;

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
  // Prediction-vs-Actual accordion (well card only; byId is null otherwise).
  // Toggle in place so the surrounding card isn't re-rendered.
  var pvaHead = byId('summary-pva-head');
  if (pvaHead) pvaHead.addEventListener('click', function () {
    pvaOpen = !pvaOpen;
    pvaProjectId = Store.projectId;
    pvaHead.classList.toggle('open', pvaOpen);
    pvaHead.setAttribute('aria-expanded', String(pvaOpen));
    var body = byId('summary-pva-body');
    if (body) body.classList.toggle('collapsed', !pvaOpen);
  });
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
