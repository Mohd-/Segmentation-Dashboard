/* =========================================================================
   Map tab — the toolbox tools.

   Only one of them carries real logic: the distance measurement. The view is
   flat Cartesian UTM37N METRES, so a distance on this map is a plain
   Euclidean hypotenuse — no geodesic maths, no projection correction, and the
   number is exact rather than "about right at this latitude". That is the
   whole reason the engine keeps UTM metres instead of reprojecting to lat/lon.
   ========================================================================= */

export var TOOL_POINTER = 'pointer';
export var TOOL_MEASURE = 'measure';

// Metres below a kilometre, kilometres with two decimals above it. The
// threshold is on the RAW value: 999.6 m is still under a kilometre and reads
// as metres (rounded), which keeps the unit switch predictable while dragging.
export function formatDistance(metres) {
  var value = Number(metres);
  if (!isFinite(value)) return '—';
  if (Math.abs(value) < 1000) return Math.round(value) + ' m';
  return (value / 1000).toFixed(2) + ' km';
}

export function segmentLength(a, b) {
  return Math.hypot(b[0] - a[0], b[1] - a[1]);
}

// Per-segment lengths of a vertex chain: n points -> n-1 lengths.
export function segmentLengths(points) {
  var out = [];
  for (var i = 1; i < (points || []).length; i += 1) out.push(segmentLength(points[i - 1], points[i]));
  return out;
}

// Cumulative length at each vertex: n points -> n values, the first always 0.
export function cumulativeLengths(points) {
  var out = [];
  var running = 0;
  for (var i = 0; i < (points || []).length; i += 1) {
    if (i > 0) running += segmentLength(points[i - 1], points[i]);
    out.push(running);
  }
  return out;
}

export function totalDistance(points) {
  var cumulative = cumulativeLengths(points);
  return cumulative.length ? cumulative[cumulative.length - 1] : 0;
}

/* The measurement's own state machine.

   drafting  -> vertices are being added, `cursor` rubber-bands to the pointer
   finished  -> the chain stays drawn (double-click / Escape / the Pointer
                tool) until Clear erases it
   empty     -> nothing drawn

   Switching back to the Pointer tool deliberately FINISHES rather than
   clears: the measurement is the answer the user asked for, so it survives
   until they say otherwise. */
export class MeasureTool {
  constructor() {
    this.points = [];
    this.cursor = null;   // [x, y] in world metres while drafting
    this.drafting = false;
  }

  get isEmpty() { return this.points.length === 0; }
  get isFinished() { return this.points.length > 0 && !this.drafting; }

  // A fresh click after a finished measurement starts a new one — the old
  // chain is replaced, not extended across a gap.
  addPoint(x, y) {
    if (!this.drafting) { this.points = []; this.drafting = true; }
    this.points.push([x, y]);
    this.cursor = [x, y];
    return this.points.length;
  }

  moveCursor(x, y) { if (this.drafting) this.cursor = [x, y]; }

  // Finishing with a single vertex leaves nothing measurable, so it clears.
  finish() {
    this.drafting = false;
    this.cursor = null;
    if (this.points.length < 2) this.points = [];
    return this.points.length;
  }

  clear() {
    this.points = [];
    this.cursor = null;
    this.drafting = false;
  }

  // The chain to draw right now: the committed vertices plus the rubber-band
  // vertex under the cursor while drafting.
  livePoints() {
    if (this.drafting && this.cursor && this.points.length) return this.points.concat([this.cursor]);
    return this.points.slice();
  }

  total() { return totalDistance(this.livePoints()); }
}
