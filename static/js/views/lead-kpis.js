/* =========================================================================
   Card 1E — the three Segment Maturation KPI tiles.

     completion donut  |  Total Active Leads  |  Total Mean OGIP

   THE POPULATION IS THE SAME FOR ALL THREE: the leads that are currently
   FILTERED (Card 1C's one central selector) AND still ACTIVE. Nothing here
   re-filters the payload -- views/lead-filters.js owns that rule and hands the
   rowset in, so the tiles can never disagree with the cards and badges beside
   them. Everything is computed from LOCAL rows in the same handler chain the
   board renders from: no fetch, no stale-response race, no loading state.

   The ACTIVE-vs-FILTERED subtlety, spelled out because it looks like a bug:
   the Status filter offers "Completed", and a completed lead is by definition
   NOT active (it has left the board for the Portfolio as a Proposed record).
   So with Status = Completed the population is empty and the tiles honestly
   read 0% / 0 / 0 BCF. That is the specified behaviour, not a miscount --
   pinned by a test in static/tests/test-lead-kpis.js.

   Rounding happens EXACTLY ONCE, at the end, on the value being displayed.
   Per-lead percentages are never rounded and never averaged: five leads at
   1/12 plus one at 7/12 is 12/72 = 17%, where averaging rounded per-lead
   percentages would report 16%.
   ========================================================================= */
import { byId, esc } from '../dom.js';
import { ICONS } from '../icons.js';

// Every prospect card carries exactly twelve tracked items (the Card 1B
// presentation adapter, workflow/projects.py _TRACKED_ITEMS), and the
// denominator is that fixed twelve per active lead -- NOT the length of the
// array that happened to arrive. A payload that lost an item must read as
// incomplete work, not as a smaller pipeline that is easier to finish.
export var TRACKED_ITEM_COUNT = 12;

// Only this one status counts as done. "Pending Approval" is work waiting on a
// supervisor -- still open, still uncompleted.
var COMPLETED_STATUS = 'Completed';

var rootId = 'lead-kpi-row';

/* -------------------------------------------------------------------------
   The population (pure)
   ------------------------------------------------------------------------- */

// A lead counts toward every KPI when it is a PROSPECT that is still running.
// `overall_status` is derived server-side (_annotate_derived_state): a lead
// whose whole applicable pipeline is approved reads 'Completed' and has moved
// on to Portfolio Analysis as a Proposed record -- it is no longer part of the
// board's workload and must not inflate any of the three numbers.
// pipeline_type follows the server's own default ('' / absent = prospect).
export function isActiveLead(lead) {
  if (!lead) return false;
  if (String(lead.overall_status || '') === COMPLETED_STATUS) return false;
  return String(lead.pipeline_type || 'prospect').toLowerCase() === 'prospect';
}

export function activeLeads(leads) {
  return (leads || []).filter(isActiveLead);
}

// How many of ONE lead's twelve tracked items are done. Capped at the
// denominator so a malformed payload can never push the donut past 100%.
export function completedItemCount(lead) {
  var items = (lead && lead.tracked_items) || [];
  var done = 0;
  for (var i = 0; i < items.length; i += 1) {
    if (items[i] && items[i].status === COMPLETED_STATUS) done += 1;
  }
  return Math.min(done, TRACKED_ITEM_COUNT);
}

/* -------------------------------------------------------------------------
   The three formulas (pure)
   ------------------------------------------------------------------------- */

// Dashboard Completion as a RATIO in [0, 1], at full precision:
//
//   sum(completed tracked items over active leads) / (active leads x 12)
//
// One fraction over the whole population, never a mean of per-lead fractions.
// An empty population is 0, not a division by zero -- the tile shows 0% with
// an empty ring, and NaN/Infinity can never reach the DOM.
export function dashboardCompletionRatio(leads) {
  var active = activeLeads(leads);
  if (!active.length) return 0;
  var done = 0;
  active.forEach(function (lead) { done += completedItemCount(lead); });
  var ratio = done / (active.length * TRACKED_ITEM_COUNT);
  return isFinite(ratio) ? Math.min(Math.max(ratio, 0), 1) : 0;
}

// The displayed percentage: the single rounding of the whole calculation.
export function dashboardCompletionPercent(leads) {
  return Math.round(dashboardCompletionRatio(leads) * 100);
}

export function totalActiveLeads(leads) {
  return activeLeads(leads).length;
}

// One lead's mean gas as a number. mean_gas_bcf is derived server-side on the
// latest-assessment-first precedence (workflow.projects._annotate_mean_gas)
// and is explicitly null when nothing usable is stored -- which reads as 0
// here, so an unassessed lead contributes nothing instead of breaking the sum.
export function leadMeanGas(lead) {
  var value = Number(lead && lead.mean_gas_bcf);
  return isFinite(value) ? value : 0;
}

// Total Mean OGIP over the active population, at FULL precision -- the sum is
// rounded only where it is printed, so a hundred leads carrying decimals do
// not accumulate a hundred rounding errors.
export function totalMeanOgip(leads) {
  var total = 0;
  activeLeads(leads).forEach(function (lead) { total += leadMeanGas(lead); });
  return isFinite(total) ? total : 0;
}

// The OGIP tile's text: whole BCF with thousands separators. '0 BCF' when the
// population is empty or nothing has been assessed -- never blank, never NaN.
export function formatOgip(total) {
  var value = isFinite(total) ? Math.round(total) : 0;
  return value.toLocaleString() + ' BCF';
}

/* -------------------------------------------------------------------------
   Markup
   ------------------------------------------------------------------------- */

// The donut is a RADIAL METER, not a chart of parts: one value against a
// fixed limit, so it is one brand hue on a recessive neutral track and needs
// no legend and no hover layer.
//
// r = 15.9155 makes the circumference exactly 100, so stroke-dasharray is the
// percentage itself -- no arc maths, no path generation. The arc is rotated
// -90deg to start at twelve o'clock and runs clockwise.
//
// At 0% the arc element is OMITTED entirely rather than given a zero-length
// dash: a zero-length subpath with a round linecap renders as a DOT, which
// would read as "a sliver of progress" on a board with nothing done.
function donutMarkup(percent) {
  var arc = percent > 0
    ? '<circle class="kpi-donut-arc" cx="21" cy="21" r="15.9155"' +
      ' stroke-dasharray="' + percent + ' ' + (100 - percent) + '"' +
      ' transform="rotate(-90 21 21)"></circle>'
    : '';
  // role="img" + aria-label makes the tile one labelled object, so a screen
  // reader hears the percentage once instead of a stray "68%" with no subject.
  return '<div class="kpi-donut" role="img" aria-label="Dashboard completion ' + percent + '%">' +
    '<svg class="kpi-donut-svg" viewBox="0 0 42 42" aria-hidden="true" focusable="false">' +
      '<circle class="kpi-donut-track" cx="21" cy="21" r="15.9155"></circle>' + arc +
    '</svg>' +
    '<span class="kpi-donut-value" aria-hidden="true">' + percent + '%</span>' +
    '</div>';
}

function tileMarkup(value, label, modifier, icon) {
  return '<div class="kpi-tile' + (modifier ? ' ' + modifier : '') + '">' +
    (icon ? '<span class="kpi-icon" aria-hidden="true">' + ICONS[icon] + '</span>' : '') +
    '<div class="kpi-tile-text">' +
      '<b class="kpi-value">' + esc(value) + '</b>' +
      '<small class="kpi-label">' + esc(label) + '</small>' +
    '</div>' +
    '</div>';
}

export function leadKpisHtml(leads) {
  return donutMarkup(dashboardCompletionPercent(leads)) +
    tileMarkup(String(totalActiveLeads(leads)), 'Total Active Leads', 'kpi-tile-count') +
    tileMarkup(formatOgip(totalMeanOgip(leads)), 'Total Mean OGIP', 'kpi-tile-ogip', 'flame');
}

/* -------------------------------------------------------------------------
   Render + wiring
   ------------------------------------------------------------------------- */

/* Recompute and repaint from a filtered rowset. Called from main.js's single
   'leads:filtered' handler, straight after the board renders, over the SAME
   filteredLeads() array the cards were drawn from -- one event, one rowset,
   one pass. The data is already local, so this is synchronous: the tiles never
   flash a spinner or a stale number between filter changes. */
export function renderLeadKpis(leads) {
  var host = byId(rootId);
  if (!host) return;
  host.innerHTML = leadKpisHtml(leads || []);
}

/* Boot entry point (main.js). `options.root` names the container id (the tests
   mount their own). Renders immediately with an empty rowset so the row shows
   0% / 0 / 0 BCF from the first paint rather than an empty gap that fills in
   when the board's fetch lands. */
export function initLeadKpis(options) {
  var settings = options || {};
  rootId = settings.root || 'lead-kpi-row';
  renderLeadKpis([]);
}
