/* =========================================================================
   Map tab — the canvas engine.

   A flat Cartesian view of UTM37N METRES with Google-Maps-style drag-to-pan
   and wheel-zoom-about-the-cursor. No tiles, no map library, no projection
   maths: ported from the standalone UTM37 viewer (static/js/map.js), whose
   mechanics are kept rather than rewritten — the DPR handling, the
   zoom-about-cursor algebra, the even-odd fill, and the hit test.

   View model: `scale` is CSS pixels per metre; (offsetX, offsetY) is the world
   coordinate at the canvas's BOTTOM-LEFT corner. Northing points up, so
   screen Y is flipped.

   Two things are new here versus the source:
   - DRAW ORDER comes from the store (borders pinned bottom, wells pinned
     top), and hit-testing walks the exact reverse of what was painted, so
     the topmost thing you can see is the thing you pick.
   - Every non-layer color is read from the app's CSS custom properties AT
     RENDER TIME, so the same canvas is correct in light and dark mode. The
     view re-renders on the app's 'theme:changed' event.
   ========================================================================= */

import { pointInRings, hasCoords, WELLS_ID } from './map-store.js';
import { TOOL_MEASURE, formatDistance, cumulativeLengths } from './map-tools.js';

// Project labels appear once a pixel is worth less than ~250 m, i.e. only when
// the view is tight enough for them not to pile into an unreadable mat.
export var WELL_LABEL_MIN_SCALE = 0.004;

var WELL_RADIUS = 5;
var HIT_RADIUS_PX = 7;

/* Scale bounds, in CSS pixels per metre. A trackpad's inertia can deliver a
   long tail of wheel events after the fingers have left the surface, and an
   unbounded product of those factors ends at a scale of 0 (a blank canvas
   whose hit tolerance is infinite) or at 1e30 (nothing on screen at all).
   1e-6 px/m puts the whole planet in a viewport; 50 px/m is 2 cm to the
   pixel, far past any survey coordinate's meaning. */
export var MIN_SCALE = 1e-6;
export var MAX_SCALE = 50;

export function clampScale(scale) {
  var value = Number(scale);
  if (!isFinite(value) || value <= 0) return MIN_SCALE;
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, value));
}

/* Wheel deltas are NOT one unit across browsers, and the zoom exponent below
   assumes pixels. Chrome/Edge report pixels (deltaMode 0); Firefox on Windows
   reports LINES (deltaMode 1, deltaY ≈ ±3 per notch), which fed straight into
   the exponent zooms roughly thirty times slower for the same flick; a
   page-scrolling device reports PAGES (deltaMode 2). Normalize to pixels
   first: a line is ~16px, a page is the viewport itself. */
export var WHEEL_LINE_PX = 16;

export function normalizeWheelDelta(deltaY, deltaMode, viewportHeight) {
  var delta = Number(deltaY);
  if (!isFinite(delta)) return 0;
  if (deltaMode === 1) return delta * WHEEL_LINE_PX;
  if (deltaMode === 2) return delta * (Number(viewportHeight) || 0);
  return delta;
}

// Every color the canvas draws that is NOT a user-chosen layer color resolves
// through the app's design tokens. Read once and CACHED by the canvas (see
// _colors below): the values only change when the theme does, and getComputed
// Style on every frame is a style recalc per paint.
export function themeColors(root) {
  var element = root || document.documentElement;
  var styles = getComputedStyle(element);
  function token(name, fallback) {
    var value = String(styles.getPropertyValue(name) || '').trim();
    return value || fallback;
  }
  return {
    canvas: token('--surface-sunken', '#f6f9fb'),
    surface: token('--surface', '#ffffff'),
    text: token('--text', '#18293a'),
    muted: token('--text-muted', '#405568'),
    borderLine: token('--border-strong', '#c3d2dd'),
    measure: token('--asas-blue', '#1d4ed8')
  };
}

export class MapCanvas {
  constructor(canvas, store) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.store = store;
    this.scale = 1;
    this.offsetX = 0;
    this.offsetY = 0;
    this.dpr = window.devicePixelRatio || 1;
    this.width = 0;    // CSS pixels
    this.height = 0;
    this.tool = 'pointer';
    this.measure = null;      // a MeasureTool, handed in by the view
    this._drag = null;
    this._raf = null;
    this._colorCache = null;  // themeColors(), dropped on 'theme:changed'
    this._hoverRaf = null;    // one hit-test per FRAME, not per mousemove
    this._hoverPoint = null;
    this._resizeTimer = null;
    this._tabEl = undefined;  // #tab-map, looked up once (see _isTabActive)

    this.onHover = function () {};        // (worldX, worldY, screenX, screenY)
    this.onPick = function () {};         // (hit|null, screenX, screenY)
    this.onLeave = function () {};
    this.onMeasureChange = function () {};

    this._bindEvents();
  }

  /* ---- sizing ------------------------------------------------------------
     The Map tab is display:none until it is activated, and a canvas measured
     while hidden is 0x0 — every caller must resize() on activation, not just
     once at construction. A zero measurement is ignored rather than stored,
     so a stray resize while hidden cannot wipe a good size. */
  resize() {
    var rect = this.canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return false;
    this.width = rect.width;
    this.height = rect.height;
    this.dpr = window.devicePixelRatio || 1;
    this.canvas.width = Math.round(rect.width * this.dpr);
    this.canvas.height = Math.round(rect.height * this.dpr);
    this.requestRender();
    return true;
  }

  get isSized() { return this.width > 0 && this.height > 0; }

  // ---- coordinate transforms ----------------------------------------------
  worldToScreen(x, y) {
    return [(x - this.offsetX) * this.scale, this.height - (y - this.offsetY) * this.scale];
  }

  screenToWorld(sx, sy) {
    return [this.offsetX + sx / this.scale, this.offsetY + (this.height - sy) / this.scale];
  }

  // Fit a UTM bbox [minx,miny,maxx,maxy] into the viewport with padding.
  fitBbox(bbox, padding) {
    if (!bbox || !this.isSized) return false;
    var pad = padding === undefined ? 0.08 : padding;
    var minx = bbox[0], miny = bbox[1], maxx = bbox[2], maxy = bbox[3];
    var dw = maxx - minx;
    var dh = maxy - miny;
    // Degenerate (single point / empty) -> give it a default span.
    if (dw <= 0) { minx -= 5000; maxx += 5000; dw = maxx - minx; }
    if (dh <= 0) { miny -= 5000; maxy += 5000; dh = maxy - miny; }
    var sx = (this.width * (1 - 2 * pad)) / dw;
    var sy = (this.height * (1 - 2 * pad)) / dh;
    this.scale = Math.min(sx, sy);
    var cx = (minx + maxx) / 2;
    var cy = (miny + maxy) / 2;
    this.offsetX = cx - this.width / 2 / this.scale;
    this.offsetY = cy - this.height / 2 / this.scale;
    this.requestRender();
    return true;
  }

  // Zoom keeping the world point under (sx, sy) pinned to that pixel. The
  // new scale is CLAMPED before the offsets are derived from it, so the pin
  // still holds at either limit instead of drifting.
  zoomAt(sx, sy, factor) {
    var world = this.screenToWorld(sx, sy);
    this.scale = clampScale(this.scale * factor);
    this.offsetX = world[0] - sx / this.scale;
    this.offsetY = world[1] - (this.height - sy) / this.scale;
    this.requestRender();
  }

  zoomCenter(factor) { this.zoomAt(this.width / 2, this.height / 2, factor); }

  // ---- events -------------------------------------------------------------
  _localPoint(event) {
    var rect = this.canvas.getBoundingClientRect();
    return [event.clientX - rect.left, event.clientY - rect.top, rect];
  }

  _bindEvents() {
    var self = this;
    var canvas = this.canvas;

    canvas.addEventListener('mousedown', function (event) {
      if (event.button !== 0) return;
      self._drag = { x: event.clientX, y: event.clientY, ox: self.offsetX, oy: self.offsetY, moved: false };
      self._lastDragMoved = false;
      canvas.classList.add('dragging');
    });

    window.addEventListener('mousemove', function (event) {
      if (!self.isSized) return;
      if (self._drag) {
        var dx = event.clientX - self._drag.x;
        var dy = event.clientY - self._drag.y;
        if (Math.abs(dx) + Math.abs(dy) > 2) self._drag.moved = true;
        self.offsetX = self._drag.ox - dx / self.scale;
        self.offsetY = self._drag.oy + dy / self.scale;
        self.requestRender();
        return;
      }
      // The listener is on WINDOW (so a drag that leaves the canvas keeps
      // panning), which means it also fires for every mouse move on every
      // OTHER tab. Bail before touching geometry: _localPoint's
      // getBoundingClientRect would flush layout on a page the map is not
      // even part of.
      if (!self._isTabActive()) return;
      var local = self._localPoint(event);
      var sx = local[0], sy = local[1], rect = local[2];
      if (sx < 0 || sy < 0 || sx > rect.width || sy > rect.height) return;
      self._queueHover(sx, sy);
    });

    // mouseup runs BEFORE click, so whether the pointer travelled is latched
    // here — by the time the click handler asks, _drag is already gone.
    window.addEventListener('mouseup', function () {
      if (!self._drag) return;
      self._lastDragMoved = self._drag.moved;
      self._drag = null;
      canvas.classList.remove('dragging');
    });

    // A measurement vertex is a CLICK, not the end of a pan: panning stays
    // available with the measure tool active, and only a pointer that did not
    // travel drops a vertex.
    canvas.addEventListener('click', function (event) {
      if (self.tool !== TOOL_MEASURE || !self.measure) return;
      if (self._dragMoved) return;
      var local = self._localPoint(event);
      var world = self.screenToWorld(local[0], local[1]);
      self.measure.addPoint(world[0], world[1]);
      self.onMeasureChange();
      self.requestRender();
    });

    canvas.addEventListener('dblclick', function (event) {
      if (self.tool !== TOOL_MEASURE || !self.measure) return;
      event.preventDefault();
      self.measure.finish();
      self.onMeasureChange();
      self.requestRender();
    });

    // Drop any hover scheduled for the next frame with it, or a queued pick
    // would re-show the tooltip the leave just hid.
    canvas.addEventListener('mouseleave', function () {
      self._hoverPoint = null;
      self.onLeave();
    });

    canvas.addEventListener('wheel', function (event) {
      event.preventDefault();
      if (!self.isSized) return;
      var local = self._localPoint(event);
      var delta = normalizeWheelDelta(event.deltaY, event.deltaMode, self.height);
      self.zoomAt(local[0], local[1], Math.exp(-delta * 0.0015));
    }, { passive: false });

    // A window resize arrives as a burst; each one re-allocates the backing
    // store (width/height assignment) and repaints, so only the last one in
    // ~100 ms is acted on.
    window.addEventListener('resize', function () {
      if (self._resizeTimer) clearTimeout(self._resizeTimer);
      self._resizeTimer = setTimeout(function () {
        self._resizeTimer = null;
        self.resize();
      }, 100);
    });
    // Canvas colors are CSS custom properties. They are read once and cached,
    // so a theme flip drops the cache and repaints.
    document.addEventListener('theme:changed', function () {
      self._colorCache = null;
      self.requestRender();
    });
  }

  /* Is the map tab the visible one? A classList read and nothing else — the
     whole point of this guard is to cost nothing while the user is on another
     tab, so offsetParent/getBoundingClientRect (both of which flush layout)
     are exactly what it must not do. The element is looked up ONCE: it is
     part of index.html's static shell and never replaced. A page without the
     wrapper at all (a test harness, a standalone embed) counts as active. */
  _isTabActive() {
    if (this._tabEl === undefined) this._tabEl = document.getElementById('tab-map');
    return !this._tabEl || this._tabEl.classList.contains('active');
  }

  /* Coalesce hovering into one hit-test per animation FRAME. A mouse can
     deliver events faster than the display refreshes (120+ Hz pointers, and
     coalesced batches on top), and _pick walks every feature of every visible
     layer — running it per event spends the frame budget on answers nobody
     ever sees. The latest position wins. */
  _queueHover(sx, sy) {
    var self = this;
    this._hoverPoint = [sx, sy];
    if (this._hoverRaf) return;
    this._hoverRaf = requestAnimationFrame(function () {
      self._hoverRaf = null;
      var point = self._hoverPoint;
      if (!point) return;
      var world = self.screenToWorld(point[0], point[1]);
      self.onHover(world[0], world[1], point[0], point[1]);
      if (self.tool === TOOL_MEASURE && self.measure && self.measure.drafting) {
        self.measure.moveCursor(world[0], world[1]);
        self.onMeasureChange();
        self.requestRender();
      }
      self.onPick(self._pick(point[0], point[1]), point[0], point[1]);
    });
  }

  // mouseup clears _drag before the click event fires, so the "did the
  // pointer travel" answer is latched here.
  get _dragMoved() { return !!(this._lastDragMoved); }

  // ---- rendering ----------------------------------------------------------
  requestRender() {
    var self = this;
    if (this._raf) return;
    this._raf = requestAnimationFrame(function () { self._raf = null; self.render(); });
  }

  // The design tokens, cached until the theme changes (see the
  // 'theme:changed' listener, which is the only thing that can move them).
  _colors() {
    if (!this._colorCache) this._colorCache = themeColors();
    return this._colorCache;
  }

  render() {
    if (!this.isSized) return;
    var ctx = this.ctx;
    var colors = this._colors();
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, this.width, this.height);
    ctx.fillStyle = this.store.backgroundColor || colors.canvas;
    ctx.fillRect(0, 0, this.width, this.height);

    // Bottom -> top exactly as the store orders it (borders pinned bottom).
    var layers = this.store.drawOrder();
    for (var i = 0; i < layers.length; i += 1) {
      var layer = layers[i];
      if (!layer.visible || !layer.features) continue;
      this._drawLayer(ctx, layer, colors);
    }
    // The wells overlay is pinned above every shapefile by construction.
    if (this.store.wellsVisible) this._drawWells(ctx, colors);
    this._drawMeasurement(ctx, colors);
  }

  _drawLayer(ctx, layer, colors) {
    for (var i = 0; i < layer.features.length; i += 1) {
      var geometry = layer.features[i].geometry;
      if (!geometry) continue;
      if (geometry.type === 'point') this._drawPoints(ctx, geometry.coordinates, layer.color);
      else if (geometry.type === 'line') this._drawRings(ctx, geometry.coordinates, layer, false, colors);
      else this._drawRings(ctx, geometry.coordinates, layer, !layer.isBorders, colors);
    }
  }

  _drawRings(ctx, rings, layer, fill, colors) {
    ctx.beginPath();
    for (var r = 0; r < rings.length; r += 1) {
      var ring = rings[r];
      for (var i = 0; i < ring.length; i += 1) {
        var point = this.worldToScreen(ring[i][0], ring[i][1]);
        if (i === 0) ctx.moveTo(point[0], point[1]);
        else ctx.lineTo(point[0], point[1]);
      }
    }
    if (fill) {
      ctx.fillStyle = withAlpha(layer.fillColor || layer.color, 0.18);
      ctx.fill('evenodd');
    }
    ctx.lineJoin = 'round';
    ctx.lineWidth = layer.isBorders ? 1.1 : 1.6;
    ctx.strokeStyle = layer.isBorders ? colors.borderLine : layer.color;
    ctx.stroke();
  }

  _drawPoints(ctx, coords, color) {
    ctx.fillStyle = color;
    ctx.strokeStyle = 'rgba(0,0,0,0.55)';
    ctx.lineWidth = 1;
    for (var i = 0; i < coords.length; i += 1) {
      var point = this.worldToScreen(coords[i][0], coords[i][1]);
      ctx.beginPath();
      ctx.arc(point[0], point[1], 4.5, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
    }
  }

  // Wells: a filled disc in the overlay's color with a contrasting surface
  // ring, so they read against both a pale polygon fill and the dark canvas.
  _drawWells(ctx, colors) {
    var wells = this.store.plottedWells();
    if (!wells.length) return;
    var showLabels = this.scale >= WELL_LABEL_MIN_SCALE;
    ctx.lineWidth = 1.6;
    for (var i = 0; i < wells.length; i += 1) {
      var point = this.worldToScreen(wells[i].x, wells[i].y);
      if (point[0] < -40 || point[1] < -40 || point[0] > this.width + 40 || point[1] > this.height + 40) continue;
      ctx.beginPath();
      ctx.arc(point[0], point[1], WELL_RADIUS, 0, Math.PI * 2);
      ctx.fillStyle = this.store.wellsColor;
      ctx.fill();
      ctx.strokeStyle = colors.surface;
      ctx.stroke();
      if (showLabels && wells[i].project_name) {
        this._label(ctx, String(wells[i].project_name), point[0] + WELL_RADIUS + 4, point[1] + 4, colors);
      }
    }
  }

  // Haloed text: stroked in the surface color first so a label stays legible
  // over a polygon fill in either theme.
  _label(ctx, text, x, y, colors) {
    ctx.font = '11px "Segoe UI", system-ui, sans-serif';
    ctx.lineJoin = 'round';
    ctx.lineWidth = 3;
    ctx.strokeStyle = colors.canvas;
    ctx.strokeText(text, x, y);
    ctx.fillStyle = colors.text;
    ctx.fillText(text, x, y);
    ctx.lineWidth = 1.6;
  }

  // The measurement chain: vertices, per-segment labels at each midpoint and
  // the running total at the last vertex. UTM metres, so the numbers are
  // exact rather than latitude-corrected.
  _drawMeasurement(ctx, colors) {
    if (!this.measure) return;
    var points = this.measure.livePoints();
    if (points.length < 1) return;
    var screen = [];
    for (var i = 0; i < points.length; i += 1) screen.push(this.worldToScreen(points[i][0], points[i][1]));

    ctx.save();
    ctx.strokeStyle = colors.measure;
    ctx.lineWidth = 2;
    ctx.lineJoin = 'round';
    if (screen.length > 1) {
      ctx.beginPath();
      ctx.moveTo(screen[0][0], screen[0][1]);
      for (var s = 1; s < screen.length; s += 1) ctx.lineTo(screen[s][0], screen[s][1]);
      ctx.stroke();
    }
    ctx.fillStyle = colors.measure;
    for (var v = 0; v < screen.length; v += 1) {
      ctx.beginPath();
      ctx.arc(screen[v][0], screen[v][1], 3.5, 0, Math.PI * 2);
      ctx.fill();
    }
    var cumulative = cumulativeLengths(points);
    for (var m = 1; m < points.length; m += 1) {
      var mx = (screen[m - 1][0] + screen[m][0]) / 2;
      var my = (screen[m - 1][1] + screen[m][1]) / 2;
      this._label(ctx, formatDistance(cumulative[m] - cumulative[m - 1]), mx + 6, my - 4, colors);
    }
    if (points.length > 1) {
      var last = screen[screen.length - 1];
      this._label(ctx, 'Σ ' + formatDistance(cumulative[cumulative.length - 1]), last[0] + 8, last[1] - 8, colors);
    }
    ctx.restore();
  }

  /* ---- hit testing -------------------------------------------------------
     Walks the EXACT REVERSE of the draw order, so the topmost visible thing
     wins, and the always-on-top wells overlay is tested first of all.

     TWO rules make "what you point at is what you get" true rather than
     nearly true:
     - among WELLS the NEAREST inside the tolerance wins, not the first in
       array order. The tolerance is 7 px expressed in metres, which at a
       fit-the-country zoom is over ten kilometres — wider than the scatter
       within one field, so first-hit would pin the same project for a whole
       cluster no matter which of them the cursor sat on.
     - within a LAYER the features are walked in reverse DRAW order, so where
       polygons overlap the one painted last (the one you can see) answers.

     Returns { kind: 'well', well, index } or
             { kind: 'feature', layer, feature, featureIndex } or null. */
  _pick(sx, sy) {
    var world = this.screenToWorld(sx, sy);
    var wx = world[0], wy = world[1];
    var tolerance = HIT_RADIUS_PX / this.scale;

    if (this.store.wellsVisible) {
      var wells = this.store.wells;
      var nearest = null;
      var nearestDistance = Infinity;
      for (var w = 0; w < wells.length; w += 1) {
        if (!hasCoords(wells[w])) continue;
        var distance = Math.hypot(wells[w].x - wx, wells[w].y - wy);
        if (distance > tolerance || distance >= nearestDistance) continue;
        nearestDistance = distance;
        nearest = { kind: 'well', well: wells[w], index: w, layerName: WELLS_ID };
      }
      if (nearest) return nearest;
    }

    var layers = this.store.drawOrder().reverse();
    for (var l = 0; l < layers.length; l += 1) {
      var layer = layers[l];
      if (!layer.visible || !layer.features) continue;
      for (var f = layer.features.length - 1; f >= 0; f -= 1) {
        var feature = layer.features[f];
        var geometry = feature && feature.geometry;
        if (!geometry) continue;
        if (geometry.type === 'point') {
          for (var p = 0; p < geometry.coordinates.length; p += 1) {
            var coordinate = geometry.coordinates[p];
            if (Math.hypot(coordinate[0] - wx, coordinate[1] - wy) <= tolerance) {
              return { kind: 'feature', layer: layer, feature: feature, featureIndex: f };
            }
          }
        } else if (geometry.type === 'polygon') {
          if (pointInRings(wx, wy, geometry.coordinates)) {
            return { kind: 'feature', layer: layer, feature: feature, featureIndex: f };
          }
        }
      }
    }
    return null;
  }
}

// rgba() from a #rrggbb layer color (ported from the source viewer's _alpha).
// Layer colors are always six-digit hex — they come from the palette or from
// an <input type="color"> — so no other notation has to be parsed.
export function withAlpha(hex, alpha) {
  var value = String(hex || '').replace('#', '');
  if (value.length !== 6) return hex;
  var r = parseInt(value.slice(0, 2), 16);
  var g = parseInt(value.slice(2, 4), 16);
  var b = parseInt(value.slice(4, 6), 16);
  return 'rgba(' + r + ',' + g + ',' + b + ',' + alpha + ')';
}
