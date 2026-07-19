import { byId, all, esc, compact, statusSlug, priorityChip, msg } from '../dom.js';
import { API } from '../api.js';
import { currentUserName, Store } from '../state.js';
import { PROSPECT_STAGES, BP_STAGES } from '../schema.js';
import { openDetail } from './detail.js';
import { refreshPortfolio } from './portfolio.js';

function prospectStages() { return (Store.meta && Store.meta.prospect_stages) || PROSPECT_STAGES; }
function bpStages() { return (Store.meta && Store.meta.bp_stages) || BP_STAGES; }

export function pipelineStageForProject(project, pipeline) {
  var current = project.current_stage || '';
  // Unrecognized stage coerces to the FIRST column for both pipelines, never
  // the most-advanced one (that misplaced fresh/legacy cards).
  var stages = pipeline === 'bp' ? bpStages() : prospectStages();
  return stages.indexOf(current) >= 0 ? current : stages[0];
}
export function renderPipeline(element, projects, stages, pipeline) {
  if (!element) return;
  var grouped = {};
  stages.forEach(function (stage) { grouped[stage] = []; });
  projects.forEach(function (project) {
    var stage = pipelineStageForProject(project, pipeline);
    if (grouped[stage]) grouped[stage].push(project);
  });
  element.innerHTML = stages.map(function (stage) {
    var cards = grouped[stage] || [];
    var cardHtml = cards.length ? cards.map(function (project) {
      var statusValue = project.overall_status || 'In Progress';
      var priorityValue = project.current_task_priority || 'Medium';
      return '<button type="button" class="pipeline-card status-' + statusSlug(statusValue) + '" data-project-id="' + project.project_id + '" data-pipeline="' + pipeline + '">' +
        '<strong>' + esc(project.project_name) + '</strong>' +
        '<span class="pipeline-card-component">' + esc(compact(project.current_task || 'No current component', 44)) + '</span>' +
        '<span class="pipeline-card-meta">' + '<span class="card-status">' + esc(statusValue) + '</span>' + (priorityValue !== 'Medium' ? priorityChip(priorityValue) : '') + '</span>' +
        '<span class="pipeline-card-assignee">' + esc(project.current_owner || 'Unassigned') + '</span>' +
        '</button>';
    }).join('') : '<div class="pipeline-empty">No leads / wells in this stage.</div>';
    return '<section class="pipeline-column"><header><h3>' + esc(stage) + '</h3><span>' + cards.length + '</span></header><div class="pipeline-cards">' + cardHtml + '</div></section>';
  }).join('');
  all('.pipeline-card', element).forEach(function (card) {
    card.addEventListener('click', function () {
      openDetail(Number(card.getAttribute('data-project-id')), card.getAttribute('data-pipeline'));
    });
  });
}

// The assignee filters use value '' for "All assignees"; the backend's
// owner_filter treats the literal 'All' as no-filter (any other value matches
// current_owner exactly), so '' maps to 'All' here.
function assigneeFilterValue(id) {
  var select = byId(id);
  return (select && select.value) || 'All';
}

export function refreshProspect() {
  var query = {
    status_filter: byId('prospect-status-filter').value,
    owner_filter: assigneeFilterValue('prospect-assignee-filter'),
    pipeline_filter: 'prospect'
  };
  API.projects(query).then(function (rows) {
    renderPipeline(byId('prospect-pipeline'), rows || [], prospectStages(), 'prospect');
  }).catch(function (error) { msg(error.message, 'error'); });
}
export function refreshBP() {
  var query = {
    status_filter: byId('bp-status-filter').value,
    owner_filter: assigneeFilterValue('bp-assignee-filter'),
    pipeline_filter: 'bp'
  };
  API.projects(query).then(function (projects) {
    var year = byId('bp-year-filter').value;
    var rows = (projects || []).filter(function (project) {
      return year === 'All' || String(project.business_plan_year || '') === year;
    });
    renderPipeline(byId('bp-pipeline'), rows, bpStages(), 'bp');
  }).catch(function (error) { msg(error.message, 'error'); });
}

export function refreshAllBoards() {
  refreshProspect();
  refreshBP();
  refreshPortfolio();
}

export function createLead(event) {
  event.preventDefault();
  var name = byId('new-lead-name').value.trim();
  if (!name) return msg('Lead Name is required.', 'error');
  // X/Y are UI-required only (the backend accepts leads without them); they
  // seed lead_x/lead_y, which also prefill the Staking well location fields.
  var leadX = byId('new-lead-x').value.trim();
  var leadY = byId('new-lead-y').value.trim();
  if (!leadX || !leadY) return msg('Lead X and Y locations are required.', 'error');
  var submitButton = event.target.querySelector('button[type="submit"]');
  if (submitButton) submitButton.disabled = true;
  API.create({ project_name: name, lead_x: leadX, lead_y: leadY, pipeline_type: 'prospect', changed_by: currentUserName() }).then(function (result) {
    byId('create-lead-form').reset();
    // Collapse the New Lead disclosure on success (main.js listens); keeps the
    // toggle-state logic out of pipeline.js.
    document.dispatchEvent(new CustomEvent('lead:created'));
    msg('Lead created.', 'success');
    refreshAllBoards();
    if (result.project_id) openDetail(result.project_id, 'prospect');
  }).catch(function (error) { msg(error.message, 'error'); }).finally(function () {
    if (submitButton) submitButton.disabled = false;
  });
}
