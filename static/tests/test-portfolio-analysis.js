// Tests for static/js/views/portfolio-analysis.js — the Portfolio Analysis
// resource summary/progress bar and the CoS/OGIP cross plot. The module's
// only dependency is dom.js, so these tests never touch the pipeline graph.
import { test, assert, fixture, mockFetch, waitFor } from './harness.js';
import {
  computeResourceSummary, crossPlotPoints, renderResourceBar, renderCrossPlot,
  setCrossPlotRows, filteredCrossPlotRows, openCrossPlotDialog, initPortfolioAnalysis,
  quadrantOf, YTF_BCF_PER_FIELD
} from '../js/views/portfolio-analysis.js';
// The table itself lives in portfolio.js; the Quadrant column is the seam
// between the two (a cross-plot classification rendered as a table column).
import { refreshPortfolio } from '../js/views/portfolio.js';

function sampleRows() {
  return [
    // Discovered: Gas + Gas over Water sum OGIP 30 + 12.5 = 42.5
    { well_name: 'ALPHA-1', gas_field: 'ALPHA', status: 'Gas', mean_ogip: 30, total_cos: 80 },
    { well_name: 'ALPHA-2', gas_field: 'ALPHA', status: 'Gas over Water', mean_ogip: 12.5, total_cos: 65 },
    // Undiscovered: Staked + Proposed sum OGIP 20 + 5 = 25
    { well_name: 'BETA-1', gas_field: 'BETA', status: 'Staked', mean_ogip: 20, total_cos: 40 },
    { well_name: 'BETA-2', gas_field: 'BETA', status: 'Proposed', mean_ogip: 5, total_cos: 55 },
    // Neither bucket: dry hole, and a gas well with no OGIP recorded
    { well_name: 'GAMMA-1', gas_field: 'GAMMA', status: 'Dry', mean_ogip: 9, total_cos: 30 },
    { well_name: 'GAMMA-2', gas_field: 'GAMMA', status: 'Gas', mean_ogip: '', total_cos: 70 }
  ];
}

test('portfolio-analysis.computeResourceSummary buckets by status and counts fields', function () {
  var summary = computeResourceSummary(sampleRows());
  assert.equal(summary.discovered, 42.5);
  assert.equal(summary.undiscovered, 25);
  assert.equal(summary.fieldCount, 3, 'ALPHA, BETA, GAMMA');
  assert.equal(summary.ytf, 3 * YTF_BCF_PER_FIELD);
  assert.equal(summary.total, 42.5 + 25 + 1200);
});

test('portfolio-analysis.computeResourceSummary handles an empty selection', function () {
  var summary = computeResourceSummary([]);
  assert.deepEqual(
    [summary.discovered, summary.undiscovered, summary.ytf, summary.total],
    [0, 0, 0, 0]);
});

test('portfolio-analysis.crossPlotPoints keeps only rows with both measures, CoS as fraction', function () {
  var points = crossPlotPoints(sampleRows());
  assert.equal(points.length, 5, 'GAMMA-2 has no OGIP');
  assert.equal(points[0].name, 'ALPHA-1');
  assert.equal(points[0].cos, 0.8);
  assert.equal(points[0].ogip, 30);
});

test('portfolio-analysis.renderResourceBar renders total + strictly proportional segments', function () {
  var root = fixture('<div id="portfolio-resource-bar"></div>');
  renderResourceBar(sampleRows());
  var title = root.querySelector('.prb-title');
  assert.match(title.textContent, /Total Estimated Original Gas Initially in Place is 1,268 BCF/);
  var segments = root.querySelectorAll('.prb-seg');
  assert.equal(segments.length, 3);
  assert.equal(segments[0].style.flexGrow, '42.5', 'discovered width tracks its value');
  assert.equal(segments[2].style.flexGrow, '1200', 'ytf width tracks its value');
  assert.equal(segments[0].style.flexBasis, '0%', 'basis 0 so widths are purely proportional');
  var keys = root.querySelectorAll('.prb-key');
  assert.equal(keys.length, 3);
  assert.match(keys[0].textContent, /42\.5 BCF/);
  assert.match(keys[0].textContent, /Discovered Resources/);
  assert.match(keys[1].textContent, /25 BCF/);
  assert.match(keys[1].textContent, /Undiscovered Resources/);
  assert.match(keys[2].textContent, /1,200 BCF/);
  assert.match(keys[2].textContent, /Yet to Find/);
});

test('portfolio-analysis.renderResourceBar drops zero segments but keeps their legend entry', function () {
  var root = fixture('<div id="portfolio-resource-bar"></div>');
  renderResourceBar([{ well_name: 'X-1', gas_field: 'X', status: 'Gas', mean_ogip: 10, total_cos: 50 }]);
  assert.equal(root.querySelectorAll('.prb-seg').length, 2, 'no segment for the 0 BCF stage');
  var keys = root.querySelectorAll('.prb-key');
  assert.equal(keys.length, 3, 'legend always lists all three stages');
  assert.match(keys[1].textContent, /0 BCF/);
});

test('portfolio-analysis.renderResourceBar shows an empty track when everything is zero', function () {
  var root = fixture('<div id="portfolio-resource-bar"></div>');
  renderResourceBar([]);
  assert.equal(root.querySelectorAll('.prb-seg.prb-empty').length, 1);
  assert.equal(root.querySelectorAll('.prb-key').length, 3);
});

test('portfolio-analysis.renderCrossPlot draws dots, cutoffs and quadrant labels', function () {
  var root = fixture('<div id="portfolio-crossplot"></div>');
  renderCrossPlot(sampleRows());
  assert.equal(root.querySelectorAll('.pxp-dot').length, 5);
  assert.equal(root.querySelectorAll('.pxp-cut').length, 2, '50% CoS + 10 BCF cutoffs');
  var quads = Array.prototype.map.call(root.querySelectorAll('.pxp-quad'),
    function (t) { return t.textContent; });
  assert.deepEqual(quads, ['Risk Takers', 'Super Stars', 'Dogs', 'Value Hunter']);
  assert.match(root.querySelector('.pxp-note').textContent, /5 records plotted · 1 without both CoS and OGIP not shown/);
});

test('portfolio-analysis.renderCrossPlot shows an empty state without plottable rows', function () {
  var root = fixture('<div id="portfolio-crossplot"></div>');
  renderCrossPlot([{ well_name: 'X-1', gas_field: 'X', status: 'Gas', mean_ogip: '', total_cos: '' }]);
  assert.equal(root.querySelectorAll('.pxp-dot').length, 0);
  assert.match(root.querySelector('.empty-state').textContent, /No records with both Total CoS and Mean OGIP/);
});

function dialogFixture() {
  return fixture(
    '<dialog id="crossplot-dialog"><div>' +
      '<div id="crossplot-field-filter"></div>' +
      '<div id="crossplot-year-filter"></div>' +
      '<div id="crossplot-status-filter"></div>' +
      '<div id="crossplot-quadrant-filter"></div>' +
      '<div id="portfolio-crossplot"></div>' +
      '<button type="button" id="crossplot-close"></button>' +
    '</div></dialog>'
  );
}

function tickBox(root, containerId, value) {
  var box = root.querySelector('#' + containerId + ' input[value="' + value + '"]');
  box.checked = true;
  box.dispatchEvent(new Event('change', { bubbles: true }));
  return box;
}

test('portfolio-analysis.quadrantOf splits on the 50% CoS / 10 BCF cutoffs', function () {
  assert.equal(quadrantOf({ total_cos: 80, mean_ogip: 30 }), 'Super Stars');
  assert.equal(quadrantOf({ total_cos: 55, mean_ogip: 5 }), 'Value Hunter');
  assert.equal(quadrantOf({ total_cos: 40, mean_ogip: 20 }), 'Risk Takers');
  assert.equal(quadrantOf({ total_cos: 30, mean_ogip: 9 }), 'Dogs');
  assert.equal(quadrantOf({ total_cos: 50, mean_ogip: 10 }), 'Super Stars', 'cutoff values count as high');
  assert.equal(quadrantOf({ total_cos: '', mean_ogip: 30 }), '', 'missing measure has no quadrant');
});

test('portfolio-analysis cross plot dialog opens with checklist options and full rowset', function () {
  var root = dialogFixture();
  setCrossPlotRows(sampleRows());
  openCrossPlotDialog();
  var dialog = root.querySelector('#crossplot-dialog');
  assert.ok(dialog.open, 'dialog opened');
  var fieldOptions = Array.prototype.map.call(
    root.querySelectorAll('#crossplot-field-filter input[type="checkbox"]'),
    function (box) { return box.value; });
  assert.deepEqual(fieldOptions, ['ALPHA', 'BETA', 'GAMMA']);
  var quadrantOptions = Array.prototype.map.call(
    root.querySelectorAll('#crossplot-quadrant-filter input[type="checkbox"]'),
    function (box) { return box.value; });
  assert.deepEqual(quadrantOptions, ['Super Stars', 'Value Hunter', 'Risk Takers', 'Dogs']);
  assert.equal(root.querySelectorAll('.pxp-dot').length, 5, 'unfiltered plot');
  dialog.close();
});

test('portfolio-analysis dialog checklists filter the plot independently', function () {
  var root = dialogFixture();
  initPortfolioAnalysis();
  setCrossPlotRows(sampleRows());
  openCrossPlotDialog();
  tickBox(root, 'crossplot-field-filter', 'ALPHA');
  assert.equal(filteredCrossPlotRows().length, 2, 'ALPHA rows only');
  assert.equal(root.querySelectorAll('.pxp-dot').length, 2, 'plot re-rendered filtered');
  // A second tick in the same group widens the selection (multi-select).
  tickBox(root, 'crossplot-field-filter', 'BETA');
  assert.equal(filteredCrossPlotRows().length, 4, 'ALPHA + BETA rows');
  root.querySelector('#crossplot-close').click();
  assert.equal(root.querySelector('#crossplot-dialog').open, false, 'close button closes');
});

test('portfolio-analysis quadrant checklist filters by plot quadrant', function () {
  var root = dialogFixture();
  setCrossPlotRows(sampleRows());
  openCrossPlotDialog();
  tickBox(root, 'crossplot-quadrant-filter', 'Super Stars');
  var names = filteredCrossPlotRows().map(function (row) { return row.well_name; });
  assert.deepEqual(names, ['ALPHA-1', 'ALPHA-2'], 'both high-CoS big-OGIP wells');
  tickBox(root, 'crossplot-quadrant-filter', 'Dogs');
  assert.equal(filteredCrossPlotRows().length, 3, 'Super Stars + Dogs');
  root.querySelector('#crossplot-dialog').close();
});

test('portfolio-analysis.renderCrossPlot tooltip appears on hover', function () {
  var root = fixture('<div id="portfolio-crossplot"></div>');
  renderCrossPlot(sampleRows());
  var hit = root.querySelector('.pxp-hit[data-index="0"]');
  hit.dispatchEvent(new MouseEvent('mouseenter'));
  var tooltip = root.querySelector('.pxp-tooltip');
  assert.equal(tooltip.hidden, false);
  assert.match(tooltip.textContent, /ALPHA-1/);
  assert.match(tooltip.textContent, /CoS 80% · OGIP 30 BCF/);
  assert.ok(root.querySelector('.pxp-dot[data-index="0"]').classList.contains('is-hover'));
  hit.dispatchEvent(new MouseEvent('mouseleave'));
  assert.equal(tooltip.hidden, true);
});

/* -------------------------------------------------------------------------
   The Quadrant TABLE column (views/portfolio.js). The quadrant is derived per
   row rather than stored, so these cover the three things that derivation has
   to behave like a stored column for: the header exists in order, the cell
   renders label + glyph, and a record missing either measure reads as a dash.
   ------------------------------------------------------------------------- */

function portfolioFixture() {
  return fixture(
    '<div id="portfolio-stats"></div>' +
    '<div id="portfolio-resource-bar"></div>' +
    '<table id="portfolio-table"></table>'
  );
}

test('portfolio table renders a derived Quadrant column with a badge per row', async function () {
  var host = portfolioFixture();
  mockFetch(function (url) {
    if (String(url).indexOf('/api/portfolio/rows') >= 0) {
      return new Response(JSON.stringify({ rows: [
        { project_id: 1, well_name: 'ALPHA-1', gas_field: 'ALPHA', status: 'Gas',
          mean_ogip: 30, total_cos: 80, is_lead: 0, pipeline_type: 'bp' },
        { project_id: 2, well_name: 'BETA-1', gas_field: 'BETA', status: 'Staked',
          mean_ogip: 3, total_cos: 20, is_lead: 1, pipeline_type: 'prospect' },
        { project_id: 3, well_name: 'GAMMA-1', gas_field: 'GAMMA', status: 'Proposed',
          mean_ogip: '', total_cos: '', is_lead: 1, pipeline_type: 'prospect' }
      ] }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    throw new Error('Unexpected request: ' + url);
  });

  await refreshPortfolio();
  await waitFor(function () { return host.querySelectorAll('#portfolio-table tbody tr').length === 3; });

  var heads = Array.prototype.map.call(
    host.querySelectorAll('#portfolio-table thead th'),
    function (th) { return th.getAttribute('data-key'); });
  assert.equal(heads[heads.length - 1], 'quadrant', 'Quadrant closes the column set');
  assert.deepEqual(heads, ['well_name', 'staked_well_name', 'gas_field', 'seismic_block',
    'classification', 'year', 'status', 'mean_ogip', 'total_cos', 'quadrant']);

  var cells = host.querySelectorAll('.pf-quadrant-cell');
  assert.equal(cells.length, 3);
  // High CoS + big volume, low CoS + small volume, and a row that cannot be
  // classified at all.
  assert.equal(cells[0].textContent.trim(), 'Super Stars');
  assert.equal(cells[1].textContent.trim(), 'Dogs');
  assert.equal(cells[2].textContent.trim(), '—');
  assert.ok(cells[0].querySelector('.pf-quadrant-superstar'));
  assert.ok(cells[0].querySelector('.pf-quadrant-icon svg'), 'the badge carries its glyph');
  assert.equal(cells[2].querySelectorAll('.pf-quadrant').length, 0,
    'an unclassifiable record gets a dash, not an empty badge');
});

test('portfolio Quadrant column filters and sorts like a stored column', async function () {
  var host = portfolioFixture();
  mockFetch(function (url) {
    if (String(url).indexOf('/api/portfolio/rows') >= 0) {
      return new Response(JSON.stringify({ rows: [
        { project_id: 1, well_name: 'ALPHA-1', gas_field: 'A', status: 'Gas',
          mean_ogip: 30, total_cos: 80, is_lead: 0, pipeline_type: 'bp' },
        { project_id: 2, well_name: 'BETA-1', gas_field: 'B', status: 'Staked',
          mean_ogip: 3, total_cos: 20, is_lead: 1, pipeline_type: 'prospect' }
      ] }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    throw new Error('Unexpected request: ' + url);
  });

  await refreshPortfolio();
  await waitFor(function () { return host.querySelectorAll('#portfolio-table tbody tr').length === 2; });

  var th = host.querySelector('#portfolio-table thead th[data-key="quadrant"]');
  // The checklist offers all four quadrants whether or not the rowset has
  // them, exactly as the Status column offers its full vocabulary.
  var options = th.querySelectorAll('.portfolio-filter-option input[type="checkbox"]');
  assert.equal(options.length, 4);
  var dogs = Array.prototype.filter.call(options, function (box) { return box.value === 'Dogs'; })[0];
  assert.ok(dogs, 'Dogs is offered');
  dogs.checked = true;
  dogs.dispatchEvent(new Event('change', { bubbles: true }));
  await waitFor(function () { return host.querySelectorAll('#portfolio-table tbody tr').length === 1; });
  assert.equal(host.querySelector('#portfolio-table tbody .pf-quadrant-cell').textContent.trim(), 'Dogs');
});
