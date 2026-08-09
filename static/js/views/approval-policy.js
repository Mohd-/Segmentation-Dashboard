// Shared rendering helpers for the server-owned approval permission object.
// Neither detail shell knows step names or approval slugs; it only renders the
// actions the current task response authorizes.

export var APPROVAL_ACTIONS = [
  { key: 'return', label: 'Return', className: 'ghost' },
  { key: 'submit', label: 'Submit for Approval', className: '' },
  { key: 'approve', label: 'Approve', className: '' },
  { key: 'reopen', label: 'Reopen', className: 'ghost' }
];

export function approvalActionsMarkup(permissions, dataAttribute) {
  permissions = permissions || {};
  if (!permissions.approval_required) return '';
  dataAttribute = dataAttribute || 'data-approval-transition';
  return APPROVAL_ACTIONS.filter(function (action) {
    return permissions['can_' + action.key];
  }).map(function (action) {
    return '<button type="button" ' + dataAttribute + '="' + action.key + '"' +
      (action.className ? ' class="' + action.className + '"' : '') + '>' +
      action.label + '</button>';
  }).join('');
}

export function applyApprovalActions(buttons, permissions) {
  permissions = permissions || {};
  APPROVAL_ACTIONS.forEach(function (action) {
    var button = buttons[action.key];
    if (!button) return;
    button.classList.toggle('hidden', !permissions.approval_required ||
      !permissions['can_' + action.key]);
    button.disabled = false;
  });
}

export function approvalContentLocked(permissions) {
  return !permissions || permissions.can_edit !== true;
}
