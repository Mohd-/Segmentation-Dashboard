// Tests for the Map tab: js/map/map-store.js (geometry, association,
// ordering, persistence, summary), js/map/map-tools.js (measurement) and the
// pure tooltip/summary builders exported by js/views/map-view.js.
//
// Everything here is logic, not painting: the canvas engine's own maths
// (worldToScreen/zoomAt) needs a laid-out canvas, so what is pinned instead
// is every rule a wrong answer would silently corrupt — which polygon a well
// falls in, which layer draws on top, what the totals say, and whether a
// hostile shapefile attribute can reach the DOM as markup.
import { test, assert, fixture, mockFetch, waitFor } from './harness.js';
import {
  LayerStore, WELLS_ID, MAP_STATE_KEY, PALETTE,
  pointInRings, polygonLabel, formatOgip, ogipValue, hasCoords, featureKey,
  computeAssociations, computeSummary, unionBbox, areaMidpoint,
  quadrantOfWell, filterWells, mapFilterOptions,
  readMapState, writeMapState, normalizeState
} from '../js/map/map-store.js';
import { utm37ToWgs84, formatLonLat, formatUtm37Coordinate } from '../js/map/utm37.js';
import {
  MeasureTool, formatDistance, segmentLengths, cumulativeLengths, totalDistance
} from '../js/map/map-tools.js';
import {
  MapCanvas, withAlpha, WELL_LABEL_MIN_SCALE,
  clampScale, normalizeWheelDelta, MIN_SCALE, MAX_SCALE, WHEEL_LINE_PX
} from '../js/map/map-canvas.js';
import {
  wellTooltipHtml, polygonTooltipHtml, summaryHtml, formatOgipTotal, formatAreaTotal, wellLabel,
  hitIdentity, errorHint, refreshMap
} from '../js/views/map-view.js';

/* -------------------------------------------------------------------------
   Fixtures
   ------------------------------------------------------------------------- */

// A 10x10 square with a 2..8 square hole punched out of it.
var SQUARE_WITH_HOLE = [
  [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
  [[2, 2], [8, 2], [8, 8], [2, 8], [2, 2]]
];
var SQUARE_0_10 = [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]];
var SQUARE_5_15 = [[[5, 5], [15, 5], [15, 15], [5, 15], [5, 5]]];

function memStorage() {
  var data = {};
  return {
    getItem: function (key) { return Object.prototype.hasOwnProperty.call(data, key) ? data[key] : null; },
    setItem: function (key, value) { data[key] = String(value); },
    removeItem: function (key) { delete data[key]; },
    _raw: data
  };
}

function polygonLayer(name, rings, options) {
  var opts = options || {};
  return {
    name: name,
    geomType: 'polygon',
    isBorders: !!opts.isBorders,
    visible: opts.visible === undefined ? true : opts.visible,
    color: '#4a9eff',
    features: rings.map(function (entry) {
      return { geometry: { type: 'polygon', coordinates: entry.rings }, properties: entry.props || {} };
    })
  };
}

function layerMeta() {
  return [
    { name: 'borders', geom_type: 'polygon', feature_count: 5, bbox: [0, 0, 100, 100], is_borders: true },
    { name: 'blocks', geom_type: 'polygon', feature_count: 2, bbox: [0, 0, 10, 10] },
    { name: 'fields', geom_type: 'polygon', feature_count: 3, bbox: [5, 5, 15, 15] },
    { name: 'seismic', geom_type: 'line', feature_count: 1, bbox: null }
  ];
}

function names(layers) { return layers.map(function (layer) { return layer.name; }); }

function assertClose(actual, expected, tolerance, message) {
  assert.ok(Math.abs(actual - expected) <= tolerance,
    (message || 'values differ') + ': expected ' + expected + ' ± ' + tolerance + ', got ' + actual);
}

/* -------------------------------------------------------------------------
   pointInRings — even-odd, holes included
   ------------------------------------------------------------------------- */

test('map: pointInRings reports a point inside a simple ring', function () {
  assert.equal(pointInRings(5, 5, SQUARE_0_10), true);
  assert.equal(pointInRings(0.5, 9.5, SQUARE_0_10), true, 'near a corner but inside');
});

test('map: pointInRings reports a point outside a simple ring', function () {
  assert.equal(pointInRings(-1, 5, SQUARE_0_10), false);
  assert.equal(pointInRings(11, 5, SQUARE_0_10), false);
  assert.equal(pointInRings(5, 20, SQUARE_0_10), false);
});

test('map: pointInRings treats a hole as outside (even-odd), matching the evenodd fill', function () {
  assert.equal(pointInRings(5, 5, SQUARE_WITH_HOLE), false, 'inside the hole is outside the feature');
  assert.equal(pointInRings(1, 1, SQUARE_WITH_HOLE), true, 'between the outer ring and the hole is inside');
  assert.equal(pointInRings(9, 9, SQUARE_WITH_HOLE), true);
});

test('map: pointInRings tolerates missing/empty ring data instead of throwing', function () {
  assert.equal(pointInRings(1, 1, null), false);
  assert.equal(pointInRings(1, 1, []), false);
  assert.equal(pointInRings(1, 1, [null]), false);
});

/* -------------------------------------------------------------------------
   Labels + numbers
   ------------------------------------------------------------------------- */

test('map: polygonLabel prefers a name-like DBF attribute, then falls back to the layer name', function () {
  assert.equal(polygonLabel({ properties: { NAME: 'Block 18', area: 42 } }, 'blocks'), 'Block 18');
  assert.equal(polygonLabel({ properties: { BLOCK_NAME: 'S-2', id: 7 } }, 'blocks'), 'S-2');
  assert.equal(polygonLabel({ properties: { label: 'Kharir' } }, 'blocks'), 'Kharir');
  assert.equal(polygonLabel({ properties: { id: 7, area: 42 } }, 'blocks'), 'blocks', 'no name-like key');
  assert.equal(polygonLabel({ properties: { name: '   ' } }, 'blocks'), 'blocks', 'blank name is not a label');
  assert.equal(polygonLabel(null, 'blocks'), 'blocks');
});

test('map: formatOgip shows two decimals, and an em dash for a missing figure', function () {
  assert.equal(formatOgip(12.3456), '12.35');
  assert.equal(formatOgip(0), '0.00');
  assert.equal(formatOgip(null), '—');
  assert.equal(formatOgip(undefined), '—');
  assert.equal(formatOgip(''), '—');
  assert.equal(formatOgip('not a number'), '—');
});

test('map: ogipValue treats a missing figure as 0 for summing', function () {
  assert.equal(ogipValue(null), 0);
  assert.equal(ogipValue(''), 0);
  assert.equal(ogipValue('7.5'), 7.5);
});

test('map: hasCoords rejects wells with no recorded coordinate', function () {
  assert.equal(hasCoords({ x: 1, y: 2 }), true);
  assert.equal(hasCoords({ x: 0, y: 0 }), true, 'the origin is still a coordinate');
  assert.equal(hasCoords({ x: null, y: 2 }), false);
  assert.equal(hasCoords({ x: 1 }), false);
  assert.equal(hasCoords(null), false);
});

test('map: unionBbox grows a box and tolerates nulls on either side', function () {
  assert.deepEqual(unionBbox(null, [0, 0, 1, 1]), [0, 0, 1, 1]);
  assert.deepEqual(unionBbox([0, 0, 1, 1], null), [0, 0, 1, 1]);
  assert.deepEqual(unionBbox([0, 0, 1, 1], [-2, 3, 4, 4]), [-2, 0, 4, 4]);
  assert.equal(unionBbox(null, null), null);
});

test('map: inverse UTM37N returns known WGS84 points', function () {
  var origin = utm37ToWgs84(500000, 0);
  assertClose(origin.lat, 0, 1e-9);
  assertClose(origin.lon, 39, 1e-9, 'zone 37 central meridian');

  // Standard WGS84 UTM fixtures: 166021.443 m is 3 degrees west of a zone's
  // central meridian at the equator; 4,649,776.225 m is 42 degrees north on it.
  var westEdge = utm37ToWgs84(166021.4431, 0);
  assertClose(westEdge.lat, 0, 1e-8);
  assertClose(westEdge.lon, 36, 1e-7);
  var north42 = utm37ToWgs84(500000, 4649776.22482);
  assertClose(north42.lat, 42, 1e-7);
  assertClose(north42.lon, 39, 1e-9);
});

test('map: inverse UTM hemisphere handling and coordinate precision are explicit', function () {
  var south42 = utm37ToWgs84(500000, 5350223.77518, false);
  assertClose(south42.lat, -42, 1e-7);
  assertClose(south42.lon, 39, 1e-9);
  assert.equal(formatLonLat(-45.123456, -12.345678, 3), '45.123°W  12.346°S');
  assert.equal(formatLonLat(39, 0, 5), '39.00000°E  0.00000°N');
  assert.equal(formatUtm37Coordinate(500000.4, 0, 4),
    'E 500000   N 0   (UTM37N m)   WGS84 39.0000°E  0.0000°N');
  assert.match(formatUtm37Coordinate(null, 10), /E —.*WGS84 —/);
});

/* -------------------------------------------------------------------------
   Well filters + area rules
   ------------------------------------------------------------------------- */

function filterRows() {
  return [
    { project_name: 'A', gas_field: 'North', year: 2025, record_status: 'Active', total_cos: 50, mean_gas_bcf: 10 },
    { project_name: 'B', gas_field: 'North', year: 2026, record_status: 'Draft', total_cos: 49.999, mean_gas_bcf: 20 },
    { project_name: 'C', gas_field: 'South', year: 2025, record_status: 'Active', total_cos: 75, mean_ogip: 5 },
    { project_name: 'D', field: 'Legacy', year: 2025, overall_status: 'Legacy status', total_cos: 10, mean_gas_bcf: 2 }
  ];
}

test('map: quadrants use inclusive 50 percent CoS and 10 BCF OGIP cutoffs', function () {
  var rows = filterRows();
  assert.equal(quadrantOfWell(rows[0]), 'Super Stars', 'both exact cutoffs are on the high side');
  assert.equal(quadrantOfWell(rows[1]), 'Risk Takers', '49.999 percent is below the CoS cutoff');
  assert.equal(quadrantOfWell(rows[2]), 'Value Hunter', 'mean_ogip spelling is accepted');
  assert.equal(quadrantOfWell(rows[3]), 'Dogs');
  assert.equal(quadrantOfWell({ total_cos: null, mean_gas_bcf: 20 }), '', 'missing measures have no quadrant');
});

test('map: all four checklist dimensions compose and empty selections do not constrain', function () {
  var rows = filterRows();
  assert.deepEqual(filterWells(rows, {}).map(function (row) { return row.project_name; }), ['A', 'B', 'C', 'D']);
  var selected = { field: ['North'], year: ['2025'], status: ['Active'], quadrant: ['Super Stars'] };
  assert.deepEqual(filterWells(rows, selected).map(function (row) { return row.project_name; }), ['A']);
  assert.deepEqual(filterWells(rows, { field: ['Legacy'], status: ['Legacy status'] })
    .map(function (row) { return row.project_name; }), ['D'], 'legacy field/status keys remain filterable');
});

test('map: filter options are distinct, sorted, and quadrants stay fixed', function () {
  var rows = filterRows();
  assert.deepEqual(mapFilterOptions(rows, 'field'), ['Legacy', 'North', 'South']);
  assert.deepEqual(mapFilterOptions(rows, 'year'), ['2025', '2026']);
  assert.deepEqual(mapFilterOptions(rows, 'status'), ['Active', 'Draft', 'Legacy status']);
  assert.deepEqual(mapFilterOptions([], 'quadrant'), ['Super Stars', 'Value Hunter', 'Risk Takers', 'Dogs']);
});

test('map: a wells reload prunes selected options that no longer exist', function () {
  var store = new LayerStore();
  store.setWells([{ project_name: 'A', gas_field: 'North', x: 1, y: 1 }]);
  store.setWellFilters({ field: ['North'] });
  assert.equal(store.wells.length, 1);
  store.setWells([{ project_name: 'B', gas_field: 'South', x: 2, y: 2 }]);
  assert.deepEqual(store.wellFilters.field, [], 'the invisible North selection is removed');
  assert.deepEqual(store.wells.map(function (well) { return well.project_name; }), ['B'],
    'the replacement rowset is not silently hidden');
});

test('map: area midpoint uses two bounds, falls back to one, and treats missing as zero', function () {
  assert.equal(areaMidpoint({ p90_area_km2: 4, p10_area_km2: 8 }), 6);
  assert.equal(areaMidpoint({ p90_area_km2: '5', p10_area_km2: null }), 5);
  assert.equal(areaMidpoint({ p90_area_km2: '', p10_area_km2: 7 }), 7);
  assert.equal(areaMidpoint({}), 0);
});

/* -------------------------------------------------------------------------
   Well <-> polygon association
   ------------------------------------------------------------------------- */

function associationLayers(fieldsVisible) {
  return [
    polygonLayer('borders', [{ rings: [[[-100, -100], [100, -100], [100, 100], [-100, 100], [-100, -100]]], props: { name: 'Yemen' } }], { isBorders: true }),
    polygonLayer('blocks', [{ rings: SQUARE_0_10, props: { NAME: 'Block A' } }]),
    polygonLayer('fields', [{ rings: SQUARE_5_15, props: { name: 'Field B' } }], { visible: fieldsVisible })
  ];
}

function associationWells() {
  return [
    { project_name: 'W1', x: 2, y: 2, mean_gas_bcf: 10 },      // blocks only
    { project_name: 'W2', x: 7, y: 7, mean_gas_bcf: 5 },       // blocks + fields
    { project_name: 'W3', x: 12, y: 12, mean_gas_bcf: null },  // fields only
    { project_name: 'W4', x: null, y: null, mean_gas_bcf: 3 }  // no coordinates
  ];
}

test('map: association matches wells against every visible polygon layer', function () {
  var result = computeAssociations(associationWells(), associationLayers(true));
  assert.deepEqual(result.polysFor[0].map(function (h) { return h.layerName; }), ['blocks']);
  assert.deepEqual(result.polysFor[1].map(function (h) { return h.layerName; }), ['blocks', 'fields'],
    'a well can sit in two layers at once');
  assert.deepEqual(result.polysFor[2].map(function (h) { return h.layerName; }), ['fields']);
  assert.deepEqual(result.polysFor[3], [], 'a well with no coordinate matches nothing');
});

test('map: association labels each hit with the polygon name, not the layer name', function () {
  var result = computeAssociations(associationWells(), associationLayers(true));
  assert.equal(result.polysFor[0][0].label, 'Block A');
  assert.equal(result.polysFor[2][0].label, 'Field B');
});

test('map: association EXCLUDES an unticked layer', function () {
  var result = computeAssociations(associationWells(), associationLayers(false));
  assert.deepEqual(result.polysFor[1].map(function (h) { return h.layerName; }), ['blocks'],
    'the hidden fields layer no longer contributes');
  assert.deepEqual(result.polysFor[2], [], 'W3 was only inside the hidden layer');
});

test('map: association excludes the country-borders background layer', function () {
  var result = computeAssociations(associationWells(), associationLayers(true));
  var hitLayers = result.polysFor[0].concat(result.polysFor[1], result.polysFor[2])
    .map(function (hit) { return hit.layerName; });
  assert.equal(hitLayers.indexOf('borders'), -1, 'borders would otherwise contain every well');
});

test('map: association exposes the reverse relation, feature -> wells', function () {
  var result = computeAssociations(associationWells(), associationLayers(true));
  assert.deepEqual(result.wellsFor[featureKey('blocks', 0)], [0, 1]);
  assert.deepEqual(result.wellsFor[featureKey('fields', 0)], [1, 2]);
  assert.equal(result.wellsFor[featureKey('borders', 0)], undefined);
});

test('map: association respects holes — a well in a hole is not inside the polygon', function () {
  var layers = [polygonLayer('blocks', [{ rings: SQUARE_WITH_HOLE, props: { name: 'Ring' } }])];
  var result = computeAssociations([{ project_name: 'H', x: 5, y: 5 }, { project_name: 'E', x: 1, y: 1 }], layers);
  assert.deepEqual(result.polysFor[0], [], 'inside the hole');
  assert.equal(result.polysFor[1].length, 1, 'inside the ring proper');
});

/* -------------------------------------------------------------------------
   Summary numbers
   ------------------------------------------------------------------------- */

test('map: summary counts visible layers, plotted wells and wells inside polygons', function () {
  var layers = associationLayers(true);
  var wells = associationWells();
  var summary = computeSummary(layers, wells, computeAssociations(wells, layers));
  assert.equal(summary.visibleLayers, 3);
  assert.equal(summary.wellsPlotted, 3, 'W4 has no coordinates');
  assert.equal(summary.wellsTotal, 4);
  assert.equal(summary.wellsInside, 3, 'W1, W2 and W3 all land in a visible polygon');
});

test('map: summary total OGIP sums plotted wells, treating a null figure as 0', function () {
  var layers = associationLayers(true);
  var wells = associationWells();
  var summary = computeSummary(layers, wells, computeAssociations(wells, layers));
  assert.equal(summary.totalOgip, 15, '10 + 5 + null(0); the uncoordinated W4 is not plotted at all');
  assert.equal(formatOgipTotal(summary.totalOgip), '15.0');
});

test('map: summary area sums midpoint estimates over every filtered well', function () {
  var wells = [
    { x: 1, y: 1, p90_area_km2: 4, p10_area_km2: 8 },
    { x: 1.5, y: 1.5, p90_area_km2: 5, p10_area_km2: null },
    { x: 2, y: 2, p90_area_km2: null, p10_area_km2: 7 }
  ];
  var summary = computeSummary([], wells, computeAssociations(wells, []));
  assert.equal(summary.wellsPlotted, 3);
  assert.equal(summary.totalArea, 18, '6 midpoint + 5 P90 fallback + 7 P10 fallback');
  assert.equal(formatAreaTotal(summary.totalArea), '18.0');
});

test('map: summary drops wells-inside when the containing layer is hidden', function () {
  var layers = associationLayers(false);
  var wells = associationWells();
  var summary = computeSummary(layers, wells, computeAssociations(wells, layers));
  assert.equal(summary.visibleLayers, 2);
  assert.equal(summary.wellsInside, 2, 'W3 was only inside the now-hidden layer');
});

test('map: summary of an empty world is all zeros', function () {
  var summary = computeSummary([], [], computeAssociations([], []));
  assert.deepEqual(
    [summary.visibleLayers, summary.wellsPlotted, summary.wellsInside, summary.totalOgip],
    [0, 0, 0, 0]);
});

test('map: summary rounds the total to one decimal only at display time', function () {
  var wells = [{ x: 1, y: 1, mean_gas_bcf: 0.05 }, { x: 2, y: 2, mean_gas_bcf: 0.06 }];
  var summary = computeSummary([], wells, computeAssociations(wells, []));
  assert.equal(formatOgipTotal(summary.totalOgip), '0.1', 'summed at full precision, rounded once');
});

/* -------------------------------------------------------------------------
   Layer ordering + pinning
   ------------------------------------------------------------------------- */

test('map: draw order pins borders at the bottom and lists shapefiles above it', function () {
  var store = new LayerStore();
  store.setLayers(layerMeta());
  assert.deepEqual(names(store.drawOrder()), ['borders', 'blocks', 'fields', 'seismic']);
  assert.deepEqual(names(store.sidebarOrder()), ['seismic', 'fields', 'blocks', 'borders'],
    'the sidebar lists topmost-first');
});

test('map: moveLayer walks one step through the draw order', function () {
  var store = new LayerStore();
  store.setLayers(layerMeta());
  assert.equal(store.moveLayer('blocks', 1), true);
  assert.deepEqual(names(store.drawOrder()), ['borders', 'fields', 'blocks', 'seismic']);
  assert.equal(store.moveLayer('blocks', -1), true);
  assert.deepEqual(names(store.drawOrder()), ['borders', 'blocks', 'fields', 'seismic']);
});

test('map: borders cannot be reordered and nothing can be pushed below it', function () {
  var store = new LayerStore();
  store.setLayers(layerMeta());
  assert.equal(store.moveLayer('borders', 1), false, 'borders is not in the reorderable list');
  assert.equal(store.moveLayer('blocks', -1), false, 'already the bottom-most shapefile');
  assert.deepEqual(names(store.drawOrder()), ['borders', 'blocks', 'fields', 'seismic']);
});

test('map: nothing can be pushed above the pinned wells overlay', function () {
  var store = new LayerStore();
  store.setLayers(layerMeta());
  assert.equal(store.moveLayer('seismic', 1), false, 'the top of the shapefile band is the ceiling');
  // The wells overlay is not a member of the ordered list at all, which is
  // what makes "always on top" structural rather than a comparison.
  assert.equal(store.order.indexOf(WELLS_ID), -1);
  assert.equal(names(store.drawOrder()).indexOf(WELLS_ID), -1);
});

test('map: a reload preserves visibility, color and loaded geometry per layer', function () {
  var store = new LayerStore();
  store.setLayers(layerMeta());
  store.setVisible('fields', false);
  store.setColor('blocks', '#ABCDEF');
  store.layers.get('blocks').features = [{ geometry: null, properties: {} }];
  store.setLayers(layerMeta());
  assert.equal(store.layers.get('fields').visible, false);
  assert.equal(store.layers.get('blocks').color, '#abcdef');
  assert.equal(store.layers.get('blocks').features.length, 1, 'geometry survives a metadata reload');
});

test('map: setColor rejects anything that is not a six-digit hex', function () {
  var store = new LayerStore();
  store.setLayers(layerMeta());
  assert.equal(store.setColor('blocks', 'red'), false);
  assert.equal(store.setColor('blocks', '#abc'), false);
  assert.equal(store.setColor('nope', '#abcdef'), false, 'unknown layer');
  assert.equal(store.setColor(WELLS_ID, '#123456'), true);
  assert.equal(store.wellsColor, '#123456');
});

test('map: shapefile layers take distinct palette colors and borders its own', function () {
  var store = new LayerStore();
  store.setLayers(layerMeta());
  assert.equal(store.layers.get('blocks').color, PALETTE[0]);
  assert.equal(store.layers.get('fields').color, PALETTE[1]);
  assert.ok(store.layers.get('borders').color !== PALETTE[0], 'borders is not palette-assigned');
});

/* -------------------------------------------------------------------------
   Persistence
   ------------------------------------------------------------------------- */

test('map: state round-trips order, visibility, colors and the summary collapse', function () {
  var storage = memStorage();
  var store = new LayerStore();
  store.setLayers(layerMeta());
  store.moveLayer('blocks', 1);
  store.setVisible('seismic', false);
  store.setVisible(WELLS_ID, false);
  store.setColor('fields', '#123456');
  store.setColor(WELLS_ID, '#654321');
  store.summaryCollapsed = true;
  store.sidebarCollapsed = true;
  store.filtersCollapsed = true;
  store.layersCollapsed = true;
  var emitted = store.toState();
  assert.equal(typeof emitted.sidebarCollapsed, 'boolean');
  assert.equal(typeof emitted.filtersCollapsed, 'boolean');
  assert.equal(typeof emitted.layersCollapsed, 'boolean');
  assert.equal(writeMapState(emitted, storage), true);
  assert.ok(storage._raw[MAP_STATE_KEY], 'written under the namespaced key');

  var restored = new LayerStore();
  restored.applyState(readMapState(storage));
  restored.setLayers(layerMeta());
  assert.deepEqual(names(restored.drawOrder()), ['borders', 'fields', 'blocks', 'seismic']);
  assert.equal(restored.layers.get('seismic').visible, false);
  assert.equal(restored.layers.get('fields').color, '#123456');
  assert.equal(restored.wellsVisible, false);
  assert.equal(restored.wellsColor, '#654321');
  assert.equal(restored.summaryCollapsed, true);
  assert.equal(restored.sidebarCollapsed, true);
  assert.equal(restored.filtersCollapsed, true);
  assert.equal(restored.layersCollapsed, true);
});

/* sidebarCollapsed is the one tri-state flag: null means "the user has never
   said", which is the view's licence to pick the default for the viewport. */
test('map: an unstored sidebar collapse reads as null, and the folds default open', function () {
  assert.equal(normalizeState(null).sidebarCollapsed, null, 'never stored');
  assert.equal(normalizeState({ sidebarCollapsed: 1 }).sidebarCollapsed, null, 'only a real boolean counts');
  assert.equal(normalizeState({ sidebarCollapsed: false }).sidebarCollapsed, false);
  assert.equal(normalizeState(null).filtersCollapsed, false);
  assert.equal(normalizeState(null).layersCollapsed, false);
  var store = new LayerStore();
  assert.equal(store.sidebarCollapsed, null, 'the store default is "never chosen" too');
  store.applyState(normalizeState(null));
  assert.equal(store.sidebarCollapsed, null, 'applyState carries the null through');
});

/* The null has to survive every persist that is NOT the sidebar toggle. It is
   written by seven unrelated paths (a layer tick, a reorder, a recolor, the
   summary toggle...), and if any of them froze the viewport-derived default as
   a stored boolean, a phone would never open full-screen again. */
test('map: an unrelated persist leaves the unchosen sidebar collapse unchosen', function () {
  var storage = memStorage();
  var store = new LayerStore();
  store.applyState(readMapState(storage));       // nothing stored yet
  store.setLayers(layerMeta());
  assert.equal(store.toState().sidebarCollapsed, null, 'nothing chosen, nothing to write');

  store.setVisible('blocks', false);              // an unrelated change...
  writeMapState(store.toState(), storage);        // ...and the persist it triggers
  var reloaded = new LayerStore().applyState(readMapState(storage));
  assert.equal(reloaded.sidebarCollapsed, null, 'still unchosen after an unrelated persist');
  assert.equal(reloaded.toState().sidebarCollapsed, null, 'and it is written back as null');

  store.sidebarCollapsed = true;                  // the toggle, the one chooser
  writeMapState(store.toState(), storage);
  var chosen = new LayerStore().applyState(readMapState(storage));
  assert.equal(chosen.sidebarCollapsed, true, 'an explicit choice round-trips as a boolean');
});

test('map: a stale saved order drops vanished layers and appends unseen ones on top', function () {
  var storage = memStorage();
  writeMapState({
    version: 1,
    order: ['gone', 'fields', 'blocks'],
    visible: { gone: false, blocks: false },
    colors: { gone: '#000000', blocks: '#0f0f0f' },
    summaryCollapsed: false
  }, storage);

  var store = new LayerStore();
  store.applyState(readMapState(storage));
  store.setLayers(layerMeta());   // 'gone' no longer exists; 'seismic' is new
  assert.deepEqual(names(store.drawOrder()), ['borders', 'fields', 'blocks', 'seismic'],
    'stale name dropped, unseen layer appended at the top');
  assert.equal(store.layers.get('blocks').visible, false, 'a surviving preference still applies');
  assert.equal(store.layers.get('seismic').visible, true, 'an unknown layer defaults to visible');
  assert.equal(store.toState().order.indexOf('gone'), -1, 'the stale name is not written back');
});

test('map: unreadable or hostile stored state degrades to defaults instead of throwing', function () {
  var storage = memStorage();
  storage.setItem(MAP_STATE_KEY, '{not json');
  assert.equal(readMapState(storage), null);
  var normalized = normalizeState({ order: ['ok', 7, null], visible: { a: 'yes' }, colors: { a: 'javascript:alert(1)', b: '#AABBCC' } });
  assert.deepEqual(normalized.order, ['ok'], 'non-string order entries are discarded');
  assert.equal(normalized.visible.a, true);
  assert.equal(normalized.colors.a, undefined, 'a non-hex color is not persisted');
  assert.equal(normalized.colors.b, '#aabbcc');
  assert.equal(normalizeState(null).version, 1);
});

/* -------------------------------------------------------------------------
   Store: wells, bboxes, lazy geometry, association cache
   ------------------------------------------------------------------------- */

test('map: the store loads a layer lazily and only once', function () {
  var calls = [];
  var store = new LayerStore(function (name) {
    calls.push(name);
    return Promise.resolve({ features: [{ geometry: { type: 'polygon', coordinates: SQUARE_0_10 }, properties: {} }], bbox: [0, 0, 10, 10] });
  });
  store.setLayers(layerMeta());
  var layer = store.layers.get('blocks');
  return Promise.all([store.ensureLoaded(layer), store.ensureLoaded(layer)]).then(function () {
    return store.ensureLoaded(layer);
  }).then(function () {
    assert.deepEqual(calls, ['blocks'], 'concurrent and repeat callers share one fetch');
    assert.equal(layer.features.length, 1);
  });
});

test('map: a failed geometry fetch leaves an empty layer rather than a broken one', function () {
  var store = new LayerStore(function () { return Promise.reject(new Error('boom')); });
  store.setLayers(layerMeta());
  var layer = store.layers.get('blocks');
  return store.ensureLoaded(layer).then(function () {
    assert.deepEqual(layer.features, []);
    assert.equal(layer.loading, false);
    assert.match(layer.error, /boom/);
  });
});

test('map: visibleBbox unions visible layers and the plotted wells', function () {
  var store = new LayerStore();
  store.setLayers(layerMeta());
  store.setVisible('borders', false);
  store.setVisible('fields', false);
  store.setWells([{ x: -5, y: -5, mean_gas_bcf: 1 }, { x: null, y: null }]);
  assert.deepEqual(store.visibleBbox(), [-5, -5, 10, 10]);
  assert.deepEqual(store.wellsBbox(), [-5, -5, -5, -5]);
  assert.equal(store.plottedWells().length, 1);
});

test('map: the association cache is invalidated by a visibility change', function () {
  var store = new LayerStore();
  store.setLayers([{ name: 'blocks', geom_type: 'polygon', feature_count: 1, bbox: [0, 0, 10, 10] }]);
  store.layers.get('blocks').features = [{ geometry: { type: 'polygon', coordinates: SQUARE_0_10 }, properties: { name: 'A' } }];
  store.setWells([{ project_name: 'W', x: 5, y: 5, mean_gas_bcf: 2 }]);
  assert.equal(store.associations().polysFor[0].length, 1);
  var first = store.associations();
  assert.ok(first === store.associations(), 'cached between reads');
  store.setVisible('blocks', false);
  assert.equal(store.associations().polysFor[0].length, 0, 'recomputed after the change');
  assert.equal(store.summary().wellsInside, 0);
});

/* -------------------------------------------------------------------------
   Measurement
   ------------------------------------------------------------------------- */

test('map: formatDistance shows metres below a kilometre and km with two decimals above', function () {
  assert.equal(formatDistance(0), '0 m');
  assert.equal(formatDistance(1), '1 m');
  assert.equal(formatDistance(750.4), '750 m');
  assert.equal(formatDistance(999), '999 m');
  assert.equal(formatDistance(1000), '1.00 km');
  assert.equal(formatDistance(1234.5), '1.23 km');
  assert.equal(formatDistance(25000), '25.00 km');
  assert.equal(formatDistance(NaN), '—');
});

test('map: segment and cumulative lengths are exact Euclidean UTM metres', function () {
  var chain = [[0, 0], [3, 4], [3, 8]];
  assert.deepEqual(segmentLengths(chain), [5, 4]);
  assert.deepEqual(cumulativeLengths(chain), [0, 5, 9]);
  assert.equal(totalDistance(chain), 9);
  assert.deepEqual(segmentLengths([[1, 1]]), [], 'one vertex has no segment');
  assert.equal(totalDistance([]), 0);
});

test('map: the measure tool drafts, rubber-bands, and finishes leaving the chain drawn', function () {
  var tool = new MeasureTool();
  assert.equal(tool.isEmpty, true);
  tool.addPoint(0, 0);
  tool.addPoint(300, 400);
  assert.equal(tool.drafting, true);
  tool.moveCursor(300, 800);
  assert.deepEqual(tool.livePoints(), [[0, 0], [300, 400], [300, 800]], 'the cursor is a rubber-band vertex');
  assert.equal(tool.total(), 900, '500 + 400 while drafting');
  tool.finish();
  assert.equal(tool.isFinished, true);
  assert.deepEqual(tool.livePoints(), [[0, 0], [300, 400]], 'the rubber-band vertex is not committed');
  assert.equal(formatDistance(tool.total()), '500 m');
});

test('map: finishing with a single vertex measures nothing, and Clear erases a finished chain', function () {
  var tool = new MeasureTool();
  tool.addPoint(5, 5);
  tool.finish();
  assert.equal(tool.isEmpty, true, 'one point is not a measurement');
  tool.addPoint(0, 0);
  tool.addPoint(0, 2000);
  tool.finish();
  assert.equal(formatDistance(tool.total()), '2.00 km');
  tool.clear();
  assert.equal(tool.isEmpty, true);
  assert.deepEqual(tool.livePoints(), []);
});

test('map: a click after a finished measurement starts a new chain rather than extending it', function () {
  var tool = new MeasureTool();
  tool.addPoint(0, 0);
  tool.addPoint(0, 100);
  tool.finish();
  tool.addPoint(500, 500);
  assert.deepEqual(tool.points, [[500, 500]]);
  assert.equal(tool.drafting, true);
});

/* -------------------------------------------------------------------------
   Canvas helpers
   ------------------------------------------------------------------------- */

test('map: withAlpha converts a layer hex to rgba and passes anything else through', function () {
  assert.equal(withAlpha('#4a9eff', 0.18), 'rgba(74,158,255,0.18)');
  assert.equal(withAlpha('rgb(1,2,3)', 0.5), 'rgb(1,2,3)', 'non-hex is returned untouched');
  assert.ok(WELL_LABEL_MIN_SCALE > 0, 'labels have a zoom threshold');
});

test('map: a wheel delta is normalized to PIXELS before it reaches the zoom exponent', function () {
  // Chrome/Edge: pixels already.
  assert.equal(normalizeWheelDelta(120, 0, 700), 120);
  assert.equal(normalizeWheelDelta(-53, 0, 700), -53);
  // Firefox on Windows: LINES. Three lines a notch, which un-normalized zooms
  // ~30x slower than the same flick on Edge.
  assert.equal(normalizeWheelDelta(3, 1, 700), 3 * WHEEL_LINE_PX);
  assert.equal(normalizeWheelDelta(-3, 1, 700), -3 * WHEEL_LINE_PX);
  // A page-scrolling device: one page is the viewport.
  assert.equal(normalizeWheelDelta(1, 2, 700), 700);
  assert.equal(normalizeWheelDelta(-2, 2, 700), -1400);
  assert.equal(normalizeWheelDelta(undefined, 0, 700), 0, 'a non-numeric delta zooms nothing');
});

test('map: the view scale is clamped, so wheel inertia cannot collapse or explode it', function () {
  assert.equal(clampScale(1), 1, 'an ordinary scale passes through');
  assert.equal(clampScale(1e12), MAX_SCALE);
  assert.equal(clampScale(1e-30), MIN_SCALE);
  assert.equal(clampScale(0), MIN_SCALE);
  assert.equal(clampScale(-4), MIN_SCALE, 'a negative scale would mirror the world');
  assert.equal(clampScale(NaN), MIN_SCALE);

  var view = pickCanvas(emptyStore());
  view.zoomAt(100, 100, 1e9);
  assert.equal(view.scale, MAX_SCALE, 'zoomAt clamps at the top');
  view.zoomAt(100, 100, 1e-12);
  assert.equal(view.scale, MIN_SCALE, 'and at the bottom');
  assert.ok(isFinite(view.offsetX) && isFinite(view.offsetY), 'the offsets stay finite at the limit');
});

/* ---- hit testing --------------------------------------------------------
   The tolerance is 7 SCREEN pixels converted to metres, so at a fit-the-
   country zoom it is kilometres wide: which candidate inside it wins is the
   whole answer, not a tie-break. */

function emptyStore() {
  return {
    wellsVisible: false, wells: [], wellsColor: '#e05252',
    plottedWells: function () { return []; },
    drawOrder: function () { return []; }
  };
}

// A canvas with its viewport stated directly: _pick needs a size and a scale,
// not a laid-out element, and 1 px/m keeps the arithmetic readable.
function pickCanvas(store) {
  var host = fixture('<canvas></canvas>');
  var view = new MapCanvas(host.querySelector('canvas'), store);
  view.width = 200;
  view.height = 200;
  view.scale = 1;
  view.offsetX = 0;
  view.offsetY = 0;
  return view;
}

test('map: the hit test picks the NEAREST well inside the tolerance, not the first one found', function () {
  var wells = [{ project_name: 'FAR', x: 100, y: 100 }, { project_name: 'NEAR', x: 104, y: 100 }];
  var store = emptyStore();
  store.wellsVisible = true;
  store.wells = wells;
  store.plottedWells = function () { return wells; };

  // World (103, 100): 3 m from FAR, 1 m from NEAR, both well inside the 7 m
  // tolerance. Array order alone would answer FAR for every pixel of the pair.
  var hit = pickCanvas(store)._pick(103, 100);
  assert.equal(hit.kind, 'well');
  assert.equal(hit.index, 1);
  assert.equal(hit.well.project_name, 'NEAR');
});

test('map: the hit test picks the TOPMOST of two overlapping polygons in one layer', function () {
  var layer = {
    name: 'blocks', visible: true, color: '#4a9eff',
    features: [
      { geometry: { type: 'polygon', coordinates: SQUARE_0_10 }, properties: { name: 'under' } },
      { geometry: { type: 'polygon', coordinates: SQUARE_5_15 }, properties: { name: 'over' } }
    ]
  };
  var store = emptyStore();
  store.drawOrder = function () { return [layer]; };

  // World (7, 7) is inside both; the second feature is painted last, so it is
  // the one the user can actually see there.
  var hit = pickCanvas(store)._pick(7, 193);
  assert.equal(hit.kind, 'feature');
  assert.equal(hit.featureIndex, 1);
  assert.equal(hit.feature.properties.name, 'over');

  // Outside the overlap the lower feature still answers for itself.
  assert.equal(pickCanvas(store)._pick(2, 198).feature.properties.name, 'under');
});

/* -------------------------------------------------------------------------
   Tooltip identity + the layer error hint (pure)
   ------------------------------------------------------------------------- */

test('map: hitIdentity names WHAT is hovered, so an unchanged tooltip need not be rebuilt', function () {
  assert.equal(hitIdentity(null), '', 'nothing hovered');
  assert.equal(hitIdentity({ kind: 'well', index: 3 }), 'well#3');
  assert.equal(hitIdentity({ kind: 'feature', layer: { name: 'blocks' }, featureIndex: 2 }), 'feature#blocks#2');
  // Same identity for two hit objects describing the same thing...
  assert.equal(hitIdentity({ kind: 'feature', layer: { name: 'blocks' }, featureIndex: 2 }),
    hitIdentity({ kind: 'feature', layer: { name: 'blocks' }, featureIndex: 2 }));
  // ...and different for everything a rebuild would have to notice.
  assert.ok(hitIdentity({ kind: 'feature', layer: { name: 'fields' }, featureIndex: 2 })
    !== hitIdentity({ kind: 'feature', layer: { name: 'blocks' }, featureIndex: 2 }), 'layer matters');
  assert.ok(hitIdentity({ kind: 'well', index: 2 })
    !== hitIdentity({ kind: 'feature', layer: { name: 'w' }, featureIndex: 2 }), 'kind matters');
});

test('map: errorHint reduces a backend error body to one short line', function () {
  assert.match(errorHint('Layer blocks cannot be read (missing or corrupt .shx/.dbf?)'),
    /Could not load this layer: Layer blocks cannot be read/);
  assert.match(errorHint('<!doctype html>\n<html>\n<body>500</body>'), /Could not load this layer: <!doctype html>$/,
    'an HTML error page is cut to its first line');
  assert.match(errorHint(''), /unknown error/);
  assert.match(errorHint(null), /unknown error/);
  assert.ok(errorHint(new Array(400).join('x')).length < 200, 'and it is bounded');
});

/* -------------------------------------------------------------------------
   Tooltips (pure builders) — including escaping
   ------------------------------------------------------------------------- */

test('map: the well tooltip states the stage, status, mean OGIP and the containing polygon', function () {
  var html = wellTooltipHtml(
    { project_name: 'HASWAH-3', display_stage: 'Staking', overall_status: 'In Progress', mean_gas_bcf: 12.345 },
    [{ layerName: 'blocks', label: 'Block A' }]);
  assert.match(html, /HASWAH-3/);
  assert.match(html, /Staking/);
  assert.match(html, /In Progress/);
  assert.match(html, /Mean OGIP: 12\.35 BCF/);
  assert.match(html, /Block A/);
});

test('map: a well with no OGIP reads as an em dash, with no stray unit', function () {
  var html = wellTooltipHtml({ project_name: 'W', mean_gas_bcf: null }, []);
  assert.match(html, /Mean OGIP: —/);
  assert.equal(/BCF/.test(html), false, 'no "— BCF"');
  assert.match(html, /Inside<\/span> —/, 'no containing polygon reads as an em dash too');
});

test('map: the polygon tooltip lists its attributes, its wells and their summed OGIP', function () {
  var layer = { name: 'blocks', isBorders: false };
  var feature = { properties: { NAME: 'Block A', AREA_KM2: 120 } };
  var html = polygonTooltipHtml(layer, feature, [
    { project_name: 'W1', mean_gas_bcf: 10 },
    { project_name: 'W2', mean_gas_bcf: null },
    { project_name: 'W3', mean_gas_bcf: 2.5 }
  ]);
  assert.match(html, /Block A/);
  assert.match(html, /AREA_KM2/);
  assert.match(html, /Wells inside \(3\)/);
  assert.match(html, /W2<\/td><td class="n">—/, 'a missing figure still shows as an em dash on its own row');
  assert.match(html, /Total Mean OGIP: 12\.5 BCF/, 'the null contributes 0 to the sum');
});

test('map: a polygon with no wells inside says so instead of showing an empty table', function () {
  var html = polygonTooltipHtml({ name: 'blocks' }, { properties: { name: 'Empty' } }, []);
  assert.match(html, /Wells inside \(0\)/);
  assert.match(html, /No wells inside this polygon/);
});

test('map: the borders tooltip has no associated-wells section', function () {
  var html = polygonTooltipHtml({ name: 'borders', isBorders: true }, { properties: { name: 'Yemen' } }, []);
  assert.match(html, /Border/);
  assert.equal(/Wells inside/.test(html), false);
});

test('map: hostile shapefile attributes are escaped, never injected as markup', function () {
  var hostile = '<img src=x onerror="alert(1)">';
  var html = polygonTooltipHtml(
    { name: '<b>layer</b>' },
    { properties: { '<script>k</script>': hostile, name: '"><svg onload=alert(2)>' } },
    [{ project_name: hostile, mean_gas_bcf: 1 }]);
  assert.equal(/<img/.test(html), false, 'no live img tag');
  assert.equal(/<script/.test(html), false, 'no live script tag');
  assert.equal(/<svg onload/.test(html), false);
  assert.match(html, /&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;/);
  // And it really is inert once parsed by the browser.
  var host = document.createElement('div');
  host.innerHTML = html;
  assert.equal(host.querySelectorAll('img, script, svg').length, 0, 'nothing hostile survives parsing');
  assert.match(host.textContent, /alert\(1\)/, 'the text is shown verbatim');
});

test('map: a hostile project name is escaped in the well tooltip too', function () {
  var html = wellTooltipHtml({ project_name: '<img src=x onerror=alert(1)>', overall_status: '</td><b>x', mean_gas_bcf: 1 }, []);
  assert.equal(/<img/.test(html), false);
  var host = document.createElement('div');
  host.innerHTML = html;
  assert.equal(host.querySelectorAll('img, b').length, 0);
});

test('map: wellLabel falls back to the project id, then to a generic label', function () {
  assert.equal(wellLabel({ project_name: 'W1', project_id: 7 }), 'W1');
  assert.equal(wellLabel({ project_id: 7 }), '7');
  assert.equal(wellLabel({}), 'Well');
  assert.equal(wellLabel(null), 'Well');
});

/* -------------------------------------------------------------------------
   Summary panel markup
   ------------------------------------------------------------------------- */

test('map: the summary panel renders exactly the three requested live figures', function () {
  var html = summaryHtml({ visibleLayers: 3, wellsPlotted: 12, wellsInside: 7, totalOgip: 123.456, totalArea: 44.44, wellsTotal: 12 });
  assert.match(html, /Total Mean OGIP<\/span><span class="map-summary-value">123\.5 BCF/);
  assert.match(html, /Total Area<\/span><span class="map-summary-value">44\.4 km²/);
  assert.match(html, /Wells shown<\/span><span class="map-summary-value">12/);
  var host = document.createElement('div');
  host.innerHTML = html;
  assert.equal(host.querySelectorAll('.map-summary-row').length, 3);
  assert.equal(/Visible layers|Wells plotted|Wells in polygons/.test(host.textContent), false);
  assert.equal(/map-summary-note/.test(html), false, 'no empty-state note when wells are plotted');
});

test('map: with no plotted wells the summary reads zero and says why', function () {
  var html = summaryHtml({ visibleLayers: 2, wellsPlotted: 0, wellsInside: 0, totalOgip: 0, totalArea: 0, wellsTotal: 0 });
  assert.match(html, /Wells shown<\/span><span class="map-summary-value">0/);
  assert.match(html, /Total Mean OGIP<\/span><span class="map-summary-value">0\.0 BCF/);
  assert.match(html, /Total Area<\/span><span class="map-summary-value">0\.0 km²/);
  assert.match(html, /No project coordinates are recorded yet/);
});

/* -------------------------------------------------------------------------
   The wired view — one end-to-end pass over the DOM contract

   Deliberately the LAST test in this module: views/map-view.js keeps a single
   live view per page (that is the point — one canvas, one store), so booting
   it is a one-way door. What this pins is exactly what unit tests cannot: the
   ids in index.html, the fetch wiring, and the fact that every handler
   renderSidebar attaches finds its element.
   ------------------------------------------------------------------------- */

var MAP_MARKUP = [
  '<section id="tab-map" class="tab active">',
  '<div class="panel map-panel">',
  '<div class="map-stage">',
  '<canvas id="map-canvas"></canvas>',
  '<div id="map-summary"><button id="map-summary-toggle" aria-expanded="true"></button>',
  '<div id="map-summary-body"></div></div>',
  '<aside id="map-sidebar" class="map-sidebar">',
  '<button id="map-sidebar-toggle" aria-expanded="true"></button>',
  '<div id="map-sidebar-body">',
  '<button id="map-fit-all" type="button"></button>',
  '<button id="map-reload" type="button"></button>',
  '<section id="map-filters-fold" class="map-fold">',
  '<button id="map-filters-toggle" class="map-fold-head open" aria-expanded="true"></button>',
  '<div id="map-filter-list"></div>',
  '</section>',
  '<section id="map-layers-fold" class="map-fold">',
  '<button id="map-layers-toggle" class="map-fold-head open" aria-expanded="true"></button>',
  '<div id="map-layer-list"></div>',
  '</section>',
  '</div></aside>',
  '<div class="map-toolbox">',
  '<button id="map-tool-pointer" aria-pressed="true"></button>',
  '<button id="map-tool-measure" aria-pressed="false"></button>',
  '<button id="map-measure-clear"></button>',
  '<button id="map-colors-toggle" aria-expanded="false"></button>',
  '<div id="map-measure-readout" hidden></div>',
  '</div>',
  '<div id="map-color-panel" hidden></div>',
  '<button id="map-zoom-in"></button><button id="map-zoom-out"></button>',
  '<div id="map-readout" hidden></div><div id="map-tooltip" hidden></div>',
  '</div></div></section>'
].join('');

function mapFetchStub() {
  var geometry = {
    borders: { name: 'borders', geom_type: 'polygon', bbox: [-100, -100, 100, 100], features: [{ geometry: { type: 'polygon', coordinates: [[[-100, -100], [100, -100], [100, 100], [-100, 100], [-100, -100]]] }, properties: { name: 'Yemen' } }] },
    blocks: { name: 'blocks', geom_type: 'polygon', bbox: [0, 0, 10, 10], features: [{ geometry: { type: 'polygon', coordinates: SQUARE_0_10 }, properties: { NAME: 'Block A' } }] },
    fields: { name: 'fields', geom_type: 'polygon', bbox: [5, 5, 15, 15], features: [{ geometry: { type: 'polygon', coordinates: SQUARE_5_15 }, properties: { name: 'Field B' } }] }
  };
  return function (url) {
    var path = String(url).split('?')[0];
    var body;
    // 'fields' is LISTED but its geometry will not load — the half-copied
    // shapefile set the backend skips on list but 400s on load. The sidebar
    // has to say so rather than showing an empty ticked checkbox.
    if (path === '/api/map/layers/fields') {
      return {
        ok: false, status: 400,
        headers: { get: function () { return 'application/json'; } },
        json: function () { return Promise.resolve({ detail: 'Layer fields cannot be read (missing or corrupt .shx/.dbf?)' }); },
        text: function () { return Promise.resolve('{}'); }
      };
    }
    if (path === '/api/map/layers') {
      body = { layers: [
        { name: 'borders', geom_type: 'polygon', feature_count: 1, bbox: [-100, -100, 100, 100], is_borders: true },
        { name: 'blocks', geom_type: 'polygon', feature_count: 1, bbox: [0, 0, 10, 10] },
        { name: 'fields', geom_type: 'polygon', feature_count: 1, bbox: [5, 5, 15, 15] }
      ] };
    } else if (path.indexOf('/api/map/layers/') === 0) {
      body = geometry[decodeURIComponent(path.slice('/api/map/layers/'.length))] || { features: [] };
    } else if (path === '/api/map/wells') {
      body = { wells: [
        { project_id: 1, project_name: 'W1', display_stage: 'Staking', overall_status: 'In Progress', gas_field: 'North', year: 2025, record_status: 'Active', total_cos: 50, x: 2, y: 2, mean_gas_bcf: 10, p90_area_km2: 5, p10_area_km2: 15 },
        { project_id: 2, project_name: 'W2', display_stage: 'Drilling', overall_status: 'In Progress', gas_field: 'South', year: 2026, record_status: 'Draft', total_cos: 40, x: 50, y: 50, mean_gas_bcf: null, p90_area_km2: 2, p10_area_km2: null },
        { project_id: 3, project_name: 'W3', display_stage: 'Lead', overall_status: 'In Progress', gas_field: 'North', year: 2025, record_status: 'Active', total_cos: 80, x: 12, y: 12, mean_gas_bcf: 4, p90_area_km2: 1, p10_area_km2: 3 }
      ] };
    } else {
      body = {};
    }
    return {
      ok: true, status: 200,
      headers: { get: function () { return 'application/json'; } },
      json: function () { return Promise.resolve(body); },
      text: function () { return Promise.resolve(JSON.stringify(body)); }
    };
  };
}

test('map: refreshMap boots the tab — sidebar order, summary figures, toolbox and color panel', function () {
  try { window.localStorage.removeItem(MAP_STATE_KEY); } catch (err) { /* storage may be unavailable */ }
  var root = fixture(MAP_MARKUP);
  mockFetch(mapFetchStub());
  return refreshMap().then(function () {
    // Sidebar: topmost-first, wells pinned at the head and borders at the foot.
    var rows = Array.prototype.map.call(root.querySelectorAll('.map-layer'), function (row) {
      return row.getAttribute('data-layer');
    });
    assert.deepEqual(rows, [WELLS_ID, 'fields', 'blocks', 'borders']);
    assert.equal(root.querySelectorAll('.map-layer[data-layer="' + WELLS_ID + '"] .map-layer-move').length, 0,
      'the pinned wells overlay has no reorder controls');
    assert.equal(root.querySelectorAll('.map-layer[data-layer="borders"] .map-layer-move').length, 0,
      'the pinned borders layer has no reorder controls');
    assert.equal(root.querySelectorAll('.map-layer[data-layer="blocks"] .map-layer-move').length, 2);

    // A layer that listed but would not load is marked, with the server's own
    // message in the marker's title; the layers that loaded are unmarked.
    var marker = root.querySelector('.map-layer[data-layer="fields"] .map-layer-error');
    assert.ok(marker, 'the unloadable layer carries a warning marker');
    assert.match(marker.getAttribute('title'), /cannot be read/);
    assert.equal(root.querySelectorAll('.map-layer[data-layer="blocks"] .map-layer-error').length, 0);

    // Summary: all map payload rows are positioned; one lands in Block A.
    var summary = document.getElementById('map-summary-body').textContent;
    assert.match(summary, /Wells shown3/);
    assert.match(summary, /Total Mean OGIP14\.0 BCF/, 'W2 has no figure and contributes 0');
    assert.match(summary, /Total Area14\.0 km²/, 'area includes every row in the filtered map payload');

    // Two containers, not one: the filters and the layer rows are rendered
    // into their own folds so each can be collapsed on its own.
    assert.equal(document.getElementById('map-filter-list').querySelectorAll('.map-well-filter').length, 4);
    assert.equal(document.getElementById('map-layer-list').querySelectorAll('.map-layer').length, 4);
    assert.equal(document.getElementById('map-filter-list').querySelectorAll('.map-layer').length, 0);
    assert.equal(document.getElementById('map-layer-list').querySelectorAll('.map-well-filter').length, 0);

    // Four independent checklist groups are populated from the full rowset.
    assert.equal(root.querySelectorAll('.map-well-filter').length, 4);
    assert.equal(root.querySelectorAll('.map-well-filter[data-filter="field"] input').length, 2);
    assert.equal(root.querySelectorAll('.map-well-filter[data-filter="quadrant"] input').length, 4);
    var south = root.querySelector('.map-well-filter[data-filter="field"] input[value="South"]');
    south.checked = true;
    south.dispatchEvent(new Event('change', { bubbles: true }));
    summary = document.getElementById('map-summary-body').textContent;
    assert.match(summary, /Wells shown1/);
    assert.match(summary, /Total Mean OGIP0\.0 BCF/);
    assert.match(summary, /Total Area2\.0 km²/);
    south.checked = false;
    south.dispatchEvent(new Event('change', { bubbles: true }));

    // The up control really moves a layer, re-renders and persists.
    root.querySelector('.map-layer[data-layer="blocks"] .map-layer-move[data-dir="1"]').click();
    var reordered = Array.prototype.map.call(root.querySelectorAll('.map-layer'), function (row) {
      return row.getAttribute('data-layer');
    });
    assert.deepEqual(reordered, [WELLS_ID, 'blocks', 'fields', 'borders']);
    var saved = JSON.parse(window.localStorage.getItem(MAP_STATE_KEY));
    assert.deepEqual(saved.order, ['fields', 'blocks'], 'draw order persisted bottom -> top');

    // The toolbox: aria-pressed tracks the active tool.
    document.getElementById('map-tool-measure').click();
    assert.equal(document.getElementById('map-tool-measure').getAttribute('aria-pressed'), 'true');
    assert.equal(document.getElementById('map-tool-pointer').getAttribute('aria-pressed'), 'false');
    document.getElementById('map-tool-pointer').click();
    assert.equal(document.getElementById('map-tool-pointer').getAttribute('aria-pressed'), 'true');

    // The color panel: one native <input type="color"> per row, wells included.
    document.getElementById('map-colors-toggle').click();
    var panel = document.getElementById('map-color-panel');
    assert.equal(panel.hidden, false);
    var inputs = panel.querySelectorAll('input[type="color"]');
    assert.equal(inputs.length, 4, 'wells + three layers');
    assert.equal(inputs[0].getAttribute('data-layer'), WELLS_ID);
    inputs[0].value = '#0a0b0c';
    inputs[0].dispatchEvent(new Event('input', { bubbles: true }));
    assert.equal(JSON.parse(window.localStorage.getItem(MAP_STATE_KEY)).colors[WELLS_ID], '#0a0b0c');

    // Collapsing the summary is persisted too.
    document.getElementById('map-summary-toggle').click();
    assert.equal(document.getElementById('map-summary-body').hidden, true);
    assert.equal(JSON.parse(window.localStorage.getItem(MAP_STATE_KEY)).summaryCollapsed, true);

    // The whole sidebar box folds to its header bar, and back.
    var box = document.getElementById('map-sidebar');
    var boxBody = document.getElementById('map-sidebar-body');
    var boxToggle = document.getElementById('map-sidebar-toggle');
    assert.equal(boxBody.hidden, false, 'desktop default is expanded');
    boxToggle.click();
    assert.equal(boxBody.hidden, true);
    assert.equal(boxToggle.getAttribute('aria-expanded'), 'false');
    assert.ok(box.classList.contains('is-collapsed'));
    assert.equal(JSON.parse(window.localStorage.getItem(MAP_STATE_KEY)).sidebarCollapsed, true);
    boxToggle.click();
    assert.equal(boxBody.hidden, false);
    assert.equal(JSON.parse(window.localStorage.getItem(MAP_STATE_KEY)).sidebarCollapsed, false);

    // Each fold inside it collapses independently, chevron state included.
    var filtersToggle = document.getElementById('map-filters-toggle');
    var filterList = document.getElementById('map-filter-list');
    filtersToggle.click();
    assert.equal(filterList.hidden, true);
    assert.equal(filtersToggle.getAttribute('aria-expanded'), 'false');
    assert.equal(filtersToggle.classList.contains('open'), false, 'the chevron flips shut');
    assert.equal(JSON.parse(window.localStorage.getItem(MAP_STATE_KEY)).filtersCollapsed, true);
    filtersToggle.click();
    assert.equal(filterList.hidden, false);
    assert.ok(filtersToggle.classList.contains('open'));
    assert.equal(JSON.parse(window.localStorage.getItem(MAP_STATE_KEY)).filtersCollapsed, false);

    var layersToggle = document.getElementById('map-layers-toggle');
    var layerList = document.getElementById('map-layer-list');
    layersToggle.click();
    assert.equal(layerList.hidden, true);
    assert.equal(layersToggle.getAttribute('aria-expanded'), 'false');
    assert.equal(layersToggle.classList.contains('open'), false);
    assert.equal(JSON.parse(window.localStorage.getItem(MAP_STATE_KEY)).layersCollapsed, true);

    // The container split did not cost the filters their handlers: a filter
    // change still moves both the summary and the wells row's count.
    var north = filterList.querySelector('.map-well-filter[data-filter="field"] input[value="North"]');
    north.checked = true;
    north.dispatchEvent(new Event('change', { bubbles: true }));
    assert.match(document.getElementById('map-summary-body').textContent, /Wells shown2/);
    assert.equal(layerList.querySelector('.map-layer[data-layer="' + WELLS_ID + '"] .map-layer-count').textContent, '2');
    north.checked = false;
    north.dispatchEvent(new Event('change', { bubbles: true }));

    // A re-render rewrites the container's CONTENTS, never its attributes —
    // so a collapsed fold stays collapsed across one.
    layerList.querySelector('.map-layer[data-layer="blocks"] .map-layer-move[data-dir="-1"]').click();
    assert.equal(document.getElementById('map-layer-list').hidden, true, 'the fold survives a re-render');

    /* A failed load writes its message into a container that may be hidden,
       and `hidden` takes it out of the accessibility tree as well — so the
       app's toast, which is independent of the box, is what carries the
       failure. The collapse state is deliberately left exactly as the user
       set it. */
    boxToggle.click();
    assert.equal(boxBody.hidden, true, 'collapsed, as a phone boots');
    var savedPrefs = window.localStorage.getItem(MAP_STATE_KEY);
    mockFetch(function () { throw new Error('network down'); });
    document.getElementById('map-reload').click();
    // Captured as it lands: the toast is a shared, self-clearing region that
    // any other view booted by this suite may write to next.
    var toasted = '';
    return waitFor(function () {
      var toast = document.getElementById('app-message');
      if (!toast || !/Could not load map layers/.test(toast.textContent)) return false;
      toasted = toast.textContent;
      return true;
    }).then(function () {
      assert.match(toasted, /Could not load map layers: network down/);
      assert.equal(boxBody.hidden, true, 'a failure does not prise the box open');
      assert.equal(layerList.hidden, true, 'nor the fold the user closed');
      assert.match(layerList.textContent, /Could not load layers: network down/,
        'the explanation is waiting inside the box for anyone who expands it');
      assert.equal(window.localStorage.getItem(MAP_STATE_KEY), savedPrefs, 'and nothing was persisted');
      try { window.localStorage.removeItem(MAP_STATE_KEY); } catch (err) { /* storage may be unavailable */ }
    });
  });
});
