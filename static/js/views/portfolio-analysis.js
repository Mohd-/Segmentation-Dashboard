import { byId, all, esc } from '../dom.js';

// Portfolio Analysis widgets: the resource progress bar (in the overview
// row beside the stat boxes, consuming the VISIBLE rowset renderBody passes
// to renderPortfolioStats, so every column-filter interaction re-scopes it)
// and the CoS/OGIP cross plot (in #crossplot-dialog with its own filter
// checklists over the FULL rowset). Kept in a module of its own (dom.js is the
// only dependency) so the computation is testable without importing
// portfolio.js's pipeline/transitions graph.

// Resource-class buckets over reporting.record_status values: a well with a
// recorded gas fluid is a discovery; an undrilled record is undiscovered
// potential, split by whether it is already Staked or still Proposed. The
// remaining fluids (Dry Hole, Water Bearing, Oil over Gas, Oil) are gas
// write-offs and count toward neither bucket.
export var DISCOVERED_STATUSES = ['Gas', 'Gas over Water'];
export var UNDISCOVERED_STATUSES = ['Staked', 'Proposed'];
// Yet-to-find is a fixed planning assumption, not data: 400 BCF for every
// distinct gas field in the current selection. That figure is the field's
// WHOLE estimated endowment, so what is left to find is it MINUS what has
// already been found or booked as potential -- discovered and undiscovered
// volumes eat into the estimate rather than stacking on top of it. The bar
// therefore always totals the endowment, and its shape reads as "how much of
// what we think is there have we accounted for".
export var YTF_BCF_PER_FIELD = 400;

// Segments vs wells: a record that has not been promoted to the Business Plan
// is a segment/lead (is_lead), the rest are wells. Counting both per bucket is
// what the two deleted stat boxes used to say, now said per category.
function emptyBucket() { return { bcf: 0, segments: 0, wells: 0 }; }

function addToBucket(bucket, row, ogip) {
  if (ogip != null) bucket.bcf += ogip;
  if (row.is_lead) bucket.segments += 1; else bucket.wells += 1;
}

export function computeResourceSummary(rows) {
  var discovered = emptyBucket();
  var staked = emptyBucket();
  var proposed = emptyBucket();
  var seenFields = {};
  var fieldCount = 0;
  (rows || []).forEach(function (row) {
    var field = row.gas_field == null ? '' : String(row.gas_field);
    if (field && !seenFields[field]) { seenFields[field] = true; fieldCount += 1; }
    var raw = Number(row.mean_ogip);
    var ogip = (row.mean_ogip === '' || row.mean_ogip == null || !isFinite(raw)) ? null : raw;
    // A record still COUNTS toward its category with no volume recorded --
    // "3 segments" is true whether or not all three carry a Mean OGIP.
    if (DISCOVERED_STATUSES.indexOf(row.status) >= 0) addToBucket(discovered, row, ogip);
    else if (row.status === 'Staked') addToBucket(staked, row, ogip);
    else if (row.status === 'Proposed') addToBucket(proposed, row, ogip);
  });
  var initialYtf = fieldCount * YTF_BCF_PER_FIELD;
  var accounted = discovered.bcf + staked.bcf + proposed.bcf;
  var remainingYtf = Math.max(0, initialYtf - accounted);
  return {
    discovered: discovered,
    staked: staked,
    proposed: proposed,
    // What is left of the endowment after everything already accounted for.
    ytf: remainingYtf,
    initialYtf: initialYtf,
    accounted: accounted,
    // True when the selection has already accounted for more than the
    // endowment assumes. Surfaced rather than silently clamped: it means the
    // 400 BCF/field assumption is low for this selection, which is worth
    // knowing.
    exceedsEstimate: accounted > initialYtf,
    fieldCount: fieldCount,
    // The bar always totals the endowment; with no field in the selection
    // there is nothing to total.
    total: initialYtf
  };
}

// BCF display formatting: thousands separators, 1 decimal only while values
// are small enough for it to matter (a 2,500 BCF total gains nothing from
// ".0" and the bar labels are tight).
function fmtBcf(value) {
  var numeric = Number(value) || 0;
  var rounded = Math.round(numeric * 10) / 10;
  return rounded.toLocaleString('en-US', {
    minimumFractionDigits: 0,
    maximumFractionDigits: Math.abs(rounded) >= 100 ? 0 : 1
  });
}

// "3 segments · 1 well", with the singular right and either half dropped when
// it is zero -- "0 wells" on every line is noise.
function countsLabel(bucket) {
  var bits = [];
  if (bucket.segments) bits.push(bucket.segments + ' segment' + (bucket.segments === 1 ? '' : 's'));
  if (bucket.wells) bits.push(bucket.wells + ' well' + (bucket.wells === 1 ? '' : 's'));
  return bits.join(' · ') || 'no records';
}

export function renderResourceBar(rows) {
  var element = byId('portfolio-resource-bar');
  if (!element) return;
  var summary = computeResourceSummary(rows);
  var fieldsShort = summary.fieldCount + ' field' + (summary.fieldCount === 1 ? '' : 's');
  var fieldsPhrase = fieldsShort + ' in the current selection';
  var stages = [
    { slug: 'discovered', label: 'Discovered', value: summary.discovered.bcf,
      counts: countsLabel(summary.discovered),
      hint: 'Sum of Mean OGIP over Gas and Gas over Water records in the current selection' },
    { slug: 'staked', label: 'Staked', value: summary.staked.bcf,
      counts: countsLabel(summary.staked),
      hint: 'Sum of Mean OGIP over Staked records in the current selection' },
    { slug: 'proposed', label: 'Proposed', value: summary.proposed.bcf,
      counts: countsLabel(summary.proposed),
      hint: 'Sum of Mean OGIP over Proposed records in the current selection' },
    { slug: 'ytf', label: 'Yet to Find', value: summary.ytf,
      // The short form: this line sits in a quarter-width column, and the
      // full phrase is already under the title and in the tooltip.
      counts: fieldsShort,
      hint: YTF_BCF_PER_FIELD + ' BCF × ' + fieldsPhrase + ', less the ' +
        fmtBcf(summary.accounted) + ' BCF already discovered or booked as potential' }
  ];
  // Segments are strictly proportional to their BCF share: inline flex
  // weights with basis 0 (and no min-width floor), so width tracks value
  // exactly however the filters re-scope the sums. A stage with no volume
  // renders no segment at all. Captions live in a separate legend row
  // (swatch + value + name, centered per entry) so a thin segment can never
  // crush or overlap its caption.
  var segments = stages.filter(function (stage) { return stage.value > 0; }).map(function (stage) {
    return '<div class="prb-seg prb-' + stage.slug + '" style="flex:' + stage.value + ' 1 0%" title="' + esc(stage.hint) + '"></div>';
  }).join('');
  if (!segments) segments = '<div class="prb-seg prb-empty"></div>';
  // Staked and Proposed drop the "Undiscovered · " prefix: they share the
  // pink family in the bar, which already groups them, and the full sense is
  // in each key's tooltip. Four keys have to sit in ONE row here -- a wrapped
  // legend is what made this card as tall as the picture tiles beside it,
  // and the bar is three short lines of content, not a panel.
  var legend = stages.map(function (stage) {
    return '<div class="prb-key" title="' + esc(stage.hint) + '">' +
      '<b><span class="prb-swatch prb-' + stage.slug + '"></span>' + esc(fmtBcf(stage.value)) + ' BCF</b>' +
      '<small>' + esc(stage.label) + '</small>' +
      '<em>' + esc(stage.counts) + '</em></div>';
  }).join('');
  // The title states the ESTIMATE the bar totals, which is the field
  // endowment -- not the sum of the segments, since the segments now divide
  // that estimate rather than adding up to a running total.
  var overrun = summary.exceedsEstimate
    ? '<p class="prb-overrun" role="status">Discovered and undiscovered volumes exceed the ' +
      esc(fmtBcf(summary.initialYtf)) + ' BCF estimate by ' +
      esc(fmtBcf(summary.accounted - summary.initialYtf)) + ' BCF — nothing is left to find under this assumption.</p>'
    : '';
  element.innerHTML =
    '<p class="prb-title">Estimated Original Gas Initially in Place is <b>' + esc(fmtBcf(summary.total)) + ' BCF</b>' +
    '<small class="prb-title-note">' + esc(YTF_BCF_PER_FIELD + ' BCF × ' + fieldsPhrase) + '</small></p>' +
    '<div class="prb-bar" role="img" aria-label="Of ' + esc(fmtBcf(summary.total)) +
    ' BCF estimated: discovered ' + esc(fmtBcf(summary.discovered.bcf)) +
    ' BCF, staked ' + esc(fmtBcf(summary.staked.bcf)) +
    ' BCF, proposed ' + esc(fmtBcf(summary.proposed.bcf)) +
    ' BCF, yet to find ' + esc(fmtBcf(summary.ytf)) + ' BCF">' + segments + '</div>' +
    '<div class="prb-legend">' + legend + '</div>' + overrun;
}

// ---------------------------------------------------------------------------
// Cross plot: Total CoS (fraction, x) vs Mean OGIP (BCF, y), with the two
// portfolio cutoffs (50% CoS, 10 BCF) splitting the plane into the four
// classic quadrants (Dogs / Risk Takers / Value Hunter / Super Stars).
// Lives in #crossplot-dialog behind the overview row's "View cross plot"
// trigger, with its own filter checklists: the table's column filters can't
// reach into the dialog, so it filters the FULL portfolio rowset
// (setCrossPlotRows, called on every portfolio fetch) independently.
// ---------------------------------------------------------------------------

export var COS_CUTOFF = 0.5;    // fraction
export var OGIP_CUTOFF = 10;    // BCF

// Rows that can be plotted: both measures present and numeric. CoS arrives
// as a percentage (portfolio table column), plotted as a fraction.
export function crossPlotPoints(rows) {
  var points = [];
  (rows || []).forEach(function (row) {
    var cos = Number(row.total_cos);
    var ogip = Number(row.mean_ogip);
    if (row.total_cos === '' || row.total_cos == null || !isFinite(cos)) return;
    if (row.mean_ogip === '' || row.mean_ogip == null || !isFinite(ogip)) return;
    points.push({ name: row.well_name || '', cos: cos / 100, ogip: ogip });
  });
  return points;
}

// Nice round y-axis ceiling: never below the 10 BCF cutoff's neighborhood,
// stepped so the axis lands on clean tick values.
function niceCeil(value) {
  var steps = [1, 2, 5];
  var target = Math.max(value, 20) / 5; // aim for ~5 ticks
  var magnitude = Math.pow(10, Math.floor(Math.log(target) / Math.LN10));
  for (var m = magnitude; ; m *= 10) {
    for (var i = 0; i < steps.length; i += 1) {
      var step = steps[i] * m;
      if (step >= target) return { step: step, max: Math.ceil(Math.max(value, 20) / step) * step };
    }
  }
}

// `hostId` lets the SAME renderer draw the dialog's full-size plot and the
// overview tile's reduced-scale one -- the tile is not a picture of the plot,
// it IS the plot, so clicking it enlarges rather than showing something else.
// The SVG has a fixed viewBox and scales to its container, so no second
// drawing path is needed for the small size.
export function renderCrossPlot(rows, hostId) {
  var host = byId(hostId || 'portfolio-crossplot');
  if (!host) return;
  var points = crossPlotPoints(rows);
  var skipped = (rows || []).length - points.length;
  var note = points.length + ' record' + (points.length === 1 ? '' : 's') + ' plotted' +
    (skipped > 0 ? ' · ' + skipped + ' without both CoS and OGIP not shown' : '');

  if (!points.length) {
    host.innerHTML = '<div class="pxp-head"><span class="pxp-note">' + esc(note) + '</span></div>' +
      '<p class="empty-state">No records with both Total CoS and Mean OGIP to plot.</p>';
    return;
  }

  // Fixed 900x440 viewBox scaled to the container width; the CSS max-width
  // keeps the on-screen scale near 1:1 so the 11px tick text stays 11px-ish.
  var W = 900, H = 440;
  var m = { top: 16, right: 20, bottom: 52, left: 64 };
  var iw = W - m.left - m.right;
  var ih = H - m.top - m.bottom;
  var yAxis = niceCeil(points.reduce(function (best, p) { return Math.max(best, p.ogip); }, 0));
  var xOf = function (cos) { return m.left + Math.min(Math.max(cos, 0), 1) * iw; };
  var yOf = function (ogip) { return m.top + ih - (Math.min(ogip, yAxis.max) / yAxis.max) * ih; };

  var svg = [];
  // Gridlines + ticks: x every 0.1 CoS, y every nice step.
  for (var cos = 0; cos <= 1.001; cos += 0.1) {
    var gx = xOf(cos);
    if (cos > 0.001) svg.push('<line class="pxp-grid" x1="' + gx + '" y1="' + m.top + '" x2="' + gx + '" y2="' + (m.top + ih) + '"/>');
    svg.push('<text class="pxp-tick" x="' + gx + '" y="' + (m.top + ih + 16) + '" text-anchor="middle">' + (Math.round(cos * 10) / 10) + '</text>');
  }
  for (var tick = 0; tick <= yAxis.max + 0.001; tick += yAxis.step) {
    var gy = yOf(tick);
    if (tick > 0.001) svg.push('<line class="pxp-grid" x1="' + m.left + '" y1="' + gy + '" x2="' + (m.left + iw) + '" y2="' + gy + '"/>');
    svg.push('<text class="pxp-tick" x="' + (m.left - 8) + '" y="' + (gy + 3.5) + '" text-anchor="end">' + tick.toLocaleString('en-US') + '</text>');
  }
  // Axes + titles.
  svg.push('<line class="pxp-axis" x1="' + m.left + '" y1="' + (m.top + ih) + '" x2="' + (m.left + iw) + '" y2="' + (m.top + ih) + '"/>');
  svg.push('<line class="pxp-axis" x1="' + m.left + '" y1="' + m.top + '" x2="' + m.left + '" y2="' + (m.top + ih) + '"/>');
  svg.push('<text class="pxp-axis-title" x="' + (m.left + iw / 2) + '" y="' + (H - 12) + '" text-anchor="middle">Total CoS (fraction)</text>');
  svg.push('<text class="pxp-axis-title" transform="rotate(-90)" x="' + -(m.top + ih / 2) + '" y="16" text-anchor="middle">Mean OGIP (BCF)</text>');

  // Cutoffs and quadrant names. Labels sit at each quadrant's midpoint,
  // nudged off the vertical center so they clear the densest dot bands.
  var cutX = xOf(COS_CUTOFF);
  var cutY = yOf(OGIP_CUTOFF);
  svg.push('<line class="pxp-cut" x1="' + cutX + '" y1="' + m.top + '" x2="' + cutX + '" y2="' + (m.top + ih) + '"/>');
  svg.push('<line class="pxp-cut" x1="' + m.left + '" y1="' + cutY + '" x2="' + (m.left + iw) + '" y2="' + cutY + '"/>');
  var leftMid = (m.left + cutX) / 2;
  var rightMid = (cutX + m.left + iw) / 2;
  var topMid = (m.top + cutY) / 2;
  var bottomMid = (cutY + m.top + ih) / 2;
  svg.push('<text class="pxp-quad" x="' + leftMid + '" y="' + topMid + '" text-anchor="middle">Risk Takers</text>');
  svg.push('<text class="pxp-quad" x="' + rightMid + '" y="' + topMid + '" text-anchor="middle">Super Stars</text>');
  svg.push('<text class="pxp-quad" x="' + leftMid + '" y="' + bottomMid + '" text-anchor="middle">Dogs</text>');
  svg.push('<text class="pxp-quad" x="' + rightMid + '" y="' + bottomMid + '" text-anchor="middle">Value Hunter</text>');

  // Dots (with the 2px surface ring) plus an invisible, larger hit circle
  // per point so hovering small marks is forgiving.
  points.forEach(function (point, index) {
    var px = xOf(point.cos);
    var py = yOf(point.ogip);
    svg.push('<circle class="pxp-dot" data-index="' + index + '" cx="' + px + '" cy="' + py + '" r="5"/>');
    svg.push('<circle class="pxp-hit" data-index="' + index + '" cx="' + px + '" cy="' + py + '" r="11"/>');
  });

  host.innerHTML =
    '<div class="pxp-head"><span class="pxp-note">' + esc(note) + '</span></div>' +
    '<svg class="pxp-svg" viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="Cross plot of total chance of success against mean OGIP with cutoffs at 50% CoS and ' + OGIP_CUTOFF + ' BCF">' +
    svg.join('') + '</svg>' +
    '<div class="pxp-tooltip" hidden></div>';

  // Per-mark hover tooltip. The tooltip is display-only (pointer-events:
  // none in CSS) so it can never steal the hover from the hit circle.
  wireCrossPlotTooltip(host, points);
}

function wireCrossPlotTooltip(host, points) {
  var tooltip = host.querySelector('.pxp-tooltip');
  all('.pxp-hit', host).forEach(function (hit) {
    var index = Number(hit.getAttribute('data-index'));
    var point = points[index];
    var dot = host.querySelector('.pxp-dot[data-index="' + index + '"]');
    hit.addEventListener('mouseenter', function () {
      if (dot) dot.classList.add('is-hover');
      tooltip.innerHTML = '<b>' + esc(point.name) + '</b>' +
        'CoS ' + esc(Math.round(point.cos * 1000) / 10) + '% · OGIP ' + esc(fmtBcf(point.ogip)) + ' BCF';
      tooltip.hidden = false;
      var hostRect = host.getBoundingClientRect();
      var dotRect = hit.getBoundingClientRect();
      var left = dotRect.left + dotRect.width / 2 - hostRect.left;
      tooltip.style.left = Math.min(Math.max(left, 70), hostRect.width - 70) + 'px';
      tooltip.style.top = (dotRect.top - hostRect.top - 8) + 'px';
    });
    hit.addEventListener('mouseleave', function () {
      if (dot) dot.classList.remove('is-hover');
      tooltip.hidden = true;
    });
  });
}

// ---------------------------------------------------------------------------
// Cross plot dialog plumbing: the full portfolio rowset (refreshed on every
// portfolio fetch), the dialog's three filter selects, and the open/close
// wiring (initPortfolioAnalysis, called once from main.js boot).
// ---------------------------------------------------------------------------

var crossPlotRows = [];
export function setCrossPlotRows(rows) {
  crossPlotRows = rows || [];
  renderCrossPlotThumb();
}

// The overview tile. Draws the rowset the dialog would OPEN with, so enlarging
// never changes what is on screen. Silently absent when the tile is not in the
// DOM (test fixtures, and the dialog-only tests).
export function renderCrossPlotThumb() {
  if (!byId('portfolio-crossplot-thumb')) return;
  renderCrossPlot(filteredCrossPlotRows(), 'portfolio-crossplot-thumb');
}

// The plot's cutoff quadrants double as a filter dimension. Cutoff values
// sit on the "high" side (CoS 50% / OGIP 10 BCF count as Super Stars-ward);
// a row missing either measure has no quadrant ('') and can never match a
// quadrant selection -- it isn't plottable anyway.
export var QUADRANT_LABELS = ['Super Stars', 'Value Hunter', 'Risk Takers', 'Dogs'];
export function quadrantOf(row) {
  var cos = Number(row.total_cos);
  var ogip = Number(row.mean_ogip);
  if (row.total_cos === '' || row.total_cos == null || !isFinite(cos)) return '';
  if (row.mean_ogip === '' || row.mean_ogip == null || !isFinite(ogip)) return '';
  var highCos = cos >= COS_CUTOFF * 100;
  var bigOgip = ogip >= OGIP_CUTOFF;
  if (highCos) return bigOgip ? 'Super Stars' : 'Value Hunter';
  return bigOgip ? 'Risk Takers' : 'Dogs';
}

// Each checklist filters on one row key (multi-select; an empty checklist =
// no constraint). Option lists are the distinct values present in the
// rowset, except Quadrant's fixed four.
var CROSSPLOT_FILTERS = [
  { id: 'crossplot-field-filter', key: 'gas_field', numeric: false },
  { id: 'crossplot-year-filter', key: 'year', numeric: true },
  { id: 'crossplot-status-filter', key: 'status', numeric: false },
  { id: 'crossplot-quadrant-filter', key: 'quadrant', fixed: QUADRANT_LABELS }
];

function checkedValues(container) {
  return all('input[type="checkbox"]:checked', container).map(function (box) { return box.value; });
}

function distinctColumn(rows, key, numeric) {
  var seen = {};
  var values = [];
  rows.forEach(function (row) {
    var value = row[key];
    if (value === null || value === undefined || value === '') return;
    var text = String(value);
    if (!seen[text]) { seen[text] = true; values.push(text); }
  });
  values.sort(numeric
    ? function (a, b) { return Number(a) - Number(b); }
    : function (a, b) { return a.localeCompare(b); });
  return values;
}

// Rebuild the checklists from the current rowset, preserving still-valid
// ticks across repopulation (reopening the dialog keeps the user's scope).
function populateCrossPlotFilters() {
  CROSSPLOT_FILTERS.forEach(function (filter) {
    var container = byId(filter.id);
    if (!container) return;
    var previous = checkedValues(container);
    var values = filter.fixed || distinctColumn(crossPlotRows, filter.key, filter.numeric);
    container.innerHTML = values.map(function (value) {
      var checked = previous.indexOf(value) >= 0 ? ' checked' : '';
      return '<label class="portfolio-filter-option"><input type="checkbox" value="' + esc(value) + '"' + checked +
        '><span>' + esc(value) + '</span></label>';
    }).join('');
  });
}

export function filteredCrossPlotRows() {
  var selections = CROSSPLOT_FILTERS.map(function (filter) {
    var container = byId(filter.id);
    return { filter: filter, selected: container ? checkedValues(container) : [] };
  });
  return crossPlotRows.filter(function (row) {
    return selections.every(function (entry) {
      if (!entry.selected.length) return true;
      var value = entry.filter.key === 'quadrant'
        ? quadrantOf(row)
        : String(row[entry.filter.key] == null ? '' : row[entry.filter.key]);
      return entry.selected.indexOf(value) >= 0;
    });
  });
}

export function openCrossPlotDialog() {
  var dialog = byId('crossplot-dialog');
  if (!dialog) return;
  populateCrossPlotFilters();
  renderCrossPlot(filteredCrossPlotRows());
  if (!dialog.open) dialog.showModal();
}

// One-time wiring for the static dialog controls. Elements are optional
// (test fixtures build partial DOMs), hence the null guards.
export function initPortfolioAnalysis() {
  var trigger = byId('open-crossplot');
  if (trigger) trigger.addEventListener('click', openCrossPlotDialog);
  var close = byId('crossplot-close');
  if (close) close.addEventListener('click', function () {
    var dialog = byId('crossplot-dialog');
    if (dialog && dialog.open) dialog.close();
  });
  // One delegated change listener per checklist container: checkbox change
  // events bubble, and the containers are static while their options are
  // rebuilt on every open.
  CROSSPLOT_FILTERS.forEach(function (filter) {
    var container = byId(filter.id);
    if (container) container.addEventListener('change', function () {
      renderCrossPlot(filteredCrossPlotRows());
      // Keep the tile showing what the dialog shows, so closing the dialog
      // does not reveal a stale thumbnail.
      renderCrossPlotThumb();
    });
  });
}
