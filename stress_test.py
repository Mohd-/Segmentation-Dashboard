#!/usr/bin/env python
"""Stress / exercise harness for the Segment Maturation and Execution System.

Run with the project venv interpreter:

    .venv/bin/python stress_test.py

SAFETY: the repo's ./pipeline_tracker.db holds REAL production data and is
NEVER touched. This script boots its own server subprocess against a scratch
SQLite DB in a temp directory (SEGMENT_TRACKER_DB_PATH override) and refuses
to run if the resolved scratch DB path ever equals the production DB path.
The production DB's size/mtime are snapshotted before and verified after.

Phases:
  0. boot     - temp dir, scratch DB, RF model stub, server subprocess, health poll
  1. sweep    - single-threaded correctness pass over every UI-triggerable action
  2. storm    - concurrent writers/readers/transitions/creates/login churn
  3. audit    - direct read-only sqlite invariant checks + API cross-checks

Exit code 0 when no error-severity findings (5xx, wrong status, invariant
break); 1 otherwise.

The harness itself is stdlib-only. The RF model stub is built by shelling out
to .venv/bin/python (joblib + sklearn live in the venv).

Targeting an already-running server: --base-url http://host:port requires the
extra --allow-mutation flag because the harness WRITES data (creates projects,
saves fields, transitions tasks). Phase 0 boot and the Phase 3 direct-sqlite
checks are skipped in that mode.
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
REAL_DB = (REPO_ROOT / "pipeline_tracker.db").resolve()
VENV_PY = REPO_ROOT / ".venv" / "bin" / "python"

# The 24-step pipeline (workflow/constants.py PIPELINE_TEMPLATES); v7 merged
# the first four Lead Assessment rows into one, so prospect stages cover
# sequences 1-9. BP rows deliberately retain their stable 13-27 sequence
# numbers; retired rows remain inactive and every invariant below deliberately
# counts active rows only.
TEMPLATE_TASK_COUNT = 24
PROSPECT_TASK_COUNT = 9

# ---------------------------------------------------------------------------
# Shared state: stats, findings, phase tallies
# ---------------------------------------------------------------------------

_LOCK = threading.Lock()


class Stats:
    """Per-endpoint request counts, status histograms and latencies."""

    def __init__(self):
        self.data = {}  # label -> {"count", "statuses": {code: n}, "latencies": [s]}

    def record(self, label, status, seconds):
        with _LOCK:
            slot = self.data.setdefault(label, {"count": 0, "statuses": {}, "latencies": []})
            slot["count"] += 1
            slot["statuses"][status] = slot["statuses"].get(status, 0) + 1
            slot["latencies"].append(seconds)

    def table(self):
        lines = []
        header = f"{'endpoint':44s} {'n':>6s} {'p50ms':>8s} {'p95ms':>8s} {'maxms':>8s}  statuses"
        lines.append(header)
        lines.append("-" * len(header))
        for label in sorted(self.data):
            slot = self.data[label]
            lat = slot["latencies"]
            p50 = statistics.median(lat) * 1000 if lat else 0.0
            if len(lat) >= 2:
                p95 = statistics.quantiles(lat, n=20)[18] * 1000
            else:
                p95 = p50
            mx = max(lat) * 1000 if lat else 0.0
            statuses = " ".join(f"{code}:{n}" for code, n in sorted(slot["statuses"].items()))
            lines.append(f"{label:44s} {slot['count']:6d} {p50:8.1f} {p95:8.1f} {mx:8.1f}  {statuses}")
        return "\n".join(lines)


class Findings:
    """Deduplicated list of unexpected behaviors. Severity: error | note."""

    def __init__(self):
        self.items = []
        self._seen = set()

    def add(self, severity, what, expected, got, body=""):
        body = (body or "")[:300]
        key = (severity, what, str(expected), str(got), body[:120])
        with _LOCK:
            if key in self._seen:
                return
            self._seen.add(key)
            self.items.append({"severity": severity, "what": what,
                               "expected": expected, "got": got, "body": body})

    def errors(self):
        return [f for f in self.items if f["severity"] == "error"]


STATS = Stats()
FINDINGS = Findings()
PHASES = {}          # name -> {"pass": n, "fail": n}
_CURRENT_PHASE = ["boot"]
# Per-project count of successful project-revision-bumping writes we performed
# (save_task / assign / transition / flags / archive / restore).
MUTATION_COUNTS = {}


def phase(name):
    _CURRENT_PHASE[0] = name
    PHASES.setdefault(name, {"pass": 0, "fail": 0})
    print("\n" + "=" * 78)
    print(f"PHASE: {name}")
    print("=" * 78)


def tally(ok):
    with _LOCK:
        slot = PHASES.setdefault(_CURRENT_PHASE[0], {"pass": 0, "fail": 0})
        slot["pass" if ok else "fail"] += 1


def check(condition, what, expected="true", got=None, body="", severity="error"):
    """Record a named assertion; a failure becomes a finding."""
    tally(bool(condition))
    if not condition:
        FINDINGS.add(severity, what, expected, got if got is not None else "condition false", body)
        print(f"  FAIL  {what}  (expected {expected}, got {got})")
    return bool(condition)


def note(what, detail):
    """Record an observed-behavior note (never affects the exit code)."""
    FINDINGS.add("note", what, "recorded", "observed", detail)
    print(f"  NOTE  {what}: {detail}")


def count_mutation(project_id):
    with _LOCK:
        MUTATION_COUNTS[project_id] = MUTATION_COUNTS.get(project_id, 0) + 1


# ---------------------------------------------------------------------------
# HTTP client (stdlib urllib + per-client cookie jar)
# ---------------------------------------------------------------------------

class Client:
    def __init__(self, base_url):
        self.base = base_url.rstrip("/")
        jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def raw(self, method, path, payload=None, timeout=60):
        """Return (status, body_bytes, seconds). status 0 = transport error."""
        url = self.base + path
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        started = time.perf_counter()
        try:
            with self.opener.open(req, timeout=timeout) as resp:
                status, body = resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            status, body = exc.code, exc.read()
        except Exception as exc:  # connection refused / timeout / etc.
            status, body = 0, repr(exc).encode()
        return status, body, time.perf_counter() - started

    def request(self, method, path, payload=None, expect=(200,), label=None,
                what=None, binary=False, severity="error", quiet=False):
        """Issue a request, record stats, verify the status against ``expect``.

        Returns (status, parsed_json_or_bytes). ``expect=None`` skips checking.
        """
        status, body, seconds = self.raw(method, path, payload)
        STATS.record(label or f"{method} {path}", status, seconds)
        parsed = body
        if not binary:
            try:
                parsed = json.loads(body.decode("utf-8"))
            except Exception:
                parsed = body.decode("utf-8", errors="replace")
        if expect is not None:
            ok = status in expect
            tally(ok)
            if not ok:
                text = body.decode("utf-8", errors="replace")[:300] if isinstance(body, bytes) else str(body)[:300]
                FINDINGS.add("error" if (status >= 500 or status == 0) else severity,
                             what or f"{method} {path}", f"status in {sorted(expect)}", status, text)
                if not quiet:
                    print(f"  FAIL  {method} {path} -> {status} (expected {sorted(expect)}) {text[:120]}")
        return status, parsed


# ---------------------------------------------------------------------------
# Phase 0: boot
# ---------------------------------------------------------------------------

def refuse_if_production(db_file: Path):
    if db_file.resolve() == REAL_DB:
        print(f"REFUSING TO RUN: resolved scratch DB path equals the production DB: {REAL_DB}")
        sys.exit(2)


def build_rf_stub(model_path: Path):
    """Build the same 3-feature RandomForest stub tests/conftest.py builds.

    Runs inside .venv/bin/python via a subprocess so the harness itself stays
    stdlib-only regardless of which interpreter launched it.
    """
    snippet = (
        "import sys, joblib\n"
        "from sklearn.ensemble import RandomForestClassifier\n"
        "X = [[0, 0.1, 0.1], [0, 0.9, 0.1], [1, 0.3, 0.7],"
        " [1, 0.5, 0.5], [2, 0.9, 0.9], [2, 0.1, 0.9]]\n"
        "y = [0, 0, 0, 1, 1, 1]\n"
        "m = RandomForestClassifier(random_state=0, n_estimators=10)\n"
        "m.fit(X, y)\n"
        "joblib.dump(m, sys.argv[1])\n"
    )
    subprocess.run([str(VENV_PY), "-c", snippet, str(model_path)],
                   check=True, cwd=str(REPO_ROOT), capture_output=True)


def boot_server(port, temp_dir):
    """Start the Flask app subprocess against the scratch DB; return (proc, log_path)."""
    scratch_db = temp_dir / "stress_pipeline_tracker.db"
    model_path = temp_dir / "RF_model.joblib"
    refuse_if_production(scratch_db)
    build_rf_stub(model_path)

    env = dict(os.environ)
    env["SEGMENT_TRACKER_DB_PATH"] = str(scratch_db)
    env["SEGMENT_TRACKER_RF_MODEL_PATH"] = str(model_path)
    env["AUTH_REQUIRED"] = "false"
    # Never let an inherited override re-point the subprocess at another DB or
    # demand a passcode the harness does not know.
    env.pop("DATABASE_URL", None)
    env.pop("SEGMENT_TRACKER_PASSCODE", None)

    log_path = temp_dir / "server.log"
    log_file = open(log_path, "wb")
    boot_code = f'import main; main.app.run(host="127.0.0.1", port={port}, threaded=True)'
    proc = subprocess.Popen([str(VENV_PY), "-c", boot_code], cwd=str(REPO_ROOT),
                            env=env, stdout=log_file, stderr=subprocess.STDOUT)
    return proc, scratch_db, log_path


def wait_for_health(client, proc, log_path, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            break
        status, body, _ = client.raw("GET", "/api/health", timeout=3)
        if status == 200:
            return json.loads(body.decode("utf-8"))
        time.sleep(0.25)
    print("SERVER FAILED TO BECOME HEALTHY. Server output:")
    try:
        print(log_path.read_text(errors="replace")[-4000:])
    except Exception as exc:
        print(f"(could not read server log: {exc})")
    # This runs before the caller's try/finally exists — reap the subprocess
    # here or a booted-but-unhealthy server outlives the harness.
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    sys.exit(2)


# ---------------------------------------------------------------------------
# Phase 1 helpers
# ---------------------------------------------------------------------------

def board_params(**overrides):
    params = {"search": "", "stage_filter": "All", "status_filter": "All",
              "owner_filter": "All", "health_filter": "All",
              "sort_key": "Well Name", "pipeline_filter": "All"}
    params.update(overrides)
    return "/api/projects?" + urllib.parse.urlencode(params)


def create_project(client, name, expect=(201,), **payload):
    body = {"project_name": name, "changed_by": "Stress Harness"}
    body.update(payload)
    return client.request("POST", "/api/projects", body, expect=expect,
                          label="POST /api/projects", what=f"create project {name!r}")


def get_tasks(client, project_id):
    _, tasks = client.request("GET", f"/api/projects/{project_id}/tasks",
                              label="GET /api/projects/<id>/tasks")
    return tasks if isinstance(tasks, list) else []


def task_by_name(client, project_id, task_name):
    for task in get_tasks(client, project_id):
        if task.get("task_name") == task_name:
            return task
    return None


def fresh_task(client, task_id):
    _, task = client.request("GET", f"/api/tasks/{task_id}", label="GET /api/tasks/<id>")
    return task if isinstance(task, dict) else {}


def save_task(client, task_id, project_id, fields=None, comments="", expect=(200,),
              revision=None, extra=None, quiet=False):
    task = fresh_task(client, task_id)
    payload = {"comments": comments, "priority": task.get("priority") or "Medium",
               "fields": fields or {}, "changed_by": "Stress Harness",
               "revision": task.get("revision") if revision is None else revision}
    if extra:
        payload.update(extra)
    status, body = client.request("PATCH", f"/api/tasks/{task_id}", payload, expect=expect,
                                  label="PATCH /api/tasks/<id>", quiet=quiet,
                                  what=f"save task {task_id}")
    if status == 200:
        count_mutation(project_id)
    return status, body


# ---------------------------------------------------------------------------
# Phase 1: full UI-action sweep
# ---------------------------------------------------------------------------

def run_sweep(base_url):
    phase("1 - full UI-action sweep")
    c = Client(base_url)

    # --- identity & meta -------------------------------------------------
    _, health = c.request("GET", "/api/health", what="health")
    check(isinstance(health, dict) and health.get("ok") is True, "health ok flag", "ok=true", health)
    _, meta = c.request("GET", "/api/meta", what="meta")
    check(isinstance(meta, dict) and meta.get("statuses") == ["Not Assigned", "In Progress", "Ready", "Approved"],
          "meta statuses list", "4 lifecycle statuses", meta.get("statuses") if isinstance(meta, dict) else meta)
    check(isinstance(meta, dict) and set(meta.get("roles", [])) == {"supervisor", "staff", "employee"},
          "meta roles list", "3 roles", meta.get("roles") if isinstance(meta, dict) else meta)
    _, me = c.request("GET", "/api/me", what="me (anonymous)")
    check(isinstance(me, dict) and me.get("authenticated") is False, "anonymous /api/me", "authenticated=false", me)
    _, users = c.request("GET", "/api/users", what="users")
    user_names = [u.get("name") for u in users] if isinstance(users, list) else []
    check("Supervisor" in user_names and "Staff Member" in user_names and "Employee" in user_names,
          "seed users present", "Supervisor/Staff Member/Employee", user_names)

    c.request("POST", "/api/login", {"name": "Supervisor"}, what="login valid name")
    _, me2 = c.request("GET", "/api/me", what="me (logged in)")
    check(isinstance(me2, dict) and me2.get("name") == "Supervisor" and me2.get("role") == "supervisor",
          "session identity after login", "Supervisor/supervisor", me2)
    c.request("POST", "/api/login", {"name": "No Such Person"}, expect=(401,), what="login unknown name")
    c.request("POST", "/api/login", {"name": ""}, expect=(400,), what="login blank name")
    c.request("POST", "/api/logout", {}, what="logout")
    _, me3 = c.request("GET", "/api/me", what="me (after logout)")
    check(isinstance(me3, dict) and me3.get("authenticated") is False, "logout clears session",
          "authenticated=false", me3)

    # --- project creation ------------------------------------------------
    status, created = create_project(c, "Sweep Alpha")
    alpha_id = created.get("project_id") if isinstance(created, dict) else None
    check(isinstance(alpha_id, int), "create returns project_id", "int project_id", created)
    status_b, created_b = c.request("POST", "/api/projects",
                                    {"project_name": "Sweep Beta 2", "changed_by": "Stress Harness"},
                                    expect=(201,), label="POST /api/projects", what="create Sweep Beta 2")
    beta_id = created_b.get("project_id") if isinstance(created_b, dict) else None
    c.request("POST", "/api/projects", {"project_name": ""}, expect=(400,),
              label="POST /api/projects", what="create with missing name")
    dup_status, dup_body = c.request("POST", "/api/projects", {"project_name": "Sweep Alpha"},
                                     expect=(400, 409), label="POST /api/projects",
                                     what="create duplicate name")
    note("duplicate-name create behavior", f"status {dup_status}: {json.dumps(dup_body)[:120]}")
    junk_status, junk_body = c.request("POST", "/api/projects",
                                       {"project_name": "Sweep Junk Coords", "lead_x": "abc", "lead_y": "12,34"},
                                       expect=(201, 400), label="POST /api/projects",
                                       what="create with junk coordinates")
    if junk_status == 201:
        note("junk lead coordinates accepted", "POST /api/projects stores non-numeric lead_x/lead_y "
             "verbatim (no validation in workflow.projects.add_project)")
    status_bp, created_bp = c.request(
        "POST", "/api/projects",
        {"project_name": "Sweep BP Well", "pipeline_type": "bp", "business_plan_enabled": True,
         "business_plan_year": 2026, "changed_by": "Stress Harness"},
        expect=(201,), label="POST /api/projects", what="create bp-pipeline well")
    bp_id = created_bp.get("project_id") if isinstance(created_bp, dict) else None
    c.request("POST", "/api/projects",
              {"project_name": "Sweep Bad Year", "business_plan_enabled": True, "business_plan_year": 1900},
              expect=(400,), label="POST /api/projects", what="create with out-of-range BP year")

    # --- boards (the exact params views/pipeline.js sends) ---------------
    _, board = c.request("GET", board_params(pipeline_filter="prospect", search="",
                                             stage_filter="All", status_filter="All",
                                             owner_filter="All"),
                         label="GET /api/projects (board)", what="prospect board")
    names = [r.get("project_name") for r in board] if isinstance(board, list) else []
    check("Sweep Alpha" in names, "new lead on prospect board", "Sweep Alpha present", names)
    if isinstance(board, list) and board:
        row_keys = set(board[0].keys())
        check({"project_id", "project_name", "current_stage", "current_task",
               "overall_status", "health"} <= row_keys,
              "board row projection shape", "trimmed list fields", sorted(row_keys))
    _, bp_board = c.request("GET", board_params(pipeline_filter="bp"),
                            label="GET /api/projects (board)", what="bp board")
    bp_names = [r.get("project_name") for r in bp_board] if isinstance(bp_board, list) else []
    check("Sweep BP Well" in bp_names, "bp well on bp board", "Sweep BP Well present", bp_names)

    # --- single-project reads --------------------------------------------
    _, proj = c.request("GET", f"/api/projects/{alpha_id}", label="GET /api/projects/<id>",
                        what="get project")
    check(isinstance(proj, dict) and proj.get("project_name") == "Sweep Alpha",
          "get project row", "Sweep Alpha", proj if not isinstance(proj, dict) else proj.get("project_name"))
    _, detail = c.request("GET", f"/api/projects/{alpha_id}/detail",
                          label="GET /api/projects/<id>/detail", what="project detail")
    check(isinstance(detail, dict) and {"project", "tasks", "completion", "fields",
                                        "overview", "formations"} <= set(detail.keys()),
          "detail payload shape", "project/tasks/completion/fields/overview/formations",
          sorted(detail.keys()) if isinstance(detail, dict) else detail)
    tasks = get_tasks(c, alpha_id)
    check(len(tasks) == TEMPLATE_TASK_COUNT, "task materialization count",
          TEMPLATE_TASK_COUNT, len(tasks))
    _, completion = c.request("GET", f"/api/projects/{alpha_id}/completion",
                              label="GET /api/projects/<id>/completion", what="completion")
    check(isinstance(completion, dict) and completion.get("percent") == 0.0,
          "fresh project completion", 0.0, completion)
    c.request("GET", f"/api/projects/{alpha_id}/dynamic-fields",
              label="GET /api/projects/<id>/dynamic-fields", what="project dynamic fields")
    c.request("GET", f"/api/projects/{alpha_id}/formations",
              label="GET /api/projects/<id>/formations", what="formations list")
    c.request("GET", "/api/projects/999999", expect=(404,), label="GET /api/projects/<id>",
              what="get missing project")
    c.request("GET", "/api/tasks/999999", expect=(404,), label="GET /api/tasks/<id>",
              what="get missing task")

    # --- assignment -------------------------------------------------------
    t1 = task_by_name(c, alpha_id, "Lead Assessment")
    t2 = task_by_name(c, alpha_id, "Reservoir CoS")
    t_cos = task_by_name(c, alpha_id, "Reservoir CoS")
    t_stake = task_by_name(c, alpha_id, "Moving Tolerance")
    t_quick = task_by_name(c, alpha_id, "Quicklook Logs")
    status, out = c.request("POST", f"/api/tasks/{t1['task_id']}/assign",
                            {"assignee": "Staff Member", "cascade": False,
                             "revision": t1.get("revision"), "changed_by": "Stress Harness"},
                            label="POST /api/tasks/<id>/assign", what="assign no-cascade")
    if status == 200:
        count_mutation(alpha_id)
    check(isinstance(out, dict) and out.get("task", {}).get("status") == "In Progress",
          "assignment moves task to In Progress", "In Progress", out)
    # Creation auto-assignment already put the RULE steps (Seismic Signature
    # Validation -> Tahira; every Pre-Well Delivery step -> Saad/Salem) In
    # Progress, so the cascade only fills the rows still Not Assigned here.
    before_cascade = {t["task_id"]: t.get("status") for t in get_tasks(c, alpha_id)}
    status, out = c.request("POST", f"/api/tasks/{t2['task_id']}/assign",
                            {"assignee": "Employee", "cascade": True,
                             "revision": t2.get("revision"), "changed_by": "Stress Harness"},
                            label="POST /api/tasks/<id>/assign", what="assign with cascade")
    if status == 200:
        count_mutation(alpha_id)
    after = get_tasks(c, alpha_id)
    cascaded = [t for t in after if t["sequence_no"] > t2["sequence_no"]
                and t["sequence_no"] <= PROSPECT_TASK_COUNT]
    check(len(cascaded) > 0 and all(
              t.get("status") == "In Progress"
              and (t.get("assigned_to") == "Employee"
                   or before_cascade.get(t["task_id"]) != "Not Assigned") for t in cascaded),
          "cascade assigns later prospect steps", "all In Progress; Not Assigned rows -> Employee",
          [(t["task_name"], t.get("status"), t.get("assigned_to")) for t in cascaded if t.get("status") != "In Progress"])
    bp_side = [t for t in after if t["sequence_no"] > PROSPECT_TASK_COUNT]
    check(len(bp_side) > 0 and all(t.get("status") == "Not Assigned" for t in bp_side),
          "cascade never leaves the applicable pipeline", "BP steps untouched",
          [(t["task_name"], t.get("status")) for t in bp_side if t.get("status") != "Not Assigned"])
    c.request("POST", f"/api/tasks/{t1['task_id']}/assign",
              {"assignee": "Nobody Real", "cascade": False}, expect=(400,),
              label="POST /api/tasks/<id>/assign", what="assign unknown user")

    # --- dynamic field saves ----------------------------------------------
    c.request("PATCH", f"/api/tasks/{t1['task_id']}/dynamic-fields",
              {"fields": {"p90_area_km2": "12.5", "p10_area_km2": "30"}, "changed_by": "Stress Harness"},
              label="PATCH /api/tasks/<id>/dynamic-fields", what="save number fields")
    _, fields = c.request("GET", f"/api/tasks/{t1['task_id']}/dynamic-fields",
                          label="GET /api/tasks/<id>/dynamic-fields", what="read fields back")
    check(isinstance(fields, dict) and fields.get("p90_area_km2") == "12.5",
          "number field round-trip", "12.5", fields)
    c.request("PATCH", f"/api/tasks/{t_quick['task_id']}/dynamic-fields",
              {"fields": {"active_drilling": "true", "quicklook_pay_thickness_ft": "42"},
               "changed_by": "Stress Harness"},
              label="PATCH /api/tasks/<id>/dynamic-fields", what="save checkbox field")
    save_task(c, t1["task_id"], alpha_id, fields={"formation_thickness_ft": "85"},
              comments="thickness noted via full save")
    # comments + text field via the UI's updateTask shape
    save_task(c, t1["task_id"], alpha_id, fields={"reservoir_area_notes": "sweep text value"},
              comments="sweep comment")
    t1_now = fresh_task(c, t1["task_id"])
    check(t1_now.get("comments") == "sweep comment", "comments persisted", "sweep comment",
          t1_now.get("comments"))

    # Reservoir CoS: valid repeatable rows -> computed reservoir_cos_pct
    rows = [{"pull_up": "Yes", "amplitude_ratio": "0.5", "base_tight_sarah": "0.7", "ar_number": "AR-1"},
            {"pull_up": "No", "amplitude_ratio": "0.1", "base_tight_sarah": "0.2"}]
    save_task(c, t_cos["task_id"], alpha_id, fields={"reservoir_cos_rows": json.dumps(rows)})
    _, cos_fields = c.request("GET", f"/api/tasks/{t_cos['task_id']}/dynamic-fields",
                              label="GET /api/tasks/<id>/dynamic-fields", what="reservoir cos read-back")
    try:
        stored_rows = json.loads(cos_fields.get("reservoir_cos_rows", "[]"))
    except Exception:
        stored_rows = []
    check(len(stored_rows) == 2 and all("reservoir_cos_pct" in r for r in stored_rows),
          "reservoir CoS rows computed by RF model", "reservoir_cos_pct on every row",
          cos_fields.get("reservoir_cos_rows") if isinstance(cos_fields, dict) else cos_fields)
    # malformed JSON -> 400
    save_task(c, t_cos["task_id"], alpha_id, fields={"reservoir_cos_rows": "{not json"},
              expect=(400,))
    # invalid pull-up -> 400 (field-specific validation)
    save_task(c, t_cos["task_id"], alpha_id,
              fields={"reservoir_cos_rows": json.dumps([{"pull_up": "maybe"}])}, expect=(400,))
    # The dynamic-fields endpoint does NOT recompute reservoir rows (save_task
    # is the computing path); record the behavior without failing.
    c.request("PATCH", f"/api/tasks/{t_cos['task_id']}/dynamic-fields",
              {"fields": {"reservoir_cos_bypass_probe": "1"}, "changed_by": "Stress Harness"},
              label="PATCH /api/tasks/<id>/dynamic-fields", what="dynamic-fields on cos task")

    # staking fields
    c.request("PATCH", f"/api/tasks/{t_stake['task_id']}/dynamic-fields",
              {"fields": {"staking_well_x": "532100.5", "staking_well_y": "2895120.1",
                          "staking_opt1_max_distance_m": "150", "staking_opt1_azimuth_deg": "45"},
               "changed_by": "Stress Harness"},
              label="PATCH /api/tasks/<id>/dynamic-fields", what="save staking fields")

    # --- transitions -------------------------------------------------------
    # submit -> approve on task 1; submit -> return on task 2
    t1_now = fresh_task(c, t1["task_id"])
    status, _b = c.request("POST", f"/api/tasks/{t1['task_id']}/transition",
                           {"action": "submit", "revision": t1_now.get("revision"),
                            "changed_by": "Stress Harness"},
                           label="POST /api/tasks/<id>/transition", what="submit task")
    if status == 200:
        count_mutation(alpha_id)
    t1_now = fresh_task(c, t1["task_id"])
    check(t1_now.get("status") == "Ready", "submit -> Ready", "Ready", t1_now.get("status"))
    status, _b = c.request("POST", f"/api/tasks/{t1['task_id']}/transition",
                           {"action": "approve", "revision": t1_now.get("revision"),
                            "changed_by": "Stress Harness"},
                           label="POST /api/tasks/<id>/transition", what="approve task")
    if status == 200:
        count_mutation(alpha_id)
    t1_now = fresh_task(c, t1["task_id"])
    check(t1_now.get("status") == "Approved" and t1_now.get("actual_finish"),
          "approve -> Approved with actual_finish", "Approved + date",
          (t1_now.get("status"), t1_now.get("actual_finish")))
    t2_now = fresh_task(c, t2["task_id"])
    status, _b = c.request("POST", f"/api/tasks/{t2['task_id']}/transition",
                           {"action": "submit", "revision": t2_now.get("revision"),
                            "changed_by": "Stress Harness"},
                           label="POST /api/tasks/<id>/transition", what="submit task 2")
    if status == 200:
        count_mutation(alpha_id)
    t2_now = fresh_task(c, t2["task_id"])
    status, _b = c.request("POST", f"/api/tasks/{t2['task_id']}/transition",
                           {"action": "return", "revision": t2_now.get("revision"),
                            "changed_by": "Stress Harness"},
                           label="POST /api/tasks/<id>/transition", what="return task 2")
    if status == 200:
        count_mutation(alpha_id)
    t2_now = fresh_task(c, t2["task_id"])
    check(t2_now.get("status") == "In Progress", "return -> In Progress", "In Progress",
          t2_now.get("status"))
    # wrong-state and unknown action
    c.request("POST", f"/api/tasks/{t2['task_id']}/transition",
              {"action": "approve", "revision": t2_now.get("revision")}, expect=(400,),
              label="POST /api/tasks/<id>/transition", what="approve from In Progress (invalid)")
    c.request("POST", f"/api/tasks/{t2['task_id']}/transition",
              {"action": "teleport"}, expect=(400,),
              label="POST /api/tasks/<id>/transition", what="unknown transition action")

    # --- priority, generic PATCH, stale revision ---------------------------
    status, _b = c.request("PATCH", f"/api/tasks/{t2['task_id']}/priority",
                           {"priority": "High", "changed_by": "Stress Harness"},
                           label="PATCH /api/tasks/<id>/priority", what="set priority high")
    t2_now = fresh_task(c, t2["task_id"])
    check(t2_now.get("priority") == "High", "priority persisted",
          "High", t2_now.get("priority"))
    save_task(c, t2["task_id"], alpha_id, extra={"status": "Bogus Status"}, expect=(400,))
    save_task(c, t2["task_id"], alpha_id, revision=0, expect=(409,))  # stale write
    c.request("POST", f"/api/tasks/{t2['task_id']}/assign",
              {"assignee": "Employee", "cascade": False, "revision": 0}, expect=(409,),
              label="POST /api/tasks/<id>/assign", what="stale assign")
    c.request("POST", f"/api/tasks/{t2['task_id']}/transition",
              {"action": "submit", "revision": "abc"}, expect=(400,),
              label="POST /api/tasks/<id>/transition", what="non-integer revision")

    # --- rename / flags / formations --------------------------------------
    c.request("PATCH", f"/api/projects/{alpha_id}/rename",
              {"new_name": "Sweep Alpha Renamed", "changed_by": "Stress Harness"},
              label="PATCH /api/projects/<id>/rename", what="rename project")
    _, proj = c.request("GET", f"/api/projects/{alpha_id}", label="GET /api/projects/<id>",
                        what="get renamed project")
    check(isinstance(proj, dict) and proj.get("project_name") == "Sweep Alpha Renamed",
          "rename persisted", "Sweep Alpha Renamed", proj.get("project_name") if isinstance(proj, dict) else proj)
    c.request("PATCH", f"/api/projects/{alpha_id}/rename", {"new_name": "Sweep BP Well"},
              expect=(400,), label="PATCH /api/projects/<id>/rename", what="rename to duplicate")
    c.request("PATCH", f"/api/projects/{alpha_id}/rename", {"new_name": ""},
              expect=(400,), label="PATCH /api/projects/<id>/rename", what="rename to blank")

    status, _b = c.request("PATCH", f"/api/projects/{alpha_id}/flags",
                           {"business_plan_enabled": True, "business_plan_year": 2027,
                            "changed_by": "Stress Harness"},
                           label="PATCH /api/projects/<id>/flags", what="promote to BP")
    if status == 200:
        count_mutation(alpha_id)
    status, _b = c.request("PATCH", f"/api/projects/{alpha_id}/flags",
                           {"active_well_enabled": True, "changed_by": "Stress Harness"},
                           label="PATCH /api/projects/<id>/flags", what="set active well flag")
    if status == 200:
        count_mutation(alpha_id)
    _, bp_rows = c.request("GET", "/api/business-plan/rows",
                           label="GET /api/business-plan/rows", what="business plan rows")
    bp_wells = [r.get("well_name") for r in bp_rows] if isinstance(bp_rows, list) else []
    check("Sweep Alpha Renamed" in bp_wells, "promoted project in business-plan rows",
          "Sweep Alpha Renamed listed", bp_wells)
    _, pf = c.request("GET", "/api/portfolio/rows?year=All&activity=All",
                      label="GET /api/portfolio/rows", what="portfolio rows")
    pf_rows = pf.get("rows", []) if isinstance(pf, dict) else (pf if isinstance(pf, list) else [])
    pf_names = json.dumps(pf_rows)
    check("Sweep Alpha Renamed" in pf_names, "promoted project in portfolio rows",
          "Sweep Alpha Renamed listed", pf_names[:200])
    c.request("GET", "/api/portfolio/rows?year=2027&activity=All",
              label="GET /api/portfolio/rows", what="portfolio year filter")
    c.request("GET", "/api/portfolio/rows?year=abc&activity=All", expect=(400,),
              label="GET /api/portfolio/rows", what="portfolio junk year")

    # formations
    c.request("PUT", f"/api/projects/{alpha_id}/formations",
              {"phase": "quicklook",
               "rows": [{"formation": "SARH", "top_tvdss_ft": "100", "base_tvdss_ft": "180",
                         "thickness_ft": "80", "porosity_pct": "12", "fluid": "Gas"},
                        {"formation": "QASM", "pay_ft": "35"}],
               "changed_by": "Stress Harness", "source_task_id": t_quick["task_id"]},
              label="PUT /api/projects/<id>/formations", what="formations upsert")
    _, formations = c.request("GET", f"/api/projects/{alpha_id}/formations",
                              label="GET /api/projects/<id>/formations", what="formations read-back")
    got = {(r.get("formation"), r.get("phase")) for r in formations} if isinstance(formations, list) else set()
    check(("SARH", "quicklook") in got and ("QASM", "quicklook") in got,
          "formation rows round-trip", "SARH+QASM quicklook", sorted(got))
    junk_status, junk_form = c.request(
        "PUT", f"/api/projects/{alpha_id}/formations",
        {"phase": "quicklook", "rows": [{"formation": "SARH", "top_tvdss_ft": "junk"}]},
        expect=(400,), label="PUT /api/projects/<id>/formations", what="formations junk numeric")
    detail_msg = junk_form.get("detail", "") if isinstance(junk_form, dict) else str(junk_form)
    check("top_tvdss_ft" in detail_msg, "junk numeric error names the field",
          "message contains top_tvdss_ft", detail_msg)
    c.request("PUT", f"/api/projects/{alpha_id}/formations",
              {"phase": "bogus_phase", "rows": []}, expect=(400,),
              label="PUT /api/projects/<id>/formations", what="formations unknown phase")
    c.request("PUT", f"/api/projects/{alpha_id}/formations",
              {"phase": "quicklook", "rows": [{"formation": "SARH", "not_a_field": 1}]},
              expect=(400,), label="PUT /api/projects/<id>/formations",
              what="formations unknown field key")

    # component folder link (may legitimately 404 when share roots are absent)
    cf_status, cf_body = c.request("GET",
                                   f"/api/projects/{alpha_id}/component-folder/{t1['task_id']}",
                                   expect=(200, 404), label="GET .../component-folder/<tid>",
                                   what="component folder link")
    if cf_status == 404:
        note("component-folder returned 404", json.dumps(cf_body)[:150])

    # activity
    _, activity = c.request("GET", "/api/activity", label="GET /api/activity", what="activity all")
    check(isinstance(activity, list) and len(activity) > 0, "activity log populated",
          "> 0 rows", len(activity) if isinstance(activity, list) else activity)
    _, proj_activity = c.request("GET", f"/api/activity?project_id={alpha_id}",
                                 label="GET /api/activity", what="activity project filter")
    check(isinstance(proj_activity, list) and proj_activity
          and all(r.get("project_id") == alpha_id for r in proj_activity),
          "activity project filter scoped", f"all rows project {alpha_id}",
          [r.get("project_id") for r in proj_activity][:5] if isinstance(proj_activity, list) else proj_activity)

    # excel export
    xl_status, xl_body = c.request("GET", "/api/export/excel", label="GET /api/export/excel",
                                   what="excel export", binary=True)
    check(xl_status == 200 and isinstance(xl_body, bytes) and xl_body[:2] == b"PK",
          "excel export is a zip container", "PK magic",
          xl_body[:8] if isinstance(xl_body, bytes) else xl_body)

    # --- archive (DELETE) / restore ---------------------------------------
    status, del_body = c.request("DELETE", f"/api/projects/{beta_id}",
                                 label="DELETE /api/projects/<id>", what="delete (archive) project")
    if status == 200:
        count_mutation(beta_id)
    check(isinstance(del_body, dict) and del_body.get("archived") is True,
          "delete responds archived=true", "archived:true", del_body)
    g_status, g_body = c.request("GET", f"/api/projects/{beta_id}", expect=(200, 404),
                                 label="GET /api/projects/<id>", what="get archived project")
    if g_status == 200:
        note("DELETE is a soft archive", "GET after DELETE returns 200 with archived="
             f"{g_body.get('archived') if isinstance(g_body, dict) else '?'} (route archives, never hard-deletes)")
    _, board2 = c.request("GET", board_params(pipeline_filter="All"),
                          label="GET /api/projects (board)", what="board after archive")
    names2 = [r.get("project_name") for r in board2] if isinstance(board2, list) else []
    check("Sweep Beta 2" not in names2, "archived project off the board", "absent", names2)
    status, _b = c.request("PATCH", f"/api/projects/{beta_id}/restore",
                           {"changed_by": "Stress Harness"},
                           label="PATCH /api/projects/<id>/restore", what="restore project")
    if status == 200:
        count_mutation(beta_id)
    _, board3 = c.request("GET", board_params(pipeline_filter="All"),
                          label="GET /api/projects (board)", what="board after restore")
    names3 = [r.get("project_name") for r in board3] if isinstance(board3, list) else []
    check("Sweep Beta 2" in names3, "restored project back on the board", "present", names3)

    # --- full approval path (for the Phase 3 completion invariant) ---------
    _, created_full = create_project(c, "Full Approval Path")
    full_id = created_full.get("project_id") if isinstance(created_full, dict) else None
    first = task_by_name(c, full_id, "Lead Assessment")
    status, _b = c.request("POST", f"/api/tasks/{first['task_id']}/assign",
                           {"assignee": "Supervisor", "cascade": True,
                            "revision": first.get("revision"), "changed_by": "Stress Harness"},
                           label="POST /api/tasks/<id>/assign", what="assign full path")
    if status == 200:
        count_mutation(full_id)
    # Completion still has twelve visible items: four field-derived Lead
    # Assessment checkpoints plus eight real tasks. Fill every checkpoint so
    # the later lifecycle approval walk genuinely represents a 100% lead.
    c.request("PATCH", f"/api/tasks/{first['task_id']}/dynamic-fields", {
        "fields": {
            "p90_area_km2": "5", "p10_area_km2": "10",
            "reservoir_thickness_ft": "50", "formation_thickness_ft": "100",
            "grv_p90_thousand_acre_ft": "100", "grv_p10_thousand_acre_ft": "200",
            "polygons_surfaces_loaded": "1", "lead_piip_gas_mean": "25",
        },
        "changed_by": "Stress Harness",
    }, label="PATCH /api/tasks/<id>/dynamic-fields", what="fill Lead Assessment checkpoints")
    for task in get_tasks(c, full_id):
        if task["sequence_no"] > PROSPECT_TASK_COUNT:
            continue
        for action in ("submit", "approve"):
            row = fresh_task(c, task["task_id"])
            s, _x = c.request("POST", f"/api/tasks/{task['task_id']}/transition",
                              {"action": action, "revision": row.get("revision"),
                               "changed_by": "Stress Harness"},
                              label="POST /api/tasks/<id>/transition",
                              what=f"{action} {task['task_name']}", quiet=True)
            if s == 200:
                count_mutation(full_id)
    _, full_completion = c.request("GET", f"/api/projects/{full_id}/completion",
                                   label="GET /api/projects/<id>/completion",
                                   what="full path completion")
    check(isinstance(full_completion, dict) and full_completion.get("percent") == 100.0,
          "fully approved prospect reads 100%", 100.0, full_completion)
    _, full_proj = c.request("GET", f"/api/projects/{full_id}", label="GET /api/projects/<id>",
                             what="full path project")
    check(isinstance(full_proj, dict) and full_proj.get("overall_status") == "Completed",
          "fully approved prospect derives Completed", "Completed",
          full_proj.get("overall_status") if isinstance(full_proj, dict) else full_proj)
    _, board4 = c.request("GET", board_params(pipeline_filter="prospect"),
                          label="GET /api/projects (board)", what="board excludes matured lead")
    names4 = [r.get("project_name") for r in board4] if isinstance(board4, list) else []
    check("Full Approval Path" not in names4, "matured lead leaves the prospect board",
          "absent", names4)

    return {"alpha_id": alpha_id, "beta_id": beta_id, "bp_id": bp_id, "full_id": full_id}


# ---------------------------------------------------------------------------
# Phase 2: concurrency storm
# ---------------------------------------------------------------------------

def run_storm(base_url, writers, readers, duration):
    phase("2 - concurrency storm")
    setup = Client(base_url)
    _, created = create_project(setup, "Storm Target")
    storm_id = created.get("project_id") if isinstance(created, dict) else None
    target = task_by_name(setup, storm_id, "Lead Assessment")
    setup.request("POST", f"/api/tasks/{target['task_id']}/assign",
                  {"assignee": "Staff Member", "cascade": False,
                   "revision": target.get("revision"), "changed_by": "Stress Harness"},
                  label="POST /api/tasks/<id>/assign", what="storm setup assign")
    count_mutation(storm_id)
    task_id = target["task_id"]

    # (b) concurrent distinct-name creates -----------------------------------
    print("burst: concurrent distinct-name creates")
    distinct_ids = []

    def create_distinct(i):
        client = Client(base_url)
        s, body = client.request("POST", "/api/projects",
                                 {"project_name": f"Storm Distinct {i}", "changed_by": "Stress Harness"},
                                 expect=(201,), label="POST /api/projects",
                                 what=f"concurrent distinct create {i}")
        return body.get("project_id") if isinstance(body, dict) else None

    with ThreadPoolExecutor(max_workers=6) as pool:
        distinct_ids = [pid for pid in pool.map(create_distinct, range(6)) if pid]
    for pid in distinct_ids:
        n = len(get_tasks(setup, pid))
        check(n == TEMPLATE_TASK_COUNT, f"concurrent create materialized tasks (project {pid})",
              TEMPLATE_TASK_COUNT, n)

    # (c) concurrent SAME-name creates ----------------------------------------
    print("burst: concurrent same-name creates")

    def create_same(_i):
        client = Client(base_url)
        status, body, _sec = client.raw("POST", "/api/projects",
                                        {"project_name": "Storm Duplicate", "changed_by": "Stress Harness"})
        STATS.record("POST /api/projects", status, _sec)
        return status, body.decode("utf-8", errors="replace")[:200]

    with ThreadPoolExecutor(max_workers=8) as pool:
        same_results = list(pool.map(create_same, range(8)))
    same_statuses = [s for s, _ in same_results]
    created_n = same_statuses.count(201)
    clean_4xx = sum(1 for s in same_statuses if 400 <= s < 500)
    check(created_n == 1, "same-name race: exactly one 201", 1, f"{created_n} of {same_statuses}")
    check(created_n + clean_4xx == len(same_statuses),
          "same-name race: losers get clean 4xx (no 500)", "all 201/4xx", same_statuses,
          body="; ".join(sorted({b for s, b in same_results if s >= 500})))
    note("same-name concurrent create behavior", f"statuses: {same_statuses}")

    # main storm ---------------------------------------------------------------
    print(f"storm: {writers} writers, 3 contenders, {readers} readers, 2 login churners, "
          f"{duration:.0f}s")
    stop_at = time.time() + duration
    counters = {"writer_ok": 0, "writer_conflict": 0, "contender": 0, "reader": 0, "churn": 0}

    def bump(key):
        with _LOCK:
            counters[key] += 1

    def writer_loop(worker):
        client = Client(base_url)
        i = 0
        while time.time() < stop_at:
            i += 1
            _, task = client.request("GET", f"/api/tasks/{task_id}", label="GET /api/tasks/<id>",
                                     quiet=True, what="storm read task")
            if not isinstance(task, dict):
                continue
            payload = {"comments": f"writer {worker} save {i}",
                       "priority": task.get("priority") or "Medium",
                       "fields": {"stress_counter": f"{worker}-{i}",
                                  "reservoir_area_notes": f"storm w{worker} i{i}"},
                       "revision": task.get("revision"), "changed_by": "Stress Harness"}
            s, _b = client.request("PATCH", f"/api/tasks/{task_id}", payload,
                                   expect=(200, 409), label="PATCH /api/tasks/<id>",
                                   what="storm same-task save", quiet=True)
            if s == 200:
                count_mutation(storm_id)
                bump("writer_ok")
            elif s == 409:
                bump("writer_conflict")

    def contender_loop(worker):
        client = Client(base_url)
        while time.time() < stop_at:
            _, task = client.request("GET", f"/api/tasks/{task_id}", label="GET /api/tasks/<id>",
                                     quiet=True, what="storm read task")
            if not isinstance(task, dict):
                continue
            status_now = task.get("status")
            rev = task.get("revision")
            if status_now == "In Progress":
                action_payload = {"action": "submit", "revision": rev, "changed_by": "Stress Harness"}
                s, _b = client.request("POST", f"/api/tasks/{task_id}/transition", action_payload,
                                       expect=(200, 400, 409), label="POST /api/tasks/<id>/transition",
                                       what="storm transition", quiet=True)
            elif status_now == "Ready":
                action = "approve" if worker % 2 == 0 else "return"
                s, _b = client.request("POST", f"/api/tasks/{task_id}/transition",
                                       {"action": action, "revision": rev, "changed_by": "Stress Harness"},
                                       expect=(200, 400, 409), label="POST /api/tasks/<id>/transition",
                                       what="storm transition", quiet=True)
            else:  # Approved (or racing): reset via a status save, and re-assign
                s, _b = client.request("PATCH", f"/api/tasks/{task_id}",
                                       {"status": "In Progress", "revision": rev,
                                        "fields": {}, "changed_by": "Stress Harness"},
                                       expect=(200, 400, 409), label="PATCH /api/tasks/<id>",
                                       what="storm status reset", quiet=True)
            if s == 200:
                count_mutation(storm_id)
            bump("contender")
            assignee = ("Staff Member", "Employee", "Supervisor")[worker % 3]
            s2, _b2 = client.request("POST", f"/api/tasks/{task_id}/assign",
                                     {"assignee": assignee, "cascade": False,
                                      "revision": None, "changed_by": "Stress Harness"},
                                     expect=(200, 400, 409), label="POST /api/tasks/<id>/assign",
                                     what="storm assign", quiet=True)
            if s2 == 200:
                count_mutation(storm_id)

    def reader_loop(worker):
        client = Client(base_url)
        i = 0
        while time.time() < stop_at:
            i += 1
            client.request("GET", board_params(pipeline_filter="prospect"),
                           label="GET /api/projects (board)", what="storm board read", quiet=True)
            client.request("GET", "/api/portfolio/rows?year=All&activity=All",
                           label="GET /api/portfolio/rows", what="storm portfolio read", quiet=True)
            client.request("GET", f"/api/projects/{storm_id}/detail",
                           label="GET /api/projects/<id>/detail", what="storm detail read", quiet=True)
            client.request("GET", "/api/activity", label="GET /api/activity",
                           what="storm activity read", quiet=True)
            if i % 8 == worker % 8:
                s, body = client.request("GET", "/api/export/excel", label="GET /api/export/excel",
                                         what="storm excel export", binary=True, quiet=True)
                if s == 200 and not (isinstance(body, bytes) and body[:2] == b"PK"):
                    check(False, "storm excel export zip magic", "PK", body[:8])
            bump("reader")

    def churn_loop(worker):
        client = Client(base_url)
        names = ["Supervisor", "Staff Member", "Employee"]
        i = 0
        while time.time() < stop_at:
            i += 1
            name = names[(worker + i) % 3]
            client.request("POST", "/api/login", {"name": name}, label="POST /api/login",
                           what="storm login", quiet=True)
            _, me = client.request("GET", "/api/me", label="GET /api/me", what="storm me", quiet=True)
            if not (isinstance(me, dict) and me.get("name") == name):
                check(False, "storm session identity", name, me)
            if i % 5 == 0:
                client.request("POST", "/api/login", {"name": "Ghost User"}, expect=(401,),
                               label="POST /api/login", what="storm unknown login", quiet=True)
            client.request("POST", "/api/logout", {}, label="POST /api/logout",
                           what="storm logout", quiet=True)
            bump("churn")

    jobs = ([("w", writer_loop, i) for i in range(writers)]
            + [("c", contender_loop, i) for i in range(3)]
            + [("r", reader_loop, i) for i in range(readers)]
            + [("l", churn_loop, i) for i in range(2)])
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = [pool.submit(fn, i) for (_k, fn, i) in jobs]
        for fut in futures:
            exc = fut.exception()
            if exc is not None:
                check(False, "storm worker crashed (harness bug)", "no exception", repr(exc))
    print(f"storm tallies: {counters}")
    check(counters["writer_ok"] > 0, "storm writers achieved successful saves", "> 0",
          counters["writer_ok"])
    return {"storm_id": storm_id, "task_id": task_id, "distinct_ids": distinct_ids,
            "counters": counters}


# ---------------------------------------------------------------------------
# Phase 3: invariant audit
# ---------------------------------------------------------------------------

def run_audit(base_url, scratch_db, sweep_ids, storm_info):
    phase("3 - invariant audit")
    c = Client(base_url)
    if scratch_db is None:
        print("  (base-url mode: direct sqlite checks skipped; API cross-checks only)")
        conn = None
    else:
        conn = sqlite3.connect(f"file:{scratch_db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row

    if conn is not None:
        # exact template task count per project
        rows = conn.execute("""
            SELECT project_id, COUNT(*) AS n FROM project_tasks
            WHERE is_active = 1 GROUP BY project_id
        """).fetchall()
        bad = [(r["project_id"], r["n"]) for r in rows if r["n"] != TEMPLATE_TASK_COUNT]
        check(not bad, "every project has exactly the template task count",
              f"{TEMPLATE_TASK_COUNT} active tasks each", bad or f"{len(rows)} projects OK")

        # no duplicate (project_id, task_name)
        dups = conn.execute("""
            SELECT project_id, task_name, COUNT(*) AS n FROM project_tasks
            GROUP BY project_id, task_name HAVING n > 1
        """).fetchall()
        check(not dups, "no duplicate (project_id, task_name) rows", "none",
              [(d["project_id"], d["task_name"], d["n"]) for d in dups] or "none")

        # project revision >= tracked successful mutating writes
        for pid, mutations in sorted(MUTATION_COUNTS.items()):
            row = conn.execute("SELECT revision FROM projects WHERE project_id = ?", (pid,)).fetchone()
            if row is None:
                check(False, f"project {pid} exists for revision audit", "row present", "missing")
                continue
            revision = int(row["revision"] or 0)
            check(revision >= mutations,
                  f"project {pid} revision >= tracked successful writes",
                  f">= {mutations}", revision)
        print(f"  revision audit: {len(MUTATION_COUNTS)} projects "
              f"(tracked successful mutating writes vs projects.revision)")

        # task_history rows for the actions performed
        events = {r["action_type"] for r in conn.execute(
            "SELECT DISTINCT action_type FROM task_history").fetchall()}
        expected_events = {"Lead Created", "Component Assigned", "Component Update",
                           "Component Inputs Updated", "Component Submitted",
                           "Component Approved", "Component Returned", "Priority Update"}
        missing = expected_events - events
        check(not missing, "task_history contains every performed action type",
              sorted(expected_events), f"missing: {sorted(missing)}" if missing else "all present")

        # completion invariant for the fully-approved project
        full_id = sweep_ids.get("full_id")
        if full_id:
            open_rows = conn.execute("""
                SELECT COUNT(*) AS n FROM project_tasks
                WHERE project_id = ? AND is_active = 1 AND sequence_no <= ?
                  AND status != 'Approved'
            """, (full_id, PROSPECT_TASK_COUNT)).fetchone()["n"]
            check(open_rows == 0, "full-approval project: all applicable tasks Approved",
                  0, open_rows)
            completed_at = conn.execute("SELECT completed_at FROM projects WHERE project_id = ?",
                                        (full_id,)).fetchone()["completed_at"]
            check(bool(completed_at), "full-approval project: completed_at stamped",
                  "non-null", completed_at)

        # storm project: /completion agrees with the DB statuses
        storm_id = storm_info.get("storm_id")
        if storm_id:
            done = conn.execute("""
                SELECT SUM(CASE WHEN status = 'Approved' THEN 1 ELSE 0 END) AS done,
                       COUNT(*) AS total
                FROM project_tasks
                WHERE project_id = ? AND is_active = 1 AND sequence_no <= ?
            """, (storm_id, PROSPECT_TASK_COUNT)).fetchone()
            expected_pct = round((done["done"] or 0) / done["total"] * 100, 1) if done["total"] else 0.0
            _, api_completion = c.request("GET", f"/api/projects/{storm_id}/completion",
                                          label="GET /api/projects/<id>/completion",
                                          what="storm completion cross-check")
            check(isinstance(api_completion, dict) and api_completion.get("percent") == expected_pct,
                  "storm project completion agrees with task statuses", expected_pct, api_completion)

        # schema_version present
        try:
            sv = conn.execute("SELECT value FROM app_settings WHERE key = 'schema_version'").fetchone()
        except sqlite3.OperationalError as exc:
            sv = None
            check(False, "app_settings table readable", "table present", repr(exc))
        else:
            check(sv is not None and str(sv["value"]).strip() != "",
                  "schema_version present in app_settings", "non-empty value",
                  sv["value"] if sv else None)

        # DB / WAL file sizes (report only)
        for suffix in ("", "-wal", "-shm"):
            f = Path(str(scratch_db) + suffix)
            size = f.stat().st_size if f.exists() else 0
            print(f"  file size: {f.name}: {size:,} bytes")
            if suffix == "-wal" and size > 50 * 1024 * 1024:
                note("WAL file unusually large", f"{size:,} bytes")
        conn.close()

    # API cross-checks (work in both modes)
    storm_id = storm_info.get("storm_id")
    if storm_id:
        _, acts = c.request("GET", f"/api/activity?project_id={storm_id}",
                            label="GET /api/activity", what="storm activity cross-check")
        check(isinstance(acts, list) and len(acts) > 0,
              "activity reflects storm project history", "> 0 rows",
              len(acts) if isinstance(acts, list) else acts)
    for pid in storm_info.get("distinct_ids", []):
        n = len(get_tasks(c, pid))
        check(n == TEMPLATE_TASK_COUNT,
              f"concurrent-create project {pid} task count (API)",
              TEMPLATE_TASK_COUNT, n)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def print_report(started_at):
    print("\n" + "=" * 78)
    print("FINAL REPORT")
    print("=" * 78)
    print(f"\nTotal wall time: {time.time() - started_at:.1f}s")
    print("\nPer-phase results:")
    for name, slot in PHASES.items():
        verdict = "PASS" if slot["fail"] == 0 else "FAIL"
        print(f"  [{verdict}] {name}: {slot['pass']} passed, {slot['fail']} failed")

    errors = FINDINGS.errors()
    notes = [f for f in FINDINGS.items if f["severity"] == "note"]
    print(f"\nFindings: {len(errors)} error(s), {len(notes)} note(s)")
    for f in errors:
        print(f"  [ERROR] {f['what']}\n          expected: {f['expected']}\n"
              f"          got:      {f['got']}")
        if f["body"]:
            print(f"          body:     {f['body'][:200]}")
    for f in notes:
        print(f"  [note ] {f['what']}: {f['body'][:200]}")

    print("\nLatency table:")
    print(STATS.table())

    ok = not errors
    print(f"\nVERDICT: {'PASS - no error-severity findings' if ok else 'FAIL - error-severity findings present'}")
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Stress/exercise the segments dashboard "
                                                 "against a scratch database.")
    parser.add_argument("--port", type=int, default=8022, help="port for the self-booted server")
    parser.add_argument("--keep-db", action="store_true",
                        help="keep the temp dir (scratch DB + server log) and print its path")
    parser.add_argument("--writers", type=int, default=4, help="storm writer threads")
    parser.add_argument("--readers", type=int, default=4, help="storm reader threads")
    parser.add_argument("--duration-seconds", type=float, default=8.0,
                        help="storm phase duration")
    parser.add_argument("--base-url", default=None,
                        help="target an ALREADY-RUNNING server instead of self-booting "
                             "(REQUIRES --allow-mutation; the harness writes data)")
    parser.add_argument("--allow-mutation", action="store_true",
                        help="required with --base-url: acknowledge the target server's "
                             "database WILL be mutated")
    args = parser.parse_args()

    started_at = time.time()
    proc = None
    temp_dir = None
    scratch_db = None

    if args.base_url:
        if not args.allow_mutation:
            print("REFUSING: --base-url targets a live server and this harness WRITES data "
                  "(creates projects, saves fields, transitions tasks).\n"
                  "Re-run with --allow-mutation if you accept that.")
            return 2
        print("!" * 78)
        print("!! WARNING: targeting an already-running server at "
              f"{args.base_url}\n!! This run WILL WRITE test data into that server's database.")
        print("!" * 78)
        base_url = args.base_url.rstrip("/")
        phase("0 - boot (skipped: --base-url)")
        client = Client(base_url)
        health = wait_for_health(client, None, Path(os.devnull), timeout=10)
        print(f"  target healthy: {health}")
        tally(True)
    else:
        if not VENV_PY.exists():
            print(f"venv interpreter not found: {VENV_PY}")
            return 2
        phase("0 - boot")
        real_stat = REAL_DB.stat() if REAL_DB.exists() else None
        temp_dir = Path(tempfile.mkdtemp(prefix="segtracker-stress-"))
        print(f"  temp dir: {temp_dir}")
        proc, scratch_db, log_path = boot_server(args.port, temp_dir)
        base_url = f"http://127.0.0.1:{args.port}"
        client = Client(base_url)
        health = wait_for_health(client, proc, log_path)
        print(f"  server healthy: {json.dumps(health)[:160]}")
        db_display = str(health.get("db", ""))
        if str(REAL_DB) in db_display:
            print("ABORT: the booted server reports the PRODUCTION DB path. Killing it.")
            proc.terminate()
            return 2
        check(str(temp_dir) in db_display or str(scratch_db) in db_display,
              "server bound to the scratch DB", f"db display contains {scratch_db}", db_display)
        check(scratch_db.exists(), "scratch DB file created", "exists", scratch_db.exists())

    exit_code = 1
    try:
        sweep_ids = run_sweep(base_url)
        storm_info = run_storm(base_url, args.writers, args.readers, args.duration_seconds)
        run_audit(base_url, scratch_db, sweep_ids, storm_info)
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        if not args.base_url:
            now_stat = REAL_DB.stat() if REAL_DB.exists() else None
            same = (real_stat is None and now_stat is None) or (
                real_stat is not None and now_stat is not None
                and real_stat.st_size == now_stat.st_size
                and real_stat.st_mtime == now_stat.st_mtime)
            check(same, "production pipeline_tracker.db untouched",
                  "size/mtime unchanged",
                  "unchanged" if same else f"CHANGED: {real_stat} -> {now_stat}")
        exit_code = print_report(started_at)
        if temp_dir is not None:
            if args.keep_db:
                print(f"\nScratch data kept at: {temp_dir}")
            else:
                shutil.rmtree(temp_dir, ignore_errors=True)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
