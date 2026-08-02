/* =========================================================================
   Card 1D — Add New Lead: interaction + validation

   The whole feature is ONE control that swaps between two states in place:

     collapsed   a single navy "+ Add New Lead" button
     expanded    exactly three inline fields — Lead Name, Lead X Coordinate,
                 Lead Y Coordinate — in that order, Lead Name focused

   Deliberately absent (the card forbids them): a modal, a route change, and
   Create / Cancel buttons. The keyboard IS the control surface:

     Enter (from any field)     create
     Escape / click outside     cancel — clears the three values, restores the
                                button, creates nothing, and leaves the board's
                                ACTIVE FILTERS untouched

   Validation is layered, not duplicated: these rules give the user an instant
   inline message, and workflow.add_project re-checks every one of them (name
   present / length / case-insensitive uniqueness, coordinates numeric) because
   the server is the authority. A server rejection is mapped back onto the field
   it belongs to, so a duplicate name reads the same whether the client or the
   database caught it.

   Nothing here talks to the board directly: a successful create refreshes
   through the Card 1C pipeline (refreshProspect -> setLeadRows), so the live
   filter selection decides whether the new lead is visible. A new lead that the
   current filters exclude is CORRECT and is not an error.
   ========================================================================= */
import { byId, msg } from '../dom.js';
import { API } from '../api.js';
import { currentUserName } from '../state.js';
import { openDetail } from './detail.js';
import { refreshAllBoards } from './pipeline.js';
import { leadRows } from './lead-filters.js';

// The three fields, IN THE ORDER THEY APPEAR and in the order validation walks
// them (so "focus the first invalid field" is just the first entry that failed).
export var LEAD_FIELDS = [
  { key: 'name', id: 'new-lead-name', errorId: 'new-lead-name-error', label: 'Lead Name' },
  { key: 'x', id: 'new-lead-x', errorId: 'new-lead-x-error', label: 'Lead X Coordinate' },
  { key: 'y', id: 'new-lead-y', errorId: 'new-lead-y-error', label: 'Lead Y Coordinate' }
];

// Messages the card pins verbatim. DUPLICATE_MESSAGE also has to match what
// workflow.add_project raises for a prospect, so the two paths read identically.
export var DUPLICATE_MESSAGE = 'A lead with this name already exists.';
export var NAME_REQUIRED_MESSAGE = 'Enter a Lead Name.';
export var NAME_TOO_LONG_MESSAGE = 'Lead Name must be 120 characters or less.';
export var X_MESSAGE = 'Enter a valid Lead X Coordinate.';
export var Y_MESSAGE = 'Enter a valid Lead Y Coordinate.';
var NETWORK_MESSAGE = 'Could not create the lead. Check your connection and try again.';
var HINT_IDLE = 'Press Enter to create';
var HINT_BUSY = 'Creating…';
var MAX_NAME_LENGTH = 120;

/* -------------------------------------------------------------------------
   Pure rules (exported so the tests can drive them without a DOM)
   ------------------------------------------------------------------------- */

// Trim only. Internal spaces and hyphens are part of the name ("Well Site A-2"
// is not "Well Site A-2" with the spaces squeezed out), so they survive
// untouched; a stray newline from a paste becomes a space rather than a hole.
export function normalizeLeadName(value) {
  return String(value == null ? '' : value).replace(/[\r\n\t]+/g, ' ').trim();
}

// Case- and surrounding-space-insensitive identity, matching the server's
// lower(trim(project_name)) comparison.
export function nameKey(value) {
  return normalizeLeadName(value).toLowerCase();
}

// A coordinate is a plain signed decimal (scientific notation included, since
// the app already accepts what type=number accepted). Everything else is
// rejected: letters, 'NaN'/'Infinity', thousands separators, two dots, a bare
// sign. NO positive-only rule — real coordinates are signed.
export function isValidCoordinate(value) {
  var text = String(value == null ? '' : value).trim();
  if (!text) return false;
  if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(text)) return false;
  return isFinite(Number(text));
}

/* Validate one submission. Returns [] when it may be sent, else a list of
   { key, message } IN FIELD ORDER. `existingNames` is the board's current
   lead names (the client-side duplicate pre-check); it is an optimization for
   the common case, never the authority — a name colliding with a BP well, or
   with a lead created by somebody else since the last refresh, is caught by the
   server and mapped back onto the same field. */
export function validateNewLead(values, existingNames) {
  var errors = [];
  var name = normalizeLeadName(values && values.name);
  if (!name) {
    errors.push({ key: 'name', message: NAME_REQUIRED_MESSAGE });
  } else if (name.length > MAX_NAME_LENGTH) {
    errors.push({ key: 'name', message: NAME_TOO_LONG_MESSAGE });
  } else if ((existingNames || []).some(function (existing) { return nameKey(existing) === nameKey(name); })) {
    errors.push({ key: 'name', message: DUPLICATE_MESSAGE });
  }
  if (!isValidCoordinate(values && values.x)) errors.push({ key: 'x', message: X_MESSAGE });
  if (!isValidCoordinate(values && values.y)) errors.push({ key: 'y', message: Y_MESSAGE });
  return errors;
}

// Which field a SERVER rejection belongs to, so it lands inline instead of only
// in a toast. Anything unrecognized (a 500, a folder failure, an offline fetch)
// is not field-specific and returns null -> toast.
export function fieldForServerError(message) {
  var text = String(message || '');
  if (/already exists/i.test(text)) return { key: 'name', message: DUPLICATE_MESSAGE };
  if (/Lead X Coordinate/i.test(text)) return { key: 'x', message: X_MESSAGE };
  if (/Lead Y Coordinate/i.test(text)) return { key: 'y', message: Y_MESSAGE };
  if (/name is required/i.test(text)) return { key: 'name', message: NAME_REQUIRED_MESSAGE };
  if (/120 characters/i.test(text)) return { key: 'name', message: NAME_TOO_LONG_MESSAGE };
  return null;
}

// Never surface a raw exception. A fetch that never reached the server has no
// user-facing detail to show, so it gets the connection wording instead.
export function friendlyErrorText(error) {
  var text = (error && error.message) || '';
  if (!text || /failed to fetch|networkerror|load failed|network request failed/i.test(text)) return NETWORK_MESSAGE;
  return text;
}

/* -------------------------------------------------------------------------
   The control
   ------------------------------------------------------------------------- */

// True while the POST is in flight. It is the ONE lock: it blocks a second
// Enter (a duplicate lead is unrecoverable), and it blocks cancel so a
// cancellation can never interrupt or duplicate a request already on the wire.
var pending = false;
var dismissWired = false;

function fieldInput(key) {
  var field = LEAD_FIELDS.filter(function (entry) { return entry.key === key; })[0];
  return field ? byId(field.id) : null;
}
function controls() { return byId('new-lead-controls'); }
function openButton() { return byId('new-lead-open'); }
function fieldsBox() { return byId('new-lead-fields'); }

export function newLeadValues() {
  return {
    name: (fieldInput('name') || {}).value || '',
    x: (fieldInput('x') || {}).value || '',
    y: (fieldInput('y') || {}).value || ''
  };
}

function setHint(text) {
  var hint = byId('new-lead-hint');
  if (hint) hint.textContent = text;
}

export function clearNewLeadErrors() {
  LEAD_FIELDS.forEach(function (field) {
    var input = byId(field.id);
    var error = byId(field.errorId);
    if (input) input.removeAttribute('aria-invalid');
    if (error) { error.textContent = ''; error.hidden = true; }
  });
  var panel = controls() && controls().closest ? controls().closest('.new-lead-panel') : null;
  if (panel) panel.classList.remove('has-error');
}

// Paint every error, then park focus on the FIRST invalid field. Entered values
// are never touched: a rejected submission must not cost the user their typing.
function showErrors(errors) {
  clearNewLeadErrors();
  if (!errors.length) return;
  errors.forEach(function (entry) {
    var field = LEAD_FIELDS.filter(function (candidate) { return candidate.key === entry.key; })[0];
    if (!field) return;
    var input = byId(field.id);
    var error = byId(field.errorId);
    if (input) input.setAttribute('aria-invalid', 'true');
    if (error) { error.textContent = entry.message; error.hidden = false; }
  });
  var panel = controls() && controls().closest ? controls().closest('.new-lead-panel') : null;
  if (panel) panel.classList.add('has-error');
  var firstInvalid = fieldInput(errors[0].key);
  if (firstInvalid) firstInvalid.focus();
}

function clearValues() {
  LEAD_FIELDS.forEach(function (field) {
    var input = byId(field.id);
    if (input) input.value = '';
  });
}

export function isNewLeadOpen() {
  var box = fieldsBox();
  return !!box && !box.classList.contains('hidden');
}

function setBusy(busy) {
  pending = busy;
  var box = fieldsBox();
  if (box) {
    box.classList.toggle('is-busy', busy);
    box.setAttribute('aria-busy', String(busy));
  }
  setHint(busy ? HINT_BUSY : HINT_IDLE);
}

/* Expand: the button GOES AWAY and the three fields take its place (the card
   asks for a swap, not a button plus a form). Lead Name takes focus. */
export function openNewLead() {
  if (pending || isNewLeadOpen()) return;
  var button = openButton();
  var box = fieldsBox();
  if (!button || !box) return;
  clearNewLeadErrors();
  setHint(HINT_IDLE);
  button.classList.add('hidden');
  button.setAttribute('aria-expanded', 'true');
  box.classList.remove('hidden');
  var name = fieldInput('name');
  if (name) name.focus();
}

/* Collapse. `restoreFocus` is false on the success path, where openDetail is
   about to move focus into the detail view anyway. Filters, the board and the
   fetched rowset are all untouched: cancelling is not a refresh. */
export function closeNewLead(restoreFocus) {
  var button = openButton();
  var box = fieldsBox();
  if (!button || !box) return;
  clearValues();
  clearNewLeadErrors();
  box.classList.add('hidden');
  button.classList.remove('hidden');
  button.setAttribute('aria-expanded', 'false');
  if (restoreFocus) button.focus();
}

export function cancelNewLead() {
  // A request already on the wire owns the control until it settles.
  if (pending || !isNewLeadOpen()) return;
  closeNewLead(true);
}

// The names currently on the board, for the duplicate pre-check. Reads the
// Card 1C module's UNFILTERED rowset, so a lead hidden by the active filters
// still blocks its own name.
function existingLeadNames() {
  return leadRows().map(function (row) { return row && row.project_name; })
    .filter(function (name) { return !!name; });
}

export function submitNewLead() {
  if (pending || !isNewLeadOpen()) return Promise.resolve(false);
  var values = newLeadValues();
  var errors = validateNewLead(values, existingLeadNames());
  if (errors.length) {
    showErrors(errors);
    return Promise.resolve(false);
  }
  clearNewLeadErrors();
  setBusy(true);
  var name = normalizeLeadName(values.name);
  return API.create({
    project_name: name,
    lead_x: String(values.x).trim(),
    lead_y: String(values.y).trim(),
    pipeline_type: 'prospect',
    changed_by: currentUserName()
  // Two-argument then, NOT .then().catch(): a fault in the success path (a
  // board re-render, the detail view) must never be reported to the user as a
  // failed creation -- the lead exists by then.
  }).then(function (result) {
    setBusy(false);
    closeNewLead(false);
    // Kept from the pre-1D control: other code may collapse UI on this event.
    document.dispatchEvent(new CustomEvent('lead:created', { detail: { project_id: result && result.project_id } }));
    msg('Lead created.', 'success');
    // Straight back through the Card 1C pipeline: refreshProspect -> setLeadRows
    // -> the live filter selection -> renderLeadBoard. The selection survives.
    refreshAllBoards();
    if (result && result.project_id) openDetail(result.project_id, 'prospect');
    return true;
  }, function (error) {
    // Recoverable by construction: the fields stay expanded, keep their values,
    // and Enter works again the moment setBusy(false) lands.
    setBusy(false);
    var text = friendlyErrorText(error);
    var field = fieldForServerError(text);
    if (field) showErrors([field]); else msg(text, 'error');
    return false;
  });
}

function onFieldKeydown(event) {
  // IME: a candidate-selecting Enter belongs to the input method, not to us.
  // keyCode 229 is the legacy signal browsers without isComposing still send.
  if (event.isComposing || event.keyCode === 229) return;
  if (event.key === 'Enter') {
    event.preventDefault();
    if (event.repeat) return;   // a held Enter is one intent, not many
    submitNewLead();
    return;
  }
  if (event.key === 'Escape' || event.key === 'Esc') {
    event.preventDefault();
    cancelNewLead();
  }
}

/* Boot entry point (main.js). Safe to call again — the tests re-mount the
   markup and re-init; the document-level dismiss listener is wired once and
   re-reads the live elements on every event. */
export function initLeadCreate() {
  var button = openButton();
  var box = fieldsBox();
  if (!button || !box) return;
  pending = false;
  button.addEventListener('click', function () { openNewLead(); });
  box.addEventListener('keydown', onFieldKeydown);
  closeNewLead(false);
  if (dismissWired) return;
  dismissWired = true;
  // mousedown, not click: the cancel has to happen before the click's focus
  // change, and a click that started inside and ended outside is not "outside".
  document.addEventListener('mousedown', function (event) {
    if (!isNewLeadOpen()) return;
    var root = controls();
    if (root && !root.contains(event.target)) cancelNewLead();
  });
}
