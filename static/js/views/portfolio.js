import { byId, all, esc, msg } from '../dom.js';
import { API } from '../api.js';
import { openDetail } from './detail.js';

// WS7: exactly the 8 analysis columns, in this order. `filter` selects the
// column-filter control rendered in the second thead row ('text' = substring
// input, 'select' = distinct-value dropdown, null = no column filter -- BP
// Year is covered by the toolbar select; Mean OGIP / Total CoS get none per
// spec). `numeric` selects Number()-based sort with blanks-last in both
// directions instead of localeCompare.
var COLUMNS = [
  { key: 'well_name', label: 'Well Name', numeric: false, filter: 'text' },
  { key: 'gas_field', label: 'Gas Field', numeric: false, filter: 'select' },
  { key: 'seismic_block', label: 'Seismic Block', numeric: false, filter: 'text' },
  { key: 'classification', label: 'Classification', numeric: false, filter: 'select' },
  { key: 'year', label: 'BP Year', numeric: true, filter: null },
  { key: 'fluid', label: 'Fluid', numeric: false, filter: 'select' },
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
// reporting.py does for the unfiltered case (round(sum, 1)).
function renderPortfolioStats(rows) {
  var element = byId('portfolio-stats');
  if (!element) return;
  var cumulativeOgip = 0;
  rows.forEach(function (row) {
    var value = Number(row.mean_ogip);
    if (isFinite(value)) cumulativeOgip += value;
  });
  cumulativeOgip = Math.round(cumulativeOgip * 10) / 10;
  element.innerHTML =
    '<div class="portfolio-stat"><small>Business Plan Wells</small><b>' + esc(rows.length) + '</b></div>' +
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
      if (!col.filter || !filterValue) return true;
      var cellValue = String(row[col.key] == null ? '' : row[col.key]);
      if (col.filter === 'text') return cellValue.toLowerCase().indexOf(String(filterValue).toLowerCase()) >= 0;
      return cellValue === filterValue;
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
  return '<tr>' +
    '<td><a href="#" class="well-link" data-project-id="' + esc(row.project_id) + '">' + esc(row.well_name || '') + '</a></td>' +
    '<td>' + esc(row.gas_field || '') + '</td>' +
    '<td>' + esc(row.seismic_block || '') + '</td>' +
    '<td>' + esc(row.classification || '') + '</td>' +
    '<td>' + esc(row.year || '') + '</td>' +
    '<td>' + esc(row.fluid || '') + '</td>' +
    '<td>' + esc(row.mean_ogip || '') + '</td>' +
    '<td>' + esc(row.total_cos || '') + '</td>' +
    '</tr>';
}

function renderBody(table) {
  var tbody = table.querySelector('tbody');
  if (!tbody) return;
  var rows = visibleRows();
  tbody.innerHTML = rows.length ? rows.map(rowMarkup).join('') :
    '<tr><td colspan="' + COLUMNS.length + '" class="empty-state">No records yet.</td></tr>';
  all('.well-link', tbody).forEach(function (link) {
    link.addEventListener('click', function (event) {
      event.preventDefault();
      openDetail(Number(link.getAttribute('data-project-id')), 'bp');
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

function renderFilterCell(col) {
  if (col.filter === 'text') {
    var textValue = state.filters[col.key] || '';
    return '<th class="portfolio-filter-cell"><input type="text" class="portfolio-filter-input" data-key="' + col.key +
      '" value="' + esc(textValue) + '" placeholder="Filter…" aria-label="Filter ' + esc(col.label) + '"></th>';
  }
  if (col.filter === 'select') {
    var current = state.filters[col.key] || 'All';
    var optionsHtml = '<option value="All">All</option>' + distinctValues(col.key).map(function (value) {
      return '<option value="' + esc(value) + '"' + (value === current ? ' selected' : '') + '>' + esc(value) + '</option>';
    }).join('');
    return '<th class="portfolio-filter-cell"><select class="portfolio-filter-select" data-key="' + col.key +
      '" aria-label="Filter ' + esc(col.label) + '">' + optionsHtml + '</select></th>';
  }
  return '<th class="portfolio-filter-cell"></th>';
}

// Rebuilds thead (header row + filter row) -- only called when the fetched
// dataset changes, so typing in a filter input never fights the DOM for
// focus (filter/sort changes only call renderBody).
function renderHead(table) {
  table.innerHTML =
    '<thead>' +
    '<tr>' + COLUMNS.map(function (col) {
      return '<th data-key="' + col.key + '" aria-sort="none"><button type="button" class="th-sort">' + esc(col.label) + '</button></th>';
    }).join('') + '</tr>' +
    '<tr class="portfolio-filter-row">' + COLUMNS.map(renderFilterCell).join('') + '</tr>' +
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
  all('.portfolio-filter-select', table).forEach(function (select) {
    select.addEventListener('change', function () {
      var key = select.getAttribute('data-key');
      state.filters[key] = select.value === 'All' ? '' : select.value;
      renderBody(table);
    });
  });
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
