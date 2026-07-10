import { byId, all, fillSelect, range } from './dom.js';
import { Store } from './state.js';
import { STATUSES } from './schema.js';
import { refreshProspect, refreshBP, createLead, addWell } from './views/pipeline.js';
import { refreshPortfolio } from './views/portfolio.js';
import { refreshAudit } from './views/audit.js';
import { saveComponent } from './views/detail-form.js';

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

export function wire() {
  all('.tabs button').forEach(function (button) { button.addEventListener('click', function () { showTab(button.getAttribute('data-tab')); }); });
  safeOn('export-excel', 'click', function () { window.location.href = '/api/export/excel'; });
  safeOn('create-lead-form', 'submit', createLead);
  safeOn('add-well-form', 'submit', addWell);
  safeOn('component-form', 'submit', saveComponent);
  safeOn('back-to-overview', 'click', function () { byId('detail-shell').classList.add('hidden'); byId('tab-' + Store.pipeline).scrollIntoView({ behavior: 'smooth', block: 'start' }); });
  ['prospect-search', 'prospect-status-filter'].forEach(function (id) { safeOn(id, 'input', refreshProspect); safeOn(id, 'change', refreshProspect); });
  ['bp-search', 'bp-year-filter', 'bp-status-filter'].forEach(function (id) { safeOn(id, 'input', refreshBP); safeOn(id, 'change', refreshBP); });
  ['portfolio-year-filter', 'portfolio-activity-filter'].forEach(function (id) { safeOn(id, 'change', refreshPortfolio); });
  safeOn('audit-project-filter', 'change', refreshAudit);
}

document.addEventListener('DOMContentLoaded', function () {
  fillSelect(byId('prospect-status-filter'), STATUSES, true);
  fillSelect(byId('bp-status-filter'), STATUSES, true);
  fillSelect(byId('portfolio-year-filter'), range(2026, 2040), true);
  fillSelect(byId('new-well-bp-year'), range(2026, 2040), false);
  fillSelect(byId('bp-year-filter'), range(2026, 2040), true);
  wire();
  showTab('prospect');
});
