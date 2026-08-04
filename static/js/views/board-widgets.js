/* =========================================================================
   Shared board-widget renderers -- pure markup builders used by BOTH the
   Segment Maturation board (views/lead-filters.js, views/lead-kpis.js,
   views/pipeline.js) and, from Card R-series on, the Business Plan board.
   Extracted verbatim/generalized out of those three modules (commit R1 of
   the BP-board-reuse plan): each function below is byte-identical to the
   markup its former owner produced for the inputs that owner passes today.
   No new CSS, no behavior change -- only the seam moved.
   ========================================================================= */
import { esc } from '../dom.js';
import { ICONS } from '../icons.js';

/* -------------------------------------------------------------------------
   Filter row (from views/lead-filters.js)
   ------------------------------------------------------------------------- */

// Shared by the maturation and BP boards' filter menus. Pure geometry: a
// position:fixed menu placed against the VIEWPORT (not the trigger's
// offsetParent) so a clipping ancestor (.pipeline-panel, .lead-column)
// cannot cut it off. Flips above the trigger when there is no room below.
export function placeFilterMenu(trigger, menu) {
  var margin = 8;
  var rect = trigger.getBoundingClientRect();
  menu.style.left = '0px';
  menu.style.top = '0px';
  menu.style.minWidth = Math.round(rect.width) + 'px';

  var width = menu.offsetWidth;
  var height = menu.offsetHeight;
  var left = rect.left;
  if (left + width > window.innerWidth - margin) left = window.innerWidth - width - margin;
  left = Math.max(margin, left);

  var below = rect.bottom + 4;
  var above = rect.top - height - 4;
  var top = (below + height > window.innerHeight - margin && above >= margin) ? above : below;
  menu.style.left = Math.round(left) + 'px';
  menu.style.top = Math.round(top) + 'px';
  menu.style.maxHeight = Math.min(320, Math.max(120, window.innerHeight - top - margin)) + 'px';
}

// The CLOSED control's trigger button. `key` is accepted (not currently
// rendered into the markup) so a future caller can wire it into an id/
// data-attribute without changing this signature; today's two boards don't
// need it. Callers own the surrounding menu markup and the wrapping div.
//
// `ariaCaption` is the caption as it should be SPOKEN. The default lowercases
// the caption, which reads right for ordinary words ("Filter by assignee") and
// wrong for an initialism ("Filter by bp gate") -- such a caption passes its
// own spelling instead.
export function filterTriggerHtml(params) {
  var caption = params.caption;
  var label = params.label;
  var active = params.active;
  var spoken = params.ariaCaption || caption.toLowerCase();
  return '<button type="button" class="lf-trigger' + (active ? ' is-active' : '') + '"' +
    ' aria-haspopup="true" aria-expanded="false" aria-label="Filter by ' + esc(spoken) + '">' +
    '<span class="lf-value">' + esc(label) + '</span>' +
    '<span class="lf-caret" aria-hidden="true">' + ICONS['chevron-down'] + '</span>' +
    '</button>';
}

// One option row inside a filter's menu. Real <button>s with checkbox/radio
// semantics: Space and Enter toggle them for free, and assistive tech reads
// the state from aria-checked.
export function filterOptionHtml(params) {
  var multi = params.multi;
  var chosen = params.chosen;
  var value = params.value;
  var icon = params.icon;
  var slug = params.slug;
  var strong = params.strong;
  var label = params.label;
  return '<button type="button" class="lf-option' + (chosen ? ' is-chosen' : '') +
    (strong ? ' lf-option-strong' : '') + '"' +
    ' role="' + (multi ? 'checkbox' : 'radio') + '" aria-checked="' + (chosen ? 'true' : 'false') + '"' +
    ' data-value="' + esc(value) + '">' +
    '<span class="lf-mark' + (multi ? ' lf-mark-box' : ' lf-mark-dot') + '" aria-hidden="true"></span>' +
    (icon
      ? '<span class="lf-option-icon' + (slug ? ' lf-icon-' + slug : '') + '" aria-hidden="true">' + ICONS[icon] + '</span>'
      : '') +
    '<span class="lf-option-label">' + esc(label) + '</span>' +
    '</button>';
}

/* -------------------------------------------------------------------------
   KPI tiles (from views/lead-kpis.js)
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
//
// `ariaLabel` is caller-supplied text, concatenated as-is (not esc()'d) --
// matching the maturation board's own literal 'Dashboard completion N%'
// string, which never needed escaping. A future caller passing untrusted
// text would need to esc() before calling.
export function kpiDonutHtml(percent, ariaLabel) {
  var arc = percent > 0
    ? '<circle class="kpi-donut-arc" cx="21" cy="21" r="15.9155"' +
      ' stroke-dasharray="' + percent + ' ' + (100 - percent) + '"' +
      ' transform="rotate(-90 21 21)"></circle>'
    : '';
  // role="img" + aria-label makes the tile one labelled object, so a screen
  // reader hears the percentage once instead of a stray "68%" with no subject.
  return '<div class="kpi-donut" role="img" aria-label="' + ariaLabel + '">' +
    '<svg class="kpi-donut-svg" viewBox="0 0 42 42" aria-hidden="true" focusable="false">' +
      '<circle class="kpi-donut-track" cx="21" cy="21" r="15.9155"></circle>' + arc +
    '</svg>' +
    '<span class="kpi-donut-value" aria-hidden="true">' + percent + '%</span>' +
    '</div>';
}

// One KPI tile. `support`, an OPTIONAL trailing <small class="kpi-support">
// line, is new with this extraction (for the BP board) -- omitted entirely
// when falsy, so the maturation board's three tiles (which never pass it)
// render byte-identical markup to before.
export function kpiTileHtml(value, label, modifier, icon, support) {
  return '<div class="kpi-tile' + (modifier ? ' ' + modifier : '') + '">' +
    (icon ? '<span class="kpi-icon" aria-hidden="true">' + ICONS[icon] + '</span>' : '') +
    '<div class="kpi-tile-text">' +
      '<b class="kpi-value">' + esc(value) + '</b>' +
      '<small class="kpi-label">' + esc(label) + '</small>' +
      (support ? '<small class="kpi-support">' + esc(support) + '</small>' : '') +
    '</div>' +
    '</div>';
}

/* -------------------------------------------------------------------------
   Lead-card people + tracked-item dots (from views/pipeline.js)
   ------------------------------------------------------------------------- */

// Tracked-item status -> dot glyph + modifier class. Each status has its OWN
// SHAPE (check / dash / empty ring), so the dots stay readable without color.
export var ITEM_DOTS = {
  'Completed': { icon: 'circle-check', slug: 'completed' },
  'Pending Approval': { icon: 'circle-minus', slug: 'pending' },
  'In Progress': { icon: 'circle', slug: 'in-progress' }
};

// The assignee chip row for a lead/well card. `names` is the array of
// assignee names (falsy/empty reads as Unassigned). Deliberately icon-less
// in the empty case: "Unassigned" is the absence of a person, not a person
// named Unassigned.
export function personChipsHtml(names) {
  var people = names || [];
  if (!people.length) {
    return '<span class="lead-person lead-person-empty">Unassigned</span>';
  }
  return people.map(function (name) {
    return '<span class="lead-person">' +
      '<span class="lead-person-icon" aria-hidden="true">' + ICONS.user + '</span>' +
      '<span class="lead-person-name">' + esc(name) + '</span>' +
      '</span>';
  }).join('');
}

// One tracked-item's dot -- role="img"/aria-label/title exactly as the
// maturation board renders today. Callers own the surrounding
// <span class="lead-item"> wrapper and the item's own visible label.
export function leadItemHtml(status, label) {
  var dot = ITEM_DOTS[status] || ITEM_DOTS['In Progress'];
  return '<span class="lead-dot lead-dot-' + dot.slug + '" role="img" aria-label="' + esc(status) +
    '" title="' + esc(label + ' — ' + status) + '">' + ICONS[dot.icon] + '</span>';
}
