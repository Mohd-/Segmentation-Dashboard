// Tests for static/js/views/detail-form.js — the ACTION ROW.
//
// The row is three shared buttons plus the form's own Save, laid out from the
// task's status and the viewer's role. Card 3D gives one step its own layout
// (SPECIAL_ACTION_ROWS), and the promise that matters is negative: an employee
// must never be shown a control the backend will refuse. So these render the
// real row against the real markup and look at what is on screen.
import { test, assert, fixture } from './harness.js';
import { renderActionButtons, savedMessage, renderFields, getFields } from '../js/views/detail-form.js';
import { Store } from '../js/state.js';

// The action row copied verbatim from static/index.html (classes included --
// the override restyles them, and the reset has to put them back).
var ACTION_ROW =
  '<form id="component-form"><div class="action-row">' +
  '<button id="return-component" type="button" class="ghost hidden">Return for Update</button>' +
  '<button id="submit-component" type="button" class="hidden">Submit for Approval</button>' +
  '<button id="approve-component" type="button" class="hidden">Approve</button>' +
  '<button id="save-component" type="submit">Save Updates</button>' +
  '</div></form>';

// One action row per test (harness.fixture removes it afterwards) plus the
// Store state the row reads: who is looking, and at which pipeline.
function mount(role, name) {
  var host = fixture(ACTION_ROW);
  Store.user = { name: name || 'Employee', role: role };
  Store.project = { pipeline_type: 'prospect' };
  Store.pipeline = 'prospect';
  return host;
}

function button(id) { return document.getElementById(id); }
function visible(id) { return !button(id).classList.contains('hidden'); }

function task(name, status, assignee) {
  return { task_id: 7, task_name: name, status: status,
           assigned_to: assignee === undefined ? 'Employee' : assignee };
}

function withStore(fn) {
  var saved = { user: Store.user, project: Store.project, pipeline: Store.pipeline };
  try { fn(); } finally {
    Store.user = saved.user;
    Store.project = saved.project;
    Store.pipeline = saved.pipeline;
  }
}

// --- the generic row is untouched -------------------------------------------

test('detail-form action row: a normal step still shows Submit to its assignee', function () {
  withStore(function () {
    mount('employee', 'Employee');
    renderActionButtons(task('Area Definition', 'In Progress'));
    assert.equal(visible('submit-component'), true, 'the assignee may submit');
    assert.equal(visible('approve-component'), false);
    assert.equal(visible('return-component'), false);
    assert.equal(button('submit-component').textContent, 'Submit for Approval');
  });
});

test('detail-form action row: a normal Ready step shows Approve to a supervisor', function () {
  withStore(function () {
    mount('supervisor', 'Supervisor');
    renderActionButtons(task('Area Definition', 'Ready'));
    assert.equal(visible('approve-component'), true);
    assert.equal(visible('return-component'), true);
    assert.equal(button('approve-component').textContent, 'Approve');
    assert.equal(button('approve-component').disabled, false);
  });
});

// --- card 3D: Segmentation Slides -------------------------------------------

test('detail-form action row: an employee on Segmentation Slides sees Save alone', function () {
  withStore(function () {
    mount('employee', 'Employee');
    ['Not Assigned', 'In Progress', 'Ready', 'Approved'].forEach(function (status) {
      renderActionButtons(task('Segmentation Slides', status));
      assert.equal(visible('submit-component'), false,
        'no Submit button at ' + status + ' — saving the ticked box IS the submission');
      assert.equal(visible('approve-component'), false,
        'an employee is never offered Approve (' + status + ')');
      assert.equal(visible('return-component'), false,
        'an employee is never offered Return (' + status + ')');
    });
    assert.ok(button('save-component'), 'Save Updates is the whole row');
    assert.equal(button('save-component').classList.contains('hidden'), false);
  });
});

test('detail-form action row: staff are employees here too (approval is supervisor-only)', function () {
  withStore(function () {
    mount('staff', 'Staff Member');
    renderActionButtons(task('Segmentation Slides', 'Ready'));
    assert.equal(visible('approve-component'), false);
    assert.equal(visible('return-component'), false);
  });
});

test('detail-form action row: a supervisor gets Save, Approved and Return side by side', function () {
  withStore(function () {
    mount('supervisor', 'Supervisor');
    renderActionButtons(task('Segmentation Slides', 'Ready'));

    assert.equal(visible('approve-component'), true);
    assert.equal(visible('return-component'), true);
    assert.equal(visible('submit-component'), false, 'a supervisor does not submit for the employee');
    assert.equal(button('approve-component').textContent, 'Approved');
    assert.equal(button('return-component').textContent, 'Return');
    // Existing button vocabulary only (no new CSS): the constructive outline
    // used elsewhere for Promote, and the plain ghost the Return button
    // already carried.
    assert.equal(button('approve-component').className, 'ghost success-outline');
    assert.ok(button('return-component').classList.contains('ghost'));
    assert.equal(button('approve-component').disabled, false);
    assert.equal(button('return-component').disabled, false);
  });
});

test('detail-form action row: the supervisor controls are visible-but-disabled until submitted', function () {
  withStore(function () {
    mount('supervisor', 'Supervisor');
    ['Not Assigned', 'In Progress', 'Approved'].forEach(function (status) {
      renderActionButtons(task('Segmentation Slides', status));
      assert.equal(visible('approve-component'), true,
        'the review controls are the point of the page (' + status + ')');
      assert.equal(button('approve-component').disabled, true,
        'nothing to approve at ' + status);
      assert.equal(button('return-component').disabled, true,
        'nothing to return at ' + status);
      assert.match(button('approve-component').title, /submitted for review/);
    });
  });
});

test('detail-form action row: reference mode disables the supervisor controls', function () {
  withStore(function () {
    mount('supervisor', 'Supervisor');
    Store.pipeline = 'bp';   // looking at the other pipeline's structure
    renderActionButtons(task('Segmentation Slides', 'Ready'));
    assert.equal(button('approve-component').disabled, true);
    assert.equal(button('return-component').disabled, true);
  });
});

test('detail-form action row: the overridden buttons are restored for the next step', function () {
  withStore(function () {
    mount('supervisor', 'Supervisor');
    renderActionButtons(task('Segmentation Slides', 'Ready'));
    assert.equal(button('approve-component').textContent, 'Approved');

    renderActionButtons(task('Area Definition', 'Ready'));

    assert.equal(button('approve-component').textContent, 'Approve',
      'the shared node carries no trace of the previous step');
    assert.equal(button('approve-component').className, '',
      'and none of its styling');
    assert.equal(button('return-component').textContent, 'Return for Update');
    assert.equal(button('return-component').className, 'ghost');
    assert.equal(button('approve-component').disabled, false);
  });
});

// --- the toast that names the combined save+submit ---------------------------

test('detail-form savedMessage names the submission only when the save made one', function () {
  var slides = { task_name: 'Segmentation Slides', status: 'Ready' };
  assert.equal(savedMessage(slides, 'In Progress'), 'Component saved and submitted for approval.');
  assert.equal(savedMessage(slides, 'Ready'), 'Component saved.',
    're-saving a pending step submits nothing, so it says nothing');
  assert.equal(savedMessage({ task_name: 'Segmentation Slides', status: 'In Progress' }, 'In Progress'),
    'Component saved.', 'an unchecked draft save is just a save');
  assert.equal(savedMessage({ task_name: 'Area Definition', status: 'Ready' }, 'In Progress'),
    'Component saved.', 'no other step has a save-submits rule');
  assert.equal(savedMessage(null, 'In Progress'), 'Component saved.');
});

// --- Trap and Seal CoS: live calculation + manual override -------------------
//
// The merged step's CoS percentages are plain editable inputs computed LIVE
// (cos-rules.js) as their formula inputs change. The precedence promise:
// auto-calc overwrites the CoS field whenever an INPUT changes; a manually
// typed CoS persists until an input next changes. These render the real form
// via renderFields and drive it with real input events.

function renderTrapSealForm(values) {
  var host = fixture('<div class="dynamic-fields"></div>');
  // A no-op onInput keeps the default handler (which touches the right panel
  // via previewSummaryInputs) out of the fixture.
  renderFields('Trap and Seal CoS', values || {}, host.firstChild, function () {});
  return host.firstChild;
}

function fieldInput(root, key) {
  return root.querySelector('[data-field="' + key + '"]');
}

function type(element, value) {
  element.value = value;
  element.dispatchEvent(new Event('input', { bubbles: true }));
  element.dispatchEvent(new Event('change', { bubbles: true }));
}

function withAllFields(allFields, fn) {
  var saved = Store.allFields;
  Store.allFields = allFields;
  try { fn(); } finally { Store.allFields = saved; }
}

test('detail-form Trap/Seal live calc: both CoS fields render as editable inputs, not calculated outputs', function () {
  withAllFields({}, function () {
    var root = renderTrapSealForm({ trap_cos_pct: '80', seal_cos_pct: '48' });
    ['trap_cos_pct', 'seal_cos_pct'].forEach(function (key) {
      var input = fieldInput(root, key);
      assert.ok(input, key + ' carries data-field (harvested by getFields)');
      assert.equal(input.tagName, 'INPUT');
      assert.equal(input.type, 'number');
      assert.ok(!input.readOnly, key + ' is typeable');
    });
    assert.equal(root.textContent.indexOf('Calculated on save'), -1,
      'the "Calculated on save" empty state is gone from this page');
    var fields = getFields(root);
    assert.equal(fields.trap_cos_pct, '80', 'getFields harvests the CoS values');
    assert.equal(fields.seal_cos_pct, '48');
  });
});

test('detail-form Trap/Seal layout: the Trap input and its CoS share one .field-row', function () {
  withAllFields({}, function () {
    var root = renderTrapSealForm({});
    var thickness = fieldInput(root, 'sarah_quwarah_thickness_ft');
    var trap = fieldInput(root, 'trap_cos_pct');
    var row = thickness.closest('.field-row');
    assert.ok(row, 'the Trap pair renders inside a field-row');
    assert.ok(row.classList.contains('cols-2'), 'two equal columns');
    assert.equal(trap.closest('.field-row'), row, 'both fields share the SAME row');
  });
});

test('detail-form Trap live calc: typing the thickness computes Trap CoS from the cross-task Sarah thickness', function () {
  withAllFields({ 'Thickness Estimation': { formation_thickness_ft: '100' } }, function () {
    var root = renderTrapSealForm({});
    type(fieldInput(root, 'sarah_quwarah_thickness_ft'), '130');
    assert.equal(fieldInput(root, 'trap_cos_pct').value, '80', 'computed live, no save involved');
    type(fieldInput(root, 'sarah_quwarah_thickness_ft'), '314');
    assert.equal(fieldInput(root, 'trap_cos_pct').value, '100', 'recomputes on every change');
  });
});

test('detail-form Trap live calc: not-computable leaves the field untouched (no cross-task thickness)', function () {
  withAllFields({}, function () {
    var root = renderTrapSealForm({ trap_cos_pct: '55' });
    type(fieldInput(root, 'sarah_quwarah_thickness_ft'), '250');
    assert.equal(fieldInput(root, 'trap_cos_pct').value, '55',
      'null from calculateTrapCos means "leave it" -- same contract as the server hook');
  });
});

test('detail-form Trap manual override: a typed CoS persists until an input next changes', function () {
  withAllFields({ 'Thickness Estimation': { formation_thickness_ft: '100' } }, function () {
    var root = renderTrapSealForm({});
    type(fieldInput(root, 'sarah_quwarah_thickness_ft'), '130');
    assert.equal(fieldInput(root, 'trap_cos_pct').value, '80');
    // Manual overtype: nothing listens on the CoS field itself, so it stays.
    type(fieldInput(root, 'trap_cos_pct'), '42');
    assert.equal(fieldInput(root, 'trap_cos_pct').value, '42', 'the manual value stays put');
    assert.equal(getFields(root).trap_cos_pct, '42', 'and is what a save would send');
    // ... until an INPUT changes: auto-calc takes the field back.
    type(fieldInput(root, 'sarah_quwarah_thickness_ft'), '314');
    assert.equal(fieldInput(root, 'trap_cos_pct').value, '100', 'recompute overwrites the override');
  });
});

test('detail-form Seal live calc: the formula inputs drive Seal CoS; manual override follows the same rule', function () {
  withAllFields({}, function () {
    var root = renderTrapSealForm({});
    type(fieldInput(root, 'seal_recent_activity_age'), '0.95');
    assert.equal(fieldInput(root, 'seal_cos_pct').value, '',
      'a partial form computes nothing (null leaves the field)');
    type(fieldInput(root, 'seal_fracture_permeability'), '0.5');
    assert.equal(fieldInput(root, 'seal_cos_pct').value, '48', '0.95 x 0.5 -> 48, live');
    // Manual override survives non-input edits...
    type(fieldInput(root, 'seal_cos_pct'), '33');
    type(fieldInput(root, 'seal_pore_pressure_gradient_psi_ft'), '0.62');
    assert.equal(fieldInput(root, 'seal_cos_pct').value, '33',
      'the pore-pressure rider is not a formula input and must not clobber the override');
    // ... and falls to the next formula-input change.
    type(fieldInput(root, 'seal_fracture_permeability'), '0.6');
    assert.equal(fieldInput(root, 'seal_cos_pct').value, '57', '0.95 x 0.6 -> 57 overwrites');
  });
});

test('detail-form Seal live calc: clearing every input clears the CoS (the blank-form rule)', function () {
  withAllFields({}, function () {
    var root = renderTrapSealForm({
      seal_recent_activity_age: '0.95', seal_fracture_permeability: '0.5', seal_cos_pct: '48'
    });
    type(fieldInput(root, 'seal_recent_activity_age'), '');
    assert.equal(fieldInput(root, 'seal_cos_pct').value, '48',
      'a PARTIAL clear cannot compute -- the stored value holds');
    type(fieldInput(root, 'seal_fracture_permeability'), '');
    assert.equal(fieldInput(root, 'seal_cos_pct').value, '',
      'a wholly blank form clears the result, mirroring cos.py');
  });
});
