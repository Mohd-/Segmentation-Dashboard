import { byId, all, esc, fillSelect, range } from './dom.js';
import { Store } from './state.js';
import { API } from './api.js';
import { refreshProspect, refreshBP, createLead, addWell } from './views/pipeline.js';
import { refreshPortfolio } from './views/portfolio.js';
import { refreshAudit } from './views/audit.js';
import { saveComponent, assignComponent, transitionComponent, ensureUsers } from './views/detail-form.js';

// The board status filters act on projects.overall_status, which only ever
// holds these two values -- filling them with task statuses made the filter
// dead for every other option.
var PROJECT_STATUSES = ['In Progress', 'Completed'];

export function showTab(name) {
  all('.tab').forEach(function (tab) { tab.classList.toggle('active', tab.id === 'tab-' + name); });
  all('.tabs button').forEach(function (button) {
    var isActive = button.getAttribute('data-tab') === name;
    button.classList.toggle('active', isActive);
    button.setAttribute('aria-selected', String(isActive));
  });
  byId('detail-shell').classList.add('hidden');
  if (name === 'prospect') refreshProspect();
  if (name === 'bp') refreshBP();
  if (name === 'portfolio') refreshPortfolio();
  if (name === 'audit') refreshAudit();
}

function safeOn(id, event, handler) { var element = byId(id); if (element) element.addEventListener(event, handler); }

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
  // Reload after sign-out: it resets all in-memory state, and when
  // AUTH_REQUIRED is on the first API call of the fresh page reopens the
  // login dialog.
  safeOn('sign-out', 'click', function () { API.logout().catch(function () {}).then(function () { window.location.reload(); }); });
  safeOn('create-lead-form', 'submit', createLead);
  safeOn('add-well-form', 'submit', addWell);
  safeOn('component-form', 'submit', saveComponent);
  safeOn('assigned-to', 'change', assignComponent);
  safeOn('submit-component', 'click', function () { transitionComponent('submit'); });
  safeOn('approve-component', 'click', function () { transitionComponent('approve'); });
  safeOn('return-component', 'click', function () { transitionComponent('return'); });
  safeOn('back-to-overview', 'click', function () { byId('detail-shell').classList.add('hidden'); byId('tab-' + Store.pipeline).scrollIntoView({ behavior: 'smooth', block: 'start' }); });
  ['prospect-search', 'prospect-status-filter', 'prospect-assignee-filter'].forEach(function (id) { safeOn(id, 'input', refreshProspect); safeOn(id, 'change', refreshProspect); });
  ['bp-search', 'bp-year-filter', 'bp-status-filter', 'bp-assignee-filter'].forEach(function (id) { safeOn(id, 'input', refreshBP); safeOn(id, 'change', refreshBP); });
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
  fillSelect(byId('prospect-status-filter'), PROJECT_STATUSES, true);
  fillSelect(byId('bp-status-filter'), PROJECT_STATUSES, true);
  fillSelect(byId('portfolio-year-filter'), range(2026, 2040), true);
  fillSelect(byId('new-well-bp-year'), range(2026, 2040), false);
  fillSelect(byId('bp-year-filter'), range(2026, 2040), true);
  ensureUsers().then(function (users) {
    fillAssigneeFilter(byId('prospect-assignee-filter'), users || []);
    fillAssigneeFilter(byId('bp-assignee-filter'), users || []);
  });
  wire();
  renderUserChip();
  showTab('prospect');
}

document.addEventListener('DOMContentLoaded', function () {
  document.addEventListener('auth:changed', renderUserChip);
  // Load authoritative stage/status metadata and the session identity before
  // the first render: boards coerce cards against server-side stage lists (not
  // the schema.js fallbacks) and changed_by payloads use the signed-in name.
  // Both probes tolerate failure so an offline/anonymous boot still renders.
  // Under AUTH_REQUIRED the meta call 401s, which pops the login dialog (see
  // api.js) before anything else loads.
  Promise.all([
    API.meta().then(function (meta) { Store.meta = meta; }).catch(function () { /* fall back to schema.js constants */ }),
    API.me().then(function (me) { if (me && me.authenticated) Store.user = { name: me.name, role: me.role }; }).catch(function () { /* stay anonymous */ })
  ]).then(boot);
});
