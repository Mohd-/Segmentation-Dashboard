import { test, assert, fixture } from './harness.js';
import {
  detailBackButtonHtml, detailStepItemHtml, detailStageHtml,
  detailEditorHeaderHtml, assignmentChecklistHtml, assignmentMembersHtml,
  wireAssignmentChecklist
} from '../js/ui/detail-shell.js';

test('detail shell back control uses the shared Lucide button contract', function () {
  var root = fixture(detailBackButtonHtml({ id: 'back', label: 'Back to Board' }));
  assert.equal(root.querySelectorAll('button.back-to-board.detail-back').length, 1);
  assert.ok(root.querySelector('svg.lucide-arrow-left'));
  assert.equal(root.textContent.trim(), 'Back to Board');
});

test('detail shell stage and step primitives preserve the shared rail anatomy', function () {
  var item = detailStepItemHtml({ number: 2, label: 'Risk Review', statusSlug: 'in-progress',
    active: true, attributes: { 'data-task-id': 9 } });
  var root = fixture(detailStageHtml({ stage: 'risk', label: 'Risk Analysis', icon: 'gauge',
    done: 1, total: 4, open: true, itemsHtml: item }));
  assert.ok(root.querySelector('.rail-stage-lead.is-active'));
  assert.equal(root.querySelector('.rail-stage-count').textContent, '1/4');
  assert.ok(root.querySelector('.rail-stage-chevron svg'));
  assert.equal(root.querySelector('.component-item.active').getAttribute('data-task-id'), '9');
});

test('detail shell editor header keeps number title status and controls in one order', function () {
  var root = fixture(detailEditorHeaderHtml({ number: 7, title: 'Well Proposal',
    statusHtml: '<span class="status">In Progress</span>',
    controlsHtml: '<div class="assignment-group"></div>' }));
  assert.equal(root.querySelector('.component-number').textContent, '7');
  assert.equal(root.querySelector('h2').textContent, 'Well Proposal');
  assert.ok(root.querySelector('.status'));
  assert.ok(root.querySelector('.assignment-group'));
});

test('detail shell assignment members use SVG removal and protect role mappings', function () {
  var root = fixture('<div>' + assignmentMembersHtml([
    { name: 'Role Owner', source: 'role' },
    { name: 'Lead Creator', source: 'creator' }
  ], { removeAttribute: 'data-remove-name', editable: true }) + '</div>');
  assert.equal(root.querySelectorAll('.assignee-chip').length, 2);
  assert.equal(root.querySelectorAll('.assignee-chip-source').length, 0);
  assert.equal(root.querySelectorAll('.assignee-chip')[0].textContent.trim(), 'Role Owner');
  assert.equal(root.querySelectorAll('.assignee-chip')[0].hasAttribute('title'), false,
    'assignment source is not exposed as tooltip text');
  assert.equal(root.querySelectorAll('.assignee-remove').length, 1);
  assert.equal(root.querySelector('.assignee-remove').getAttribute('data-remove-name'), 'Lead Creator');
  assert.ok(root.querySelector('.assignee-remove svg.lucide-x'));
});

test('detail shell assignment checklist exposes checked, role-locked and editable people', function () {
  var root = fixture(assignmentChecklistHtml([
    { name: 'Role Owner' }, { name: 'Manual Owner' }, { name: 'Available User' }
  ], [
    { name: 'Role Owner', source: 'role' }, { name: 'Manual Owner', source: 'manual' }
  ], { triggerId: 'people', menuId: 'people-menu', editable: true }));
  var inputs = root.querySelectorAll('[data-assignment-name]');
  assert.equal(inputs.length, 3);
  assert.equal(inputs[0].checked, true);
  assert.equal(inputs[0].disabled, true, 'role assignments cannot be unchecked');
  assert.equal(inputs[1].checked, true);
  assert.equal(inputs[1].disabled, false);
  assert.equal(inputs[2].checked, false);
  assert.equal(root.querySelector('#people').getAttribute('aria-controls'), 'people-menu');
});

test('detail shell assignment checklist toggles in place and dismisses accessibly', async function () {
  var root = fixture(assignmentChecklistHtml([{ name: 'Available User' }], [], {
    triggerId: 'people-toggle', menuId: 'people-toggle-menu', editable: true
  }));
  var changes = [];
  wireAssignmentChecklist(root, function (change) {
    changes.push({ name: change.name, checked: change.checked });
  });
  var trigger = root.querySelector('#people-toggle');
  var menu = root.querySelector('#people-toggle-menu');
  trigger.click();
  assert.equal(menu.hidden, false);
  assert.equal(trigger.getAttribute('aria-expanded'), 'true');
  var input = menu.querySelector('input');
  input.checked = true;
  input.dispatchEvent(new Event('change', { bubbles: true }));
  await Promise.resolve();
  assert.deepEqual(changes, [{ name: 'Available User', checked: true }]);
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  assert.equal(menu.hidden, true);
  assert.equal(trigger.getAttribute('aria-expanded'), 'false');
  trigger.click();
  document.body.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  assert.equal(menu.hidden, true, 'an outside click dismisses the checklist');
});
