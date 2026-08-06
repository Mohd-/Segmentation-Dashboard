import { currentUserName } from './state.js';
import { loginDialog } from './dialog.js';

var API_VERSION = '13';

export function requestUrl(path) {
  return path + (path.indexOf('?') >= 0 ? '&' : '?') + '_v=' + API_VERSION + '&_t=' + Date.now();
}
export function jsonOptions(method, payload) {
  return { method: method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload || {}) };
}
function handleResponse(response) {
  var contentType = response.headers.get('content-type') || '';
  if (!response.ok) {
    return (contentType.indexOf('json') >= 0 ? response.json() : response.text()).then(function (payload) {
      var error = new Error(typeof payload === 'string' ? payload : (payload.detail || payload.message || JSON.stringify(payload)));
      // Callers need to tell "the server READ this and refused it" (4xx --
      // fix the form) from "the request never landed" (network or 5xx --
      // try again). A rejected fetch throws with no status at all, which is
      // the same signal by absence.
      error.status = response.status;
      throw error;
    });
  }
  return contentType.indexOf('json') >= 0 ? response.json() : response;
}
export function api(path, options) {
  var opts = options || {};
  opts.cache = 'no-store';
  return fetch(requestUrl(path), opts).then(function (response) {
    // AUTH_REQUIRED deployments answer 401 until a session exists: open the
    // login dialog, then retry the original request ONCE. The retry path goes
    // straight to handleResponse, so a second 401 surfaces as a normal error
    // instead of looping. A dismissed dialog surfaces the original 401.
    if (response.status === 401) {
      return loginDialog().then(function (user) {
        if (!user) return handleResponse(response);
        return fetch(requestUrl(path), opts).then(handleResponse);
      });
    }
    return handleResponse(response);
  });
}

export var API = {
  meta: function () { return api('/api/meta'); },
  me: function () { return api('/api/me'); },
  users: function () { return api('/api/users'); },
  logout: function () { return api('/api/logout', jsonOptions('POST', {})); },
  projects: function (query) {
    var qs = new URLSearchParams(query || {}).toString();
    return api('/api/projects' + (qs ? '?' + qs : ''));
  },
  create: function (payload) { return api('/api/projects', jsonOptions('POST', payload)); },
  project: function (id) { return api('/api/projects/' + id); },
  detail: function (id) { return api('/api/projects/' + id + '/detail'); },
  rename: function (id, payload) { return api('/api/projects/' + id + '/rename', jsonOptions('PATCH', payload)); },
  deleteProject: function (id) { return api('/api/projects/' + id, { method: 'DELETE' }); },
  flags: function (id, payload) { return api('/api/projects/' + id + '/flags', jsonOptions('PATCH', payload)); },
  // Lead-level priority: ONE stored value per record (projects.priority),
  // supervisor-only server-side. Replaces the retired per-task
  // PATCH /api/tasks/<id>/priority call.
  projectPriority: function (id, payload) { return api('/api/projects/' + id + '/priority', jsonOptions('PATCH', payload)); },
  tasks: function (id) { return api('/api/projects/' + id + '/tasks'); },
  projectFields: function (id) { return api('/api/projects/' + id + '/dynamic-fields'); },
  formations: function (id) { return api('/api/projects/' + id + '/formations'); },
  saveFormations: function (id, payload) { return api('/api/projects/' + id + '/formations', jsonOptions('PUT', payload)); },
  componentFolder: function (projectId, taskId) { return api('/api/projects/' + projectId + '/component-folder/' + taskId); },
  sectionFolder: function (projectId, sectionKey) { return api('/api/projects/' + projectId + '/folders/' + sectionKey); },
  fields: function (id) { return api('/api/tasks/' + id + '/dynamic-fields'); },
  saveFields: function (id, fields) { return api('/api/tasks/' + id + '/dynamic-fields', jsonOptions('PATCH', { fields: fields, changed_by: currentUserName() })); },
  updateTask: function (id, payload) { return api('/api/tasks/' + id, jsonOptions('PATCH', payload)); },
  assign: function (id, payload) { return api('/api/tasks/' + id + '/assign', jsonOptions('POST', payload)); },
  transition: function (id, payload) { return api('/api/tasks/' + id + '/transition', jsonOptions('POST', payload)); },
  // Resource Assessment calculator (views/resource-calculator.js): taskId is
  // the Resource Assessment component's own task_id.
  resourceAssessment: function (taskId, payload) { return api('/api/tasks/' + taskId + '/resource-assessment', jsonOptions('POST', payload)); },
  calculatorResources: function (payload) { return api('/api/calculators/resources', jsonOptions('POST', payload)); },
  calculatorReservoirCos: function (payload) { return api('/api/calculators/reservoir-cos', jsonOptions('POST', payload)); },
  // Header bell (views/header-menus.js). All three answer with the CURRENT
  // unread_count alongside their own payload, so the red dot and the menu are
  // updated from one round trip and can never disagree. Every route is scoped
  // server-side to the session identity -- there is no "notifications for user
  // X" call to make.
  notifications: function () { return api('/api/notifications'); },
  markNotificationRead: function (id) { return api('/api/notifications/' + id + '/read', jsonOptions('POST', {})); },
  markAllNotificationsRead: function () { return api('/api/notifications/read-all', jsonOptions('POST', {})); },
  activity: function (projectId) { return api('/api/activity' + (projectId ? '?project_id=' + encodeURIComponent(projectId) : '')); },
  businessRows: function () { return api('/api/business-plan/rows'); },
  businessPlanDashboard: function (query) {
    var qs = new URLSearchParams(query || {}).toString();
    return api('/api/business-plan/dashboard' + (qs ? '?' + qs : ''));
  },
  businessPlanDetail: function (projectId, step) {
    return api('/api/business-plan/wells/' + projectId + '/steps/' + encodeURIComponent(step));
  },
  saveBusinessPlanField: function (projectId, step, payload) {
    return api('/api/business-plan/wells/' + projectId + '/steps/' + encodeURIComponent(step) + '/field',
      jsonOptions('PATCH', payload));
  },
  saveBusinessPlanFormations: function (projectId, step, rows) {
    return api('/api/business-plan/wells/' + projectId + '/steps/' + encodeURIComponent(step) + '/formations',
      jsonOptions('PUT', { rows: rows, changed_by: currentUserName() }));
  },
  saveBusinessPlanFlowback: function (projectId, rows) {
    return api('/api/business-plan/wells/' + projectId + '/flowback-stages',
      jsonOptions('PUT', { rows: rows, changed_by: currentUserName() }));
  },
  transitionBusinessPlan: function (projectId, step, action, comment) {
    return api('/api/business-plan/wells/' + projectId + '/steps/' + encodeURIComponent(step) + '/transition',
      jsonOptions('POST', { action: action, comment: comment || '', changed_by: currentUserName() }));
  },
  assignBusinessPlan: function (projectId, step, assignee) {
    return api('/api/business-plan/wells/' + projectId + '/steps/' + encodeURIComponent(step) + '/assign',
      jsonOptions('POST', { assignee: assignee, changed_by: currentUserName() }));
  },
  portfolioRows: function (query) {
    var qs = new URLSearchParams(query || {}).toString();
    return api('/api/portfolio/rows' + (qs ? '?' + qs : ''));
  },
  // The one multipart call in the app: NO Content-Type header, because the
  // browser has to set it itself with the multipart boundary. jsonOptions is
  // deliberately not used here for that reason.
  uploadPortfolioWaterfall: function (formData) {
    return api('/api/portfolio/waterfall', { method: 'POST', body: formData });
  },
  deletePortfolioWaterfall: function () {
    return api('/api/portfolio/waterfall', { method: 'DELETE' });
  }
};
