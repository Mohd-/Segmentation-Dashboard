// Live round-trip tests against the serving Flask app. Only active when the
// runner URL carries ?live=1 (the python driver adds it); a manual file-less
// open without the flag reports these as skipped instead of failing on
// network access.
import { test, skip, assert } from './harness.js';

var live = new URLSearchParams(window.location.search).get('live') === '1';

function getJson(path) {
  return fetch(path, { cache: 'no-store' }).then(function (response) {
    assert.equal(response.status, 200, path + ' answers 200');
    return response.json();
  });
}

if (!live) {
  ['live: /api/health reports ok',
   'live: /api/meta shape (stages, statuses, seismic blocks)',
   'live: /api/me shape',
   'live: /api/users is an array of {name, role}'
  ].forEach(function (name) {
    skip(name, 'live server tests need ?live=1 on the runner URL');
  });
} else {
  test('live: /api/health reports ok', function () {
    return getJson('/api/health').then(function (body) {
      assert.equal(body.ok, true);
      assert.ok(typeof body.version === 'string' && body.version.length, 'version present');
      assert.ok(typeof body.app === 'string' && body.app.length, 'app name present');
    });
  });

  test('live: /api/meta shape (stages, statuses, seismic blocks)', function () {
    return getJson('/api/meta').then(function (meta) {
      assert.ok(Array.isArray(meta.prospect_stages) && meta.prospect_stages.length,
        'prospect_stages is a non-empty array');
      assert.ok(Array.isArray(meta.bp_stages) && meta.bp_stages.length,
        'bp_stages is a non-empty array');
      assert.ok(Array.isArray(meta.statuses), 'statuses is an array');
      assert.equal(meta.statuses.length, 4, 'exactly 4 lifecycle statuses');
      assert.ok(meta.seismic_blocks && typeof meta.seismic_blocks === 'object'
        && !Array.isArray(meta.seismic_blocks), 'seismic_blocks is an object map');
      Object.keys(meta.seismic_blocks).forEach(function (block) {
        assert.ok(Array.isArray(meta.seismic_blocks[block]), 'block "' + block + '" maps to an AR array');
      });
    });
  });

  test('live: /api/me shape', function () {
    return getJson('/api/me').then(function (me) {
      assert.equal(typeof me.authenticated, 'boolean', 'authenticated is boolean');
      assert.equal(typeof me.auth_required, 'boolean', 'auth_required is boolean');
      assert.ok('name' in me, 'name key present');
      assert.ok('role' in me, 'role key present');
      if (!me.authenticated) {
        assert.equal(me.name, null, 'anonymous name is null');
        assert.equal(me.role, null, 'anonymous role is null');
      }
    });
  });

  test('live: /api/users is an array of {name, role}', function () {
    return getJson('/api/users').then(function (users) {
      assert.ok(Array.isArray(users), 'users is an array');
      users.forEach(function (user) {
        assert.ok(typeof user.name === 'string' && user.name.length, 'user has a name');
        assert.ok(typeof user.role === 'string' && user.role.length, 'user has a role');
      });
    });
  });
}
