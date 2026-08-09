import { test, assert, fixture } from './harness.js';
import { ICONS, ICON_ALIASES, LUCIDE_VERSION } from '../js/icons.js';
import { hydrateStaticIcons } from '../js/ui/static-icons.js';

var REQUIRED = [
  'workflow', 'briefcase', 'target', 'map-pin', 'shield-check', 'calculator',
  'clipboard-check', 'clipboard-list', 'gauge', 'drill', 'trending-up', 'star',
  'dices', 'search', 'dog', 'maximize-2', 'arrow-left-right', 'arrow-right',
  'chevron-right'
];

test('icons: the application exposes the pinned official Lucide set', function () {
  assert.equal(LUCIDE_VERSION, '1.27.0');
  REQUIRED.forEach(function (key) {
    assert.ok(ICONS[key], key + ' is available');
    assert.match(ICONS[key], new RegExp('class="lucide lucide-' + key + '"'));
    assert.match(ICONS[key], /stroke="currentColor"/);
    assert.match(ICONS[key], /aria-hidden="true"/);
    assert.match(ICONS[key], /focusable="false"/);
  });
});

test('icons: documented compatibility aliases reuse canonical markup', function () {
  Object.keys(ICON_ALIASES).forEach(function (alias) {
    assert.equal(ICONS[alias], ICONS[ICON_ALIASES[alias]], alias);
  });
});

test('icons: static shell placeholders hydrate without duplicating sources', function () {
  var root = fixture(
    '<span data-lucide-icon="workflow"></span>' +
    '<span data-lucide-icon="settings"></span>' +
    '<span data-lucide-icon="missing"></span>');
  hydrateStaticIcons(root);
  assert.ok(root.children[0].querySelector('.lucide-workflow'));
  assert.ok(root.children[1].querySelector('.lucide-settings'));
  assert.equal(root.children[2].innerHTML, '');
});
