import { CURRENT_USER } from './state.js';

var API_VERSION = '12';

export function requestUrl(path) {
  return path + (path.indexOf('?') >= 0 ? '&' : '?') + '_v=' + API_VERSION + '&_t=' + Date.now();
}
export function jsonOptions(method, payload) {
  return { method: method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload || {}) };
}
export function api(path, options) {
  var opts = options || {};
  opts.cache = 'no-store';
  return fetch(requestUrl(path), opts).then(function (response) {
    var contentType = response.headers.get('content-type') || '';
    if (!response.ok) {
      return (contentType.indexOf('json') >= 0 ? response.json() : response.text()).then(function (payload) {
        throw new Error(typeof payload === 'string' ? payload : (payload.detail || payload.message || JSON.stringify(payload)));
      });
    }
    return contentType.indexOf('json') >= 0 ? response.json() : response;
  });
}

export var API = {
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
  tasks: function (id) { return api('/api/projects/' + id + '/tasks'); },
  projectFields: function (id) { return api('/api/projects/' + id + '/dynamic-fields'); },
  componentFolder: function (projectId, taskId) { return api('/api/projects/' + projectId + '/component-folder/' + taskId); },
  fields: function (id) { return api('/api/tasks/' + id + '/dynamic-fields'); },
  saveFields: function (id, fields) { return api('/api/tasks/' + id + '/dynamic-fields', jsonOptions('PATCH', { fields: fields, changed_by: CURRENT_USER })); },
  updateTask: function (id, payload) { return api('/api/tasks/' + id, jsonOptions('PATCH', payload)); },
  priority: function (id, payload) { return api('/api/tasks/' + id + '/priority', jsonOptions('PATCH', payload)); },
  activity: function (projectId) { return api('/api/activity' + (projectId ? '?project_id=' + encodeURIComponent(projectId) : '')); },
  businessRows: function () { return api('/api/business-plan/rows'); },
  portfolioRows: function (query) {
    var qs = new URLSearchParams(query || {}).toString();
    return api('/api/portfolio/rows' + (qs ? '?' + qs : ''));
  }
};
