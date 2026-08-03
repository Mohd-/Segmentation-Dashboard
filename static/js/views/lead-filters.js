/* =========================================================================
   Card 1C — the Segment Maturation FILTER ROW and the one central
   filtered-leads selector.

   THE CONTRACT (what later cards, Card 1E's KPIs first, must build on):

     setLeadRows(rows)     hand the board's UNFILTERED prospect payload in
                           (refreshProspect fetches it once per refresh)
     filteredLeads()       -> the current filtered leads, a fresh array
     onLeadsFiltered(fn)   -> subscribe; fn(leads) runs on every change
     'leads:filtered'      the same thing as a document CustomEvent, with
                           event.detail.leads

   There is exactly ONE filtered rowset in this module and everything that
   claims to describe "the leads on the board" -- the cards, the three column
   badges, and later the KPI tiles -- must read it from here. Nothing else may
   re-filter the payload on its own: two filter implementations would drift
   the moment a rule changes.

   Filtering is entirely client-side over one fetch (the portfolio table's
   visibleRows() pattern): no request per filter change, no per-lead or
   per-user request, users fetched once by main.js's boot.

   Combination rule: AND across the three categories, OR inside Assignee.
   ========================================================================= */
import { byId, all, esc } from '../dom.js';
import { ICONS } from '../icons.js';

// The sentinel for "leads nobody is assigned to". Deliberately not a name: no
// person is called Unassigned, and the value must never collide with one.
export var UNASSIGNED = '__unassigned__';

// The automation identity (workflow/users.py SYSTEM_USER) drives auto-completed
// steps; it is not a person to filter a board by.
var SYSTEM_USER = 'System';

// Board-level status vocabulary. NOT the stored task lifecycle (Not Assigned /
// In Progress / Ready / Approved) and NOT the same list as the card dots'
// vocabulary by accident -- see leadStatus() for the mapping. Each option
// carries its own GLYPH so the three never read by color alone.
var STATUS_OPTIONS = [
  { value: 'Completed', icon: 'circle-check', slug: 'completed' },
  { value: 'Pending Approval', icon: 'circle-minus', slug: 'pending' },
  { value: 'In Progress', icon: 'circle', slug: 'in-progress' }
];

// The three controls, left to right (the Card 1B band: Assignee / Field /
// Status — priority stays visible as the lead cards' border color and the
// board's sort order, not as a filter). `multi` picks checkbox semantics
// (Assignee) over radio semantics (everything else).
var FILTERS = [
  { key: 'assignee', multi: true, caption: 'Assignee', allLabel: 'All Assignees' },
  { key: 'field', multi: false, caption: 'Field', allLabel: 'All Fields' },
  { key: 'status', multi: false, caption: 'Status', allLabel: 'All Statuses' }
];

// Module state. `filters.assignees` empty = All Assignees; the two
// single-selects use '' for their All option.
var filters = defaultFilters();
var rows = [];        // the unfiltered dataset, exactly as GET /api/projects returned it
var users = [];       // active user names, 'System' excluded
var filtered = [];    // the one filtered rowset -- see filteredLeads()
var subscribers = []; // onLeadsFiltered listeners
var rootId = 'lead-filter-row';
var openKey = null;   // the filter whose menu is open (one at a time), or null

function defaultFilters() {
  return { assignees: [], field: '', status: '' };
}

/* -------------------------------------------------------------------------
   Lead -> filter value (pure; the single definition of each rule)
   ------------------------------------------------------------------------- */

// A lead's assignees are DERIVED server-side from its per-task assigned_to
// values (ordered, distinct) -- multi-assignee needs no schema of its own, and
// assignment stays a per-task edit in the detail page.
export function leadAssignees(lead) {
  return (lead && lead.assignees) || [];
}

// The record's field, derived server-side from the record name by the same
// split the share paths use ("GALV-2" -> "GALV"); no stored field column.
export function leadField(lead) {
  return (lead && lead.field) || '';
}

// Board status of one lead:
//   Completed        the whole applicable pipeline is approved (overall_status)
//   Pending Approval at least one tracked item is waiting on a supervisor
//   In Progress      everything else -- there is no "Not Assigned" board status
export function leadStatus(lead) {
  if (((lead && lead.overall_status) || '') === 'Completed') return 'Completed';
  var items = (lead && lead.tracked_items) || [];
  for (var i = 0; i < items.length; i += 1) {
    if (items[i] && items[i].status === 'Pending Approval') return 'Pending Approval';
  }
  return 'In Progress';
}

// AND across categories, OR inside Assignee. A multi-assignee lead matches if
// ANY selected member is among its assignees; Unassigned matches leads with no
// assignee at all.
export function matchesLeadFilters(lead, selection) {
  var choice = selection || filters;
  var selected = choice.assignees || [];
  if (selected.length) {
    var people = leadAssignees(lead);
    var hit = false;
    for (var i = 0; i < selected.length; i += 1) {
      if (selected[i] === UNASSIGNED ? people.length === 0 : people.indexOf(selected[i]) >= 0) {
        hit = true;
        break;
      }
    }
    if (!hit) return false;
  }
  if (choice.field && leadField(lead) !== choice.field) return false;
  if (choice.status && leadStatus(lead) !== choice.status) return false;
  return true;
}

/* -------------------------------------------------------------------------
   The contract surface
   ------------------------------------------------------------------------- */

export function filteredLeads() { return filtered.slice(); }

// The UNFILTERED dataset of the current refresh, as a copy. Card 1D's duplicate
// name pre-check needs it: a lead the active filters hide still owns its name.
// Everything that RENDERS must keep using filteredLeads() instead.
export function leadRows() { return rows.slice(); }

// A copy, so a consumer can read the current selection without being able to
// mutate it behind the controls' back.
export function leadFilterState() {
  return { assignees: filters.assignees.slice(), field: filters.field,
           status: filters.status };
}

export function onLeadsFiltered(handler) {
  if (typeof handler === 'function') subscribers.push(handler);
}

function publish() {
  filtered = rows.filter(function (lead) { return matchesLeadFilters(lead, filters); });
  var payload = filteredLeads();
  subscribers.forEach(function (handler) { handler(payload); });
  document.dispatchEvent(new CustomEvent('leads:filtered', { detail: { leads: payload } }));
  return filtered;
}

// The board's whole dataset for this refresh. Selections SURVIVE it: only the
// option lists are rebuilt (a field can appear or vanish with the data).
export function setLeadRows(newRows) {
  rows = newRows || [];
  renderFilterRow();
  return publish();
}

// Active users, fetched ONCE at boot (main.js) -- never per lead, never per
// dropdown open.
export function setLeadUsers(activeUsers) {
  users = (activeUsers || []).map(function (user) {
    return typeof user === 'string' ? user : user.name;
  }).filter(function (name) { return name && name !== SYSTEM_USER; });
  renderFilterRow();
}

export function clearLeadFilters() {
  filters = defaultFilters();
  syncFilterMarks();
  publish();
}

/* -------------------------------------------------------------------------
   Options
   ------------------------------------------------------------------------- */

// Assignee options: All, Unassigned, then every active user. Only the named
// members carry the person glyph -- the absence of a person is not a person.
function assigneeOptions() {
  var options = [{ value: '', label: 'All Assignees' },
                 { value: UNASSIGNED, label: 'Unassigned', strong: true }];
  users.forEach(function (name) {
    options.push({ value: name, label: name, icon: 'user' });
  });
  return options;
}

// Field options are DATA-DRIVEN (the distinct fields of the leads on hand) --
// there is no fixed field vocabulary to hard-code. A field that is currently
// selected stays listed even if the refreshed data no longer contains it, so
// the control can never show a selection it cannot clear.
function fieldOptions() {
  var seen = {};
  var values = [];
  rows.forEach(function (lead) {
    var value = leadField(lead);
    if (value && !seen[value]) { seen[value] = true; values.push(value); }
  });
  if (filters.field && values.indexOf(filters.field) < 0) values.push(filters.field);
  values.sort(function (a, b) { return a.localeCompare(b); });
  return [{ value: '', label: 'All Fields' }].concat(values.map(function (value) {
    return { value: value, label: value };
  }));
}

function statusOptions() {
  return [{ value: '', label: 'All Statuses' }].concat(STATUS_OPTIONS.map(function (option) {
    return { value: option.value, label: option.value, icon: option.icon, slug: option.slug };
  }));
}

function optionsFor(key) {
  if (key === 'assignee') return assigneeOptions();
  if (key === 'field') return fieldOptions();
  return statusOptions();
}

// Whether one option reads as chosen right now. The All option of every
// category is chosen exactly when nothing else is.
function isChosen(key, value) {
  if (key === 'assignee') {
    return value === '' ? filters.assignees.length === 0 : filters.assignees.indexOf(value) >= 0;
  }
  return filters[key] === value;
}

// The CLOSED control's text: the selection itself, so the row reads as a
// sentence of what the board is showing. At rest it is the bare caption
// ("Assignee", not "All Assignees") — the three triggers share a band third
// and the longer forms truncate there; the "All …" wording still appears as
// each menu's first option, where it is the clear affordance.
function triggerLabel(key) {
  if (key !== 'assignee') {
    var caption = FILTERS.filter(function (f) { return f.key === key; })[0].caption;
    return filters[key] || caption;
  }
  var selected = filters.assignees;
  if (!selected.length) return 'Assignee';
  if (selected.length === 1) return selected[0] === UNASSIGNED ? 'Unassigned' : selected[0];
  return selected.length + ' Assignees';
}

function isFilterActive(key) {
  return key === 'assignee' ? filters.assignees.length > 0 : !!filters[key];
}

function anyFilterActive() {
  return FILTERS.some(function (filter) { return isFilterActive(filter.key); });
}

/* -------------------------------------------------------------------------
   Markup
   ------------------------------------------------------------------------- */

function optionMarkup(filter, option) {
  var chosen = isChosen(filter.key, option.value);
  // Real <button>s with checkbox/radio semantics: Space and Enter toggle them
  // for free, and assistive tech reads the state from aria-checked.
  return '<button type="button" class="lf-option' + (chosen ? ' is-chosen' : '') +
    (option.strong ? ' lf-option-strong' : '') + '"' +
    ' role="' + (filter.multi ? 'checkbox' : 'radio') + '" aria-checked="' + (chosen ? 'true' : 'false') + '"' +
    ' data-value="' + esc(option.value) + '">' +
    '<span class="lf-mark' + (filter.multi ? ' lf-mark-box' : ' lf-mark-dot') + '" aria-hidden="true"></span>' +
    (option.icon
      ? '<span class="lf-option-icon' + (option.slug ? ' lf-icon-' + option.slug : '') + '" aria-hidden="true">' + ICONS[option.icon] + '</span>'
      : '') +
    '<span class="lf-option-label">' + esc(option.label) + '</span>' +
    '</button>';
}

function filterMarkup(filter) {
  return '<div class="lead-filter" data-filter="' + filter.key + '">' +
    '<button type="button" class="lf-trigger' + (isFilterActive(filter.key) ? ' is-active' : '') + '"' +
      ' aria-haspopup="true" aria-expanded="false" aria-label="Filter by ' + esc(filter.caption.toLowerCase()) + '">' +
      '<span class="lf-value">' + esc(triggerLabel(filter.key)) + '</span>' +
      '<span class="lf-caret" aria-hidden="true">' + ICONS['chevron-down'] + '</span>' +
    '</button>' +
    '<div class="lf-menu" hidden role="' + (filter.multi ? 'group' : 'radiogroup') + '"' +
      ' aria-label="' + esc(filter.caption) + '">' +
      optionsFor(filter.key).map(function (option) { return optionMarkup(filter, option); }).join('') +
    '</div>' +
    '</div>';
}

/* -------------------------------------------------------------------------
   Placement + dismissal

   The menu is position: fixed and placed against the VIEWPORT, the same
   escape hatch the portfolio column menus use: .pipeline-panel and
   .lead-column both clip their overflow, so an absolutely positioned menu
   would be cut off by the stage container the moment it grew past the row.
   ------------------------------------------------------------------------- */

function placeMenu(trigger, menu) {
  var margin = 8;
  var rect = trigger.getBoundingClientRect();
  menu.style.left = '0px';
  menu.style.top = '0px';
  menu.style.minWidth = Math.round(rect.width) + 'px';

  var width = menu.offsetWidth;
  var height = menu.offsetHeight;
  var left = rect.left;
  if (left + width > window.innerWidth - margin) left = window.innerWidth - width - margin;
  left = Math.max(margin, left);

  var below = rect.bottom + 4;
  var above = rect.top - height - 4;
  var top = (below + height > window.innerHeight - margin && above >= margin) ? above : below;
  menu.style.left = Math.round(left) + 'px';
  menu.style.top = Math.round(top) + 'px';
  menu.style.maxHeight = Math.min(320, Math.max(120, window.innerHeight - top - margin)) + 'px';
}

/* The page-wide "one dropdown at a time" contract (added with Card 1F).

   Every floating menu on the page -- these four filter menus and the header's
   bell / gear menus -- announces itself on this document event before opening
   and closes itself when someone ELSE announces. A document event rather than
   cross-imports: the header must not import the board's filters and the
   filters must not import the header, and a future menu joins by honoring the
   same two lines. `detail.source` identifies the announcer so a module never
   closes itself in response to its own event. */
export var DROPDOWN_OPEN_EVENT = 'dropdown:open';
var DROPDOWN_SOURCE = 'lead-filters';

function announceOpen() {
  document.dispatchEvent(new CustomEvent(DROPDOWN_OPEN_EVENT, {
    detail: { source: DROPDOWN_SOURCE }
  }));
}

// Closing never changes a selection: Escape and an outside click DISMISS, they
// do not clear (that is what the Clear button is for).
export function closeLeadMenus() {
  openKey = null;
  all('.lf-menu', document).forEach(function (menu) { menu.hidden = true; });
  all('.lf-trigger', document).forEach(function (trigger) { trigger.setAttribute('aria-expanded', 'false'); });
}

function openMenu(key) {
  var host = byId(rootId);
  if (!host) return;
  var group = host.querySelector('.lead-filter[data-filter="' + key + '"]');
  if (!group) return;
  closeLeadMenus();
  var trigger = group.querySelector('.lf-trigger');
  var menu = group.querySelector('.lf-menu');
  announceOpen();   // closes the header's bell/gear menus, if either is open
  menu.hidden = false;
  placeMenu(trigger, menu);
  trigger.setAttribute('aria-expanded', 'true');
  openKey = key;
}

// One registration for the lifetime of the page: the handlers query the DOM
// live, so they keep working across every rebuild of the row.
var dismissWired = false;
function wireDismiss() {
  if (dismissWired) return;
  dismissWired = true;
  document.addEventListener('click', function (event) {
    var target = event.target;
    if (!target || !target.closest) return;
    // A click on a trigger (its own handler toggles) or anywhere inside an open
    // menu must not dismiss: the assignee list has to stay open while several
    // members are ticked.
    if (target.closest('.lf-trigger') || target.closest('.lf-menu')) return;
    closeLeadMenus();
  });
  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape' || !openKey) return;
    var host = byId(rootId);
    var trigger = host && host.querySelector('.lead-filter[data-filter="' + openKey + '"] .lf-trigger');
    closeLeadMenus();
    if (trigger) trigger.focus();
  });
  // The other half of the one-dropdown-at-a-time contract: another module
  // opening its menu closes ours.
  document.addEventListener(DROPDOWN_OPEN_EVENT, function (event) {
    if (!event.detail || event.detail.source === DROPDOWN_SOURCE) return;
    closeLeadMenus();
  });
  window.addEventListener('resize', closeLeadMenus);
  // Capture, so scrolling ANY ancestor (the board, the page) dismisses a menu
  // that is fixed-positioned and would otherwise hang in the wrong place --
  // but NOT when the scrolling element is the menu's own option list, which a
  // long assignee roster makes scrollable.
  window.addEventListener('scroll', function (event) {
    var target = event.target;
    if (target && target.closest && target.closest('.lf-menu')) return;
    closeLeadMenus();
  }, true);
}

/* -------------------------------------------------------------------------
   Render + wiring
   ------------------------------------------------------------------------- */

// Reflect the current selection onto the existing controls WITHOUT rebuilding
// them, so an open menu keeps its DOM (and the focused option keeps focus)
// while the board re-filters underneath.
function syncFilterMarks() {
  var host = byId(rootId);
  if (!host) return;
  FILTERS.forEach(function (filter) {
    var group = host.querySelector('.lead-filter[data-filter="' + filter.key + '"]');
    if (!group) return;
    var trigger = group.querySelector('.lf-trigger');
    trigger.querySelector('.lf-value').textContent = triggerLabel(filter.key);
    trigger.classList.toggle('is-active', isFilterActive(filter.key));
    all('.lf-option', group).forEach(function (option) {
      var chosen = isChosen(filter.key, option.getAttribute('data-value'));
      option.setAttribute('aria-checked', chosen ? 'true' : 'false');
      option.classList.toggle('is-chosen', chosen);
    });
  });
  var clear = host.querySelector('.lf-clear');
  if (clear) clear.disabled = !anyFilterActive();
}

function chooseOption(filter, value) {
  if (!filter.multi) {
    filters[filter.key] = value;
    syncFilterMarks();
    publish();
    closeLeadMenus();   // a single choice is a finished choice
    return;
  }
  // Assignee: All clears the individual picks; picking an individual leaves
  // All; unticking the last one falls back to All.
  if (value === '') {
    filters.assignees = [];
  } else if (filters.assignees.indexOf(value) >= 0) {
    filters.assignees = filters.assignees.filter(function (name) { return name !== value; });
  } else {
    filters.assignees = filters.assignees.concat([value]);
  }
  syncFilterMarks();
  publish();  // the menu deliberately STAYS OPEN
}

// Full rebuild of the row (options can change with the data/users). Selections
// live in module state, so they survive every rebuild -- and so does an
// unrelated category when one filter changes.
function renderFilterRow() {
  var host = byId(rootId);
  if (!host) return;
  closeLeadMenus();
  host.innerHTML = FILTERS.map(filterMarkup).join('') +
    '<button type="button" class="lf-clear ghost">Clear</button>';

  FILTERS.forEach(function (filter) {
    var group = host.querySelector('.lead-filter[data-filter="' + filter.key + '"]');
    var trigger = group.querySelector('.lf-trigger');
    trigger.addEventListener('click', function () {
      if (openKey === filter.key) closeLeadMenus(); else openMenu(filter.key);
    });
    all('.lf-option', group).forEach(function (option) {
      option.addEventListener('click', function () {
        chooseOption(filter, option.getAttribute('data-value'));
      });
    });
  });
  host.querySelector('.lf-clear').addEventListener('click', clearLeadFilters);
  syncFilterMarks();
  wireDismiss();
}

/* Boot entry point (main.js). `options.root` names the container id (the tests
   mount their own); `options.onChange` is the board's renderer -- registering
   it here instead of importing the board keeps this module free of any view
   dependency. Every call resets the selection, so a re-init is a clean slate. */
export function initLeadFilters(options) {
  var settings = options || {};
  rootId = settings.root || 'lead-filter-row';
  filters = defaultFilters();
  rows = [];
  filtered = [];
  subscribers = [];
  users = [];
  onLeadsFiltered(settings.onChange);
  renderFilterRow();
}
