// Tests for static/js/state.js. Store is shared module state, so every test
// snapshots it up front and restores it in finally.
import { test, assert } from './harness.js';
import {
  Store, currentUserName, currentRole, currentProjectPipeline,
  isCurrentPipelineView, canManageAssignments, resetSelection
} from '../js/state.js';

function snapshotStore() {
  var snap = {};
  Object.keys(Store).forEach(function (key) { snap[key] = Store[key]; });
  return snap;
}

function restoreStore(snap) {
  Object.keys(snap).forEach(function (key) { Store[key] = snap[key]; });
}

function withStore(fn) {
  var snap = snapshotStore();
  try { fn(); } finally { restoreStore(snap); }
}

test('state.currentUserName falls back to Web User when anonymous', function () {
  withStore(function () {
    Store.user = null;
    assert.equal(currentUserName(), 'Web User');
  });
});

test('state.currentUserName reports the signed-in name', function () {
  withStore(function () {
    Store.user = { name: 'Alice', role: 'staff' };
    assert.equal(currentUserName(), 'Alice');
    Store.user = { name: '', role: 'staff' }; // blank name → fallback
    assert.equal(currentUserName(), 'Web User');
  });
});

test('state.currentRole: anonymous acts as supervisor (dev-mode mirror)', function () {
  withStore(function () {
    Store.user = null;
    assert.equal(currentRole(), 'supervisor');
  });
});

test('state.currentRole: signed-in role wins; missing role defaults employee', function () {
  withStore(function () {
    Store.user = { name: 'A', role: 'staff' };
    assert.equal(currentRole(), 'staff');
    Store.user = { name: 'A', role: 'employee' };
    assert.equal(currentRole(), 'employee');
    Store.user = { name: 'A' }; // no role on the user object
    assert.equal(currentRole(), 'employee');
  });
});

test('state.canManageAssignments per role', function () {
  withStore(function () {
    Store.user = null; // anonymous → supervisor
    assert.equal(canManageAssignments(), true);
    Store.user = { name: 'A', role: 'supervisor' };
    assert.equal(canManageAssignments(), true);
    Store.user = { name: 'A', role: 'staff' };
    assert.equal(canManageAssignments(), true);
    Store.user = { name: 'A', role: 'employee' };
    assert.equal(canManageAssignments(), false);
  });
});

test('state.currentProjectPipeline: bp vs prospect vs missing project', function () {
  withStore(function () {
    Store.project = { pipeline_type: 'bp' };
    assert.equal(currentProjectPipeline(), 'bp');
    Store.project = { pipeline_type: 'BP' }; // case-insensitive
    assert.equal(currentProjectPipeline(), 'bp');
    Store.project = { pipeline_type: 'prospect' };
    assert.equal(currentProjectPipeline(), 'prospect');
    Store.project = {}; // no pipeline_type
    assert.equal(currentProjectPipeline(), 'prospect');
    Store.project = null; // no project at all
    assert.equal(currentProjectPipeline(), 'prospect');
  });
});

test('state.isCurrentPipelineView compares the view to the project pipeline', function () {
  withStore(function () {
    Store.project = { pipeline_type: 'bp' };
    Store.pipeline = 'bp';
    assert.equal(isCurrentPipelineView(), true);
    Store.pipeline = 'prospect'; // reference view of the other phase
    assert.equal(isCurrentPipelineView(), false);
    Store.project = null;
    assert.equal(isCurrentPipelineView(), true); // prospect view of a missing project
  });
});

test('state.resetSelection clears every selection field, leaves the rest', function () {
  withStore(function () {
    Store.meta = { keep: true };
    Store.user = { name: 'Keep Me', role: 'staff' };
    Store.pipeline = 'bp';
    Store.projectId = 7;
    Store.project = { project_id: 7 };
    Store.tasks = [{ id: 1 }];
    Store.task = { id: 1 };
    Store.allFields = { a: 1 };
    Store.leadSummary = { s: 1 };
    Store.overview = { o: 1 };
    Store.formations = [{ f: 1 }];

    resetSelection();

    assert.equal(Store.projectId, null);
    assert.equal(Store.project, null);
    assert.deepEqual(Store.tasks, []);
    assert.equal(Store.task, null);
    assert.deepEqual(Store.allFields, {});
    assert.equal(Store.leadSummary, null);
    assert.equal(Store.overview, null);
    assert.deepEqual(Store.formations, []);
    // Non-selection state is untouched.
    assert.deepEqual(Store.meta, { keep: true });
    assert.equal(Store.user.name, 'Keep Me');
    assert.equal(Store.pipeline, 'bp');
  });
});
