/* Hydrate the few icons that live in the static app shell. Dynamic views use
   ICONS directly; this keeps index.html on the same vendored source without
   pasting a second copy of every SVG path into the document. */
import { ICONS } from '../icons.js';

export function hydrateStaticIcons(root) {
  var scope = root || document;
  Array.prototype.forEach.call(scope.querySelectorAll('[data-lucide-icon]'), function (host) {
    var name = host.getAttribute('data-lucide-icon');
    host.innerHTML = ICONS[name] || '';
  });
}
