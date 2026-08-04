import { byId, all } from './dom.js';
import { currentProjectPipeline } from './state.js';

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

/* Leave a record's detail page and return to its own board.

   The ONE composite in this module, and a deliberate exception to the note
   above: it is the shared behavior of BOTH back controls in the detail shell —
   Card 2A's single outlined "Back to Segment Maturation" on a lead page
   (#back-to-board) and the BP well / reference-view shell's original
   "← Back to <pipeline>" (#back-to-overview). It lives here rather than in
   main.js so it can be regression-tested without booting the whole app.

   NAVIGATION ONLY. This is a single-page workspace, so "returning to the board"
   is re-activating its tab and hiding the detail shell: nothing is refetched,
   nothing is re-initialized, nothing is reset. That is precisely why a
   board -> detail -> Back round trip leaves the board exactly as it was — the
   Card 1C filter selection and its filtered rowset live in that module's own
   state and are never touched here, and the board DOM is never re-rendered, so
   the scroll position survives too. (Contrast main.js's showTab(), which DOES
   refresh: that is a deliberate tab switch, not a return.)

   Both controls are real <button>s, so Enter and Space activate them natively
   — there is no key handling to write. */
export function backToBoard() {
  var pipeline = currentProjectPipeline();
  activateTab(pipeline);
  var shell = byId('detail-shell');
  if (shell) shell.classList.add('hidden');
  scrollToTab(pipeline);
}
