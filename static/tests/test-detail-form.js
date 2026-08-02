// Tests for static/js/views/detail-form.js — the ACTION ROW.
//
// The row is three shared buttons plus the form's own Save, laid out from the
// task's status and the viewer's role. Card 3D gives one step its own layout
// (SPECIAL_ACTION_ROWS), and the promise that matters is negative: an employee
// must never be shown a control the backend will refuse. So these render the
// real row against the real markup and look at what is on screen.
import { test, assert, fixture } from './harness.js';
import { renderActionButtons, savedMessage } from '../js/views/detail-form.js';
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
