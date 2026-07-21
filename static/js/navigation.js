import { byId, all } from './dom.js';

// Switch the visible top-level workspace without deciding what data to fetch
// or whether a detail/editor shell should close. main.js owns those policies;
// detail.js also uses this small DOM-only helper when a Portfolio row opens a
// record directly in its operating pipeline.
export function activateTab(name) {
  all('.tab').forEach(function (tab) { tab.classList.toggle('active', tab.id === 'tab-' + name); });
  all('.tabs button').forEach(function (button) {
    var isActive = button.getAttribute('data-tab') === name;
    button.classList.toggle('active', isActive);
    button.setAttribute('aria-selected', String(isActive));
  });
}

export function scrollToTab(name) {
  var tab = byId('tab-' + name);
  if (tab) tab.scrollIntoView({ behavior: 'smooth', block: 'start' });
}
