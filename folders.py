"""Windows/UNC share-path building and folder-link resolution.

What belongs here:
- Turning a project name into (field, well) and safe folder names.
- Building Windows/UNC paths and ``file://`` URLs from the roots/maps in
  ``config.py``.
- Resolving the folder links the UI buttons open, and creating the on-disk
  folders when the share happens to be mounted.

What does NOT belong here:
- Task/project lifecycle logic (workflow.py) or reporting.

The DB-aware functions take a SQLAlchemy ``session`` as their first argument and
read the small amount of project/task data they need directly via the db
helpers, so this module does not depend on workflow.py.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Tuple

import config
import db


# ---------------------------------------------------------------------------
# Pure name/path helpers
# ---------------------------------------------------------------------------

def parse_field_and_well(project_name: str) -> Tuple[str, str]:
    """Split a project name into (field, well).

    Convention: ``MDFT-3`` -> field ``MDFT``, well ``MDFT-3``. A single word maps
    field and well to the same value.
    """
    name = (project_name or '').strip()
    if not name:
        return '', ''
    if '-' in name:
        return name.split('-', 1)[0].strip(), name
    parts = name.split()
    if len(parts) > 1:
        return parts[0], name
    return name, name


def _safe_folder_name(name: str) -> str:
    """Sanitize a component name into a Windows-safe folder name (<=120 chars)."""
    text = str(name or "").strip() or "Component"
    # Windows also disallows backslash as part of a folder name.
    text = text.replace('\\', '-')
    for ch in '<>:"/|?*':
        text = text.replace(ch, "-")
    return " ".join(text.split())[:120]


def _windows_join(*parts) -> str:
    """Join Windows/UNC path parts without depending on the server OS."""
    clean = []
    for idx, part in enumerate(parts):
        text = str(part or "").strip()
        if not text:
            continue
        if idx == 0:
            clean.append(text.rstrip("\\/"))
        else:
            clean.append(text.strip("\\/"))
    return "\\".join(clean)


def _windows_path_to_file_url(path_text: str) -> str:
    """Convert a Windows UNC or drive path to a browser ``file://`` URL."""
    path_text = str(path_text or "").strip()
    if not path_text:
        return ""
    normalized = path_text.replace("\\", "/")
    if normalized.startswith("//"):
        # UNC path: //server/share/folder -> file://///server/share/folder
        return "file:" + normalized
    if len(normalized) >= 2 and normalized[1] == ":":
        # Drive path: C:/folder -> file:///C:/folder
        return "file:///" + normalized
    return "file:///" + normalized.lstrip("/")


def default_lead_folder_path(project_name: str) -> str:
    """Default UNC lead-folder path derived from a project name."""
    field_name, well_name = parse_field_and_well(project_name or "")
    return _windows_join(
        config.WINDOWS_WELL_SHARE_ROOT, field_name, well_name,
        config.WELL_OVERVIEW_DIRECTORY_MAP.get("lead", "Leads"),
    )


# ---------------------------------------------------------------------------
# DB-aware folder resolution
# ---------------------------------------------------------------------------

def _project_row(session, project_id: int):
    return db.fetch_one(session, "SELECT * FROM projects WHERE project_id = :project_id",
                        {"project_id": project_id})


def _task_row(session, task_id: int):
    return db.fetch_one(session, "SELECT * FROM project_tasks WHERE task_id = :task_id",
                        {"task_id": task_id})


def get_open_folder_path(session, project_id: int, section_key: str = "well") -> Path:
    """Return the on-disk (mounted) path for a project's folder section."""
    project = _project_row(session, project_id)
    if not project:
        raise ValueError("Well not found.")
    if section_key not in config.WELL_OVERVIEW_DIRECTORY_MAP:
        raise ValueError(f"Unknown folder section: {section_key}")
    root = (config.LEAD_WORKFLOW_DIRECTORY_ROOT if section_key in config.LEAD_WORKFLOW_SECTION_KEYS
            else config.WELL_OVERVIEW_DIRECTORY_ROOT)
    section = config.WELL_OVERVIEW_DIRECTORY_MAP.get(section_key, "")
    field_name, well_name = parse_field_and_well(project.get("project_name") or "")
    path = root / field_name / well_name
    return path / section if section else path


def get_component_folder_link(session, project_id: int, task_id: int) -> Dict[str, object]:
    """Return the supporting-files folder link for one component (task)."""
    project = _project_row(session, project_id)
    task = _task_row(session, task_id)
    if not project or not task or int(task.get("project_id") or 0) != int(project_id):
        raise ValueError("Component folder could not be resolved.")
    task_name = task.get("task_name") or "Component"
    requires_folder = task_name in config.COMPONENT_FILE_SECTIONS
    field_name, well_name = parse_field_and_well(project.get("project_name") or "")
    section = _safe_folder_name(task_name)
    server_path = config.WELL_OVERVIEW_DIRECTORY_ROOT / field_name / well_name / "Component Files" / section
    unc_path = _windows_join(config.WINDOWS_WELL_SHARE_ROOT, field_name, well_name, "Component Files", section)
    return {
        "requires_folder": 1 if requires_folder else 0,
        "path": unc_path,
        "unc_path": unc_path,
        "file_url": _windows_path_to_file_url(unc_path),
        "section": task_name,
        "server_path": str(server_path),
    }


def ensure_well_folders(session, project_id: int) -> str:
    """Best-effort create every well/component folder; return the well path.

    Folder creation failures (e.g. the share is not mounted) are swallowed so a
    new project is never blocked by an unavailable share.
    """
    created = []
    for section_key in config.WELL_OVERVIEW_DIRECTORY_MAP:
        path = get_open_folder_path(session, project_id, section_key)
        try:
            path.mkdir(parents=True, exist_ok=True)
            created.append(str(path))
        except Exception:
            pass
    tasks = db.fetch_all(
        session,
        "SELECT * FROM project_tasks WHERE project_id = :project_id AND is_active = 1 ORDER BY sequence_no",
        {"project_id": project_id},
    )
    for task in tasks:
        if task.get("task_name") not in config.COMPONENT_FILE_SECTIONS:
            continue
        info = get_component_folder_link(session, project_id, task["task_id"])
        try:
            Path(info["server_path"]).mkdir(parents=True, exist_ok=True)
            created.append(info["server_path"])
        except Exception:
            pass
    return str(get_open_folder_path(session, project_id, "well"))
