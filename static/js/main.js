import { byId, all, esc, fillSelect, range } from './dom.js';
import { Store } from './state.js';
import { API } from './api.js';
import { refreshProspect, refreshBP, createLead } from './views/pipeline.js';
import { refreshPortfolio } from './views/portfolio.js';
import { refreshAudit } from './views/audit.js';
import { saveComponent, assignComponent, transitionComponent, cyclePriorityChip, ensureUsers } from './views/detail-form.js';
import { performLogin, fetchUserOptions } from './auth.js';

// The board status filters act on projects.overall_status, which only ever
// holds these two values -- filling them with task statuses made the filter
// dead for every other option. Both boards exclude 'Completed': a fully
// matured lead leaves the prospect board and a fully-approved BP well
// (drilled/finished, incl. imported historical wells) leaves the BP board
// (workflow/projects.py get_projects), so the option would always yield an
// empty board.
var PROJECT_STATUSES = ['In Progress'];
var PROSPECT_STATUSES = ['In Progress'];

export function showTab(name) {
  all('.tab').forEach(function (tab) { tab.classList.toggle('active', tab.id === 'tab-' + name); });
  all('.tabs button').forEach(function (button) {
    var isActive = button.getAttribute('data-tab') === name;
    button.classList.toggle('active', isActive);
    button.setAttribute('aria-selected', String(isActive));
  });
  byId('detail-shell').classList.add('hidden');
  byId('project-editor').classList.add('hidden');
  if (name === 'prospect') refreshProspect();
  if (name === 'bp') refreshBP();
  if (name === 'portfolio') refreshPortfolio();
  if (name === 'audit') refreshAudit();
}

function safeOn(id, event, handler) { var element = byId(id); if (element) element.addEventListener(event, handler); }

// Dark theme. The <head> inline script stamps data-theme pre-paint; these
// keep the toggle glyph/state in sync and persist the user's choice. The
// button shows ☾ in light mode (click → go dark) and ☀ in dark (click → light).
function syncThemeToggle() {
  var isDark = document.documentElement.dataset.theme === 'dark';
  var button = byId('theme-toggle');
  if (!button) return;
  button.textContent = isDark ? '☀' : '☾';
  button.setAttribute('aria-pressed', String(isDark));
}
function applyTheme(theme) {
  var root = document.documentElement;
  if (theme === 'dark') root.dataset.theme = 'dark'; else delete root.dataset.theme;
  try { localStorage.setItem('theme', theme); } catch (e) { /* storage may be unavailable */ }
  syncThemeToggle();
}

// New Lead disclosure: swap the toggle label, reveal/hide the form body, and
// focus the name field when opening. Collapsed again on 'lead:created' (fired
// by createLead's success path).
function setNewLeadOpen(open) {
  var body = byId('new-lead-body');
  var toggle = byId('new-lead-toggle');
  if (!body || !toggle) return;
  body.classList.toggle('hidden', !open);
  toggle.textContent = open ? '− New Lead' : '+ New Lead';
  if (open) { var nameInput = byId('new-lead-name'); if (nameInput) nameInput.focus(); }
}

// Header identity chip: "Signed in as <name> (<role>)" + Sign out. Hidden
// entirely when anonymous (dev mode with AUTH_REQUIRED off). Re-rendered on
// the 'auth:changed' event the login dialog dispatches after a mid-session
// sign-in (see dialog.js).
export function renderUserChip() {
  var chip = byId('user-chip');
  var signOutButton = byId('sign-out');
  if (!chip || !signOutButton) return;
  chip.textContent = Store.user ? 'Signed in as ' + Store.user.name + ' (' + Store.user.role + ')' : '';
  chip.classList.toggle('hidden', !Store.user);
  signOutButton.classList.toggle('hidden', !Store.user);
}

export function wire() {
  all('.tabs button').forEach(function (button) { button.addEventListener('click', function () { showTab(button.getAttribute('data-tab')); }); });
  safeOn('export-excel', 'click', function () { window.location.href = '/api/export/excel'; });
  safeOn('theme-toggle', 'click', function () {
    applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
  });
  syncThemeToggle();
  // Reload after sign-out: it resets all in-memory state, and when
  // AUTH_REQUIRED is on the first API call of the fresh page reopens the
  // login dialog.
  safeOn('sign-out', 'click', function () { API.logout().catch(function () {}).then(function () { window.location.reload(); }); });
  safeOn('create-lead-form', 'submit', createLead);
  safeOn('new-lead-toggle', 'click', function () { setNewLeadOpen(byId('new-lead-body').classList.contains('hidden')); });
  document.addEventListener('lead:created', function () { setNewLeadOpen(false); });
  safeOn('component-form', 'submit', saveComponent);
  safeOn('component-priority-chip', 'click', cyclePriorityChip);
  safeOn('assigned-to', 'change', assignComponent);
  safeOn('submit-component', 'click', function () { transitionComponent('submit'); });
  safeOn('approve-component', 'click', function () { transitionComponent('approve'); });
  safeOn('return-component', 'click', function () { transitionComponent('return'); });
  safeOn('back-to-overview', 'click', function () { byId('detail-shell').classList.add('hidden'); byId('tab-' + Store.pipeline).scrollIntoView({ behavior: 'smooth', block: 'start' }); });
  ['prospect-status-filter', 'prospect-assignee-filter'].forEach(function (id) { safeOn(id, 'input', refreshProspect); safeOn(id, 'change', refreshProspect); });
  ['bp-year-filter', 'bp-status-filter', 'bp-assignee-filter'].forEach(function (id) { safeOn(id, 'input', refreshBP); safeOn(id, 'change', refreshBP); });
  ['portfolio-year-filter', 'portfolio-activity-filter'].forEach(function (id) { safeOn(id, 'change', refreshPortfolio); });
  safeOn('audit-project-filter', 'change', refreshAudit);
}

// Board assignee filters: value '' = All assignees (pipeline.js maps '' to the
// backend's 'All'). Options are the active users, matching current_owner names.
function fillAssigneeFilter(select, users) {
  if (!select) return;
  var previous = select.value;
  select.innerHTML = '<option value="">All assignees</option>' + users.map(function (user) {
    return '<option>' + esc(user.name) + '</option>';
  }).join('');
  select.value = previous || '';
}

function boot() {
  fillSelect(byId('prospect-status-filter'), PROSPECT_STATUSES, true);
  fillSelect(byId('bp-status-filter'), PROJECT_STATUSES, true);
  // The portfolio year range is only a boot placeholder: the first unfiltered
  // portfolio fetch replaces it with the distinct years actually present
  // (views/portfolio.js), so imported historical (pre-2026) wells become
  // selectable. The BP board select stays a fixed 2026+ planning range.
  fillSelect(byId('portfolio-year-filter'), range(2026, 2040), true);
  fillSelect(byId('bp-year-filter'), range(2026, 2040), true);
  ensureUsers().then(function (users) {
    fillAssigneeFilter(byId('prospect-assignee-filter'), users || []);
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
