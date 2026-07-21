import { byId, all, esc, msg, fillSelect, fmtNum } from '../dom.js';
import { API } from '../api.js';
import { FLUID_TYPES } from '../schema.js';
import { currentUserName } from '../state.js';
import { canTransitionPhase, promoteProject, recallProject } from './transitions.js';
import { openProjectEditor } from './project-editor.js';
// Deliberate module cycle (portfolio → pipeline → portfolio): refreshAllBoards
// is a hoisted function declaration and only called from event handlers, same
// as the existing portfolio → project-editor → pipeline chain.
import { refreshAllBoards } from './pipeline.js';

// Exactly the 8 analysis columns, in this order. `filter` selects the
// column-filter control rendered in the second thead row: 'text' = substring
// input, 'multi' = distinct-value checklist popover (multi-select; BP Year's
// discrete values fit this, layered over the toolbar's server-side year
// select), 'range' = numeric min/max pair for the continuous measures (Mean
// OGIP / Total CoS). `numeric` selects Number()-based sort with blanks-last
// in both directions instead of localeCompare. The former `fluid` column is
// now `status` (fluid value, or 'Staked'/'Proposed' for undrilled records --
// see reporting.record_status); rows also carry `pipeline_type` and
// `is_mature_lead`, consumed only by the Actions cell / stats below.
// The full status vocabulary (reporting.record_status): every fluid a record
// can carry plus the two undrilled markers. The Status checklist always offers
// ALL of these -- a data-driven list alone hides whichever statuses happen to
// be absent from the current rowset, reading as if they don't exist.
var STATUS_OPTIONS = FLUID_TYPES.filter(function (value) { return value !== ''; })
  .concat(['Proposed', 'Staked']);

var COLUMNS = [
  { key: 'well_name', label: 'Well Name', numeric: false, filter: 'text' },
  { key: 'gas_field', label: 'Field', numeric: false, filter: 'multi' },
  { key: 'seismic_block', label: 'Seismic Block', numeric: false, filter: 'text' },
  { key: 'classification', label: 'Classification', numeric: false, filter: 'multi' },
  { key: 'year', label: 'BP Year', numeric: true, filter: 'multi' },
  { key: 'status', label: 'Status', numeric: false, filter: 'multi', options: STATUS_OPTIONS },
  { key: 'mean_ogip', label: 'Mean OGIP (BCF)', numeric: true, filter: 'range' },
  { key: 'total_cos', label: 'Total CoS (%)', numeric: true, filter: 'range' }
];

// Module-level state per spec: fetched once per refreshPortfolio(), then
// sort/filter changes re-render locally without refetching.
var state = { rows: [], sortKey: null, sortDir: 1, filters: {} };

// Stats follow the VISIBLE rowset (server + column filters applied) instead
// of the fetch payload's server-computed summary, using the same
// is_mature_lead split reporting.get_portfolio_rows uses for its summary
// object (business_plan_wells / mature_leads).
function renderPortfolioStats(rows) {
  var element = byId('portfolio-stats');
  if (!element) return;
  var bpWells = 0;
  var matureLeads = 0;
  rows.forEach(function (row) {
    if (row.is_mature_lead) matureLeads += 1; else bpWells += 1;
  });
  element.innerHTML =
    '<div class="portfolio-stat"><small>Business Plan Wells</small><b>' + esc(bpWells) + '</b></div>' +
    '<div class="portfolio-stat"><small>Mature Leads</small><b>' + esc(matureLeads) + '</b></div>';
}

function distinctValues(key) {
  var seen = {};
  var values = [];
  state.rows.forEach(function (row) {
    var value = row[key];
    if (value === null || value === undefined || value === '') return;
    var text = String(value);
    if (!seen[text]) { seen[text] = true; values.push(text); }
  });
  values.sort(function (a, b) { return a.localeCompare(b); });
  return values;
}

// Checklist options for a 'multi' column: the distinct data values, unioned
// with the column's fixed vocabulary when it declares one (Status) so every
// legal value is always offered, even with no matching row yet. Numeric
// columns (BP Year) sort by value, not lexically.
function filterValues(col) {
  var values = distinctValues(col.key);
  (col.options || []).forEach(function (value) {
    if (values.indexOf(value) < 0) values.push(value);
  });
  values.sort(col.numeric
    ? function (a, b) { return Number(a) - Number(b); }
    : function (a, b) { return a.localeCompare(b); });
  return values;
}

function applyFilters(rows) {
  return rows.filter(function (row) {
    return COLUMNS.every(function (col) {
      var filterValue = state.filters[col.key];
      if (!col.filter || filterValue == null) return true;
      var cellValue = String(row[col.key] == null ? '' : row[col.key]);
      if (col.filter === 'text') {
        if (!filterValue) return true;
        return cellValue.toLowerCase().indexOf(String(filterValue).toLowerCase()) >= 0;
      }
      // 'range': {min, max} strings; an unset bound doesn't constrain. Once
      // either bound is set, blank/non-numeric cells drop out (a row with no
      // value can't satisfy a numeric constraint).
      if (col.filter === 'range') {
        var hasMin = filterValue.min !== '' && isFinite(Number(filterValue.min));
        var hasMax = filterValue.max !== '' && isFinite(Number(filterValue.max));
        if (!hasMin && !hasMax) return true;
        var numeric = Number(cellValue);
        if (cellValue === '' || !isFinite(numeric)) return false;
        return (!hasMin || numeric >= Number(filterValue.min)) &&
          (!hasMax || numeric <= Number(filterValue.max));
      }
      // 'multi': array of selected values; an empty/absent array = no filter.
      return !filterValue.length || filterValue.indexOf(cellValue) >= 0;
    });
  });
}

function compareRows(a, b, col) {
  if (col.numeric) {
    var an = Number(a[col.key]);
    var bn = Number(b[col.key]);
    var aBlank = a[col.key] === '' || a[col.key] == null || !isFinite(an);
    var bBlank = b[col.key] === '' || b[col.key] == null || !isFinite(bn);
    if (aBlank && bBlank) return 0;
    if (aBlank) return 1;  // blanks/non-numeric always sort last, both directions
    if (bBlank) return -1;
    return (an - bn) * state.sortDir;
  }
  var at = String(a[col.key] == null ? '' : a[col.key]);
  var bt = String(b[col.key] == null ? '' : b[col.key]);
  return at.localeCompare(bt) * state.sortDir;
}

function visibleRows() {
  var rows = applyFilters(state.rows);
  if (!state.sortKey) return rows;
  var col = COLUMNS.filter(function (c) { return c.key === state.sortKey; })[0];
  if (!col) return rows;
  var sorted = rows.slice();
  sorted.sort(function (a, b) { return compareRows(a, b, col); });
  return sorted;
}

function rowMarkup(row) {
  // Supervisor-only trailing Actions cell: mature leads get Promote (year
  // prompt + snapshot via transitions.js promoteProject), BP wells keep the
  // existing Recall (transitions.js confirm + PATCH /flags).
  var actionButton = row.is_mature_lead
    ? '<button type="button" class="ghost success-outline portfolio-promote" data-project-id="' + esc(row.project_id) + '" data-project-name="' + esc(row.well_name || '') + '">Promote…</button>'
    : '<button type="button" class="ghost danger-outline portfolio-recall" data-project-id="' + esc(row.project_id) + '" data-project-name="' + esc(row.well_name || '') + '">Recall</button>';
  var actionsCell = canTransitionPhase()
    ? '<td class="portfolio-actions-cell">' + actionButton + '</td>'
    : '';
  return '<tr>' +
    '<td><a href="#" class="well-link" data-project-id="' + esc(row.project_id) + '">' + esc(row.well_name || '') + '</a></td>' +
    '<td>' + esc(row.gas_field || '') + '</td>' +
    '<td>' + esc(row.seismic_block || '') + '</td>' +
    '<td>' + esc(row.classification || '') + '</td>' +
    '<td>' + esc(row.year || '') + '</td>' +
    '<td>' + esc(row.status || '') + '</td>' +
    '<td>' + esc(fmtNum(row.mean_ogip) || '') + '</td>' +
    '<td>' + esc(fmtNum(row.total_cos) || '') + '</td>' +
    actionsCell +
    '</tr>';
}

function renderBody(table) {
  var tbody = table.querySelector('tbody');
  if (!tbody) return;
  var rows = visibleRows();
  var columnCount = COLUMNS.length + (canTransitionPhase() ? 1 : 0);
  tbody.innerHTML = rows.length ? rows.map(rowMarkup).join('') :
    '<tr><td colspan="' + columnCount + '" class="empty-state">No records yet.</td></tr>';
  all('.well-link', tbody).forEach(function (link) {
    link.addEventListener('click', function (event) {
      event.preventDefault();
      openProjectEditor(Number(link.getAttribute('data-project-id')));
    });
  });
  all('.portfolio-recall', tbody).forEach(function (button) {
    button.addEventListener('click', function () {
      var project = {
        project_id: Number(button.getAttribute('data-project-id')),
        project_name: button.getAttribute('data-project-name') || ''
      };
      recallProject(project, currentUserName()).then(function (result) {
        if (result === null) return; // dialog cancelled
        refreshAllBoards(); // includes refreshPortfolio, so the row drops out
        msg('Recalled to lead phase.', 'success');
      }).catch(function (error) { msg(error.message, 'error'); });
    });
  });
  all('.portfolio-promote', tbody).forEach(function (button) {
    button.addEventListener('click', function () {
      var project = {
        project_id: Number(button.getAttribute('data-project-id')),
        project_name: button.getAttribute('data-project-name') || ''
      };
      // No prospectTasks here: portfolio mature leads are 100% approved by
      // definition (that's how they entered the portfolio), so promoteProject
      // omits the N-of-M line/warning when the argument is falsy.
      promoteProject(project, null, currentUserName()).then(function (result) {
        if (result === null) return; // dialog cancelled
        refreshAllBoards(); // includes refreshPortfolio, so the row switches to BP + Recall
        msg('Promoted to BP well.', 'success');
      }).catch(function (error) { msg(error.message, 'error'); });
    });
  });
  renderPortfolioStats(rows);
}

function colByKey(key) {
  return COLUMNS.filter(function (c) { return c.key === key; })[0];
}

// Sort-direction labels read naturally per column type: value order for the
// numeric measures, alphabetical for text/categorical columns.
function sortLabels(col) {
  return col.numeric
    ? { asc: 'Low → High', desc: 'High → Low' }
    : { asc: 'A → Z', desc: 'Z → A' };
}

function isSortActive(col, dir) {
  return state.sortKey === col.key && state.sortDir === dir;
}

function isFilledBound(value) { return value !== '' && value != null; }

// Whether a column currently constrains the rowset -- drives the header's
// "filtered" dot and is the single source of truth for the filter marks.
function columnIsFiltered(col) {
  var filterValue = state.filters[col.key];
  if (filterValue == null) return false;
  if (col.filter === 'range') return isFilledBound(filterValue.min) || isFilledBound(filterValue.max);
  if (col.filter === 'multi') return !!(filterValue && filterValue.length);
  return !!filterValue; // text
}

// The Filter section of a column's pop-over, shaped to the column type:
// free-text "contains", a numeric min/max pair, or a scrolling checklist.
function filterGroupMarkup(col) {
  if (col.filter === 'text') {
    var textValue = state.filters[col.key] || '';
    return '<label class="pf-field-label">Contains' +
      '<input type="text" class="portfolio-filter-input" data-key="' + col.key + '" value="' + esc(textValue) +
      '" placeholder="Type to filter…" aria-label="Filter ' + esc(col.label) + '"></label>';
  }
  if (col.filter === 'range') {
    var range = state.filters[col.key] || { min: '', max: '' };
    return '<div class="portfolio-filter-range">' +
      '<input type="number" class="portfolio-filter-input" data-key="' + col.key + '" data-bound="min" value="' +
      esc(range.min) + '" placeholder="Min" aria-label="Minimum ' + esc(col.label) + '">' +
      '<input type="number" class="portfolio-filter-input" data-key="' + col.key + '" data-bound="max" value="' +
      esc(range.max) + '" placeholder="Max" aria-label="Maximum ' + esc(col.label) + '">' +
      '</div>';
  }
  // 'multi': the same checklist as before, now living inside the column menu.
  var selected = state.filters[col.key] || [];
  var optionsHtml = filterValues(col).map(function (value) {
    var checked = selected.indexOf(value) >= 0 ? ' checked' : '';
    return '<label class="portfolio-filter-option"><input type="checkbox" value="' + esc(value) + '"' + checked +
      '><span>' + esc(value) + '</span></label>';
  }).join('');
  return '<div class="pf-multi-list">' + optionsHtml + '</div>';
}

// One column header: a title button that opens a pop-over combining Sort
// (two directional options) and Filter (type-appropriate). The last two
// (right-most) columns anchor their menu to the right edge so it can't be
// clipped by the panel's overflow.
function columnHeaderMarkup(col, index) {
  var labels = sortLabels(col);
  var alignRight = index >= COLUMNS.length - 2;
  var sortMark = state.sortKey === col.key ? (state.sortDir === 1 ? '▲' : '▼') : '';
  var filtered = columnIsFiltered(col);
  return '<th data-key="' + col.key + '" aria-sort="none">' +
    '<button type="button" class="pf-col-trigger" aria-haspopup="true" aria-expanded="false">' +
      '<span class="pf-col-label">' + esc(col.label) + '</span>' +
      '<span class="pf-col-affix">' +
        '<span class="pf-sort-mark" aria-hidden="true">' + sortMark + '</span>' +
        '<span class="pf-filter-mark' + (filtered ? ' is-on' : '') + '" aria-hidden="true"></span>' +
        '<span class="pf-caret" aria-hidden="true">▾</span>' +
      '</span>' +
    '</button>' +
    '<div class="pf-popover' + (alignRight ? ' pf-popover--right' : '') + '" hidden>' +
      '<div class="pf-pop-group">' +
        '<div class="pf-pop-title">Sort</div>' +
        '<button type="button" class="pf-sort-opt' + (isSortActive(col, 1) ? ' is-active' : '') + '" data-dir="1">' + labels.asc + '</button>' +
        '<button type="button" class="pf-sort-opt' + (isSortActive(col, -1) ? ' is-active' : '') + '" data-dir="-1">' + labels.desc + '</button>' +
      '</div>' +
      '<div class="pf-pop-group">' +
        '<div class="pf-pop-title">Filter</div>' +
        filterGroupMarkup(col) +
      '</div>' +
      '<div class="pf-pop-foot"><button type="button" class="pf-clear-filter">Clear filter</button></div>' +
    '</div></th>';
}

// Only one column menu is open at a time; closing hides every one currently in
// the DOM (queried live, so it survives renderHead rebuilds).
function closePortfolioPopovers() {
  all('.pf-popover', document).forEach(function (popover) { popover.hidden = true; });
  all('.pf-col-trigger', document).forEach(function (trigger) { trigger.setAttribute('aria-expanded', 'false'); });
}

// Outside-click + Escape dismissal is wired to the document ONCE: renderHead can
// run again after a refetch, but these listeners key off live DOM queries so a
// single registration keeps working across rebuilds.
var dismissWired = false;
function wirePortfolioDismiss() {
  if (dismissWired) return;
  dismissWired = true;
  document.addEventListener('click', function (event) {
    var target = event.target;
    // Clicks on a column trigger (its own handler toggles) or anywhere inside a
    // menu (sort option, filter input, checkbox, Clear) must not close it.
    if (target.closest && (target.closest('.pf-col-trigger') || target.closest('.pf-popover'))) return;
    closePortfolioPopovers();
  });
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') closePortfolioPopovers();
  });
}

// Reflect current sort/filter state onto the (persistent) header without a
// thead rebuild: aria-sort, the inline ▲/▼ mark, the active sort option, and
// the per-column "filtered" dot. Called after every sort/filter change so the
// open menu keeps its DOM (and any focused input) intact.
function refreshHeaderState(table) {
  all('th[data-key]', table).forEach(function (th) {
    var key = th.getAttribute('data-key');
    var col = colByKey(key);
    var isSorted = key === state.sortKey;
    th.classList.toggle('sorted-asc', isSorted && state.sortDir === 1);
    th.classList.toggle('sorted-desc', isSorted && state.sortDir === -1);
    th.setAttribute('aria-sort', isSorted ? (state.sortDir === 1 ? 'ascending' : 'descending') : 'none');
    var sortMark = th.querySelector('.pf-sort-mark');
    if (sortMark) sortMark.textContent = isSorted ? (state.sortDir === 1 ? '▲' : '▼') : '';
    all('.pf-sort-opt', th).forEach(function (opt) {
      opt.classList.toggle('is-active', isSorted && Number(opt.getAttribute('data-dir')) === state.sortDir);
    });
    var filtered = col && columnIsFiltered(col);
    var filterMark = th.querySelector('.pf-filter-mark');
    if (filterMark) filterMark.classList.toggle('is-on', !!filtered);
  });
}

// Rebuilds thead -- only called when the fetched dataset changes, so typing in
// a filter input or an open menu never fights the DOM for focus (sort/filter
// changes only re-render tbody + refreshHeaderState in place).
function renderHead(table) {
  // Supervisor-only trailing Actions column (per-row Recall/Promote): one
  // unsortable header th with no data-key, so it grows no column menu.
  var actionsHead = canTransitionPhase() ? '<th class="portfolio-actions-th">Actions</th>' : '';
  table.innerHTML =
    '<thead><tr>' + COLUMNS.map(columnHeaderMarkup).join('') + actionsHead + '</tr></thead><tbody></tbody>';

  all('th[data-key]', table).forEach(function (th) {
    var key = th.getAttribute('data-key');
    var trigger = th.querySelector('.pf-col-trigger');
    var popover = th.querySelector('.pf-popover');
    // Title toggles this column's menu (closing any other first).
    trigger.addEventListener('click', function () {
      var wasOpen = !popover.hidden;
      closePortfolioPopovers();
      if (!wasOpen) { popover.hidden = false; trigger.setAttribute('aria-expanded', 'true'); }
    });
    // Sort options: pick a direction, or click the active one again to clear.
    all('.pf-sort-opt', th).forEach(function (opt) {
      opt.addEventListener('click', function () {
        var dir = Number(opt.getAttribute('data-dir'));
        if (state.sortKey === key && state.sortDir === dir) { state.sortKey = null; state.sortDir = 1; }
        else { state.sortKey = key; state.sortDir = dir; }
        refreshHeaderState(table);
        renderBody(table);
      });
    });
    // One handler covers both filter-input shapes: a plain text input stores its
    // string; a range input (marked by data-bound="min"/"max") stores into its
    // column's {min, max} pair.
    all('.portfolio-filter-input', th).forEach(function (input) {
      input.addEventListener('input', function () {
        var bound = input.getAttribute('data-bound');
        if (bound) {
          var range = state.filters[key] || { min: '', max: '' };
          range[bound] = input.value;
          state.filters[key] = range;
        } else {
          state.filters[key] = input.value;
        }
        refreshHeaderState(table);
        renderBody(table);
      });
    });
    // Multi-select checklist: toggling a checkbox re-renders only tbody + stats
    // and refreshes the header marks; the thead is left intact so the open menu
    // keeps its state.
    all('.portfolio-filter-option input[type="checkbox"]', th).forEach(function (box) {
      box.addEventListener('change', function () {
        var selected = state.filters[key] || [];
        if (box.checked) {
          if (selected.indexOf(box.value) < 0) selected = selected.concat([box.value]);
        } else {
          selected = selected.filter(function (value) { return value !== box.value; });
        }
        state.filters[key] = selected;
        refreshHeaderState(table);
        renderBody(table);
      });
    });
    // Clear filter (this column only): drop its filter state and reset controls.
    th.querySelector('.pf-clear-filter').addEventListener('click', function () {
      delete state.filters[key];
      all('.portfolio-filter-input', th).forEach(function (input) { input.value = ''; });
      all('.portfolio-filter-option input[type="checkbox"]', th).forEach(function (box) { box.checked = false; });
      refreshHeaderState(table);
      renderBody(table);
    });
  });
  wirePortfolioDismiss();
  refreshHeaderState(table);
}

// Distinct BP years present in a rowset, sorted ascending. Rows without a
// year (mature leads) contribute nothing -- they only show under 'All',
// matching the backend filter (reporting.get_portfolio_rows).
function distinctYears(rows) {
  var seen = {};
  var years = [];
  rows.forEach(function (row) {
    if (row.year === null || row.year === undefined || row.year === '') return;
    var text = String(row.year);
    if (!seen[text]) { seen[text] = true; years.push(text); }
  });
  years.sort(function (a, b) { return Number(a) - Number(b); });
  return years;
}

export function refreshPortfolio() {
  var year = byId('portfolio-year-filter').value || 'All';
  var activity = byId('portfolio-activity-filter').value || 'All';
  API.portfolioRows({ year: year, activity: activity }).then(function (payload) {
    state.rows = (payload && payload.rows) || [];
    state.filters = {};
    // The year select's options track the data: a fully unfiltered fetch
    // (both selects at 'All' -- the boot state) rebuilds them from the
    // distinct years actually present, so imported historical wells
    // (business_plan_year < 2026) are selectable. Filtered fetches never
    // rebuild (a year-filtered rowset carries one year; an activity-filtered
    // one can hide years) and an empty portfolio keeps the boot-time
    // 2026-2040 default (main.js). fillSelect preserves the selection.
    if (year === 'All' && activity === 'All') {
      var years = distinctYears(state.rows);
      if (years.length) fillSelect(byId('portfolio-year-filter'), years, true);
    }
    var table = byId('portfolio-table');
    if (!table) return;
    renderHead(table);
    renderBody(table);
  }).catch(function (error) { msg(error.message, 'error'); });
}
