import { range } from '../dom.js';
import { API } from '../api.js';
import { currentRole } from '../state.js';
import { DONE } from '../schema.js';
import { confirmDialog } from '../dialog.js';

// Phase transitions: the ONLY way a record moves between the lead maturation
// pipeline and the Business Plan execution pipeline. Both helpers confirm via
// dialog, then PATCH /flags with business_plan_enabled — the one backend path
// that runs the full promote/recall side effects (snapshot, stage switch) and
// 403s non-supervisors. Callers own the post-transition refresh + messaging.

export function canTransitionPhase() {
  return currentRole() === 'supervisor';
}

// Promote a lead to a BP well. `prospectTasks` are the record's prospect-stage
// tasks (tasksForPipeline('prospect') at the call site) — used only for the
// N-of-M progress line and the early-promotion warning. Falsy/empty (e.g.
// portfolio mature-lead rows, which are 100% approved by definition) omits
// both lines. Resolves the /flags response on confirm, null on cancel.
export function promoteProject(project, prospectTasks, changedBy) {
  var tasks = prospectTasks || [];
  var approved = tasks.filter(function (task) { return DONE[task.status]; }).length;
  var year = Number(project.business_plan_year || new Date().getFullYear());
  if (year < 2026 || year > 2040) year = 2026;
  var lines = [];
  if (tasks.length) {
    lines.push(approved + ' of ' + tasks.length + ' prospect steps approved.');
    if (approved < tasks.length) {
      lines.push('Promoting now switches this record to the Well Delivery stages before maturation is complete.');
    }
  }
  lines.push('The record switches to the BP execution stages, a Lead Summary snapshot is captured, and the well appears in the Portfolio.');
  return confirmDialog({
    title: 'Promote to BP Well',
    message: lines.join('\n'),
    confirmLabel: 'Promote',
    selectLabel: 'Business Plan Year',
    selectOptions: range(2026, 2040),
    selectValue: String(year)
  }).then(function (selectedYear) {
    if (selectedYear === null) return null;
    return API.flags(project.project_id, {
      business_plan_enabled: true,
      business_plan_year: selectedYear,
      changed_by: changedBy
    });
  });
}

// Recall a BP well to the lead phase. Resolves the /flags response on confirm,
// null on cancel.
export function recallProject(project, changedBy) {
  var name = project.project_name || 'this well';
  return confirmDialog({
    title: 'Recall to Lead Phase',
    message: 'This removes "' + name + '" from the Business Plan.\nA fully matured lead (all prospect steps approved) stays in the Portfolio; otherwise the record returns to the maturation board exactly where it left off.\nAll entered data, audit history and the promotion snapshot are preserved.',
    confirmLabel: 'Recall',
    danger: true
  }).then(function (confirmed) {
    if (!confirmed) return null;
    return API.flags(project.project_id, {
      business_plan_enabled: false,
      changed_by: changedBy
    });
  });
}
