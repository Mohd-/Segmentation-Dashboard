/* =========================================================================
   Card 1F — the header's two dropdowns: the NOTIFICATION BELL and the GEAR
   (app settings) menu.

   WHAT THIS MODULE OWNS
     - opening/closing/placing both menus (and the page-wide "one dropdown at
       a time" rule they share with the board's filter menus);
     - the bell's data: the feed, the unread count, the red dot, and the two
       mark-as-read calls;
     - the gear's three items and their ORDER.

   WHAT IT DELIBERATELY DOES NOT OWN
     The gear's three ACTIONS. Dark mode, Export to Excel and Sign out are the
     app's behaviors, not the menu's: main.js still owns them and hands them in
     through `initHeaderMenus({ actions })`. Card 1F re-homed those handlers
     from the hidden #header-legacy-controls buttons into this menu; it did not
     reimplement them, so there is exactly one theme toggle, one export trigger
     and one sign-out flow in the app.

   POLLING: none. There is no timer here. The unread count refreshes on the
   board's own 'leads:filtered' cycle (throttled) and on every menu open. A
   bell that polls on its own would be a second, unsynchronized clock.
   ========================================================================= */
import { byId, all, esc, msg } from '../dom.js';
import { ICONS } from '../icons.js';
import { API } from '../api.js';
import { Store } from '../state.js';
import { openDetail } from './detail.js';
import { DROPDOWN_OPEN_EVENT } from './lead-filters.js';

var DROPDOWN_SOURCE = 'header-menus';

// The stored event vocabulary (models.Notification's CHECK constraint) ->
// the line the user reads. An unknown event still renders, titled by its own
// raw value, rather than producing a blank row.
var EVENT_TITLES = {
  submitted: 'Submitted for approval',
  approved: 'Approved',
  returned: 'Returned for update'
};

// Minimum gap between two BACKGROUND unread-count refreshes. The board's
// 'leads:filtered' event fires on every filter tick (which can be several per
// second while a user works the assignee checklist), and the count is not
// worth a request each time. Opening the menu ignores this throttle: an
// explicit open always shows current data.
var COUNT_THROTTLE_MS = 20000;

// How long the Export item stays disabled after firing. The export is a
// browser NAVIGATION (/api/export/excel is a file download), so there is no
// completion event to listen for -- and the request is a 3-5s CPU-bound job on
// the server at production scale. The cooldown is therefore a double-fire
// guard, not a progress indicator: it never claims the file is ready.
var EXPORT_COOLDOWN_MS = 4000;

var settings = {
  actions: {},
  // Whether the Sign out item exists at all. Mirrors what #sign-out did before
  // this card: the legacy button carried .hidden unless Store.user was set, so
  // an AUTH_REQUIRED-off instance never showed a sign-out that signs nothing
  // out. A hidden item, never a dead one.
  canSignOut: function () { return !!Store.user; },
  // Injectable for the test harness; the app always navigates via openDetail.
  openRecord: openDetail
};

var state = {
  items: null,        // null = never loaded; [] = loaded and empty
  unread: 0,
  loading: false,
  error: '',
  markingAll: false,
  identity: '',       // the /api/me name the cached feed belongs to
  lastCountAt: 0
};

var opened = null;    // 'notify' | 'settings' | null
var wired = false;

/* -------------------------------------------------------------------------
   Elements
   ------------------------------------------------------------------------- */

function notifyTrigger() { return byId('notify-toggle'); }
function notifyMenu() { return byId('notify-menu'); }
function notifyDot() { return byId('notify-dot'); }
function gearTrigger() { return byId('app-settings-toggle'); }
function gearMenu() { return byId('app-settings-menu'); }

function triggerFor(name) { return name === 'settings' ? gearTrigger() : notifyTrigger(); }
function menuFor(name) { return name === 'settings' ? gearMenu() : notifyMenu(); }

function identityKey() { return (Store.user && Store.user.name) || ''; }

/* -------------------------------------------------------------------------
   Formatting (pure)
   ------------------------------------------------------------------------- */

// Stored timestamps are UTC in the 'YYYY-MM-DD HH:MM:SS' shape (helpers.
// utc_now_str). That string is NOT valid ISO-8601, and engines are free to
// read it as local time -- which would shift every notification by the
// viewer's offset. Normalizing to '...T...Z' first makes the UTC reading
// explicit, and toLocaleString then renders it in the reader's own locale and
// zone. Anything unparseable falls through as its raw text rather than
// "Invalid Date".
export function formatWhen(value) {
  var text = String(value == null ? '' : value).trim();
  if (!text) return '';
  var iso = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(text) ? text.replace(' ', 'T') + 'Z' : text;
  var when = new Date(iso);
  if (isNaN(when.getTime())) return text;
  return when.toLocaleString();
}

export function eventTitle(event) {
  return EVENT_TITLES[String(event || '').toLowerCase()] || String(event || 'Update');
}

// "PROJECT — Task", skipping either half when the snapshot has none.
function targetLine(item) {
  return [item.project_name, item.task_name].filter(function (part) {
    return part !== null && part !== undefined && String(part).trim() !== '';
  }).join(' — ');
}

/* -------------------------------------------------------------------------
   The bell menu
   ------------------------------------------------------------------------- */

function itemMarkup(item) {
  var unread = !item.read_at;
  // Unread is signalled by WEIGHT plus a dot glyph, never by color alone: the
  // dot has its own shape and position, so the state survives a monochrome or
  // color-blind reading.
  return '<button type="button" class="hm-item' + (unread ? ' is-unread' : '') + '"' +
    ' data-id="' + esc(item.id) + '"' +
    ' data-project-id="' + esc(item.project_id == null ? '' : item.project_id) + '"' +
    ' data-pipeline="' + esc(item.pipeline_type === 'bp' ? 'bp' : 'prospect') + '">' +
    '<span class="hm-item-dot" aria-hidden="true"></span>' +
    '<span class="hm-item-body">' +
      '<span class="hm-item-title">' + esc(eventTitle(item.event)) + '</span>' +
      '<span class="hm-item-target">' + esc(targetLine(item)) + '</span>' +
      '<span class="hm-item-message">' + esc(item.message || '') + '</span>' +
      '<span class="hm-item-time">' + esc(formatWhen(item.created_at)) + '</span>' +
    '</span>' +
    (unread ? '<span class="visually-hidden">Unread</span>' : '') +
    '</button>';
}

function listMarkup() {
  if (state.error) return '<p class="hm-note hm-error">' + esc(state.error) + '</p>';
  // The existing loading idiom (detail.js's folder cards): say "Loading…",
  // never render placeholder rows that look like real notifications.
  if (state.items === null) return '<p class="hm-note">Loading…</p>';
  if (!state.items.length) return '<p class="hm-note hm-empty">No notifications</p>';
  return state.items.map(itemMarkup).join('');
}

function renderNotifyMenu() {
  var menu = notifyMenu();
  if (!menu) return;
  menu.innerHTML =
    '<div class="hm-head">' +
      '<h3 class="hm-title">Notifications</h3>' +
      // Only offered when there is something to mark: a permanently visible
      // "Mark All as Read" on an all-read list is a button that does nothing.
      (state.unread > 0
        ? '<button type="button" class="hm-mark-all"' + (state.markingAll ? ' disabled' : '') + '>Mark All as Read</button>'
        : '') +
    '</div>' +
    '<div class="hm-list">' + listMarkup() + '</div>';

  var markAll = menu.querySelector('.hm-mark-all');
  if (markAll) markAll.addEventListener('click', markAllRead);
  all('.hm-item', menu).forEach(function (element) {
    element.addEventListener('click', function () { openItem(element); });
  });
}

function syncDot() {
  var dot = notifyDot();
  if (dot) dot.classList.toggle('hidden', !(state.unread > 0));
  var trigger = notifyTrigger();
  if (trigger) {
    trigger.setAttribute('aria-label',
      state.unread > 0 ? 'Notifications (' + state.unread + ' unread)' : 'Notifications');
  }
}

// Adopt the unread_count every notification response carries (the list call
// and both mutations), so the dot and the menu are never two sources of truth.
function applyCount(payload) {
  var count = payload && payload.unread_count;
  state.unread = typeof count === 'number' && count >= 0 ? count : state.unread;
  syncDot();
  return payload;
}

function loadNotifications() {
  state.loading = true;
  state.error = '';
  state.identity = identityKey();
  state.lastCountAt = Date.now();
  renderNotifyMenu();
  return API.notifications().then(function (payload) {
    state.loading = false;
    state.items = (payload && payload.notifications) || [];
    applyCount(payload);
    renderNotifyMenu();
    return payload;
  }).catch(function (error) {
    state.loading = false;
    // A failed load must not clear the dot: the count on screen is the last
    // one the server confirmed, and "we could not reach the server" is not
    // evidence that the notifications were read.
    state.error = error && error.message ? error.message : 'Could not load notifications.';
    renderNotifyMenu();
  });
}

/* The cheap background refresh: no timer of its own, no second endpoint. It
   rides the board's existing 'leads:filtered' cycle and is throttled, so a
   burst of filter changes costs at most one request per COUNT_THROTTLE_MS.
   The response also refreshes the cached list, so an open right after one is
   already warm -- the open still re-fetches, because "current" matters more
   than one saved request. `force` is the menu-open path. */
export function refreshUnreadCount(force) {
  var now = Date.now();
  if (identityKey() !== state.identity) {
    // A sign-in/sign-out changed WHOSE bell this is: the cached feed belongs
    // to someone else and must not be shown for a moment longer.
    state.items = null;
    state.unread = 0;
    state.identity = identityKey();
    syncDot();
  } else if (!force && now - state.lastCountAt < COUNT_THROTTLE_MS) {
    return Promise.resolve(null);
  }
  state.lastCountAt = now;
  return API.notifications().then(function (payload) {
    state.items = (payload && payload.notifications) || [];
    state.error = '';
    applyCount(payload);
    if (opened === 'notify') renderNotifyMenu();
    return payload;
  }).catch(function () {
    // Silent: a background count refresh must never toast, and must never
    // clear a dot it failed to verify.
    return null;
  });
}

function markAllRead() {
  if (state.markingAll || !(state.unread > 0)) return;
  state.markingAll = true;      // one request in flight, never two
  renderNotifyMenu();
  API.markAllNotificationsRead().then(function (payload) {
    state.markingAll = false;
    (state.items || []).forEach(function (item) {
      if (!item.read_at) item.read_at = 'read';
    });
    applyCount(payload);
    renderNotifyMenu();
  }).catch(function (error) {
    // The dot stays exactly where it was: nothing was read.
    state.markingAll = false;
    renderNotifyMenu();
    msg((error && error.message) || 'Could not mark notifications as read.', 'error');
  });
}

// Click-through: mark read, then open the record the notification is about.
// The POST is fired FIRST and not awaited -- the read is a side effect of the
// click, and making navigation wait on it would put a round trip between the
// user and the record they asked for. A failed POST leaves the item unread
// (and the dot untouched), which is the honest outcome.
function openItem(element) {
  var id = Number(element.getAttribute('data-id'));
  var projectId = Number(element.getAttribute('data-project-id'));
  var pipeline = element.getAttribute('data-pipeline') === 'bp' ? 'bp' : 'prospect';
  var item = (state.items || []).filter(function (row) { return Number(row.id) === id; })[0];
  if (item && !item.read_at) {
    API.markNotificationRead(id).then(function (payload) {
      item.read_at = 'read';
      applyCount(payload);
      if (opened === 'notify') renderNotifyMenu();
    }).catch(function () { /* stays unread; the dot keeps its confirmed value */ });
  }
  closeHeaderMenus();
  if (projectId) settings.openRecord(projectId, pipeline);
}

/* -------------------------------------------------------------------------
   The gear menu
   ------------------------------------------------------------------------- */

// Card 3B. Announced, not built. They are listed here so the roadmap is
// visible where it will land, and they are genuinely inert: `soon` items get
// no data-action, so runGearAction can never be reached for them, and the
// button is `disabled` so neither click nor Enter/Space activates it. No
// route, no download, no request, no loading state.
var COMING_SOON = [
  { label: 'Export automatic Well Prop.' },
  { label: 'Export Well Logs Data' }
];

// The three real actions, in this order, then the coming-soon pair. Sign out
// is OMITTED (not disabled, not greyed) when there is no session to end.
function gearItems() {
  var isDark = document.documentElement.dataset.theme === 'dark';
  var items = [
    { key: 'theme', label: isDark ? 'Light Mode' : 'Dark Mode',
      icon: isDark ? 'sun' : 'moon', pressed: isDark },
    { key: 'export', label: 'Export to Excel', icon: 'file-spreadsheet', tint: 'excel' },
    { key: 'signout', label: 'Sign out', icon: 'log-out' }
  ];
  return items.filter(function (item) {
    return item.key !== 'signout' || settings.canSignOut();
  });
}

function gearItemMarkup(item) {
  return '<button type="button" class="hm-action" data-action="' + item.key + '"' +
    (item.pressed === undefined ? '' : ' aria-pressed="' + (item.pressed ? 'true' : 'false') + '"') +
    '>' +
    '<span class="hm-action-icon' + (item.tint ? ' hm-icon-' + item.tint : '') + '" aria-hidden="true">' +
      ICONS[item.icon] +
    '</span>' +
    '<span class="hm-action-label">' + esc(item.label) + '</span>' +
    '</button>';
}

// No icon, no data-action, no badge -- the card asks for a plain disabled
// label in the established faint treatment.
function comingSoonMarkup(item) {
  return '<button type="button" class="hm-action hm-action-soon" disabled title="Coming soon">' +
    '<span class="hm-action-label">' + esc(item.label) + '</span>' +
    '</button>';
}

function renderGearMenu() {
  var menu = gearMenu();
  if (!menu) return;
  menu.innerHTML = gearItems().map(gearItemMarkup).join('') +
    '<hr class="hm-soon-divider">' +
    COMING_SOON.map(comingSoonMarkup).join('');
  all('.hm-action[data-action]', menu).forEach(function (element) {
    element.addEventListener('click', function () {
      runGearAction(element.getAttribute('data-action'), element);
    });
  });
}

function runGearAction(key, element) {
  var actions = settings.actions || {};
  if (key === 'theme') {
    // The menu STAYS OPEN: the label and glyph swap in place, which is the
    // feedback that the click landed (the whole page changing is the rest).
    if (actions.toggleTheme) actions.toggleTheme();
    renderGearMenu();
    return;
  }
  if (key === 'export') {
    if (element.disabled) return;
    element.disabled = true;
    element.setAttribute('aria-busy', 'true');
    if (actions.exportExcel) actions.exportExcel();
    closeHeaderMenus();
    // Re-arm even though the menu closed: the same element is reused when the
    // menu is re-rendered, and a stuck-disabled item would be worse than an
    // early re-arm.
    setTimeout(function () {
      element.disabled = false;
      element.removeAttribute('aria-busy');
    }, EXPORT_COOLDOWN_MS);
    return;
  }
  if (key === 'signout') {
    closeHeaderMenus();
    if (actions.signOut) actions.signOut();
  }
}

/* -------------------------------------------------------------------------
   Open / close / placement

   Fixed positioning against the VIEWPORT, the approach the filter menus
   proved: the header is a sticky, overflow-clipping bar, so an absolutely
   positioned menu would be cut at its bottom edge. Right-aligned to its
   trigger (both triggers sit at the far right of the header) and clamped
   inside the viewport. The menus live outside the normal flow, so opening one
   never moves a pixel of the page.
   ------------------------------------------------------------------------- */

function placeMenu(trigger, menu) {
  var margin = 8;
  var rect = trigger.getBoundingClientRect();
  menu.style.left = '0px';
  menu.style.top = '0px';
  var width = menu.offsetWidth;
  var left = rect.right - width;                       // right-aligned
  if (left + width > window.innerWidth - margin) left = window.innerWidth - width - margin;
  left = Math.max(margin, left);
  var top = rect.bottom + 6;
  menu.style.left = Math.round(left) + 'px';
  menu.style.top = Math.round(top) + 'px';
  menu.style.maxHeight = Math.max(160, window.innerHeight - top - margin) + 'px';
}

// Closing is idempotent. `restoreFocus` returns focus to the trigger, which
// Escape must do (a keyboard user has nowhere else to be) and an outside click
// must NOT (the user is already somewhere else).
export function closeHeaderMenus(restoreFocus) {
  var previous = opened;
  opened = null;
  ['notify', 'settings'].forEach(function (name) {
    var menu = menuFor(name);
    var trigger = triggerFor(name);
    if (menu) menu.hidden = true;
    if (trigger) trigger.setAttribute('aria-expanded', 'false');
  });
  if (restoreFocus && previous) {
    var trigger = triggerFor(previous);
    if (trigger) trigger.focus();
  }
}

function openHeaderMenu(name) {
  var trigger = triggerFor(name);
  var menu = menuFor(name);
  if (!trigger || !menu) return;
  closeHeaderMenus();
  // One dropdown at a time, page-wide: this closes the OTHER header menu (just
  // done above) and any open board filter menu, which honors the same event.
  document.dispatchEvent(new CustomEvent(DROPDOWN_OPEN_EVENT, {
    detail: { source: DROPDOWN_SOURCE }
  }));
  if (name === 'notify') {
    // Opening never marks anything read -- reading is an explicit act (click
    // an item, or Mark All as Read).
    renderNotifyMenu();
  } else {
    renderGearMenu();
  }
  menu.hidden = false;
  placeMenu(trigger, menu);
  trigger.setAttribute('aria-expanded', 'true');
  opened = name;
  // Lazily fetch on open, every open: the count may have moved since the last
  // board refresh, and a stale list is worse than a brief "Loading…".
  if (name === 'notify') {
    if (identityKey() !== state.identity) state.items = null;
    loadNotifications();
  }
}

function toggleHeaderMenu(name) {
  if (opened === name) closeHeaderMenus(); else openHeaderMenu(name);
}

/* -------------------------------------------------------------------------
   Wiring
   ------------------------------------------------------------------------- */

function wireOnce() {
  if (wired) return;
  wired = true;

  // CAPTURE phase, deliberately. Both menus re-render their own innerHTML from
  // inside a click handler (the theme item swaps its label in place), which
  // DETACHES the clicked element before a bubbling listener would see it --
  // and a detached target's closest() matches nothing, so a bubbling handler
  // would read every in-menu click as an outside click and dismiss the menu.
  // At capture time the target is still in the tree and the test is honest.
  document.addEventListener('click', function (event) {
    if (!opened) return;
    var target = event.target;
    if (target && target.closest &&
        (target.closest('#notify-toggle') || target.closest('#app-settings-toggle') ||
         target.closest('#notify-menu') || target.closest('#app-settings-menu'))) return;
    closeHeaderMenus();
  }, true);

  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape' || !opened) return;
    closeHeaderMenus(true);
  });

  // Someone else's dropdown opened.
  document.addEventListener(DROPDOWN_OPEN_EVENT, function (event) {
    if (!event.detail || event.detail.source === DROPDOWN_SOURCE) return;
    closeHeaderMenus();
  });

  // A fixed-positioned menu would hang in the wrong place after a scroll or a
  // resize; dismiss instead of chasing the trigger. Capture, so scrolling any
  // ancestor counts -- but not the menu's own list.
  window.addEventListener('resize', function () { closeHeaderMenus(); });
  window.addEventListener('scroll', function (event) {
    var target = event.target;
    if (target && target.closest && target.closest('.header-menu')) return;
    closeHeaderMenus();
  }, true);

  // The theme can also change from elsewhere (the pre-paint script, a future
  // OS-preference listener); the gear label follows whatever it becomes.
  document.addEventListener('theme:changed', function () { renderGearMenu(); });

  // Identity changed: the Sign out item appears/disappears and the bell now
  // belongs to someone else.
  document.addEventListener('auth:changed', function () {
    renderGearMenu();
    refreshUnreadCount(true);
  });

  // THE POLL, such as it is: the board's own refresh cycle. No timer.
  document.addEventListener('leads:filtered', function () { refreshUnreadCount(); });
}

/* Boot entry point (main.js).

   options.actions   { toggleTheme, exportExcel, signOut } -- the app's existing
                     handlers, re-homed from #header-legacy-controls.
   options.canSignOut() -> whether to show the Sign out item at all.
   options.openRecord(projectId, pipeline) -> navigation (defaults to
                     openDetail; the harness injects a spy).

   Safe to call more than once: the document-level listeners register exactly
   once, and every call re-renders both menus from current state. */
export function initHeaderMenus(options) {
  var config = options || {};
  settings.actions = config.actions || {};
  if (typeof config.canSignOut === 'function') settings.canSignOut = config.canSignOut;
  if (typeof config.openRecord === 'function') settings.openRecord = config.openRecord;

  var notify = notifyTrigger();
  var gear = gearTrigger();
  if (notify && !notify.__hmWired) {
    notify.__hmWired = true;
    notify.setAttribute('aria-haspopup', 'menu');
    notify.setAttribute('aria-expanded', 'false');
    notify.addEventListener('click', function () { toggleHeaderMenu('notify'); });
  }
  if (gear && !gear.__hmWired) {
    gear.__hmWired = true;
    gear.setAttribute('aria-haspopup', 'menu');
    gear.setAttribute('aria-expanded', 'false');
    gear.addEventListener('click', function () { toggleHeaderMenu('settings'); });
  }
  wireOnce();
  renderGearMenu();
  syncDot();
  refreshUnreadCount(true);
}

/* A read-only snapshot for the test harness (and for debugging in the
   console). Never a handle on the live state: callers get a copy. */
export function headerMenuState() {
  return {
    open: opened,
    unread: state.unread,
    loaded: state.items !== null,
    count: (state.items || []).length,
    markingAll: state.markingAll,
    error: state.error,
    identity: state.identity
  };
}

/* Test-only reset: clears the cached feed so each case starts from a cold
   bell. The document-level listeners stay registered (they query the DOM
   live, exactly as in the app). */
export function resetHeaderMenus() {
  state.items = null;
  state.unread = 0;
  state.error = '';
  state.markingAll = false;
  state.identity = '';
  state.lastCountAt = 0;
  opened = null;
}
