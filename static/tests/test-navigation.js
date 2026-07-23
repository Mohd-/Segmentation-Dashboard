// Tests for static/js/navigation.js. activateTab queries document-wide for
// `.tab` panels and `.tabs button` — the fixture supplies both (runner.html
// itself deliberately uses neither class).
import { test, assert, fixture } from './harness.js';
import { activateTab, scrollToTab } from '../js/navigation.js';

function tabFixture() {
  return fixture(
    '<nav class="tabs">' +
      '<button data-tab="prospect" class="active" aria-selected="true" type="button">Prospect</button>' +
      '<button data-tab="portfolio" aria-selected="false" type="button">Portfolio</button>' +
      '<button data-tab="bp" aria-selected="false" type="button">BP</button>' +
    '</nav>' +
    '<section id="tab-prospect" class="tab active"></section>' +
    '<section id="tab-portfolio" class="tab"></section>' +
    '<section id="tab-bp" class="tab"></section>'
  );
}

test('navigation.activateTab activates the matching panel and button', function () {
  var root = tabFixture();
  activateTab('portfolio');
  assert.ok(root.querySelector('#tab-portfolio').classList.contains('active'), 'target panel active');
  assert.ok(!root.querySelector('#tab-prospect').classList.contains('active'), 'previous panel deactivated');
  assert.ok(!root.querySelector('#tab-bp').classList.contains('active'));
  var buttons = root.querySelectorAll('.tabs button');
  assert.equal(buttons[0].classList.contains('active'), false);
  assert.equal(buttons[0].getAttribute('aria-selected'), 'false');
  assert.equal(buttons[1].classList.contains('active'), true);
  assert.equal(buttons[1].getAttribute('aria-selected'), 'true');
  assert.equal(buttons[2].getAttribute('aria-selected'), 'false');
});

test('navigation.activateTab is idempotent and switches back cleanly', function () {
  var root = tabFixture();
  activateTab('bp');
  activateTab('bp');
  activateTab('prospect');
  assert.ok(root.querySelector('#tab-prospect').classList.contains('active'));
  assert.ok(!root.querySelector('#tab-bp').classList.contains('active'));
  var active = root.querySelectorAll('.tabs button.active');
  assert.equal(active.length, 1, 'exactly one active button');
  assert.equal(active[0].getAttribute('data-tab'), 'prospect');
});

test('navigation.activateTab with an unknown name deactivates everything', function () {
  var root = tabFixture();
  activateTab('nope');
  assert.equal(root.querySelectorAll('.tab.active').length, 0);
  assert.equal(root.querySelectorAll('.tabs button.active').length, 0);
  var selected = Array.prototype.map.call(root.querySelectorAll('.tabs button'),
    function (b) { return b.getAttribute('aria-selected'); });
  assert.deepEqual(selected, ['false', 'false', 'false']);
});

test('navigation.scrollToTab tolerates a missing panel and scrolls an existing one', function () {
  scrollToTab('does-not-exist'); // must not throw
  var root = tabFixture();
  var panel = root.querySelector('#tab-bp');
  var called = null;
  panel.scrollIntoView = function (opts) { called = opts; }; // instance stub, fixture-local
  scrollToTab('bp');
  assert.ok(called, 'scrollIntoView invoked');
  assert.equal(called.behavior, 'smooth');
  assert.equal(called.block, 'start');
});
