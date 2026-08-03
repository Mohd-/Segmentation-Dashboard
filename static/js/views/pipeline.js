import { byId, all, esc, compact, statusSlug, priorityChip, msg } from '../dom.js';
import { ICONS } from '../icons.js';
import { API } from '../api.js';
import { Store } from '../state.js';
import { PROSPECT_STAGES, BP_STAGES } from '../schema.js';
import { openDetail } from './detail.js';
import { refreshPortfolio } from './portfolio.js';
import { setLeadRows } from './lead-filters.js';

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
      // The chip flags an ESCALATION, so the resting priorities carry none.
      // 'Medium' was the creation default before Card 1D and 'Low' is it now,
      // which is why both are silent here — otherwise every newly created BP
      // well would wear a permanent LOW badge that means nothing. Priority is
      // a LEAD/WELL-LEVEL attribute (stored projects.priority, delivered as
      // lead_priority), so the card reads the record's own value.
      var priorityValue = project.lead_priority || 'Medium';
      var isDefaultPriority = priorityValue === 'Medium' || priorityValue === 'Low';
      return '<button type="button" class="pipeline-card status-' + statusSlug(statusValue) + '" data-project-id="' + project.project_id + '" data-pipeline="' + pipeline + '">' +
        '<strong>' + esc(project.project_name) + '</strong>' +
        '<span class="pipeline-card-component">' + esc(compact(project.current_task || 'No current component', 44)) + '</span>' +
        '<span class="pipeline-card-meta">' + '<span class="card-status">' + esc(statusValue) + '</span>' + (isDefaultPriority ? '' : priorityChip(priorityValue)) + '</span>' +
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

/* =========================================================================
   Card 1B — the prospect LEAD BOARD (three workflow columns, redesigned cards)

   Presentation only. The columns are the three DISPLAY stages the server maps
   the stored stage groups onto (display_stage), and each card shows the four
   tracked items belonging to its own column (tracked_items) — both derived at
   read time in workflow/projects.py, nothing stored. The BP board keeps using
   renderPipeline above, untouched.
   ========================================================================= */

// Column order, and the header glyph for each. These three strings must match
// the server's _DISPLAY_STAGE_BY_STAGE values.
export var DISPLAY_STAGES = ['Lead Assessment', 'Risk Analysis', 'Pre-Well Delivery'];
var STAGE_HEADER_ICONS = {
  'Lead Assessment': 'clipboard-check',
  'Risk Analysis': 'gauge',
  'Pre-Well Delivery': 'rig'
};

// Tracked-item status -> dot glyph + modifier class. Each status has its OWN
// SHAPE (check / dash / empty ring), so the dots stay readable without color.
var ITEM_DOTS = {
  'Completed': { icon: 'circle-check', slug: 'completed' },
  'Pending Approval': { icon: 'circle-minus', slug: 'pending' },
  'In Progress': { icon: 'circle', slug: 'in-progress' }
};

// Card border color + column order. Unknown/absent reads Low (gray), matching
// the server's own default.
var PRIORITY_RANK = { High: 0, Medium: 1, Low: 2 };
function priorityRank(project) {
  var rank = PRIORITY_RANK[project.lead_priority];
  return rank === undefined ? PRIORITY_RANK.Low : rank;
}

export function leadDisplayStage(project) {
  // Unrecognized stage coerces to the FIRST column, never the most advanced
  // one — same rule renderPipeline uses.
  var stage = project.display_stage || '';
  return DISPLAY_STAGES.indexOf(stage) >= 0 ? stage : DISPLAY_STAGES[0];
}

// High -> Medium -> Low, STABLE inside one priority: the server's order is the
// tiebreak (decorate/undecorate, so it holds on every engine).
function byPriority(projects) {
  return projects.map(function (project, index) { return { project: project, index: index }; })
    .sort(function (a, b) {
      var delta = priorityRank(a.project) - priorityRank(b.project);
      return delta !== 0 ? delta : a.index - b.index;
    })
    .map(function (entry) { return entry.project; });
}

function assigneesHtml(project) {
  var people = project.assignees || [];
  if (!people.length) {
    // Deliberately icon-less: "Unassigned" is the absence of a person, not a
    // person named Unassigned.
    return '<span class="lead-person lead-person-empty">Unassigned</span>';
  }
  return people.map(function (name) {
    return '<span class="lead-person">' +
      '<span class="lead-person-icon" aria-hidden="true">' + ICONS.user + '</span>' +
      '<span class="lead-person-name">' + esc(name) + '</span>' +
      '</span>';
  }).join('');
}

function trackedItemsHtml(project, stage) {
  return (project.tracked_items || []).filter(function (item) {
    return item.stage === stage;
  }).map(function (item) {
    var dot = ITEM_DOTS[item.status] || ITEM_DOTS['In Progress'];
    return '<span class="lead-item">' +
      '<span class="lead-dot lead-dot-' + dot.slug + '" role="img" aria-label="' + esc(item.status) +
      '" title="' + esc(item.label + ' — ' + item.status) + '">' + ICONS[dot.icon] + '</span>' +
      '<span class="lead-item-label">' + esc(item.label) + '</span>' +
      '</span>';
  }).join('');
}

function leadCardHtml(project, stage) {
  var priority = PRIORITY_RANK[project.lead_priority] === undefined ? 'Low' : project.lead_priority;
  return '<button type="button" class="lead-card lead-card-' + statusSlug(priority) + '"' +
    ' data-project-id="' + project.project_id + '" data-pipeline="prospect"' +
    ' data-priority="' + esc(priority) + '">' +
    '<span class="lead-card-identity">' +
      '<span class="lead-card-name">' + esc(project.project_name) + '</span>' +
      '<span class="lead-card-people">' + assigneesHtml(project) + '</span>' +
    '</span>' +
    '<span class="lead-card-items">' + trackedItemsHtml(project, stage) + '</span>' +
    '</button>';
}

export function renderLeadBoard(element, projects) {
  if (!element) return;
  element.classList.add('lead-board');
  var grouped = {};
  DISPLAY_STAGES.forEach(function (stage) { grouped[stage] = []; });
  (projects || []).forEach(function (project) { grouped[leadDisplayStage(project)].push(project); });
  element.innerHTML = DISPLAY_STAGES.map(function (stage) {
    var cards = byPriority(grouped[stage]);
    var body = cards.length
      ? cards.map(function (project) { return leadCardHtml(project, stage); }).join('')
      : '<div class="pipeline-empty">No leads in this stage.</div>';
    return '<section class="lead-column">' +
      '<header>' +
        '<span class="lead-column-icon" aria-hidden="true">' + ICONS[STAGE_HEADER_ICONS[stage]] + '</span>' +
        '<h3>' + esc(stage) + '</h3>' +
        '<span class="lead-column-count">' + cards.length + '</span>' +
      '</header>' +
      '<div class="lead-cards">' + body + '</div>' +
      '</section>';
  }).join('');
  all('.lead-card', element).forEach(function (card) {
    card.addEventListener('click', function () {
      openDetail(Number(card.getAttribute('data-project-id')), 'prospect');
    });
  });
}

// The BP board's assignee select uses value '' for "All assignees"; the
// backend's owner_filter treats the literal 'All' as no-filter (any other
// value matches current_owner exactly), so '' maps to 'All' here. (The
// prospect board no longer server-filters at all -- see refreshProspect.)
function assigneeFilterValue(id) {
  var select = byId(id);
  return (select && select.value) || 'All';
}

/* Card 1C: the lead board fetches its dataset UNFILTERED, once per refresh,
   and every filter is applied client-side by views/lead-filters.js -- the one
   place that decides which leads are on the board (cards, column badges, and
   later the KPIs all read the same filtered rowset). `include_completed`
   opts out of the server's "a fully matured lead leaves the board" rule,
   because the board now offers Completed as an explicit status and shows
   those leads by default. Rendering happens through the filter module's
   onChange (wired in main.js), never straight from this response. */
export function refreshProspect() {
  return API.projects({ pipeline_filter: 'prospect', include_completed: '1' })
    .then(function (rows) { setLeadRows(rows || []); })
    .catch(function (error) { msg(error.message, 'error'); });
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

/* Lead creation moved out to views/lead-create.js with Card 1D: the Add New
   Lead control is an interaction of its own (expand / Enter / Escape / inline
   validation), not a board concern. It calls refreshAllBoards above on success,
   so the new lead still lands through the same setLeadRows pipeline. */
