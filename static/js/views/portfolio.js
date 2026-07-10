import { byId, table, esc, classChip, msg } from '../dom.js';
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
    table(byId('portfolio-table'), ['Year', 'Well', 'Pre-Drill OGIP (BCF)', 'Post-Drill OGIP (BCF)', 'Chance of Success (%)', 'Class'], rows.map(function (row) {
      return [
        esc(row.year || ''),
        esc(row.well_name || ''),
        esc(row.pre_drill_ogip || ''),
        esc(row.post_drill_ogip || ''),
        esc(row.chance_of_success || ''),
        classChip(row.segment_class || '')
      ];
    }));
  }).catch(function (error) { msg(error.message, 'error'); });
}
