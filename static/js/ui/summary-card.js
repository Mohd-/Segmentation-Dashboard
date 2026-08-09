/* Shared, state-free markup primitives for Lead and Well summary cards.

   Callers own domain data, permissions, actions, ids, and event wiring. This
   module owns only the common anatomy so the Segment Maturation and Business
   Plan Execution summaries cannot drift into parallel lookalikes. */
import { esc, isFilled } from '../dom.js';
import { ICONS } from '../icons.js';

function classes(base, extra) {
  return base + (extra ? ' ' + extra : '');
}

export function summaryProgressPercent(progress) {
  var done = Number((progress || {}).completed);
  var total = Number((progress || {}).total);
  if (!isFinite(done) || !isFinite(total) || total <= 0) return 0;
  return Math.round((Math.min(Math.max(done, 0), total) / total) * 100);
}

export function summaryProgressHtml(progress) {
  var percent = summaryProgressPercent(progress);
  var done = Number((progress || {}).completed) || 0;
  var total = Number((progress || {}).total) || 0;
  return '<div class="ls-progress">' +
    '<div class="ls-progress-track"><span style="width:' + percent + '%"></span></div>' +
    '<div class="ls-progress-figures"><b>' + percent + '%</b>' +
    '<small>' + done + ' / ' + total + '</small></div></div>';
}

export function summaryPhaseHtml(phase) {
  var data = phase || {};
  var secondary = isFilled(data.secondary)
    ? '<span class="summary-phase-well" title="' + esc(data.secondaryTitle || '') + '">' +
      esc(data.secondary) + '</span>'
    : '';
  return '<div class="summary-phase"><span class="summary-phase-label">' +
    esc(data.label || '') + '</span>' + secondary + '</div>';
}

export function summaryGridHtml(columns, options) {
  var items = columns || [];
  var settings = options || {};
  var empty = settings.emptyText == null ? '—' : String(settings.emptyText);
  var cells = items.map(function (column) {
    var displayed = isFilled(column.value) ? column.value : empty;
    return '<div class="ls-col"><span class="ls-col-label">' + esc(column.label) + '</span>' +
      '<span class="ls-col-value">' + esc(displayed) + '</span></div>';
  }).join('');
  return '<div class="ls-grid" style="grid-template-columns:repeat(' +
    items.length + ',minmax(0,1fr))">' + cells + '</div>';
}

export function summarySectionHtml(title, bodyHtml, extraClass) {
  return '<section class="' + classes('ls-section', extraClass) + '">' +
    '<h4 class="ls-section-title">' + esc(title) + '</h4>' + (bodyHtml || '') + '</section>';
}

export function summaryFoldHtml(id, title, bodyHtml, folds, prefix) {
  var open = !!(folds || {})[id];
  var domId = (prefix || '') + 'summary-fold-' + id;
  return '<div class="summary-fold">' +
    '<button id="' + esc(domId) + '" type="button" class="summary-fold-head' + (open ? ' open' : '') +
    '" data-fold="' + esc(id) + '" aria-expanded="' + open + '" aria-controls="' + esc(domId) + '-body">' +
    '<span class="summary-fold-title">' + esc(title) + '</span>' +
    '<span class="summary-fold-chevron" aria-hidden="true">' + ICONS['chevron-down'] + '</span></button>' +
    '<div id="' + esc(domId) + '-body" class="summary-fold-body' + (open ? '' : ' collapsed') + '">' +
    (bodyHtml || '') + '</div></div>';
}

export function summaryHeaderHtml(title, actionHtml, titleId) {
  var id = titleId ? ' id="' + esc(titleId) + '"' : '';
  return '<div class="ls-head"><h3' + id + ' class="ls-title">' + esc(title) + '</h3>' +
    (actionHtml || '') + '</div>';
}

export function summaryCardHtml(options) {
  var data = options || {};
  var id = data.id ? ' id="' + esc(data.id) + '"' : '';
  return '<div' + id + ' class="' + classes('ls-card summary-card-shell', data.className) + '">' +
    summaryHeaderHtml(data.title || '', data.actionHtml, data.titleId) +
    (data.progress ? summaryProgressHtml(data.progress) : '') +
    (data.phase ? summaryPhaseHtml(data.phase) : '') +
    (data.bodyHtml || '') +
    (data.footerHtml || '') + (data.menuHtml || '') + '</div>';
}
