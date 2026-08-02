/* =========================================================================
   Card 2A — THE SHARED LEAD SUMMARY COMPONENT.

   One block, rendered identically on every detailed LEAD workflow page (Lead
   Assessment today; Risk Analysis and Pre-Well Delivery as those pages land).
   It is deliberately the whole card — its own header + gear, its progress bar,
   its five sections and its footer — so a page adopts it by dropping
   `leadSummaryHtml(data)` into a container and calling `wireLeadSummary()`.

   PURE BY CONTRACT: leadSummaryHtml() reads NO application state. It never
   imports Store, never fetches, never looks at the DOM. Its caller
   (views/detail.js) resolves every value and hands in ONE plain object:

     {
       progress: { completed: <n>, total: 12 },   // tracked items, from the
                                                  // SAME derivation the board
                                                  // KPI donut uses -- there is
                                                  // no second formula here
       gas:       { p90, mean, p10 },             // BCF
       liquid:    { p90, mean, p10 } | null,      // MMSTB; NULL hides the whole
                                                  // section and the sections
                                                  // below simply reflow up
       thickness: { formation, reservoir },       // ft, FINAL values (never TWT)
       area:      { p90, p10 },                   // km²
       cos:       { reservoir, trap, seal, total },// %, total is the derived one
       block:     '<seismic block>',              // footer reference, may be ''
       ar:        '<AR number>',                  //   "        "        "
       canManage: <bool>                          // gear enabled?
     }

   EVERY unavailable value renders as an em dash (—). A section is never hidden
   for being empty; only `liquid: null` (the lead's saved scenario is not a
   condensate one) removes a section outright.

   The card is NOT collapsible: it has no folds and no chevron. Everything the
   old card hid behind a gear that does not belong to a LEAD (the Active Well
   flag, the promote-to-BP-well move) now lives on the well side -- see
   views/detail.js's relocation note.
   ========================================================================= */
import { byId, all, esc, isFilled, fmtNum } from '../dom.js';
import { ICONS } from '../icons.js';

// The one "no value" glyph of this card.
export var EM_DASH = '—';

// A value cell: rounded for display (fmtNum passes text through untouched) or
// an em dash. Never blank -- a blank cell reads as a layout bug, a dash reads
// as "not recorded yet".
function value(raw) {
  return isFilled(raw) ? esc(fmtNum(raw)) : EM_DASH;
}

/* One section: a left-aligned heading over N evenly distributed value columns
   (sub-label above its value). The columns share one grid template, so P90 /
   Mean / P10 line up with Formation / Reservoir and with RES. / Trap / Seal /
   Total down the whole card. */
function section(title, columns) {
  var cells = columns.map(function (column) {
    return '<div class="ls-col">' +
      '<span class="ls-col-label">' + esc(column.label) + '</span>' +
      '<span class="ls-col-value">' + value(column.value) + '</span>' +
      '</div>';
  }).join('');
  return '<section class="ls-section">' +
    '<h4 class="ls-section-title">' + esc(title) + '</h4>' +
    '<div class="ls-grid" style="grid-template-columns:repeat(' + columns.length + ',minmax(0,1fr))">' +
    cells + '</div></section>';
}

/* The progress bar: a slim track with the percentage and the raw count at its
   RIGHT end, exactly as the board's KPI donut reports the same ratio. The
   percentage is computed here from the two numbers handed in -- the caller
   supplies the completed/total pair, this file never decides what "completed"
   means. */
export function progressPercent(progress) {
  var done = Number((progress || {}).completed);
  var total = Number((progress || {}).total);
  if (!isFinite(done) || !isFinite(total) || total <= 0) return 0;
  return Math.round((Math.min(Math.max(done, 0), total) / total) * 100);
}

function progressHtml(progress) {
  var percent = progressPercent(progress);
  var done = Number((progress || {}).completed) || 0;
  var total = Number((progress || {}).total) || 0;
  return '<div class="ls-progress">' +
    '<div class="ls-progress-track"><span style="width:' + percent + '%"></span></div>' +
    '<div class="ls-progress-figures"><b>' + percent + '%</b>' +
    '<small>' + done + ' / ' + total + '</small></div></div>';
}

/* Footer: the lead's seismic reference, left-aligned. Both halves come from the
   saved lead record (the primary Reservoir CoS row's block + AR number); either
   half missing renders as a dash, and a record with neither renders one dash. */
function footerHtml(block, ar) {
  if (!isFilled(block) && !isFilled(ar)) {
    return '<footer class="ls-footer"><span class="ls-footer-value">' + EM_DASH + '</span></footer>';
  }
  return '<footer class="ls-footer">' +
    '<span class="ls-footer-value">' + (isFilled(block) ? esc(block) : EM_DASH) + '</span>' +
    '<span class="ls-footer-sep" aria-hidden="true">|</span>' +
    '<span class="ls-footer-value">' + (isFilled(ar) ? 'AR-' + esc(ar) : EM_DASH) + '</span>' +
    '</footer>';
}

/* The gear menu: EXACTLY three items, in this order. Active Well and Promote to
   BP Well are deliberately absent -- neither is a lead-summary concern; both
   were relocated (see views/detail.js). Closed at render time, always. */
function menuHtml(canManage) {
  var disabled = canManage ? '' : ' disabled';
  return '<div id="lead-summary-menu" class="ls-menu hidden" role="menu" aria-labelledby="lead-summary-gear">' +
    '<button id="lead-summary-edit-all" type="button" class="ls-menu-item" role="menuitem"' + disabled + '>Edit All Inputs</button>' +
    '<button id="lead-summary-rename" type="button" class="ls-menu-item" role="menuitem"' + disabled + '>Rename Lead</button>' +
    '<button id="lead-summary-delete" type="button" class="ls-menu-item ls-menu-item-danger" role="menuitem"' + disabled + '>Delete Lead</button>' +
    '</div>';
}

/* -------------------------------------------------------------------------
   The component (pure)
   ------------------------------------------------------------------------- */

export function leadSummaryHtml(data) {
  var d = data || {};
  var gas = d.gas || {};
  var thickness = d.thickness || {};
  var area = d.area || {};
  var cos = d.cos || {};
  var canManage = d.canManage !== false;

  var liquidSection = '';
  if (d.liquid) {
    liquidSection = section('Liquid (MMSTB)', [
      { label: 'P90', value: d.liquid.p90 },
      { label: 'Mean', value: d.liquid.mean },
      { label: 'P10', value: d.liquid.p10 }
    ]);
  }

  return '<div class="ls-card">' +
    '<div class="ls-head">' +
      '<h3 class="ls-title">Lead Summary</h3>' +
      '<button id="lead-summary-gear" type="button" class="icon-btn ls-gear"' +
        ' aria-haspopup="menu" aria-expanded="false"' +
        ' title="' + (canManage ? 'Manage lead' : 'Return to the current pipeline to manage this lead') + '"' +
        ' aria-label="' + (canManage ? 'Manage lead' : 'Return to the current pipeline to manage this lead') + '"' +
        (canManage ? '' : ' disabled') + '>' + ICONS.settings + '</button>' +
    '</div>' +
    progressHtml(d.progress) +
    section('Gas (BCF)', [
      { label: 'P90', value: gas.p90 },
      { label: 'Mean', value: gas.mean },
      { label: 'P10', value: gas.p10 }
    ]) +
    liquidSection +
    section('Thickness (ft)', [
      { label: 'Formation', value: thickness.formation },
      { label: 'Reservoir', value: thickness.reservoir }
    ]) +
    section('Reservoir Area (km²)', [
      { label: 'P90', value: area.p90 },
      { label: 'P10', value: area.p10 }
    ]) +
    section('Chance of Success (%)', [
      { label: 'RES.', value: cos.reservoir },
      { label: 'Trap', value: cos.trap },
      { label: 'Seal', value: cos.seal },
      { label: 'Total', value: cos.total }
    ]) +
    footerHtml(d.block, d.ar) +
    menuHtml(canManage) +
    '</div>';
}

/* -------------------------------------------------------------------------
   Wiring

   The card is re-rendered wholesale after every save/refresh, so the in-card
   listeners are bound per render (the elements are new each time). The two
   DOCUMENT-level dismissals are registered exactly once for the lifetime of the
   page and resolve the popover by id at event time, so no listeners stack.
   ------------------------------------------------------------------------- */

export function closeLeadSummaryMenu() {
  var menu = byId('lead-summary-menu');
  if (menu) menu.classList.add('hidden');
  var gear = byId('lead-summary-gear');
  if (gear) gear.setAttribute('aria-expanded', 'false');
}

function menuIsOpen() {
  var menu = byId('lead-summary-menu');
  return !!menu && !menu.classList.contains('hidden');
}

var dismissWired = false;
function wireDismissOnce() {
  if (dismissWired) return;
  dismissWired = true;
  document.addEventListener('click', function (event) {
    if (!menuIsOpen()) return;
    var menu = byId('lead-summary-menu');
    var gear = byId('lead-summary-gear');
    if ((menu && menu.contains(event.target)) || (gear && gear.contains(event.target))) return;
    closeLeadSummaryMenu();
  });
  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape' || !menuIsOpen()) return;
    closeLeadSummaryMenu();
    var gear = byId('lead-summary-gear');
    if (gear) gear.focus();
  });
}

/* `handlers` is { onEditAll, onRename, onDelete }; each is optional. Every item
   closes the menu BEFORE running its action, so a confirm dialog never opens
   behind a menu still hanging over the card. */
export function wireLeadSummary(handlers) {
  var actions = handlers || {};
  wireDismissOnce();
  var gear = byId('lead-summary-gear');
  if (gear) {
    gear.addEventListener('click', function (event) {
      event.stopPropagation();
      var menu = byId('lead-summary-menu');
      if (!menu) return;
      var opening = menu.classList.contains('hidden');
      menu.classList.toggle('hidden', !opening);
      gear.setAttribute('aria-expanded', String(opening));
    });
  }
  [['lead-summary-edit-all', actions.onEditAll],
   ['lead-summary-rename', actions.onRename],
   ['lead-summary-delete', actions.onDelete]].forEach(function (entry) {
    var button = byId(entry[0]);
    if (!button || typeof entry[1] !== 'function') return;
    button.addEventListener('click', function () {
      closeLeadSummaryMenu();
      entry[1]();
    });
  });
  // Every render starts closed, whatever the previous one was doing.
  all('#lead-summary-menu').forEach(function (menu) { menu.classList.add('hidden'); });
  if (gear) gear.setAttribute('aria-expanded', 'false');
}
