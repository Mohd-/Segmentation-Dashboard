import { byId, all, esc, msg } from '../dom.js';
import { API } from '../api.js';
import { currentUserName } from '../state.js';
import { canTransitionPhase, promoteProject, recallProject } from './transitions.js';
import { openProjectEditor } from './project-editor.js';
// Deliberate module cycle (portfolio → pipeline → portfolio): refreshAllBoards
// is a hoisted function declaration and only called from event handlers, same
// as the existing portfolio → project-editor → pipeline chain.
import { refreshAllBoards } from './pipeline.js';

// Exactly the 8 analysis columns, in this order. `filter` selects the
// column-filter control rendered in the second thead row ('text' = substring
// input, 'multi' = distinct-value checklist popover (multi-select), null = no
// column filter -- BP Year is covered by the toolbar select; Mean OGIP / Total
// CoS get none per spec). `numeric` selects Number()-based sort with blanks-last
// in both directions instead of localeCompare. The former `fluid` column is
// now `status` (fluid value, or 'Staked'/'Proposed' for undrilled records --
// see reporting.record_status); rows also carry `pipeline_type` and
// `is_mature_lead`, consumed only by the Actions cell / stats below.
var COLUMNS = [
  { key: 'well_name', label: 'Well Name', numeric: false, filter: 'text' },
  { key: 'gas_field', label: 'Gas Field', numeric: false, filter: 'multi' },
  { key: 'seismic_block', label: 'Seismic Block', numeric: false, filter: 'text' },
  { key: 'classification', label: 'Classification', numeric: false, filter: 'multi' },
  { key: 'year', label: 'BP Year', numeric: true, filter: null },
  { key: 'status', label: 'Status', numeric: false, filter: 'multi' },
  { key: 'mean_ogip', label: 'Mean OGIP (BCF)', numeric: true, filter: null },
  { key: 'total_cos', label: 'Total CoS (%)', numeric: true, filter: null }
];

// Module-level state per spec: fetched once per refreshPortfolio(), then
// sort/filter changes re-render locally without refetching.
var state = { rows: [], sortKey: null, sortDir: 1, filters: {} };

export function formatNumber(value) {
  var numeric = Number(value);
  if (!isFinite(numeric)) return '0.0';
  return numeric.toLocaleString(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
}

// Stats follow the VISIBLE rowset (server + column filters applied) instead
// of the fetch payload's server-computed summary -- mirrors the rounding
// reporting.py does for the unfiltered case (round(sum, 1)), and the same
// is_mature_lead split reporting.get_portfolio_rows uses for its summary
// object (business_plan_wells / mature_leads / cumulative_ogip).
function renderPortfolioStats(rows) {
  var element = byId('portfolio-stats');
  if (!element) return;
  var bpWells = 0;
  var matureLeads = 0;
  var cumulativeOgip = 0;
  rows.forEach(function (row) {
    if (row.is_mature_lead) matureLeads += 1; else bpWells += 1;
    var value = Number(row.mean_ogip);
    if (isFinite(value)) cumulativeOgip += value;
  });
  cumulativeOgip = Math.round(cumulativeOgip * 10) / 10;
  element.innerHTML =
    '<div class="portfolio-stat"><small>Business Plan Wells</small><b>' + esc(bpWells) + '</b></div>' +
    '<div class="portfolio-stat"><small>Mature Leads</small><b>' + esc(matureLeads) + '</b></div>' +
    '<div class="portfolio-stat"><small>Cumulative OGIP (BCF)</small><b>' + esc(formatNumber(cumulativeOgip)) + '</b></div>';
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
    ? '<button type="button" class="ghost portfolio-promote" data-project-id="' + esc(row.project_id) + '" data-project-name="' + esc(row.well_name || '') + '">Promote…</button>'
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
    '<td>' + esc(row.mean_ogip || '') + '</td>' +
    '<td>' + esc(row.total_cos || '') + '</td>' +
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

function updateSortIndicators(table) {
  all('th[data-key]', table).forEach(function (th) {
    var isSorted = th.getAttribute('data-key') === state.sortKey;
    th.classList.toggle('sorted-asc', isSorted && state.sortDir === 1);
    th.classList.toggle('sorted-desc', isSorted && state.sortDir === -1);
    th.setAttribute('aria-sort', isSorted ? (state.sortDir === 1 ? 'ascending' : 'descending') : 'none');
  });
}

// Trigger label for a 'multi' column: "All" when nothing is picked, the value
// itself for exactly one, otherwise "N selected".
function filterLabel(key) {
  var selected = state.filters[key];
  if (!selected || !selected.length) return 'All';
  if (selected.length === 1) return selected[0];
  return selected.length + ' selected';
}

function renderFilterCell(col) {
  if (col.filter === 'text') {
    var textValue = state.filters[col.key] || '';
    return '<th class="portfolio-filter-cell"><input type="text" class="portfolio-filter-input" data-key="' + col.key +
      '" value="' + esc(textValue) + '" placeholder="Filter…" aria-label="Filter ' + esc(col.label) + '"></th>';
  }
  if (col.filter === 'multi') {
    var selected = state.filters[col.key] || [];
    var optionsHtml = distinctValues(col.key).map(function (value) {
      var checked = selected.indexOf(value) >= 0 ? ' checked' : '';
      return '<label class="portfolio-filter-option"><input type="checkbox" value="' + esc(value) + '"' + checked +
        '><span>' + esc(value) + '</span></label>';
    }).join('');
    // The trigger button carries data-key (not the th) so the sort handler's
    // th[data-key] lookup on the header row never picks up filter cells.
    return '<th class="portfolio-filter-cell portfolio-filter-multi">' +
      '<button type="button" class="portfolio-filter-trigger" data-key="' + col.key +
      '" aria-haspopup="true" aria-expanded="false" aria-label="Filter ' + esc(col.label) + '">' +
      '<span class="portfolio-filter-trigger-label">' + esc(filterLabel(col.key)) + '</span>' +
      '<span class="portfolio-filter-caret" aria-hidden="true">▾</span></button>' +
      '<div class="portfolio-filter-popover" hidden>' +
      '<div class="portfolio-filter-clear-row"><button type="button" class="portfolio-filter-clear">Clear</button></div>' +
      optionsHtml + '</div></th>';
  }
  return '<th class="portfolio-filter-cell"></th>';
}

// Only one checklist popover is open at a time; closing simply hides every one
// currently in the DOM (queried live, so it survives renderHead rebuilds).
function closePortfolioPopovers() {
  all('.portfolio-filter-popover', document).forEach(function (popover) { popover.hidden = true; });
  all('.portfolio-filter-trigger', document).forEach(function (trigger) { trigger.setAttribute('aria-expanded', 'false'); });
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
    // Clicks on a trigger (its own handler toggles) or inside a popover
    // (checkbox / Clear) must not close it; everything else dismisses.
    if (target.closest && (target.closest('.portfolio-filter-trigger') || target.closest('.portfolio-filter-popover'))) return;
    closePortfolioPopovers();
  });
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') closePortfolioPopovers();
  });
}

// Rebuilds thead (header row + filter row) -- only called when the fetched
// dataset changes, so typing in a filter input never fights the DOM for
// focus (filter/sort changes only call renderBody).
function renderHead(table) {
  // Supervisor-only trailing Actions column (per-row Recall): one unsortable
  // header th (no data-key) and one empty filter cell to keep the rows aligned.
  var actionsHead = canTransitionPhase() ? '<th class="portfolio-actions-th">Actions</th>' : '';
  var actionsFilter = canTransitionPhase() ? '<th class="portfolio-filter-cell"></th>' : '';
  table.innerHTML =
    '<thead>' +
    '<tr>' + COLUMNS.map(function (col) {
      return '<th data-key="' + col.key + '" aria-sort="none"><button type="button" class="th-sort">' + esc(col.label) + '</button></th>';
    }).join('') + actionsHead + '</tr>' +
    '<tr class="portfolio-filter-row">' + COLUMNS.map(renderFilterCell).join('') + actionsFilter + '</tr>' +
    '</thead><tbody></tbody>';
  // Handler lives on the th, not the inner .th-sort button: the CSS makes the
  // whole cell (padding and the ▲/▼ zone included) read as clickable, and
  // button clicks / keyboard activation bubble up to the th anyway.
  all('th[data-key]', table).forEach(function (th) {
    th.addEventListener('click', function () {
      var key = th.getAttribute('data-key');
      if (state.sortKey !== key) { state.sortKey = key; state.sortDir = 1; }
      else if (state.sortDir === 1) { state.sortDir = -1; }
      else { state.sortKey = null; state.sortDir = 1; }
      updateSortIndicators(table);
      renderBody(table);
    });
  });
  all('.portfolio-filter-input', table).forEach(function (input) {
    input.addEventListener('input', function () {
      state.filters[input.getAttribute('data-key')] = input.value;
      renderBody(table);
    });
  });
  // Multi-select checklist popovers. Toggling a checkbox re-renders only the
  // tbody + stats and rewrites the trigger label in place -- the thead is left
  // intact so the open popover keeps its state (see renderHead's contract).
  all('.portfolio-filter-multi', table).forEach(function (cell) {
    var trigger = cell.querySelector('.portfolio-filter-trigger');
    var popover = cell.querySelector('.portfolio-filter-popover');
    var labelEl = cell.querySelector('.portfolio-filter-trigger-label');
    var key = trigger.getAttribute('data-key');
    trigger.addEventListener('click', function () {
      var wasOpen = !popover.hidden;
      closePortfolioPopovers();
      if (!wasOpen) { popover.hidden = false; trigger.setAttribute('aria-expanded', 'true'); }
    });
    all('input[type="checkbox"]', popover).forEach(function (box) {
      box.addEventListener('change', function () {
        var selected = state.filters[key] || [];
        if (box.checked) {
          if (selected.indexOf(box.value) < 0) selected = selected.concat([box.value]);
        } else {
          selected = selected.filter(function (value) { return value !== box.value; });
        }
        state.filters[key] = selected;
        labelEl.textContent = filterLabel(key);
        renderBody(table);
      });
    });
    cell.querySelector('.portfolio-filter-clear').addEventListener('click', function () {
      state.filters[key] = [];
      all('input[type="checkbox"]', popover).forEach(function (box) { box.checked = false; });
      labelEl.textContent = filterLabel(key);
      renderBody(table);
    });
  });
  wirePortfolioDismiss();
  updateSortIndicators(table);
}

export function refreshPortfolio() {
  var year = byId('portfolio-year-filter').value || 'All';
  var activity = byId('portfolio-activity-filter').value || 'All';
  API.portfolioRows({ year: year, activity: activity }).then(function (payload) {
    state.rows = (payload && payload.rows) || [];
    state.filters = {};
    var table = byId('portfolio-table');
    if (!table) return;
    renderHead(table);
    renderBody(table);
  }).catch(function (error) { msg(error.message, 'error'); });
}
