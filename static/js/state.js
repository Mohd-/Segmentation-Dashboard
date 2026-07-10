export const Store = {
  meta: null,
  user: null, // {name, role} once signed in (from /api/me or the login dialog); null when anonymous
  users: null, // active users [{name, role}] cached from /api/users for the assignee select
  projectId: null,
  project: null,
  tasks: [],
  task: null,
  allFields: {},
  leadSummary: null,
  pipeline: 'prospect'
};

// The name to stamp into changed_by payloads. The server overrides this with
// the session identity when one exists; 'Web User' is the legacy fallback for
// anonymous (dev-mode) usage.
export function currentUserName() {
  return (Store.user && Store.user.name) || 'Web User';
}

// The effective permission role. Anonymous (null Store.user) mirrors the
// backend's dev-mode default in main.current_role(): everything runs as
// 'supervisor' when no session exists and AUTH_REQUIRED is off.
export function currentRole() {
  return Store.user ? (Store.user.role || 'employee') : 'supervisor';
}

// supervisor/staff manage assignment; only a supervisor approves/returns.
export function canManageAssignments() {
  var role = currentRole();
  return role === 'supervisor' || role === 'staff';
}

export function resetSelection() {
  Store.projectId = null;
  Store.project = null;
  Store.tasks = [];
  Store.task = null;
  Store.allFields = {};
  Store.leadSummary = null;
}
