import { byId, table, esc, msg, stamp } from '../dom.js';
import { API } from '../api.js';

export function auditChange(row) {
  var action = row.action_type || 'Update';
  var comment = row.comment || '';
  if (comment) return '<b>' + esc(action) + '</b><span class="audit-note">' + esc(comment) + '</span>';
  return esc(action);
}
export function refreshAudit() {
  API.projects({}).then(function (projects) {
    var filter = byId('audit-project-filter');
    var previous = filter.value;
    filter.innerHTML = '<option value="">All leads / wells</option>' + (projects || []).map(function (project) {
      return '<option value="' + project.project_id + '">' + esc(project.project_name) + '</option>';
    }).join('');
    filter.value = previous;
    return API.activity(filter.value);
  }).then(function (rows) {
    table(byId('audit-table'), ['When', 'Lead / Well', 'Component', 'Change', 'By'], (rows || []).map(function (row) {
      return [esc(row.changed_at || ''), esc(row.project_name || ''), esc(row.task_name || ''), auditChange(row), esc(row.changed_by || '')];
    }));
    stamp();
  }).catch(function (error) { msg(error.message, 'error'); });
}
