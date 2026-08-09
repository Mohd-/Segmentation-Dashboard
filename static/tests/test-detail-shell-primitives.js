import { test, assert, fixture } from './harness.js';
import {
  detailBackButtonHtml, detailStepItemHtml, detailStageHtml,
  detailEditorHeaderHtml, assignmentMembersHtml
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
  assert.equal(root.querySelectorAll('.assignee-remove').length, 1);
  assert.equal(root.querySelector('.assignee-remove').getAttribute('data-remove-name'), 'Lead Creator');
  assert.ok(root.querySelector('.assignee-remove svg.lucide-x'));
});
