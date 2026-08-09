/* Shared, pure markup primitives for the Segment Maturation and BPE detail
   shells. Domain modules keep ownership of state, permissions, requests and
   event wiring; this module owns the visual/DOM contract the two shells share. */
import { esc } from '../dom.js';
import { ICONS } from '../icons.js';

function attributesHtml(attributes) {
  return Object.keys(attributes || {}).map(function (name) {
    var value = attributes[name];
    if (value === null || value === undefined || value === false) return '';
    return ' ' + name + (value === true ? '' : '="' + esc(value) + '"');
  }).join('');
}

export function detailBackButtonHtml(options) {
  var opts = options || {};
  var iconName = opts.icon || 'arrow-left';
  return '<button type="button" id="' + esc(opts.id || 'detail-back') +
    '" class="ghost back-to-board detail-back"' + attributesHtml(opts.attributes) + '>' +
    (ICONS[iconName] || '') + '<span>' + esc(opts.label || 'Back') + '</span></button>';
}

export function detailStepItemHtml(options) {
  var opts = options || {};
  var tag = opts.disabled ? 'div' : 'button';
  var classes = 'component-item status-' + esc(opts.statusSlug || 'not-assigned') +
    (opts.active ? ' active' : '') + (opts.disabled ? ' component-item-future' : '') +
    (opts.className ? ' ' + esc(opts.className) : '');
  return '<' + tag + (tag === 'button' ? ' type="button"' : '') + ' class="' + classes + '"' +
    attributesHtml(opts.attributes) + '><span class="component-num">' + esc(opts.number) +
    '</span><b>' + esc(opts.label || '') + '</b></' + tag + '>';
}

export function detailStageHtml(options) {
  var opts = options || {};
  var open = !!opts.open;
  var stage = opts.stage || '';
  return '<div class="rail-stage rail-stage-lead' + (open ? ' is-active' : '') +
    '" data-stage="' + esc(stage) + '">' +
    '<button type="button" class="rail-stage-head' + (open ? ' open' : '') +
      '" data-stage="' + esc(stage) + '" aria-expanded="' + open + '">' +
      '<span class="stage-icon" aria-hidden="true">' + (ICONS[opts.icon] || '') + '</span>' +
      '<span class="rail-stage-name">' + esc(opts.label || stage) + '</span>' +
      '<span class="rail-stage-count">' + esc(opts.done || 0) + '/' + esc(opts.total || 0) + '</span>' +
      '<span class="rail-stage-chevron" aria-hidden="true">' + (ICONS['chevron-down'] || '') + '</span>' +
    '</button>' +
    '<div class="rail-stage-body' + (open ? '' : ' collapsed') + '" data-stage="' + esc(stage) + '">' +
      (opts.itemsHtml || '') + '</div></div>';
}

export function detailEditorHeaderHtml(options) {
  var opts = options || {};
  return '<div class="editor-head">' +
    '<span class="component-number">' + esc(opts.number || 1) + '</span>' +
    '<div class="editor-title"><h2>' + esc(opts.title || '') + '</h2></div>' +
    (opts.saveStateHtml || '') + (opts.statusHtml || '') + (opts.controlsHtml || '') +
    '</div>';
}

export function assignmentMembersHtml(members, options) {
  var list = members || [];
  var opts = options || {};
  if (!list.length) return '<span class="assignee-chip">Unassigned</span>';
  return list.map(function (member) {
    var removable = member.source !== 'role' && opts.removable !== false;
    var removeAttrs = {};
    if (opts.removeAttribute) removeAttrs[opts.removeAttribute] = member.name;
    if (opts.editable === false) removeAttrs.disabled = true;
    return '<span class="assignee-chip">' + esc(member.name) +
      (removable ? '<button type="button" class="assignee-remove" aria-label="Remove ' + esc(member.name) +
        '"' + attributesHtml(removeAttrs) + '>' + ICONS.x + '</button>' : '') + '</span>';
  }).join('');
}

/* One checklist contract for both detail shells.  Assignment ownership stays
   in the view modules; this helper only normalizes people and renders state. */
export function assignmentChecklistHtml(users, members, options) {
  var opts = options || {};
  var editable = opts.editable !== false;
  var memberByName = {};
  (members || []).forEach(function (member) { memberByName[member.name] = member; });
  var names = [];
  (users || []).concat(members || []).forEach(function (entry) {
    var name = entry && entry.name;
    if (name && names.indexOf(name) < 0) names.push(name);
  });
  var triggerId = opts.triggerId || 'assignment-picker-trigger';
  var menuId = opts.menuId || triggerId + '-menu';
  var disabledReason = opts.disabledReason || 'You do not have permission to change assignees.';
  var rows = names.map(function (name) {
    var member = memberByName[name];
    var checked = !!member;
    var roleLocked = checked && member.source === 'role';
    var disabled = !editable || roleLocked;
    var reason = roleLocked ? 'Assigned by role mapping and cannot be removed.' : (!editable ? disabledReason : '');
    return '<label class="assignment-check-option' + (disabled ? ' is-disabled' : '') + '"' +
      (reason ? ' title="' + esc(reason) + '"' : '') + '>' +
      '<input type="checkbox" data-assignment-name="' + esc(name) + '"' +
      (checked ? ' checked' : '') + (disabled ? ' disabled' : '') + '>' +
      '<span>' + esc(name) + '</span>' +
      (roleLocked ? '<span class="sr-only">Assigned by role mapping; cannot be removed.</span>' : '') +
      '</label>';
  }).join('') || '<p class="assignment-check-empty">No active users available.</p>';
  return '<div class="assignment-picker" data-assignment-picker>' +
    '<button type="button" id="' + esc(triggerId) + '" class="editor-assignee assignment-picker-trigger"' +
      ' aria-haspopup="true" aria-expanded="false" aria-controls="' + esc(menuId) + '"' +
      (!editable ? ' disabled title="' + esc(disabledReason) + '"' : '') + '>' +
      '<span>Manage assignees</span>' + ICONS['chevron-down'] + '</button>' +
    '<div id="' + esc(menuId) + '" class="assignment-checklist" role="group" aria-label="Assignees" hidden>' +
      rows + '</div></div>';
}

var assignmentDismissWired = false;

function closeAssignmentPickers(except) {
  Array.prototype.slice.call(document.querySelectorAll('[data-assignment-picker]')).forEach(function (picker) {
    if (picker === except) return;
    var trigger = picker.querySelector('.assignment-picker-trigger');
    var menu = picker.querySelector('.assignment-checklist');
    if (trigger) trigger.setAttribute('aria-expanded', 'false');
    if (menu) menu.hidden = true;
  });
}

/* onToggle receives {name, checked, input}. It may return a promise; a failed
   mutation restores the previous checkbox state and leaves the menu usable. */
export function wireAssignmentChecklist(root, onToggle) {
  var picker = root && root.matches && root.matches('[data-assignment-picker]')
    ? root : root && root.querySelector && root.querySelector('[data-assignment-picker]');
  if (!picker || picker.dataset.assignmentBound) return;
  picker.dataset.assignmentBound = '1';
  var trigger = picker.querySelector('.assignment-picker-trigger');
  var menu = picker.querySelector('.assignment-checklist');
  if (!trigger || !menu) return;
  trigger.addEventListener('click', function () {
    var opening = menu.hidden;
    closeAssignmentPickers(opening ? picker : null);
    menu.hidden = !opening;
    trigger.setAttribute('aria-expanded', String(opening));
  });
  menu.addEventListener('change', function (event) {
    var input = event.target.closest('[data-assignment-name]');
    if (!input || input.disabled) return;
    var next = input.checked;
    input.disabled = true;
    var mutation;
    try {
      mutation = onToggle({ name: input.dataset.assignmentName, checked: next, input: input });
    } catch (error) {
      mutation = Promise.reject(error);
    }
    Promise.resolve(mutation)
      .catch(function () { input.checked = !next; })
      .then(function () { if (document.body.contains(input)) input.disabled = false; });
  });
  if (!assignmentDismissWired) {
    assignmentDismissWired = true;
    document.addEventListener('click', function (event) {
      if (!event.target.closest('[data-assignment-picker]')) closeAssignmentPickers();
    });
    document.addEventListener('keydown', function (event) {
      if (event.key !== 'Escape') return;
      var open = document.querySelector('[data-assignment-picker] .assignment-checklist:not([hidden])');
      if (!open) return;
      var owner = open.closest('[data-assignment-picker]');
      var ownerTrigger = owner.querySelector('.assignment-picker-trigger');
      closeAssignmentPickers();
      if (ownerTrigger) ownerTrigger.focus();
    });
  }
}
