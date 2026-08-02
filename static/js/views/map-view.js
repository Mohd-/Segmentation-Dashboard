/* =========================================================================
   The Map tab.

   One screen, four surfaces, all of them overlaid on a single canvas:

     sidebar      the ORDERED layer list — visibility, color, feature count,
                  zoom-to, and the up/down controls that ARE the draw order
     summary      a floating, collapsible panel of live totals (top-left)
     toolbox      pointer / measure / clear / colors (top-right)
     tooltip      feature attributes and the well<->polygon association

   This module is the controller: it owns the DOM, the fetches and the
   interaction wiring. Everything with a rule in it lives next door and is
   testable without a browser tab — map/map-store.js (order, association,
   summary, persistence), map/map-canvas.js (projection, hit test, painting),
   map/map-tools.js (measurement).

   TWO INTEGRATION RULES matter more than anything else here:

   1. The tab is display:none until activated, and a canvas measured while
      hidden is 0x0. So the view initialises LAZILY on first activation and
      resize()s on EVERY activation. Nothing draws before it has a size.
   2. Layer geometry is cached for the session (it is static shapefile data,
      and it is the expensive fetch); the wells overlay is refetched on every
      activation, because a project's coordinates or OGIP can change while
      the user is on another tab.
   ========================================================================= */

import { byId, all, esc } from '../dom.js';
import { ICONS } from '../icons.js';
import {
  LayerStore, WELLS_ID, readMapState, writeMapState,
  formatOgip, ogipValue, polygonLabel, featureKey
} from '../map/map-store.js';
import { MapCanvas } from '../map/map-canvas.js';
import { MeasureTool, TOOL_POINTER, TOOL_MEASURE, formatDistance } from '../map/map-tools.js';
import { fetchMapLayers, fetchMapLayer, fetchMapWells } from '../map/map-api.js';

var store = null;
var view = null;
var measure = null;
var tool = TOOL_POINTER;
var booted = false;
var layersLoaded = false;
var colorPanelOpen = false;
var lastHitId = '';         // see handlePick — the tooltip is not rebuilt per pixel
var tooltipSize = [0, 0];   // its measured box, so a move costs no layout read
var wellsRequest = 0;       // see loadWells — the stale-response guard

var EMPTY_HINT = 'No shapefile layers found. Drop shapefile sets into data/map/layers/ on the server.';

/* -------------------------------------------------------------------------
   Display helpers (pure — exported for the tests)
   ------------------------------------------------------------------------- */

// The summary's Total Mean OGIP: summed at full precision upstream, rounded
// exactly once, here, for display.
export function formatOgipTotal(value) {
  var numeric = Number(value);
  return (isFinite(numeric) ? numeric : 0).toFixed(1);
}

/* The hover text for a failed layer's warning marker. A backend error body
   can be a whole HTML page, so only its first line survives and only 160
   characters of that — the same discipline the sidebar's load-failure hint
   uses, applied to a per-row title attribute. */
export function errorHint(message) {
  var text = String(message === null || message === undefined ? '' : message).split('\n')[0].trim();
  return 'Could not load this layer: ' + (text ? text.slice(0, 160) : 'unknown error');
}

export function wellLabel(well) {
  return String((well && (well.project_name || well.project_id)) || 'Well');
}

/* The hovered WELL's tooltip: who it is, where it is in the workflow, its
   mean OGIP, and which visible polygon(s) contain it. Every value is escaped
   — these strings are project names and shapefile attributes, i.e. data. */
export function wellTooltipHtml(well, hits) {
  var rows = [
    ['Stage', well && well.display_stage],
    ['Status', well && well.overall_status],
    ['Field', well && well.field]
  ].filter(function (row) { return row[1] !== null && row[1] !== undefined && row[1] !== ''; })
    .map(function (row) { return '<tr><td class="k">' + esc(row[0]) + '</td><td>' + esc(row[1]) + '</td></tr>'; })
    .join('');
  var inside = (hits || []).length
    ? (hits || []).map(function (hit) { return esc(hit.label || hit.layerName); }).join(', ')
    : '—';
  return '<div class="map-tt-title">' + esc(wellLabel(well)) + '</div>'
    + (rows ? '<table>' + rows + '</table>' : '')
    + '<div class="map-tt-metric">Mean OGIP: ' + esc(formatOgip(well && well.mean_gas_bcf))
    + (formatOgip(well && well.mean_gas_bcf) === '—' ? '' : ' BCF') + '</div>'
    + '<div class="map-tt-inside"><span class="k">Inside</span> ' + inside + '</div>';
}

/* The hovered POLYGON's tooltip: the ported attribute table, plus the wells
   the association found inside this exact feature — count, each well's name
   and mean OGIP, and their summed mean OGIP (nulls contribute 0 to the sum
   even though they display as an em dash on their own row). */
export function polygonTooltipHtml(layer, feature, wells) {
  var props = (feature && feature.properties) || {};
  var attrRows = Object.keys(props).map(function (key) {
    return '<tr><td class="k">' + esc(key) + '</td><td>' + esc(props[key]) + '</td></tr>';
  }).join('');
  var title = layer && layer.isBorders ? 'Border' : polygonLabel(feature, layer && layer.name);
  var list = wells || [];
  var total = list.reduce(function (sum, well) { return sum + ogipValue(well.mean_gas_bcf); }, 0);
  var wellRows = list.map(function (well) {
    var ogip = formatOgip(well.mean_gas_bcf);
    return '<tr><td>' + esc(wellLabel(well)) + '</td><td class="n">' + esc(ogip) + '</td></tr>';
  }).join('');
  var section = '<div class="map-tt-section">'
    + '<div class="map-tt-section-head">Wells inside (' + list.length + ')</div>'
    + (list.length
      ? '<table class="map-tt-wells">' + wellRows + '</table>'
        + '<div class="map-tt-total">Total Mean OGIP: ' + esc(formatOgipTotal(total)) + ' BCF</div>'
      : '<div class="map-tt-empty">No wells inside this polygon.</div>')
    + '</div>';
  return '<div class="map-tt-title">' + esc(title) + '</div>'
    + '<div class="map-tt-sub">' + esc((layer && layer.name) || '') + '</div>'
    + (attrRows ? '<table>' + attrRows + '</table>' : '<em class="map-tt-empty">No attributes</em>')
    + (layer && layer.isBorders ? '' : section);
}

export function summaryHtml(summary) {
  var rows = [
    ['Visible layers', String(summary.visibleLayers)],
    ['Wells plotted', String(summary.wellsPlotted)],
    ['Wells in polygons', String(summary.wellsInside)],
    ['Total Mean OGIP', formatOgipTotal(summary.totalOgip) + ' BCF']
  ];
  var body = rows.map(function (row) {
    return '<div class="map-summary-row"><span class="map-summary-key">' + esc(row[0])
      + '</span><span class="map-summary-value">' + esc(row[1]) + '</span></div>';
  }).join('');
  var note = summary.wellsPlotted === 0
    ? '<p class="map-summary-note">No project coordinates are recorded yet.</p>'
    : '';
  return body + note;
}

/* -------------------------------------------------------------------------
   Sidebar
   ------------------------------------------------------------------------- */

// Top -> bottom, i.e. the reverse of the draw order: the wells overlay is
// pinned at the head of the list, the borders pseudo-layer at its foot, and
// only what sits between them can be reordered.
function sidebarRows() {
  var rows = [{
    id: WELLS_ID,
    label: 'Wells (projects)',
    count: store.plottedWells().length,
    color: store.wellsColor,
    visible: store.wellsVisible,
    pinned: true,
    layer: null
  }];
  store.sidebarOrder().forEach(function (layer) {
    rows.push({
      id: layer.name,
      label: layer.isBorders ? 'Country borders' : layer.name,
      count: layer.featureCount,
      color: layer.color,
      visible: layer.visible,
      pinned: !!layer.isBorders,
      error: layer.error || '',
      layer: layer
    });
  });
  return rows;
}

function renderSidebar() {
  var list = byId('map-layer-list');
  if (!list) return;
  var rows = sidebarRows();
  var hasShapefiles = rows.some(function (row) { return row.layer && !row.layer.isBorders; });
  list.innerHTML = rows.map(function (row, index) {
    var checkId = 'map-layer-chk-' + index;
    // A layer that was listed but could not be fetched is otherwise just an
    // empty ticked checkbox — nothing on the canvas and no reason given. The
    // marker carries the server's own message in its tooltip.
    var warning = row.error
      ? '<span class="map-layer-error" role="img" title="' + esc(errorHint(row.error))
        + '" aria-label="' + esc(errorHint(row.error)) + '">⚠</span>'
      : '';
    var actions = row.pinned ? '' :
      '<button type="button" class="map-layer-btn map-layer-move" data-dir="1" title="Move up (draw above)" aria-label="Move ' + esc(row.label) + ' up">' + ICONS['chevron-up'] + '</button>'
      + '<button type="button" class="map-layer-btn map-layer-move" data-dir="-1" title="Move down (draw below)" aria-label="Move ' + esc(row.label) + ' down">' + ICONS['chevron-down'] + '</button>';
    return '<div class="map-layer" data-layer="' + esc(row.id) + '">'
      + '<input type="checkbox" class="map-layer-check" id="' + checkId + '"' + (row.visible ? ' checked' : '') + '>'
      + '<button type="button" class="map-swatch" style="background:' + esc(row.color) + '" '
      + 'title="Change color" aria-label="Change color for ' + esc(row.label) + '"></button>'
      + '<label class="map-layer-name" for="' + checkId + '" title="' + esc(row.label) + '">' + esc(row.label) + '</label>'
      + warning
      + '<span class="map-layer-count">' + esc(row.count === null || row.count === undefined ? '' : row.count) + '</span>'
      + '<span class="map-layer-actions">' + actions
      + '<button type="button" class="map-layer-btn map-layer-zoom" title="Zoom to layer" aria-label="Zoom to ' + esc(row.label) + '">' + ICONS['target'] + '</button>'
      + '</span></div>';
  }).join('') + (hasShapefiles ? '' : '<p class="map-empty-hint">' + esc(EMPTY_HINT) + '</p>');

  all('.map-layer', list).forEach(function (rowEl) {
    var id = rowEl.getAttribute('data-layer');
    rowEl.querySelector('.map-layer-check').addEventListener('change', function (event) {
      store.setVisible(id, event.target.checked);
      persist();
      // Repaint immediately (a hide is instant), and again once a
      // just-revealed layer's geometry has actually arrived.
      afterDataChange();
      var layer = store.layers.get(id);
      if (layer && layer.visible && !layer.features) store.ensureLoaded(layer).then(function () { afterDataChange(); });
    });
    rowEl.querySelector('.map-swatch').addEventListener('click', function () { openColorFor(id); });
    all('.map-layer-move', rowEl).forEach(function (button) {
      button.addEventListener('click', function () {
        if (!store.moveLayer(id, Number(button.getAttribute('data-dir')))) return;
        persist();
        renderSidebar();
        afterDataChange();
      });
    });
    rowEl.querySelector('.map-layer-zoom').addEventListener('click', function () { zoomToRow(id); });
  });
}

function zoomToRow(id) {
  if (id === WELLS_ID) { view.fitBbox(store.wellsBbox()); return; }
  var layer = store.layers.get(id);
  if (!layer) return;
  if (!layer.visible) { store.setVisible(id, true); persist(); renderSidebar(); }
  store.ensureLoaded(layer).then(function () {
    afterDataChange();
    view.fitBbox(layer.bbox);
  });
}

/* -------------------------------------------------------------------------
   Summary panel
   ------------------------------------------------------------------------- */

function renderSummary() {
  var body = byId('map-summary-body');
  if (!body) return;
  body.innerHTML = summaryHtml(store.summary());
  applySummaryCollapse();
}

function applySummaryCollapse() {
  var panel = byId('map-summary');
  var toggle = byId('map-summary-toggle');
  var body = byId('map-summary-body');
  if (!panel || !toggle || !body) return;
  var collapsed = !!store.summaryCollapsed;
  panel.classList.toggle('is-collapsed', collapsed);
  body.hidden = collapsed;
  toggle.setAttribute('aria-expanded', String(!collapsed));
}

/* -------------------------------------------------------------------------
   Toolbox: pointer / measure / clear / colors
   ------------------------------------------------------------------------- */

function setTool(next) {
  tool = next;
  view.tool = next;
  // Leaving the measure tool FINISHES the chain rather than erasing it — the
  // measurement is the answer the user asked for, and only Clear discards it.
  if (next !== TOOL_MEASURE && measure.drafting) measure.finish();
  var pointer = byId('map-tool-pointer');
  var ruler = byId('map-tool-measure');
  if (pointer) pointer.setAttribute('aria-pressed', String(next === TOOL_POINTER));
  if (ruler) ruler.setAttribute('aria-pressed', String(next === TOOL_MEASURE));
  var canvas = byId('map-canvas');
  if (canvas) canvas.classList.toggle('measuring', next === TOOL_MEASURE);
  renderMeasureReadout();
  view.requestRender();
}

function renderMeasureReadout() {
  var readout = byId('map-measure-readout');
  if (!readout) return;
  if (measure.isEmpty) {
    readout.textContent = tool === TOOL_MEASURE ? 'Click to add points' : '';
    readout.hidden = tool !== TOOL_MEASURE;
    return;
  }
  readout.hidden = false;
  readout.textContent = formatDistance(measure.total()) + (measure.drafting ? ' (drawing…)' : '');
}

function renderColorPanel() {
  var panel = byId('map-color-panel');
  if (!panel) return;
  var rows = sidebarRows();
  panel.innerHTML = '<div class="map-color-head">Layer colors</div>' + rows.map(function (row, index) {
    var inputId = 'map-color-' + index;
    return '<label class="map-color-row" for="' + inputId + '">'
      + '<input type="color" id="' + inputId + '" value="' + esc(row.color) + '" data-layer="' + esc(row.id) + '">'
      + '<span>' + esc(row.label) + '</span></label>';
  }).join('');
  all('input[type="color"]', panel).forEach(function (input) {
    input.addEventListener('input', function () {
      if (!store.setColor(input.getAttribute('data-layer'), input.value)) return;
      persist();
      renderSidebar();
      view.requestRender();
    });
  });
}

function setColorPanelOpen(open) {
  colorPanelOpen = !!open;
  var panel = byId('map-color-panel');
  var toggle = byId('map-colors-toggle');
  if (panel) panel.hidden = !colorPanelOpen;
  if (toggle) toggle.setAttribute('aria-expanded', String(colorPanelOpen));
  if (colorPanelOpen) renderColorPanel();
}

// The sidebar swatch is a shortcut to the same native color input, not a
// second picker: it opens the one panel and pops that row's input.
function openColorFor(id) {
  setColorPanelOpen(true);
  var panel = byId('map-color-panel');
  if (!panel) return;
  var input = panel.querySelector('input[data-layer="' + cssEscape(id) + '"]');
  if (input) { input.focus(); input.click(); }
}

function cssEscape(value) { return String(value).replace(/["\\]/g, '\\$&'); }

/* -------------------------------------------------------------------------
   Tooltip + readout
   ------------------------------------------------------------------------- */

function hideHoverChrome() {
  var readout = byId('map-readout');
  var tooltip = byId('map-tooltip');
  if (readout) readout.hidden = true;
  if (tooltip) tooltip.hidden = true;
  lastHitId = '';   // the next hover rebuilds, even onto the same thing
}

/* What the tooltip is CURRENTLY describing, as a comparable string. Hit
   objects are rebuilt on every hit-test, so identity has to be by address —
   which well, or which feature of which layer — not by reference. Empty
   string means "nothing", and never equals a real hit. */
export function hitIdentity(hit) {
  if (!hit) return '';
  if (hit.kind === 'well') return 'well#' + hit.index;
  return 'feature#' + ((hit.layer && hit.layer.name) || '') + '#' + hit.featureIndex;
}

// Keep the card inside the stage (ported from the source viewer). The box is
// passed in rather than measured here, so repositioning an unchanged tooltip
// reads no layout at all.
function positionTooltip(tooltip, sx, sy, width, height) {
  var pad = 14;
  var left = sx + pad;
  var top = sy + pad;
  if (left + width > view.width) left = sx - width - pad;
  if (top + height > view.height) top = sy - height - pad;
  tooltip.style.left = Math.max(0, left) + 'px';
  tooltip.style.top = Math.max(0, top) + 'px';
}

function showTooltip(html, sx, sy) {
  var tooltip = byId('map-tooltip');
  if (!tooltip) return;
  tooltip.innerHTML = html;
  tooltip.hidden = false;
  // The one place the box is measured: an innerHTML write invalidates layout,
  // so this read is the flush that was going to happen anyway.
  tooltipSize = [tooltip.offsetWidth, tooltip.offsetHeight];
  positionTooltip(tooltip, sx, sy, tooltipSize[0], tooltipSize[1]);
}

/* A hover runs once per frame across the whole canvas, but the ANSWER changes
   only when the cursor crosses into something else. While it does not, the
   card just follows the pointer: no innerHTML rebuild, no offsetWidth/Height
   read, i.e. no forced layout on a frame that had nothing new to say. */
function handlePick(hit, sx, sy) {
  var tooltip = byId('map-tooltip');
  if (!hit) { lastHitId = ''; if (tooltip) tooltip.hidden = true; return; }
  var identity = hitIdentity(hit);
  if (tooltip && identity === lastHitId && !tooltip.hidden) {
    positionTooltip(tooltip, sx, sy, tooltipSize[0], tooltipSize[1]);
    return;
  }
  lastHitId = identity;
  if (hit.kind === 'well') {
    showTooltip(wellTooltipHtml(hit.well, store.associations().polysFor[hit.index] || []), sx, sy);
    return;
  }
  var indices = store.associations().wellsFor[featureKey(hit.layer.name, hit.featureIndex)] || [];
  var wells = indices.map(function (index) { return store.wells[index]; });
  showTooltip(polygonTooltipHtml(hit.layer, hit.feature, wells), sx, sy);
}

/* -------------------------------------------------------------------------
   Wiring + data
   ------------------------------------------------------------------------- */

function persist() { writeMapState(store.toState()); }

// One place to say "the picture and its numbers are stale": the association
// cache is dropped, the summary recomputed, the canvas repainted.
function afterDataChange() {
  store.invalidate();
  lastHitId = '';   // the same hit can now have different contents
  renderSummary();
  view.requestRender();
}

function loadLayers() {
  var list = byId('map-layer-list');
  if (list) list.innerHTML = '<p class="map-empty-hint">Loading layers…</p>';
  return fetchMapLayers().then(function (meta) {
    store.setLayers(meta);
    layersLoaded = true;
    renderSidebar();
    return store.loadVisible();
  }).then(function () {
    afterDataChange();
    view.fitBbox(store.visibleBbox());
  }).catch(function (err) {
    layersLoaded = false;
    // A backend error body can be a whole HTML page; the sidebar shows the
    // first line of it, escaped, rather than a wall of markup.
    var detail = String((err && err.message) || 'unknown error').split('\n')[0].slice(0, 120);
    if (list) list.innerHTML = '<p class="map-empty-hint">Could not load layers: ' + esc(detail) + '</p>';
  });
}

/* The overlay is refetched on EVERY activation, so two of them can be in
   flight at once (tab away and back before the first lands). Each request
   carries a sequence number and only the newest one is allowed to write —
   otherwise a slow first response can land after a fast second and put the
   older coordinates back on the map. */
function loadWells() {
  wellsRequest += 1;
  var request = wellsRequest;
  return fetchMapWells().then(function (wells) {
    if (request !== wellsRequest) return;
    store.setWells(wells);
    renderSidebar();
    afterDataChange();
  }).catch(function () {
    if (request !== wellsRequest) return;
    store.setWells([]);
    afterDataChange();
  });
}

function wire() {
  var canvas = byId('map-canvas');

  view.onHover = function (wx, wy) {
    var readout = byId('map-readout');
    if (!readout) return;
    readout.hidden = false;
    readout.textContent = 'E ' + wx.toFixed(0) + '   N ' + wy.toFixed(0) + '   (UTM37N m)';
  };
  view.onPick = handlePick;
  view.onLeave = hideHoverChrome;
  view.onMeasureChange = renderMeasureReadout;

  byId('map-fit-all').addEventListener('click', function () {
    store.loadVisible().then(function () {
      afterDataChange();
      view.fitBbox(store.visibleBbox());
    });
  });
  byId('map-reload').addEventListener('click', function () { loadLayers(); loadWells(); });
  byId('map-zoom-in').addEventListener('click', function () { view.zoomCenter(1.3); });
  byId('map-zoom-out').addEventListener('click', function () { view.zoomCenter(1 / 1.3); });

  byId('map-tool-pointer').addEventListener('click', function () { setTool(TOOL_POINTER); });
  byId('map-tool-measure').addEventListener('click', function () { setTool(TOOL_MEASURE); });
  byId('map-measure-clear').addEventListener('click', function () {
    measure.clear();
    renderMeasureReadout();
    view.requestRender();
  });
  byId('map-colors-toggle').addEventListener('click', function () { setColorPanelOpen(!colorPanelOpen); });

  byId('map-summary-toggle').addEventListener('click', function () {
    store.summaryCollapsed = !store.summaryCollapsed;
    applySummaryCollapse();
    persist();
  });

  // The floating color panel follows the app's single-open-panel discipline:
  // an outside click or Escape dismisses it. The Escape handler is scoped to
  // this tab and to something actually being open, so it never competes with
  // the other document-level Escape handlers in the app (each of which guards
  // on its own open state the same way).
  document.addEventListener('click', function (event) {
    if (!colorPanelOpen) return;
    var panel = byId('map-color-panel');
    var toggle = byId('map-colors-toggle');
    if (!panel || panel.contains(event.target)) return;
    if (toggle && toggle.contains(event.target)) return;
    var swatch = event.target.closest ? event.target.closest('.map-swatch') : null;
    if (swatch) return;
    setColorPanelOpen(false);
  });
  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape') return;
    var tab = byId('tab-map');
    if (!tab || !tab.classList.contains('active')) return;
    if (colorPanelOpen) { setColorPanelOpen(false); return; }
    if (measure.drafting) {
      measure.finish();
      renderMeasureReadout();
      view.requestRender();
    }
  });

  if (canvas) canvas.addEventListener('contextmenu', function (event) { event.preventDefault(); });
}

function boot() {
  store = new LayerStore(fetchMapLayer);
  store.applyState(readMapState());
  measure = new MeasureTool();
  view = new MapCanvas(byId('map-canvas'), store);
  view.measure = measure;
  wire();
  view.resize();
  setTool(TOOL_POINTER);
  applySummaryCollapse();
  renderSummary();
  booted = true;
  return loadLayers();
}

/* The one entry point main.js calls from showTab('map').

   First activation boots the whole view; every activation (including the
   first) re-measures the canvas — it was 0x0 while the tab was hidden — and
   refetches the wells overlay. Layer GEOMETRY is deliberately not refetched:
   it is static and it is the expensive call. */
export function refreshMap() {
  if (!byId('map-canvas')) return Promise.resolve();
  if (!booted) return boot().then(loadWells);
  view.resize();
  if (!layersLoaded) loadLayers();
  return loadWells();
}

// Test seam: the module keeps one live view per page, so the tests need a way
// to drive a freshly-constructed store without an activation cycle.
export function __mapInternals() {
  return { store: store, view: view, measure: measure, tool: tool };
}
