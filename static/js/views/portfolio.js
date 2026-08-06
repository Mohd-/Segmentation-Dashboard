import { byId, all, esc, msg, fmtNum, isFilled } from '../dom.js';
import { ICONS } from '../icons.js';
import { API } from '../api.js';
import { FLUID_TYPES } from '../schema.js';
import { currentUserName } from '../state.js';
import { canTransitionPhase, promoteProject, recallProject } from './transitions.js';
import { openDetail } from './detail.js';
// Deliberate module cycle (portfolio → pipeline → portfolio): refreshAllBoards
// is a hoisted function declaration and only called from event handlers, same
// as the existing detail → pipeline → portfolio chain.
import { refreshAllBoards } from './pipeline.js';
import { renderResourceBar, setCrossPlotRows, quadrantOf } from './portfolio-analysis.js';

// Exactly the 8 analysis columns, in this order. `filter` selects the
// column-filter control rendered in the second thead row: 'text' = substring
// input, 'multi' = distinct-value checklist popover (multi-select; BP Year's
// discrete values fit this, layered over the toolbar's server-side year
// select), 'range' = numeric min/max pair for the continuous measures (Mean
// OGIP / Total CoS). `numeric` selects Number()-based sort with blanks-last
// in both directions instead of localeCompare. The former `fluid` column is
// now `status` (fluid value, or 'Staked'/'Proposed' for undrilled records --
// see reporting.record_status); rows also carry `pipeline_type`, `is_lead`
// and `risking_passed`, consumed only by the Actions cell / stats below.
// The full status vocabulary (reporting.record_status): every fluid a record
// can carry plus the two undrilled markers. The Status checklist always offers
// ALL of these -- a data-driven list alone hides whichever statuses happen to
// be absent from the current rowset, reading as if they don't exist.
var STATUS_OPTIONS = FLUID_TYPES.filter(function (value) { return value !== ''; })
  .concat(['Proposed', 'Staked']);

var COLUMNS = [
  // `well_name` is the name the record is KNOWN BY here: the staked well name
  // once it has one, the lead name until then (workflow.display_record_name).
  // The lead name is not a column of its own -- it appears under the well name
  // only on the rows where the two differ, which is the whole lead <-> well
  // map without a column that repeats the other one on every other row.
  { key: 'well_name', label: 'Well Name', numeric: false, filter: 'text' },
  // Card 3N's requested order. This is a PRESENTATION change only: every
  // column keeps its data key, formatter and filter, and the export's column
  // positions are a separate contract that does not follow this row.
  //
  // The card lists six of these plus a seventh, `nucd Area`, which is REPORTED
  // BLOCKED: there is no field, config entry or data of that name anywhere in
  // this application, and the card forbids guessing its source. Classification
  // and BP Year are not in the card's list either; they are kept here rather
  // than dropped, because removing working columns is not what "reorder" asks
  // for and both are still filtered on.
  { key: 'mean_ogip', label: 'Mean OGIP (BCF)', numeric: true, filter: 'range' },
  { key: 'total_cos', label: 'Total CoS (%)', numeric: true, filter: 'range' },
  { key: 'status', label: 'Status', numeric: false, filter: 'multi', options: STATUS_OPTIONS },
  { key: 'seismic_block', label: 'Seismic Block', numeric: false, filter: 'text' },
  { key: 'gas_field', label: 'Field', numeric: false, filter: 'multi' },
  { key: 'classification', label: 'Classification', numeric: false, filter: 'multi' },
  { key: 'year', label: 'BP Year', numeric: true, filter: 'multi' }
];

// The cross plot's four cutoff quadrants are NOT a column: they are a property
// of the record, so the glyph rides in the name cell rather than taking a
// column's worth of width to repeat one of four words down the page. The
// dialog's Quadrant checklist remains the place to filter by them.

// Every column read goes through here so a derived column behaves exactly
// like a stored one in filtering, sorting and rendering.
function cellValue(row, col) {
  var raw = col.derive ? col.derive(row) : row[col.key];
  return raw == null ? '' : raw;
}

// Module-level state per spec: fetched once per refreshPortfolio(), then
// sort/filter changes re-render locally without refetching.
//
// `sortChain` is an ORDERED list of {key, dir}: the first entry decides, and
// each later one breaks the ties the ones before it leave. One column was
// never enough here -- "biggest volumes, grouped by field" is two -- and a
// chain says exactly that instead of making the user sort twice and hope the
// second pass is stable.
var state = { rows: [], sortChain: [], filters: {} };

function sortIndex(key) {
  for (var i = 0; i < state.sortChain.length; i += 1) {
    if (state.sortChain[i].key === key) return i;
  }
  return -1;
}

function sortEntry(key) {
  var index = sortIndex(key);
  return index < 0 ? null : state.sortChain[index];
}

/* Picking a direction APPENDS the column to the chain, or re-points it if it
   is already there; picking the direction it already has REMOVES it. So one
   control both adds and drops a level, and a column can never appear twice. */
function toggleSort(key, dir) {
  var entry = sortEntry(key);
  if (entry && entry.dir === dir) {
    state.sortChain = state.sortChain.filter(function (item) { return item.key !== key; });
    return;
  }
  if (entry) { entry.dir = dir; return; }
  state.sortChain.push({ key: key, dir: dir });
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
      var text = String(cellValue(row, col));
      if (col.filter === 'text') {
        if (!filterValue) return true;
        return text.toLowerCase().indexOf(String(filterValue).toLowerCase()) >= 0;
      }
      // 'range': {min, max} strings; an unset bound doesn't constrain. Once
      // either bound is set, blank/non-numeric cells drop out (a row with no
      // value can't satisfy a numeric constraint).
      if (col.filter === 'range') {
        var hasMin = filterValue.min !== '' && isFinite(Number(filterValue.min));
        var hasMax = filterValue.max !== '' && isFinite(Number(filterValue.max));
        if (!hasMin && !hasMax) return true;
        var numeric = Number(text);
        if (text === '' || !isFinite(numeric)) return false;
        return (!hasMin || numeric >= Number(filterValue.min)) &&
          (!hasMax || numeric <= Number(filterValue.max));
      }
      // 'multi': array of selected values; an empty/absent array = no filter.
      return !filterValue.length || filterValue.indexOf(text) >= 0;
    });
  });
}

function compareRows(a, b, col, dir) {
  var av = cellValue(a, col);
  var bv = cellValue(b, col);
  if (col.numeric) {
    var an = Number(av);
    var bn = Number(bv);
    var aBlank = av === '' || !isFinite(an);
    var bBlank = bv === '' || !isFinite(bn);
    if (aBlank && bBlank) return 0;
    if (aBlank) return 1;  // blanks/non-numeric always sort last, both directions
    if (bBlank) return -1;
    return (an - bn) * dir;
  }
  return String(av).localeCompare(String(bv)) * dir;
}

// Walk the chain and return the FIRST non-zero comparison; every level keeps
// its own column's blanks-last and numeric/lexical rules unchanged.
function compareByChain(a, b) {
  for (var i = 0; i < state.sortChain.length; i += 1) {
    var entry = state.sortChain[i];
    var col = colByKey(entry.key);
    if (!col) continue;
    var result = compareRows(a, b, col, entry.dir);
    if (result !== 0) return result;
  }
  return 0;
}

function visibleRows() {
  var rows = applyFilters(state.rows);
  if (!state.sortChain.length) return rows;
  var sorted = rows.slice();
  sorted.sort(compareByChain);
  return sorted;
}

// BP Year cell with inline promote/recall icons (replaces the old trailing
// Actions column; still supervisor-only via canTransitionPhase). A bare
// Lucide X sits beside the year of every record already in the Business
// Plan (is_lead 0 -- the same membership signal the old Recall button keyed
// on) and runs the Recall flow. A Lucide '+' stands in for the missing year
// on undrilled (Staked/Proposed) lead records once risking has passed
// (risking_passed -- 'Segmentation Slides' Approved) and runs the Promote
// flow (year prompt + snapshot). The .portfolio-promote/.portfolio-recall
// classes keep the existing renderBody click wiring.
function yearCellMarkup(row) {
  var yearText = esc(row.year || '');
  if (!canTransitionPhase()) return '<td>' + yearText + '</td>';
  var attrs = ' data-project-id="' + esc(row.project_id) + '" data-project-name="' + esc(row.well_name || '') + '"';
  if (!row.is_lead) {
    // Bare, small X (no box chrome): muted until hovered so it doesn't
    // shout on every BP row; the confirm dialog still guards the recall.
    return '<td class="pf-year-cell">' + yearText + '<button type="button" class="pf-year-x portfolio-recall"' + attrs +
      ' title="Remove from Business Plan — recall to lead phase" aria-label="Recall ' + esc(row.well_name || '') + ' to lead phase">' + ICONS.x + '</button></td>';
  }
  var hasYear = !(row.year === null || row.year === undefined || row.year === '');
  var undrilled = row.status === 'Staked' || row.status === 'Proposed';
  if (!hasYear && undrilled && row.risking_passed) {
    return '<td class="pf-year-cell"><button type="button" class="pf-year-action pf-year-add portfolio-promote"' + attrs +
      ' title="No BP year yet — promote to BP well" aria-label="Promote ' + esc(row.well_name || '') + ' to BP well">' + ICONS.plus + '</button></td>';
  }
  return '<td>' + yearText + '</td>';
}

var QUADRANT_ICONS = {
  'Super Stars': 'quadrant-superstar',
  'Risk Takers': 'quadrant-risk-taker',
  'Value Hunter': 'quadrant-value-hunter',
  'Dogs': 'quadrant-dog'
};

// The quadrant mark that sits beside a record's name. Icon only -- the word is
// carried by the title and the accessible label, because four names repeated
// down a column is what made this a column in the first place. A record
// missing either measure has no quadrant and gets no mark at all (an absent
// classification is not a fifth class).
function quadrantMarkMarkup(row) {
  var quadrant = quadrantOf(row);
  if (!quadrant) return '';
  var key = QUADRANT_ICONS[quadrant];
  return '<span class="pf-quadrant pf-' + esc(key) + '" role="img"' +
    ' title="' + esc(quadrant) + '" aria-label="' + esc(quadrant) + '">' + ICONS[key] + '</span>';
}

/* The name cell: the quadrant mark, the name the record is known by, and --
   only when staking gave it a different one -- the lead name it was matured
   under, quietly beneath. That second line is the lead <-> well map, and it
   costs nothing on the rows where the two names are the same. */
function nameCellMarkup(row) {
  var toBP = row.pipeline_type === 'bp';
  var leadName = row.lead_name || '';
  var showsLead = isFilled(leadName) && leadName !== row.well_name;
  return '<td class="pf-name-cell">' +
    quadrantMarkMarkup(row) +
    '<span class="pf-name">' +
      '<a href="#" class="well-link" data-project-id="' + esc(row.project_id) + '"' +
      ' data-pipeline="' + (toBP ? 'bp' : 'prospect') + '"' +
      ' title="Open in ' + (toBP ? 'Business Plan Execution' : 'Segment Maturation') + '">' +
      esc(row.well_name || '') + '</a>' +
      (showsLead ? '<small class="pf-lead-name" title="Lead name in Segment Maturation">' +
        esc(leadName) + '</small>' : '') +
    '</span></td>';
}

// Cell order follows COLUMNS. The two measured columns carry .pf-num rather
// than relying on their POSITION for alignment -- a positional rule silently
// pointed at the wrong columns the moment Card 3N reordered the table.
function rowMarkup(row) {
  return '<tr>' +
    nameCellMarkup(row) +
    '<td class="pf-num">' + esc(fmtNum(row.mean_ogip) || '') + '</td>' +
    '<td class="pf-num">' + esc(fmtNum(row.total_cos) || '') + '</td>' +
    '<td>' + esc(row.status || '') + '</td>' +
    '<td>' + esc(row.seismic_block || '') + '</td>' +
    '<td>' + esc(row.gas_field || '') + '</td>' +
    '<td>' + esc(row.classification || '') + '</td>' +
    yearCellMarkup(row) +
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
      openDetail(Number(link.getAttribute('data-project-id')), link.getAttribute('data-pipeline'));
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
      // No prospectTasks here: the "+" only ever shows once risking has
      // passed (yearCellMarkup's risking_passed gate), so promoteProject
      // omits the N-of-M line/warning when the argument is falsy.
      promoteProject(project, null, currentUserName()).then(function (result) {
        if (result === null) return; // dialog cancelled
        refreshAllBoards({ businessPlanYear: result.business_plan_year });
        msg('Promoted to Business Plan ' + result.business_plan_year + '.', 'success');
      }).catch(function (error) { msg(error.message, 'error'); });
    });
  });
  // The resource bar tracks the VISIBLE rowset, so every column filter
  // re-scopes it -- including the per-category segment/well counts in its
  // legend, which is where the two deleted stat boxes' numbers went. (The
  // cross plot does NOT: it has its own filters over the full rowset -- see
  // portfolio-analysis.js.)
  renderResourceBar(rows);
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
  var entry = sortEntry(col.key);
  return !!entry && entry.dir === dir;
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
// (two directional options) and Filter (type-appropriate). The menu is placed
// against the viewport when opened so table/panel overflow cannot clip it.
// The header's sort mark: the column's RANK in the chain plus its arrow, so a
// three-level sort is readable from the header row alone rather than only from
// the strip below the table.
function sortMarkHtml(key) {
  var index = sortIndex(key);
  if (index < 0) return '';
  var entry = state.sortChain[index];
  return '<span class="pf-sort-rank">' + (index + 1) + '</span>' +
    (entry.dir === 1 ? ICONS['chevron-up'] : ICONS['chevron-down']);
}

function columnHeaderMarkup(col) {
  var labels = sortLabels(col);
  var sortMark = sortMarkHtml(col.key);
  var filtered = columnIsFiltered(col);
  return '<th data-key="' + col.key + '" aria-sort="none">' +
    '<button type="button" class="pf-col-trigger" aria-haspopup="true" aria-expanded="false">' +
      '<span class="pf-col-label">' + esc(col.label) + '</span>' +
      '<span class="pf-col-affix">' +
        '<span class="pf-sort-mark" aria-hidden="true">' + sortMark + '</span>' +
        '<span class="pf-filter-mark' + (filtered ? ' is-on' : '') + '" aria-hidden="true"></span>' +
        '<span class="pf-caret" aria-hidden="true">' + ICONS['chevron-down'] + '</span>' +
      '</span>' +
    '</button>' +
    '<div class="pf-popover" hidden>' +
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

// Fixed positioning lets the menu escape every overflow boundary around the
// table. Clamp it to the viewport and, when necessary, open above the header.
function placePortfolioPopover(trigger, popover) {
  var margin = 8;
  var triggerRect = trigger.getBoundingClientRect();
  popover.style.left = '0px';
  popover.style.right = 'auto';
  popover.style.top = '0px';
  popover.style.maxHeight = '320px';

  var width = popover.offsetWidth;
  var height = popover.offsetHeight;
  var left = triggerRect.left + 6;
  if (left + width > window.innerWidth - margin) left = window.innerWidth - width - margin;
  left = Math.max(margin, left);

  var below = triggerRect.bottom - 2;
  var above = triggerRect.top - height + 2;
  var top = (below + height > window.innerHeight - margin && above >= margin) ? above : below;
  var availableHeight = Math.max(120, window.innerHeight - top - margin);

  popover.style.left = Math.round(left) + 'px';
  popover.style.top = Math.round(top) + 'px';
  popover.style.maxHeight = Math.min(320, availableHeight) + 'px';
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
  window.addEventListener('resize', closePortfolioPopovers);
  window.addEventListener('scroll', closePortfolioPopovers);
}

// Reflect current sort/filter state onto the (persistent) header without a
// thead rebuild: aria-sort, the inline chevron sort mark, the active sort option, and
// the per-column "filtered" dot. Called after every sort/filter change so the
// open menu keeps its DOM (and any focused input) intact.
function refreshHeaderState(table) {
  all('th[data-key]', table).forEach(function (th) {
    var key = th.getAttribute('data-key');
    var col = colByKey(key);
    var entry = sortEntry(key);
    th.classList.toggle('sorted-asc', !!entry && entry.dir === 1);
    th.classList.toggle('sorted-desc', !!entry && entry.dir === -1);
    // aria-sort is per-column and has no notion of rank; the chain's order is
    // spoken by the "Sorted by" strip, which is a live region.
    th.setAttribute('aria-sort', entry ? (entry.dir === 1 ? 'ascending' : 'descending') : 'none');
    var sortMark = th.querySelector('.pf-sort-mark');
    if (sortMark) sortMark.innerHTML = sortMarkHtml(key);
    all('.pf-sort-opt', th).forEach(function (opt) {
      opt.classList.toggle('is-active', !!entry && Number(opt.getAttribute('data-dir')) === entry.dir);
    });
    var filtered = col && columnIsFiltered(col);
    var filterMark = th.querySelector('.pf-filter-mark');
    if (filterMark) filterMark.classList.toggle('is-on', !!filtered);
  });
}

/* The "Sorted by" strip above the table: the chain in order, each level
   removable on the spot, plus Clear all. The header marks say WHICH columns
   sort and in which direction; this says in what ORDER, which is the part a
   per-column mark cannot show. It renders nothing at all when there is no
   sort, so an unsorted table gains no chrome. Lives in a container the page
   owns (#portfolio-sort-strip), created next to the table on first use. */
function sortStripHost(table) {
  // Scoped to THIS table's parent, not looked up by id document-wide: a
  // re-mounted panel leaves the old strip detached from the new table, and a
  // global lookup would keep rendering levels into the orphan.
  var existing = table.parentNode.querySelector('#portfolio-sort-strip');
  if (existing) return existing;
  var host = document.createElement('div');
  host.id = 'portfolio-sort-strip';
  host.className = 'pf-sort-strip';
  host.setAttribute('role', 'status');
  host.setAttribute('aria-live', 'polite');
  table.parentNode.insertBefore(host, table);
  return host;
}

function renderSortStrip(table) {
  var host = sortStripHost(table);
  if (!state.sortChain.length) {
    host.innerHTML = '';
    host.hidden = true;
    return;
  }
  host.hidden = false;
  var levels = state.sortChain.map(function (entry, index) {
    var col = colByKey(entry.key);
    if (!col) return '';
    var labels = sortLabels(col);
    var direction = entry.dir === 1 ? labels.asc : labels.desc;
    return '<button type="button" class="pf-sort-level" data-sort-key="' + esc(entry.key) + '"' +
      ' title="Remove this sort level"' +
      ' aria-label="' + esc('Sort level ' + (index + 1) + ': ' + col.label + ', ' + direction +
        '. Activate to remove.') + '">' +
      '<span class="pf-sort-rank" aria-hidden="true">' + (index + 1) + '</span>' +
      esc(col.label) + ' <small>' + esc(direction) + '</small>' +
      '<span class="pf-sort-drop" aria-hidden="true">' + ICONS.x + '</span></button>';
  }).join('');
  host.innerHTML = '<span class="pf-sort-strip-label">Sorted by</span>' + levels +
    '<button type="button" id="portfolio-clear-sort" class="ghost pf-sort-clear">Clear all</button>';

  all('.pf-sort-level', host).forEach(function (button) {
    button.addEventListener('click', function () {
      var key = button.getAttribute('data-sort-key');
      state.sortChain = state.sortChain.filter(function (item) { return item.key !== key; });
      refreshHeaderState(table);
      renderSortStrip(table);
      renderBody(table);
    });
  });
  byId('portfolio-clear-sort').addEventListener('click', function () {
    state.sortChain = [];
    refreshHeaderState(table);
    renderSortStrip(table);
    renderBody(table);
  });
}

// Rebuilds thead -- only called when the fetched dataset changes, so typing in
// a filter input or an open menu never fights the DOM for focus (sort/filter
// changes only re-render tbody + refreshHeaderState in place).
// (The former supervisor-only Actions column is gone: promote/recall now
// live as inline icons in the BP Year cells -- yearCellMarkup.)
function renderHead(table) {
  table.innerHTML =
    '<thead><tr>' + COLUMNS.map(columnHeaderMarkup).join('') + '</tr></thead><tbody></tbody>';

  all('th[data-key]', table).forEach(function (th) {
    var key = th.getAttribute('data-key');
    var trigger = th.querySelector('.pf-col-trigger');
    var popover = th.querySelector('.pf-popover');
    // Title toggles this column's menu (closing any other first).
    trigger.addEventListener('click', function () {
      var wasOpen = !popover.hidden;
      closePortfolioPopovers();
      if (!wasOpen) {
        popover.hidden = false;
        placePortfolioPopover(trigger, popover);
        trigger.setAttribute('aria-expanded', 'true');
      }
    });
    // Sort options: pick a direction to add the column to the chain (or
    // re-point it); pick the direction it already has to drop it out.
    all('.pf-sort-opt', th).forEach(function (opt) {
      opt.addEventListener('click', function () {
        toggleSort(key, Number(opt.getAttribute('data-dir')));
        refreshHeaderState(table);
        renderSortStrip(table);
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
  renderSortStrip(table);
}

export function refreshPortfolio() {
  // Always the unfiltered fetch: the toolbar selects are gone, so scoping is
  // entirely client-side via the column-menu filters (BP Year and Status are
  // both 'multi' columns, replacing the old year/activity selects).
  // The promise is RETURNED (it was not before) so a caller can wait for the
  // table rather than poll for it; every existing call site ignores it, which
  // is unchanged behaviour.
  return API.portfolioRows({ year: 'All', activity: 'All' }).then(function (payload) {
    state.rows = (payload && payload.rows) || [];
    state.filters = {};
    // The cross plot dialog filters the full rowset independently of the
    // column filters (its selects can't see them from inside the dialog).
    setCrossPlotRows(state.rows);
    var table = byId('portfolio-table');
    if (!table) return;
    renderHead(table);
    renderBody(table);
  }).catch(function (error) { msg(error.message, 'error'); });
}
