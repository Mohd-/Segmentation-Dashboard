/* =========================================================================
   Map tab — the data layer.

   Everything in this module is state + pure computation: layer metadata,
   visibility, colors, DRAW ORDER, lazily fetched geometry, the wells overlay,
   the well->polygon association, the summary numbers, and the localStorage
   round trip. It knows nothing about the canvas or the DOM, which is what
   makes the interesting rules (point-in-polygon, ordering/pinning, OGIP
   totals) testable without booting a view.

   Ported from the standalone UTM37 map viewer (static/js/layers.js +
   map.js's pointInRings): the mechanics are kept verbatim where they were
   already proven — even-odd point-in-rings, lazy per-layer geometry fetch,
   metadata-preserving reload.

   DRAW ORDER is the spine of this module. `order` is an array of SHAPEFILE
   layer names, bottom-most first. Two layers are PINNED and never appear in
   it: the country-borders pseudo-layer is always drawn first (bottom) and the
   Wells overlay is always drawn last (top). The sidebar renders the reverse
   of the draw order, so "up" in the sidebar means "later in the draw order".
   ========================================================================= */

// Distinct, reasonably color-blind-friendly palette for shapefile layers
// (ported verbatim from the source viewer).
export var PALETTE = [
  '#4a9eff', '#ff8c42', '#3ecf8e', '#e15bb5',
  '#f4c430', '#9b7bff', '#ff6b6b', '#2dd4bf'
];

// The wells overlay is not a server layer, so it needs a stable id of its own
// for the persisted visibility/color maps and for the color panel's rows.
export var WELLS_ID = '__wells__';
export var WELLS_DEFAULT_COLOR = '#e05252';
export var BORDERS_DEFAULT_COLOR = '#8fa3b8';

export var MAP_STATE_KEY = 'asas.map.state';
export var MAP_STATE_VERSION = 1;

/* -------------------------------------------------------------------------
   Geometry (pure)
   ------------------------------------------------------------------------- */

// Even-odd point-in-polygon across ALL rings of one feature (outer + holes).
// Ported verbatim from the source viewer: a point inside a hole crosses an
// even number of edges and therefore reads as outside, which is exactly the
// same rule the canvas fills with ('evenodd'), so what you see is what hits.
export function pointInRings(x, y, rings) {
  var inside = false;
  if (!rings) return false;
  for (var r = 0; r < rings.length; r += 1) {
    var ring = rings[r];
    if (!ring) continue;
    for (var i = 0, j = ring.length - 1; i < ring.length; j = i, i += 1) {
      var xi = ring[i][0], yi = ring[i][1];
      var xj = ring[j][0], yj = ring[j][1];
      var intersect = (yi > y) !== (yj > y)
        && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi;
      if (intersect) inside = !inside;
    }
  }
  return inside;
}

// Union of two [minx,miny,maxx,maxy] boxes; either may be null.
export function unionBbox(a, b) {
  if (!a) return b ? b.slice() : null;
  if (!b) return a.slice();
  return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[2]), Math.max(a[3], b[3])];
}

/* -------------------------------------------------------------------------
   Labels + number formatting (pure)
   ------------------------------------------------------------------------- */

// The best human label for a polygon feature: a "name"-like string attribute
// out of the shapefile's DBF, else the layer name. Shapefiles in the wild
// spell it NAME / Name / BLOCK_NAME / name_en / LABEL, so the search is by
// shape of the key, not by an allow-list.
export function polygonLabel(feature, layerName) {
  var props = (feature && feature.properties) || {};
  var keys = Object.keys(props);
  var tiers = [
    function (k) { return k === 'name'; },
    function (k) { return k.indexOf('name') === 0 || /_name$/.test(k); },
    function (k) { return k.indexOf('name') >= 0; },
    function (k) { return k === 'label' || k === 'title'; }
  ];
  for (var t = 0; t < tiers.length; t += 1) {
    for (var i = 0; i < keys.length; i += 1) {
      if (!tiers[t](String(keys[i]).toLowerCase())) continue;
      var value = props[keys[i]];
      if (value === null || value === undefined) continue;
      var text = String(value).trim();
      if (text) return text;
    }
  }
  return layerName || '';
}

// Mean OGIP for display. Null/blank/non-numeric is an em dash, never 0 —
// "no figure recorded" and "zero gas" are different statements. Sums treat
// the missing ones as 0 (see computeSummary), which is a different rule on
// purpose.
export function formatOgip(value) {
  if (value === null || value === undefined || value === '') return '—';
  var numeric = Number(value);
  if (!isFinite(numeric)) return '—';
  return numeric.toFixed(2);
}

export function ogipValue(value) {
  var numeric = Number(value);
  return isFinite(numeric) && value !== null && value !== undefined && value !== '' ? numeric : 0;
}

function toCoordinate(value) {
  if (value === null || value === undefined || value === '') return null;
  var numeric = Number(value);
  return isFinite(numeric) ? numeric : null;
}

export function hasCoords(well) {
  return !!well && isFinite(Number(well.x)) && isFinite(Number(well.y))
    && well.x !== null && well.y !== null && well.x !== '' && well.y !== '';
}

/* -------------------------------------------------------------------------
   Well <-> polygon association (pure)
   ------------------------------------------------------------------------- */

// One feature's identity inside an association result. Feature objects are
// not keys we can serialise or compare in a test, so a layer-scoped index is.
export function featureKey(layerName, featureIndex) {
  return layerName + '#' + featureIndex;
}

/* Associate every well with every polygon feature that contains it.

   Only VISIBLE, LOADED, non-borders polygon layers take part: an unticked
   layer must not silently keep contributing to the summary counts or to a
   tooltip, and the country-borders background would otherwise "contain"
   every well in the country, which is noise rather than an association.

   Returns two views of the same relation so neither hover direction has to
   scan:
     polysFor[wellIndex] -> [{ layerName, featureIndex, key, label }]
     wellsFor[featureKey] -> [wellIndex] */
export function computeAssociations(wells, layers) {
  var polysFor = [];
  var wellsFor = {};
  // Deliberately keyed off each FEATURE's geometry.type below, not off the
  // layer's declared geom_type: a shapefile set can advertise one type in its
  // metadata and carry another in its records (the borders layer does exactly
  // that), and a mis-declared layer must not silently stop associating.
  var polyLayers = (layers || []).filter(function (layer) {
    return layer && layer.visible && !layer.isBorders && layer.features;
  });
  (wells || []).forEach(function (well, wellIndex) {
    var hits = [];
    if (hasCoords(well)) {
      var wx = Number(well.x);
      var wy = Number(well.y);
      polyLayers.forEach(function (layer) {
        layer.features.forEach(function (feature, featureIndex) {
          var geometry = feature && feature.geometry;
          if (!geometry || geometry.type !== 'polygon') return;
          if (!pointInRings(wx, wy, geometry.coordinates)) return;
          var key = featureKey(layer.name, featureIndex);
          hits.push({
            layerName: layer.name,
            featureIndex: featureIndex,
            key: key,
            label: polygonLabel(feature, layer.name)
          });
          if (!wellsFor[key]) wellsFor[key] = [];
          wellsFor[key].push(wellIndex);
        });
      });
    }
    polysFor.push(hits);
  });
  return { polysFor: polysFor, wellsFor: wellsFor };
}

/* -------------------------------------------------------------------------
   Summary panel numbers (pure)
   ------------------------------------------------------------------------- */

/* The four figures the floating summary panel shows.

   visibleLayers counts the MAP layers that are ticked (shapefiles + the
   borders pseudo-layer); the wells overlay is reported separately as
   wellsPlotted rather than being counted as a layer.

   totalOgip sums at full precision and is rounded once, by the caller, for
   display — a null mean_gas_bcf contributes 0 to the sum (there is nothing
   to add) even though it displays as an em dash on its own. */
export function computeSummary(layers, wells, associations) {
  var visibleLayers = (layers || []).filter(function (layer) { return layer && layer.visible; }).length;
  var plotted = (wells || []).filter(hasCoords);
  var polysFor = (associations && associations.polysFor) || [];
  var wellsInside = 0;
  (wells || []).forEach(function (well, index) {
    if (!hasCoords(well)) return;
    if ((polysFor[index] || []).length) wellsInside += 1;
  });
  var totalOgip = plotted.reduce(function (sum, well) { return sum + ogipValue(well.mean_gas_bcf); }, 0);
  return {
    visibleLayers: visibleLayers,
    wellsPlotted: plotted.length,
    wellsInside: wellsInside,
    wellsTotal: (wells || []).length,
    totalOgip: totalOgip
  };
}

/* -------------------------------------------------------------------------
   Persisted state (localStorage)
   ------------------------------------------------------------------------- */

/* The persisted shape, under 'asas.map.state':

     { "version": 1,
       "order": ["blocks", "fields"],        // shapefile names, bottom -> top
       "visible": { "blocks": true, "__wells__": false },
       "colors":  { "blocks": "#4a9eff", "__wells__": "#e05252" },
       "summaryCollapsed": false }

   Nothing in here is authoritative about which layers EXIST — the server's
   layer list is. Names that have since disappeared are dropped on apply, and
   names the state has never seen are appended on top with a palette color, so
   a stale state can only ever mean "some preferences were lost", never a
   phantom layer or a crash. */
export function readMapState(storage) {
  var store = storage || safeStorage();
  if (!store) return null;
  try {
    var raw = store.getItem(MAP_STATE_KEY);
    if (!raw) return null;
    var parsed = JSON.parse(raw);
    return normalizeState(parsed);
  } catch (err) {
    return null;
  }
}

export function writeMapState(state, storage) {
  var store = storage || safeStorage();
  if (!store) return false;
  try {
    store.setItem(MAP_STATE_KEY, JSON.stringify(normalizeState(state)));
    return true;
  } catch (err) {
    return false;
  }
}

export function normalizeState(raw) {
  var input = raw && typeof raw === 'object' ? raw : {};
  var order = Array.isArray(input.order) ? input.order.filter(function (name) { return typeof name === 'string'; }) : [];
  return {
    version: MAP_STATE_VERSION,
    order: order,
    visible: plainMap(input.visible, function (value) { return !!value; }),
    colors: plainMap(input.colors, function (value) { return typeof value === 'string' && /^#[0-9a-fA-F]{6}$/.test(value) ? value.toLowerCase() : null; }),
    summaryCollapsed: !!input.summaryCollapsed
  };
}

function plainMap(source, coerce) {
  var out = {};
  if (!source || typeof source !== 'object') return out;
  Object.keys(source).forEach(function (key) {
    var value = coerce(source[key]);
    if (value !== null) out[key] = value;
  });
  return out;
}

function safeStorage() {
  try { return window.localStorage; } catch (err) { return null; }
}

/* -------------------------------------------------------------------------
   The store
   ------------------------------------------------------------------------- */

export class LayerStore {
  constructor(fetchLayerGeometry) {
    this.layers = new Map();          // name -> layer object
    this.order = [];                  // shapefile names, bottom -> top
    this.bordersName = null;
    this.wells = [];
    this.wellsVisible = true;
    this.wellsColor = WELLS_DEFAULT_COLOR;
    this.summaryCollapsed = false;
    this.prefs = normalizeState(null);
    this._fetchLayer = fetchLayerGeometry || function () { return Promise.resolve({ features: [] }); };
    this._assoc = null;
  }

  // Preferences restored from localStorage. Applied by setLayers (colors and
  // visibility) and by the constructor of the view (summary collapse), never
  // trusted as a list of layers in its own right.
  applyState(state) {
    this.prefs = normalizeState(state);
    this.summaryCollapsed = this.prefs.summaryCollapsed;
    if (Object.prototype.hasOwnProperty.call(this.prefs.visible, WELLS_ID)) {
      this.wellsVisible = this.prefs.visible[WELLS_ID];
    }
    if (this.prefs.colors[WELLS_ID]) this.wellsColor = this.prefs.colors[WELLS_ID];
    return this;
  }

  toState() {
    var visible = {};
    var colors = {};
    this.layers.forEach(function (layer, name) {
      visible[name] = !!layer.visible;
      colors[name] = layer.color;
    });
    visible[WELLS_ID] = !!this.wellsVisible;
    colors[WELLS_ID] = this.wellsColor;
    return {
      version: MAP_STATE_VERSION,
      order: this.order.slice(),
      visible: visible,
      colors: colors,
      summaryCollapsed: !!this.summaryCollapsed
    };
  }

  /* Rebuild from /api/map/layers metadata. Visibility, color and already
     fetched geometry survive a reload for every layer that still exists;
     anything else comes from the persisted prefs, then from the palette. */
  setLayers(metaList) {
    var previous = this.layers;
    var prefs = this.prefs;
    var palette = 0;
    this.layers = new Map();
    this.bordersName = null;
    var self = this;
    (metaList || []).forEach(function (meta) {
      if (!meta || !meta.name) return;
      var old = previous.get(meta.name);
      var isBorders = !!meta.is_borders;
      if (isBorders) self.bordersName = meta.name;
      var color = (old && old.color)
        || prefs.colors[meta.name]
        || (isBorders ? BORDERS_DEFAULT_COLOR : PALETTE[palette % PALETTE.length]);
      if (!isBorders && !(old && old.color) && !prefs.colors[meta.name]) palette += 1;
      var visible = old ? old.visible
        : (Object.prototype.hasOwnProperty.call(prefs.visible, meta.name) ? prefs.visible[meta.name] : true);
      self.layers.set(meta.name, {
        name: meta.name,
        geomType: meta.geom_type,
        bbox: meta.bbox || null,
        featureCount: meta.feature_count,
        isBorders: isBorders,
        color: color,
        visible: visible,
        features: old ? old.features : null,
        loading: false
      });
    });
    this._rebuildOrder();
    this.invalidate();
    return this;
  }

  // Draw order = persisted order (minus names that no longer exist) followed
  // by any layer the persisted order has never seen. Borders is pinned out of
  // the list entirely: it is always the bottom-most draw.
  _rebuildOrder() {
    var self = this;
    var seen = {};
    var order = [];
    this.prefs.order.forEach(function (name) {
      var layer = self.layers.get(name);
      if (!layer || layer.isBorders || seen[name]) return;   // stale name -> dropped
      seen[name] = true;
      order.push(name);
    });
    this.layers.forEach(function (layer, name) {
      if (layer.isBorders || seen[name]) return;
      seen[name] = true;
      order.push(name);
    });
    this.order = order;
    this.prefs.order = order.slice();
  }

  get bordersLayer() { return this.bordersName ? this.layers.get(this.bordersName) : null; }

  // Bottom -> top. Wells are NOT here: the canvas draws that overlay last, by
  // construction, so nothing can push a shapefile above it.
  drawOrder() {
    var out = [];
    var borders = this.bordersLayer;
    if (borders) out.push(borders);
    var self = this;
    this.order.forEach(function (name) {
      var layer = self.layers.get(name);
      if (layer) out.push(layer);
    });
    return out;
  }

  // Top -> bottom: what the sidebar lists and what hit-testing walks.
  sidebarOrder() { return this.drawOrder().slice().reverse(); }

  visibleLayers() { return this.drawOrder().filter(function (layer) { return layer.visible; }); }

  /* Move one shapefile layer one step through the draw order.
     direction: +1 = towards the top (later), -1 = towards the bottom.
     Borders and the wells overlay are pinned and cannot be moved, and no move
     can carry a layer past either of them because neither is in `order`. */
  moveLayer(name, direction) {
    var index = this.order.indexOf(name);
    if (index < 0) return false;
    var target = index + (direction > 0 ? 1 : -1);
    if (target < 0 || target >= this.order.length) return false;
    var moved = this.order.splice(index, 1)[0];
    this.order.splice(target, 0, moved);
    this.prefs.order = this.order.slice();
    this.invalidate();
    return true;
  }

  setVisible(name, visible) {
    if (name === WELLS_ID) { this.wellsVisible = !!visible; this.invalidate(); return true; }
    var layer = this.layers.get(name);
    if (!layer) return false;
    layer.visible = !!visible;
    this.invalidate();
    return true;
  }

  setColor(name, color) {
    if (!/^#[0-9a-fA-F]{6}$/.test(String(color || ''))) return false;
    var value = String(color).toLowerCase();
    if (name === WELLS_ID) { this.wellsColor = value; return true; }
    var layer = this.layers.get(name);
    if (!layer) return false;
    layer.color = value;
    return true;
  }

  /* Wells arrive as JSON, so x/y may be numbers, numeric strings or null.
     They are coerced to real numbers ONCE here — but a missing coordinate
     stays null rather than becoming Number(null) === 0, which would plot
     every uncoordinated project at the UTM origin. */
  setWells(wells) {
    this.wells = (wells || []).map(function (well) {
      return Object.assign({}, well, { x: toCoordinate(well.x), y: toCoordinate(well.y) });
    });
    this.invalidate();
    return this;
  }

  plottedWells() { return this.wells.filter(hasCoords); }

  wellsBbox() {
    var box = null;
    this.plottedWells().forEach(function (well) {
      box = unionBbox(box, [well.x, well.y, well.x, well.y]);
    });
    return box;
  }

  // Ensure one layer's geometry is loaded. Concurrent callers share the one
  // in-flight promise rather than firing a second request.
  ensureLoaded(layer) {
    if (!layer) return Promise.resolve(null);
    if (layer.features) return Promise.resolve(layer);
    if (layer._pending) return layer._pending;
    var self = this;
    layer.loading = true;
    layer._pending = Promise.resolve(this._fetchLayer(layer.name)).then(function (data) {
      layer.features = (data && data.features) || [];
      if (data && data.bbox) layer.bbox = data.bbox;
      self.invalidate();
      return layer;
    }).catch(function (err) {
      layer.features = [];
      layer.error = String((err && err.message) || err);
      return layer;
    }).then(function (result) {
      layer.loading = false;
      layer._pending = null;
      return result;
    });
    return layer._pending;
  }

  loadVisible() {
    var self = this;
    return Promise.all(this.visibleLayers().map(function (layer) { return self.ensureLoaded(layer); }));
  }

  visibleBbox() {
    var box = null;
    this.visibleLayers().forEach(function (layer) { box = unionBbox(box, layer.bbox); });
    if (this.wellsVisible) box = unionBbox(box, this.wellsBbox());
    return box;
  }

  // The association cache. Visibility, order, color-free data changes and new
  // geometry all invalidate it; nothing else can, so a hover never pays for a
  // recompute.
  invalidate() { this._assoc = null; }

  associations() {
    if (!this._assoc) this._assoc = computeAssociations(this.wells, this.drawOrder());
    return this._assoc;
  }

  summary() { return computeSummary(this.drawOrder(), this.wells, this.associations()); }
}
