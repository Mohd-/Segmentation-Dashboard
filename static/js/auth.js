import { esc } from './dom.js';
import { Store } from './state.js';

// Shared, api.js-free login primitives used by BOTH the boot-time full-page
// login (main.js) and the mid-session login modal (dialog.js). Raw fetch, NOT
// api.js, on purpose: api.js opens the modal on 401, so importing it here would
// create a require cycle and a login failure must never recursively re-open the
// dialog. /api/users and /api/login are both exempt from AUTH_REQUIRED, so
// these calls work without a session.

// Fill a <select> with the active-user names for a login dropdown. Resolves to
// the users array (or [] on failure) so callers can react.
export function fetchUserOptions(select) {
  return fetch('/api/users', { cache: 'no-store' }).then(function (response) {
    return response.ok ? response.json() : [];
  }).then(function (users) {
    if (select) {
      select.innerHTML = (users || []).map(function (user) {
        return '<option>' + esc(user.name) + '</option>';
      }).join('');
    }
    return users || [];
  }).catch(function () {
    if (select) select.innerHTML = '';
    return [];
  });
}

// POST /api/login and resolve {ok, body}. On success also sets Store.user and
// dispatches 'auth:changed' so the header chip re-renders -- the single place
// that side effect lives, shared by every login surface. Never rejects for an
// HTTP error (only a network failure rejects, which callers catch).
export function performLogin(name, passcode) {
  return fetch('/api/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    cache: 'no-store',
    body: JSON.stringify({ name: name, passcode: passcode })
  }).then(function (response) {
    return response.json().then(function (body) { return { ok: response.ok, body: body }; });
  }).then(function (result) {
    if (result.ok) {
      Store.user = { name: result.body.name, role: result.body.role };
      document.dispatchEvent(new CustomEvent('auth:changed'));
    }
    return result;
  });
}
