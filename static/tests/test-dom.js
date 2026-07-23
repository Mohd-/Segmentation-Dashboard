// Tests for static/js/dom.js — pure helpers plus the small DOM writers.
import { test, assert, fixture } from './harness.js';
import {
  esc, compact, fmtNum, truthy, range, isFilled, statusSlug,
  fillSelect, table, msg, statusChip, priorityChip, byId
} from '../js/dom.js';

// --- esc -------------------------------------------------------------------

test('dom.esc escapes &, <, > and "', function () {
  assert.equal(esc('a & b < c > d "e"'), 'a &amp; b &lt; c &gt; d &quot;e&quot;');
});

test('dom.esc: null and undefined become empty string', function () {
  assert.equal(esc(null), '');
  assert.equal(esc(undefined), '');
});

test('dom.esc stringifies non-strings and leaves safe text alone', function () {
  assert.equal(esc(12.5), '12.5');
  assert.equal(esc('plain text'), 'plain text');
});

// --- compact ---------------------------------------------------------------

test('dom.compact: blank/null render as dash', function () {
  assert.equal(compact(null), '-');
  assert.equal(compact(''), '-');
  assert.equal(compact(undefined), '-');
});

test('dom.compact truncates past the limit with an ellipsis', function () {
  assert.equal(compact('abcdefgh', 5), 'abcd…');
  assert.equal(compact('abcde', 5), 'abcde'); // exactly at limit: untouched
});

test('dom.compact default limit is 48', function () {
  var long = new Array(50).join('x'); // 49 chars
  assert.equal(compact(long).length, 48);
  assert.equal(compact(long), long.slice(0, 47) + '…');
  var ok = new Array(49).join('x'); // 48 chars
  assert.equal(compact(ok), ok);
});

// --- fmtNum ----------------------------------------------------------------

test('dom.fmtNum rounds to 1 decimal', function () {
  assert.equal(fmtNum('1.24'), '1.2');
  assert.equal(fmtNum(1.25), '1.3');
  assert.equal(fmtNum(2), '2');
  assert.equal(fmtNum('10.05'), '10.1');
  assert.equal(fmtNum(0), '0');
});

test('dom.fmtNum passes non-numeric and blank values through untouched', function () {
  assert.equal(fmtNum('abc'), 'abc');
  assert.equal(fmtNum(''), '');
  assert.equal(fmtNum(null), null);
  assert.equal(fmtNum(undefined), undefined);
  assert.equal(fmtNum('  '), '  ');
  assert.equal(fmtNum('Infinity'), 'Infinity'); // non-finite stays raw
});

// --- truthy / range / isFilled / statusSlug --------------------------------

test('dom.truthy recognises 1/true/yes/on case-insensitively', function () {
  ['1', 'true', 'YES', 'On', true, 1].forEach(function (v) {
    assert.equal(truthy(v), true, 'truthy(' + JSON.stringify(v) + ')');
  });
  ['0', 'no', '', null, undefined, 'off', 0, false].forEach(function (v) {
    assert.equal(truthy(v), false, 'truthy(' + JSON.stringify(v) + ')');
  });
});

test('dom.range builds inclusive string years', function () {
  assert.deepEqual(range(2026, 2028), ['2026', '2027', '2028']);
  assert.deepEqual(range(5, 5), ['5']);
  assert.deepEqual(range(5, 4), []);
});

test('dom.isFilled treats 0 and false as filled, blanks as not', function () {
  assert.equal(isFilled(0), true);
  assert.equal(isFilled(false), true); // String(false) === 'false'
  assert.equal(isFilled('x'), true);
  assert.equal(isFilled(''), false);
  assert.equal(isFilled('   '), false);
  assert.equal(isFilled(null), false);
  assert.equal(isFilled(undefined), false);
});

test('dom.statusSlug lowercases and hyphenates whitespace', function () {
  assert.equal(statusSlug('In Progress'), 'in-progress');
  assert.equal(statusSlug('Not   Assigned'), 'not-assigned');
  assert.equal(statusSlug(null), '-');
  assert.equal(statusSlug(''), '-');
});

// --- fillSelect ------------------------------------------------------------

test('dom.fillSelect fills options, no All by default', function () {
  var root = fixture('<select id="fx-select"></select>');
  var select = root.querySelector('select');
  fillSelect(select, ['One', 'Two']);
  var labels = Array.prototype.map.call(select.options, function (o) { return o.textContent; });
  assert.deepEqual(labels, ['One', 'Two']);
});

test('dom.fillSelect withAll prepends All', function () {
  var root = fixture('<select></select>');
  var select = root.querySelector('select');
  fillSelect(select, ['One'], true);
  assert.equal(select.options.length, 2);
  assert.equal(select.options[0].textContent, 'All');
});

test('dom.fillSelect preserves a prior selection still present', function () {
  var root = fixture('<select></select>');
  var select = root.querySelector('select');
  fillSelect(select, ['One', 'Two', 'Three']);
  select.value = 'Two';
  fillSelect(select, ['Two', 'Three', 'Four']);
  assert.equal(select.value, 'Two');
});

test('dom.fillSelect drops a prior selection that vanished (falls back to first)', function () {
  var root = fixture('<select></select>');
  var select = root.querySelector('select');
  fillSelect(select, ['One', 'Two']);
  select.value = 'Two';
  fillSelect(select, ['Alpha', 'Beta']);
  assert.equal(select.value, 'Alpha');
});

test('dom.fillSelect preserves the All selection', function () {
  var root = fixture('<select></select>');
  var select = root.querySelector('select');
  fillSelect(select, ['One'], true);
  select.value = 'All';
  fillSelect(select, ['Two'], true);
  assert.equal(select.value, 'All');
});

test('dom.fillSelect escapes option labels', function () {
  var root = fixture('<select></select>');
  var select = root.querySelector('select');
  fillSelect(select, ['<b>evil</b>']);
  assert.equal(select.options.length, 1);
  assert.equal(select.options[0].textContent, '<b>evil</b>'); // literal text, not markup
  assert.equal(select.querySelector('b'), null);
});

test('dom.fillSelect tolerates a null element', function () {
  fillSelect(null, ['One']); // must not throw
  assert.ok(true);
});

// --- table -----------------------------------------------------------------

test('dom.table renders escaped headings and one tr per row', function () {
  var root = fixture('<table></table>');
  var el = root.querySelector('table');
  table(el, ['Name', 'A<B'], [['x', 'y'], ['z', 'w']]);
  var ths = el.querySelectorAll('thead th');
  assert.equal(ths.length, 2);
  assert.equal(ths[1].textContent, 'A<B'); // esc applied → renders as text
  assert.equal(el.querySelectorAll('tbody tr').length, 2);
  assert.equal(el.querySelectorAll('tbody tr')[1].getAttribute('data-index'), '1');
});

test('dom.table empty rows render the empty state with full colspan', function () {
  var root = fixture('<table></table>');
  var el = root.querySelector('table');
  table(el, ['A', 'B', 'C'], []);
  var td = el.querySelector('tbody td.empty-state');
  assert.ok(td, 'empty-state cell exists');
  assert.equal(td.getAttribute('colspan'), '3');
  assert.equal(td.textContent, 'No records yet.');
});

test('dom.table row click reports the row index', function () {
  var root = fixture('<table></table>');
  var el = root.querySelector('table');
  var clicked = [];
  table(el, ['A'], [['first'], ['second']], function (index) { clicked.push(index); });
  el.querySelectorAll('tbody tr')[1].click();
  assert.deepEqual(clicked, [1]);
  el.querySelectorAll('tbody tr')[0].click();
  assert.deepEqual(clicked, [1, 0]);
});

test('dom.table inserts cell content as raw HTML (callers must pre-escape)', function () {
  var root = fixture('<table></table>');
  var el = root.querySelector('table');
  table(el, ['A'], [['<b class="markup">bold</b>']]);
  assert.ok(el.querySelector('tbody td b.markup'), 'cell HTML is parsed as markup');
});

test('dom.table tolerates a null element', function () {
  table(null, ['A'], []); // must not throw
  assert.ok(true);
});

// --- msg -------------------------------------------------------------------

test('dom.msg creates #app-message as first body child and sets class', function () {
  var existing = byId('app-message');
  if (existing) existing.remove();
  try {
    msg('saved ok', 'success');
    var el = byId('app-message');
    assert.ok(el, '#app-message exists');
    assert.equal(document.body.firstChild, el, 'inserted as first body child');
    assert.equal(el.className, 'app-message success');
    assert.equal(el.textContent, 'saved ok');
    msg('boom', 'error'); // reuses the same element
    assert.equal(byId('app-message'), el);
    assert.equal(el.className, 'app-message error');
    msg('note'); // default type is info
    assert.equal(el.className, 'app-message info');
  } finally {
    clearTimeout(msg.timer);
    var el2 = byId('app-message');
    if (el2) el2.remove();
  }
});

// --- chips -----------------------------------------------------------------

test('dom.statusChip escapes the label and slugs the class', function () {
  var root = fixture();
  root.innerHTML = statusChip('In Progress');
  var span = root.querySelector('span.status.in-progress');
  assert.ok(span, 'status + slug classes present');
  assert.equal(span.textContent, 'In Progress');
  // Escaping: markup in the value must render as text.
  root.innerHTML = statusChip('X<Y');
  assert.equal(root.querySelector('span').textContent, 'X<Y');
  assert.equal(root.querySelector('span b'), null);
  // Fallback for missing status.
  root.innerHTML = statusChip(null);
  assert.equal(root.querySelector('span').textContent, '-');
});

test('dom.priorityChip defaults to Medium and escapes the label', function () {
  var root = fixture();
  root.innerHTML = priorityChip(null);
  var span = root.querySelector('span.priority.priority-medium');
  assert.ok(span, 'default Medium classes present');
  assert.equal(span.textContent, 'Medium');
  root.innerHTML = priorityChip('High');
  assert.ok(root.querySelector('span.priority-high'));
  root.innerHTML = priorityChip('A<B');
  assert.equal(root.querySelector('span').textContent, 'A<B');
});
