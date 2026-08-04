/* WGS84 / UTM zone 37 inverse projection and display helpers.

   The canvas is deliberately kept in projected metres.  This module only
   translates the pointer readout to geographic coordinates; it does not
   change any map geometry or distance calculation.  The inverse transverse
   Mercator series is the standard WGS84 UTM expansion (zone 37 central
   meridian 39°E, k0 0.9996, false easting 500,000 m). */

var WGS84_A = 6378137;
var WGS84_F = 1 / 298.257223563;
var K0 = 0.9996;
var FALSE_EASTING = 500000;
var FALSE_NORTHING_SOUTH = 10000000;
var CENTRAL_MERIDIAN_RAD = 39 * Math.PI / 180;

function finiteNumber(value) {
  if (value === null || value === undefined || value === '') return null;
  var numeric = Number(value);
  return isFinite(numeric) ? numeric : null;
}

// `northernHemisphere` defaults to true because this application is UTM37N.
// The optional false branch makes the underlying inverse explicit and keeps
// hemisphere behaviour testable rather than baking a hidden northing rule in.
export function utm37ToWgs84(easting, northing, northernHemisphere) {
  var e = finiteNumber(easting);
  var n = finiteNumber(northing);
  if (e === null || n === null) return null;

  var north = northernHemisphere === undefined ? true : !!northernHemisphere;
  var y = north ? n : n - FALSE_NORTHING_SOUTH;
  var x = e - FALSE_EASTING;
  var e2 = WGS84_F * (2 - WGS84_F);
  var ep2 = e2 / (1 - e2);
  var sqrtOneMinusE2 = Math.sqrt(1 - e2);
  var e1 = (1 - sqrtOneMinusE2) / (1 + sqrtOneMinusE2);
  var m = y / K0;
  var mu = m / (WGS84_A * (1 - e2 / 4 - 3 * e2 * e2 / 64 - 5 * e2 * e2 * e2 / 256));

  var phi1 = mu
    + (3 * e1 / 2 - 27 * Math.pow(e1, 3) / 32) * Math.sin(2 * mu)
    + (21 * e1 * e1 / 16 - 55 * Math.pow(e1, 4) / 32) * Math.sin(4 * mu)
    + (151 * Math.pow(e1, 3) / 96) * Math.sin(6 * mu)
    + (1097 * Math.pow(e1, 4) / 512) * Math.sin(8 * mu);
  var sinPhi1 = Math.sin(phi1);
  var cosPhi1 = Math.cos(phi1);
  var tanPhi1 = Math.tan(phi1);
  var n1 = WGS84_A / Math.sqrt(1 - e2 * sinPhi1 * sinPhi1);
  var r1 = WGS84_A * (1 - e2) / Math.pow(1 - e2 * sinPhi1 * sinPhi1, 1.5);
  var t1 = tanPhi1 * tanPhi1;
  var c1 = ep2 * cosPhi1 * cosPhi1;
  var d = x / (n1 * K0);

  var latitude = phi1 - (n1 * tanPhi1 / r1) * (
    d * d / 2
    - (5 + 3 * t1 + 10 * c1 - 4 * c1 * c1 - 9 * ep2) * Math.pow(d, 4) / 24
    + (61 + 90 * t1 + 298 * c1 + 45 * t1 * t1 - 252 * ep2 - 3 * c1 * c1) * Math.pow(d, 6) / 720
  );
  var longitude = CENTRAL_MERIDIAN_RAD + (
    d
    - (1 + 2 * t1 + c1) * Math.pow(d, 3) / 6
    + (5 - 2 * c1 + 28 * t1 - 3 * c1 * c1 + 8 * ep2 + 24 * t1 * t1) * Math.pow(d, 5) / 120
  ) / cosPhi1;

  return { lon: longitude * 180 / Math.PI, lat: latitude * 180 / Math.PI };
}

export function formatLonLat(lon, lat, precision) {
  var x = finiteNumber(lon);
  var y = finiteNumber(lat);
  if (x === null || y === null) return '—';
  var digits = precision === undefined ? 5 : Math.max(0, Math.min(8, Number(precision) || 0));
  return Math.abs(x).toFixed(digits) + '°' + (x < 0 ? 'W' : 'E')
    + '  ' + Math.abs(y).toFixed(digits) + '°' + (y < 0 ? 'S' : 'N');
}

// One pure formatter owns the complete pointer text so the existing projected
// answer and its WGS84 companion can never drift apart in the DOM controller.
export function formatUtm37Coordinate(easting, northing, precision) {
  var e = finiteNumber(easting);
  var n = finiteNumber(northing);
  if (e === null || n === null) return 'E —   N —   (UTM37N m)   WGS84 —';
  var geographic = utm37ToWgs84(e, n, true);
  return 'E ' + e.toFixed(0) + '   N ' + n.toFixed(0) + '   (UTM37N m)   WGS84 '
    + formatLonLat(geographic.lon, geographic.lat, precision);
}
