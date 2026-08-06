// Tests for static/js/views/portfolio-waterfall.js — the Portfolio Analysis
// waterfall tile: viewer and drop zone in one, with three states (empty,
// filled, busy). The image lives behind /api/portfolio/waterfall, which 404s
// when there is none — and that 404 IS the empty state, so the tile never
// makes a separate "does one exist?" request.
import { test, assert, fixture, mockFetch, waitFor } from './harness.js';
import { initPortfolioWaterfall, refreshPortfolioWaterfall } from '../js/views/portfolio-waterfall.js';

var TILE =
  '<div id="portfolio-waterfall" class="portfolio-tile portfolio-tile-waterfall">' +
    '<span class="portfolio-tile-head"><span class="portfolio-tile-title">Waterfall</span>' +
      '<span id="portfolio-waterfall-actions" class="portfolio-tile-actions"></span></span>' +
    '<div id="portfolio-waterfall-body" class="portfolio-tile-body"></div>' +
    '<input id="portfolio-waterfall-file" type="file" hidden>' +
  '</div>' +
  '<dialog id="ra-plot-lightbox"><img id="ra-lightbox-img" alt=""></dialog>';

// A 1x1 transparent GIF: small, and it actually LOADS in the browser, so the
// image's load/error events fire for real rather than being simulated.
var REAL_IMAGE = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';

function mountTile() {
  var host = fixture(TILE);
  initPortfolioWaterfall();
  return host;
}

/* The tile decides its state from whether the <img> LOADS. Rather than race a
   real network load (the mount's own request 404s against the test server,
   and whichever settles last would win), these tests re-render synchronously
   and DISPATCH the load event themselves: the wiring under test is "what does
   the tile do when the image loads", not the browser's fetching. The genuine
   load and error paths are covered by the drop-zone test above, which lets the
   real 404 drive it. */
function showLoadedImage(host) {
  refreshPortfolioWaterfall();
  var image = host.querySelector('#waterfall-image');
  image.src = REAL_IMAGE;
  image.dispatchEvent(new Event('load'));
  return host;
}

test('waterfall tile falls back to its drop zone when no image loads', function () {
  var host = mountTile();
  // The src it was given (the API path) 404s in the test page, so the error
  // path is the real one here.
  return waitFor(function () { return host.querySelector('.waterfall-drop'); }).then(function () {
    assert.match(host.querySelector('.waterfall-drop-lead').textContent, /Drop a waterfall diagram/);
    assert.match(host.querySelector('.waterfall-drop-hint').textContent, /PNG, JPEG or SVG/);
    assert.match(host.querySelector('.waterfall-drop-hint').textContent, /shared with everyone/);
    // Nothing to replace or remove yet.
    assert.equal(host.querySelectorAll('#portfolio-waterfall-actions button').length, 0);
  });
});

test('waterfall tile shows Replace and Remove once an image loads', function () {
  var host = mountTile();
  showLoadedImage(host);
  var labels = Array.prototype.map.call(
    host.querySelectorAll('#portfolio-waterfall-actions button'),
    function (button) { return button.textContent; });
  assert.deepEqual(labels, ['Replace', 'Remove']);
  assert.ok(host.querySelector('#waterfall-open'), 'the image itself is the enlarge control');
});

test('waterfall tile enlarges into the app lightbox', function () {
  var host = mountTile();
  showLoadedImage(host);
  var dialog = document.getElementById('ra-plot-lightbox');
  // Reuses the Lead Assessment plot lightbox, so enlarging behaves the same
  // way everywhere in the app.
  host.querySelector('#waterfall-open').click();
  assert.ok(dialog.open);
  assert.match(document.getElementById('ra-lightbox-img').src, /portfolio\/waterfall/);
  assert.equal(document.getElementById('ra-lightbox-img').alt, 'Portfolio waterfall diagram');
  dialog.close();
});

test('waterfall tile uploads a chosen file as multipart', function () {
  var host = mountTile();
  var seen = null;
  mockFetch(function (url, options) {
    seen = { url: String(url), method: options.method, body: options.body };
    return new Response(JSON.stringify({ present: true }), {
      status: 200, headers: { 'Content-Type': 'application/json' }
    });
  });
  var input = host.querySelector('#portfolio-waterfall-file');
  var file = new File([new Uint8Array([1, 2, 3])], 'waterfall.png', { type: 'image/png' });
  var transfer = new DataTransfer();
  transfer.items.add(file);
  input.files = transfer.files;
  input.dispatchEvent(new Event('change', { bubbles: true }));

  return waitFor(function () { return seen; }).then(function () {
    assert.match(seen.url, /^\/api\/portfolio\/waterfall\?/);
    assert.equal(seen.method, 'POST');
    // FormData, NOT a JSON body: the browser has to set the multipart
    // boundary itself, which it cannot do if we send a Content-Type.
    assert.ok(seen.body instanceof FormData);
    assert.equal(seen.body.get('file').name, 'waterfall.png');
    assert.equal(input.value, '', 'cleared, or choosing the same file twice fires no change');
  });
});

test('waterfall tile surfaces the server message when an upload is refused', function () {
  var host = mountTile();
  mockFetch(function () {
    return new Response(JSON.stringify({ detail: 'Upload a PNG, JPEG or SVG image.' }), {
      status: 400, headers: { 'Content-Type': 'application/json' }
    });
  });
  var input = host.querySelector('#portfolio-waterfall-file');
  var transfer = new DataTransfer();
  transfer.items.add(new File([new Uint8Array([9])], 'notes.txt', { type: 'text/plain' }));
  input.files = transfer.files;
  input.dispatchEvent(new Event('change', { bubbles: true }));

  return waitFor(function () {
    var toast = document.getElementById('app-message');
    return toast && /Upload a PNG/.test(toast.textContent);
  }).then(function () {
    // The server names the actual problem; it is shown verbatim rather than
    // replaced with a generic failure.
    assert.match(document.getElementById('app-message').textContent, /Upload a PNG, JPEG or SVG image\./);
    assert.equal(host.querySelector('#portfolio-waterfall').classList.contains('is-busy'), false,
      'the tile is interactive again after a failure');
  });
});

test('waterfall tile accepts a dropped file and never lets the browser navigate', function () {
  var host = mountTile();
  var seen = null;
  mockFetch(function (url, options) {
    seen = options.body;
    return new Response(JSON.stringify({ present: true }), {
      status: 200, headers: { 'Content-Type': 'application/json' }
    });
  });
  var tile = host.querySelector('#portfolio-waterfall');
  var transfer = new DataTransfer();
  transfer.items.add(new File([new Uint8Array([1])], 'dropped.png', { type: 'image/png' }));

  var over = new DragEvent('dragover', { bubbles: true, cancelable: true, dataTransfer: transfer });
  tile.dispatchEvent(over);
  assert.ok(over.defaultPrevented, 'without this the browser opens the file and loses the page');
  assert.ok(tile.classList.contains('is-dragover'), 'the target says it will accept the drop');

  var drop = new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: transfer });
  tile.dispatchEvent(drop);
  assert.ok(drop.defaultPrevented);
  assert.equal(tile.classList.contains('is-dragover'), false);
  return waitFor(function () { return seen; }).then(function () {
    assert.equal(seen.get('file').name, 'dropped.png');
  });
});

test('waterfall tile returns to its drop zone after a removal', function () {
  var host = mountTile();
  var method = null;
  mockFetch(function (url, options) {
    method = options.method;
    return new Response(JSON.stringify({ present: false }), {
      status: 200, headers: { 'Content-Type': 'application/json' }
    });
  });
  showLoadedImage(host);
  host.querySelector('#waterfall-remove').click();
  return waitFor(function () { return host.querySelector('.waterfall-drop'); }).then(function () {
    assert.equal(method, 'DELETE');
    assert.equal(host.querySelectorAll('#portfolio-waterfall-actions button').length, 0);
  });
});
