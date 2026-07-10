"""Flask application entrypoint and HTTP routes.

Thin layer only: each route opens the per-request SQLAlchemy session (bound to
``flask.g`` in db.py) and delegates to the domain modules -- workflow (lifecycle),
reporting (dashboards), folders (share links) and export_excel. The JSON shapes,
status codes and error ``detail`` strings are the public API contract.

Error handling is CENTRALIZED in the errorhandlers below -- routes do not wrap
calls in try/except. The mapping:
- ValueError                  -> 400 (validation messages are user-facing; verbatim)
- workflow.StaleRevisionError -> 409 (optimistic-lock conflict; exact message)
- FileNotFoundError           -> 404
- anything else (incl. other RuntimeErrors) -> 500 with a GENERIC detail; the
  real traceback goes to the server log only. Internal exception text must
  never reach the client.
Explicit not-found responses (project/task lookups) return their exact strings
directly from the route.

Identity: POST /api/login matches the supplied name (case-insensitively)
against the ``users`` table (seeded from config.SEED_USERS) and stores the
canonical name plus role in the signed Flask session; unknown names are 401.
The passcode check (config.SHARED_PASSCODE) still applies first when
configured. ``actor()`` stamps the session name into every changed_by, falling
back to the client-supplied value for anonymous users so the unmodified
front-end keeps working. When config.AUTH_REQUIRED is on, every /api/* route
except /api/health, /api/login, /api/logout, /api/me and /api/users requires a
session. Role gating: ``current_role()`` / ``require_role()`` (PermissionError
-> 403 via the centralized handler).
"""
from __future__ import annotations

import os
import secrets
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Optional

from flask import Flask, jsonify, request, send_file, send_from_directory
from flask import session as flask_session
from werkzeug.exceptions import HTTPException

import config
import db
import export_excel
import folders
import reporting
import workflow

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")
app.secret_key = config.SECRET_KEY
# The session cookie only carries the display name; keep it script-inaccessible
# and same-site.
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = config.SESSION_COOKIE_SECURE
# Bootstrap schema/migrations once when the application process starts.
db.init_db()
app.teardown_appcontext(db.remove_session)


def json_response(data, status=200):
    response = jsonify(data)
    response.status_code = status
    return response


def error_response(message, status=400):
    return json_response({"detail": str(message) if message else "Request failed"}, status)


def actor(payload) -> str:
    """The name to record as changed_by for the current request.

    A logged-in session identity always wins (users cannot spoof someone else
    via the request body). Anonymous requests fall back to the client-supplied
    ``changed_by`` exactly as before -- byte-identical legacy behavior.
    """
    name = flask_session.get("name")
    if name:
        return name
    return (payload or {}).get("changed_by", "Web User")


# ---------------------------------------------------------------------------
# Centralized error handling (the ONLY place exceptions become HTTP responses)
# ---------------------------------------------------------------------------

@app.errorhandler(ValueError)
def handle_validation_error(exc):
    """Domain validation failures; their messages are written for end users."""
    return error_response(exc, 400)


@app.errorhandler(workflow.StaleRevisionError)
def handle_conflict_error(exc):
    """Optimistic-lock conflicts -> 409 (message shown to the user verbatim).

    Deliberately NOT plain RuntimeError: other RuntimeErrors are internal
    failures and fall through to the generic 500 handler.
    """
    return error_response(exc, 409)


@app.errorhandler(FileNotFoundError)
def handle_missing_file(exc):
    """Folder/file lookups that fail resolve to 404."""
    return error_response(exc, 404)


@app.errorhandler(PermissionError)
def handle_forbidden(exc):
    """Role-gate failures (require_role) -> 403; the message is user-facing.

    PermissionError is reserved for authorization in this codebase: filesystem
    permission failures cannot reach here (folder creation is wrapped
    best-effort in its route), so a 403 always means "your role may not do
    this".
    """
    return error_response(exc, 403)


@app.errorhandler(Exception)
def handle_unexpected_error(exc):
    """Anything else is an internal bug: log the traceback, return a generic 500.

    No exception text ever reaches the client from this path.
    """
    if isinstance(exc, HTTPException):
        # Let Flask's own 404/405/... responses pass through untouched.
        return exc
    app.logger.exception("Unhandled error on %s %s", request.method, request.path)
    return error_response("Internal server error.", 500)


@app.after_request
def add_api_cache_headers(response):
    # API data must not be stale. Static assets retain normal browser caching.
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# ---------------------------------------------------------------------------
# Identity (session login) + optional API-wide enforcement
# ---------------------------------------------------------------------------

# Endpoints that must stay reachable without a session even when AUTH_REQUIRED
# is on: health checks, the login call itself, logout (idempotent, always 200)
# and /api/me (the front-end probes it to decide whether to show a login form).
# /api/users is also exempt: the login dialog needs the name list to render its
# dropdown BEFORE a session exists. Tradeoff: user names and roles become
# enumerable without auth. Accepted -- they are not secrets on the trusted
# internal network this app targets, and the alternative (free-typed names)
# reintroduces the typo/casing drift the users table exists to prevent.
_AUTH_EXEMPT_PATHS = {"/api/health", "/api/login", "/api/logout", "/api/me", "/api/users"}


def current_role() -> Optional[str]:
    """The permission role for the current request.

    Logged-in sessions carry the role stored at login (a pre-WS3 session that
    somehow has a name but no role degrades to least-privileged 'employee').
    With no session and AUTH_REQUIRED off (dev/test mode) everything runs as
    'supervisor' so an open instance behaves exactly as before roles existed.
    With AUTH_REQUIRED on, an anonymous request never reaches a role check --
    the before_request gate has already returned 401 -- so the None return is
    defensive only.
    """
    if flask_session.get("name"):
        return flask_session.get("role") or "employee"
    if not config.AUTH_REQUIRED:
        return "supervisor"
    return None


def require_role(*roles: str) -> None:
    """Raise PermissionError (-> 403) unless the current role is one of ``roles``.

    Consumers: POST /api/tasks/<id>/assign (supervisor, staff) and the
    approve/return actions of POST /api/tasks/<id>/transition (supervisor).
    """
    if current_role() not in roles:
        raise PermissionError("Forbidden: requires " + " or ".join(roles) + " role.")


@app.before_request
def require_login_when_enabled():
    """Return 401 for /api/* requests without a session when AUTH_REQUIRED is on.

    config.AUTH_REQUIRED is read here at request time (not captured at import)
    so tests can monkeypatch it. Static files and the index page stay open.
    """
    if not config.AUTH_REQUIRED:
        return None
    path = request.path
    if not path.startswith("/api/") or path in _AUTH_EXEMPT_PATHS:
        return None
    if flask_session.get("name"):
        return None
    return error_response("Authentication required.", 401)


@app.post("/api/login")
def login():
    """Start a session: {"name": ..., "passcode": ...} -> {"ok", "name", "role"}.

    Name is required (trimmed, 1-80 chars). The passcode is only checked when
    config.SHARED_PASSCODE is configured; otherwise login is name-only (see
    config.py for the trusted-network rationale). The name must then match an
    active row in the ``users`` table (case-insensitive); unknown names get
    401. The session stores the DB row's canonical casing and role, so audit
    trails never fork on "alice" vs "Alice".
    """
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name") or "").strip()
    if not name or len(name) > 80:
        raise ValueError("Name must be 1 to 80 characters.")
    if config.SHARED_PASSCODE:
        supplied = str(payload.get("passcode") or "")
        if not secrets.compare_digest(supplied, config.SHARED_PASSCODE):
            return error_response("Invalid passcode.", 401)
    user = workflow.find_active_user(db.get_session(), name)
    if not user:
        return error_response("Unknown user.", 401)
    flask_session["name"] = user["name"]
    flask_session["role"] = user["role"]
    return json_response({"ok": True, "name": user["name"], "role": user["role"]})


@app.post("/api/logout")
def logout():
    """End the session. Idempotent: always 200, even with no active session."""
    flask_session.clear()
    return json_response({"ok": True})


@app.get("/api/me")
def me():
    """Report the current session identity. Never 401 (even with AUTH_REQUIRED on).

    ``role`` is the session-stored role, NOT current_role(): an anonymous
    request in dev mode reports role None here so the front-end hides the
    signed-in chip, even though role checks would treat it as supervisor.
    """
    name: Optional[str] = flask_session.get("name")
    return json_response({
        "authenticated": bool(name),
        "name": name if name else None,
        "role": (flask_session.get("role") or "employee") if name else None,
    })


@app.get("/api/users")
def users():
    """Active users as [{name, role}] ordered by name (assignee/login dropdowns).

    Exempt from AUTH_REQUIRED -- see the _AUTH_EXEMPT_PATHS comment for the
    tradeoff.
    """
    session = db.get_session()
    return json_response(workflow.get_active_users(session))


@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/api/meta")
def meta():
    """Authoritative stage/status/role lists so the frontend never hardcodes them.

    Source of truth is workflow.py; the schema.js arrays are boot fallbacks only.
    """
    return json_response({
        "prospect_stages": workflow.PROSPECT_STAGES,
        "bp_stages": workflow.BP_EXECUTION_STAGES,
        "stage_order": workflow.STAGE_ORDER,
        "statuses": workflow.STATUSES,
        "roles": ["supervisor", "staff", "employee"],
    })


@app.get("/api/health")
def health():
    return json_response({"ok": True, "app": config.APP_NAME, "version": config.APP_VERSION,
                          "backend": "Flask", "db": db.current_display()})


@app.get("/api/projects")
def list_projects():
    session = db.get_session()
    return json_response(workflow.get_projects(
        session,
        request.args.get("search", ""),
        request.args.get("stage_filter", "All"),
        request.args.get("status_filter", "All"),
        request.args.get("owner_filter", "All"),
        request.args.get("health_filter", "All"),
        request.args.get("sort_key", "Well Name"),
        request.args.get("pipeline_filter", "All"),
    ))


@app.post("/api/projects")
def create_project():
    session = db.get_session()
    payload = request.get_json(silent=True) or {}
    project_id = workflow.add_project(
        session,
        payload.get("project_name", ""),
        payload.get("start_date", ""),
        payload.get("target_date", ""),
        actor(payload),
        # Coordinates are no longer collected in the UI; old API callers remain compatible.
        payload.get("lead_x"), payload.get("lead_y"),
        payload.get("business_plan_year"), bool(payload.get("business_plan_enabled")),
        bool(payload.get("active_well_enabled")), payload.get("pipeline_type", "prospect"),
    )
    # Folder creation is best-effort by design: an unavailable share must never
    # fail project creation. Documented exception to the no-try/except rule.
    try:
        folder_path = folders.ensure_well_folders(session, project_id)
    except Exception:
        folder_path = None
    return json_response({"project_id": project_id, "folder_path": folder_path}, 201)


@app.get("/api/projects/<int:project_id>")
def get_project(project_id):
    session = db.get_session()
    project = workflow.get_project(session, project_id)
    if not project:
        return error_response("Lead / well not found", 404)
    return json_response(project)


@app.get("/api/projects/<int:project_id>/detail")
def project_detail(project_id):
    session = db.get_session()
    project = workflow.get_project(session, project_id)
    if not project:
        return error_response("Lead / well not found", 404)
    return json_response({
        "project": project,
        "tasks": workflow.get_project_tasks(session, project_id),
        "completion": {"percent": workflow.project_completion_percent(session, project_id)},
        "fields": workflow.get_project_dynamic_field_map(session, project_id),
        "lead_summary": workflow.get_lead_summary_snapshot(session, project_id),
        # Derived per-project values (e.g. derisking = Total Chance of Success,
        # maintained by recalculate_presence_cos since the step's removal).
        "overview": workflow.get_project_overview(session, project_id),
        # Well-level formation rows (SARH/QASM/QWRH x quicklook/final).
        "formations": workflow.get_project_formations(session, project_id),
    })


@app.get("/api/projects/<int:project_id>/formations")
def get_formations(project_id):
    session = db.get_session()
    if not workflow.get_project(session, project_id):
        return error_response("Lead / well not found", 404)
    return json_response(workflow.get_project_formations(session, project_id))


@app.put("/api/projects/<int:project_id>/formations")
def put_formations(project_id):
    """Upsert well-level formation rows for one phase.

    No role gate: step-level assignment governs who edits formation data.
    """
    session = db.get_session()
    payload = request.get_json(silent=True) or {}
    formations = workflow.upsert_project_formations(
        session, project_id, payload.get("phase", ""), payload.get("rows"),
        actor(payload), payload.get("source_task_id"),
    )
    return json_response({"ok": True, "formations": formations})


@app.patch("/api/projects/<int:project_id>/rename")
def rename_project(project_id):
    session = db.get_session()
    payload = request.get_json(silent=True) or {}
    workflow.update_project_name(
        session, project_id, payload.get("new_name", ""), actor(payload),
        payload.get("lead_x"), payload.get("lead_y"), payload.get("business_plan_year"),
        payload.get("business_plan_enabled") if "business_plan_enabled" in payload else None,
        payload.get("active_well_enabled") if "active_well_enabled" in payload else None,
    )
    return json_response({"ok": True})


@app.delete("/api/projects/<int:project_id>")
def delete_project(project_id):
    """Archive by default so components, inputs and history remain recoverable."""
    session = db.get_session()
    workflow.archive_project(session, project_id, actor(None))
    return json_response({"ok": True, "archived": True})


@app.patch("/api/projects/<int:project_id>/restore")
def restore_project(project_id):
    session = db.get_session()
    payload = request.get_json(silent=True) or {}
    workflow.restore_project(session, project_id, actor(payload))
    return json_response({"ok": True, "archived": False})


@app.get("/api/projects/<int:project_id>/completion")
def completion(project_id):
    session = db.get_session()
    return json_response({"percent": workflow.project_completion_percent(session, project_id)})


@app.patch("/api/projects/<int:project_id>/flags")
def project_flags(project_id):
    session = db.get_session()
    payload = request.get_json(silent=True) or {}
    workflow.update_project_flags(
        session,
        project_id,
        payload.get("business_plan_enabled") if "business_plan_enabled" in payload else None,
        payload.get("active_well_enabled") if "active_well_enabled" in payload else None,
        payload.get("business_plan_year"), actor(payload),
    )
    return json_response({"ok": True})


@app.get("/api/projects/<int:project_id>/tasks")
def tasks(project_id):
    session = db.get_session()
    return json_response(workflow.get_project_tasks(session, project_id))


@app.get("/api/tasks/<int:task_id>")
def task(task_id):
    session = db.get_session()
    item = workflow.get_task(session, task_id)
    if not item:
        return error_response("Task not found", 404)
    return json_response(item)


@app.patch("/api/tasks/<int:task_id>")
def update_task(task_id):
    session = db.get_session()
    payload = request.get_json(silent=True) or {}
    task_after_save = workflow.save_task(session, task_id, payload, actor(payload))
    return json_response({"ok": True, "task": task_after_save})


@app.post("/api/tasks/<int:task_id>/assign")
def assign_task(task_id):
    """Assign a component (supervisor/staff only); cascade defaults to true."""
    require_role("supervisor", "staff")
    session = db.get_session()
    payload = request.get_json(silent=True) or {}
    cascade = payload.get("cascade")
    task_after = workflow.assign_task(
        session, task_id, payload.get("assignee", ""),
        True if cascade is None else bool(cascade),
        actor(payload), payload.get("revision"),
    )
    return json_response({"ok": True, "task": task_after})


@app.post("/api/tasks/<int:task_id>/transition")
def transition_task(task_id):
    """Lifecycle actions: submit (assignee/staff/supervisor), approve/return (supervisor).

    The approve/return supervisor gate lives here (require_role); the
    employee-must-be-assignee rule for submit needs the task row, so it is
    enforced in workflow.transition_task using the session identity passed in.
    """
    payload = request.get_json(silent=True) or {}
    action = str(payload.get("action") or "").strip().lower()
    if action in {"approve", "return"}:
        require_role("supervisor")
    session = db.get_session()
    task_after = workflow.transition_task(
        session, task_id, action, actor(payload),
        expected_revision=payload.get("revision"),
        actor_role=current_role(), actor_name=flask_session.get("name"),
    )
    return json_response({"ok": True, "task": task_after})


@app.get("/api/tasks/<int:task_id>/dynamic-fields")
def get_task_dynamic_fields(task_id):
    session = db.get_session()
    return json_response(workflow.get_task_dynamic_fields(session, task_id))


@app.patch("/api/tasks/<int:task_id>/dynamic-fields")
def save_task_dynamic_fields(task_id):
    session = db.get_session()
    payload = request.get_json(silent=True) or {}
    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else payload
    workflow.save_task_dynamic_fields(session, task_id, fields or {}, actor(payload))
    return json_response({"ok": True})


@app.get("/api/projects/<int:project_id>/dynamic-fields")
def project_dynamic_fields(project_id):
    session = db.get_session()
    return json_response(workflow.get_project_dynamic_field_map(session, project_id))


@app.get("/api/projects/<int:project_id>/component-folder/<int:task_id>")
def component_folder(project_id, task_id):
    session = db.get_session()
    return json_response(folders.get_component_folder_link(session, project_id, task_id))


@app.patch("/api/tasks/<int:task_id>/priority")
def priority(task_id):
    session = db.get_session()
    payload = request.get_json(silent=True) or {}
    workflow.set_task_priority(session, task_id, payload.get("priority", payload.get("priority_value", "Medium")), actor(payload))
    return json_response({"ok": True})


@app.get("/api/business-plan/rows")
def business_rows():
    session = db.get_session()
    return json_response(reporting.get_business_plan_rows(session))


@app.get("/api/portfolio/rows")
def portfolio_rows():
    session = db.get_session()
    return json_response(reporting.get_portfolio_rows(session, request.args.get("year", "All"), request.args.get("activity", "All")))


@app.get("/api/activity")
def activity():
    session = db.get_session()
    project_id = request.args.get("project_id")
    try:
        project_id_int = int(project_id) if project_id else None
    except ValueError:
        project_id_int = None
    return json_response(reporting.get_activity_log(session, project_id=project_id_int, limit=500))


@app.get("/api/export/excel")
def export_excel_route():
    session = db.get_session()
    filename = f"Segment_Maturation_and_Execution_System_{date.today().isoformat()}.xlsx"
    tmp = NamedTemporaryFile(delete=False, suffix=".xlsx")
    tmp_path = tmp.name
    tmp.close()
    # Cleanup-then-reraise: the temp file must not leak when export fails; the
    # re-raised exception reaches the centralized 500 handler. Documented
    # exception to the no-try/except rule.
    try:
        export_excel.export_to_excel(session, tmp_path)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise
    response = send_file(tmp_path, as_attachment=True, download_name=filename,
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response.call_on_close(lambda: os.path.exists(tmp_path) and os.unlink(tmp_path))
    return response


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8020, debug=False)
