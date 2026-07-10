export const Store = {
  meta: null,
  user: null, // {name, role} once signed in (from /api/me or the login dialog); null when anonymous
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

export function resetSelection() {
  Store.projectId = null;
  Store.project = null;
  Store.tasks = [];
  Store.task = null;
  Store.allFields = {};
  Store.leadSummary = null;
}
