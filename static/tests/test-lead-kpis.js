// Tests for static/js/views/lead-kpis.js — the Card 1E KPI tiles.
//
// The fixtures are board payload rows exactly as GET /api/projects returns
// them: tracked_items / overall_status / assignees / field / pipeline_type and
// mean_gas_bcf are all server-derived (workflow/projects.py), so nothing here
// re-derives them.
//
// Two things this file exists to pin above all others:
//   1. Rounding happens ONCE, on the displayed number. Per-lead percentages
//      are never rounded and never averaged.
//   2. The KPI population is "filtered AND active", so the Status = Completed
//      filter legitimately empties it and the tiles read 0% / 0 / 0 BCF.
import { test, assert, fixture } from './harness.js';
import {
  initLeadKpis, renderLeadKpis, leadKpisHtml, isActiveLead, activeLeads,
  completedItemCount, dashboardCompletionRatio, dashboardCompletionPercent,
  totalActiveLeads, leadMeanGas, totalMeanOgip, formatOgip, TRACKED_ITEM_COUNT
} from '../js/views/lead-kpis.js';
import { initLeadFilters, setLeadRows, setLeadUsers } from '../js/views/lead-filters.js';

var KPI_ROOT = 'kpi-test-root';
var FILTER_ROOT = 'kpi-test-filters';

// The twelve tracked items a prospect card always carries, with the first
// `completed` of them marked done. `pending` marks that many of the REMAINDER
// as Pending Approval — work waiting on a supervisor, which must not count.
function items(completed, pending) {
  var list = [];
  for (var i = 0; i < TRACKED_ITEM_COUNT; i += 1) {
    var status = 'In Progress';
    if (i < completed) status = 'Completed';
    else if (i < completed + (pending || 0)) status = 'Pending Approval';
    list.push({ stage: 'Lead Assessment', label: 'Item ' + (i + 1), status: status });
  }
  return list;
}

function lead(name, options) {
  var extra = options || {};
  return {
    project_id: extra.project_id || 1,
    project_name: name,
    pipeline_type: extra.pipeline_type === undefined ? 'prospect' : extra.pipeline_type,
    field: extra.field === undefined ? name.split('-')[0] : extra.field,
    display_stage: extra.display_stage || 'Lead Assessment',
    overall_status: extra.overall_status || 'In Progress',
    assignees: extra.assignees || [],
    lead_priority: extra.lead_priority === undefined ? 'Medium' : extra.lead_priority,
    mean_gas_bcf: extra.mean_gas_bcf === undefined ? null : extra.mean_gas_bcf,
    tracked_items: items(extra.completed || 0, extra.pending || 0)
  };
}

// Mounts just the KPI row over `rows` and returns its container element.
function mount(rows) {
  var root = fixture('<div id="' + KPI_ROOT + '"></div>');
  initLeadKpis({ root: KPI_ROOT });
  if (rows) renderLeadKpis(rows);
  return root.querySelector('#' + KPI_ROOT);
}

// Mounts the filter row AND the KPI row wired exactly as main.js wires them:
// one onChange, one filtered rowset, both surfaces repainted from it.
function mountWired(rows, users) {
  var root = fixture('<div id="' + FILTER_ROOT + '"></div><div id="' + KPI_ROOT + '"></div>');
  initLeadKpis({ root: KPI_ROOT });
  var boardRenders = [];
  initLeadFilters({
    root: FILTER_ROOT,
    onChange: function (leads) {
      boardRenders.push(leads.length);   // stands in for renderLeadBoard
      renderLeadKpis(leads);
    }
  });
  setLeadUsers(users || []);
  setLeadRows(rows || []);
  return {
    filters: root.querySelector('#' + FILTER_ROOT),
    kpis: root.querySelector('#' + KPI_ROOT),
    boardRenders: boardRenders
  };
}

function donutLabel(host) {
  return host.querySelector('.kpi-donut').getAttribute('aria-label');
}
function donutText(host) {
  return host.querySelector('.kpi-donut-value').textContent;
}
function arc(host) {
  return host.querySelector('.kpi-donut-arc');
}
function tileValue(host, modifier) {
  return host.querySelector('.kpi-tile-' + modifier + ' .kpi-value').textContent;
}
function tileLabel(host, modifier) {
  return host.querySelector('.kpi-tile-' + modifier + ' .kpi-label').textContent;
}
function chooseFilter(host, key, value) {
  var group = host.querySelector('.lead-filter[data-filter="' + key + '"]');
  var match = Array.prototype.slice.call(group.querySelectorAll('.lf-option')).filter(function (option) {
    return option.getAttribute('data-value') === value;
  })[0];
  if (!match) throw new Error('no "' + value + '" option in the ' + key + ' menu');
  match.click();
  return match;
}

/* -------------------------------------------------------------------------
   The population: filtered AND active
   ------------------------------------------------------------------------- */

test('an in-progress prospect is active; a completed one is not', function () {
  assert.ok(isActiveLead(lead('GALV-1')));
  assert.ok(!isActiveLead(lead('GALV-2', { overall_status: 'Completed' })));
});

test('a lead that finished Pre-Well Delivery is excluded from every KPI', function () {
  // It has left the board for Portfolio Analysis as a Proposed record, so it
  // is neither an active lead nor part of the completion denominator, and its
  // OGIP belongs to the portfolio's total, not the board's.
  var rows = [
    lead('GALV-1', { completed: 6, mean_gas_bcf: 100 }),
    lead('GALV-2', { completed: 12, overall_status: 'Completed', mean_gas_bcf: 900 })
  ];
  assert.equal(totalActiveLeads(rows), 1);
  assert.equal(dashboardCompletionPercent(rows), 50);   // 6/12, not 18/24
  assert.equal(totalMeanOgip(rows), 100);
});

test('a BP well in the payload never counts as a lead', function () {
  var rows = [lead('GALV-1', { completed: 3 }),
              lead('WELL-9', { pipeline_type: 'bp', completed: 12, mean_gas_bcf: 500 })];
  assert.equal(totalActiveLeads(rows), 1);
  assert.equal(totalMeanOgip(rows), 0);
  assert.deepEqual(activeLeads(rows).map(function (row) { return row.project_name; }), ['GALV-1']);
});

test('an absent pipeline_type reads prospect, matching the server default', function () {
  assert.ok(isActiveLead(lead('GALV-1', { pipeline_type: '' })));
  assert.ok(isActiveLead(lead('GALV-2', { pipeline_type: undefined })));
});

/* -------------------------------------------------------------------------
   Completion — the formula
   ------------------------------------------------------------------------- */

test('only Completed tracked items count — Pending Approval does not', function () {
  // Six done, four more submitted and waiting on a supervisor. Waiting is not
  // finished: the lead reads 6/12, never 10/12.
  assert.equal(completedItemCount(lead('GALV-1', { completed: 6, pending: 4 })), 6);
  assert.equal(dashboardCompletionPercent([lead('GALV-1', { completed: 6, pending: 4 })]), 50);
});

test('per-lead 0 / 3 / 6 / 9 / 12 completed sums to one dashboard percentage', function () {
  var rows = [0, 3, 6, 9, 12].map(function (done, index) {
    return lead('GALV-' + index, { project_id: index + 1, completed: done });
  });
  // 30 completed items over 5 leads x 12 = 60 slots.
  assert.equal(dashboardCompletionRatio(rows), 0.5);
  assert.equal(dashboardCompletionPercent(rows), 50);
});

test('a fully completed set of active leads reads 100%', function () {
  var rows = [lead('GALV-1', { completed: 12 }), lead('GALV-2', { project_id: 2, completed: 12 })];
  assert.equal(dashboardCompletionPercent(rows), 100);
});

test('the denominator is always twelve per lead, not the array length', function () {
  var thin = lead('GALV-1', { completed: 3 });
  thin.tracked_items = thin.tracked_items.slice(0, 6);   // a truncated payload
  // 3 of 12, not 3 of 6: lost items are unfinished work, not a shorter pipeline.
  assert.equal(dashboardCompletionPercent([thin]), 25);
});

test('completion keeps FULL precision until the single final rounding', function () {
  // Five leads at 1/12 plus one at 7/12: the honest figure is 12/72 = 16.67%,
  // which rounds ONCE to 17%. Rounding each lead first (8,8,8,8,8,58) and
  // averaging those would report 16% — this is the case that tells them apart.
  var rows = [];
  for (var i = 0; i < 5; i += 1) rows.push(lead('GALV-' + i, { project_id: i + 1, completed: 1 }));
  rows.push(lead('GALV-9', { project_id: 9, completed: 7 }));
  assert.equal(dashboardCompletionRatio(rows), 12 / 72);
  assert.ok(Math.abs(dashboardCompletionRatio(rows) - 0.1667) < 0.001,
            'ratio kept unrounded: ' + dashboardCompletionRatio(rows));
  assert.equal(dashboardCompletionPercent(rows), 17);
});

test('an intermediate ratio is never pre-rounded to whole percents', function () {
  // Three leads at 1/12 = 3/36. A per-lead round would make this exactly 0.08.
  var rows = [1, 1, 1].map(function (done, index) {
    return lead('GALV-' + index, { project_id: index + 1, completed: done });
  });
  assert.equal(dashboardCompletionRatio(rows), 3 / 36);
  assert.ok(dashboardCompletionRatio(rows) !== 0.08, 'ratio was pre-rounded');
  assert.equal(dashboardCompletionPercent(rows), 8);
});

test('an empty population is 0%, never NaN or Infinity', function () {
  [[], [lead('GALV-1', { overall_status: 'Completed' })]].forEach(function (rows) {
    var percent = dashboardCompletionPercent(rows);
    assert.equal(percent, 0);
    assert.ok(isFinite(percent), 'percent must be finite');
  });
});

/* -------------------------------------------------------------------------
   Total Mean OGIP
   ------------------------------------------------------------------------- */

test('a missing or unusable mean gas contributes 0, never NaN', function () {
  assert.equal(leadMeanGas(lead('GALV-1')), 0);                              // null
  assert.equal(leadMeanGas(lead('GALV-2', { mean_gas_bcf: undefined })), 0); // absent
  assert.equal(leadMeanGas(lead('GALV-3', { mean_gas_bcf: '' })), 0);        // blank
  var rows = [lead('GALV-1', { mean_gas_bcf: 120 }), lead('GALV-2', { project_id: 2 })];
  assert.equal(totalMeanOgip(rows), 120);
});

test('OGIP sums at full precision and rounds only for display', function () {
  var rows = [0.4, 0.4, 0.4].map(function (value, index) {
    return lead('GALV-' + index, { project_id: index + 1, mean_gas_bcf: value });
  });
  // Rounding each lead first would give 0 + 0 + 0 = "0 BCF"; the honest sum is
  // 1.2, which rounds once to 1.
  assert.ok(Math.abs(totalMeanOgip(rows) - 1.2) < 1e-9, 'sum: ' + totalMeanOgip(rows));
  assert.equal(formatOgip(totalMeanOgip(rows)), '1 BCF');
});

test('OGIP display uses thousands separators and whole BCF', function () {
  assert.equal(formatOgip(0), '0 BCF');
  assert.equal(formatOgip(859.6), (860).toLocaleString() + ' BCF');
  assert.equal(formatOgip(12345.4), (12345).toLocaleString() + ' BCF');
});

test('an empty population reads 0 BCF, never blank', function () {
  assert.equal(formatOgip(totalMeanOgip([])), '0 BCF');
  var host = mount([]);
  assert.equal(tileValue(host, 'ogip'), '0 BCF');
});

/* -------------------------------------------------------------------------
   Rendering
   ------------------------------------------------------------------------- */

test('the row renders the donut and both number tiles with their labels', function () {
  var rows = [lead('GALV-1', { completed: 6, mean_gas_bcf: 500 }),
              lead('GALV-2', { project_id: 2, completed: 6, mean_gas_bcf: 360 })];
  var host = mount(rows);
  assert.equal(donutText(host), '50%');
  assert.equal(tileValue(host, 'count'), '2');
  assert.equal(tileLabel(host, 'count'), 'Total Active Leads');
  assert.equal(tileValue(host, 'ogip'), (860).toLocaleString() + ' BCF');
  assert.equal(tileLabel(host, 'ogip'), 'Total Mean OGIP');
});

test('the KPI row never shows an x/12 fraction', function () {
  var host = mount([lead('GALV-1', { completed: 6 })]);
  assert.ok(host.textContent.indexOf('/12') < 0, 'found an x/12 in: ' + host.textContent);
  assert.ok(host.textContent.indexOf('/ 12') < 0, 'found an x / 12 in: ' + host.textContent);
});

test('the donut arc length IS the percentage, starting at twelve o\'clock', function () {
  var host = mount([lead('GALV-1', { completed: 3 })]);
  assert.equal(arc(host).getAttribute('stroke-dasharray'), '25 75');
  assert.match(arc(host).getAttribute('transform'), /rotate\(-90/);
});

test('0% renders an EMPTY ring — no arc element at all', function () {
  // A zero-length dash with a round linecap draws a dot, which would read as a
  // sliver of progress on a board where nothing is done.
  var host = mount([lead('GALV-1', { completed: 0 })]);
  assert.equal(donutText(host), '0%');
  assert.equal(arc(host), null);
  assert.ok(host.querySelector('.kpi-donut-track'), 'the track still renders');
});

test('the donut carries the percentage in its accessible name', function () {
  var host = mount([lead('GALV-1', { completed: 9 })]);
  assert.equal(donutLabel(host), 'Dashboard completion 75%');
  assert.equal(host.querySelector('.kpi-donut').getAttribute('role'), 'img');
  // The visible numeral is hidden from AT so the value is announced once.
  assert.equal(host.querySelector('.kpi-donut-value').getAttribute('aria-hidden'), 'true');
});

test('initLeadKpis paints zeros before any payload arrives', function () {
  var root = fixture('<div id="' + KPI_ROOT + '"></div>');
  initLeadKpis({ root: KPI_ROOT });
  var host = root.querySelector('#' + KPI_ROOT);
  assert.equal(donutText(host), '0%');
  assert.equal(tileValue(host, 'count'), '0');
  assert.equal(tileValue(host, 'ogip'), '0 BCF');
});

test('leadKpisHtml is pure — same rows, same markup, no DOM needed', function () {
  var rows = [lead('GALV-1', { completed: 4, mean_gas_bcf: 12.5 })];
  assert.equal(leadKpisHtml(rows), leadKpisHtml(rows));
});

/* -------------------------------------------------------------------------
   Recalculation on filter change (wired exactly as main.js wires it)
   ------------------------------------------------------------------------- */

test('every KPI recomputes from the same filtered rowset the board renders', function () {
  var rows = [
    lead('GALV-1', { project_id: 1, completed: 12, mean_gas_bcf: 300, assignees: ['R. Khalid'] }),
    lead('GALV-2', { project_id: 2, completed: 0, mean_gas_bcf: 100, assignees: ['S. Ali'] }),
    lead('LUNA-1', { project_id: 3, completed: 6, mean_gas_bcf: 60, assignees: ['S. Ali'] })
  ];
  var wired = mountWired(rows, [{ name: 'R. Khalid' }, { name: 'S. Ali' }]);

  // Unfiltered: 3 leads, 18/36 items, 460 BCF.
  assert.equal(tileValue(wired.kpis, 'count'), '3');
  assert.equal(donutText(wired.kpis), '50%');
  assert.equal(tileValue(wired.kpis, 'ogip'), '460 BCF');

  // Filter to one assignee: the board and the tiles move together, off the
  // one rowset the single onChange received.
  chooseFilter(wired.filters, 'assignee', 'S. Ali');
  assert.equal(wired.boardRenders[wired.boardRenders.length - 1], 2);
  assert.equal(tileValue(wired.kpis, 'count'), '2');
  assert.equal(donutText(wired.kpis), '25%');           // 6 of 24
  assert.equal(tileValue(wired.kpis, 'ogip'), '160 BCF');
});

test('a filter that matches nothing zeroes all three tiles', function () {
  var rows = [lead('GALV-1', { completed: 12, mean_gas_bcf: 300, assignees: ['R. Khalid'] })];
  var wired = mountWired(rows, [{ name: 'R. Khalid' }, { name: 'S. Ali' }]);
  chooseFilter(wired.filters, 'assignee', 'S. Ali');
  assert.equal(tileValue(wired.kpis, 'count'), '0');
  assert.equal(donutText(wired.kpis), '0%');
  assert.equal(arc(wired.kpis), null);
  assert.equal(tileValue(wired.kpis, 'ogip'), '0 BCF');
});

test('Status = Completed empties the KPI population: 0% / 0 / 0 BCF', function () {
  // NOT a miscount. The Status filter can select completed leads (Card 1C
  // ships that option and the board shows their cards), but a completed lead
  // is by definition not active — so the intersection "filtered AND active" is
  // empty and all three tiles honestly read zero while cards are still on
  // screen. Changing this would mean either hiding those cards or counting
  // records that have already left for Portfolio Analysis.
  var rows = [
    lead('GALV-1', { project_id: 1, completed: 12, overall_status: 'Completed', mean_gas_bcf: 900 }),
    lead('GALV-2', { project_id: 2, completed: 4, mean_gas_bcf: 100 })
  ];
  var wired = mountWired(rows);
  assert.equal(tileValue(wired.kpis, 'count'), '1');   // only the running lead

  chooseFilter(wired.filters, 'status', 'Completed');
  assert.equal(wired.boardRenders[wired.boardRenders.length - 1], 1);  // a card IS shown
  assert.equal(tileValue(wired.kpis, 'count'), '0');
  assert.equal(donutText(wired.kpis), '0%');
  assert.equal(donutLabel(wired.kpis), 'Dashboard completion 0%');
  assert.equal(tileValue(wired.kpis, 'ogip'), '0 BCF');

  // Clearing the filter brings the running lead's numbers straight back.
  wired.filters.querySelector('.lf-clear').click();
  assert.equal(tileValue(wired.kpis, 'count'), '1');
  assert.equal(donutText(wired.kpis), '33%');          // 4 of 12
  assert.equal(tileValue(wired.kpis, 'ogip'), '100 BCF');
});
