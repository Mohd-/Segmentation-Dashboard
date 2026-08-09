// Tests for static/js/views/detail-form.js — the ACTION ROW.
//
// The row is three shared buttons plus the form's own Save, laid out from the
// task's status and the viewer's role. Card 3D gives one step its own layout
// (SPECIAL_ACTION_ROWS), and the promise that matters is negative: an employee
// must never be shown a control the backend will refuse. So these render the
// real row against the real markup and look at what is on screen.
import { test, assert, fixture, mockFetch } from './harness.js';
import {
  renderActionButtons, savedMessage, renderFields, getFields, loadComponent,
  stepHostsResourceCalculator, renderRepeatableField
} from '../js/views/detail-form.js';
import { Store } from '../js/state.js';
import { FLOWBACK_STAGE_COLUMNS } from '../js/schema.js';

// The action row copied verbatim from static/index.html (classes included --
// the override restyles them, and the reset has to put them back).
var ACTION_ROW =
  '<form id="component-form"><div class="action-row">' +
  '<button id="return-component" type="button" class="ghost hidden">Return for Update</button>' +
  '<button id="submit-component" type="button" class="hidden">Submit for Approval</button>' +
  '<button id="approve-component" type="button" class="hidden">Approve</button>' +
  '<button id="reopen-component" type="button" class="ghost hidden">Reopen</button>' +
  '<button id="save-component" type="submit">Save Updates</button>' +
  '</div></form>';

// One action row per test (harness.fixture removes it afterwards) plus the
// Store state the row reads: who is looking, and at which pipeline. `pipeline`
// defaults to prospect; the generic-lifecycle tests pass 'bp' because since
// Item A only BP step pages carry the Submit/Approve/Return row (and the Save
// button) at all.
function mount(role, name, pipeline) {
  var host = fixture(ACTION_ROW);
  Store.user = { name: name || 'Employee', role: role };
  Store.project = { pipeline_type: pipeline || 'prospect' };
  Store.pipeline = pipeline || 'prospect';
  return host;
}

function button(id) { return document.getElementById(id); }
function visible(id) { return !button(id).classList.contains('hidden'); }

function task(name, status, assignee, assignees) {
  var t = { task_id: 7, task_name: name, status: status,
            assigned_to: assignee === undefined ? 'Employee' : assignee };
  if (assignees) t.assignees = assignees;
  return t;
}

function withStore(fn) {
  var saved = { user: Store.user, project: Store.project, pipeline: Store.pipeline };
  try { fn(); } finally {
    Store.user = saved.user;
    Store.project = saved.project;
    Store.pipeline = saved.pipeline;
  }
}

// The shared detail shell's minimum real contract for loadComponent. These ids
// are intentionally the production ids: a navigation regression should fail
// here if step-owned DOM can outlive the step that owns it.
var DETAIL_SHELL =
  '<div id="detail-shell" class="detail-shell-lead">' +
  '<span id="component-number"></span><h2 id="component-title"></h2>' +
  '<span id="component-status-chip"></span><select id="assigned-to"></select>' +
  '<form id="component-form"><div id="dynamic-fields"></div>' +
  '<div id="comments-field"><textarea id="comments"></textarea></div>' +
  '<div class="action-row">' +
  '<button id="return-component" type="button" class="ghost hidden">Return for Update</button>' +
  '<button id="submit-component" type="button" class="hidden">Submit for Approval</button>' +
  '<button id="approve-component" type="button" class="hidden">Approve</button>' +
  '<button id="reopen-component" type="button" class="ghost hidden">Reopen</button>' +
  '<button id="save-component" type="submit">Save Updates</button>' +
  '</div></form><div id="summary-card-head"></div><div id="lead-summary"></div></div>';

var ASSIGNMENT_DETAIL_SHELL = DETAIL_SHELL.replace(
  '<span id="component-status-chip"></span><select id="assigned-to"></select>',
  '<span id="component-status-chip"></span><div id="assignment-group">' +
    '<div id="assigned-members"></div><select id="assigned-to"></select></div>'
);

function response(body) {
  return new Response(JSON.stringify(body), {
    status: 200, headers: { 'Content-Type': 'application/json' }
  });
}

async function withDetailStore(fn) {
  var saved = {
    user: Store.user, users: Store.users, projectId: Store.projectId,
    project: Store.project, tasks: Store.tasks, task: Store.task,
    allFields: Store.allFields, leadSummary: Store.leadSummary,
    overview: Store.overview, formations: Store.formations, pipeline: Store.pipeline
  };
  try {
    Store.user = { name: 'Supervisor', role: 'supervisor' };
    Store.users = [];
    Store.projectId = 44;
    Store.project = { project_id: 44, project_name: 'Navigation Guard', pipeline_type: 'prospect' };
    Store.tasks = [];
    Store.task = null;
    Store.allFields = {};
    Store.leadSummary = null;
    Store.overview = {};
    Store.formations = [];
    Store.pipeline = 'prospect';
    await fn();
  } finally {
    Object.keys(saved).forEach(function (key) { Store[key] = saved[key]; });
  }
}

test('detail-form ownership: GeoX can never host the Resource Assessment calculator', function () {
  assert.equal(stepHostsResourceCalculator('Resource Assessment'), true);
  assert.equal(stepHostsResourceCalculator('Pre-Drilling GeoX Assessment'), false);
  assert.equal(stepHostsResourceCalculator('Well Site Location'), false);
});

test('detail-form normalizes legacy Flowback rows without losing opaque IDs', function () {
  var field = { key: 'flowback_stages_rows', label: 'Flowback Stages', type: 'repeatable',
    columns: FLOWBACK_STAGE_COLUMNS };
  var host = fixture('<div id="dynamic-fields">' + renderRepeatableField(field, JSON.stringify([
    { _id: 'legacy-stage', flowback_formation: 'SARH', flowback_gas_rate_mmscfd: '8.2',
      flowback_choke_size_in: '0.5', future_key: 'retain' }
  ])) + '</div>');
  var rows = JSON.parse(getFields(host).flowback_stages_rows);
  assert.deepEqual(rows, [{
    id: 'legacy-stage', formation: 'SARH', top_md: '', base_md: '', dynamic_area_km2: '',
    dynamic_ogip_bcf: '', gas_rate_mmscfd: '8.2', water_rate_bwpd: '', liquid_rate_bpd: '',
    choke_size_in: '0.5', fwhp_psi: '', future_key: 'retain'
  }]);
});

test('detail-form GeoX: loadComponent renders the seven manual external-result controls', async function () {
  await withDetailStore(async function () {
    fixture(DETAIL_SHELL);
    var geox = {
      task_id: 91, task_name: 'Pre-Drilling GeoX Assessment', sequence_no: 12,
      stage_group: 'Pre-Well Delivery', status: 'In Progress', priority: 'Medium'
    };
    Store.tasks = [geox];
    mockFetch(function (url) {
      var path = String(url);
      if (path.indexOf('/api/tasks/91/dynamic-fields') >= 0) {
        return response({ pre_drill_piip_gas_p90: '80', pre_drill_piip_gas_mean: '100',
                          pre_drill_piip_gas_p10: '130' });
      }
      if (path.indexOf('/api/projects/44/component-folder/91') >= 0) {
        return response({ requires_folder: 0 });
      }
      throw new Error('Unexpected request: ' + path);
    });

    await loadComponent(geox);
    assert.equal(document.getElementById('resource-calculator-panel'), null);
    assert.equal(document.querySelectorAll('#dynamic-fields [data-field^="pre_drill_piip_"]').length, 7);
    assert.equal(document.querySelector('[data-field="pre_drill_piip_gas_p90"]').value, '80');
  });
});

test('detail-form assignment group shows every member and protects role members', async function () {
  await withDetailStore(async function () {
    fixture(ASSIGNMENT_DETAIL_SHELL);
    var component = {
      task_id: 95, task_name: 'Reservoir CoS', sequence_no: 2,
      stage_group: 'Risk Analysis', status: 'In Progress', priority: 'Medium',
      assigned_to: 'Employee', default_domain_role: 'Petrophysicist',
      assignees: [
        { name: 'Employee', source: 'role', notified: true },
        { name: 'Staff Member', source: 'manual', notified: true }
      ]
    };
    Store.tasks = [component];
    mockFetch(function (url) {
      var path = String(url);
      if (path.indexOf('/api/tasks/95/dynamic-fields') >= 0) return response({});
      if (path.indexOf('/api/projects/44/component-folder/95') >= 0) return response({ requires_folder: 0 });
      throw new Error('Unexpected request: ' + path);
    });

    await loadComponent(component);
    var chips = Array.from(document.querySelectorAll('#assigned-members .assignee-chip'));
    assert.equal(chips.length, 2);
    assert.ok(chips[0].textContent.indexOf('Employee') >= 0);
    assert.ok(chips[0].textContent.indexOf('Role') >= 0);
    assert.equal(chips[0].querySelector('.assignee-remove'), null,
      'role-derived membership cannot be removed manually');
    assert.ok(chips[1].querySelector('.assignee-remove'),
      'manual additions remain removable');
  });
});

test('detail-form navigation: stale step responses cannot remount over Staking Letters', async function () {
  await withDetailStore(async function () {
    fixture(DETAIL_SHELL);
    var staleFieldsResolve;
    var staleFolderResolve;
    var staleTask = {
      task_id: 90, task_name: 'Pre-Drilling GeoX Assessment', sequence_no: 12,
      stage_group: 'Pre-Well Delivery', status: 'In Progress', priority: 'Medium'
    };
    var approval = {
      task_id: 92, task_name: 'Approval to Stake', sequence_no: 10,
      stage_group: 'Pre-Well Delivery', status: 'In Progress', priority: 'Medium'
    };
    var wellsite = {
      task_id: 93, task_name: 'Well Site Location', sequence_no: 11,
      stage_group: 'Pre-Well Delivery', status: 'In Progress', priority: 'Medium'
    };
    Store.tasks = [staleTask, approval, wellsite];
    Store.allFields = { 'Approval to Stake': {}, 'Well Site Location': {} };
    mockFetch(function (url) {
      var path = String(url);
      if (path.indexOf('/api/tasks/90/dynamic-fields') >= 0) {
        return new Promise(function (resolve) { staleFieldsResolve = resolve; });
      }
      if (path.indexOf('/api/projects/44/component-folder/90') >= 0) {
        return new Promise(function (resolve) { staleFolderResolve = resolve; });
      }
      if (path.indexOf('/api/projects/44/component-folder/92') >= 0) {
        return response({ requires_folder: 0 });
      }
      throw new Error('Unexpected request: ' + path);
    });

    var staleLoad = loadComponent(staleTask);
    var leaked = document.createElement('div');
    leaked.id = 'resource-calculator-panel';
    leaked.textContent = 'old calculator';
    document.getElementById('dynamic-fields').appendChild(leaked);

    await loadComponent(wellsite);
    assert.equal(document.getElementById('resource-calculator-panel'), null,
      'navigation tears down all previous step-owned UI synchronously');
    assert.ok(document.querySelector('.sl-workspace'), 'the destination workspace owns the shell');
    assert.ok(document.querySelector('[data-sl-field="staked_x"]'), 'Card 4B X exists in the DOM');
    assert.ok(document.querySelector('[data-sl-field="staked_y"]'), 'Card 4B Y exists in the DOM');
    var reveal = document.querySelector('[data-sl-field="wellsite_letter_loaded"]');
    reveal.checked = true;
    reveal.dispatchEvent(new Event('change', { bubbles: true }));
    assert.equal(document.querySelector('[data-sl-section="location"]').classList.contains('hidden'), false,
      'checking the Wellsite letter reveals Staking Location X/Y');

    staleFieldsResolve(response({ pre_drill_piip_gas_p90: '999' }));
    staleFolderResolve(response({ requires_folder: 1, unc_path: 'stale' }));
    await staleLoad;
    assert.ok(document.querySelector('.sl-workspace'), 'the stale completion cannot replace the new step');
    assert.equal(document.querySelector('[data-field="pre_drill_piip_gas_p90"]'), null);
    assert.equal(document.getElementById('resource-calculator-panel'), null);
  });
});

// --- the generic row survives on BP step pages -------------------------------

test('detail-form action row: a BP step still shows Submit to its assignee', function () {
  withStore(function () {
    mount('employee', 'Employee', 'bp');
    renderActionButtons(task('Flowback Results', 'In Progress'));
    assert.equal(visible('submit-component'), true, 'the assignee may submit');
    assert.equal(visible('approve-component'), false);
    assert.equal(visible('return-component'), false);
    assert.equal(button('submit-component').textContent, 'Submit for Approval');
  });
});

test('detail-form action row: a BP Ready step shows Approve to a supervisor', function () {
  withStore(function () {
    mount('supervisor', 'Supervisor', 'bp');
    renderActionButtons(task('Flowback Results', 'Ready'));
    assert.equal(visible('approve-component'), true);
    assert.equal(visible('return-component'), true);
    assert.equal(button('approve-component').textContent, 'Approve');
    assert.equal(button('approve-component').disabled, false);
  });
});

// --- Item A: prospect step pages carry neither Save nor lifecycle buttons ----

test('detail-form action row: prospect steps hide Submit/Approve/Return at every status and for every role', function () {
  withStore(function () {
    ['employee', 'staff', 'supervisor'].forEach(function (role) {
      mount(role, role === 'employee' ? 'Employee' : role);
      ['Not Assigned', 'In Progress', 'Ready', 'Approved'].forEach(function (status) {
        renderActionButtons(task('Trap and Seal CoS', status));
        assert.equal(visible('submit-component'), false,
          role + '/' + status + ': the server auto-approves prospect saves, so Submit is furniture');
        assert.equal(visible('approve-component'), false, role + '/' + status);
        assert.equal(visible('return-component'), false, role + '/' + status);
      });
    });
  });
});

test('detail-form action row: the Save button is hidden on prospect pages and present on BP', function () {
  withStore(function () {
    mount('supervisor', 'Supervisor');
    renderActionButtons(task('Trap and Seal CoS', 'In Progress'));
    assert.equal(button('save-component').classList.contains('hidden'), true,
      'prospect pages auto-save; the button is gone');

    renderActionButtons(task('Segmentation Slides', 'Ready'));
    assert.equal(button('save-component').classList.contains('hidden'), true,
      'Segmentation Slides keeps its review row but not the Save button');
  });
  withStore(function () {
    mount('employee', 'Employee', 'bp');
    renderActionButtons(task('Flowback Results', 'In Progress'));
    assert.equal(button('save-component').classList.contains('hidden'), false,
      'BP step pages keep the explicit Save button');
  });
});

// --- card 3D: Segmentation Slides -------------------------------------------

// Card 3S put this step on the shared approval framework, which changed the
// employee's half: a save is never a submission, so there IS a Submit button
// now, and it appears only while there is something to ask for.
test('detail-form action row: an employee on Segmentation Slides submits explicitly', function () {
  withStore(function () {
    mount('employee', 'Employee');
    ['Not Assigned', 'In Progress'].forEach(function (status) {
      renderActionButtons(task('Segmentation Slides', status));
      assert.equal(visible('submit-component'), true,
        'the employee asks for review explicitly at ' + status);
      assert.equal(visible('approve-component'), false,
        'an employee is never offered Approve (' + status + ')');
      assert.equal(visible('return-component'), false,
        'an employee is never offered Return (' + status + ')');
      assert.equal(visible('reopen-component'), false,
        'reopening is a supervisor decision (' + status + ')');
    });
    // Already asked, or already decided: nothing left to submit.
    ['Ready', 'Approved'].forEach(function (status) {
      renderActionButtons(task('Segmentation Slides', status));
      assert.equal(visible('submit-component'), false,
        'no second request for the same review at ' + status);
      assert.equal(visible('reopen-component'), false);
    });
    // The Save button stays gone from prospect pages -- auto-save persists the
    // inputs, and Card 3S is explicit that auto-save is never submission.
    assert.equal(button('save-component').classList.contains('hidden'), true);
  });
});

test('detail-form action row: a supervisor may reopen an APPROVED Segmentation Slides', function () {
  withStore(function () {
    mount('supervisor', 'Supervisor');
    renderActionButtons(task('Segmentation Slides', 'Approved'));
    assert.equal(visible('reopen-component'), true);
    // Only from Approved -- there is nothing to reopen before that.
    ['Not Assigned', 'In Progress', 'Ready'].forEach(function (status) {
      renderActionButtons(task('Segmentation Slides', status));
      assert.equal(visible('reopen-component'), false, 'nothing to reopen at ' + status);
    });
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
    // BP context: the generic row is the one place the restored defaults are
    // still VISIBLE (prospect steps hide the whole row since Item A).
    mount('supervisor', 'Supervisor', 'bp');
    Store.pipeline = 'prospect';
    Store.project = { pipeline_type: 'prospect' };
    renderActionButtons(task('Segmentation Slides', 'Ready'));
    assert.equal(button('approve-component').textContent, 'Approved');

    Store.pipeline = 'bp';
    Store.project = { pipeline_type: 'bp' };
    renderActionButtons(task('Flowback Results', 'Ready'));

    assert.equal(button('approve-component').textContent, 'Approve',
      'the shared node carries no trace of the previous step');
    assert.equal(button('approve-component').className, '',
      'and none of its styling');
    assert.equal(button('return-component').textContent, 'Return for Update');
    assert.equal(button('return-component').className, 'ghost');
    assert.equal(button('approve-component').disabled, false);
  });
});

// --- multi-assignee: non-primary assignees can act on their work -------------

test('detail-form action row: a non-primary assignee can submit Segmentation Slides', function () {
  withStore(function () {
    mount('employee', 'Zed');
    renderActionButtons(task('Segmentation Slides', 'In Progress', 'Alice',
      [{ name: 'Alice', source: 'role' }, { name: 'Zed', source: 'role' }]));
    assert.equal(visible('submit-component'), true,
      'Zed is in task.assignees even though assigned_to is Alice');
    assert.equal(visible('approve-component'), false);
    assert.equal(visible('return-component'), false);
  });
});

test('detail-form action row: a non-primary assignee can submit a generic BP step', function () {
  withStore(function () {
    mount('employee', 'Zed', 'bp');
    renderActionButtons(task('Flowback Results', 'In Progress', 'Alice',
      [{ name: 'Alice', source: 'role' }, { name: 'Zed', source: 'role' }]));
    assert.equal(visible('submit-component'), true,
      'the generic submit button also honours task.assignees');
  });
});

test('detail-form action row: a non-primary assignee can return a Ready BP step', function () {
  withStore(function () {
    mount('employee', 'Zed', 'bp');
    renderActionButtons(task('Flowback Results', 'Ready', 'Alice',
      [{ name: 'Alice', source: 'role' }, { name: 'Zed', source: 'role' }]));
    assert.equal(visible('return-component'), true,
      'the Return button is visible to any assignee');
  });
});

test('detail-form action row: a non-assignee employee cannot submit', function () {
  withStore(function () {
    mount('employee', 'Bobby');
    renderActionButtons(task('Segmentation Slides', 'In Progress', 'Alice',
      [{ name: 'Alice', source: 'role' }, { name: 'Zed', source: 'role' }]));
    assert.equal(button('submit-component').disabled, true,
      'Bobby is not in assignees; submit must be disabled');
  });
});

test('detail-form action row: assignee matching is case-insensitive', function () {
  withStore(function () {
    mount('employee', 'zed');
    renderActionButtons(task('Segmentation Slides', 'In Progress', 'Alice',
      [{ name: 'Alice', source: 'role' }, { name: 'Zed', source: 'role' }]));
    assert.equal(visible('submit-component'), true,
      'lowercase user "zed" matches assignee "Zed"');
  });
});

test('detail-form action row: assigned_to fallback works when assignees array is absent', function () {
  withStore(function () {
    mount('employee', 'Alice');
    var t = task('Segmentation Slides', 'In Progress', 'Alice');
    delete t.assignees;
    renderActionButtons(t);
    assert.equal(visible('submit-component'), true,
      'the legacy scalar assigned_to still gates the action row');
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
