/* =========================================================================
   Map tab — the three backend calls.

   They go through js/api.js's shared `api()` helper rather than a bare fetch,
   so they inherit the app's cache-busting, its JSON/error handling and its
   401 -> login-dialog -> retry-once behaviour. This module exists (instead of
   three more entries in the API object) purely to keep the map feature's
   surface inside static/js/map/.

   All coordinates are UTM Zone 37N metres. Nothing here reprojects.
   ========================================================================= */

import { api } from '../api.js';

// Layer metadata only — no geometry. The country-borders pseudo-layer comes
// first and is flagged is_borders.
export function fetchMapLayers() {
  return api('/api/map/layers').then(function (data) { return (data && data.layers) || []; });
}

// One layer's geometry. Fetched lazily, the first time a layer needs drawing.
export function fetchMapLayer(name) {
  return api('/api/map/layers/' + encodeURIComponent(name));
}

// Every project with a recorded coordinate, for the always-on-top overlay.
export function fetchMapWells() {
  return api('/api/map/wells').then(function (data) { return (data && data.wells) || []; });
}
