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
Rows with a per-user password_hash (add_users.py) require that password;
otherwise the shared passcode (config.SHARED_PASSCODE) applies when
configured. ``actor()`` stamps the session name into every changed_by, falling
back to the client-supplied value for anonymous users so the unmodified
front-end keeps working. When config.AUTH_REQUIRED is on, every /api/* route
except /api/health, /api/login, /api/logout, /api/me and /api/users requires a
session. Role gating: ``current_role()`` / ``require_role()`` (PermissionError
-> 403 via the centralized handler).
"""
from __future__ import annotations

import gzip
import os
import secrets
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Optional

from flask import Flask, jsonify, request, send_file, send_from_directory
from flask import session as flask_session
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash

import config
import cos
import db
import export_excel
import folders
import map_layers
import reporting
import resource_calc
import uploads
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


# Gzip floor: bodies below this stay uncompressed (the header overhead and CPU
# aren't worth it for a few hundred bytes; every large surface -- the boards,
# portfolio, activity -- is tens to hundreds of KB).
_COMPRESS_MIN_BYTES = 1024


@app.after_request
def _gzip_json_response(response):
    """Gzip successful JSON responses for clients that accept it.

    Stdlib-only (no flask-compress / brotli dependency): the wins here are
    ~10x on the list payloads, which matters for remote/VPN users. File and
    streamed responses are skipped via direct_passthrough (the Excel download
    is already a compressed container), as are non-2xx bodies and anything
    already carrying a Content-Encoding. Vary: Accept-Encoding is set on
    every considered response -- including uncompressed ones -- so a cache
    can never hand a gzipped body to a client that didn't ask for it.
    """
    if (response.direct_passthrough
            or response.mimetype != "application/json"
            or not 200 <= response.status_code < 300
            or response.headers.get("Content-Encoding")):
        return response
    response.vary.add("Accept-Encoding")
    if "gzip" not in request.headers.get("Accept-Encoding", "").lower():
        return response
    data = response.get_data()
    if len(data) < _COMPRESS_MIN_BYTES:
        return response
    # set_data refreshes Content-Length itself.
    response.set_data(gzip.compress(data, compresslevel=6))
    response.headers["Content-Encoding"] = "gzip"
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


def current_identity() -> Optional[str]:
    """WHO the current request is, for anything addressed to a person by name.

    Exactly what GET /api/me reports as ``name``: the session identity, or None
    when there is no session. Deliberately NOT ``actor()``'s 'Web User'
    fallback and NOT ``current_role()``'s dev-mode 'supervisor': those answer
    "what should we stamp on this write" and "what may this request do".
    "Whose mail is this" has a third answer -- with AUTH_REQUIRED off and no
    session there is no addressee at all, and the notification endpoints report
    an empty feed rather than inventing an inbox for an anonymous user.

    Consumers: the /api/notifications routes.
    """
    return flask_session.get("name") or None


def require_role(*roles: str) -> None:
    """Raise PermissionError (-> 403) unless the current role is one of ``roles``.

    Consumers: POST /api/tasks/<id>/assign (supervisor, staff), the
    approve actions of POST /api/tasks/<id>/transition (supervisor),
    business_plan_enabled changes via PATCH /api/projects/<id>/flags
    (supervisor), PATCH /api/tasks/<id>/priority (supervisor),
    PATCH /api/projects/<id>/priority (supervisor), the approve/return/reopen
    actions of POST /api/business-plan/wells/<id>/steps/<slug>/transition
    (supervisor), and business_plan_enabled at creation via POST
    /api/projects (supervisor). Priority is also guarded on the Save path:
    PATCH /api/tasks/<id> passes allow_priority_change so a non-supervisor's
    save keeps the stored value.
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

    Name is required (trimmed, 1-80 chars) and must match an active row in the
    ``users`` table (case-insensitive); unknown names get 401. The single
    front-end Passcode input then satisfies exactly ONE check:
    - a row with a ``password_hash`` (set via add_users.py) requires that
      per-user password (``passcode`` carries it; a ``password`` key is
      accepted too) -- the shared passcode does NOT also apply, because the
      login form has only one secret box;
    - otherwise the shared config.SHARED_PASSCODE is required when configured,
      and login is name-only when it is not (see config.py for the
      trusted-network rationale).
    The session stores the DB row's canonical casing and role, so audit trails
    never fork on "alice" vs "Alice".
    """
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name") or "").strip()
    if not name or len(name) > 80:
        raise ValueError("Name must be 1 to 80 characters.")
    user = workflow.find_active_user(db.get_session(), name)
    if not user:
        return error_response("Unknown user.", 401)
    if user.get("password_hash"):
        supplied = str(payload.get("password") or payload.get("passcode") or "")
        if not check_password_hash(user["password_hash"], supplied):
            return error_response("Invalid password.", 401)
    elif config.SHARED_PASSCODE:
        supplied = str(payload.get("passcode") or "")
        if not secrets.compare_digest(supplied, config.SHARED_PASSCODE):
            return error_response("Invalid passcode.", 401)
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

    ``auth_required`` mirrors config.AUTH_REQUIRED, read here at REQUEST time
    (like the before_request gate, so tests can monkeypatch it). The front-end
    reads it to decide whether to front the app with the full-page login screen
    before any data/meta loads (which WOULD 401 under AUTH_REQUIRED).
    """
    name: Optional[str] = flask_session.get("name")
    return json_response({
        "authenticated": bool(name),
        "name": name if name else None,
        "role": (flask_session.get("role") or "employee") if name else None,
        "auth_required": config.AUTH_REQUIRED,
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
        # The 4 lifecycle statuses, straight from the domain layer.
        "statuses": workflow.STATUSES,
        "roles": ["supervisor", "staff", "employee"],
        # Block name -> [AR number, ...], from config.SEISMIC_BLOCK_AR_MAP
        # (seismic_blocks.json). Feeds the Reservoir CoS sheet's dependent
        # Block/AR dropdowns -- this endpoint is their single source of truth.
        "seismic_blocks": config.SEISMIC_BLOCK_AR_MAP,
        # Configured resource-assessment scenarios for the Resource
        # Assessment pop-up calculator's scenario dropdown.
        "resource_scenarios": resource_calc.scenario_options(),
        # The petrophysical distributions each scenario runs on (porosity, Sg,
        # NGR, geometric factor, 1/Bg), keyed by scenario id. Feeds the
        # Calculator's Advanced settings panel, which prefills from them and
        # may send edited copies back as `overrides` on a single run.
        "resource_parameters": resource_calc.scenario_parameters(),
        # Card 2B, Section 1. row ("reservoir"/"formation") -> {m, b} for the
        # straight-line TWT (ms) <-> thickness (ft) conversion, from
        # config.TWT_THICKNESS_COEFFICIENTS. SHIPS EMPTY: a row with no entry
        # renders as two plain manual inputs plus the "conversion pending
        # configuration" note, so this endpoint is the single production switch
        # that turns derivation on (see that constant's own comment).
        "twt_thickness_coefficients": config.TWT_THICKNESS_COEFFICIENTS,
        # The user-maintained pick lists (config/lists.yaml). Served here so
        # the client has ONE source for them instead of a hand-kept copy in
        # schema.js -- that array is now a boot fallback like the stage lists
        # above. See config/lists.yaml for how to extend them.
        "formations": list(config.formations()),
        "hole_sections": list(config.hole_sections()),
    })


@app.get("/api/health")
def health():
    return json_response({"ok": True, "app": config.APP_NAME, "version": config.APP_VERSION,
                          "backend": "Flask", "db": db.current_display()})


# The list/board projection: exactly what the list surfaces consume (pipeline
# board cards + their filters, the audit-log project dropdown). Everything else
# the full row carries (lead_folder_path, coordinates, dates, revision, ...)
# only matters on a single-project surface, which uses GET /api/projects/<id>
# and stays full-row. Trimming here halves the board payload at a few hundred
# wells. Pinned by test_list_projects_row_shape -- widen deliberately, not by
# reverting to the raw row.
_PROJECT_LIST_FIELDS = (
    "project_id", "project_name", "pipeline_type",
    "current_stage", "current_task", "current_owner",
    "overall_status", "current_task_priority", "health",
    "business_plan_year", "active_well_enabled", "active_drilling",
    "has_high_priority_tasks",
    # Card 1B lead-card fields: all derived at read time from the task rows the
    # board query already loads (workflow.projects._annotate_card_state) -- no
    # stored column, no extra query. Since migration v5 there is no presentation
    # adapter left for the other eight items; v7 projects the first four from
    # fields on the single Lead Assessment row, keeping twelve communicated
    # items while the stored prospect workflow has nine rows. display_stage is
    # the derived current_stage verbatim. assignees and
    # lead_priority are the board's own per-lead values.
    "assignees", "tracked_items", "display_stage", "lead_priority",
    # Card 3V: project_name above is the CANONICAL name -- the staked well name
    # once staking is confirmed. These carry the pairing alongside it, so a
    # board card can show what the record was matured as without a second read.
    "lead_name", "staked_well_name",
    # Card 1C: the record's field, DERIVED from the record name by the same
    # folders.parse_field_and_well split the share paths use -- there is no
    # stored field column. Feeds the board's Field filter.
    "field",
    # Card 1E: the LATEST saved Mean Gas in BCF, derived at read time from the
    # assessment steps' task_dynamic_fields on the LATEST_MEAN_GAS_SOURCES
    # precedence (workflow.projects._annotate_mean_gas -- one batched query for
    # the whole board). null when nothing is recorded or the stored value is
    # not a number; the board's Total Mean OGIP tile treats null as 0.
    "mean_gas_bcf",
)


@app.get("/api/projects")
def list_projects():
    session = db.get_session()
    rows = workflow.get_projects(
        session,
        request.args.get("search", ""),
        request.args.get("stage_filter", "All"),
        request.args.get("status_filter", "All"),
        request.args.get("owner_filter", "All"),
        request.args.get("health_filter", "All"),
        request.args.get("sort_key", "Well Name"),
        request.args.get("pipeline_filter", "All"),
        # Opt-in only (Card 1C's lead board, which offers its own Completed
        # status filter); absent/0 keeps the "finished records leave the
        # board" default every other caller relies on.
        include_completed=request.args.get("include_completed", "") in ("1", "true", "yes"),
    )
    return json_response([{key: row.get(key) for key in _PROJECT_LIST_FIELDS} for row in rows])


@app.post("/api/projects")
def create_project():
    session = db.get_session()
    payload = request.get_json(silent=True) or {}
    # A born-BP well is a promotion done at creation time, so it gets the same
    # gates a promotion via PATCH /flags gets: supervisor-only, and the year
    # window promotion.py enforces for a newly-enabling record (not the wider
    # allow_historical_year floor -- import_excel is the only historical-year
    # caller, and it creates via workflow.add_project in-process, never here).
    if payload.get("business_plan_enabled"):
        require_role("supervisor")
        current_year = date.today().year
        year_val = payload.get("business_plan_year")
        try:
            year_val = int(year_val)
        except (TypeError, ValueError):
            year_val = None
        if year_val is None or year_val < current_year or year_val > 2035:
            raise ValueError(f"Select a business plan year from {current_year} to 2035.")
    project_id = workflow.add_project(
        session,
        payload.get("project_name", ""),
        payload.get("start_date", ""),
        payload.get("target_date", ""),
        actor(payload),
        # Card 1D's Add New Lead control requires both coordinates client-side;
        # they stay OPTIONAL here (importer / older API callers), but a supplied
        # value must be numeric -- workflow.add_project validates it.
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
        # Composed from the task inputs at read time (e.g. derisking = Total
        # Chance of Success) -- there is no stored overview to go stale.
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
        payload.get("lead_x"), payload.get("lead_y"),
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
    # Promoting/recalling a project (business_plan_enabled) is a supervisor-only
    # action; toggling only active_well_enabled stays ungated.
    if "business_plan_enabled" in payload:
        require_role("supervisor")
    workflow.update_project_flags(
        session,
        project_id,
        payload.get("business_plan_enabled") if "business_plan_enabled" in payload else None,
        payload.get("active_well_enabled") if "active_well_enabled" in payload else None,
        payload.get("business_plan_year"), actor(payload),
    )
    return json_response({"ok": True})


@app.patch("/api/projects/<int:project_id>/priority")
def project_priority(project_id):
    """Set the lead/well-level priority (supervisor only).

    Body: {"priority": "Low"|"Medium"|"High", "changed_by": ...}. An
    unrecognized priority is a ValueError -> 400 via the centralized handler;
    a missing project is 404 (the single-project route pattern).
    """
    require_role("supervisor")
    session = db.get_session()
    if not workflow.get_project(session, project_id):
        return error_response("Lead / well not found", 404)
    payload = request.get_json(silent=True) or {}
    value = workflow.set_project_priority(session, project_id, payload.get("priority"), actor(payload))
    return json_response({"ok": True, "priority": value})


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
    task_after_save = workflow.save_task(
        session, task_id, payload, actor(payload),
        allow_priority_change=current_role() == "supervisor",
    )
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
    """Lifecycle actions: submit, approve (supervisor), and return (supervisor/assignee).

    The approve supervisor gate lives here (require_role). Return is available
    to supervisors and the task's assignee. The assignee checks for return and
    employee submit both need the task row, so they are enforced in
    workflow.transition_task using the session identity passed in.
    """
    payload = request.get_json(silent=True) or {}
    action = str(payload.get("action") or "").strip().lower()
    # The PUBLIC vocabulary is exactly workflow.TASK_TRANSITIONS. workflow.
    # transition_task additionally honors the engine-only "reopen"
    # (Approved -> In Progress, used by the field-completion engine); this
    # check is what keeps that move off the HTTP surface, where it would be an
    # ungated un-approve.
    if action not in workflow.TASK_TRANSITIONS:
        raise ValueError("Unknown action. Use one of: submit, approve, return.")
    if action == "approve":
        require_role("supervisor")
    session = db.get_session()
    task_after = workflow.transition_task(
        session, task_id, action, actor(payload),
        expected_revision=payload.get("revision"),
        actor_role=current_role(), actor_name=flask_session.get("name"),
    )
    return json_response({"ok": True, "task": task_after})


@app.post("/api/tasks/<int:task_id>/resource-assessment")
def resource_assessment(task_id):
    """Run the Lead Assessment resource calculator for its owning task.

    The task must exist (404 otherwise). The JSON body carries the pop-up's
    scenario/method/inputs; resource_calc.run drives the Monte Carlo engine and
    returns the PIIP percentiles plus base64 exceedance plots. Invalid inputs
    surface as ValueError -> 400 via the centralized handler. Nothing is stored
    here: persisting the chosen PIIP values is the pop-up's separate Apply/Save
    flow through the normal dynamic-fields path.
    """
    session = db.get_session()
    task = workflow.get_task(session, task_id)
    if not task:
        return error_response("Task not found", 404)
    # GeoX records results produced by the external GeoX application. Keeping
    # the task-scoped calculator boundary narrow prevents a future UI wiring
    # regression from silently turning that results-entry step back into a
    # Monte Carlo calculator. Resource Assessment is the inactive pre-v7 name
    # retained for rolling-upgrade compatibility.
    if task.get("task_name") not in {"Lead Assessment", "Resource Assessment"}:
        raise ValueError("Resource calculator is only available for Lead Assessment.")
    payload = request.get_json(silent=True) or {}
    # Advanced settings are a Calculator-tab affordance only. This endpoint's
    # results are STORED on the lead and compared across leads, so they must
    # always come from the scenario's approved assumptions -- a stored number
    # nobody can tell was computed on substituted inputs is worse than no
    # number. Refused rather than ignored, so a caller is never misled.
    if payload.get("overrides"):
        raise ValueError(
            "Advanced settings are available in the Calculator tab only: a lead's stored "
            "assessment must use the scenario's approved assumptions.")
    return json_response(resource_calc.run(payload))


@app.post("/api/calculators/resources")
def calculator_resources():
    """Run the Monte Carlo resource calculator without a project or task."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object.")
    return json_response(resource_calc.run(payload))


@app.post("/api/calculators/reservoir-cos")
def calculator_reservoir_cos():
    """Score one standalone Reservoir CoS row with the approved RF model."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object.")
    rows_json = cos.calculate_reservoir_cos_rows([payload])
    return app.response_class(rows_json, mimetype="application/json")


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
    """Card 3AB: the approved destination for this step, or nothing.

    ``requires_folder: 0`` is how "this step has no folder component" reaches
    the client, which is the same signal it already honoured -- so an unmapped
    step renders nothing at all rather than an empty card.
    """
    session = db.get_session()
    task = folders.task_row(session, task_id)
    if not task or int(task.get("project_id") or 0) != int(project_id):
        raise ValueError("Component folder could not be resolved.")
    mapped = folders.mapped_step_folder(
        session, project_id, task_name=task.get("task_name"),
        canonical_name=workflow.canonical_record_name(session, project_id))
    return json_response(mapped or {"requires_folder": 0})


@app.get("/api/projects/<int:project_id>/folders/<section_key>")
def project_section_folder(project_id, section_key):
    session = db.get_session()
    return json_response(folders.get_section_folder_link(session, project_id, section_key))


@app.patch("/api/tasks/<int:task_id>/priority")
def priority(task_id):
    """Set a component's priority (supervisor only)."""
    require_role("supervisor")
    session = db.get_session()
    payload = request.get_json(silent=True) or {}
    workflow.set_task_priority(session, task_id, payload.get("priority", payload.get("priority_value", "Medium")), actor(payload))
    return json_response({"ok": True})


# ---------------------------------------------------------------------------
# Notifications (the header bell) -- every route is scoped to current_identity()
# ---------------------------------------------------------------------------

@app.get("/api/notifications")
def notifications():
    """The signed-in user's feed plus the unread count, in one round trip.

    The count travels with the list (and with both mutations below) so the
    client updates the red dot and the menu from a single response -- they can
    never disagree. Anonymous (AUTH_REQUIRED off, no session) gets an empty
    feed and a zero count; see current_identity() for why that is not
    'Web User'.
    """
    session = db.get_session()
    return json_response(workflow.notification_feed(session, current_identity()))


@app.post("/api/notifications/<int:notification_id>/read")
def notification_read(notification_id):
    """Mark ONE of the caller's own notifications read (idempotent).

    An unknown id and another user's id are indistinguishable here: both raise
    ValueError -> 400 "Notification not found." from the domain layer, so the
    endpoint cannot be used to enumerate other people's notifications.
    """
    session = db.get_session()
    identity = current_identity()
    workflow.mark_read(session, identity, notification_id)
    return json_response({"ok": True, "unread_count": workflow.unread_count(session, identity)})


@app.post("/api/notifications/read-all")
def notifications_read_all():
    """Mark every unread notification of the caller read (idempotent)."""
    session = db.get_session()
    identity = current_identity()
    marked = workflow.mark_all_read(session, identity)
    return json_response({"ok": True, "marked": marked,
                          "unread_count": workflow.unread_count(session, identity)})


@app.get("/api/business-plan/rows")
def business_rows():
    session = db.get_session()
    return json_response(reporting.get_business_plan_rows(session))


@app.get("/api/business-plan/dashboard")
def business_plan_dashboard():
    """One canonical filtered population for BPE cards, counts, and KPIs."""
    session = db.get_session()
    payload = workflow.get_bpe_dashboard(session, {
        "assignee": request.args.get("assignee", "All Assignees"),
        "field": request.args.get("field", "All Fields"),
        "status": request.args.get("status", "All Status"),
        "year": request.args.get("year", date.today().year),
    })
    payload["role"] = current_role()
    return json_response(payload)


@app.get("/api/business-plan/wells/<int:project_id>/steps/<detail_slug>")
def business_plan_detail(project_id, detail_slug):
    session = db.get_session()
    payload = workflow.get_bpe_detail(session, project_id, detail_slug)
    payload["role"] = current_role()
    # Card 3AB keys the BPE side by DETAIL SLUG, not task name: sad-model-update
    # and final-summary-slides share the "SAD Update" task and take different
    # destinations. `folder` is absent when the step has no mapping, and the
    # detail form renders no folder component for it.
    payload["folder"] = folders.mapped_step_folder(
        session, project_id, detail_slug=detail_slug,
        canonical_name=payload["project"]["project_name"])
    return json_response(payload)


@app.patch("/api/business-plan/wells/<int:project_id>/steps/<detail_slug>/field")
def business_plan_save_field(project_id, detail_slug):
    session = db.get_session()
    payload = request.get_json(silent=True) or {}
    result = workflow.save_bpe_field(
        session, project_id, detail_slug, payload.get("field_key", ""),
        payload.get("value"), actor(payload), current_role(),
        bool(payload.get("confirm_reset")), payload.get("override_reason"),
    )
    result["role"] = current_role()
    return json_response({"ok": True, "detail": result})


@app.put("/api/business-plan/wells/<int:project_id>/steps/<detail_slug>/formations")
def business_plan_save_formations(project_id, detail_slug):
    session = db.get_session()
    payload = request.get_json(silent=True) or {}
    result = workflow.save_bpe_formations(
        session, project_id, detail_slug, payload.get("rows", []),
        actor(payload), current_role(),
    )
    result["role"] = current_role()
    return json_response({"ok": True, "detail": result})


@app.put("/api/business-plan/wells/<int:project_id>/flowback-stages")
def business_plan_save_flowback(project_id):
    session = db.get_session()
    payload = request.get_json(silent=True) or {}
    result = workflow.save_bpe_flowback_stages(
        session, project_id, payload.get("rows", []), actor(payload), current_role())
    result["role"] = current_role()
    return json_response({"ok": True, "detail": result})


@app.post("/api/business-plan/wells/<int:project_id>/steps/<detail_slug>/transition")
def business_plan_transition(project_id, detail_slug):
    session = db.get_session()
    payload = request.get_json(silent=True) or {}
    action = str(payload.get("action") or "").lower()
    # The supervisor gate for the un-approve-capable actions lives here, beside
    # every other route-level role check; transition_bpe_approval repeats it as
    # defense-in-depth for non-HTTP callers.
    if action in {"approve", "return", "reopen"}:
        require_role("supervisor")
    result = workflow.transition_bpe_approval(
        session, project_id, detail_slug, action,
        actor(payload), current_role(), payload.get("comment", ""),
    )
    result["role"] = current_role()
    return json_response({"ok": True, "detail": result})


@app.post("/api/business-plan/wells/<int:project_id>/steps/<detail_slug>/assign")
def business_plan_assign(project_id, detail_slug):
    require_role("supervisor", "staff")
    session = db.get_session()
    payload = request.get_json(silent=True) or {}
    result = workflow.assign_bpe_detail(
        session, project_id, detail_slug, payload.get("assignee", ""),
        actor(payload), current_role(),
    )
    result["role"] = current_role()
    return json_response({"ok": True, "detail": result})


@app.get("/api/portfolio/rows")
def portfolio_rows():
    session = db.get_session()
    return json_response(reporting.get_portfolio_rows(session, request.args.get("year", "All"), request.args.get("activity", "All")))


# ---------------------------------------------------------------------------
# Portfolio waterfall diagram -- the app's only user-uploaded file
# ---------------------------------------------------------------------------
# ONE image for the whole portfolio, shared by everyone, so there is no id in
# any of these paths. uploads.py owns every rule (type sniffed from the bytes,
# size cap, our own stored name); this layer only moves bytes.

@app.get("/api/portfolio/waterfall")
def portfolio_waterfall_image():
    """Serve the stored diagram, or 404 when none has been uploaded."""
    session = db.get_session()
    record = uploads.get_waterfall(session)
    if not record:
        return json_response({"detail": "No waterfall diagram has been uploaded."}, 404)
    # download_name is OURS, not anything the uploader supplied.
    return send_file(str(record["path"]), mimetype=record["content_type"],
                     download_name="portfolio-waterfall." + record["extension"])


@app.post("/api/portfolio/waterfall")
def portfolio_waterfall_upload():
    """Replace the portfolio's waterfall diagram (multipart 'file' part)."""
    require_role("supervisor", "staff")
    session = db.get_session()
    uploaded = request.files.get("file")
    if uploaded is None:
        raise ValueError("Choose an image to upload.")
    # actor() works on the form dict exactly as it does on a JSON body: a
    # logged-in session name always wins over anything the client sent.
    return json_response(uploads.save_waterfall(session, uploaded.read(), actor(request.form)))


@app.delete("/api/portfolio/waterfall")
def portfolio_waterfall_delete():
    require_role("supervisor", "staff")
    session = db.get_session()
    return json_response(uploads.delete_waterfall(session))


@app.get("/api/activity")
def activity():
    session = db.get_session()
    project_id = request.args.get("project_id")
    try:
        project_id_int = int(project_id) if project_id else None
    except ValueError:
        project_id_int = None
    return json_response(reporting.get_activity_log(session, project_id=project_id_int, limit=500))


# ---------------------------------------------------------------------------
# Map (UTM Zone 37N). Layers are files on the map share (map_layers.py); the
# wells overlay is derived project state (workflow.map_wells). Coordinates are
# metres and are never reprojected on the way out.
# ---------------------------------------------------------------------------

@app.get("/api/map/layers")
def map_layers_list():
    """Available layers as metadata only; the ``borders`` backdrop comes first."""
    return json_response({"layers": map_layers.list_layers()})


@app.get("/api/map/layers/<path:name>")
def map_layer(name):
    """One layer's geometry (UTM37 metres) as GeoJSON-like features.

    ``borders`` is a prebuilt JSON file, passed through verbatim rather than
    re-serialized. Every other name resolves to a shapefile set: an invalid or
    traversing name raises ValueError -> 400 and an unknown one
    FileNotFoundError -> 404, both through the centralized handlers.
    """
    if name == map_layers.BORDERS_LAYER_NAME:
        return app.response_class(map_layers.load_borders(), mimetype="application/json")
    return json_response(map_layers.load_layer(name))


@app.get("/api/map/wells")
def map_wells():
    """Lead / well pins: staked coordinates when known, else the lead's."""
    session = db.get_session()
    return json_response({"wells": workflow.map_wells(session)})


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
