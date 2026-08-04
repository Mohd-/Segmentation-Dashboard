/* Card 4B -- the CONSOLIDATED STAKING LETTERS page.
 *
 * Pre-Well Delivery still has FOUR tracked items. Two of them -- "Approval to
 * Stake" and "Well Site Location" -- are not two pieces of work, they are two
 * letters in one staking package, and a geologist files them in a single
 * sitting. So they keep their two rail rows, two statuses and two dots, and
 * lose their two FORMS: clicking either row opens this one page, whose three
 * checkboxes read in the order the work actually happens.
 *
 *   1. the well record and its folder exist          -> Approval to Stake
 *   2. the Approval to Stake letter is filed         -> Approval to Stake
 *   3. the Wellsite Location letter is filed         -> Well Site Location
 *      ...and ticking 3 REVEALS the staked location  -> Well Site Location
 *
 * Checkbox 1 is the v5 backfill (STAKING_WELL_CREATED_KEY): the retired "Well
 * Creation" step's sign-off became this box, and the migration ticked it for
 * every project whose Well Creation row had been Approved. It is a
 * PREREQUISITE recorded on the page, never a fifth tracked item -- the step is
 * retired and nothing here resurrects it.
 *
 * THE SAME SHAPE AS CARD 2B (views/lead-assessment.js), deliberately scoped
 * smaller: it mounts into the shared detail shell's #dynamic-fields body,
 * REUSES the shell's comments textarea / folder slot / Save button, groups its
 * values back onto their owning tasks and PATCHes only the dirty ones. What it
 * does NOT inherit is that page's calculator, auto-run and debounce: this page
 * computes nothing.
 *
 * WHAT IS PURE. The rules -- which task owns which key, when the location
 * section shows, what a coordinate has to be, how the page's values group back
 * onto two tasks -- are pure exported functions tested without a DOM
 * (static/tests/test-staking-letters.js).
 */
import { byId, all, esc, isFilled, truthy, msg } from '../dom.js';
import { API } from '../api.js';
import { Store, currentUserName, isCurrentPipelineView } from '../state.js';
import { refreshAfterRecordChange } from './detail.js';

// ---------------------------------------------------------------------------
// The contract: steps, keys, and which task owns each key
// ---------------------------------------------------------------------------

// The two tracked items this page consolidates, in rail order. Either one opens
// the page; neither opens a form of its own any more.
export var STAKING_LETTER_STEPS = ['Approval to Stake', 'Well Site Location'];

// The step whose task carries the page's ONE comments box and its folder row
// (the shared folder the two letters are filed in -- config.COMPONENT_FILE_
// SECTIONS already lists this step, so the row is the step's own, not a new
// invention).
export var PRIMARY_STEP = 'Approval to Stake';

// field key -> the task that owns it. The user sees one page and presses one
// button; every value still lands on the tracked item whose completion rule
// reads it (workflow/constants.py FIELD_COMPLETION). Keys are the EAV contract
// -- never rename one, a rename orphans stored data, and the first of them is
// data migration v5 already wrote.
export var KEY_OWNER = {
  staking_well_created: 'Approval to Stake',
  approval_stake_letter_loaded: 'Approval to Stake',
  wellsite_letter_loaded: 'Well Site Location',
  staked_x: 'Well Site Location',
  staked_y: 'Well Site Location',
  // Item B: the staked well's name, captured alongside the coordinates and
  // OWNED BY THE SAME TASK ROW as staked_x/staked_y so the three staking
  // readings travel together. Free text, and it gates NOTHING: the server's
  // FIELD_COMPLETION for Well Site Location does not read it, and no client
  // rule below does either.
  staked_well_name: 'Well Site Location'
};

// The three confirmations, IN PROCESS ORDER, with the card's exact wording.
// Order is the whole point: you cannot file a letter about a well that has not
// been created, and you cannot record where a rig was staked before the letter
// that stakes it exists.
export var CHECKBOXES = [
  { key: 'staking_well_created',
    label: 'Well creation and well folder are completed' },
  { key: 'approval_stake_letter_loaded',
    label: 'The Approval to Stake letter is placed in the shared folder' },
  { key: 'wellsite_letter_loaded',
    label: 'The Wellsite Location letter is placed in the shared folder' }
];

// Ticking the LAST checkbox reveals the staking location. Named once, so the
// reveal rule and the markup can never disagree about which box drives it.
export var REVEAL_KEY = 'wellsite_letter_loaded';

export var LOCATION_HEADING = 'Staking Location';

// The revealed pair. The label IS the placeholder (the mockup's light-gray
// ghost text): the two boxes are self-describing and the row has no caption.
export var LOCATION_FIELDS = [
  { key: 'staked_x', label: 'Staked X Coordinate' },
  { key: 'staked_y', label: 'Staked Y Coordinate' }
];

// Item B: the well-name input rendered alongside the coordinate pair, under
// the same reveal. Deliberately NOT in LOCATION_FIELDS -- that list feeds the
// numeric coordinate validator, and a name is free text with no rule at all.
export var WELL_NAME_FIELD = { key: 'staked_well_name', label: 'Well Name' };

export var LABELS = {
  staked_x: 'Staked X Coordinate',
  staked_y: 'Staked Y Coordinate',
  staked_well_name: 'Well Name'
};

export var MESSAGES = {
  // A coordinate that is present but not a number. NO positivity and NO
  // magnitude rule: a UTM easting is a six/seven-digit reading and the server's
  // NUMERIC_FIELDS validator says the same thing (workflow/constants.py).
  coordinate: function (label) { return label + ' must be numeric.'; }
};

export var COMMENTS_PLACEHOLDER = 'Comments, assumptions, rationale, or required notes...';

// ---------------------------------------------------------------------------
// Pure: the reveal
// ---------------------------------------------------------------------------

// Does the staking location show, given the page's current values? Purely the
// third checkbox -- nothing about whether coordinates happen to be stored.
//
// THE HIDE IS COSMETIC, NEVER DESTRUCTIVE: unticking the box hides the two
// inputs but the inputs STAY IN THE DOM carrying their values, so
// readFormValues still harvests them and buildSavePlan still writes them back
// unchanged. A user who unticks the box, saves, and ticks it again finds the
// coordinates they entered. (Reopening the item is the correct consequence of
// unticking; losing the survey is not.)
export function locationRevealed(values) {
  return truthy((values || {})[REVEAL_KEY]);
}

// ---------------------------------------------------------------------------
// Pure: validation
// ---------------------------------------------------------------------------

// A blank coordinate is NEVER an error -- it is an INCOMPLETE item, which the
// server's FIELD_COMPLETION already expresses by leaving Well Site Location
// open. Only a value the user actually typed is checked.
export function coordinateError(key, raw) {
  if (!isFilled(raw)) return null;
  return isNaN(Number(raw)) ? MESSAGES.coordinate(LABELS[key] || key) : null;
}

export function validateStakingLetters(values) {
  values = values || {};
  var errors = {};
  LOCATION_FIELDS.forEach(function (field) {
    var error = coordinateError(field.key, values[field.key]);
    if (error) errors[field.key] = error;
  });
  return errors;
}

// The first error in reading order -- what a blocked Save toasts.
export function firstError(errors) {
  errors = errors || {};
  for (var i = 0; i < LOCATION_FIELDS.length; i += 1) {
    if (errors[LOCATION_FIELDS[i].key]) return errors[LOCATION_FIELDS[i].key];
  }
  return null;
}

// ---------------------------------------------------------------------------
// Pure: the batched save plan
// ---------------------------------------------------------------------------

// The page's values -> [{ taskName, fields }] for the tasks whose stored values
// actually CHANGED, in rail order. One PATCH per dirty task (each carries its
// own revision and optimistic lock), never a blanket two-task write: an
// untouched step must not collect a history entry, and must not 409 on a
// revision somebody else legitimately moved.
//
// `saved` is the {taskName: {key: value}} map of what the server currently
// holds (Store.allFields). A task appears when ANY of its keys differs, and
// then carries ALL of its keys -- the whole-task payload is what makes the
// write idempotent and readable in the audit trail. Identical rule to card 2B's
// buildSavePlan; the two pages are deliberately the same machine.
export function buildSavePlan(values, saved) {
  values = values || {};
  saved = saved || {};
  var byTask = {};
  Object.keys(KEY_OWNER).forEach(function (key) {
    var taskName = KEY_OWNER[key];
    if (!byTask[taskName]) byTask[taskName] = {};
    byTask[taskName][key] = values[key] == null ? '' : String(values[key]);
  });
  return STAKING_LETTER_STEPS.filter(function (taskName) {
    var fields = byTask[taskName] || {};
    var stored = saved[taskName] || {};
    return Object.keys(fields).some(function (key) {
      return String(stored[key] == null ? '' : stored[key]) !== fields[key];
    });
  }).map(function (taskName) {
    return { taskName: taskName, fields: byTask[taskName] };
  });
}

// The NON-primary step's comments, when it has any. COMMENTS DECISION (stated,
// because the card asks): both tasks keep their own comments columns --
// provenance is not something a layout change gets to delete -- but the page
// surfaces ONE editable box, bound to Approval to Stake (the item the page's
// first two confirmations belong to, and the one the Portfolio reads). Whatever
// Well Site Location already carries is shown READ-ONLY in a small fold,
// attributed to the step that recorded it, and only when there is something to
// show. Exactly card 2B's rule, with one earlier step instead of three.
export function earlierComments(tasks) {
  return (tasks || []).filter(function (task) {
    return task && task.task_name !== PRIMARY_STEP &&
      STAKING_LETTER_STEPS.indexOf(task.task_name) >= 0 && isFilled(task.comments);
  }).sort(function (a, b) {
    return STAKING_LETTER_STEPS.indexOf(a.task_name) - STAKING_LETTER_STEPS.indexOf(b.task_name);
  }).map(function (task) {
    return { step: task.task_name, comments: String(task.comments) };
  });
}

// ---------------------------------------------------------------------------
// Markup
// ---------------------------------------------------------------------------

function checkboxMarkup(entry, values) {
  return '<label class="check-label sl-check"><input type="checkbox" data-sl-field="' + esc(entry.key) + '"' +
    (truthy(values[entry.key]) ? ' checked' : '') + '> ' + esc(entry.label) + '</label>';
}

function coordinateMarkup(field, values) {
  var value = values[field.key];
  return '<div class="sl-cell">' +
    '<input type="number" step="any" data-sl-field="' + esc(field.key) + '"' +
    ' value="' + esc(value == null ? '' : value) + '"' +
    ' placeholder="' + esc(field.label) + '" aria-label="' + esc(field.label) + '">' +
    '<span class="sl-field-error" data-error-for="' + esc(field.key) + '" role="alert"></span>' +
    '</div>';
}

// Item B: the free-text Well Name cell. Same self-describing treatment as the
// coordinates (the label IS the placeholder and the aria-label); no error slot
// because no rule can reject a name.
function wellNameMarkup(values) {
  var value = values[WELL_NAME_FIELD.key];
  return '<div class="sl-cell">' +
    '<input type="text" data-sl-field="' + esc(WELL_NAME_FIELD.key) + '"' +
    ' value="' + esc(value == null ? '' : value) + '"' +
    ' placeholder="' + esc(WELL_NAME_FIELD.label) + '" aria-label="' + esc(WELL_NAME_FIELD.label) + '"' +
    ' autocomplete="off" spellcheck="false">' +
    '</div>';
}

// The revealed section. Rendered ALWAYS and merely hidden when the box is
// unticked -- see locationRevealed for why the inputs must survive in the DOM.
// It is nested inside the checkbox list, indented under the third box, because
// it is that box's consequence rather than a fourth item. Reading order: the
// well is NAMED, then WHERE it was staked.
export function locationSectionMarkup(values) {
  var hidden = !locationRevealed(values);
  return '<div class="sl-location' + (hidden ? ' hidden' : '') + '" data-sl-section="location"' +
    ' aria-hidden="' + (hidden ? 'true' : 'false') + '">' +
    '<div class="sl-location-heading">' + esc(LOCATION_HEADING) + '</div>' +
    '<div class="sl-location-row">' +
    wellNameMarkup(values) +
    LOCATION_FIELDS.map(function (field) { return coordinateMarkup(field, values); }).join('') +
    '</div></div>';
}

// The read-only provenance fold (see earlierComments). Rendered only when the
// other step actually carries comments -- an always-present empty fold would be
// furniture.
export function earlierCommentsMarkup(entries) {
  if (!entries || !entries.length) return '';
  return '<details class="sl-earlier"><summary>Earlier step comments</summary>' +
    entries.map(function (entry) {
      return '<div class="sl-earlier-entry"><b>' + esc(entry.step) + '</b>' +
        '<p>' + esc(entry.comments) + '</p></div>';
    }).join('') + '</details>';
}

export function workspaceMarkup(state) {
  var values = state.values || {};
  return '<div class="sl-workspace">' +
    '<section class="sl-card" data-sl-section="letters">' +
    CHECKBOXES.map(function (entry) {
      return checkboxMarkup(entry, values) +
        (entry.key === REVEAL_KEY ? locationSectionMarkup(values) : '');
    }).join('') +
    '</section>' +
    earlierCommentsMarkup(state.earlier) +
    '</div>';
}

// ---------------------------------------------------------------------------
// Render generation + wiring
// ---------------------------------------------------------------------------
// Same guard idiom as views/lead-assessment.js: `state` is the module-level
// "is this page mounted" flag, and tearing down nulls it so a stray async
// handler that resolves after the user navigated away is a safe no-op.

var state = null;

export function teardownStakingLetters() { state = null; }

export function isStakingLetterStep(taskName) {
  return STAKING_LETTER_STEPS.indexOf(taskName) >= 0;
}

// Is the consolidated page the thing currently mounted? detail-form.js's save
// handler asks before doing anything else.
export function stakingLettersActive() { return !!state; }

function taskNamed(name) {
  return (Store.tasks || []).find(function (task) { return task.task_name === name; }) || null;
}

// Every stored value the page edits, resolved from the per-task field map.
function readStoredValues(allFields) {
  var values = {};
  Object.keys(KEY_OWNER).forEach(function (key) {
    var stored = (allFields[KEY_OWNER[key]] || {})[key];
    values[key] = stored == null ? '' : String(stored);
  });
  return values;
}

// The live values as typed. Checkboxes normalize to the same '1'/'' the rest of
// the app stores. Reads HIDDEN inputs too -- that is what preserves the staked
// coordinates across an untick (see locationRevealed).
export function readFormValues(root) {
  var values = {};
  all('[data-sl-field]', root || document).forEach(function (element) {
    var key = element.getAttribute('data-sl-field');
    values[key] = element.type === 'checkbox' ? (element.checked ? '1' : '') : element.value;
  });
  return values;
}

function renderErrors(errors) {
  errors = errors || {};
  all('.sl-field-error').forEach(function (slot) {
    var key = slot.getAttribute('data-error-for');
    var message = errors[key] || '';
    slot.textContent = message;
    slot.classList.toggle('is-shown', !!message);
    var input = document.querySelector('[data-sl-field="' + key + '"]');
    if (input) input.classList.toggle('sl-invalid', !!message);
  });
}

// Show/hide the location section in place. The inputs are never rebuilt and
// never cleared -- only the wrapper's `hidden` class moves.
function syncReveal(root, values) {
  var section = (root || document).querySelector('[data-sl-section="location"]');
  if (!section) return;
  var revealed = locationRevealed(values);
  section.classList.toggle('hidden', !revealed);
  section.setAttribute('aria-hidden', String(!revealed));
}

function onFieldInput(root) {
  if (!state) return;
  state.values = readFormValues(root);
  syncReveal(root, state.values);
  state.errors = validateStakingLetters(state.values);
  renderErrors(state.errors);
}

function wireInputs(root) {
  all('[data-sl-field]', root).forEach(function (element) {
    if (element.dataset.slBound) return;
    element.dataset.slBound = 'true';
    element.addEventListener('input', function () { onFieldInput(root); });
    element.addEventListener('change', function () { onFieldInput(root); });
  });
}

// ---------------------------------------------------------------------------
// Folder row
// ---------------------------------------------------------------------------

// The Approval to Stake step's OWN component folder row -- the shared folder
// both letters are filed in. Same endpoint, same markup and same slot the
// generic per-step form uses (detail-form.js renderComponentFolder); it is
// rendered here only because the consolidated page skips that form's per-step
// fetch entirely.
function renderFolderRow(task, onCopy) {
  var previous = byId('component-folder-card');
  if (previous) previous.remove();
  var anchor = byId('comments-field');
  if (!anchor || !task) return;
  var card = document.createElement('div');
  card.id = 'component-folder-card';
  card.className = 'folder-card';
  card.innerHTML = '<span class="folder-glyph" aria-hidden="true">📁</span>' +
    '<span class="folder-path" id="sl-folder-path">Loading…</span>' +
    '<button type="button" class="icon-btn" id="copy-component-folder" title="Copy folder link" aria-label="Copy folder link" disabled>⧉</button>';
  anchor.parentNode.insertBefore(card, anchor.nextSibling);
  var forProjectId = Store.projectId;
  API.componentFolder(forProjectId, task.task_id).then(function (info) {
    if (Store.projectId !== forProjectId) return;
    var pathElement = byId('sl-folder-path');
    if (!pathElement) return;
    if (!info || !Number(info.requires_folder)) {
      var existing = byId('component-folder-card');
      if (existing) existing.remove();
      return;
    }
    var path = info.unc_path || '';
    var button = byId('copy-component-folder');
    pathElement.textContent = path || 'Folder path placeholder not configured.';
    pathElement.title = path;
    if (button && path) {
      button.disabled = false;
      button.addEventListener('click', function () { onCopy(path); });
    }
  }).catch(function () {
    if (Store.projectId !== forProjectId) return;
    var pathElement = byId('sl-folder-path');
    if (pathElement) pathElement.textContent = 'Folder link unavailable.';
  });
}

// ---------------------------------------------------------------------------
// Mount
// ---------------------------------------------------------------------------

// Render the whole page into the shell's #dynamic-fields body. `task` is
// whichever of the two rail rows the user clicked -- it decides nothing about
// the layout, only which row stays highlighted. `onCopy` is detail-form.js's
// copyText (passed rather than imported, so this module does not take a
// dependency back on the form it mounts inside).
export function renderStakingLetters(root, options) {
  options = options || {};
  var allFields = Store.allFields || {};
  var primaryTask = taskNamed(PRIMARY_STEP);
  state = {
    projectId: Store.projectId,
    values: readStoredValues(allFields),
    errors: {},
    earlier: earlierComments(Store.tasks)
  };
  root.innerHTML = workspaceMarkup(state);
  wireInputs(root);
  state.errors = validateStakingLetters(state.values);
  renderErrors(state.errors);

  // The page's ONE comments box is the shell's own textarea, bound to the
  // Approval to Stake task (see earlierComments).
  var comments = byId('comments');
  if (comments) {
    comments.value = (primaryTask && primaryTask.comments) || '';
    comments.placeholder = COMMENTS_PLACEHOLDER;
  }
  renderFolderRow(primaryTask, options.onCopy || function () {});
}

// ---------------------------------------------------------------------------
// Save
// ---------------------------------------------------------------------------

// ONE save routine for the whole page (Item A: no button any more -- the
// auto-save controller invokes this through saveComponent's dispatch). It
// validates the coordinates, groups the values back onto their two owning
// tasks, and PATCHes only the dirty ones -- each with its OWN revision,
// sequentially, so the existing optimistic lock and its 409 toast keep working
// per task exactly as they do for a single-step form. TWO tracked outcomes
// from one save: the server's engine decides each item from the fields that
// item owns.
//
// Resolves the shared outcome shape ({ok, state, message} -- see
// detail-form.js saveComponent); `options.auto` suppresses every toast (the
// inline field errors and the save-state indicator speak instead).
export function saveStakingLetters(options) {
  options = options || {};
  var auto = !!options.auto;
  if (!state) return Promise.resolve({ ok: false, state: 'error', message: 'Staking Letters is not open.' });
  if (!isCurrentPipelineView()) {
    var pipelineMessage = 'Switch back to the current pipeline to save changes.';
    if (!auto) msg(pipelineMessage, 'error');
    return Promise.resolve({ ok: false, state: 'error', message: pipelineMessage });
  }
  var root = byId('dynamic-fields');
  var values = readFormValues(root);
  state.values = values;
  var errors = validateStakingLetters(values);
  state.errors = errors;
  renderErrors(errors);
  var blocking = firstError(errors);
  if (blocking) {
    if (!auto) msg(blocking, 'error');
    return Promise.resolve({ ok: false, state: 'invalid', message: blocking });
  }

  var plan = buildSavePlan(values, Store.allFields || {});
  var comments = byId('comments');
  var commentsValue = comments ? comments.value : '';
  var primaryTask = taskNamed(PRIMARY_STEP);
  var commentsChanged = !!primaryTask && String(primaryTask.comments || '') !== String(commentsValue);
  if (commentsChanged && !plan.some(function (entry) { return entry.taskName === PRIMARY_STEP; })) {
    plan.push({ taskName: PRIMARY_STEP, fields: {} });
  }
  if (!plan.length) {
    if (!auto) msg('No changes to save.', 'success');
    return Promise.resolve({ ok: true, state: 'nochange' });
  }

  var saveButton = byId('save-component');
  if (saveButton) saveButton.disabled = true;
  var chain = Promise.resolve();
  plan.forEach(function (entry) {
    chain = chain.then(function () {
      var task = taskNamed(entry.taskName);
      if (!task) throw new Error(entry.taskName + ' component not found.');
      // comments/priority are ECHOED per task: save_task clears an absent
      // comments key and defaults an absent priority to Medium, so a batched
      // write that omitted them would quietly wipe the other step's notes.
      return API.updateTask(task.task_id, {
        fields: entry.fields,
        comments: entry.taskName === PRIMARY_STEP ? commentsValue : (task.comments || ''),
        priority: task.priority || 'Medium',
        revision: task.revision,
        changed_by: currentUserName()
      }).catch(function (error) {
        throw new Error(entry.taskName + ': ' + error.message);
      });
    });
  });
  return chain.then(function () {
    // Auto saves refresh silently; the indicator says 'Saved'.
    return refreshAfterRecordChange(auto ? null : 'Staking letters saved.');
  }).then(function () {
    return { ok: true, state: 'saved' };
  }).catch(function (error) {
    if (!auto) msg(error.message, 'error');
    return { ok: false, state: 'error', message: error.message };
  }).finally(function () {
    var button = byId('save-component');
    if (button) button.disabled = false;
  });
}
