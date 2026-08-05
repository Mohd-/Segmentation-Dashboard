import { byId, all, esc, compact, fmtNum, isFilled, msg, truthy } from '../dom.js';
import { ICONS } from '../icons.js';
import { API } from '../api.js';
import { Store, currentRole, currentUserName } from '../state.js';
import { confirmDialog } from '../dialog.js';
import {
  placeFilterMenu, filterTriggerHtml, filterOptionHtml,
  kpiDonutHtml, kpiTileHtml, personChipsHtml, leadItemHtml,
  priorityChipHtml, applyPriorityChip, nextLeadPriority
} from './board-widgets.js';
import { DROPDOWN_OPEN_EVENT } from './lead-filters.js';
// One numeric rule set for the whole app; see bpeNumericError for the one
// argument this form passes differently.
import { numericFieldError, PDF_LABEL } from '../schema.js';
// The Well Summary wears the Lead Summary card's chrome, and borrows its one
// "no value recorded" glyph so the two panels cannot disagree about how an
// empty fact looks.
import { EM_DASH } from './lead-summary.js';

var FLUIDS = ['Gas', 'Gas over Water', 'Water Bearing', 'Dry Hole', 'Oil', 'Oil over Gas', 'Oil over Water'];
var STAGE_META = [
  // The multi-step clipboard, so the BP stage reads apart from the maturation
  // board's Lead Assessment clipboard at a glance.
  { key: 'pre_drilling', label: 'Pre-Drilling', icon: 'clipboard-steps' },
  { key: 'post_drilling', label: 'Post-Drilling', icon: 'rig' },
  { key: 'post_testing', label: 'Post-Testing', icon: 'gauge' }
];
var CONDITIONAL_FIELDS = {
  bp_gate_classification: true,
  bp_gate_logging_program: true,
  bp_gate_coring_program: true,
  gheer_vsp_required: true,
  post_drill_piip_has_liquid: true,
  resource_update_has_liquid: true,
  reserves_booking_response: true
};
var state = {
  initialized: false,
  dashboard: null,
  dashboardRequest: 0,
  detail: null,
  detailRequest: 0,
  projectId: null,
  detailSlug: null,
  saveQueue: Promise.resolve(),
  saveVersion: 0,
  saveDelay: 500,
  contextId: 0,
  fieldDrafts: {},
  structureDrafts: { formations: null, flowback: null },
  retryCommand: null,
  timers: {},
  // null = "follow the open step's stage"; a key = the user folded the rail by
  // hand; '' = they folded every stage away.
  railStage: null,
  users: null
};

function icon(name) { return ICONS[name] || '' ; }

function selectOptions(values, selected, placeholder) {
  var html = placeholder ? '<option value="">' + esc(placeholder) + '</option>' : '';
  return html + (values || []).map(function (value) {
    var item = typeof value === 'object' ? value : { value: value, label: value };
    return '<option value="' + esc(item.value) + '" ' + (String(item.value) === String(selected) ? 'selected' : '') + '>' +
      esc(item.label) + '</option>';
  }).join('');
}

function setSelect(id, values, selected) {
  var element = byId(id);
  if (!element) return;
  element.innerHTML = selectOptions(values, selected);
  element.value = String(selected);
}

function currentFilters() {
  return {
    assignee: (byId('bp-assignee-filter') && byId('bp-assignee-filter').value) || 'All Assignees',
    field: (byId('bp-field-filter') && byId('bp-field-filter').value) || 'All Fields',
    status: (byId('bp-status-filter') && byId('bp-status-filter').value) || 'All Status',
    year: (byId('bp-year-filter') && byId('bp-year-filter').value) || String(new Date().getFullYear()),
    step: (byId('bp-step-filter') && byId('bp-step-filter').value) || 'business-plan-gate'
  };
}

function initialize() {
  if (state.initialized) return;
  state.initialized = true;
  var years = [];
  for (var year = 1999; year <= 2035; year += 1) years.push(String(year));
  setSelect('bp-assignee-filter', ['All Assignees', 'Unassigned'], 'All Assignees');
  setSelect('bp-field-filter', ['All Fields'], 'All Fields');
  setSelect('bp-status-filter', ['All Status', 'Completed', 'Pending Approval', 'In Progress'], 'All Status');
  setSelect('bp-year-filter', years, String(new Date().getFullYear()));
  setSelect('bp-step-filter', [
    { value: 'all', label: 'All Steps' },
    { value: 'business-plan-gate', label: 'Business Plan Gate' }
  ], 'business-plan-gate');
  // Card BP1: this module owns all five filters -- populating them (above)
  // AND binding their change handler, so there is exactly one fill and one
  // listener per select, never main.js's boot() racing this initialize().
  ['bp-assignee-filter', 'bp-field-filter', 'bp-status-filter', 'bp-year-filter', 'bp-step-filter'].forEach(function (id) {
    var element = byId(id);
    if (element) element.addEventListener('change', refreshBusinessPlan);
  });
  // Paint the visible band from the defaults above, so the row shows its five
  // triggers from the first frame instead of a gap that fills in when the
  // dashboard fetch lands (views/lead-kpis.js initLeadKpis does the same).
  renderFilterTriggers();
}

/* =========================================================================
   Card R2 — the VISIBLE filter band.

   The five <select>s stay: they are the STATE STORE and the server contract
   (currentFilters() reads them, renderDashboard() repopulates them,
   syncBusinessPlanPromotion() writes them, and their own 'change' listener
   above is the single refresh path). This section only draws the maturation
   board's controls over them: a trigger per select, a menu of that select's
   own <option>s, and a Clear. Choosing an option writes the select and
   dispatches 'change' — so there is still exactly ONE refresh path, and a
   fixture without #bpe-filter-row keeps working untouched.
   ========================================================================= */

// THIS ARRAY IS THE VISIBLE LEFT-TO-RIGHT ORDER of the filter row; the hidden
// selects in index.html keep their own (now different) order, which is purely
// cosmetic because every lookup here goes through `data-bp-filter`. The step
// filter leads and the assignee filter closes the row, per the requested swap.
// `fallback` is the select's default value, i.e. the one that reads as NO
// filter -- the same literals currentFilters() falls back to and
// syncBusinessPlanPromotion() resets to (Year defaults to the current year
// instead, see defaultValue).
var BP_FILTERS = [
  // An initialism has to keep its spelling in the spoken label, or the trigger
  // announces itself as "Filter by bp gate".
  // `captionAtRest` is the maturation row's own rule (lead-filters triggerLabel):
  // a filter sitting on its default shows the CAPTION, because the option text
  // repeats it. Only this filter qualifies -- "All Assignees" still says more
  // than "Assignee", and the Year's default is a real selection, not the
  // absence of one.
  { key: 'step', id: 'bp-step-filter', caption: 'BP Gate', ariaCaption: 'BP Gate',
    fallback: 'business-plan-gate', captionAtRest: true },
  { key: 'field', id: 'bp-field-filter', caption: 'Field', fallback: 'All Fields' },
  { key: 'status', id: 'bp-status-filter', caption: 'Status', fallback: 'All Status' },
  { key: 'year', id: 'bp-year-filter', caption: 'Year', fallback: null },
  { key: 'assignee', id: 'bp-assignee-filter', caption: 'Assignee', fallback: 'All Assignees' }
];

// Status glyphs echo the card dots and the maturation menu exactly, so the
// two boards' menus speak one vocabulary. Everything else is glyph-less.
var BP_STATUS_ICONS = {
  'Completed': { icon: 'circle-check', slug: 'completed' },
  'Pending Approval': { icon: 'circle-minus', slug: 'pending' },
  'In Progress': { icon: 'circle', slug: 'in-progress' }
};

var BPE_FILTER_ROOT = 'bpe-filter-row';
var BPE_DROPDOWN_SOURCE = 'bpe';

function defaultValue(filter) {
  return filter.key === 'year' ? String(new Date().getFullYear()) : filter.fallback;
}

// The CLOSED control's text: the selected option's own label, so the row reads
// as a sentence of what the board is showing. A select whose options have not
// arrived yet falls back to its raw value, then to the caption.
function triggerLabel(filter, select) {
  if (filter.captionAtRest && String(select.value) === String(defaultValue(filter))) return filter.caption;
  var option = select.options[select.selectedIndex];
  return (option && option.text) || select.value || filter.caption;
}

function isFilterActive(filter, select) {
  return String(select.value) !== String(defaultValue(filter));
}

function anyFilterActive() {
  return BP_FILTERS.some(function (filter) {
    var select = byId(filter.id);
    return !!select && isFilterActive(filter, select);
  });
}

function filterGroupHtml(filter) {
  var select = byId(filter.id);
  if (!select) return '';
  return '<div class="lead-filter" data-bp-filter="' + filter.key + '">' +
    filterTriggerHtml({
      key: filter.key,
      caption: filter.caption,
      ariaCaption: filter.ariaCaption,
      label: triggerLabel(filter, select),
      active: isFilterActive(filter, select)
    }) +
    // Filled on OPEN, from the select's live options -- never a second copy of
    // the vocabulary that could drift from the one the server just sent.
    '<div class="lf-menu" hidden role="radiogroup" aria-label="' + esc(filter.caption) + '"></div>' +
    '</div>';
}

// Every filter here is single-select (the BP dashboard's server contract takes
// one value per category), so every mark is a radio dot.
function optionsMarkup(filter, select) {
  return all('option', select).map(function (option) {
    var glyph = filter.key === 'status' ? BP_STATUS_ICONS[option.value] : null;
    return filterOptionHtml({
      multi: false,
      chosen: String(option.value) === String(select.value),
      value: option.value,
      icon: glyph && glyph.icon,
      slug: glyph && glyph.slug,
      strong: false,
      label: option.text
    });
  }).join('');
}

/* -------------------------------------------------------------------------
   Placement + dismissal — the same contract views/lead-filters.js honors:
   a fixed-positioned menu placed against the viewport (the panel and the
   columns clip their overflow), one open dropdown page-wide, and dismissal
   that never changes a selection.
   ------------------------------------------------------------------------- */

function announceOpen() {
  document.dispatchEvent(new CustomEvent(DROPDOWN_OPEN_EVENT, {
    detail: { source: BPE_DROPDOWN_SOURCE }
  }));
}

/* WHICH menu is open is read off the DOM, never remembered in a variable.
   views/lead-filters.js closes every .lf-menu on the page (ours included) on
   any dismissal, and the header menus do the same through the shared event --
   so a remembered key goes stale behind our back and the next click on that
   trigger would "toggle closed" a menu that is already closed. The DOM cannot
   go stale about its own hidden attribute. */
function openBpeGroup() {
  var host = byId(BPE_FILTER_ROOT);
  if (!host) return null;
  return all('.lead-filter', host).filter(function (group) {
    var menu = group.querySelector('.lf-menu');
    return menu && !menu.hidden;
  })[0] || null;
}

// Scoped to this row: the maturation module keeps its own open state, so
// closing its menus is ITS job (our announce above is what asks it to).
function closeBpeMenus() {
  var host = byId(BPE_FILTER_ROOT);
  if (!host) return;
  all('.lf-menu', host).forEach(function (menu) { menu.hidden = true; });
  all('.lf-trigger', host).forEach(function (trigger) { trigger.setAttribute('aria-expanded', 'false'); });
}

function openBpeMenu(filter) {
  var host = byId(BPE_FILTER_ROOT);
  var group = host && host.querySelector('.lead-filter[data-bp-filter="' + filter.key + '"]');
  var select = byId(filter.id);
  if (!group || !select) return;
  closeBpeMenus();
  var trigger = group.querySelector('.lf-trigger');
  var menu = group.querySelector('.lf-menu');
  menu.innerHTML = optionsMarkup(filter, select);
  announceOpen();   // closes the maturation filters and the header's menus
  menu.hidden = false;
  placeFilterMenu(trigger, menu);
  trigger.setAttribute('aria-expanded', 'true');
}

// One registration for the lifetime of the page: the handlers query the DOM
// live, so they keep working across every rebuild of the row.
var bpeFilterDismissWired = false;
function wireBpeFilterDismiss() {
  if (bpeFilterDismissWired) return;
  bpeFilterDismissWired = true;
  document.addEventListener('click', function (event) {
    var target = event.target;
    if (!target || !target.closest) return;
    // A trigger toggles itself and an in-menu click is a choice; neither is a
    // dismissal. (Either board's controls -- ours close on their announce.)
    if (target.closest('.lf-trigger') || target.closest('.lf-menu')) return;
    closeBpeMenus();
  });
  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape') return;
    var group = openBpeGroup();
    if (!group) return;
    var trigger = group.querySelector('.lf-trigger');
    closeBpeMenus();
    if (trigger) trigger.focus();
  });
  // The other half of the one-dropdown-at-a-time contract.
  document.addEventListener(DROPDOWN_OPEN_EVENT, function (event) {
    if (!event.detail || event.detail.source === BPE_DROPDOWN_SOURCE) return;
    closeBpeMenus();
  });
  window.addEventListener('resize', closeBpeMenus);
  // Capture, so scrolling ANY ancestor dismisses a menu that is fixed-
  // positioned and would otherwise hang in the wrong place -- but NOT when the
  // scrolling element is the menu's own option list (37 Business Plan years).
  window.addEventListener('scroll', function (event) {
    var target = event.target;
    if (target && target.closest && target.closest('.lf-menu')) return;
    closeBpeMenus();
  }, true);
}

/* -------------------------------------------------------------------------
   Choosing
   ------------------------------------------------------------------------- */

// A choice is a WRITE TO THE SELECT plus its own 'change' -- the existing
// listener refreshes the dashboard, so the trigger row can never become a
// second, competing refresh path. Focus returns to the trigger: the chosen
// option is inside the menu we just hid, and a hidden element cannot hold it.
function chooseFilterOption(filter, value) {
  var select = byId(filter.id);
  var host = byId(BPE_FILTER_ROOT);
  var trigger = host && host.querySelector('.lead-filter[data-bp-filter="' + filter.key + '"] .lf-trigger');
  closeBpeMenus();
  if (select && String(select.value) !== String(value)) {
    select.value = String(value);
    select.dispatchEvent(new Event('change', { bubbles: true }));
  }
  syncBpTriggers();   // immediate feedback; the refresh repaints again
  if (trigger) trigger.focus();
}

// Clear resets all five and refreshes ONCE, rather than dispatching five
// 'change' events and firing five dashboard requests.
function clearBpFilters() {
  closeBpeMenus();
  var changed = false;
  BP_FILTERS.forEach(function (filter) {
    var select = byId(filter.id);
    if (!select || String(select.value) === String(defaultValue(filter))) return;
    select.value = String(defaultValue(filter));
    changed = true;
  });
  syncBpTriggers();
  if (changed) refreshBusinessPlan();
}

/* -------------------------------------------------------------------------
   Render

   TWO passes, the same split views/lead-filters.js makes:

     syncBpTriggers()        reflect the current selection onto the EXISTING
                             controls, in place
     renderFilterTriggers()  rebuild the row, for when the option sets change

   A pick only changes what a trigger SAYS, so it syncs. Rebuilding there would
   replace the element the user just activated, dropping keyboard focus to the
   body mid-interaction.
   ------------------------------------------------------------------------- */

function syncBpTriggers() {
  var host = byId(BPE_FILTER_ROOT);
  if (!host) return;
  BP_FILTERS.forEach(function (filter) {
    var group = host.querySelector('.lead-filter[data-bp-filter="' + filter.key + '"]');
    var select = byId(filter.id);
    if (!group || !select) return;
    var trigger = group.querySelector('.lf-trigger');
    trigger.querySelector('.lf-value').textContent = triggerLabel(filter, select);
    trigger.classList.toggle('is-active', isFilterActive(filter, select));
  });
  var clear = host.querySelector('.lf-clear');
  if (clear) clear.disabled = !anyFilterActive();
}

// Full rebuild of the row. The selection lives in the selects, so it survives
// every rebuild -- and the row is rebuilt whenever the server hands over a new
// set of options (initialize, renderDashboard), never on a pick.
function renderFilterTriggers() {
  var host = byId(BPE_FILTER_ROOT);
  if (!host) return;
  closeBpeMenus();
  host.innerHTML = BP_FILTERS.map(filterGroupHtml).join('') +
    '<button type="button" class="lf-clear ghost">Clear</button>';

  BP_FILTERS.forEach(function (filter) {
    var group = host.querySelector('.lead-filter[data-bp-filter="' + filter.key + '"]');
    if (!group) return;
    var menu = group.querySelector('.lf-menu');
    group.querySelector('.lf-trigger').addEventListener('click', function () {
      if (!menu.hidden) closeBpeMenus(); else openBpeMenu(filter);
    });
    // Delegated, because the options are built on open.
    menu.addEventListener('click', function (event) {
      var option = event.target && event.target.closest ? event.target.closest('.lf-option') : null;
      if (option) chooseFilterOption(filter, option.getAttribute('data-value'));
    });
  });
  var clear = host.querySelector('.lf-clear');
  clear.disabled = !anyFilterActive();
  clear.addEventListener('click', clearBpFilters);
  wireBpeFilterDismiss();
}

function showDashboard() {
  byId('bpe-main-view').classList.remove('hidden');
  byId('bpe-detail-view').classList.add('hidden');
  state.contextId += 1;
  state.detail = null;
  state.projectId = null;
  state.detailSlug = null;
}

function loadBusinessPlanDashboard() {
  if (!byId('bpe-main-view') || !byId('bpe-detail-view') || !byId('bp-pipeline')) {
    return Promise.resolve();
  }
  initialize();
  showDashboard();
  var requestId = ++state.dashboardRequest;
  var filters = currentFilters();
  byId('bp-pipeline').setAttribute('aria-busy', 'true');
  return API.businessPlanDashboard(filters).then(function (payload) {
    if (requestId !== state.dashboardRequest) return;
    state.dashboard = payload;
    renderDashboard(payload, filters);
  }).catch(function (error) {
    if (requestId === state.dashboardRequest) {
      byId('bp-pipeline').innerHTML = '<div class="empty-state">Business Plan Execution could not be loaded.</div>';
      msg(error.message, 'error');
    }
  }).finally(function () {
    if (requestId === state.dashboardRequest) byId('bp-pipeline').removeAttribute('aria-busy');
  });
}

export function refreshBusinessPlan() {
  if (!state.detail) return loadBusinessPlanDashboard();
  return flushPendingSaves().then(function (saved) {
    if (!saved) return null;
    return loadBusinessPlanDashboard();
  });
}

export function syncBusinessPlanPromotion(year) {
  var selectedYear = Number(year);
  if (!Number.isInteger(selectedYear) || selectedYear < 1999 || selectedYear > 2035) {
    return Promise.reject(new Error('Select a Business Plan year from 1999 to 2035.'));
  }
  initialize();
  var ready = state.detail ? flushPendingSaves() : Promise.resolve(true);
  return ready.then(function (saved) {
    if (!saved) return null;
    var defaults = {
      'bp-assignee-filter': 'All Assignees',
      'bp-field-filter': 'All Fields',
      'bp-status-filter': 'All Status',
      'bp-year-filter': String(selectedYear),
      'bp-step-filter': 'business-plan-gate'
    };
    Object.keys(defaults).forEach(function (id) {
      var element = byId(id);
      if (element) element.value = defaults[id];
    });
    return loadBusinessPlanDashboard();
  });
}

function renderDashboard(payload, selected) {
  var options = payload.options || {};
  setSelect('bp-assignee-filter', options.assignees || [], selected.assignee);
  setSelect('bp-field-filter', options.fields || [], selected.field);
  setSelect('bp-status-filter', options.statuses || [], selected.status);
  setSelect('bp-year-filter', (options.years || []).map(String), String(selected.year));
  setSelect('bp-step-filter', options.steps || [], selected.step);
  // The triggers read their labels off the selects, so they are redrawn AFTER
  // the repopulation above -- never before it.
  renderFilterTriggers();
  renderKpis(payload.kpis || {});
  renderDataNotice(payload);
  renderStageBoard(payload);
}

// PLAIN text, not markup: kpiTileHtml() escapes every value it is handed, so
// escaping here would double-escape.
function dayValue(value) {
  return fmtNum(value == null ? 0 : value) + ' Days';
}

/* The four KPI groups, in the maturation band's own vocabulary
   (views/board-widgets.js): three tiles plus the Success Rate donut.

   The donut is the SAME radial meter the maturation band uses and, like it,
   prints its own percentage inside the ring -- so its group carries only the
   label, in the same .kpi-label the tiles use. */
function renderKpis(kpis) {
  var host = byId('bpe-kpis');
  if (!host) return;
  // The dash array IS the percentage, so it must be a bounded whole number:
  // a missing or malformed rate reads 0%, never NaN and never a runaway arc.
  var percent = Math.min(Math.max(Math.round(Number(kpis.success_rate_pct) || 0), 0), 100);
  host.innerHTML =
    kpiTileHtml(dayValue(kpis.rig_inventory_days), 'Rig Inventory', '', 'calendar-days') +
    kpiTileHtml(dayValue(kpis.rig_target_days), 'Rig Target', '', 'flag') +
    '<div class="kpi-donut-group">' +
      kpiDonutHtml(percent, 'Success Rate ' + percent + '%') +
      '<small class="kpi-label">Success Rate</small>' +
    '</div>' +
    kpiTileHtml((kpis.actual_mean_ogip_bcf || 0) + '/' + (kpis.simulated_mean_ogip_bcf || 0) + ' BCF',
      'Total Mean OGIP', 'kpi-tile-ogip', 'flame', 'Actual/Simulated');
}

function renderDataNotice(payload) {
  var notice = byId('bpe-data-notice');
  var missing = ((payload.data_quality || {}).missing_simulated_mean_project_ids || []).length;
  var inconsistent = ((payload.data_quality || {}).unsuccessful_with_actual_project_ids || []).length;
  var outside = payload.out_of_range_years || [];
  var messages = [];
  if (missing) messages.push(missing + ' visible well' + (missing === 1 ? '' : 's') + ' missing simulated Mean OGIP');
  if (inconsistent) messages.push(inconsistent + ' unsuccessful well' + (inconsistent === 1 ? '' : 's') +
    ' with stored Actual Mean OGIP excluded');
  if (outside.length) messages.push('Historical Business Plan years outside 1999-2035: ' + outside.join(', '));
  notice.textContent = messages.join(' | ');
  notice.classList.toggle('hidden', !messages.length);
}

function statusIcon(item) {
  if (item.status === 'Pending Approval') return icon('circle-minus');
  if (item.status === 'Completed') return icon('circle-check');
  return icon('circle');
}

/* =========================================================================
   Card R2 — the WELL BOARD, in the Segment Maturation board's language:
   three .lead-column blocks of .lead-card buttons (views/board-widgets.js,
   the same renderers views/pipeline.js renderLeadBoard uses).

   The card is ONE target. Its six tracked items are a READOUT (dot + label,
   exactly the maturation card's item rows), not six buttons: the card opens
   the step the well is actually waiting on, so "where do I continue?" is
   answered by clicking the well rather than by aiming at a row.
   ========================================================================= */

// Priority drives the card border, never workflow status. Anything the server
// did not send reads Low, matching its own default.
var CARD_PRIORITIES = ['high', 'medium', 'low'];
function cardPriority(well) {
  var priority = String(well.priority || '').toLowerCase();
  return CARD_PRIORITIES.indexOf(priority) >= 0 ? priority : 'low';
}

// The payload carries BOTH an ordered, distinct `assignees` array and the
// pre-joined `assignee_label` ('Not Assigned' when there is none). The array
// is the one personChipsHtml wants -- it renders one chip per person and owns
// the empty case ("Unassigned" is the absence of a person, not a person).
function wellAssignees(well) {
  return (well && well.assignees) || [];
}

// The step the card opens: the first of the six items that is not finished
// ("Pending Approval" is work waiting on a supervisor, so it still counts as
// open). A fully completed well falls back to its first item.
function firstIncompleteSlug(well) {
  var items = (well && well.items) || [];
  var open = items.filter(function (item) { return item.status !== 'Completed'; })[0];
  return (open || items[0] || {}).detail_slug || '';
}

// One row per tracked item: the shared dot (its SHAPE carries the status, and
// its title reads "Label — Status") plus the item's label. The labels are
// compacted because the BP vocabulary runs long ("Post-Drilling Analysis &
// Reserves Booking") and this package must not eat the card's identity half --
// the dot's title always holds the full text.
function trackedItemsHtml(well) {
  return ((well && well.items) || []).map(function (item) {
    // item.source is the server's own 'system' | 'manual' | 'approval' marker
    // (workflow/business_plan.py _state): a step the workflow closed on the
    // user's behalf gets the muted check, not the green one.
    return '<span class="lead-item">' +
      leadItemHtml(item.status, item.label, item.source) +
      '<span class="lead-item-label">' + esc(compact(item.label, 22)) + '</span>' +
      '</span>';
  }).join('');
}

function wellCard(well) {
  return '<button type="button" class="lead-card lead-card-' + cardPriority(well) + '"' +
    ' data-project-id="' + well.project_id + '" data-step="' + esc(firstIncompleteSlug(well)) + '">' +
    '<span class="lead-card-identity">' +
      '<span class="lead-card-name">' + esc(well.project_name) + '</span>' +
      '<span class="lead-card-people">' + personChipsHtml(wellAssignees(well)) + '</span>' +
    '</span>' +
    '<span class="lead-card-items">' + trackedItemsHtml(well) + '</span>' +
    '</button>';
}

function renderStageBoard(payload) {
  var wells = payload.wells || [];
  var board = byId('bp-pipeline');
  board.innerHTML = STAGE_META.map(function (stage) {
    var rows = wells.filter(function (well) { return well.stage_key === stage.key; });
    var body = rows.length ? rows.map(wellCard).join('') :
      '<div class="pipeline-empty">No wells match these filters.</div>';
    // Plain navy headers, uniform across the three columns: the stage is named
    // by its glyph and title, never by a per-column accent color.
    return '<section class="lead-column">' +
      '<header>' +
        '<span class="lead-column-icon" aria-hidden="true">' + icon(stage.icon) + '</span>' +
        '<h3>' + esc(stage.label) + '</h3>' +
        '<span class="lead-column-count">' + rows.length + '</span>' +
      '</header>' +
      '<div class="lead-cards">' + body + '</div>' +
      '</section>';
  }).join('');
  all('.lead-card', board).forEach(function (card) {
    card.addEventListener('click', function () {
      openBusinessPlanDetail(Number(card.dataset.projectId), card.dataset.step);
    });
  });
}

function loadBusinessPlanDetail(projectId, detailSlug) {
  initialize();
  state.contextId += 1;
  state.projectId = projectId;
  state.detailSlug = detailSlug;
  state.fieldDrafts = {};
  state.structureDrafts = { formations: null, flowback: null };
  state.retryCommand = null;
  state.timers = {};
  // A hand-folded rail belongs to the page it was folded on: the new step's
  // own stage is the one that should be open when it lands.
  state.railStage = null;
  var requestId = ++state.detailRequest;
  var root = byId('bpe-detail-view');
  byId('bpe-main-view').classList.add('hidden');
  root.classList.remove('hidden');
  root.innerHTML = '<div class="bpe-detail-loading">Loading...</div>';
  return API.businessPlanDetail(projectId, detailSlug).then(function (detail) {
    if (requestId !== state.detailRequest) return;
    state.detail = detail;
    renderDetail();
    window.scrollTo({ top: 0, behavior: 'auto' });
  }).catch(function (error) {
    root.innerHTML = '<button id="bpe-load-back" type="button" class="ghost">' + icon('arrow-left') +
      ' Back to Business Plan Execution</button><div class="empty-state">This step could not be loaded.</div>';
    var back = byId('bpe-load-back');
    if (back) back.addEventListener('click', refreshBusinessPlan);
    msg(error.message, 'error');
  });
}

export function openBusinessPlanDetail(projectId, detailSlug) {
  if (!state.detail) return loadBusinessPlanDetail(projectId, detailSlug);
  return flushPendingSaves().then(function (saved) {
    if (!saved) return null;
    return loadBusinessPlanDetail(projectId, detailSlug);
  });
}

function trackingByKey(key) {
  var items = (state.detail && state.detail.stage_items) || [];
  return items.find(function (item) { return item.key === key; }) || { status: 'In Progress', color: 'empty', locked: false };
}

function value(key) { return (state.detail.values || {})[key]; }

// The house checkbox card (components.css .check-label). `is-system` still
// means "the workflow ticked this, not you" and still reads faint.
function checkbox(key, label, options) {
  options = options || {};
  var checked = options.checked == null ? truthy(value(key)) : !!options.checked;
  var disabled = options.disabled ? 'disabled' : '';
  return '<label class="check-label' + (options.system ? ' is-system' : '') + '">' +
    '<input type="checkbox" data-bpe-field="' + esc(key) + '" ' + (checked ? 'checked' : '') + ' ' + disabled + '>' +
    '<span>' + esc(label) + '</span></label>';
}

function textInput(key, label, options) {
  options = options || {};
  var type = options.type || 'text';
  return '<label class="bpe-field ' + (options.className || '') + '"><span>' + esc(label) +
    (options.required ? '<b aria-hidden="true">*</b>' : '') + '</span><input type="' + type + '" data-bpe-field="' + esc(key) +
    '" value="' + esc(value(key) || '') + '" ' + (options.disabled ? 'disabled' : '') +
    (options.readonly ? ' readonly' : '') + (options.placeholder ? ' placeholder="' + esc(options.placeholder) + '"' : '') + '></label>';
}

function selectInput(key, label, options, configOptions) {
  configOptions = configOptions || {};
  var labelMarkup = configOptions.headingLabel ?
    '<span class="bpe-heading-label-spacer" aria-hidden="true"></span><span class="visually-hidden">' + esc(label) +
      (configOptions.required ? ' (required)' : '') + '</span>' :
    '<span>' + esc(label) + (configOptions.required ? '<b aria-hidden="true">*</b>' : '') + '</span>';
  return '<label class="bpe-field ' + (configOptions.className || '') + '">' + labelMarkup +
    '<select data-bpe-field="' + esc(key) + '" ' +
    (configOptions.disabled ? 'disabled ' : '') + (configOptions.invalid ? 'aria-invalid="true" ' : '') + '>' +
    selectOptions(options, value(key), configOptions.placeholder) + '</select></label>';
}

// The house radio group (components.css .radio-group / .radio-group-label /
// .radio-options / .radio-option, the same markup detail-form.js emits): a
// quiet label over a row of option pills. `hideLegend` keeps the label in the
// accessibility tree and takes it off the screen -- the group's heading is
// already above it.
function radioGroup(key, label, options, disabled, hideLegend) {
  var labelId = 'bpe-radio-label-' + esc(key);
  return '<div class="radio-group" role="radiogroup" aria-labelledby="' + labelId + '">' +
    '<span class="radio-group-label' + (hideLegend ? ' visually-hidden' : '') + '" id="' + labelId + '">' +
    esc(label) + '</span><div class="radio-options">' + options.map(function (option) {
      return '<label class="radio-option"><input type="radio" name="' + esc(key) + '" data-bpe-field="' + esc(key) +
        '" value="' + esc(option) + '" ' + (String(value(key)) === option ? 'checked' : '') + ' ' +
        (disabled ? 'disabled' : '') + '><span>' + esc(option) + '</span></label>';
    }).join('') + '</div></div>';
}

function commentsMarkup() {
  var key = state.detail.comments_key;
  return '<label class="bpe-comments"><span>Comments</span><textarea data-bpe-field="' + esc(key) + '">' +
    esc(value(key) || '') + '</textarea></label>';
}

// The house file-location card (components.css .folder-card, the same glyph /
// path / copy-button row detail-form.js renderComponentFolder and the summary
// panel's folder rows draw). The path stays a LINK here -- the BP share is
// openable and always was -- wearing the house .folder-path chrome.
function folderMarkup(omit) {
  if (omit) return '';
  var folder = state.detail.folder || {};
  if (!folder.path) return '';
  return '<div class="folder-card">' +
    '<span class="folder-glyph" aria-hidden="true">📁</span>' +
    '<a class="folder-path" href="' + esc(folder.file_url || '#') + '" title="' + esc(folder.path) + '">' +
    esc(folder.path) + '</a>' +
    '<button type="button" id="bpe-copy-folder" class="icon-btn" title="Copy shared-folder path" aria-label="Copy shared-folder path">' +
    icon('copy') + '</button></div>';
}

function commonTail(options) {
  options = options || {};
  return commentsMarkup() + folderMarkup(options.omitFolder) +
    '<div class="bpe-save-line"><span>All changes are saved automatically</span>' +
    // The house auto-save indicator (components.css .save-state): ambient
    // status, not a toast. setFeedback() owns its is-saving/is-saved/is-error
    // state classes; the texts are unchanged.
    '<small id="bpe-save-feedback" class="save-state" aria-live="polite"></small>' +
    '<button type="button" id="bpe-retry-save" class="ghost hidden">Retry</button></div>' +
    approvalMarkup();
}

function gateForm() {
  var classification = value('bp_gate_classification');
  var formations = state.detail.formation_options || [];
  var intervalOptions = formations.concat(state.detail.hole_sections || []);
  var calcTdEditable = state.detail.role === 'supervisor';
  var coring = value('bp_gate_coring_program') === 'Yes';
  var intervalConflict = ['Standard A', 'Standard B'].indexOf(value('bp_gate_logging_program')) >= 0 &&
    value('bp_gate_interval_from') && value('bp_gate_interval_from') === value('bp_gate_interval_to');
  var selectedCoring = [];
  if (Array.isArray(value('bp_gate_coring_formations'))) selectedCoring = value('bp_gate_coring_formations');
  else {
  try { selectedCoring = JSON.parse(value('bp_gate_coring_formations') || '[]'); } catch (error) { selectedCoring = []; }
  }
  return '<div class="bpe-form-section"><h3>Well Classification</h3>' +
    radioGroup('bp_gate_classification', 'Well Classification', ['Development', 'Appraisal', 'Exploration'], false, true) + '</div>' +
    '<div class="bpe-form-section"><h3>Depth &amp; Schedule</h3><div class="bpe-gate-depth">' +
    textInput('bp_gate_calculated_td_ft_md', 'Calculated Business Plan TD (ft MD)', {
      type: 'number', required: true, readonly: !calcTdEditable,
      placeholder: calcTdEditable ? '' : 'Awaiting approved calculation'
    }) + textInput('bp_gate_actual_td_ft_md', 'Actual Business Plan TD (ft MD)', { type: 'number', required: true }) +
    textInput('bp_gate_calculated_drilling_days', 'Calculated Drilling Days (days)', {
      type: 'number', required: true, readonly: true, placeholder: 'Awaiting approved equation'
    }) + textInput('bp_gate_actual_drilling_days', 'Actual Drilling Days (days)', { type: 'number', required: true }) +
    '</div>' + (calcTdEditable ? textInput('bp_gate_calculated_td_override_reason', 'Calculated TD Override Reason', {
      className: 'bpe-override-reason'
    }) : '') + '</div>' +
    '<div class="bpe-form-section"><h3>Logging Program</h3><div class="bpe-gate-logging">' +
    selectInput('bp_gate_logging_program', 'Logging Program', ['Standard A', 'Standard B', 'Optimized Standard B'], {
      required: true, placeholder: 'Select Program', headingLabel: true
    }) +
    selectInput('bp_gate_interval_from', 'Interval From', intervalOptions, {
      required: true, placeholder: 'Select Formation', invalid: intervalConflict
    }) +
    selectInput('bp_gate_interval_to', 'Interval To', intervalOptions, {
      required: true, placeholder: 'Select Formation', invalid: intervalConflict
    }) +
    textInput('bp_gate_swc', 'SWC', { type: 'number', required: true }) +
    textInput('bp_gate_pressure_points', 'Pressure Points', { type: 'number', required: true }) +
    textInput('bp_gate_fluid_samples', 'Fluid Samples', { type: 'number', required: true }) + '</div>' +
    (intervalConflict ? '<p class="bpe-field-error" role="alert">Interval From and Interval To must differ for Standard A and Standard B.</p>' : '') +
    '</div>' +
    '<div class="bpe-form-section"><h3>Coring Program</h3><div class="bpe-gate-coring">' +
    selectInput('bp_gate_coring_program', 'Coring Program', ['Yes', 'No'], {
      required: true, placeholder: 'Select', headingLabel: true
    }) +
    textInput('bp_gate_coring_thickness_ft', 'Coring Thickness (ft)', { type: 'number', required: coring, disabled: !coring }) +
    // Multi-select, and it always was -- the value is stored as a JSON array.
    // It only READ as single-select: with no `size` a <select multiple> is
    // browser-sized to about four rows and looks like a tall dropdown. An
    // explicit size (bounded, so a long formation list scrolls rather than
    // pushing the form around) plus a hint that names the interaction and
    // echoes the current count makes it self-describing.
    '<label class="bpe-field bpe-coring-formations"><span>Coring Formations' + (coring ? '<b aria-hidden="true">*</b>' : '') +
    '</span><select multiple size="' + Math.min(Math.max(formations.length, 4), 8) + '"' +
    ' data-bpe-field="bp_gate_coring_formations" ' + (!coring ? 'disabled' : '') + '>' +
    formations.map(function (formation) { return '<option ' + (selectedCoring.indexOf(formation) >= 0 ? 'selected' : '') + '>' + esc(formation) + '</option>'; }).join('') +
    '</select><small class="bpe-field-hint">Select one or more — hold Ctrl (⌘ on Mac) to pick several. ' +
    (selectedCoring.length ? esc(selectedCoring.length + ' selected') : 'None selected') +
    '</small></label></div></div>' +
    checkbox('bp_gate_slides_saved', 'Business Plan Execution Gate slides are saved in the shared folder.') +
    commonTail();
}

function wellLettersForm() {
  var proposal = trackingByKey('well-proposal');
  return checkbox('well_proposal_shared', 'Well Proposal is completed and placed in the shared folder.', {
    disabled: proposal.locked, system: proposal.source === 'system',
    checked: proposal.source === 'system' && proposal.status === 'Completed' ? true : null
  }) + checkbox('site_preparation_shared', 'Site Preparation Letter is completed and placed in the shared folder.') +
    checkbox('approval_to_drill_shared', 'Approval to Drill Letter is completed and placed in the shared folder.') + commonTail();
}

function gheerForm() {
  var vsp = truthy(value('gheer_vsp_required'));
  var link = (state.detail.links || {}).vsp;
  return checkbox('gheer_geophysical_shared', 'Geophysical GHEER inputs are loaded in the shared folder.') +
    checkbox('gheer_geomechanical_shared', 'Geomechanical GHEER inputs are loaded in the shared folder.') +
    checkbox('gheer_vsp_required', 'VSP is required.') +
    (vsp ? '<div class="bpe-external-link">' + (link ? '<a href="' + esc(link) + '" target="_blank" rel="noopener">Open VSP form</a>' :
      '<span>VSP form link: Not configured</span>') + '</div>' : '') + commonTail();
}

function aapForm() {
  return checkbox('aap_petrel_loaded', 'Aramco Approved Picks are loaded into the PETREL repository.') +
    checkbox('aap_geoknowledge_loaded', 'Aramco Approved Picks are loaded into the GeoKnowledge database.') +
    commonTail({ omitFolder: true });
}

function summaryForm(finalSummary) {
  if (finalSummary) {
    var dry = (state.detail.fluid_state || {}).decision === 'all_water_or_dry';
    var copied = state.detail.sad_update_branch === 'copied_from_sad';
    return checkbox('final_exec_summary_done', 'Final Executive Summary slides are placed in the shared folder.', {
      disabled: dry, system: dry, checked: dry ? true : null
    }) + checkbox('final_ured_update_done', 'Final URED Update slides are placed in the shared folder.', {
      disabled: dry || copied, system: dry || copied, checked: dry || copied ? true : null
    }) + commonTail();
  }
  var executive = trackingByKey('executive-summary');
  var ured = trackingByKey('ured-update');
  return checkbox('exec_summary_loaded', 'Executive Summary slides are placed in the shared folder.', {
    disabled: executive.locked, system: executive.source === 'system',
    checked: executive.source === 'system' && executive.status === 'Completed' ? true : null
  }) + checkbox('ured_update_loaded', 'URED Update slides are placed in the shared folder.', {
    disabled: ured.locked, system: ured.source === 'system',
    checked: ured.source === 'system' && ured.status === 'Completed' ? true : null
  }) + commonTail();
}

function sadForm(update) {
  var base = update ? 'sad_update' : 'sad';
  var prefix = update ? 'resource_update' : 'post_drill_piip';
  var locked = update && trackingByKey('sad-update').locked;
  var liquid = truthy(value(prefix + '_has_liquid'));
  return '<div class="bpe-form-section"><h3>Reservoir Area (km²)</h3><div class="bpe-pair">' +
    // Card 3G: these four were the ONLY labels in the app reading "B90"/"B10".
    // The keys have always been p90/p10 -- the labels were the typo, so this is
    // a display correction with no data or validation change behind it.
    textInput(base + '_area_km2_p90', 'P90', { type: 'number', required: true, disabled: locked }) +
    textInput(base + '_area_km2_p10', 'P10', { type: 'number', required: true, disabled: locked }) + '</div></div>' +
    '<div class="bpe-form-section"><h3>GRV (10³ acre&middot;ft)</h3><div class="bpe-pair">' +
    textInput(base + '_grv_p90', 'P90', { type: 'number', required: true, disabled: locked }) +
    textInput(base + '_grv_p10', 'P10', { type: 'number', required: true, disabled: locked }) + '</div></div>' +
    checkbox(base + '_surfaces_polygons_loaded', 'The polygons and surfaces are placed in the shared folder.', { disabled: locked, system: locked }) +
    checkbox(base + '_slides_loaded', 'SAD Model slides are placed in the shared folder.', { disabled: locked, system: locked }) +
    '<div class="bpe-form-section"><h3>Gas Field Inputs</h3><div class="bpe-trio">' +
    textInput(prefix + '_gas_p90', 'P90 (BCF)', { type: 'number', required: true, disabled: locked }) +
    textInput(prefix + '_gas_mean', 'Mean OGIP (BCF)', { type: 'number', required: true, disabled: locked }) +
    textInput(prefix + '_gas_p10', 'P10 (BCF)', { type: 'number', required: true, disabled: locked }) + '</div></div>' +
    checkbox(prefix + '_has_liquid', 'Liquid (MMSTB)', { disabled: locked }) +
    (liquid ? '<div class="bpe-trio bpe-liquid-fields">' +
      textInput(prefix + '_liquid_p90', 'P90 (MMSTB)', { type: 'number', required: true, disabled: locked }) +
      textInput(prefix + '_liquid_mean', 'Mean (MMSTB)', { type: 'number', required: true, disabled: locked }) +
      textInput(prefix + '_liquid_p10', 'P10 (MMSTB)', { type: 'number', required: true, disabled: locked }) + '</div>' : '') +
    (update && state.detail.sad_update_branch === 'unresolved_comparison' ?
      '<div class="bpe-branch-note">Comparison is unresolved. No branch has been selected.</div>' : '') + commonTail();
}

function learningForm() {
  return checkbox('post_well_slides_loaded', 'Post-Drill Learning Review slides are placed in the shared folder.') + commonTail();
}

function formationRowMarkup(row, formationIndex) {
  var options = (state.detail.formation_options || []).slice();
  if (row.formation && options.indexOf(row.formation) < 0) options.push(row.formation);
  var payRows = (row.pay_intervals || []).map(function (interval, payIndex) {
    return '<div class="bpe-pay-row" data-pay-index="' + payIndex + '">' +
      formationCell(formationIndex, payIndex, 'top_tvdss_ft', 'Top TVDSS (ft)', interval.top_tvdss_ft, 'number') +
      formationCell(formationIndex, payIndex, 'base_tvdss_ft', 'Base TVDSS (ft)', interval.base_tvdss_ft, 'number') +
      formationCell(formationIndex, payIndex, 'phit_pct', 'Phit (%)', interval.phit_pct, 'number') +
      formationCell(formationIndex, payIndex, 'swt_pct', 'Swt (%)', interval.swt_pct, 'number') +
      formationCell(formationIndex, payIndex, 'ngr_pct', 'NGR (%)', interval.ngr_pct, 'number') +
      formationCell(formationIndex, payIndex, 'kint_md', 'Kint (mD)', interval.kint_md, 'number') +
      '<label><span>Fluid*</span><select data-formation-index="' + formationIndex + '" data-pay-index="' + payIndex +
      '" data-pay-field="fluid">' + selectOptions(FLUIDS, interval.fluid, 'Select Fluid') + '</select></label>' +
      '<button type="button" class="icon-btn bpe-remove-pay" data-formation-index="' + formationIndex +
      '" data-pay-index="' + payIndex + '" title="Remove Pay Interval" aria-label="Remove Pay Interval">' + icon('x') + '</button></div>';
  }).join('');
  return '<section class="bpe-formation-block" data-formation-index="' + formationIndex + '">' +
    '<header><label><span>Formation*</span><select data-formation-index="' + formationIndex + '" data-formation-field="formation">' +
    selectOptions(options, row.formation, 'Select Formation') + '</select></label>' +
    '<button type="button" class="icon-btn bpe-remove-formation" data-formation-index="' + formationIndex +
    '" title="Remove Formation" aria-label="Remove Formation">' + icon('x') + '</button></header>' +
    '<div class="bpe-formation-envelope">' +
    formationEnvelopeCell(formationIndex, 'top_tvdss_ft', 'Formation Top*', row.top_tvdss_ft) +
    formationEnvelopeCell(formationIndex, 'base_tvdss_ft', 'Formation Base*', row.base_tvdss_ft) +
    formationEnvelopeCell(formationIndex, 'thickness_ft', 'Formation Thickness*', row.thickness_ft) + '</div>' +
    '<div class="bpe-pay-heading"><h4>Pay Intervals</h4><button type="button" class="ghost bpe-add-pay" data-formation-index="' +
    formationIndex + '">' + icon('plus') + ' Add Pay Interval</button></div>' +
    '<div class="bpe-pay-list">' + (payRows || '<div class="bpe-inline-empty">No Pay Intervals.</div>') + '</div></section>';
}

// TVDSS is the one signed measure in this form (above datum reads negative);
// thickness, porosity, saturation, permeability and every rate cannot be.
// Shared by both formation cells and the flowback cells below.
function isSignedKey(key) {
  return /tvdss/.test(key);
}

function numericFloor(key) {
  return isSignedKey(key) ? '' : ' min="0"';
}

/* Client-side numeric validation for this whole form. Until now there was
   none here at all: a negative porosity or a typo'd rate travelled to the
   server, which refused it with a message the user only saw after the round
   trip (and, for the structure sheets, as a whole-sheet failure rather than
   against the cell).

   It reuses schema.js's numericFieldError -- the same rules the maturation
   forms enforce -- with bigOk ALWAYS on: BP figures such as a measured depth
   in feet legitimately exceed that helper's 9999 sanity cap, which exists for
   the maturation side's small measures. The negative and <=100% rules are the
   ones that matter here. */
function bpeNumericError(label, key, raw) {
  return numericFieldError(label || key, raw, true, /_pct$/.test(key), isSignedKey(key));
}

// The visible caption of a form cell, so the message names what the user is
// looking at rather than a storage key. Falls back to the key.
function fieldLabel(element, key) {
  var caption = element.closest('label');
  var span = caption && caption.querySelector('span');
  return span ? span.textContent.replace(/\*$/, '').trim() : key;
}

// Reject an edit before it is buffered or queued: the message lands in the
// save-state strip (this form's only feedback channel) and the input is
// marked, so the offending cell is findable in a long sheet.
function rejectNumericEdit(element, message) {
  element.classList.add('bpe-invalid');
  element.setAttribute('aria-invalid', 'true');
  setFeedback(message, true);
}

function clearNumericRejection(element) {
  element.classList.remove('bpe-invalid');
  element.removeAttribute('aria-invalid');
}

// Guard shared by the three edit paths (plain fields, formation/pay cells,
// flowback cells). Returns true when the edit may proceed.
function numericEditAllowed(element, key) {
  if (element.type !== 'number') return true;
  var error = bpeNumericError(fieldLabel(element, key), key, element.value);
  if (error) {
    rejectNumericEdit(element, error);
    return false;
  }
  clearNumericRejection(element);
  return true;
}

function formationEnvelopeCell(index, key, label, cellValue) {
  return '<label><span>' + esc(label) + '</span><input type="number" step="any"' + numericFloor(key) +
    ' data-formation-index="' + index +
    '" data-formation-field="' + esc(key) + '" value="' + esc(cellValue == null ? '' : cellValue) + '"></label>';
}

function formationCell(formationIndex, payIndex, key, label, cellValue, type) {
  var numeric = type === 'number' ? ' step="any"' + numericFloor(key) : '';
  return '<label><span>' + esc(label) + '</span><input type="' + type + '"' + numeric +
    ' data-formation-index="' + formationIndex +
    '" data-pay-index="' + payIndex + '" data-pay-field="' + esc(key) + '" value="' +
    esc(cellValue == null ? '' : cellValue) + '"></label>';
}

// Card 3C. The formation sheet IS the work on Quicklook Logs and Final Log
// Analysis, so it opens on first entry rather than making everyone click
// before they can type. The open state is remembered per (well, step): the
// re-render that follows every auto-save keeps whatever the user chose, while
// walking to a different step or well is a first load again and opens it.
// Only these two steps render the section at all, so the default is scoped by
// construction -- no other workflow's disclosure changes.
var formationsFoldContext = null;
var formationsFoldOpen = true;

function formationsFoldIsOpen() {
  var context = state.projectId + '|' + state.detailSlug;
  if (formationsFoldContext !== context) {
    formationsFoldContext = context;
    formationsFoldOpen = true;
  }
  return formationsFoldOpen;
}

function formationsForm() {
  var rows = state.detail.formations || [];
  var open = formationsFoldIsOpen();
  var confirmations = state.detailSlug === 'quicklook-logs' ?
    checkbox('quicklook_pdf', PDF_LABEL) + checkbox('quicklook_las', 'Logs as LAS') :
    checkbox('final_petrel', 'Logs in Petrel') + checkbox('final_pdf', PDF_LABEL) + checkbox('final_las', 'Logs as LAS');
  return '<div class="summary-fold bpe-formations-fold">' +
      '<button type="button" id="bpe-formations-head" class="summary-fold-head' + (open ? ' open' : '') +
        '" aria-expanded="' + open + '" aria-controls="bpe-formations">' +
        '<span class="summary-fold-title">Formation Interpretation</span>' +
        '<span class="summary-fold-chevron" aria-hidden="true"></span></button>' +
      '<div id="bpe-formations" class="bpe-formations summary-fold-body' + (open ? '' : ' collapsed') + '">' +
        rows.map(formationRowMarkup).join('') +
        '<button type="button" id="bpe-add-formation" class="ghost">' + icon('plus') + ' Add Formation</button>' +
      '</div>' +
    '</div>' +
    confirmations + commonTail();
}

function blankFlowbackStage() {
  return { id: (window.crypto && window.crypto.randomUUID) ? window.crypto.randomUUID() : 'draft-' + Date.now() + '-' + Math.random(),
    formation: '', top_md: '', base_md: '', dynamic_area_km2: '', dynamic_ogip_bcf: '', gas_rate_mmscfd: '',
    water_rate_bwpd: '', liquid_rate_bpd: '', choke_size_in: '', fwhp_psi: '' };
}

function flowCell(index, key, label, required, formation) {
  var disabled = trackingByKey('flowback').locked ? ' disabled' : '';
  if (key === 'formation') {
    return '<label><span>' + esc(label) + (required ? '<b>*</b>' : '') + '</span><select data-flow-index="' + index +
      '" data-flow-field="' + key + '"' + disabled + '>' + selectOptions(state.detail.formation_options || [], formation, 'Select Formation') + '</select></label>';
  }
  var row = state.detail.flowback_stages[index];
  return '<label><span>' + esc(label) + (required ? '<b>*</b>' : '') + '</span><input type="number" step="any"' +
    numericFloor(key) + ' data-flow-index="' + index +
    '" data-flow-field="' + key + '" value="' + esc(row[key] == null ? '' : row[key]) + '"' + disabled + '></label>';
}

function flowbackForm() {
  var locked = trackingByKey('flowback').locked;
  if (!state.detail.flowback_stages.length && !state.detail.flowback_initialized) {
    state.detail.flowback_stages = [blankFlowbackStage()];
    state.detail.flowback_initialized = true;
  }
  var panels = state.detail.flowback_stages.map(function (row, index) {
    return '<section class="bpe-flow-stage ' + (locked ? 'is-locked' : '') + '"><header>' +
      '<button type="button" class="icon-btn bpe-remove-flow" data-flow-index="' + index + '" title="Delete Stage ' + (index + 1) +
      '" aria-label="Delete Stage ' + (index + 1) + '" ' + (locked ? 'disabled' : '') + '>' + icon('x') + '</button>' +
      '<h4>Stage ' + (index + 1) + '</h4></header><div class="bpe-flow-grid">' +
      flowCell(index, 'formation', 'Formation', true, row.formation) +
      flowCell(index, 'top_md', 'Top (Measured Depth)', true) +
      flowCell(index, 'base_md', 'Base (Measured Depth)', true) +
      flowCell(index, 'dynamic_area_km2', 'Dynamic Area (km²)', false) +
      flowCell(index, 'dynamic_ogip_bcf', 'Dynamic OGIP (BCF)', false) +
      flowCell(index, 'gas_rate_mmscfd', 'Gas Rate (MMSCFD)', false) +
      flowCell(index, 'water_rate_bwpd', 'Water Rate (BWPD)', false) +
      flowCell(index, 'liquid_rate_bpd', 'Liquid Rate (BPD)', false) +
      flowCell(index, 'choke_size_in', 'Choke Size (in)', true) +
      flowCell(index, 'fwhp_psi', 'FWHP (psi)', true) + '</div></section>';
  }).join('');
  return '<div class="bpe-flow-heading"><h3>Flowback Stage Results</h3><button type="button" id="bpe-add-flow" class="icon-btn" ' +
    (locked ? 'disabled' : '') + ' title="Add Flowback stage" aria-label="Add Flowback stage">' + icon('plus') + '</button></div>' + panels +
    checkbox('flowback_shared_confirmed', 'Flowback sheet and slides are placed in the shared folder', {
      disabled: locked, system: locked, checked: locked ? true : null
    }) +
    commonTail();
}

function mtrForm() {
  var item = trackingByKey('mtr');
  var link = (state.detail.links || {}).structural_mtr;
  return checkbox('structural_mtr_shared', 'Structural MTR slides are placed in the shared folder.', {
    disabled: item.locked, system: item.source === 'system',
    checked: item.source === 'system' && item.status === 'Completed' ? true : null
  }) + '<div class="bpe-external-link">' + (link ? '<a href="' + esc(link) + '" target="_blank" rel="noopener">Open Structural MTR</a>' :
    '<span>Structural MTR link: Not configured</span>') + '</div>' + commonTail();
}

function pdaForm() {
  var item = trackingByKey('pda-booking');
  var development = value('bp_gate_classification') === 'Development';
  var response = value('reserves_booking_response') || '';
  var years = (state.detail.booking_years || []).map(String);
  if (value('reserves_booking_year') && years.indexOf(String(value('reserves_booking_year'))) < 0) years.unshift(String(value('reserves_booking_year')));
  return checkbox('pda_complete', 'Post-Drilling Analysis is completed and placed in the shared folder.', {
    disabled: development, system: development, checked: development ? true : null
  }) + radioGroup('reserves_booking_response', 'Is the well included in a Reserves Booking Cycle?', ['Yes', 'No'], item.locked) +
    (response === 'Yes' ? selectInput('reserves_booking_year', 'Reserves Booking Year', years, { required: true, disabled: item.locked }) : '') +
    commonTail();
}

function bodyMarkup() {
  var slug = state.detailSlug;
  if (slug === 'business-plan-gate') return gateForm();
  if (slug === 'well-letters') return wellLettersForm();
  if (slug === 'gheer-inputs') return gheerForm();
  if (slug === 'quicklook-logs' || slug === 'final-log-analysis') return formationsForm();
  if (slug === 'aramco-approved-pics') return aapForm();
  if (slug === 'sad-model') return sadForm(false);
  if (slug === 'summary-slides') return summaryForm(false);
  if (slug === 'post-drill-learning-review') return learningForm();
  if (slug === 'flowback-results') return flowbackForm();
  if (slug === 'sad-model-update') return sadForm(true);
  if (slug === 'final-summary-slides') return summaryForm(true);
  if (slug === 'structural-mtr') return mtrForm();
  if (slug === 'pda-booking') return pdaForm();
  return commonTail();
}

/* =========================================================================
   Card R3 — the detail SHELL, in the Segment Maturation detail page's own
   language: the house .detail-shell.detail-shell-lead grid (rail | editor |
   summary), the house .rail-stage-lead / .component-item rail, the house
   .editor-head, and the Lead Summary card's .ls-* anatomy.

   Nothing about the save engine, the field keys or the focus-restore
   attributes moved -- only the chrome around them.
   ========================================================================= */

/* The rail badge's tint. TWO vocabularies meet here, so the mapping is
   explicit rather than the house rail's toLowerCase(): a BP step's status is
   the BOARD's (Completed / Pending Approval / In Progress, from the server's
   navigation payload), while the rail's four tints are named for the TASK
   lifecycle (components.css .component-item.status-*). A lowercased
   'Completed' would produce a class with no rule behind it -- which is exactly
   how all fourteen badges ended up uniform gray. */
var RAIL_STATUS_SLUG = {
  'Completed': 'approved',
  'Pending Approval': 'ready',
  'In Progress': 'in-progress'
};

function railStatusSlug(status) {
  return RAIL_STATUS_SLUG[status] || 'not-assigned';
}

/* The rail is the maturation rail's ACCORDION: exactly one stage expanded at
   a time, chevron on the head, the open block carrying the navy .is-active
   accent (views/detail.js renderLeadRail + wireRailHandlers + syncStageOpenState).

   `state.railStage` is an OVERRIDE, not the state: null means "follow the step
   that is open", which is what makes navigating to another stage's step expand
   that stage. loadBusinessPlanDetail clears the override on every step load,
   so a manual fold never outlives the page it was made on. */
function activeStageKey() {
  var groups = (state.detail && state.detail.navigation) || [];
  var owner = groups.filter(function (group) {
    return group.details.some(function (item) { return item.slug === state.detailSlug; });
  })[0];
  return owner ? owner.stage_key : (groups[0] || {}).stage_key;
}

function openStageKey() {
  return state.railStage === undefined || state.railStage === null ? activeStageKey() : state.railStage;
}

// Sync the already-rendered rail to the open stage -- head open/aria-expanded,
// body collapsed, block .is-active -- without re-rendering the list, exactly as
// the house rail's syncStageOpenState does.
function syncRailOpenState() {
  var root = byId('bpe-detail-view');
  if (!root) return;
  var open = openStageKey();
  all('.rail-stage-head', root).forEach(function (head) {
    var isOpen = head.getAttribute('data-stage') === open;
    head.classList.toggle('open', isOpen);
    head.setAttribute('aria-expanded', String(isOpen));
  });
  all('.rail-stage-body', root).forEach(function (body) {
    body.classList.toggle('collapsed', body.getAttribute('data-stage') !== open);
  });
  all('.rail-stage-lead', root).forEach(function (block) {
    block.classList.toggle('is-active', block.getAttribute('data-stage') === open);
  });
}

function detailNavMarkup() {
  var stageIcons = {};
  STAGE_META.forEach(function (stage) { stageIcons[stage.key] = stage.icon; });
  var open = openStageKey();
  var number = 0;
  return (state.detail.navigation || []).map(function (group) {
    var isOpen = group.stage_key === open;
    var done = group.details.filter(function (item) { return item.status === 'Completed'; }).length;
    var items = group.details.map(function (item) {
      number += 1;
      // .bpe-nav-item is carried for the click wiring and the tests; the LOOK
      // is entirely the house .component-item (+ status-* for the badge tint
      // and .active for the current one, exactly as views/detail.js marks its
      // own rail).
      return '<button type="button" class="component-item status-' + railStatusSlug(item.status) +
        (item.slug === state.detailSlug ? ' active' : '') + ' bpe-nav-item"' +
        ' data-detail-slug="' + esc(item.slug) + '">' +
        '<span class="component-num">' + number + '</span><b>' + esc(item.label) + '</b></button>';
    }).join('');
    return '<div class="rail-stage rail-stage-lead' + (isOpen ? ' is-active' : '') + '"' +
      ' data-stage="' + esc(group.stage_key) + '">' +
      '<button type="button" class="rail-stage-head' + (isOpen ? ' open' : '') + '"' +
        ' data-stage="' + esc(group.stage_key) + '" aria-expanded="' + isOpen + '">' +
        '<span class="stage-icon" aria-hidden="true">' + icon(stageIcons[group.stage_key]) + '</span>' +
        '<span class="rail-stage-name">' + esc(group.stage_label) + '</span>' +
        '<span class="rail-stage-count">' + done + '/' + group.details.length + '</span>' +
        '<span class="rail-stage-chevron" aria-hidden="true"></span></button>' +
      '<div class="rail-stage-body' + (isOpen ? '' : ' collapsed') + '"' +
        ' data-stage="' + esc(group.stage_key) + '">' + items + '</div>' +
      '</div>';
  }).join('');
}

function railMarkup() {
  var detail = state.detail;
  return '<aside class="component-rail">' +
    '<div class="rail-head">' +
      // The page's one back control, where the maturation detail page keeps
      // its own: first thing in the rail head, above the record name.
      '<button type="button" id="bpe-back" class="ghost back-to-board">' + icon('arrow-left') +
        ' <span>Back to Business Plan Execution</span></button>' +
      // The record's priority chip, in the maturation shell's exact position:
      // beside the record name, one chip for the whole well (never per step).
      '<div class="detail-title-row"><h3>' + esc(detail.project.project_name) + '</h3>' +
        priorityChipHtml('bpe-priority-chip', detail.project.priority,
                         (detail.role || currentRole()) === 'supervisor') + '</div>' +
      '<p class="bpe-rail-eyebrow">' + esc(detail.detail.stage_label) + '</p>' +
    '</div>' +
    '<div class="component-list">' + detailNavMarkup() + '</div>' +
    '</aside>';
}

function assigneeOptions() {
  var users = state.users || Store.users || [];
  return [{ value: '', label: 'Not Assigned' }].concat(users.map(function (user) { return { value: user.name, label: user.name }; }));
}

// The editor head's control wears the maturation editor's compact select
// chrome (.editor-assignee). Assignee is the only control here: priority moved
// to the click-to-cycle chip beside the record name, because it belongs to the
// WELL and not to the step this editor is showing -- a per-step-looking
// dropdown invited the reading that each step carries its own priority.
function topControlsMarkup() {
  var role = state.detail.role || currentRole();
  return '<div class="bpe-detail-controls">' +
    '<label>Assignee<select id="bpe-assignee" class="editor-assignee" ' +
    (role === 'employee' ? 'disabled' : '') + '>' + selectOptions(assigneeOptions(), state.detail.assignee) + '</select></label></div>';
}

function editorMarkup() {
  var detail = state.detail;
  var chip = (detail.tracking || []).length
    ? '<span class="bpe-detail-status state-' + esc(detail.tracking[0].color) + '">' +
      statusIcon(detail.tracking[0]) + esc(detail.tracking[0].status) + '</span>'
    : '';
  return '<section class="component-editor bpe-detail-form">' +
    '<div class="editor-head"><h2>' + esc(detail.detail.label) + '</h2>' + chip + topControlsMarkup() + '</div>' +
    bodyMarkup() +
    '</section>';
}

/* -------------------------------------------------------------------------
   The Well Summary. Its CONTENT is the original panel's, unchanged and in the
   original order -- four label/value rows (Well, Field, Business Plan Year,
   Stage Progress as completed/total) over the stage's tracking items, with the
   gear menu -- and its CHROME is the redesign's Lead Summary card
   (views/lead-summary.js: .ls-card / .ls-head / .ls-title / .ls-section /
   .ls-gear / .ls-menu). No progress bar and no column grid: this panel reads
   as a fact sheet, which is what it always was.
   ------------------------------------------------------------------------- */

// One fact row. The value is the value or the one "no value" glyph -- never
// blank, because a blank cell reads as a layout bug where a dash reads as
// "not recorded yet".
function summaryFact(label, raw) {
  return '<div><dt>' + esc(label) + '</dt>' +
    '<dd>' + (isFilled(raw) ? esc(raw) : EM_DASH) + '</dd></div>';
}

function summaryMarkup() {
  var detail = state.detail;
  var project = detail.project || {};
  var items = detail.stage_items || [];
  var done = items.filter(function (item) { return item.status === 'Completed'; }).length;
  return '<aside class="summary-panel"><div class="ls-card">' +
    '<div class="ls-head"><h3 class="ls-title">Well Summary</h3>' +
      '<button type="button" id="bpe-summary-gear" class="icon-btn ls-gear" aria-haspopup="menu"' +
      ' aria-expanded="false" title="Well Summary actions" aria-label="Well Summary actions">' + icon('settings') + '</button>' +
    '</div>' +
    '<dl class="bpe-summary-facts">' +
      summaryFact('Well', project.project_name) +
      summaryFact('Field', project.field) +
      summaryFact('Business Plan Year', project.business_plan_year) +
      // The denominator is the stage's OWN item count, never a hard-coded six.
      summaryFact('Stage Progress', done + ' / ' + items.length) +
    '</dl>' +
    '<section class="ls-section"><div class="ls-items">' + items.map(function (item) {
      // The board's own dot vocabulary (views/board-widgets.js), so a step's
      // state reads the same on the card and in this panel.
      return '<span class="lead-item">' + leadItemHtml(item.status, item.label, item.source) +
        '<span class="lead-item-label">' + esc(item.label) + '</span></span>';
    }).join('') + '</div></section>' +
    '<div id="bpe-summary-menu" class="ls-menu hidden" role="menu" aria-labelledby="bpe-summary-gear">' +
      '<button type="button" id="bpe-edit-all" class="ls-menu-item" role="menuitem">Edit all project fields</button>' +
    '</div>' +
    '</div></aside>';
}

function approvalMarkup() {
  if (['business-plan-gate', 'sad-model', 'post-drill-learning-review', 'sad-model-update'].indexOf(state.detailSlug) < 0) return '';
  if (state.detailSlug === 'sad-model-update' && state.detail.sad_update_branch !== 'manual_update') return '';
  var tracking = (state.detail.tracking || [])[0] || { status: 'In Progress' };
  var role = state.detail.role || currentRole();
  var buttons = '';
  if (role === 'supervisor') {
    buttons += '<button type="button" data-bpe-transition="return" class="ghost" ' +
      (tracking.status !== 'Pending Approval' ? 'disabled' : '') + '>Return</button>';
    buttons += '<button type="button" data-bpe-transition="approve" ' +
      (tracking.status !== 'Pending Approval' ? 'disabled' : '') + '>Approve</button>';
  }
  buttons += '<button type="button" data-bpe-transition="submit" ' +
    (tracking.status !== 'In Progress' ? 'disabled' : '') + '>Submit for Approval</button>';
  if (tracking.status === 'Completed' && role === 'supervisor') {
    buttons += '<button type="button" data-bpe-transition="reopen" class="ghost">Reopen</button>';
  }
  return '<div class="bpe-approval-row">' + buttons + '</div>';
}

var DETAIL_FOCUS_ATTRIBUTES = [
  'data-bpe-field', 'data-formation-index', 'data-formation-field',
  'data-pay-index', 'data-pay-field', 'data-flow-index', 'data-flow-field',
  'data-bpe-transition', 'data-detail-slug'
];

function focusAttributeValue(value) {
  return String(value).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}

function captureDetailFocus() {
  var root = byId('bpe-detail-view');
  var active = document.activeElement;
  if (!root || !active || !root.contains(active)) return null;
  var selector = active.id ? '#' + active.id : active.tagName.toLowerCase();
  var identified = !!active.id;
  if (!active.id) {
    DETAIL_FOCUS_ATTRIBUTES.forEach(function (name) {
      if (active.hasAttribute(name)) {
        identified = true;
        selector += '[' + name + '="' + focusAttributeValue(active.getAttribute(name)) + '"]';
      }
    });
    if (active.type === 'radio') selector += '[value="' + focusAttributeValue(active.value) + '"]';
  }
  if (!identified) return null;
  var snapshot = { selector: selector, tagName: active.tagName, type: active.type };
  if (active.tagName === 'SELECT' && active.multiple) {
    snapshot.values = all('option:checked', active).map(function (option) { return option.value; });
  } else if (active.type === 'checkbox' || active.type === 'radio') {
    snapshot.checked = active.checked;
  } else if ('value' in active) {
    snapshot.value = active.value;
    try {
      snapshot.selectionStart = active.selectionStart;
      snapshot.selectionEnd = active.selectionEnd;
    } catch (error) { /* Selection is unavailable for number inputs and selects. */ }
  }
  return snapshot;
}

function restoreDetailFocus(snapshot) {
  if (!snapshot) return;
  var element = document.querySelector(snapshot.selector);
  if (!element || element.disabled) return;
  if (snapshot.values) {
    all('option', element).forEach(function (option) { option.selected = snapshot.values.indexOf(option.value) >= 0; });
  } else if (snapshot.checked != null) {
    element.checked = snapshot.checked;
  } else if (snapshot.value != null) {
    element.value = snapshot.value;
  }
  try { element.focus({ preventScroll: true }); } catch (error) { element.focus(); }
  if (snapshot.selectionStart != null) {
    try { element.setSelectionRange(snapshot.selectionStart, snapshot.selectionEnd); } catch (error) { /* Unsupported input type. */ }
  }
}

function applyDetailControlLocks() {
  var tracking = (state.detail.tracking || [])[0] || {};
  var approvalLocked = tracking.source === 'approval' &&
    (tracking.status === 'Pending Approval' || tracking.status === 'Completed');
  var comparisonLocked = state.detailSlug === 'sad-model-update' &&
    state.detail.sad_update_branch !== 'manual_update';
  if (!approvalLocked && !comparisonLocked) return;
  all('[data-bpe-field]', byId('bpe-detail-view')).forEach(function (element) {
    if (element.dataset.bpeField !== state.detail.comments_key) element.disabled = true;
  });
}

function renderDetail() {
  var detail = state.detail;
  if (!detail) return;
  var focus = captureDetailFocus();
  byId('bpe-detail-view').innerHTML =
    '<div class="detail-shell detail-shell-lead panel">' +
      railMarkup() + editorMarkup() + summaryMarkup() +
    '</div>';
  applyDetailControlLocks();
  bindDetail();
  ensureUsers();
  restoreDetailFocus(focus);
}

function ensureUsers() {
  if (state.users || Store.users) return;
  API.users().then(function (users) {
    state.users = users || [];
    if (byId('bpe-assignee')) {
      var selected = state.detail.assignee || '';
      byId('bpe-assignee').innerHTML = selectOptions(assigneeOptions(), selected);
      byId('bpe-assignee').value = selected;
    }
  }).catch(function () {});
}

// The house .save-state indicator's three moods. The TEXTS are the contract
// (the save engine and the tests read them); the classes only color them.
// `retryable` decides whether the Retry button comes with the error. It is
// false when the server READ the request and refused it: re-sending the same
// payload would be refused again, and the fix is in the form. Defaults to
// true so every existing caller keeps the transport-failure behaviour.
function setFeedback(text, error, retryable) {
  var feedback = byId('bpe-save-feedback');
  if (!feedback) return;
  feedback.textContent = text || '';
  feedback.classList.toggle('is-error', !!error);
  feedback.classList.toggle('is-saving', !error && text === 'Saving...');
  feedback.classList.toggle('is-saved', !error && text === 'Saved');
  var retry = byId('bpe-retry-save');
  if (retry) retry.classList.toggle('hidden', !error || retryable === false);
}

function copyRows(rows) {
  return JSON.parse(JSON.stringify(rows || []));
}

function currentContext() {
  return { id: state.contextId, projectId: state.projectId, detailSlug: state.detailSlug };
}

function isCurrentContext(context) {
  return !!context && context.id === state.contextId && context.projectId === state.projectId &&
    context.detailSlug === state.detailSlug;
}

function draftIsCurrent(draft) {
  return !!draft && isCurrentContext(draft.context);
}

function hasCurrentDrafts() {
  return Object.keys(state.fieldDrafts).some(function (key) { return draftIsCurrent(state.fieldDrafts[key]); }) ||
    draftIsCurrent(state.structureDrafts.formations) || draftIsCurrent(state.structureDrafts.flowback);
}

// Only UNSAVED DATA blocks navigation, and this is the predicate that says so.
//
// It used to answer true for `state.retryCommand` as well, which is what froze
// the page after a rejected Submit for Approval: a refused transition changes
// nothing and leaves nothing unsaved, but every navigation entry point gates
// on flushPendingSaves(), so the back button, the rail, other wells' cards and
// even re-clicking Submit all became silent no-ops until the user happened to
// edit a field (which clears retryCommand as a side effect). A failed field or
// structure draft is different -- the user's typing really is only in the
// browser -- so those still hold the page.
function hasFailedCurrentDrafts() {
  return Object.keys(state.fieldDrafts).some(function (key) {
    var draft = state.fieldDrafts[key];
    return draftIsCurrent(draft) && draft.failed;
  }) || ['formations', 'flowback'].some(function (key) {
    var draft = state.structureDrafts[key];
    return draftIsCurrent(draft) && draft.failed;
  });
}

function applyCurrentDrafts() {
  if (!state.detail) return;
  state.detail.values = state.detail.values || {};
  Object.keys(state.fieldDrafts).forEach(function (key) {
    var draft = state.fieldDrafts[key];
    if (draftIsCurrent(draft)) state.detail.values[key] = draft.value;
  });
  if (draftIsCurrent(state.structureDrafts.formations)) {
    state.detail.formations = copyRows(state.structureDrafts.formations.rows);
  }
  if (draftIsCurrent(state.structureDrafts.flowback)) {
    state.detail.flowback_stages = copyRows(state.structureDrafts.flowback.rows);
    state.detail.flowback_initialized = true;
  }
}

function mergeReturnedDetail(next) {
  next.folder = next.folder || state.detail.folder;
  next.role = next.role || state.detail.role;
  state.detail = next;
  applyCurrentDrafts();
}

function detailStateSignature(detail) {
  return JSON.stringify({
    branch: detail.sad_update_branch,
    fluid: detail.fluid_state,
    tracking: detail.tracking,
    stage: detail.stage_items
  });
}

function queueSave(work, options) {
  options = options || {};
  var context = options.context || currentContext();
  if (isCurrentContext(context)) setFeedback('Saving...', false);
  var job = state.saveQueue.catch(function () {}).then(function () {
    var before = isCurrentContext(context) && state.detail ? detailStateSignature(state.detail) : null;
    return work().then(function (response) { return { response: response, before: before }; });
  }).then(function (result) {
    var response = result.response;
    if (options.onSuccess) options.onSuccess(response);
    if (!isCurrentContext(context)) return response;
    if (options.merge !== false) {
      var next = response.detail || response;
      mergeReturnedDetail(next);
      if (options.rerender || result.before !== detailStateSignature(state.detail)) renderDetail();
    }
    setFeedback(hasCurrentDrafts() ? 'Saving...' : 'Saved', false);
    return response;
  }).catch(function (error) {
    if (options.onFailure) options.onFailure(error);
    if (isCurrentContext(context)) {
      if (options.failureText) setFeedback(options.failureText(error), true, isRetryable(error));
      else setFeedback('Save failed', true);
      msg(error.message, 'error');
    }
    return null;
  });
  state.saveQueue = job;
  return job;
}

// A 4xx means the server read the request and refused it -- the approval
// validators in workflow/business_plan.py _approval_errors, say, or a stale
// revision. Sending the identical payload again cannot help. Anything else
// (a rejected fetch, which carries no status, or a 5xx) is a transport
// problem, where Retry is exactly the right offer.
function isRetryable(error) {
  var status = error && error.status;
  return !(status >= 400 && status < 500);
}

function queueCommandSave(work, options) {
  options = options || {};
  var context = options.context || currentContext();
  var originalSuccess = options.onSuccess;
  var originalFailure = options.onFailure;
  function run() {
    state.retryCommand = null;
    var commandOptions = Object.assign({}, options, {
      context: context,
      onSuccess: function (response) {
        if (state.retryCommand && state.retryCommand.run === run) state.retryCommand = null;
        if (originalSuccess) originalSuccess(response);
      },
      onFailure: function (error) {
        // Only arm Retry for a transport failure. A refusal leaves nothing
        // pending, so there is nothing to re-send -- and arming it would put
        // a button on screen whose only effect is to be refused again.
        state.retryCommand = isRetryable(error) ? { context: context, run: run } : null;
        if (originalFailure) originalFailure(error);
      },
      // The command did not save anything, so "Save failed" would be a lie
      // about the user's data as well as unhelpful about the actual problem.
      failureText: options.failureText || function (error) {
        return isRetryable(error) ? 'Save failed' : (error.message || 'Refused');
      }
    });
    return queueSave(work, commandOptions);
  }
  return run();
}

function inputValue(element) {
  if (element.type === 'checkbox') return element.checked;
  if (element.multiple) return all('option:checked', element).map(function (option) { return option.value; });
  return element.value;
}

function enqueueFieldDraft(key, version) {
  var draft = state.fieldDrafts[key];
  if (!draft || draft.version !== version || draft.queuedVersion === version) return state.saveQueue;
  clearTimeout(state.timers[key]);
  draft.queuedVersion = version;
  draft.failed = false;
  var payload = Object.assign({}, draft.payload);
  if (key === 'bp_gate_calculated_td_ft_md' && isCurrentContext(draft.context)) {
    payload.override_reason = value('bp_gate_calculated_td_override_reason') || '';
  }
  var context = draft.context;
  return queueSave(function () {
    return API.saveBusinessPlanField(context.projectId, context.detailSlug, payload);
  }, {
    context: context,
    rerender: draft.rerender,
    onSuccess: function () {
      var latest = state.fieldDrafts[key];
      if (latest && latest.version === version && latest.context.id === context.id) delete state.fieldDrafts[key];
    },
    onFailure: function () {
      var latest = state.fieldDrafts[key];
      if (latest && latest.version === version && latest.context.id === context.id) {
        latest.failed = true;
        latest.queuedVersion = null;
      }
    }
  });
}

function valueFromElement(key) {
  var element = document.querySelector('[data-bpe-field="' + key + '"]');
  return element ? inputValue(element) : value(key);
}

// The tail of every field edit: buffer the draft and either save it now (a
// tick / a pick is a finished edit) or after the typing pause. Split out of
// the listener below because the Well Classification path has to WAIT for a
// dialog answer first -- the queueing itself is identical either way.
function queueFieldDraft(element, immediate, key, nextValue, payload) {
  if (key === 'bp_gate_calculated_td_ft_md') {
    payload.override_reason = valueFromElement('bp_gate_calculated_td_override_reason');
  }
  var version = ++state.saveVersion;
  state.detail.values[key] = nextValue;
  state.fieldDrafts[key] = {
    context: currentContext(), version: version, queuedVersion: null, failed: false,
    value: nextValue, payload: payload,
    rerender: !!CONDITIONAL_FIELDS[key] || element.type === 'checkbox' || element.tagName === 'SELECT'
  };
  state.retryCommand = null;
  setFeedback('Saving...', false);
  clearTimeout(state.timers[key]);
  if (immediate) enqueueFieldDraft(key, version);
  else state.timers[key] = setTimeout(function () { enqueueFieldDraft(key, version); }, state.saveDelay);
}

function bindFieldInputs() {
  all('[data-bpe-field]', byId('bpe-detail-view')).forEach(function (element) {
    var immediate = element.type === 'checkbox' || element.type === 'radio' || element.tagName === 'SELECT';
    element.addEventListener(immediate ? 'change' : 'input', function () {
      var key = element.dataset.bpeField;
      if (!numericEditAllowed(element, key)) return;
      var nextValue = inputValue(element);
      var previous = value(key);
      var payload = { field_key: key, value: nextValue, changed_by: currentUserName() };
      if (key === 'bp_gate_classification' && previous && previous !== nextValue) {
        // The app dialog is ASYNC, so nothing is queued until it answers.
        // Cancelling re-renders the form, which restores the stored
        // classification -- the select is left showing the rejected pick
        // otherwise. The context is captured first: a save landing while the
        // dialog is open can replace state.detail underneath it.
        var context = currentContext();
        confirmDialog({
          title: 'Change Well Classification',
          message: 'Changing the classification from "' + previous + '" to "' + nextValue +
            '" resets the defaults this step derives from it.\nValues that do not depend on the classification are kept.',
          confirmLabel: 'Change'
        }).then(function (confirmed) {
          if (!isCurrentContext(context)) return;
          if (!confirmed) { renderDetail(); return; }
          payload.confirm_reset = true;
          queueFieldDraft(element, immediate, key, nextValue, payload);
        });
        return;
      }
      queueFieldDraft(element, immediate, key, nextValue, payload);
    });
  });
}

function markStructureDraft(kind, rows) {
  var draft = {
    context: currentContext(), version: ++state.saveVersion, queuedVersion: null,
    failed: false, rows: copyRows(rows)
  };
  state.structureDrafts[kind] = draft;
  setFeedback('Saving...', false);
  return draft.version;
}

function hydrateFormationDraftIds(draftRows, serverRows) {
  var usedFormationIds = {};
  (draftRows || []).forEach(function (row) { if (row.id != null) usedFormationIds[String(row.id)] = true; });
  (draftRows || []).forEach(function (row, rowIndex) {
    var serverRow = (serverRows || []).find(function (candidate) {
      return candidate.formation === row.formation && !usedFormationIds[String(candidate.id)];
    });
    if (!serverRow && serverRows && serverRows[rowIndex] &&
        !usedFormationIds[String(serverRows[rowIndex].id)]) serverRow = serverRows[rowIndex];
    if (!serverRow) return;
    if (row.id == null) row.id = serverRow.id;
    usedFormationIds[String(serverRow.id)] = true;
    var usedPayIds = {};
    (row.pay_intervals || []).forEach(function (interval) { if (interval.id != null) usedPayIds[String(interval.id)] = true; });
    (row.pay_intervals || []).forEach(function (interval, intervalIndex) {
      if (interval.id != null) return;
      var serverInterval = (serverRow.pay_intervals || [])[intervalIndex];
      if (serverInterval && !usedPayIds[String(serverInterval.id)]) {
        interval.id = serverInterval.id;
        usedPayIds[String(serverInterval.id)] = true;
      }
    });
  });
}

function enqueueStructureDraft(kind, version, rerender) {
  var draft = state.structureDrafts[kind];
  if (!draft || draft.version !== version || draft.queuedVersion === version) return state.saveQueue;
  clearTimeout(state.timers[kind]);
  draft.queuedVersion = version;
  draft.failed = false;
  var context = draft.context;
  var rows = copyRows(draft.rows);
  var work = kind === 'formations' ? function () {
    return API.saveBusinessPlanFormations(context.projectId, context.detailSlug, rows);
  } : function () {
    return API.saveBusinessPlanFlowback(context.projectId, rows);
  };
  return queueSave(work, {
    context: context,
    rerender: !!rerender,
    onSuccess: function (response) {
      var latest = state.structureDrafts[kind];
      if (kind === 'formations' && latest && latest.version !== version && latest.context.id === context.id) {
        hydrateFormationDraftIds(latest.rows, ((response.detail || response).formations || []));
      }
      if (latest && latest.version === version && latest.context.id === context.id) state.structureDrafts[kind] = null;
    },
    onFailure: function () {
      var latest = state.structureDrafts[kind];
      if (latest && latest.version === version && latest.context.id === context.id) {
        latest.failed = true;
        latest.queuedVersion = null;
      }
    }
  });
}

function updateFormationBuffer(element) {
  var formationIndex = Number(element.dataset.formationIndex);
  var row = state.detail.formations[formationIndex];
  if (!row) return;
  if (element.dataset.formationField) row[element.dataset.formationField] = element.value;
  if (element.dataset.payField) {
    var pay = row.pay_intervals[Number(element.dataset.payIndex)];
    if (pay) pay[element.dataset.payField] = element.value;
  }
}

function saveFormationBuffer(rerender) {
  var version = markStructureDraft('formations', state.detail.formations || []);
  return enqueueStructureDraft('formations', version, rerender);
}

/* -------------------------------------------------------------------------
   Row identity across an async confirmation

   A removal is confirmed through a dialog, and a save response landing while
   that dialog is open replaces state.detail wholesale (mergeReturnedDetail) --
   so by the time the answer arrives, the row a click meant may sit at a
   different index, or be gone. Each handler therefore captures a MARKER of the
   row BEFORE opening the dialog and re-resolves it after: by stored id where
   the row has one, else by position plus the field that identified the row on
   screen. No match means the rows moved under the dialog, and the removal is
   DROPPED rather than applied to a stranger.
   ------------------------------------------------------------------------- */
function rowMarker(row, index, field) {
  return { index: index, id: row ? row.id : null, field: field, value: row ? row[field] : null };
}

function rowIndexFor(rows, marker) {
  var list = rows || [];
  if (marker.id != null) {
    for (var i = 0; i < list.length; i += 1) {
      if (list[i] && String(list[i].id) === String(marker.id)) return i;
    }
    return -1;
  }
  var row = list[marker.index];
  if (!row) return -1;
  return String(row[marker.field] == null ? '' : row[marker.field]) ===
    String(marker.value == null ? '' : marker.value) ? marker.index : -1;
}

function bindFormationInputs() {
  all('[data-formation-field], [data-pay-field]', byId('bpe-detail-view')).forEach(function (element) {
    element.addEventListener(element.tagName === 'SELECT' ? 'change' : 'input', function () {
      var cellKey = element.dataset.payField || element.dataset.formationField;
      if (!numericEditAllowed(element, cellKey)) return;
      updateFormationBuffer(element);
      var version = markStructureDraft('formations', state.detail.formations || []);
      clearTimeout(state.timers.formations);
      state.timers.formations = setTimeout(function () {
        enqueueStructureDraft('formations', version, false);
      }, element.tagName === 'SELECT' ? 0 : state.saveDelay);
    });
  });
  // Toggle in place: the sheet holds live inputs and a pending auto-save, so
  // re-rendering the page to open a disclosure would be both wasteful and a
  // way to lose a half-typed cell.
  var formationsHead = byId('bpe-formations-head');
  if (formationsHead) formationsHead.addEventListener('click', function () {
    formationsFoldOpen = !formationsFoldOpen;
    formationsHead.classList.toggle('open', formationsFoldOpen);
    formationsHead.setAttribute('aria-expanded', String(formationsFoldOpen));
    var body = byId('bpe-formations');
    if (body) body.classList.toggle('collapsed', !formationsFoldOpen);
  });
  var addFormation = byId('bpe-add-formation');
  if (addFormation) addFormation.addEventListener('click', function () {
    var used = state.detail.formations.map(function (row) { return row.formation; });
    var formation = (state.detail.formation_options || []).find(function (name) { return used.indexOf(name) < 0; }) || 'SARH';
    state.detail.formations.push({ formation: formation, top_tvdss_ft: '', base_tvdss_ft: '', thickness_ft: '', pay_intervals: [
      { top_tvdss_ft: '', base_tvdss_ft: '', phit_pct: '', swt_pct: '', ngr_pct: '', kint_md: '', fluid: '' }
    ] });
    saveFormationBuffer(true);
  });
  all('.bpe-add-pay', byId('bpe-detail-view')).forEach(function (button) {
    button.addEventListener('click', function () {
      state.detail.formations[Number(button.dataset.formationIndex)].pay_intervals.push({
        top_tvdss_ft: '', base_tvdss_ft: '', phit_pct: '', swt_pct: '', ngr_pct: '', kint_md: '', fluid: ''
      });
      saveFormationBuffer(true);
    });
  });
  // Every removal below is confirmed through the app dialog, then re-checks
  // BOTH the context (a different step may be loaded) and the row marker (the
  // rows may have been replaced by a save response) before it cuts anything.
  all('.bpe-remove-pay', byId('bpe-detail-view')).forEach(function (button) {
    button.addEventListener('click', function () {
      var context = currentContext();
      var formationIndex = Number(button.dataset.formationIndex);
      var payIndex = Number(button.dataset.payIndex);
      var formation = (state.detail.formations || [])[formationIndex] || {};
      var formationMark = rowMarker(formation, formationIndex, 'formation');
      var payMark = rowMarker((formation.pay_intervals || [])[payIndex], payIndex, 'top_tvdss_ft');
      confirmDialog({
        title: 'Remove Pay Interval',
        message: 'Remove this pay interval? Its entered depths and properties are discarded.',
        confirmLabel: 'Remove'
      }).then(function (confirmed) {
        if (!confirmed || !isCurrentContext(context)) return;
        var rows = state.detail.formations || [];
        var at = rowIndexFor(rows, formationMark);
        if (at < 0) return;
        var payAt = rowIndexFor(rows[at].pay_intervals || [], payMark);
        if (payAt < 0) return;
        rows[at].pay_intervals.splice(payAt, 1);
        saveFormationBuffer(true);
      });
    });
  });
  all('.bpe-remove-formation', byId('bpe-detail-view')).forEach(function (button) {
    button.addEventListener('click', function () {
      var context = currentContext();
      var index = Number(button.dataset.formationIndex);
      var row = (state.detail.formations || [])[index] || {};
      var marker = rowMarker(row, index, 'formation');
      var intervals = (row.pay_intervals || []).length;
      confirmDialog({
        title: 'Remove Formation',
        message: 'Remove ' + (row.formation ? '"' + row.formation + '"' : 'this formation') + ' and its ' +
          intervals + ' pay interval' + (intervals === 1 ? '' : 's') + '?\nEvery value entered for them is discarded.',
        confirmLabel: 'Remove',
        danger: true
      }).then(function (confirmed) {
        if (!confirmed || !isCurrentContext(context)) return;
        var at = rowIndexFor(state.detail.formations, marker);
        if (at < 0) return;
        state.detail.formations.splice(at, 1);
        saveFormationBuffer(true);
      });
    });
  });
}

function updateFlowBuffer(element) {
  var row = state.detail.flowback_stages[Number(element.dataset.flowIndex)];
  if (row) row[element.dataset.flowField] = element.value;
}

function saveFlowback(rerender) {
  var version = markStructureDraft('flowback', state.detail.flowback_stages || []);
  return enqueueStructureDraft('flowback', version, rerender);
}

function bindFlowbackInputs() {
  all('[data-flow-field]', byId('bpe-detail-view')).forEach(function (element) {
    element.addEventListener(element.tagName === 'SELECT' ? 'change' : 'input', function () {
      if (!numericEditAllowed(element, element.dataset.flowField)) return;
      updateFlowBuffer(element);
      var version = markStructureDraft('flowback', state.detail.flowback_stages || []);
      clearTimeout(state.timers.flowback);
      state.timers.flowback = setTimeout(function () {
        enqueueStructureDraft('flowback', version, true);
      }, element.tagName === 'SELECT' ? 0 : state.saveDelay);
    });
  });
  var add = byId('bpe-add-flow');
  if (add) add.addEventListener('click', function () {
    state.detail.flowback_stages.push(blankFlowbackStage());
    saveFlowback(true);
  });
  all('.bpe-remove-flow', byId('bpe-detail-view')).forEach(function (button) {
    button.addEventListener('click', function () {
      var context = currentContext();
      var index = Number(button.dataset.flowIndex);
      // Every flowback row carries an id (the server's, or a draft uuid from
      // blankFlowbackStage), so this marker always resolves by identity.
      var marker = rowMarker((state.detail.flowback_stages || [])[index], index, 'formation');
      confirmDialog({
        title: 'Delete Flowback Stage',
        message: 'Delete Stage ' + (index + 1) + '? Its entered rates, pressures and depths are discarded.',
        confirmLabel: 'Delete',
        danger: true
      }).then(function (confirmed) {
        if (!confirmed || !isCurrentContext(context)) return;
        var at = rowIndexFor(state.detail.flowback_stages, marker);
        if (at < 0) return;
        state.detail.flowback_stages.splice(at, 1);
        saveFlowback(true);
      });
    });
  });
}

function flushPendingSaves() {
  Object.keys(state.timers).forEach(function (key) {
    clearTimeout(state.timers[key]);
    delete state.timers[key];
  });
  Object.keys(state.fieldDrafts).forEach(function (key) {
    var draft = state.fieldDrafts[key];
    if (draftIsCurrent(draft) && draft.queuedVersion !== draft.version) {
      enqueueFieldDraft(key, draft.version);
    }
  });
  ['formations', 'flowback'].forEach(function (kind) {
    var draft = state.structureDrafts[kind];
    if (draftIsCurrent(draft) && draft.queuedVersion !== draft.version) {
      enqueueStructureDraft(kind, draft.version, true);
    }
  });
  return state.saveQueue.then(function () {
    if (hasFailedCurrentDrafts()) {
      setFeedback('Save failed', true);
      return false;
    }
    if (hasCurrentDrafts()) return flushPendingSaves();
    return true;
  });
}

function transition(action) {
  flushPendingSaves().then(function (saved) {
    if (!saved) return;
    var context = currentContext();
    var comment = value(state.detail.comments_key) || '';
    queueCommandSave(function () {
      return API.transitionBusinessPlan(context.projectId, context.detailSlug, action, comment);
    }, { context: context, rerender: true });
  });
}

// Well Summary gear menu (mirrors views/lead-summary.js's dismissal pair):
// bindDetail() re-runs on every render, but the two DOCUMENT-level listeners
// must not stack, so they are registered exactly once for the page's
// lifetime and resolve the popover by id at event time.
function bpeSummaryMenuIsOpen() {
  var menu = byId('bpe-summary-menu');
  return !!menu && !menu.classList.contains('hidden');
}

function closeBpeSummaryMenu() {
  var menu = byId('bpe-summary-menu');
  if (menu) menu.classList.add('hidden');
  var gear = byId('bpe-summary-gear');
  if (gear) gear.setAttribute('aria-expanded', 'false');
}

var bpeSummaryDismissWired = false;
function wireBpeSummaryDismissOnce() {
  if (bpeSummaryDismissWired) return;
  bpeSummaryDismissWired = true;
  document.addEventListener('click', function (event) {
    if (!bpeSummaryMenuIsOpen()) return;
    var menu = byId('bpe-summary-menu');
    var gear = byId('bpe-summary-gear');
    if ((menu && menu.contains(event.target)) || (gear && gear.contains(event.target))) return;
    closeBpeSummaryMenu();
  });
  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape' || !bpeSummaryMenuIsOpen()) return;
    closeBpeSummaryMenu();
    var gear = byId('bpe-summary-gear');
    if (gear) gear.focus();
  });
}

function bindDetail() {
  byId('bpe-back').addEventListener('click', refreshBusinessPlan);
  // The stage accordion, mirroring views/detail.js wireRailHandlers: clicking
  // a head opens that stage (the single-open sync closes whichever was open)
  // and clicking the OPEN head folds it away again.
  all('.rail-stage-head', byId('bpe-detail-view')).forEach(function (head) {
    head.addEventListener('click', function () {
      var stage = head.getAttribute('data-stage');
      state.railStage = openStageKey() === stage ? '' : stage;
      syncRailOpenState();
    });
  });
  all('.bpe-nav-item', byId('bpe-detail-view')).forEach(function (button) {
    button.addEventListener('click', function () { openBusinessPlanDetail(state.projectId, button.dataset.detailSlug); });
  });
  bindFieldInputs();
  bindFormationInputs();
  bindFlowbackInputs();
  var retry = byId('bpe-retry-save');
  if (retry) retry.addEventListener('click', function () {
    if (state.retryCommand && isCurrentContext(state.retryCommand.context)) {
      state.retryCommand.run();
      return;
    }
    Object.keys(state.fieldDrafts).forEach(function (key) {
      if (draftIsCurrent(state.fieldDrafts[key])) state.fieldDrafts[key].failed = false;
    });
    ['formations', 'flowback'].forEach(function (key) {
      if (draftIsCurrent(state.structureDrafts[key])) state.structureDrafts[key].failed = false;
    });
    flushPendingSaves();
  });
  all('[data-bpe-transition]', byId('bpe-detail-view')).forEach(function (button) {
    button.addEventListener('click', function () { transition(button.dataset.bpeTransition); });
  });
  var copy = byId('bpe-copy-folder');
  if (copy) copy.addEventListener('click', function () {
    navigator.clipboard.writeText((state.detail.folder || {}).path || '').then(function () {
      msg('Shared-folder path copied.', 'success');
    }).catch(function () { msg('The shared-folder path could not be copied.', 'error'); });
  });
  var assignee = byId('bpe-assignee');
  if (assignee) assignee.addEventListener('change', function () {
    var context = currentContext();
    var selected = assignee.value;
    queueCommandSave(function () { return API.assignBusinessPlan(context.projectId, context.detailSlug, selected); }, {
      context: context, rerender: true
    });
  });
  // The chip cycles Low -> Medium -> High -> Low, exactly as the maturation
  // shell's does. It is rendered `disabled` for anyone but a supervisor, so
  // the click can never fire for them.
  var priorityChipEl = byId('bpe-priority-chip');
  if (priorityChipEl) priorityChipEl.addEventListener('click', function () {
    var context = currentContext();
    var selected = nextLeadPriority(state.detail.project.priority);
    queueCommandSave(function () {
      return API.projectPriority(context.projectId, { priority: selected, changed_by: currentUserName() });
    }, {
      context: context,
      merge: false,
      onSuccess: function () {
        if (!isCurrentContext(context)) return;
        state.detail.project.priority = selected;
        // Repaint in place: the chip is the only thing this change moves, and
        // a full re-render would drop the focus ring off the button just
        // clicked.
        applyPriorityChip(byId('bpe-priority-chip'), selected,
                          (state.detail.role || currentRole()) === 'supervisor');
      }
    });
  });
  var gear = byId('bpe-summary-gear');
  var menu = byId('bpe-summary-menu');
  if (gear && menu) gear.addEventListener('click', function (event) {
    event.stopPropagation();
    var open = menu.classList.toggle('hidden') === false;
    gear.setAttribute('aria-expanded', String(open));
  });
  wireBpeSummaryDismissOnce();
  var edit = byId('bpe-edit-all');
  if (edit) edit.addEventListener('click', function () {
    closeBpeSummaryMenu();
    flushPendingSaves().then(function (saved) {
      if (!saved) return;
      var projectId = state.projectId;
      byId('bpe-detail-view').classList.add('hidden');
      import('./project-editor.js').then(function (module) { module.openProjectEditor(projectId); });
    });
  });
}

export function businessPlanTestHooks() {
  return {
    currentFilters: currentFilters,
    statusIcon: statusIcon,
    blankFlowbackStage: blankFlowbackStage,
    configureSaveDelay: function (delay) { state.saveDelay = delay == null ? 500 : delay; },
    flushPendingSaves: flushPendingSaves,
    state: state
  };
}
