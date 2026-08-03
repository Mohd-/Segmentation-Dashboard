import { byId, all, esc, fillSelect, range } from './dom.js';
import { Store } from './state.js';
import { API } from './api.js';
import { activateTab, backToBoard } from './navigation.js';
import { refreshProspect, refreshBP, renderLeadBoard } from './views/pipeline.js';
import { initLeadFilters, setLeadUsers } from './views/lead-filters.js';
import { initLeadKpis, renderLeadKpis } from './views/lead-kpis.js';
import { initLeadCreate } from './views/lead-create.js';
import { initHeaderMenus } from './views/header-menus.js';
import { refreshPortfolio } from './views/portfolio.js';
import { initPortfolioAnalysis } from './views/portfolio-analysis.js';
import { refreshAudit } from './views/audit.js';
import { refreshMap } from './views/map-view.js';
import { initCalculators } from './views/calculators.js';
import { saveComponent, assignComponent, transitionComponent, cyclePriorityChip, ensureUsers } from './views/detail-form.js';
import { openProjectEditor } from './views/project-editor.js';
import { performLogin, fetchUserOptions } from './auth.js';

// The BP board's status select acts on projects.overall_status, which only
// ever holds these two values -- filling it with task statuses made the filter
// dead for every other option. 'Completed' is excluded: a fully-approved BP
// well (drilled/finished, incl. imported historical wells) leaves the BP board
// (workflow/projects.py get_projects), so the option would always yield an
// empty board. (The prospect board has no server-side status select any more:
// Card 1C filters it client-side and DOES show completed leads -- see
// views/lead-filters.js.)
var PROJECT_STATUSES = ['In Progress'];

export function showTab(name) {
  activateTab(name);
  byId('detail-shell').classList.add('hidden');
  byId('project-editor').classList.add('hidden');
  if (name === 'prospect') refreshProspect();
  if (name === 'bp') refreshBP();
  if (name === 'portfolio') refreshPortfolio();
  // The map canvas is 0x0 while the tab is display:none, so refreshMap() is
  // BOTH the lazy first-time boot and the per-activation re-measure. It runs
  // after activateTab() above, i.e. once the section is actually laid out.
  if (name === 'map') refreshMap();
  if (name === 'audit') refreshAudit();
}

function safeOn(id, event, handler) { var element = byId(id); if (element) element.addEventListener(event, handler); }


/* The three app-chrome actions the Card 1F gear menu triggers.

   They live HERE, not in views/header-menus.js: they are app behaviors (the
   theme, the export download, ending the session), and the menu is only the
   surface that offers them. Before Card 1F they were wired to the
   #theme-toggle / #export-excel / #sign-out buttons in the header; that markup
   is gone and these same functions are now handed to initHeaderMenus. Nothing
   about what they DO changed. */

// Dark theme. The <head> inline script stamps data-theme pre-paint; this
// persists the user's choice and announces the change so any surface showing
// the current theme (the gear menu's Dark/Light Mode item) re-renders.
export function applyTheme(theme) {
  var root = document.documentElement;
  if (theme === 'dark') root.dataset.theme = 'dark'; else delete root.dataset.theme;
  try { localStorage.setItem('theme', theme); } catch (e) { /* storage may be unavailable */ }
  document.dispatchEvent(new CustomEvent('theme:changed', { detail: { theme: theme } }));
}

export function toggleTheme() {
  applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
}

// Excel export: a plain navigation to the download endpoint, exactly as the
// old #export-excel button did. There is no completion event to observe (the
// browser owns the download), which is why the gear item guards against a
// double fire itself rather than waiting for a result here.
export function exportExcel() {
  window.location.href = '/api/export/excel';
}

// Reload after sign-out: it resets all in-memory state, and when
// AUTH_REQUIRED is on the first API call of the fresh page reopens the
// login dialog.
export function signOut() {
  return API.logout().catch(function () {}).then(function () { window.location.reload(); });
}

// Header identity chip: "Signed in as <name> (<role>)". Hidden entirely when
// anonymous (dev mode with AUTH_REQUIRED off). Re-rendered on the
// 'auth:changed' event the login dialog dispatches after a mid-session sign-in
// (see dialog.js). The Sign out control it used to hide alongside itself is
// now a gear-menu item, which applies the SAME rule (header-menus.canSignOut).
export function renderUserChip() {
  var chip = byId('user-chip');
  if (!chip) return;
  chip.textContent = Store.user ? 'Signed in as ' + Store.user.name + ' (' + Store.user.role + ')' : '';
  chip.classList.toggle('hidden', !Store.user);
}

export function wire() {
  all('.tabs button').forEach(function (button) { button.addEventListener('click', function () { showTab(button.getAttribute('data-tab')); }); });
  // Card 1F: the bell and the gear. The three chrome actions are handed in
  // rather than re-implemented there, and Sign out is shown only when there is
  // a session to end — the rule the removed #sign-out button carried.
  initHeaderMenus({
    actions: { toggleTheme: toggleTheme, exportExcel: exportExcel, signOut: signOut },
    canSignOut: function () { return !!Store.user; }
  });
  // Card 1D: the Add New Lead control wires itself (button, Enter, Escape,
  // outside click, inline validation) -- there is no form to submit and no
  // disclosure state for main.js to own any more.
  initLeadCreate();
  safeOn('component-form', 'submit', saveComponent);
  safeOn('component-priority-chip', 'click', cyclePriorityChip);
  safeOn('assigned-to', 'change', assignComponent);
  safeOn('submit-component', 'click', function () { transitionComponent('submit'); });
  safeOn('approve-component', 'click', function () { transitionComponent('approve'); });
  safeOn('return-component', 'click', function () { transitionComponent('return'); });
  safeOn('back-to-overview', 'click', backToBoard);
  safeOn('back-to-board', 'click', backToBoard);
  safeOn('open-project-editor', 'click', function () {
    if (Store.projectId) openProjectEditor(Store.projectId);
  });
  // The prospect board's filters are not selects any more (Card 1C's filter
  // row wires itself and re-filters in place, with no refetch); the BP board
  // keeps its three server-side selects exactly as they were.
  ['bp-year-filter', 'bp-status-filter', 'bp-assignee-filter'].forEach(function (id) { safeOn(id, 'input', refreshBP); safeOn(id, 'change', refreshBP); });
  safeOn('audit-project-filter', 'change', refreshAudit);
  // Portfolio Analysis: cross plot dialog trigger, close, and filter selects.
  // (The portfolio table itself filters via its column menus -- portfolio.js.)
  initPortfolioAnalysis();
  initCalculators();
}

// The BP board's assignee select: value '' = All assignees (pipeline.js maps
// '' to the backend's 'All'). Options are the active users, matching
// current_owner names.
function fillAssigneeFilter(select, users) {
  if (!select) return;
  var previous = select.value;
  select.innerHTML = '<option value="">All assignees</option>' + users.map(function (user) {
    return '<option>' + esc(user.name) + '</option>';
  }).join('');
  select.value = previous || '';
}

function boot() {
  fillSelect(byId('bp-status-filter'), PROJECT_STATUSES, true);
  fillSelect(byId('bp-year-filter'), range(2026, 2040), true);
  // Card 1C: the filter row owns the lead board's rowset. It is initialized
  // BEFORE the first refresh so the payload lands in a live filter module, and
  // the board renders only through its onChange -- one filtered rowset, one
  // renderer. The users list is the same single /api/users fetch the assignee
  // select uses (ensureUsers caches it in Store), never a second request.
  // Card 1E: the KPI tiles are wired into the SAME onChange the board renders
  // from, so every filter change recomputes cards, badges and tiles from ONE
  // filteredLeads() array in one pass -- no second subscription that could see
  // a different rowset, and no fetch of its own (the data is already local, so
  // there is no in-flight response to go stale). initLeadKpis runs first so
  // the row paints 0% / 0 / 0 BCF instead of a gap before the first payload.
  initLeadKpis();
  initLeadFilters({
    onChange: function (leads) {
      renderLeadBoard(byId('prospect-pipeline'), leads);
      renderLeadKpis(leads);
    }
  });
  ensureUsers().then(function (users) {
    setLeadUsers(users || []);
    fillAssigneeFilter(byId('bp-assignee-filter'), users || []);
  });
  wire();
  renderUserChip();
  showTab('prospect');
}

// Show/hide the app chrome (header, tabs, main) as one unit. The full-page
// login replaces it until sign-in under AUTH_REQUIRED.
function setChromeHidden(hidden) {
  all('.app-chrome').forEach(function (element) { element.classList.toggle('hidden', hidden); });
}

// Load authoritative stage/status metadata (tolerating failure -> schema.js
// fallbacks) then hand off to boot() for the first render. Deferred behind the
// login page under AUTH_REQUIRED so the meta call never 401s pre-session.
function loadMetaAndBoot() {
  return API.meta()
    .then(function (meta) { Store.meta = meta; })
    .catch(function () { /* fall back to schema.js constants */ })
    .then(boot);
}

// Full-page boot login, shown only when AUTH_REQUIRED is on and no session
// exists. Distinct from the mid-session modal (dialog.js): the meta + data
// loads are deferred until sign-in succeeds, so the app never renders (or
// 401s) behind it. On success performLogin has already set Store.user and
// dispatched 'auth:changed'; we then swap the login page for the chrome and
// run the deferred boot.
function showLoginPage() {
  var page = byId('login-page');
  var form = byId('login-page-form');
  var select = byId('login-page-name');
  var passcodeInput = byId('login-page-passcode');
  var errorEl = byId('login-page-error');
  setChromeHidden(true);
  page.classList.remove('hidden');
  errorEl.classList.add('hidden');
  fetchUserOptions(select);
  form.addEventListener('submit', function (event) {
    event.preventDefault();
    performLogin(select.value, passcodeInput.value).then(function (result) {
      if (!result.ok) {
        errorEl.textContent = (result.body && result.body.detail) || 'Login failed.';
        errorEl.classList.remove('hidden');
        return;
      }
      page.classList.add('hidden');
      setChromeHidden(false);
      loadMetaAndBoot();
    }).catch(function () {
      errorEl.textContent = 'Login failed. Check your connection and try again.';
      errorEl.classList.remove('hidden');
    });
  });
}

document.addEventListener('DOMContentLoaded', function () {
  document.addEventListener('auth:changed', renderUserChip);
  // /api/me is probed FIRST because it never 401s (even under AUTH_REQUIRED).
  // It reports both the session identity and whether auth is required, so we
  // can front the app with the full-page login BEFORE the meta/data loads
  // (which WOULD 401 without a session). When authenticated or auth is off we
  // boot straight in. A failed /api/me (offline) still boots so the app renders.
  API.me().then(function (me) {
    if (me && me.authenticated) Store.user = { name: me.name, role: me.role };
    if (me && me.auth_required && !me.authenticated) {
      showLoginPage();
    } else {
      loadMetaAndBoot();
    }
  }).catch(function () { loadMetaAndBoot(); });
});
