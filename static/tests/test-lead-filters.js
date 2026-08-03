// Tests for static/js/views/lead-filters.js — the Card 1C filter row and the
// central filtered-leads selector every later card (1E's KPIs first) reads.
//
// The fixtures are board payload rows exactly as GET /api/projects returns
// them: assignees / tracked_items / display_stage / lead_priority / field are
// all server-derived (workflow/projects.py), so nothing here re-derives them.
import { test, assert, fixture } from './harness.js';
import {
  initLeadFilters, setLeadRows, setLeadUsers, filteredLeads, leadFilterState,
  clearLeadFilters, matchesLeadFilters, leadStatus, leadField,
  onLeadsFiltered, UNASSIGNED
} from '../js/views/lead-filters.js';
import { setComponentReferenceMode } from '../js/views/detail-form.js';
import { backToBoard } from '../js/navigation.js';
import { Store } from '../js/state.js';

var ROOT = 'lf-test-root';

function items(overrides) {
  var statuses = overrides || {};
  return [
    { stage: 'Lead Assessment', label: 'Area Definition', status: statuses['Area Definition'] || 'In Progress' },
    { stage: 'Lead Assessment', label: 'Thickness Estimation', status: statuses['Thickness Estimation'] || 'In Progress' },
    { stage: 'Risk Analysis', label: 'Segmentation Slides', status: statuses['Segmentation Slides'] || 'In Progress' }
  ];
}

function lead(name, extra) {
  var options = extra || {};
  return {
    project_id: options.project_id || 1,
    project_name: name,
    pipeline_type: 'prospect',
    field: options.field === undefined ? name.split('-')[0] : options.field,
    display_stage: options.display_stage || 'Lead Assessment',
    overall_status: options.overall_status || 'In Progress',
    assignees: options.assignees || [],
    lead_priority: options.lead_priority === undefined ? 'Medium' : options.lead_priority,
    tracked_items: items(options.statuses)
  };
}

// Mounts a live filter row over `rows` and returns its container element. Every
// call re-initializes the module, so the selection starts at its defaults.
function mount(rows, users) {
  var root = fixture('<div id="' + ROOT + '"></div>');
  initLeadFilters({ root: ROOT });
  setLeadUsers(users || []);
  setLeadRows(rows || []);
  return root.querySelector('#' + ROOT);
}

function control(host, key) {
  return host.querySelector('.lead-filter[data-filter="' + key + '"]');
}
function trigger(host, key) { return control(host, key).querySelector('.lf-trigger'); }
function label(host, key) { return trigger(host, key).querySelector('.lf-value').textContent; }
function options(host, key) {
  return Array.prototype.slice.call(control(host, key).querySelectorAll('.lf-option'));
}
function optionLabels(host, key) {
  return options(host, key).map(function (option) {
    return option.querySelector('.lf-option-label').textContent;
  });
}
function choose(host, key, value) {
  var match = options(host, key).filter(function (option) {
    return option.getAttribute('data-value') === value;
  })[0];
  if (!match) throw new Error('no "' + value + '" option in the ' + key + ' menu');
  match.click();
  return match;
}
function names() {
  return filteredLeads().map(function (row) { return row.project_name; });
}
function isOpen(host, key) {
  return !control(host, key).querySelector('.lf-menu').hidden;
}

var USERS = [{ name: 'R. Khalid', role: 'staff' }, { name: 'S. Ali', role: 'employee' },
             { name: 'N. Saleh', role: 'employee' }, { name: 'System', role: 'supervisor' }];

// ---------------------------------------------------------------------------
// The per-lead rules (pure)
// ---------------------------------------------------------------------------

test('lead-filters leadStatus: Completed / Pending Approval / In Progress, nothing else', function () {
  assert.equal(leadStatus(lead('A-1', { overall_status: 'Completed' })), 'Completed');
  assert.equal(leadStatus(lead('B-1', { statuses: { 'Segmentation Slides': 'Pending Approval' } })),
    'Pending Approval');
  assert.equal(leadStatus(lead('C-1')), 'In Progress');
  // A stored task status the board has no vocabulary for is still In Progress —
  // there is no Not Assigned board status.
  assert.equal(leadStatus(lead('D-1', { statuses: { 'Area Definition': 'Not Assigned' } })), 'In Progress');
  // Completed wins over any item state (a completed lead has nothing pending).
  assert.equal(leadStatus(lead('E-1', { overall_status: 'Completed',
    statuses: { 'Segmentation Slides': 'Pending Approval' } })), 'Completed');
});

test('lead-filters leadField reads the server-derived field, never the name', function () {
  assert.equal(leadField(lead('GALV-2')), 'GALV');
  assert.equal(leadField(lead('SOLO', { field: '' })), '');
});

test('lead-filters matchesLeadFilters ORs inside Assignee and ANDs across categories', function () {
  var multi = lead('CROX-2', { assignees: ['N. Saleh', 'S. Ali'] });
  // OR within the category: ANY selected member is enough.
  assert.ok(matchesLeadFilters(multi, { assignees: ['S. Ali'] }));
  assert.ok(matchesLeadFilters(multi, { assignees: ['R. Khalid', 'S. Ali'] }));
  assert.ok(!matchesLeadFilters(multi, { assignees: ['R. Khalid'] }));
  // AND across categories.
  assert.ok(matchesLeadFilters(multi, { assignees: ['S. Ali'], field: 'CROX' }));
  assert.ok(!matchesLeadFilters(multi, { assignees: ['S. Ali'], field: 'GALV' }));
  // Unassigned is the absence of assignees, and never matches a staffed lead.
  assert.ok(!matchesLeadFilters(multi, { assignees: [UNASSIGNED] }));
  assert.ok(matchesLeadFilters(lead('ORYX-2'), { assignees: [UNASSIGNED] }));
  // Unassigned ORs with people like any other option.
  assert.ok(matchesLeadFilters(multi, { assignees: [UNASSIGNED, 'S. Ali'] }));
});

// ---------------------------------------------------------------------------
// The row itself
// ---------------------------------------------------------------------------

test('lead-filters renders the three controls left to right, all defaulted', function () {
  var host = mount([lead('GALV-2')], USERS);
  assert.deepEqual(Array.prototype.map.call(host.querySelectorAll('.lead-filter'),
    function (group) { return group.getAttribute('data-filter'); }),
    ['assignee', 'field', 'status'],
    'no Priority control — priority stays on the cards as border color and sort');
  assert.equal(label(host, 'assignee'), 'Assignee');
  assert.equal(label(host, 'field'), 'Field');
  assert.equal(label(host, 'status'), 'Status');
  assert.ok(host.querySelector('.lf-clear'), 'the row offers a Clear control');
});

test('lead-filters assignee options are All / Unassigned / every active user, System excluded', function () {
  var host = mount([lead('GALV-2')], USERS);
  assert.deepEqual(optionLabels(host, 'assignee'),
    ['All Assignees', 'Unassigned', 'R. Khalid', 'S. Ali', 'N. Saleh']);
  var list = options(host, 'assignee');
  // Person glyph beside the named members only — the absence of a person is
  // not a person (same rule the lead card follows).
  assert.equal(list[0].querySelectorAll('.lf-option-icon').length, 0);
  assert.equal(list[1].querySelectorAll('.lf-option-icon').length, 0, 'Unassigned carries no person glyph');
  assert.equal(list[2].querySelectorAll('.lf-option-icon svg.lucide-user').length, 1);
  // Checkbox semantics on a real button.
  assert.equal(list[2].tagName, 'BUTTON');
  assert.equal(list[2].getAttribute('role'), 'checkbox');
  assert.equal(list[2].getAttribute('aria-checked'), 'false');
});

test('lead-filters field options come from the DATA, not a hard-coded list', function () {
  var host = mount([lead('GALV-2'), lead('GALV-3', { project_id: 2 }), lead('LUNA-1', { project_id: 3 })], USERS);
  assert.deepEqual(optionLabels(host, 'field'), ['All Fields', 'GALV', 'LUNA']);
});

test('lead-filters status options are the three board statuses, each with its own glyph', function () {
  var host = mount([lead('GALV-2')], USERS);
  assert.deepEqual(optionLabels(host, 'status'),
    ['All Statuses', 'Completed', 'Pending Approval', 'In Progress'],
    'no Not Assigned / Unassigned option exists');
  var glyphs = options(host, 'status').slice(1).map(function (option) {
    return option.querySelector('.lf-option-icon svg').getAttribute('class');
  });
  assert.ok(glyphs[0].indexOf('lucide-circle-check') >= 0, 'Completed = check');
  assert.ok(glyphs[1].indexOf('lucide-circle-minus') >= 0, 'Pending Approval = dash');
  assert.ok(glyphs[2].indexOf('lucide-circle') >= 0 && glyphs[2].indexOf('check') < 0, 'In Progress = empty ring');
  // Single-select semantics.
  assert.equal(options(host, 'status')[1].getAttribute('role'), 'radio');
});

test('lead-filters shows every lead by default, Completed leads included', function () {
  // The behaviour change this card owns: the board used to be hard-filtered to
  // In Progress, so matured leads were invisible. The default is now All.
  var host = mount([
    lead('DONE-1', { project_id: 1, overall_status: 'Completed' }),
    lead('OPEN-1', { project_id: 2 })
  ], USERS);
  assert.deepEqual(names(), ['DONE-1', 'OPEN-1']);
  choose(host, 'status', 'Completed');
  assert.deepEqual(names(), ['DONE-1']);
});

// ---------------------------------------------------------------------------
// Selecting
// ---------------------------------------------------------------------------

test('lead-filters assignee multi-select ORs members and keeps the menu open', function () {
  var host = mount([
    lead('A-1', { project_id: 1, assignees: ['R. Khalid'] }),
    lead('B-1', { project_id: 2, assignees: ['S. Ali', 'N. Saleh'] }),
    lead('C-1', { project_id: 3, assignees: ['N. Saleh'] }),
    lead('D-1', { project_id: 4 })
  ], USERS);
  trigger(host, 'assignee').click();
  assert.ok(isOpen(host, 'assignee'));

  choose(host, 'assignee', 'R. Khalid');
  assert.deepEqual(names(), ['A-1']);
  assert.ok(isOpen(host, 'assignee'), 'the menu stays open while several members are ticked');
  assert.equal(label(host, 'assignee'), 'R. Khalid');

  choose(host, 'assignee', 'S. Ali');
  assert.deepEqual(names(), ['A-1', 'B-1'], 'a lead matches if ANY selected member is on it');
  assert.equal(label(host, 'assignee'), '2 Assignees');
  assert.ok(isOpen(host, 'assignee'));

  // Un-ticking one leaves the other in force.
  choose(host, 'assignee', 'R. Khalid');
  assert.deepEqual(names(), ['B-1']);
  assert.equal(label(host, 'assignee'), 'S. Ali');
  // Un-ticking the last selection reverts to the resting caption.
  choose(host, 'assignee', 'S. Ali');
  assert.deepEqual(names(), ['A-1', 'B-1', 'C-1', 'D-1']);
  assert.equal(label(host, 'assignee'), 'Assignee');
});

test('lead-filters Unassigned matches leads with no assignees at all', function () {
  var host = mount([
    lead('A-1', { project_id: 1, assignees: ['R. Khalid'] }),
    lead('B-1', { project_id: 2 })
  ], USERS);
  choose(host, 'assignee', UNASSIGNED);
  assert.deepEqual(names(), ['B-1']);
  assert.equal(label(host, 'assignee'), 'Unassigned');
  // Unassigned + a member is still an OR.
  choose(host, 'assignee', 'R. Khalid');
  assert.deepEqual(names(), ['A-1', 'B-1']);
  assert.equal(label(host, 'assignee'), '2 Assignees');
});

test('lead-filters All Assignees clears the individual picks, and vice versa', function () {
  var host = mount([
    lead('A-1', { project_id: 1, assignees: ['R. Khalid'] }),
    lead('B-1', { project_id: 2, assignees: ['S. Ali'] })
  ], USERS);
  choose(host, 'assignee', 'R. Khalid');
  choose(host, 'assignee', 'S. Ali');
  assert.deepEqual(leadFilterState().assignees, ['R. Khalid', 'S. Ali']);

  choose(host, 'assignee', '');   // All Assignees
  assert.deepEqual(leadFilterState().assignees, []);
  assert.deepEqual(names(), ['A-1', 'B-1']);
  assert.equal(options(host, 'assignee')[0].getAttribute('aria-checked'), 'true',
    'All reads as chosen exactly when nothing else is');

  choose(host, 'assignee', 'S. Ali');  // and vice versa
  assert.deepEqual(leadFilterState().assignees, ['S. Ali']);
  assert.equal(options(host, 'assignee')[0].getAttribute('aria-checked'), 'false');
});

test('lead-filters single-selects replace their value and close the menu', function () {
  var host = mount([
    lead('GALV-2', { project_id: 1 }),
    lead('LUNA-1', { project_id: 2 }),
    lead('LUNA-2', { project_id: 3, lead_priority: 'High' })
  ], USERS);
  trigger(host, 'field').click();
  choose(host, 'field', 'LUNA');
  assert.deepEqual(names(), ['LUNA-1', 'LUNA-2']);
  assert.equal(label(host, 'field'), 'LUNA');
  assert.ok(!isOpen(host, 'field'), 'a single choice is a finished choice');

  choose(host, 'field', 'GALV');   // replaces, never accumulates
  assert.deepEqual(names(), ['GALV-2']);
  choose(host, 'field', '');
  assert.deepEqual(names(), ['GALV-2', 'LUNA-1', 'LUNA-2']);
});

test('lead-filters combines Assignee AND Status AND Field', function () {
  var host = mount([
    lead('GALV-2', { project_id: 1, assignees: ['R. Khalid'] }),
    lead('GALV-3', { project_id: 2, assignees: ['R. Khalid'],
                     statuses: { 'Segmentation Slides': 'Pending Approval' } }),
    lead('LUNA-1', { project_id: 3, assignees: ['R. Khalid'] }),
    lead('LUNA-2', { project_id: 4, assignees: ['S. Ali'] })
  ], USERS);
  choose(host, 'assignee', 'R. Khalid');
  assert.deepEqual(names(), ['GALV-2', 'GALV-3', 'LUNA-1']);
  choose(host, 'status', 'Pending Approval');
  assert.deepEqual(names(), ['GALV-3']);
  // Changing one category never resets another.
  assert.deepEqual(leadFilterState().assignees, ['R. Khalid']);
  choose(host, 'field', 'LUNA');
  assert.deepEqual(names(), []);
  choose(host, 'status', '');
  assert.deepEqual(names(), ['LUNA-1']);
  assert.deepEqual(leadFilterState(), { assignees: ['R. Khalid'], field: 'LUNA', status: '' });
});

test('lead-filters Clear restores every default', function () {
  var host = mount([
    lead('GALV-2', { project_id: 1, assignees: ['R. Khalid'] }),
    lead('LUNA-1', { project_id: 2 })
  ], USERS);
  choose(host, 'assignee', 'R. Khalid');
  choose(host, 'field', 'GALV');
  choose(host, 'status', 'In Progress');
  assert.deepEqual(names(), ['GALV-2']);

  host.querySelector('.lf-clear').click();
  assert.deepEqual(leadFilterState(), { assignees: [], field: '', status: '' });
  assert.deepEqual(names(), ['GALV-2', 'LUNA-1']);
  assert.equal(label(host, 'assignee'), 'Assignee');
  assert.equal(label(host, 'field'), 'Field');
  assert.equal(label(host, 'status'), 'Status');
});

// ---------------------------------------------------------------------------
// Interaction: one menu at a time, dismissal, refreshes
// ---------------------------------------------------------------------------

test('lead-filters opens one menu at a time and reports it via aria-expanded', function () {
  var host = mount([lead('GALV-2')], USERS);
  trigger(host, 'assignee').click();
  assert.ok(isOpen(host, 'assignee'));
  assert.equal(trigger(host, 'assignee').getAttribute('aria-expanded'), 'true');

  trigger(host, 'status').click();
  assert.ok(isOpen(host, 'status'));
  assert.ok(!isOpen(host, 'assignee'), 'opening one dismisses the other');
  assert.equal(trigger(host, 'assignee').getAttribute('aria-expanded'), 'false');

  trigger(host, 'status').click();   // the trigger toggles
  assert.ok(!isOpen(host, 'status'));
});

test('lead-filters Escape and an outside click dismiss WITHOUT clearing', function () {
  var host = mount([
    lead('GALV-2', { project_id: 1, assignees: ['R. Khalid'] }),
    lead('LUNA-1', { project_id: 2 })
  ], USERS);
  choose(host, 'assignee', 'R. Khalid');
  trigger(host, 'assignee').click();

  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  assert.ok(!isOpen(host, 'assignee'), 'Escape closes');
  assert.deepEqual(leadFilterState().assignees, ['R. Khalid'], 'Escape keeps the selection');
  assert.deepEqual(names(), ['GALV-2']);

  trigger(host, 'assignee').click();
  document.body.click();
  assert.ok(!isOpen(host, 'assignee'), 'an outside click closes');
  assert.deepEqual(leadFilterState().assignees, ['R. Khalid'], 'an outside click keeps the selection');
});

test('lead-filters keeps the menu open while its own option list is scrolled', function () {
  // A long assignee roster makes the menu itself scrollable; only scrolling
  // something AROUND it (the board, the page) may dismiss it.
  var host = mount([lead('GALV-2')], USERS);
  var menu = control(host, 'assignee').querySelector('.lf-menu');
  trigger(host, 'assignee').click();
  menu.dispatchEvent(new Event('scroll', { bubbles: false }));
  assert.ok(isOpen(host, 'assignee'), 'scrolling the list is not a dismissal');
  document.dispatchEvent(new Event('scroll'));
  assert.ok(!isOpen(host, 'assignee'), 'scrolling around it still dismisses');
});

test('lead-filters places an opened menu against the viewport', function () {
  // The menu is position: fixed (components.css) and gets viewport coordinates
  // written onto it here, which is what keeps it out of .pipeline-panel's and
  // .lead-column's overflow clipping. The runner loads no app stylesheet, so
  // the inline placement is the part this test can pin.
  var host = mount([lead('GALV-2')], USERS);
  var menu = control(host, 'assignee').querySelector('.lf-menu');
  assert.equal(menu.style.top, '', 'unplaced before it is opened');
  trigger(host, 'assignee').click();
  assert.ok(!menu.hidden);
  assert.match(menu.style.top, /^-?\d+px$/);
  assert.match(menu.style.left, /^-?\d+px$/);
  assert.match(menu.style.maxHeight, /^\d+px$/);
});

test('lead-filters selections survive a data refresh', function () {
  var host = mount([
    lead('GALV-2', { project_id: 1, assignees: ['R. Khalid'] }),
    lead('LUNA-1', { project_id: 2, assignees: ['S. Ali'] })
  ], USERS);
  choose(host, 'assignee', 'R. Khalid');
  choose(host, 'field', 'GALV');

  // What refreshAllBoards does: the same board, refetched.
  setLeadRows([
    lead('GALV-2', { project_id: 1, assignees: ['R. Khalid'] }),
    lead('GALV-9', { project_id: 3, assignees: ['R. Khalid'] }),
    lead('LUNA-1', { project_id: 2, assignees: ['S. Ali'] })
  ]);
  assert.deepEqual(leadFilterState(), { assignees: ['R. Khalid'], field: 'GALV', status: '' });
  assert.deepEqual(names(), ['GALV-2', 'GALV-9']);
  assert.equal(label(host, 'assignee'), 'R. Khalid');
  assert.equal(label(host, 'field'), 'GALV');
});

test('lead-filters keeps a selected field listed even after it leaves the data', function () {
  var host = mount([lead('GALV-2'), lead('LUNA-1', { project_id: 2 })], USERS);
  choose(host, 'field', 'GALV');
  setLeadRows([lead('LUNA-1', { project_id: 2 })]);
  assert.deepEqual(optionLabels(host, 'field'), ['All Fields', 'GALV', 'LUNA'],
    'a selection the user can see must stay clearable');
  assert.deepEqual(names(), []);
});

// ---------------------------------------------------------------------------
// The contract Card 1E consumes
// ---------------------------------------------------------------------------

test('lead-filters publishes the filtered rowset to subscribers and the event', function () {
  var seen = [];
  var events = [];
  var root = fixture('<div id="' + ROOT + '"></div>');
  var listener = function (event) { events.push(event.detail.leads.length); };
  document.addEventListener('leads:filtered', listener);
  try {
    initLeadFilters({ root: ROOT, onChange: function (leads) { seen.push(leads.length); } });
    setLeadUsers(USERS);
    setLeadRows([
      lead('GALV-2', { project_id: 1, assignees: ['R. Khalid'] }),
      lead('LUNA-1', { project_id: 2 })
    ]);
    assert.deepEqual(seen, [2], 'the first payload publishes once');
    assert.deepEqual(events, [2]);

    var host = root.querySelector('#' + ROOT);
    choose(host, 'assignee', 'R. Khalid');
    assert.deepEqual(seen, [2, 1], 'every filter change republishes — no refetch');
    assert.deepEqual(events, [2, 1]);
    assert.deepEqual(filteredLeads().map(function (row) { return row.project_name; }), ['GALV-2']);
  } finally {
    document.removeEventListener('leads:filtered', listener);
  }
});

test('lead-filters filteredLeads() hands out a copy, not the live array', function () {
  mount([lead('GALV-2'), lead('LUNA-1', { project_id: 2 })], USERS);
  var first = filteredLeads();
  first.push(lead('FAKE-1'));
  assert.equal(filteredLeads().length, 2, 'a consumer cannot grow the board by mutating its copy');
  clearLeadFilters();
  assert.equal(filteredLeads().length, 2);
});

test('lead-filters onLeadsFiltered subscribers survive later filter changes', function () {
  var host = mount([
    lead('GALV-2', { project_id: 1, assignees: ['R. Khalid'] }),
    lead('LUNA-1', { project_id: 2 })
  ], USERS);
  var counts = [];
  onLeadsFiltered(function (leads) { counts.push(leads.length); });
  choose(host, 'assignee', 'R. Khalid');
  choose(host, 'assignee', '');
  assert.deepEqual(counts, [1, 2]);
});

// ---------------------------------------------------------------------------
// KI-002 — the assignee control's role gate (fixed with this card's assignee
// work; see KNOWN_ISSUES.md)
// ---------------------------------------------------------------------------

test('detail-form leaving reference mode does NOT enable the assignee select for an employee', function () {
  var savedUser = Store.user;
  var savedProject = Store.project;
  var savedPipeline = Store.pipeline;
  // The hostile arrangement: #assigned-to sits INSIDE the form the sweep walks.
  fixture('<form id="component-form">' +
          '<select id="assigned-to" disabled></select>' +
          '<input id="comments">' +
          '</form>');
  try {
    Store.user = { name: 'Employee', role: 'employee' };
    Store.project = { pipeline_type: 'prospect' };
    Store.pipeline = 'prospect';

    setComponentReferenceMode(true);
    assert.equal(document.getElementById('assigned-to').disabled, true);
    assert.equal(document.getElementById('comments').disabled, true);

    // The second, post-async call that used to re-enable everything.
    setComponentReferenceMode(false);
    assert.equal(document.getElementById('comments').disabled, false,
      'the form controls this mode owns come back');
    assert.equal(document.getElementById('assigned-to').disabled, true,
      'an employee must never be handed a control the backend will refuse');

    // A supervisor gets it back, in the current pipeline only.
    Store.user = { name: 'Supervisor', role: 'supervisor' };
    setComponentReferenceMode(false);
    assert.equal(document.getElementById('assigned-to').disabled, false);
    setComponentReferenceMode(true);
    assert.equal(document.getElementById('assigned-to').disabled, true,
      'reference mode still locks it for everyone');
  } finally {
    Store.user = savedUser;
    Store.project = savedProject;
    Store.pipeline = savedPipeline;
  }
});

/* -------------------------------------------------------------------------
   Card 2A round trip: board -> lead detail -> Back to Segment Maturation.

   The single back control is NAVIGATION ONLY (navigation.js backToBoard), so
   the board must come back exactly as it was left: same selection, same
   filtered rowset. This is the regression that keeps a future "refresh on the
   way back" from quietly resetting the user's filters.
   ------------------------------------------------------------------------- */

test('board -> detail -> Back preserves the active filters and the filtered rowset', function () {
  var shell = fixture(
    '<nav class="tabs">' +
      '<button data-tab="prospect" type="button" aria-selected="false">Prospect</button>' +
      '<button data-tab="bp" type="button" aria-selected="false">BP</button>' +
    '</nav>' +
    '<section id="tab-prospect" class="tab"></section>' +
    '<section id="tab-bp" class="tab"></section>' +
    '<section id="detail-shell"></section>'
  );
  var host = mount(
    [lead('GALV-2', { project_id: 1, assignees: ['R. Khalid'] }),
     lead('LUNA-2', { project_id: 2, assignees: ['S. Ali'] })],
    USERS
  );
  choose(host, 'assignee', 'R. Khalid');
  assert.deepEqual(names(), ['GALV-2'], 'a filter is active before the round trip');
  var before = leadFilterState();

  // ... the user opens GALV-2's lead detail page, then uses the one back
  // control at its top left.
  Store.project = { project_id: 1, project_name: 'GALV-2', pipeline_type: 'prospect' };
  backToBoard();

  assert.ok(shell.querySelector('#detail-shell').classList.contains('hidden'), 'the detail shell closes');
  assert.ok(shell.querySelector('#tab-prospect').classList.contains('active'), 'the board tab is active again');
  assert.deepEqual(leadFilterState(), before, 'the selection is untouched');
  assert.deepEqual(names(), ['GALV-2'], 'the same filtered rowset, not a refetch');
  Store.project = null;
});
