import { byId, table, esc, msg } from '../dom.js';
import { API } from '../api.js';

export function formatNumber(value) {
  var numeric = Number(value);
  if (!isFinite(numeric)) return '0.0';
  return numeric.toLocaleString(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
}
export function renderPortfolioStats(summary) {
  var element = byId('portfolio-stats');
  if (!element) return;
  summary = summary || {};
  element.innerHTML =
    '<div class="portfolio-stat"><small>Business Plan Wells</small><b>' + esc(summary.business_plan_wells || 0) + '</b></div>' +
    '<div class="portfolio-stat"><small>Cumulative OGIP (BCF)</small><b>' + esc(formatNumber(summary.cumulative_ogip || 0)) + '</b></div>';
}
export function refreshPortfolio() {
  var year = byId('portfolio-year-filter').value || 'All';
  var activity = byId('portfolio-activity-filter').value || 'All';
  API.portfolioRows({ year: year, activity: activity }).then(function (payload) {
    var rows = (payload && payload.rows) || [];
    renderPortfolioStats((payload && payload.summary) || {});
    // WS7: exactly the 8 analysis columns, in this order.
    table(byId('portfolio-table'), ['Well Name', 'Gas Field', 'Seismic Block', 'Classification', 'BP Year', 'Fluid', 'Mean OGIP (BCF)', 'Total CoS (%)'], rows.map(function (row) {
      return [
        esc(row.well_name || ''),
        esc(row.gas_field || ''),
        esc(row.seismic_block || ''),
        esc(row.classification || ''),
        esc(row.year || ''),
        esc(row.fluid || ''),
        esc(row.mean_ogip || ''),
        esc(row.total_cos || '')
      ];
    }));
  }).catch(function (error) { msg(error.message, 'error'); });
}
