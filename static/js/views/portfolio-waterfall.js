/* =========================================================================
   Portfolio Analysis — the waterfall diagram tile.

   ONE image for the whole portfolio, uploaded by a user, so the tile is both
   the viewer and the drop zone rather than a picture with a separate upload
   screen somewhere else. Three states, and the tile never leaves them:

     empty    dashed drop zone; the whole tile is the control (click or drop)
     filled   the image, click to enlarge, Replace/Remove in the header
     busy     the previous state, dimmed, with the control inert

   The image is served from /api/portfolio/waterfall, which 404s when nothing
   has been uploaded. That 404 is the NORMAL empty case, not an error: the tile
   asks for the image and treats a failure to load as "there isn't one", so no
   extra request is needed to find out which state to render.
   ========================================================================= */
import { byId, all, esc, msg } from '../dom.js';
import { ICONS } from '../icons.js';
import { API } from '../api.js';

var WATERFALL_URL = '/api/portfolio/waterfall';

// The stored image is replaced in place, so the browser would happily show the
// old one from cache after an upload. Every render gets a fresh cache-buster.
var cacheBust = 0;

// Which render the tile is currently showing. An <img> load is asynchronous,
// so a re-render (an upload landing, a removal) leaves the PREVIOUS image
// still loading -- and when that one fails, its error handler would happily
// wipe the good image that replaced it. Each render stamps its own token and
// the handlers stand down unless they are still the current one.
var renderToken = 0;

function imageUrl() { return WATERFALL_URL + '?v=' + cacheBust; }

function tile() { return byId('portfolio-waterfall'); }

function setBusy(busy) {
  var host = tile();
  if (host) host.classList.toggle('is-busy', !!busy);
}

function renderActions(present) {
  var actions = byId('portfolio-waterfall-actions');
  if (!actions) return;
  actions.innerHTML = present
    ? '<button type="button" id="waterfall-replace" class="ghost">Replace</button>' +
      '<button type="button" id="waterfall-remove" class="ghost">Remove</button>'
    : '';
  var replace = byId('waterfall-replace');
  if (replace) replace.addEventListener('click', function (event) {
    event.stopPropagation();
    pickFile();
  });
  var remove = byId('waterfall-remove');
  if (remove) remove.addEventListener('click', function (event) {
    event.stopPropagation();
    removeImage();
  });
}

function renderEmpty() {
  var body = byId('portfolio-waterfall-body');
  if (!body) return;
  renderToken += 1;   // any image still loading is now stale
  renderActions(false);
  body.innerHTML =
    '<button type="button" id="waterfall-drop" class="waterfall-drop">' +
      '<span class="waterfall-drop-glyph" aria-hidden="true">' + ICONS['chart-scatter'] + '</span>' +
      '<span class="waterfall-drop-lead">Drop a waterfall diagram, or click to upload</span>' +
      '<span class="waterfall-drop-hint">PNG, JPEG or SVG · up to 5 MB · shared with everyone</span>' +
    '</button>';
  byId('waterfall-drop').addEventListener('click', pickFile);
}

function renderImage() {
  var body = byId('portfolio-waterfall-body');
  if (!body) return;
  var token = ++renderToken;
  body.innerHTML = '<button type="button" id="waterfall-open" class="waterfall-figure" ' +
    'title="Click to enlarge"><img id="waterfall-image" alt="Portfolio waterfall diagram"></button>';
  var image = byId('waterfall-image');
  // Resolve the state from the LOAD, not from a separate existence check: the
  // request that decides is the one that would fetch the picture anyway.
  image.addEventListener('load', function () {
    if (token === renderToken) renderActions(true);
  });
  image.addEventListener('error', function () {
    if (token === renderToken) renderEmpty();
  });
  image.src = imageUrl();
  byId('waterfall-open').addEventListener('click', openLightbox);
}

// Reuses the app's existing image lightbox (#ra-plot-lightbox), the same one
// the Lead Assessment plots enlarge into -- one enlarge behaviour everywhere.
function openLightbox() {
  var dialog = byId('ra-plot-lightbox');
  var image = byId('ra-lightbox-img');
  if (!dialog || !image) return;
  image.src = imageUrl();
  image.alt = 'Portfolio waterfall diagram';
  if (!dialog.open) dialog.showModal();
}

function pickFile() {
  var input = byId('portfolio-waterfall-file');
  if (input) input.click();
}

function upload(file) {
  if (!file) return;
  setBusy(true);
  var form = new FormData();
  form.append('file', file);
  API.uploadPortfolioWaterfall(form).then(function () {
    cacheBust += 1;
    renderImage();
    msg('Waterfall diagram updated.', 'success');
  }).catch(function (error) {
    // The server's message names the actual problem (wrong type, too large),
    // so it is shown verbatim rather than replaced with a generic failure.
    msg(error.message, 'error');
  }).finally(function () { setBusy(false); });
}

function removeImage() {
  setBusy(true);
  API.deletePortfolioWaterfall().then(function () {
    cacheBust += 1;
    renderEmpty();
    msg('Waterfall diagram removed.', 'success');
  }).catch(function (error) {
    msg(error.message, 'error');
  }).finally(function () { setBusy(false); });
}

/* One-time wiring. The drag handlers sit on the TILE rather than on the empty
   state's button so a drop can also replace an existing image, and every one
   preventDefaults -- without that the browser navigates away to the dropped
   file, which loses whatever the user was doing. */
export function initPortfolioWaterfall() {
  var host = tile();
  if (!host) return false;

  var input = byId('portfolio-waterfall-file');
  if (input) input.addEventListener('change', function () {
    upload(input.files && input.files[0]);
    // Clear it, or choosing the same file twice in a row fires no change.
    input.value = '';
  });

  ['dragenter', 'dragover'].forEach(function (name) {
    host.addEventListener(name, function (event) {
      event.preventDefault();
      host.classList.add('is-dragover');
    });
  });
  ['dragleave', 'dragend'].forEach(function (name) {
    host.addEventListener(name, function (event) {
      event.preventDefault();
      if (event.target === host || !host.contains(event.relatedTarget)) {
        host.classList.remove('is-dragover');
      }
    });
  });
  host.addEventListener('drop', function (event) {
    event.preventDefault();
    host.classList.remove('is-dragover');
    var files = event.dataTransfer && event.dataTransfer.files;
    upload(files && files[0]);
  });

  renderImage();
  return true;
}

// Exported for the tests and for a caller that wants to re-check the tile
// after something else replaced the image.
export function refreshPortfolioWaterfall() {
  cacheBust += 1;
  renderImage();
}
