// Tests for static/js/views/header-menus.js — the Card 1F notification bell
// and gear menu, and the page-wide "one dropdown at a time" contract they
// share with the Card 1C filter menus.
//
// The module addresses the real header ids, so every test mounts a fixture
// carrying the same ids static/index.html does. The three gear ACTIONS are
// injected (they live in main.js, which the harness must not import — its
// DOMContentLoaded boot would try to run the whole app), so what is asserted
// here is that the menu invokes them, not what they do.
import { test, skip, assert, fixture, mockFetch, waitFor } from './harness.js';
import {
  initHeaderMenus, closeHeaderMenus, refreshUnreadCount, headerMenuState,
  resetHeaderMenus, formatWhen, eventTitle
} from '../js/views/header-menus.js';
import { initLeadFilters, setLeadRows, setLeadUsers, closeLeadMenus } from '../js/views/lead-filters.js';

var live = new URLSearchParams(window.location.search).get('live') === '1';

// The header ids, copied from static/index.html.
var HEADER_HTML =
  '<div class="header-actions">' +
    '<span id="user-chip" class="user-chip hidden"></span>' +
    '<button id="notify-toggle" type="button" class="icon-btn" aria-label="Notifications">' +
      '<span id="notify-dot" class="notify-dot hidden"></span>' +
    '</button>' +
    '<button id="app-settings-toggle" type="button" class="icon-btn" aria-label="Settings"></button>' +
    '<div id="notify-menu" class="header-menu" role="menu" hidden></div>' +
    '<div id="app-settings-menu" class="header-menu" role="menu" hidden></div>' +
  '</div>';

function jsonResponse(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status: status,
    headers: { get: function () { return 'application/json'; } },
    json: function () { return Promise.resolve(body); },
    text: function () { return Promise.resolve(JSON.stringify(body)); }
  };
}

function notification(id, extra) {
  var options = extra || {};
  return {
    id: id,
    created_at: options.created_at || '2026-08-01 09:30:00',
    recipient: 'Supervisor',
    actor: options.actor || 'Employee',
    event: options.event || 'submitted',
    project_id: options.project_id === undefined ? 7 : options.project_id,
    task_id: options.task_id || 100 + id,
    task_name: options.task_name || 'Area Definition',
    project_name: options.project_name || 'GALV-2',
    message: options.message || 'Employee submitted Area Definition on GALV-2',
    read_at: options.read_at || null,
    pipeline_type: options.pipeline_type || 'prospect'
  };
}

/* A stand-in server for the three notification routes. `server.feed` is the
   payload GET answers with; `server.fail` names a path that should reject
   (network failure). Every request is recorded in `server.calls`. */
function makeServer(feed) {
  var server = {
    feed: feed || { notifications: [], unread_count: 0 },
    fail: '',
    pending: '',
    calls: []
  };
  mockFetch(function (url, opts) {
    var method = (opts && opts.method) || 'GET';
    server.calls.push({ url: url, method: method });
    if (server.fail && url.indexOf(server.fail) >= 0) throw new Error('network down');
    if (server.pending && url.indexOf(server.pending) >= 0) return new Promise(function () {});
    if (url.indexOf('/read-all') >= 0) {
      server.feed.notifications.forEach(function (item) { item.read_at = item.read_at || '2026-08-02 10:00:00'; });
      server.feed.unread_count = 0;
      return jsonResponse(200, { ok: true, marked: 1, unread_count: 0 });
    }
    if (/\/api\/notifications\/\d+\/read/.test(url)) {
      var id = Number(url.match(/\/api\/notifications\/(\d+)\/read/)[1]);
      server.feed.notifications.forEach(function (item) {
        if (item.id === id) item.read_at = '2026-08-02 10:00:00';
      });
      server.feed.unread_count = server.feed.notifications.filter(function (item) {
        return !item.read_at;
      }).length;
      return jsonResponse(200, { ok: true, unread_count: server.feed.unread_count });
    }
    return jsonResponse(200, server.feed);
  });
  return server;
}

function spy() {
  var fn = function () { fn.calls.push(Array.prototype.slice.call(arguments)); };
  fn.calls = [];
  return fn;
}

/* Mounts a live header over `server` and returns the handles a test needs.
   Every call resets the module's cached feed, so each case starts cold. */
function mount(server, options) {
  var settings = options || {};
  resetHeaderMenus();
  delete document.documentElement.dataset.theme;
  var root = fixture(HEADER_HTML);
  var actions = {
    toggleTheme: settings.toggleTheme || spy(),
    exportExcel: settings.exportExcel || spy(),
    signOut: settings.signOut || spy()
  };
  var openRecord = settings.openRecord || spy();
  initHeaderMenus({
    actions: actions,
    canSignOut: settings.canSignOut === undefined ? function () { return true; } : settings.canSignOut,
    openRecord: openRecord
  });
  return {
    root: root,
    actions: actions,
    openRecord: openRecord,
    bell: root.querySelector('#notify-toggle'),
    gear: root.querySelector('#app-settings-toggle'),
    bellMenu: root.querySelector('#notify-menu'),
    gearMenu: root.querySelector('#app-settings-menu'),
    dot: root.querySelector('#notify-dot')
  };
}

function gearLabels(host) {
  return Array.prototype.slice.call(host.gearMenu.querySelectorAll('.hm-action-label'))
    .map(function (element) { return element.textContent; });
}
function gearAction(host, key) {
  return host.gearMenu.querySelector('.hm-action[data-action="' + key + '"]');
}
function items(host) {
  return Array.prototype.slice.call(host.bellMenu.querySelectorAll('.hm-item'));
}
function itemText(element, className) {
  var node = element.querySelector('.' + className);
  return node ? node.textContent : '';
}
function loaded() {
  return waitFor(function () { return headerMenuState().loaded; });
}

// ---------------------------------------------------------------------------
// Pure formatting
// ---------------------------------------------------------------------------

test('header-menus formatWhen reads the stored UTC stamp as UTC, not local time', function () {
  // 'YYYY-MM-DD HH:MM:SS' is NOT valid ISO-8601; read naively it would be
  // treated as local time and every notification would shift by the viewer's
  // offset. The formatted output must equal the locale rendering of the SAME
  // instant expressed explicitly in UTC.
  var expected = new Date('2026-08-01T09:30:00Z').toLocaleString();
  assert.equal(formatWhen('2026-08-01 09:30:00'), expected);
  assert.equal(formatWhen(''), '');
  assert.equal(formatWhen(null), '');
  // Unparseable text passes through rather than rendering "Invalid Date".
  assert.equal(formatWhen('sometime yesterday'), 'sometime yesterday');
});

test('header-menus eventTitle covers the three stored events and degrades safely', function () {
  assert.equal(eventTitle('submitted'), 'Submitted for approval');
  assert.equal(eventTitle('approved'), 'Approved');
  assert.equal(eventTitle('returned'), 'Returned for update');
  assert.equal(eventTitle('something-new'), 'something-new');
  assert.equal(eventTitle(''), 'Update');
});

// ---------------------------------------------------------------------------
// The gear menu
// ---------------------------------------------------------------------------

test('header-menus gear menu offers exactly three items, in order', function () {
  var server = makeServer();
  var host = mount(server);
  host.gear.click();
  assert.equal(host.gearMenu.hidden, false);
  assert.deepEqual(gearLabels(host), ['Dark Mode', 'Export to Excel', 'Sign out']);
  // The Excel glyph is tinted by CLASS (the success token), not by a colored
  // icon file — so it inverts with the theme like every other glyph.
  assert.ok(gearAction(host, 'export').querySelector('.hm-icon-excel'),
    'the export item carries the success-tinted icon class');
  assert.ok(gearAction(host, 'export').querySelector('.lucide-file-spreadsheet'),
    'the vendored file-spreadsheet glyph is used');
  assert.ok(gearAction(host, 'signout').querySelector('.lucide-log-out'),
    'the vendored log-out glyph is used');
});

test('header-menus gear menu HIDES Sign out when there is no session to end', function () {
  var server = makeServer();
  var host = mount(server, { canSignOut: function () { return false; } });
  host.gear.click();
  // Hidden, not disabled: a dead action is worse than a missing one. The other
  // two keep their order.
  assert.deepEqual(gearLabels(host), ['Dark Mode', 'Export to Excel']);
  assert.equal(gearAction(host, 'signout'), null);
});

test('header-menus Dark Mode swaps to Light Mode (and moon to sun) with the theme', function () {
  var server = makeServer();
  // The injected handler is the app's real one in production; here it does
  // what applyTheme does to the DOM, so the menu can be seen to follow.
  var toggle = function () {
    var root = document.documentElement;
    if (root.dataset.theme === 'dark') delete root.dataset.theme; else root.dataset.theme = 'dark';
    toggle.calls += 1;
  };
  toggle.calls = 0;
  var host = mount(server, { toggleTheme: toggle });
  try {
    host.gear.click();
    var themeItem = gearAction(host, 'theme');
    assert.equal(itemText(themeItem, 'hm-action-label'), 'Dark Mode');
    assert.ok(themeItem.querySelector('.lucide-moon'), 'light theme offers the moon');
    assert.equal(themeItem.getAttribute('aria-pressed'), 'false');

    themeItem.click();
    assert.equal(toggle.calls, 1, 'the app handler is invoked, not a private copy');
    // The menu STAYS OPEN and re-renders in place.
    assert.equal(host.gearMenu.hidden, false);
    var swapped = gearAction(host, 'theme');
    assert.equal(itemText(swapped, 'hm-action-label'), 'Light Mode');
    assert.ok(swapped.querySelector('.lucide-sun'), 'dark theme offers the sun');
    assert.equal(swapped.getAttribute('aria-pressed'), 'true');

    swapped.click();
    assert.equal(itemText(gearAction(host, 'theme'), 'hm-action-label'), 'Dark Mode');
  } finally {
    delete document.documentElement.dataset.theme;
  }
});

test('header-menus a theme:changed event from elsewhere re-labels the gear item', function () {
  var server = makeServer();
  var host = mount(server);
  try {
    host.gear.click();
    assert.equal(itemText(gearAction(host, 'theme'), 'hm-action-label'), 'Dark Mode');
    document.documentElement.dataset.theme = 'dark';
    document.dispatchEvent(new CustomEvent('theme:changed', { detail: { theme: 'dark' } }));
    assert.equal(itemText(gearAction(host, 'theme'), 'hm-action-label'), 'Light Mode');
  } finally {
    delete document.documentElement.dataset.theme;
  }
});

test('header-menus Export to Excel fires the app handler, disables the item and closes the menu', function () {
  var server = makeServer();
  var exporter = spy();
  var host = mount(server, { exportExcel: exporter });
  host.gear.click();
  var exportItem = gearAction(host, 'export');
  exportItem.click();

  assert.equal(exporter.calls.length, 1, 'the existing export handler ran');
  assert.equal(exportItem.disabled, true, 'the item is disabled while the download is generating');
  assert.equal(exportItem.getAttribute('aria-busy'), 'true');
  assert.equal(host.gearMenu.hidden, true, 'the menu closes as the export starts');
  assert.equal(headerMenuState().open, null);

  // A second click on the still-disabled item cannot double-fire the 3-5s
  // server-side export.
  exportItem.click();
  assert.equal(exporter.calls.length, 1);
});

test('header-menus Sign out fires the existing flow and closes the menu', function () {
  var server = makeServer();
  var out = spy();
  var host = mount(server, { signOut: out });
  host.gear.click();
  gearAction(host, 'signout').click();
  assert.equal(out.calls.length, 1);
  assert.equal(host.gearMenu.hidden, true);
});

// ---------------------------------------------------------------------------
// The bell: data, states, and the dot
// ---------------------------------------------------------------------------

test('header-menus the bell says Loading… before the feed lands — never placeholder rows', function () {
  var server = makeServer({ notifications: [notification(1)], unread_count: 1 });
  server.pending = '/api/notifications';
  var host = mount(server);
  host.bell.click();
  assert.equal(host.bellMenu.hidden, false);
  assert.equal(host.bellMenu.querySelector('.hm-note').textContent, 'Loading…');
  assert.equal(items(host).length, 0, 'no fake items while loading');
  assert.equal(host.bellMenu.querySelector('.hm-title').textContent, 'Notifications');
});

test('header-menus an empty feed reads exactly "No notifications"', function () {
  var server = makeServer({ notifications: [], unread_count: 0 });
  var host = mount(server);
  host.bell.click();
  return loaded().then(function () {
    assert.equal(host.bellMenu.querySelector('.hm-empty').textContent, 'No notifications');
    assert.equal(items(host).length, 0);
  });
});

test('header-menus renders the feed in the order served, with title, target, message and time', function () {
  var server = makeServer({
    notifications: [
      notification(3, { event: 'approved', created_at: '2026-08-02 11:00:00',
                        task_name: 'Thickness Estimation', project_name: 'LUNA-1',
                        message: 'Supervisor approved Thickness Estimation on LUNA-1' }),
      notification(2, { event: 'returned', created_at: '2026-08-02 10:00:00' }),
      notification(1, { created_at: '2026-08-01 09:30:00', read_at: '2026-08-01 12:00:00' })
    ],
    unread_count: 2
  });
  var host = mount(server);
  host.bell.click();
  return loaded().then(function () {
    var rows = items(host);
    assert.equal(rows.length, 3);
    // Newest first: the server orders, the client never re-sorts.
    assert.deepEqual(rows.map(function (row) { return row.getAttribute('data-id'); }), ['3', '2', '1']);
    assert.deepEqual(rows.map(function (row) { return itemText(row, 'hm-item-title'); }),
      ['Approved', 'Returned for update', 'Submitted for approval']);
    assert.equal(itemText(rows[0], 'hm-item-target'), 'LUNA-1 — Thickness Estimation');
    assert.equal(itemText(rows[0], 'hm-item-message'),
      'Supervisor approved Thickness Estimation on LUNA-1');
    assert.equal(itemText(rows[0], 'hm-item-time'),
      new Date('2026-08-02T11:00:00Z').toLocaleString());
  });
});

test('header-menus never truncates a notification — long text is left for CSS to wrap', function () {
  var longName = 'A-VERY-LONG-LEAD-NAME-THAT-KEEPS-GOING-AND-GOING-1234567890';
  var longMessage = 'Employee submitted ' + longName + ' step on ' + longName +
    ' with a note that runs well past the width of a 360px menu';
  var server = makeServer({
    notifications: [notification(1, { project_name: longName, message: longMessage })],
    unread_count: 1
  });
  var host = mount(server);
  host.bell.click();
  return loaded().then(function () {
    var row = items(host)[0];
    // The module hands the full string to the DOM (no compact()/ellipsis): the
    // fixed-width menu wraps it in CSS, so nothing is silently lost.
    assert.equal(itemText(row, 'hm-item-message'), longMessage);
    assert.ok(itemText(row, 'hm-item-target').indexOf(longName) === 0);
  });
});

test('header-menus marks unread by WEIGHT and a dot, not by color alone', function () {
  var server = makeServer({
    notifications: [notification(2), notification(1, { read_at: '2026-08-01 12:00:00' })],
    unread_count: 1
  });
  var host = mount(server);
  host.bell.click();
  return loaded().then(function () {
    var rows = items(host);
    assert.ok(rows[0].classList.contains('is-unread'), 'the unread row is flagged');
    assert.ok(!rows[1].classList.contains('is-unread'), 'the read row is not');
    // Both rows keep the dot ELEMENT (so marking one read reflows nothing);
    // only the unread row carries the state class that fills it, plus a
    // text-only "Unread" for assistive tech.
    assert.equal(rows[0].querySelectorAll('.hm-item-dot').length, 1);
    assert.equal(rows[1].querySelectorAll('.hm-item-dot').length, 1);
    assert.ok(rows[0].textContent.indexOf('Unread') >= 0, 'unread is announced in text');
    assert.ok(rows[1].textContent.indexOf('Unread') < 0);
  });
});

test('header-menus the red dot is visible exactly when unread_count > 0', function () {
  var server = makeServer({ notifications: [], unread_count: 0 });
  var host = mount(server);
  return waitFor(function () { return headerMenuState().unread === 0; }).then(function () {
    assert.equal(host.dot.classList.contains('hidden'), true, 'no unread, no dot');
    server.feed = { notifications: [notification(1), notification(2)], unread_count: 2 };
    return refreshUnreadCount(true);
  }).then(function () {
    assert.equal(host.dot.classList.contains('hidden'), false, 'unread lights the dot');
    assert.match(host.bell.getAttribute('aria-label'), /2 unread/);
    server.feed = { notifications: [], unread_count: 0 };
    return refreshUnreadCount(true);
  }).then(function () {
    assert.equal(host.dot.classList.contains('hidden'), true);
    assert.equal(host.bell.getAttribute('aria-label'), 'Notifications');
  });
});

test('header-menus opening the bell does NOT mark anything read', function () {
  var server = makeServer({ notifications: [notification(1), notification(2)], unread_count: 2 });
  var host = mount(server);
  host.bell.click();
  return loaded().then(function () {
    assert.equal(server.calls.filter(function (call) { return call.method === 'POST'; }).length, 0,
      'opening is a read of the feed, never a write');
    assert.equal(headerMenuState().unread, 2);
    assert.equal(host.dot.classList.contains('hidden'), false);
  });
});

// ---------------------------------------------------------------------------
// Mark as read (one item, and all)
// ---------------------------------------------------------------------------

test('header-menus Mark All as Read is offered only while unread notifications exist', function () {
  var server = makeServer({ notifications: [notification(1)], unread_count: 1 });
  var host = mount(server);
  host.bell.click();
  return loaded().then(function () {
    assert.ok(host.bellMenu.querySelector('.hm-mark-all'), 'offered while something is unread');
    host.bellMenu.querySelector('.hm-mark-all').click();
    return waitFor(function () { return headerMenuState().unread === 0; });
  }).then(function () {
    assert.equal(host.bellMenu.querySelector('.hm-mark-all'), null,
      'withdrawn once there is nothing left to mark');
    assert.equal(host.dot.classList.contains('hidden'), true);
    // Items are never deleted by reading them.
    assert.equal(items(host).length, 1);
    assert.ok(!items(host)[0].classList.contains('is-unread'));
  });
});

test('header-menus Mark All as Read cannot fire twice while a request is in flight', function () {
  var server = makeServer({ notifications: [notification(1), notification(2)], unread_count: 2 });
  var host = mount(server);
  host.bell.click();
  return loaded().then(function () {
    server.pending = '/read-all';   // the request never settles
    host.bellMenu.querySelector('.hm-mark-all').click();
    assert.equal(headerMenuState().markingAll, true);
    // The re-rendered button is disabled, and clicking it again is a no-op.
    var button = host.bellMenu.querySelector('.hm-mark-all');
    assert.equal(button.disabled, true);
    button.click();
    button.click();
    assert.equal(server.calls.filter(function (call) {
      return call.url.indexOf('/read-all') >= 0;
    }).length, 1, 'exactly one read-all request');
  });
});

test('header-menus a failed Mark All leaves the dot lit and the items unread', function () {
  var server = makeServer({ notifications: [notification(1), notification(2)], unread_count: 2 });
  var host = mount(server);
  host.bell.click();
  return loaded().then(function () {
    server.fail = '/read-all';
    host.bellMenu.querySelector('.hm-mark-all').click();
    return waitFor(function () { return headerMenuState().markingAll === false; });
  }).then(function () {
    // Nothing was read, so nothing is cleared: the dot is evidence of server
    // state, not of a button press.
    assert.equal(headerMenuState().unread, 2);
    assert.equal(host.dot.classList.contains('hidden'), false);
    assert.equal(items(host).filter(function (row) {
      return row.classList.contains('is-unread');
    }).length, 2);
    // ... and the control comes back so the user can retry.
    assert.equal(host.bellMenu.querySelector('.hm-mark-all').disabled, false);
  });
});

test('header-menus clicking an item marks it read, closes the menu and opens its record', function () {
  var opener = spy();
  var server = makeServer({
    notifications: [notification(9, { project_id: 42, pipeline_type: 'bp' }), notification(8)],
    unread_count: 2
  });
  var host = mount(server, { openRecord: opener });
  host.bell.click();
  return loaded().then(function () {
    items(host)[0].click();
    // Navigation is immediate — the read is a side effect, not a gate.
    assert.deepEqual(opener.calls, [[42, 'bp']], 'opened on the pipeline the server reported');
    assert.equal(host.bellMenu.hidden, true, 'the menu closes on click-through');
    return waitFor(function () { return headerMenuState().unread === 1; });
  }).then(function () {
    var read = server.calls.filter(function (call) {
      return call.method === 'POST' && /\/api\/notifications\/9\/read/.test(call.url);
    });
    assert.equal(read.length, 1, 'exactly one read POST, for the clicked id');
    assert.equal(host.dot.classList.contains('hidden'), false, 'one still unread');
  });
});

test('header-menus clicking an already-read item navigates without a second POST', function () {
  var opener = spy();
  var server = makeServer({
    notifications: [notification(5, { read_at: '2026-08-01 12:00:00' })],
    unread_count: 0
  });
  var host = mount(server, { openRecord: opener });
  host.bell.click();
  return loaded().then(function () {
    items(host)[0].click();
    assert.deepEqual(opener.calls, [[7, 'prospect']]);
    assert.equal(server.calls.filter(function (call) { return call.method === 'POST'; }).length, 0);
  });
});

// ---------------------------------------------------------------------------
// Refresh strategy: no timer of its own
// ---------------------------------------------------------------------------

test('header-menus refreshes the count on the board cycle, throttled, with no poll timer', function () {
  var server = makeServer({ notifications: [notification(1)], unread_count: 1 });
  mount(server);
  function feedCalls() {
    return server.calls.filter(function (call) { return call.method === 'GET'; }).length;
  }
  return loaded().then(function () {
    var afterBoot = feedCalls();
    assert.equal(afterBoot, 1, 'exactly one fetch at boot');

    // A burst of filter changes inside the throttle window costs nothing.
    document.dispatchEvent(new CustomEvent('leads:filtered', { detail: { leads: [] } }));
    document.dispatchEvent(new CustomEvent('leads:filtered', { detail: { leads: [] } }));
    assert.equal(feedCalls(), afterBoot, 'throttled: no request per filter tick');

    // With the window elapsed (resetHeaderMenus clears the last-fetch stamp),
    // the SAME board event does refresh the count — the hook is real.
    resetHeaderMenus();
    document.dispatchEvent(new CustomEvent('leads:filtered', { detail: { leads: [] } }));
    return waitFor(function () { return feedCalls() === afterBoot + 1; });
  }).then(function () {
    var settled = feedCalls();
    // And nothing fires on its own afterwards: there is no interval.
    return new Promise(function (resolve) { setTimeout(resolve, 120); }).then(function () {
      assert.equal(feedCalls(), settled, 'no self-scheduled polling');
    });
  });
});

test('header-menus a failed background refresh never clears the dot', function () {
  var server = makeServer({ notifications: [notification(1), notification(2)], unread_count: 2 });
  var host = mount(server);
  return waitFor(function () { return headerMenuState().unread === 2; }).then(function () {
    server.fail = '/api/notifications';
    return refreshUnreadCount(true);
  }).then(function () {
    assert.equal(headerMenuState().unread, 2, 'the last server-confirmed count stands');
    assert.equal(host.dot.classList.contains('hidden'), false);
  });
});

// ---------------------------------------------------------------------------
// Shared dropdown behavior
// ---------------------------------------------------------------------------

test('header-menus only one menu is open at a time: bell and gear close each other', function () {
  var server = makeServer();
  var host = mount(server);
  host.bell.click();
  assert.equal(host.bellMenu.hidden, false);
  assert.equal(host.gearMenu.hidden, true);
  assert.equal(headerMenuState().open, 'notify');

  host.gear.click();
  assert.equal(host.bellMenu.hidden, true, 'opening the gear closed the bell');
  assert.equal(host.gearMenu.hidden, false);
  assert.equal(headerMenuState().open, 'settings');

  // A trigger toggles its own menu shut.
  host.gear.click();
  assert.equal(host.gearMenu.hidden, true);
  assert.equal(headerMenuState().open, null);
});

test('header-menus a header menu and a board filter menu are mutually exclusive', function () {
  var server = makeServer();
  var host = mount(server);
  var filterRoot = fixture('<div id="hm-filter-root"></div>');
  initLeadFilters({ root: 'hm-filter-root' });
  setLeadUsers([{ name: 'R. Khalid', role: 'staff' }]);
  setLeadRows([{ project_id: 1, project_name: 'GALV-2', field: 'GALV',
                 display_stage: 'Lead Assessment', overall_status: 'In Progress',
                 assignees: [], lead_priority: 'Medium', tracked_items: [] }]);
  var host2 = filterRoot.querySelector('#hm-filter-root');
  var filterTrigger = host2.querySelector('.lead-filter[data-filter="assignee"] .lf-trigger');
  var filterMenu = host2.querySelector('.lead-filter[data-filter="assignee"] .lf-menu');
  try {
    filterTrigger.click();
    assert.equal(filterMenu.hidden, false, 'the filter menu is open');

    host.gear.click();
    assert.equal(filterMenu.hidden, true, 'opening the gear closed the filter menu');
    assert.equal(host.gearMenu.hidden, false);

    filterTrigger.click();
    assert.equal(host.gearMenu.hidden, true, 'opening the filter menu closed the gear');
    assert.equal(headerMenuState().open, null);

    host.bell.click();
    assert.equal(filterMenu.hidden, true, 'the bell closes it too');
  } finally {
    closeLeadMenus();
    closeHeaderMenus();
  }
});

test('header-menus Escape closes the open menu and returns focus to its trigger', function () {
  var server = makeServer();
  var host = mount(server);
  host.bell.click();
  host.bellMenu.focus();
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  assert.equal(host.bellMenu.hidden, true);
  assert.equal(document.activeElement, host.bell, 'focus returns to the bell');

  host.gear.click();
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  assert.equal(host.gearMenu.hidden, true);
  assert.equal(document.activeElement, host.gear, 'focus returns to the gear');
});

test('header-menus an outside click closes the menu (and does not steal focus)', function () {
  var server = makeServer();
  var host = mount(server);
  var outside = fixture('<button id="hm-outside" type="button">elsewhere</button>')
    .querySelector('#hm-outside');
  host.gear.click();
  assert.equal(host.gearMenu.hidden, false);

  // A click INSIDE the menu must not dismiss it.
  host.gearMenu.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  assert.equal(host.gearMenu.hidden, false, 'clicks inside the menu keep it open');

  outside.focus();
  outside.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  assert.equal(host.gearMenu.hidden, true);
  assert.equal(document.activeElement, outside, 'the user stays where they clicked');
});

test('header-menus aria-expanded tracks both triggers', function () {
  var server = makeServer();
  var host = mount(server);
  assert.equal(host.bell.getAttribute('aria-expanded'), 'false');
  assert.equal(host.gear.getAttribute('aria-expanded'), 'false');
  assert.equal(host.bell.getAttribute('aria-haspopup'), 'menu');
  assert.equal(host.gear.getAttribute('aria-haspopup'), 'menu');

  host.bell.click();
  assert.equal(host.bell.getAttribute('aria-expanded'), 'true');
  assert.equal(host.gear.getAttribute('aria-expanded'), 'false');

  host.gear.click();
  assert.equal(host.bell.getAttribute('aria-expanded'), 'false');
  assert.equal(host.gear.getAttribute('aria-expanded'), 'true');

  closeHeaderMenus();
  assert.equal(host.gear.getAttribute('aria-expanded'), 'false');
});

test('header-menus both menus stay out of flow, so opening one shifts nothing', function () {
  var server = makeServer();
  var host = mount(server);
  // [hidden] is how they rest; the module never adds/removes them from the
  // DOM, and both carry .header-menu, whose CSS is position: fixed.
  assert.equal(host.bellMenu.hidden, true);
  assert.equal(host.gearMenu.hidden, true);
  host.gear.click();
  assert.ok(host.gearMenu.classList.contains('header-menu'));
  assert.equal(host.bellMenu.hidden, true, 'the other menu stays hidden, not merely empty');
  assert.ok(host.gearMenu.parentNode === host.bellMenu.parentNode,
    'both live in the header actions row, not inside the board');
});

// ---------------------------------------------------------------------------
// The legacy header controls are GONE from the shipped markup
// ---------------------------------------------------------------------------

if (!live) {
  // The real assertion needs the SERVED index.html, so without ?live=1 there is
  // nothing to check. Registered through skip() -- which keeps the entry (and
  // the total) exactly as stable as a pass-through would -- so a non-live run
  // reports it as SKIP rather than banking a green tick for an assert.ok(true)
  // that proves nothing. run_frontend_tests.py always passes ?live=1, so the
  // substantive branch below is what the shipped suite actually runs.
  skip('header-menus (skipped without ?live=1) the shipped header has no legacy controls',
       'the legacy-header check needs the served index.html (?live=1)');
} else {
  test('header-menus (skipped without ?live=1) the shipped header has no legacy controls', function () {
    return fetch('/static/index.html', { cache: 'no-store' }).then(function (response) {
      assert.equal(response.status, 200);
      return response.text();
    }).then(function (html) {
      ['id="header-legacy-controls"', 'id="theme-toggle"', 'id="export-excel"',
       'id="sign-out"', 'class="header-link"', 'Segments Dashboard'
      ].forEach(function (needle) {
        assert.ok(html.indexOf(needle) < 0, 'removed from the header: ' + needle);
      });
      ['id="notify-toggle"', 'id="notify-dot"', 'id="app-settings-toggle"',
       'id="notify-menu"', 'id="app-settings-menu"'
      ].forEach(function (needle) {
        assert.ok(html.indexOf(needle) >= 0, 'present in the header: ' + needle);
      });
    });
  });
}
