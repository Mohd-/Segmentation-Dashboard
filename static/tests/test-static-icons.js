import { test, assert, fixture } from './harness.js';
import { hydrateStaticIcons } from '../js/ui/static-icons.js';

test('static icon placeholders hydrate from the shared Lucide registry', function () {
  var root = fixture('<button><span data-lucide-icon="briefcase" aria-hidden="true"></span></button>');
  hydrateStaticIcons(root);
  assert.ok(root.querySelector('svg.lucide-briefcase'));
  assert.equal(root.querySelectorAll('[data-lucide-icon] svg').length, 1);
});

test('unknown static icon placeholders fail closed without text glyphs', function () {
  var root = fixture('<span data-lucide-icon="not-a-real-icon"></span>');
  hydrateStaticIcons(root);
  assert.equal(root.textContent, '');
  assert.equal(root.querySelector('svg'), null);
});
