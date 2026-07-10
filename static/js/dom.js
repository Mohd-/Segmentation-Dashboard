export function byId(id) { return document.getElementById(id); }
export function all(selector, root) { return Array.prototype.slice.call((root || document).querySelectorAll(selector)); }
export function esc(value) {
  return String(value == null ? '' : value).replace(/[&<>\"]/g, function (char) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[char];
  });
}
export function compact(value, maxLength) {
  var text = String(value == null || value === '' ? '-' : value);
  var limit = maxLength || 48;
  return text.length <= limit ? text : text.slice(0, limit - 1) + '…';
}
export function range(start, end) {
  var values = [];
  for (var year = start; year <= end; year += 1) values.push(String(year));
  return values;
}
export function isFilled(value) { return value !== null && value !== undefined && String(value).trim() !== ''; }
export function truthy(value) { return ['1', 'true', 'yes', 'on'].indexOf(String(value || '').toLowerCase()) >= 0; }
export function stamp() {
  var el = byId('last-refreshed');
  if (el) el.textContent = 'Last refreshed: ' + new Date().toLocaleString();
}
export function msg(message, type) {
  var el = byId('app-message');
  if (!el) {
    el = document.createElement('div');
    el.id = 'app-message';
    document.body.insertBefore(el, document.body.firstChild);
  }
  el.className = 'app-message ' + (type || 'info');
  el.textContent = message;
  clearTimeout(msg.timer);
  msg.timer = setTimeout(function () {
    el.className = 'app-message';
    el.textContent = '';
  }, 5000);
}
export function fillSelect(element, values, withAll) {
  if (!element) return;
  var previous = element.value;
  element.innerHTML = (withAll ? '<option>All</option>' : '') + values.map(function (value) {
    return '<option>' + esc(value) + '</option>';
  }).join('');
  if (values.indexOf(previous) >= 0 || (withAll && previous === 'All')) element.value = previous;
}
export function table(element, headings, rows, onClick) {
  if (!element) return;
  var body = rows.length ? rows.map(function (row, index) {
    return '<tr data-index="' + index + '">' + row.map(function (cell) { return '<td>' + cell + '</td>'; }).join('') + '</tr>';
  }).join('') : '<tr><td colspan="' + headings.length + '" class="empty-state">No records yet.</td></tr>';
  element.innerHTML = '<thead><tr>' + headings.map(function (heading) { return '<th>' + esc(heading) + '</th>'; }).join('') + '</tr></thead><tbody>' + body + '</tbody>';
  if (onClick) {
    all('tbody tr[data-index]', element).forEach(function (rowEl) {
      rowEl.addEventListener('click', function () {
        onClick(Number(rowEl.getAttribute('data-index')));
      });
    });
  }
}
export function statusChip(status) {
  var value = status || '-';
  return '<span class="status ' + String(value).toLowerCase().replace(/\s+/g, '-') + '">' + esc(value) + '</span>';
}
export function priorityChip(priority) {
  var value = priority || 'Medium';
  return '<span class="priority priority-' + String(value).toLowerCase() + '">' + esc(value) + '</span>';
}
export function classChip(value) {
  if (!value) return '';
  return '<span class="class-chip ' + String(value).toLowerCase().replace(/\s+/g, '-') + '">' + esc(value) + '</span>';
}
