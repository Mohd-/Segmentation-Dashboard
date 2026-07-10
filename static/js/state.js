export const CURRENT_USER = 'Web User';

export const Store = {
  projectId: null,
  project: null,
  tasks: [],
  task: null,
  allFields: {},
  leadSummary: null,
  pipeline: 'prospect'
};

export function resetSelection() {
  Store.projectId = null;
  Store.project = null;
  Store.tasks = [];
  Store.task = null;
  Store.allFields = {};
  Store.leadSummary = null;
}
