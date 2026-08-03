/* ITEM A -- the prospect-pipeline AUTO-SAVE controller.
 *
 * On the prospect maturation step pages (the generic dynamic-form editor, the
 * consolidated Lead Assessment workspace, the Staking Letters page) the Save
 * button is gone: edits persist themselves. One delegated listener on the
 * static #component-form schedules the EXISTING save routine (detail-form.js's
 * saveComponent, which still dispatches to saveLeadAssessment /
 * saveStakingLetters exactly as the button did) after AUTOSAVE_DEBOUNCE_MS of
 * quiet; Enter (outside a textarea) saves immediately. BP well pages keep
 * their explicit Save button and never enter this controller
 * (autoSaveEligible below).
 *
 * THE TWO INVARIANTS THIS MODULE OWNS:
 *
 * 1. SERIALIZATION. Never two in-flight task PATCHes for the same record: the
 *    optimistic revision lock would 409 the second one. While a save is in
 *    flight, at most ONE trailing save is queued (`queued`); it fires only
 *    after the first save's whole chain -- PATCH, /detail refresh, re-render
 *    -- has settled, so the trailing save reads FRESH revisions and the
 *    latest DOM values. A burst of edits therefore costs at most two writes.
 *
 * 2. QUIET FEEDBACK. Auto saves never toast: success, 'No changes to save.'
 *    and error toasts are all suppressed (the save fns take {auto: true} for
 *    exactly this), and the persistent #save-state indicator in the editor
 *    head carries the state instead -- 'Saving...', 'Saved', or the blocking
 *    message. Inline/strip validation errors keep rendering as before. The
 *    ONE exception lives in saveComponent itself: a save that doubled as a
 *    checkbox-submission (Segmentation Slides) still announces it, because a
 *    lifecycle change is not a routine save.
 *
 * FOCUS PRESERVATION. The save fns re-render the whole form after saving
 * (loadComponent / refreshAfterRecordChange), which would steal the focus and
 * clobber anything typed while the save was in flight. captureEditorFocus /
 * restoreEditorFocus -- called by those re-render sites, not from here -- take
 * a snapshot of the focused control (a stable data-*-field/id selector, its
 * CURRENT value, and the caret) immediately before the DOM is replaced and put
 * all three back once the fresh markup is in place. The captured value is
 * authoritative for the focused control: it is what the user has typed, and
 * any difference from the just-saved value is exactly what the queued trailing
 * save persists next.
 *
 * NAVIGATION. syncAutoSaveContext (called on every loadComponent) resets the
 * controller ONLY when the mounted task actually changed: the post-save reload
 * of the same task must not drop a queued trailing save, while moving to
 * another step must never let a stale timer save the wrong task. A pending
 * debounce is deliberately dropped on navigation -- same unsaved-edit loss the
 * old explicit-Save world had, minus the 800ms window.
 */
import { byId, msg } from '../dom.js';
import { Store, currentProjectPipeline, isCurrentPipelineView } from '../state.js';
// Runtime-only cycle (autosave -> detail-form -> autosave), same idiom as the
// existing detail <-> detail-form pair: hoisted function declarations, never
// called at module-eval time.
import { saveComponent } from './detail-form.js';

export var AUTOSAVE_DEBOUNCE_MS = 800;
var debounceMs = AUTOSAVE_DEBOUNCE_MS;

// Test hook: the suite shrinks the quiet window so a debounce assertion does
// not cost 800 real milliseconds. null restores the production value.
export function configureAutoSaveDelay(ms) {
  debounceMs = ms == null ? AUTOSAVE_DEBOUNCE_MS : ms;
}

var timer = null;
var inFlight = false;
var queued = false;
// Bumped on every context reset; async completions compare against it so a
// save that finishes after the user navigated away neither paints the
// indicator nor fires its trailing save at the wrong task.
var session = 0;
var boundProjectId = null;
var boundTaskId = null;

// Auto-save owns persistence ONLY on an editable prospect step page: the
// record's operating pipeline is prospect AND the user is looking at it (a
// reference view is read-only, a BP well keeps its Save button).
export function autoSaveEligible() {
  return !!Store.task && currentProjectPipeline() === 'prospect' && isCurrentPipelineView();
}

// ---------------------------------------------------------------------------
// The save-state indicator (#save-state in the editor head)
// ---------------------------------------------------------------------------

export function setSaveState(state, message) {
  var element = byId('save-state');
  if (!element) return;
  var text = '';
  if (state === 'saving') text = 'Saving…';
  else if (state === 'saved') text = 'Saved';
  else if (state === 'error') text = message || 'Not saved';
  element.textContent = text;
  element.className = 'save-state' + (state ? ' is-' + state : '');
}

export function resetAutoSave() {
  session += 1;
  if (timer) { clearTimeout(timer); timer = null; }
  queued = false;
  setSaveState('');
}

// Called by loadComponent on EVERY mount. Same project + same task is the
// post-save reload -- keep the queued trailing save and the indicator; a
// different task (or record) is navigation -- reset everything.
export function syncAutoSaveContext() {
  var taskId = Store.task ? Store.task.task_id : null;
  if (Store.projectId === boundProjectId && taskId === boundTaskId) return;
  boundProjectId = Store.projectId;
  boundTaskId = taskId;
  resetAutoSave();
}

// ---------------------------------------------------------------------------
// The serialized fire loop
// ---------------------------------------------------------------------------

// Outcome contract ({ok, state, message}) shared by all three save fns -- see
// saveComponent. 'nochange' is a success: everything on screen is stored.
function renderOutcome(outcome) {
  outcome = outcome || {};
  if (outcome.ok) setSaveState('saved');
  else setSaveState('error', outcome.message);
}

function fire() {
  timer = null;
  if (!autoSaveEligible()) { queued = false; return; }
  if (inFlight) { queued = true; return; }
  var mySession = session;
  inFlight = true;
  setSaveState('saving');
  Promise.resolve()
    .then(function () { return saveComponent(null, { auto: true }); })
    .then(function (outcome) {
      if (session === mySession) renderOutcome(outcome);
    }, function (error) {
      // The save fns resolve their own failures; this catch is the belt for a
      // synchronous throw. Never a toast on the auto path.
      if (session === mySession) setSaveState('error', error && error.message);
    })
    .then(function () {
      inFlight = false;
      // Exactly one trailing save: edits that arrived during the flight fire
      // once more, now against the refreshed revisions.
      if (queued) {
        queued = false;
        if (session === mySession && autoSaveEligible()) fire();
      }
    });
}

export function scheduleAutoSave() {
  if (timer) clearTimeout(timer);
  timer = setTimeout(fire, debounceMs);
}

export function flushAutoSave() {
  if (timer) { clearTimeout(timer); timer = null; }
  fire();
}

// ---------------------------------------------------------------------------
// Wiring -- one delegated set of listeners on the static #component-form
// ---------------------------------------------------------------------------

// Row-mutation buttons change the harvested data without dispatching an input
// event, so a click on one schedules a save too.
var ROW_ACTION_SELECTOR = '.add-repeatable-row, .remove-repeatable-row, ' +
  '.pay-interval-add, .pay-interval-remove, .formation-remove';

export function initAutoSave() {
  var form = byId('component-form');
  if (!form || form.dataset.autosaveBound) return;
  form.dataset.autosaveBound = 'true';
  function onEdit() {
    if (!autoSaveEligible()) return;
    scheduleAutoSave();
  }
  // Delegated, so the per-render field bindings need no knowledge of this
  // module and a re-rendered form stays wired. 'change' backs up 'input' for
  // selects/checkboxes; the debounce collapses the double fire.
  form.addEventListener('input', onEdit);
  form.addEventListener('change', onEdit);
  form.addEventListener('click', function (event) {
    if (!autoSaveEligible()) return;
    var target = event.target;
    if (target && target.closest && target.closest(ROW_ACTION_SELECTOR)) scheduleAutoSave();
  });
  form.addEventListener('keydown', function (event) {
    if (event.key !== 'Enter' || !autoSaveEligible()) return;
    var target = event.target;
    var tag = target && target.tagName;
    // Enter in a textarea is a newline; on a button it is a click. Everywhere
    // else it means "save now" -- and preventDefault stops the browser's
    // implicit form submission from double-firing the save.
    if (tag === 'TEXTAREA' || tag === 'BUTTON') return;
    event.preventDefault();
    flushAutoSave();
  });
}

// ---------------------------------------------------------------------------
// Focus preservation across the post-save re-render
// ---------------------------------------------------------------------------

// Every attribute that identifies a form control across a re-render. An
// element is addressed by ALL of the attributes it carries from this list, so
// a repeatable cell resolves to its exact row/column and a formation metric to
// its exact buffer row.
var FOCUS_KEY_ATTRS = [
  'data-field', 'data-la-field', 'data-sl-field',
  'data-repeatable-input', 'data-repeatable-row', 'data-repeatable-column',
  'data-formation-metric', 'data-formation-row',
  'data-pay-field', 'data-pay-row'
];

function attrEscape(value) {
  return String(value).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}

function focusSelector(element) {
  if (element.id) return '#' + element.id;
  var parts = [];
  FOCUS_KEY_ATTRS.forEach(function (attr) {
    if (element.hasAttribute(attr)) {
      parts.push('[' + attr + '="' + attrEscape(element.getAttribute(attr)) + '"]');
    }
  });
  if (!parts.length) return null;
  // Radios share one data-field per option; the value picks the option.
  if (element.type === 'radio') parts.push('[value="' + attrEscape(element.value) + '"]');
  return element.tagName.toLowerCase() + parts.join('');
}

// The selection API is only legal on these input types (plus textarea);
// calling setSelectionRange on a number input throws in Firefox.
var SELECTABLE_TYPES = { text: 1, search: 1, url: 1, tel: 1, password: 1 };

// Snapshot the focused editor control immediately BEFORE a re-render replaces
// it: a stable selector, the value as typed right now (which may already be
// newer than what was just saved), and the caret. null when focus is not on a
// form control inside #component-form.
export function captureEditorFocus() {
  var active = document.activeElement;
  var form = byId('component-form');
  if (!active || !form || !form.contains(active)) return null;
  var tag = active.tagName;
  if (tag !== 'INPUT' && tag !== 'TEXTAREA' && tag !== 'SELECT') return null;
  var selector = focusSelector(active);
  if (!selector) return null;
  var snapshot = { selector: selector };
  var type = tag === 'INPUT' ? active.type : '';
  if (tag === 'TEXTAREA' || (tag === 'INPUT' && type !== 'checkbox' && type !== 'radio')) {
    snapshot.value = active.value;
    if (tag === 'TEXTAREA' || SELECTABLE_TYPES[type]) {
      try {
        snapshot.selectionStart = active.selectionStart;
        snapshot.selectionEnd = active.selectionEnd;
      } catch (e) { /* selection unsupported here */ }
    }
  }
  return snapshot;
}

export function restoreEditorFocus(snapshot) {
  if (!snapshot) return;
  var form = byId('component-form');
  if (!form) return;
  // A focus the user deliberately moved OUTSIDE the form while the save was in
  // flight is theirs to keep -- only reclaim it from <body> (where an
  // innerHTML wipe drops it) or from within the form itself.
  var current = document.activeElement;
  if (current && current !== document.body && current !== document.documentElement &&
      !form.contains(current)) return;
  var element = form.querySelector(snapshot.selector);
  if (!element || element.disabled) return;
  // The captured value is what the user had typed; the re-render painted the
  // SAVED value. Put the typing back -- the trailing save persists it.
  if (snapshot.value !== undefined && element.value !== snapshot.value) {
    element.value = snapshot.value;
  }
  try { element.focus({ preventScroll: true }); } catch (e) { element.focus(); }
  if (snapshot.selectionStart != null) {
    try { element.setSelectionRange(snapshot.selectionStart, snapshot.selectionEnd); } catch (e) { /* number inputs */ }
  }
}
