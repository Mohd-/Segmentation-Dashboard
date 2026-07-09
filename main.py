from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile

from flask import Flask, jsonify, request, send_file, send_from_directory

from dependencies import close_db, get_db, init_db, DB_PATH

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
APP_NAME = "Segment Maturation and Execution System"

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")
# Bootstrap schema/migrations once when the application process starts.
init_db(DB_PATH)
app.teardown_appcontext(close_db)


def json_response(data, status=200):
    response = jsonify(data)
    response.status_code = status
    return response


def error_response(message, status=400):
    return json_response({"detail": str(message) if message else "Request failed"}, status)


@app.after_request
def add_api_cache_headers(response):
    # API data must not be stale. Static assets retain normal browser caching.
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/api/health")
def health():
    return json_response({"ok": True, "app": APP_NAME, "version": "v12", "backend": "Flask", "db": str(DB_PATH)})


@app.get("/api/projects")
def list_projects():
    db = get_db()
    return json_response(db.get_projects(
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
    payload = request.get_json(silent=True) or {}
    try:
        project_id = get_db().add_project(
            payload.get("project_name", ""),
            payload.get("start_date", ""),
            payload.get("target_date", ""),
            payload.get("changed_by", "Web User"),
            # Coordinates are no longer collected in the UI; old API callers remain compatible.
            payload.get("lead_x"), payload.get("lead_y"),
            payload.get("business_plan_year"), bool(payload.get("business_plan_enabled")),
            bool(payload.get("active_well_enabled")), payload.get("pipeline_type", "prospect"),
        )
        try:
            folder_path = get_db().ensure_well_folders(project_id)
        except Exception:
            folder_path = None
        return json_response({"project_id": project_id, "folder_path": folder_path}, 201)
    except Exception as exc:
        return error_response(exc, 400)


@app.get("/api/projects/<int:project_id>")
def get_project(project_id):
    project = get_db().get_project(project_id)
    if not project:
        return error_response("Lead / well not found", 404)
    return json_response(project)


@app.get("/api/projects/<int:project_id>/detail")
def project_detail(project_id):
    db = get_db()
    project = db.get_project(project_id)
    if not project:
        return error_response("Lead / well not found", 404)
    return json_response({
        "project": project,
        "tasks": db.get_project_tasks(project_id),
        "completion": {"percent": db.project_completion_percent(project_id)},
        "fields": db.get_project_dynamic_field_map(project_id),
        "lead_summary": db.get_lead_summary_snapshot(project_id),
    })


@app.patch("/api/projects/<int:project_id>/rename")
def rename_project(project_id):
    payload = request.get_json(silent=True) or {}
    try:
        get_db().update_project_name(
            project_id, payload.get("new_name", ""), payload.get("changed_by", "Web User"),
            payload.get("lead_x"), payload.get("lead_y"), payload.get("business_plan_year"),
            payload.get("business_plan_enabled") if "business_plan_enabled" in payload else None,
            payload.get("active_well_enabled") if "active_well_enabled" in payload else None,
        )
        return json_response({"ok": True})
    except Exception as exc:
        return error_response(exc, 400)


@app.patch("/api/projects/<int:project_id>/archive")
def archive_project(project_id):
    payload = request.get_json(silent=True) or {}
    try:
        get_db().archive_project(project_id, payload.get("changed_by", "Web User"))
        return json_response({"ok": True})
    except Exception as exc:
        return error_response(exc, 400)


@app.delete("/api/projects/<int:project_id>")
def delete_project(project_id):
    """Archive by default so components, inputs and history remain recoverable."""
    try:
        get_db().archive_project(project_id, "Web User")
        return json_response({"ok": True, "archived": True})
    except Exception as exc:
        return error_response(exc, 400)


@app.patch("/api/projects/<int:project_id>/restore")
def restore_project(project_id):
    payload = request.get_json(silent=True) or {}
    try:
        get_db().restore_project(project_id, payload.get("changed_by", "Web User"))
        return json_response({"ok": True, "archived": False})
    except Exception as exc:
        return error_response(exc, 400)


@app.get("/api/projects/<int:project_id>/completion")
def completion(project_id):
    return json_response({"percent": get_db().project_completion_percent(project_id)})


@app.patch("/api/projects/<int:project_id>/location")
def location(project_id):
    payload = request.get_json(silent=True) or {}
    get_db().update_project_location(project_id, payload.get("location", ""))
    return json_response({"ok": True})


@app.patch("/api/projects/<int:project_id>/lead-folder")
def lead_folder(project_id):
    payload = request.get_json(silent=True) or {}
    try:
        path = get_db().update_project_lead_folder(project_id, payload.get("lead_folder_path", ""))
        return json_response({"ok": True, "lead_folder_path": path})
    except Exception as exc:
        return error_response(exc, 400)


@app.patch("/api/projects/<int:project_id>/business-plan")
def business_plan(project_id):
    payload = request.get_json(silent=True) or {}
    try:
        get_db().set_business_plan(project_id, bool(payload.get("enabled")), payload.get("year"), payload.get("changed_by", "Web User"))
        return json_response({"ok": True})
    except Exception as exc:
        return error_response(exc, 400)


@app.patch("/api/projects/<int:project_id>/flags")
def project_flags(project_id):
    payload = request.get_json(silent=True) or {}
    try:
        get_db().update_project_flags(
            project_id,
            payload.get("business_plan_enabled") if "business_plan_enabled" in payload else None,
            payload.get("active_well_enabled") if "active_well_enabled" in payload else None,
            payload.get("business_plan_year"), payload.get("changed_by", "Web User"),
        )
        return json_response({"ok": True})
    except Exception as exc:
        return error_response(exc, 400)


@app.get("/api/projects/<int:project_id>/tasks")
def tasks(project_id):
    return json_response(get_db().get_project_tasks(project_id))


@app.get("/api/tasks/<int:task_id>")
def task(task_id):
    item = get_db().get_task(task_id)
    if not item:
        return error_response("Task not found", 404)
    return json_response(item)


@app.patch("/api/tasks/<int:task_id>")
def update_task(task_id):
    payload = request.get_json(silent=True) or {}
    try:
        task_after_save = get_db().save_task(task_id, payload, payload.get("changed_by", "Web User"))
        return json_response({"ok": True, "task": task_after_save})
    except RuntimeError as exc:
        return error_response(exc, 409)
    except Exception as exc:
        return error_response(exc, 400)


@app.get("/api/tasks/<int:task_id>/dynamic-fields")
def get_task_dynamic_fields(task_id):
    try:
        return json_response(get_db().get_task_dynamic_fields(task_id))
    except Exception as exc:
        return error_response(exc, 400)


@app.patch("/api/tasks/<int:task_id>/dynamic-fields")
def save_task_dynamic_fields(task_id):
    payload = request.get_json(silent=True) or {}
    try:
        fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else payload
        get_db().save_task_dynamic_fields(task_id, fields or {}, payload.get("changed_by", "Web User"))
        return json_response({"ok": True})
    except Exception as exc:
        return error_response(exc, 400)


@app.get("/api/projects/<int:project_id>/dynamic-fields")
def project_dynamic_fields(project_id):
    try:
        return json_response(get_db().get_project_dynamic_field_map(project_id))
    except Exception as exc:
        return error_response(exc, 400)


@app.get("/api/projects/<int:project_id>/component-folder/<int:task_id>")
def component_folder(project_id, task_id):
    try:
        return json_response(get_db().get_component_folder_link(project_id, task_id))
    except Exception as exc:
        return error_response(exc, 400)


@app.patch("/api/tasks/<int:task_id>/priority")
def priority(task_id):
    payload = request.get_json(silent=True) or {}
    try:
        get_db().set_task_priority(task_id, payload.get("priority", payload.get("priority_value", "Medium")), payload.get("changed_by", "Web User"))
        return json_response({"ok": True})
    except Exception as exc:
        return error_response(exc, 400)


@app.get("/api/projects/<int:project_id>/next-task")
def next_task(project_id):
    return json_response(get_db().get_first_open_task(project_id) or {})


@app.get("/api/projects/<int:project_id>/overview")
def overview(project_id):
    return json_response(get_db().get_project_overview(project_id))


@app.get("/api/overview/all")
def overview_all():
    return json_response(get_db().get_well_overview_rows())


@app.get("/api/open-folder")
def open_folder():
    try:
        project_id = int(request.args.get("project_id", "0"))
        section = request.args.get("section", "well")
        return json_response(get_db().open_folder(project_id, section))
    except FileNotFoundError as exc:
        return error_response(exc, 404)
    except Exception as exc:
        return error_response(exc, 400)


@app.get("/api/dashboard/metrics")
def metrics():
    try:
        threshold_days = int(request.args.get("threshold_days", "14"))
    except ValueError:
        threshold_days = 14
    db = get_db()
    metrics_data, stage_counts, owner_workload = db.dashboard_metrics()
    return json_response({"metrics": metrics_data, "stage_counts": stage_counts, "owner_workload": owner_workload,
                          "bottlenecks": db.bottleneck_rows(threshold_days), "attention": db.attention_rows()})


@app.get("/api/dashboard/monthly")
def monthly():
    return json_response(get_db().monthly_progress_metrics(limit=12))


@app.get("/api/business-plan/rows")
def business_rows():
    return json_response(get_db().get_business_plan_rows())


@app.get("/api/portfolio/rows")
def portfolio_rows():
    try:
        return json_response(get_db().get_portfolio_rows(request.args.get("year", "All"), request.args.get("activity", "All")))
    except Exception as exc:
        return error_response(exc, 400)


@app.get("/api/business-plan/commitment")
def commitment():
    return json_response(get_db().get_business_plan_commitment())


@app.post("/api/business-plan/commitment")
def save_commitment():
    get_db().update_business_plan_commitment(request.get_json(silent=True) or {})
    return json_response({"ok": True})


@app.get("/api/activity")
def activity():
    project_id = request.args.get("project_id")
    try:
        project_id_int = int(project_id) if project_id else None
    except ValueError:
        project_id_int = None
    return json_response(get_db().get_activity_log(project_id=project_id_int, limit=500))


@app.get("/api/owners")
def owners():
    return json_response(get_db().get_distinct_owners())


@app.get("/api/export/excel")
def export_excel():
    tmp_path = None
    try:
        filename = f"Segment_Maturation_and_Execution_System_{date.today().isoformat()}.xlsx"
        tmp = NamedTemporaryFile(delete=False, suffix=".xlsx")
        tmp_path = tmp.name
        tmp.close()
        get_db().export_to_excel(tmp_path)
        response = send_file(tmp_path, as_attachment=True, download_name=filename,
                             mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        response.call_on_close(lambda: os.path.exists(tmp_path) and os.unlink(tmp_path))
        return response
    except Exception as exc:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        return error_response(exc, 500)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8020, debug=False)
