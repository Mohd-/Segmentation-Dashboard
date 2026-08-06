/* Card 4B — the CONSOLIDATED STAKING LETTERS page
   (static/js/views/staking-letters.js).

   Two tracked items, one page, one Save. The module follows card 2B's split:
   every RULE is a pure exported function (which task owns which key, when the
   staking location shows, what a coordinate has to be, how the page's values
   group back onto two tasks) and only the mount/save half touches Store or the
   network. The tests follow that split — most hand a plain object to a pure
   function; the last group mounts the page into a shell fixture. */
import { test, assert, fixture, mockFetch, waitFor } from './harness.js';
import { Store } from '../js/state.js';
import { SCHEMA } from '../js/schema.js';
import {
  STAKING_LETTER_STEPS, PRIMARY_STEP, KEY_OWNER, CHECKBOXES, REVEAL_KEY,
  LOCATION_FIELDS, LOCATION_HEADING, LABELS, MESSAGES,
  locationRevealed, coordinateError, validateStakingLetters, firstError,
  buildSavePlan, earlierComments,
  workspaceMarkup, locationSectionMarkup, earlierCommentsMarkup,
  isStakingLetterStep, stakingLettersActive, renderStakingLetters,
  teardownStakingLetters, readFormValues
} from '../js/views/staking-letters.js';

// The three confirmations' exact wording, as the card specifies them. Spelled
// out here rather than read back off CHECKBOXES so a silent edit to the module
// fails a test instead of quietly agreeing with itself.
var LABEL_1 = 'Well creation and well folder are completed';
// Card 3V's handover confirmation, worded EXACTLY as the card writes it and
// placed immediately after the well-creation control the card names.
var LABEL_HANDOVER = 'Lead Folder is moved to the Well Proposal Folder';
var LABEL_2 = 'The Approval to Stake letter is placed in the shared folder';
var LABEL_3 = 'The Wellsite Location letter is placed in the shared folder';

function values(overrides) {
  return Object.assign({
    staking_well_created: '',
    approval_stake_letter_loaded: '',
    wellsite_letter_loaded: '',
    staked_x: '',
    staked_y: '',
    staked_well_name: ''
  }, overrides || {});
}

function renderPage(vals, earlier) {
  return fixture(workspaceMarkup({ values: vals || values(), earlier: earlier || [] }));
}

function checkbox(root, key) { return root.querySelector('[data-sl-field="' + key + '"]'); }

/* -------------------------------------------------------------------------
   The storage contract — which task owns which key
   ------------------------------------------------------------------------- */

test('staking-letters: the two consolidated steps, in rail order', function () {
  assert.deepEqual(STAKING_LETTER_STEPS, ['Approval to Stake', 'Well Site Location']);
  STAKING_LETTER_STEPS.forEach(function (name) {
    assert.ok(isStakingLetterStep(name), name + ' opens the consolidated page');
  });
  // The two Pre-Well Delivery steps that are NOT on this page keep their own
  // forms — Moving Tolerance is card 4A's generic grid, and the GeoX
  // assessment hosts the resource calculator.
  ['Moving Tolerance', 'Pre-Drilling GeoX Assessment', 'Area Definition', 'Well Creation']
    .forEach(function (name) {
      assert.ok(!isStakingLetterStep(name), name + ' is not part of the Staking Letters page');
    });
});

test('staking-letters: every edited key names its owning task', function () {
  // The page is one workspace; the STORAGE is still two tracked items, each
  // completing on the keys it owns (workflow/constants.py FIELD_COMPLETION).
  assert.deepEqual(KEY_OWNER, {
    staking_well_created: 'Approval to Stake',
    lead_folder_handover_confirmed: 'Approval to Stake',
    approval_stake_letter_loaded: 'Approval to Stake',
    wellsite_letter_loaded: 'Well Site Location',
    staked_x: 'Well Site Location',
    staked_y: 'Well Site Location',
    staked_well_name: 'Well Site Location'
  });
  // Item B: the well name lives on the SAME task row as staked_x/staked_y, so
  // the three staking readings always travel in one PATCH.
  assert.equal(KEY_OWNER.staked_well_name, KEY_OWNER.staked_x);
  assert.equal(PRIMARY_STEP, 'Approval to Stake', 'the comments box binds to the first letter');
  // The v5 backfill key lands on Approval to Stake — that is where the
  // migration wrote it, and moving it would orphan every backfilled lead.
  assert.equal(KEY_OWNER.staking_well_created, 'Approval to Stake');
});

test('staking-letters: the page never resurrects the retired Well Creation step', function () {
  // Well creation is a PREREQUISITE recorded as checkbox 1, not a fifth
  // tracked item: no step of that name is claimed, rendered or saved.
  assert.ok(STAKING_LETTER_STEPS.indexOf('Well Creation') < 0);
  assert.ok(Object.keys(KEY_OWNER).every(function (key) {
    return KEY_OWNER[key] !== 'Well Creation';
  }));
  assert.ok(!('Well Creation' in SCHEMA), 'and it has no SCHEMA entry either');
});

/* -------------------------------------------------------------------------
   Rendering — the three confirmations, in process order
   ------------------------------------------------------------------------- */

test('staking-letters: four checkboxes render in PROCESS order with the exact labels', function () {
  var root = renderPage();
  var boxes = Array.prototype.slice.call(root.querySelectorAll('.sl-check'));
  assert.equal(boxes.length, 4, 'four confirmations, no more');
  // Card 3V's handover sits immediately after well creation -- you move the
  // folder once the well exists, and before its letters are filed.
  assert.deepEqual(boxes.map(function (label) { return label.textContent.trim(); }),
    [LABEL_1, LABEL_HANDOVER, LABEL_2, LABEL_3]);
  assert.deepEqual(boxes.map(function (label) {
    return label.querySelector('input').getAttribute('data-sl-field');
  }), ['staking_well_created', 'lead_folder_handover_confirmed',
       'approval_stake_letter_loaded', 'wellsite_letter_loaded']);
  assert.deepEqual(CHECKBOXES.map(function (entry) { return entry.label; }),
    [LABEL_1, LABEL_HANDOVER, LABEL_2, LABEL_3]);
});

test('staking-letters: the handover confirmation gates nothing', function () {
  // It records that a PERSON moved the folder. The application performs no
  // file operation for it, and FIELD_COMPLETION['Approval to Stake'] is
  // unchanged -- ticking it alone completes nothing.
  var plan = buildSavePlan(values({ lead_folder_handover_confirmed: '1' }), {});
  assert.deepEqual(plan.map(function (entry) { return entry.taskName; }), ['Approval to Stake']);
  assert.deepEqual(plan[0].fields,
    { staking_well_created: '', lead_folder_handover_confirmed: '1',
      approval_stake_letter_loaded: '' });
});

test('staking-letters: the wording is "Staking", never "Stacking"', function () {
  var text = workspaceMarkup({ values: values({ wellsite_letter_loaded: '1' }), earlier: [] });
  assert.ok(text.indexOf('Stacking') < 0, 'no "Stacking" anywhere on the page');
  assert.ok(text.indexOf(LOCATION_HEADING) >= 0);
  assert.equal(LOCATION_HEADING, 'Staking Location');
});

test('staking-letters: a v5-backfilled staking_well_created renders already ticked', function () {
  // A lead whose retired Well Creation step had been Approved arrives here with
  // migration v5's '1' already stored — the page must SHOW that, so the user's
  // only remaining job is the letter.
  var root = renderPage(values({ staking_well_created: '1' }));
  assert.equal(checkbox(root, 'staking_well_created').checked, true);
  assert.equal(checkbox(root, 'approval_stake_letter_loaded').checked, false,
    'and the letter box is still the user\'s to tick');
});

/* -------------------------------------------------------------------------
   Progressive disclosure
   ------------------------------------------------------------------------- */

test('staking-letters: the staking location is HIDDEN until the third box is ticked', function () {
  var root = renderPage();
  var section = root.querySelector('[data-sl-section="location"]');
  assert.ok(section, 'the section is in the DOM from the start');
  assert.ok(section.classList.contains('hidden'), 'but hidden');
  assert.equal(section.getAttribute('aria-hidden'), 'true');
  assert.equal(locationRevealed(values()), false);
});

test('staking-letters: ticking the third box reveals Staked X / Staked Y', function () {
  var root = renderPage(values({ wellsite_letter_loaded: '1' }));
  var section = root.querySelector('[data-sl-section="location"]');
  assert.ok(!section.classList.contains('hidden'));
  assert.equal(section.getAttribute('aria-hidden'), 'false');
  assert.equal(section.querySelector('.sl-location-heading').textContent, 'Staking Location');
  // The labels are the PLACEHOLDERS (the mockup's light-gray ghosts) and the
  // accessible names — the row carries no captions.
  var inputs = Array.prototype.slice.call(section.querySelectorAll('input'));
  assert.deepEqual(inputs.map(function (input) { return input.getAttribute('data-sl-field'); }),
    ['staked_well_name', 'staked_x', 'staked_y'],
    'Item B: the well is NAMED, then WHERE it was staked');
  assert.deepEqual(inputs.map(function (input) { return input.placeholder; }),
    ['Well Name', 'Staked X Coordinate', 'Staked Y Coordinate']);
  assert.deepEqual(inputs.map(function (input) { return input.getAttribute('aria-label'); }),
    ['Well Name', 'Staked X Coordinate', 'Staked Y Coordinate']);
  assert.equal(locationRevealed(values({ wellsite_letter_loaded: '1' })), true);
});

test('staking-letters: the reveal follows the BOX, not the stored coordinates', function () {
  // Coordinates stored under an unticked box (the user unticked and saved) stay
  // hidden: the box is the rule, and showing the fields anyway would make the
  // reveal read as "we found data" rather than "this letter is filed".
  assert.equal(locationRevealed(values({ staked_x: '532100', staked_y: '2895120' })), false);
  assert.equal(locationRevealed(values({ wellsite_letter_loaded: '1' })), true);
  assert.equal(REVEAL_KEY, 'wellsite_letter_loaded');
});

test('staking-letters: hiding the location NEVER clears the stored coordinates', function () {
  // THE PROMISE: the inputs stay in the DOM carrying their values, so
  // readFormValues still harvests them and the save writes them back
  // unchanged. Only the item reopens; the survey survives. Mounted (rather
  // than markup-only) because the reveal is a WIRED reaction to the box.
  var root = mountPage({
    'Well Site Location': { wellsite_letter_loaded: '1', staked_x: '532100.5', staked_y: '2895120.1' }
  }).root;
  var box = checkbox(root, 'wellsite_letter_loaded');
  box.checked = false;
  box.dispatchEvent(new Event('change', { bubbles: true }));

  var section = root.querySelector('[data-sl-section="location"]');
  assert.ok(section.classList.contains('hidden'), 'the section hides');
  assert.equal(checkbox(root, 'staked_x').value, '532100.5', 'the input keeps its value');
  assert.equal(checkbox(root, 'staked_y').value, '2895120.1');

  var live = readFormValues(root);
  assert.equal(live.staked_x, '532100.5', 'and the harvest still sees it');
  assert.equal(live.wellsite_letter_loaded, '');

  // The save plan therefore carries the coordinates back UNCHANGED alongside
  // the cleared box: only the confirmation moves.
  var plan = buildSavePlan(live, {
    'Well Site Location': { wellsite_letter_loaded: '1', staked_x: '532100.5', staked_y: '2895120.1' }
  });
  assert.deepEqual(plan.map(function (entry) { return entry.taskName; }), ['Well Site Location']);
  assert.deepEqual(plan[0].fields, {
    wellsite_letter_loaded: '', staked_x: '532100.5', staked_y: '2895120.1',
    staked_well_name: ''
  });
  teardownStakingLetters();
});

test('staking-letters: re-ticking the box brings the same values back', function () {
  var root = mountPage({
    'Well Site Location': { wellsite_letter_loaded: '1', staked_x: '532100.5', staked_y: '2895120.1' }
  }).root;
  var box = checkbox(root, 'wellsite_letter_loaded');
  box.checked = false;
  box.dispatchEvent(new Event('change', { bubbles: true }));
  box.checked = true;
  box.dispatchEvent(new Event('change', { bubbles: true }));
  var section = root.querySelector('[data-sl-section="location"]');
  assert.ok(!section.classList.contains('hidden'));
  assert.equal(checkbox(root, 'staked_x').value, '532100.5');
  assert.equal(checkbox(root, 'staked_y').value, '2895120.1');
  teardownStakingLetters();
});

/* -------------------------------------------------------------------------
   Validation
   ------------------------------------------------------------------------- */

test('staking-letters: a blank coordinate is INCOMPLETE, not invalid', function () {
  assert.deepEqual(validateStakingLetters(values()), {});
  assert.equal(coordinateError('staked_x', ''), null);
  assert.equal(firstError({}), null);
});

test('staking-letters: a coordinate must parse as a number — and nothing more', function () {
  assert.equal(coordinateError('staked_x', 'TBD'), MESSAGES.coordinate(LABELS.staked_x));
  assert.equal(coordinateError('staked_x', '532100.5'), null);
  // NO positivity and NO magnitude rule: a UTM easting is seven digits and a
  // zero/negative reading is a number like any other.
  assert.equal(coordinateError('staked_y', '0'), null);
  assert.equal(coordinateError('staked_y', '-120'), null);
  assert.equal(coordinateError('staked_y', '2895120.1'), null);
});

test('staking-letters: errors read in field order and name the field', function () {
  var errors = validateStakingLetters(values({ staked_x: 'x', staked_y: 'y' }));
  assert.deepEqual(Object.keys(errors).sort(), ['staked_x', 'staked_y']);
  assert.equal(firstError(errors), 'Staked X Coordinate must be numeric.');
  assert.deepEqual(LOCATION_FIELDS.map(function (field) { return field.key; }),
    ['staked_x', 'staked_y']);
});

/* -------------------------------------------------------------------------
   The batched save plan — TWO tracked outcomes from ONE page
   ------------------------------------------------------------------------- */

test('staking-letters: only the tasks whose values CHANGED are in the plan', function () {
  var saved = {
    'Approval to Stake': { staking_well_created: '1', approval_stake_letter_loaded: '1' },
    'Well Site Location': { wellsite_letter_loaded: '', staked_x: '', staked_y: '' }
  };
  // Nothing typed: nothing to write.
  assert.deepEqual(buildSavePlan(values({
    staking_well_created: '1', approval_stake_letter_loaded: '1'
  }), saved), []);
  // Only the third box moves -> only Well Site Location is PATCHed, so
  // Approval to Stake collects no history entry and cannot 409.
  var plan = buildSavePlan(values({
    staking_well_created: '1', approval_stake_letter_loaded: '1', wellsite_letter_loaded: '1'
  }), saved);
  assert.deepEqual(plan.map(function (entry) { return entry.taskName; }), ['Well Site Location']);
});

test('staking-letters: the plan groups by owning task, in rail order, whole-task payloads', function () {
  var plan = buildSavePlan(values({
    staking_well_created: '1',
    approval_stake_letter_loaded: '1',
    wellsite_letter_loaded: '1',
    staked_x: '532100.5',
    staked_y: '2895120.1'
  }), {});
  assert.deepEqual(plan.map(function (entry) { return entry.taskName; }),
    ['Approval to Stake', 'Well Site Location']);
  assert.deepEqual(plan[0].fields,
    { staking_well_created: '1', lead_folder_handover_confirmed: '',
      approval_stake_letter_loaded: '1' });
  assert.deepEqual(plan[1].fields,
    { wellsite_letter_loaded: '1', staked_x: '532100.5', staked_y: '2895120.1',
      staked_well_name: '' });
});

test('staking-letters: boxes 1+2 alone complete ONE item and leave the other alone', function () {
  // The server decides completion, but the CLIENT decides what each PATCH
  // carries — and this is the shape that turns exactly one dot green.
  var plan = buildSavePlan(values({
    staking_well_created: '1', approval_stake_letter_loaded: '1'
  }), {});
  assert.deepEqual(plan.map(function (entry) { return entry.taskName; }), ['Approval to Stake']);
  assert.deepEqual(plan[0].fields,
    { staking_well_created: '1', lead_folder_handover_confirmed: '',
      approval_stake_letter_loaded: '1' });
});

/* -------------------------------------------------------------------------
   Comments provenance
   ------------------------------------------------------------------------- */

test('staking-letters: Well Site Location comments are kept, attributed, read-only', function () {
  var entries = earlierComments([
    { task_name: 'Approval to Stake', comments: 'this one is editable' },
    { task_name: 'Well Site Location', comments: 'staked 40 m north of plan' },
    { task_name: 'Moving Tolerance', comments: 'not on this page' }
  ]);
  assert.deepEqual(entries, [{ step: 'Well Site Location', comments: 'staked 40 m north of plan' }]);
  var root = fixture(earlierCommentsMarkup(entries));
  assert.equal(root.querySelectorAll('.sl-earlier-entry').length, 1);
  assert.equal(root.querySelector('.sl-earlier-entry b').textContent, 'Well Site Location');
  assert.ok(root.querySelector('.sl-earlier-entry p').textContent.indexOf('40 m north') >= 0);
  assert.equal(root.querySelectorAll('textarea, input').length, 0, 'and it is not editable');
});

test('staking-letters: the provenance fold renders only when there is something to show', function () {
  assert.deepEqual(earlierComments([{ task_name: 'Well Site Location', comments: '   ' }]), []);
  assert.equal(earlierCommentsMarkup([]), '');
  assert.equal(renderPage().querySelectorAll('.sl-earlier').length, 0);
});

/* -------------------------------------------------------------------------
   Mounted into the shell
   ------------------------------------------------------------------------- */

// The detail shell's own nodes, copied by id from static/index.html — the page
// mounts into #dynamic-fields and REUSES the comments box, the folder slot and
// the Save button rather than rendering its own.
function shellFixture() {
  return fixture(
    '<form id="component-form">' +
    '<div id="dynamic-fields" class="dynamic-fields"></div>' +
    '<label id="comments-field">Comments<textarea id="comments"></textarea></label>' +
    '<div class="action-row"><button id="save-component" type="submit">Save Updates</button></div>' +
    '</form>');
}

function jsonResponse(body) {
  return {
    ok: true, status: 200,
    headers: { get: function () { return 'application/json'; } },
    json: function () { return Promise.resolve(body); },
    text: function () { return Promise.resolve(JSON.stringify(body)); }
  };
}

function mountPage(fields, taskOverrides) {
  var calls = [];
  mockFetch(function (url, options) {
    calls.push({ url: url, body: options && options.body ? JSON.parse(options.body) : null });
    if (url.indexOf('/component-folder/') >= 0) {
      return jsonResponse({ requires_folder: 1, unc_path: '\\\\share\\WWWW\\WWWW-44\\Approval_to_Stake' });
    }
    return jsonResponse({});
  });
  Store.projectId = 7;
  Store.pipeline = 'prospect';
  Store.project = { pipeline_type: 'prospect' };
  Store.tasks = STAKING_LETTER_STEPS.map(function (name, index) {
    return Object.assign({ task_id: 200 + index, task_name: name, comments: '',
                           priority: 'Medium', revision: 1,
                           stage_group: 'Pre-Well Delivery', status: 'In Progress' },
                         (taskOverrides || {})[name] || {});
  });
  Store.allFields = fields || {};
  var root = shellFixture();
  renderStakingLetters(root.querySelector('#dynamic-fields'), { onCopy: function () {} });
  return { root: root, calls: calls };
}

test('staking-letters: mounting reads every stored value out of the two tasks', function () {
  var mounted = mountPage({
    'Approval to Stake': { staking_well_created: '1', approval_stake_letter_loaded: '1' },
    'Well Site Location': { wellsite_letter_loaded: '1', staked_x: '532100.5', staked_y: '2895120.1' }
  });
  assert.equal(stakingLettersActive(), true);
  var root = mounted.root;
  assert.equal(checkbox(root, 'staking_well_created').checked, true);
  assert.equal(checkbox(root, 'approval_stake_letter_loaded').checked, true);
  assert.equal(checkbox(root, 'wellsite_letter_loaded').checked, true);
  assert.equal(checkbox(root, 'staked_x').value, '532100.5');
  assert.ok(!root.querySelector('[data-sl-section="location"]').classList.contains('hidden'),
    'a fully filled page opens with the location visible');
  teardownStakingLetters();
  assert.equal(stakingLettersActive(), false);
});

test('staking-letters: the page binds the shell comments box to Approval to Stake', function () {
  var mounted = mountPage({}, {
    'Approval to Stake': { comments: 'letter chased with the drilling team' },
    'Well Site Location': { comments: 'legacy note from the old form' }
  });
  assert.equal(document.getElementById('comments').value, 'letter chased with the drilling team');
  // ...and the other step's note is shown read-only rather than silently lost.
  var fold = mounted.root.querySelector('.sl-earlier-entry');
  assert.ok(fold, 'the provenance fold renders');
  assert.equal(fold.querySelector('b').textContent, 'Well Site Location');
  teardownStakingLetters();
});

test('staking-letters: the folder row resolves the Approval to Stake share', function () {
  var mounted = mountPage({});
  return waitFor(function () {
    var element = document.getElementById('sl-folder-path');
    return element && element.textContent.indexOf('Approval_to_Stake') >= 0;
  }).then(function () {
    var call = mounted.calls.filter(function (item) {
      return item.url.indexOf('/component-folder/') >= 0;
    })[0];
    // The PRIMARY step's own component folder (task 200), not a new endpoint.
    assert.match(call.url, /\/api\/projects\/7\/component-folder\/200(\?|$)/);
    assert.equal(document.getElementById('copy-component-folder').disabled, false,
      'and the copy button arms once a path resolves');
    teardownStakingLetters();
  });
});

test('staking-letters: the coordinate boxes are NUMERIC inputs — free text never gets in', function () {
  // Which is why coordinateError above reads as belt-and-braces: the browser
  // already refuses 'TBD' in a type=number box, so the rule that really
  // matters is the SERVER's (workflow/constants.py NUMERIC_FIELDS), and this
  // client twin is its mirror for a value that arrived some other way.
  var mounted = mountPage({ 'Well Site Location': { wellsite_letter_loaded: '1' } });
  var input = checkbox(mounted.root, 'staked_x');
  assert.equal(input.type, 'number');
  assert.equal(input.step, 'any', 'decimal UTM readings are allowed');
  input.value = 'TBD';
  assert.equal(input.value, '', 'the box simply refuses it');
  input.value = '532100.5';
  input.dispatchEvent(new Event('input', { bubbles: true }));
  var slot = mounted.root.querySelector('.sl-field-error[data-error-for="staked_x"]');
  assert.equal(slot.textContent, '', 'and a real coordinate raises nothing');
  assert.ok(!input.classList.contains('sl-invalid'));
  assert.equal(readFormValues(mounted.root).staked_x, '532100.5');
  teardownStakingLetters();
});

/* -------------------------------------------------------------------------
   Item B — the Well Name input
   ------------------------------------------------------------------------- */

test('staking-letters well name: a free-TEXT input under the same reveal as the coordinates', function () {
  var mounted = mountPage({ 'Well Site Location': { wellsite_letter_loaded: '1' } });
  var input = checkbox(mounted.root, 'staked_well_name');
  assert.ok(input, 'the Well Name input renders');
  assert.equal(input.type, 'text', 'free text — well names are not numbers');
  assert.equal(input.placeholder, 'Well Name');
  assert.equal(input.getAttribute('aria-label'), 'Well Name');
  assert.ok(input.closest('[data-sl-section="location"]'),
    'it lives inside the revealed staking-location section');
  // Same non-destructive hide as the coordinates: unticking the reveal keeps
  // the input (and its value) in the DOM.
  var box = checkbox(mounted.root, 'wellsite_letter_loaded');
  input.value = 'SARH-101';
  box.checked = false;
  box.dispatchEvent(new Event('change', { bubbles: true }));
  assert.ok(mounted.root.querySelector('[data-sl-section="location"]').classList.contains('hidden'));
  assert.equal(checkbox(mounted.root, 'staked_well_name').value, 'SARH-101');
  assert.equal(readFormValues(mounted.root).staked_well_name, 'SARH-101');
  teardownStakingLetters();
});

test('staking-letters well name: hydrates from the stored field and round-trips the save plan', function () {
  var mounted = mountPage({
    'Well Site Location': { wellsite_letter_loaded: '1', staked_x: '532100.5',
                            staked_y: '2895120.1', staked_well_name: 'SARH-101' }
  });
  assert.equal(checkbox(mounted.root, 'staked_well_name').value, 'SARH-101',
    'the stored name renders');
  // Renaming ONLY the well dirties exactly the owning task, and the plan
  // carries the coordinates back unchanged beside the new name.
  var live = readFormValues(mounted.root);
  live.staked_well_name = 'SARH-102';
  var plan = buildSavePlan(live, {
    'Well Site Location': { wellsite_letter_loaded: '1', staked_x: '532100.5',
                            staked_y: '2895120.1', staked_well_name: 'SARH-101' }
  });
  assert.deepEqual(plan.map(function (entry) { return entry.taskName; }), ['Well Site Location'],
    'Approval to Stake is untouched — no spurious PATCH, no spurious history');
  assert.deepEqual(plan[0].fields, {
    wellsite_letter_loaded: '1', staked_x: '532100.5', staked_y: '2895120.1',
    staked_well_name: 'SARH-102'
  });
  teardownStakingLetters();
});

test('staking-letters well name: gates NOTHING — no validation rule, no completion input', function () {
  // Any text is legal, including something that looks numeric or blank.
  ['SARH-101', 'TBD', '', '  ', '42'].forEach(function (name) {
    var errors = validateStakingLetters(values({ wellsite_letter_loaded: '1', staked_well_name: name }));
    assert.deepEqual(errors, {}, JSON.stringify(name) + ' raises nothing');
    assert.equal(firstError(errors), null);
  });
  // The validated coordinate list stays exactly the coordinate pair: the name
  // can never join a completion or validation predicate by accident.
  assert.deepEqual(LOCATION_FIELDS.map(function (field) { return field.key; }),
    ['staked_x', 'staked_y']);
});
