"""Shared public-write and approval policy for workflow tasks.

Persisted task statuses remain the stable internal vocabulary (``Ready`` and
``Approved``).  This module is the one authorization registry layered over
those rows for both the Segment Maturation and Business Plan shells.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import db

from . import domain_roles


# task_name -> BPE detail slug.  ``None`` identifies the Segment Maturation
# approval step.  SAD Update is conditional; ``approval_required`` asks the
# BPE effective-state engine whether its manual comparison branch is active.
APPROVAL_POLICY = {
    "Segmentation Slides": None,
    "BP Execution Gate": "business-plan-gate",
    "SAD Model": "sad-model",
    "Post-Well Outcome & Decision Gate": "post-drill-learning-review",
    "SAD Update": "sad-model-update",
}

BPE_CONTENT_TASKS = frozenset({
    "BP Execution Gate", "Well Proposal", "Site Preparation", "Approval To Drill",
    "GHEER", "Quicklook Logs", "Aramco Picks", "SAD Model", "Executive Summary",
    "Post-Well Outcome & Decision Gate", "Flowback Results", "SAD Update",
    "Final Log Analysis", "PVAD Structural MTR", "PDA",
})


def project_is_bpe(session, project_id: int) -> bool:
    project = db.fetch_one(session, """
        SELECT pipeline_type, business_plan_enabled
        FROM projects WHERE project_id = :project_id
    """, {"project_id": project_id})
    return bool(project and (
        project.get("pipeline_type") == "bp"
        or int(project.get("business_plan_enabled") or 0) == 1
    ))


def task_is_bpe(session, task: Optional[Dict[str, Any]]) -> bool:
    return bool(task and project_is_bpe(session, int(task["project_id"])))


def approval_detail_slug(session, task: Optional[Dict[str, Any]],
                         detail_slug: Optional[str] = None) -> Optional[str]:
    """Return the active BPE approval slug, ``"segment"``, or ``None``."""
    if not task:
        return None
    task_name = task.get("task_name") or ""
    if task_name == "Segmentation Slides":
        return None if task_is_bpe(session, task) else "segment"
    policy_slug = APPROVAL_POLICY.get(task_name)
    if not policy_slug or not task_is_bpe(session, task):
        return None
    # A shared stored task may back another detail editor.  In particular,
    # final-summary-slides shares SAD Update but is an auto-complete detail.
    if detail_slug is not None and detail_slug != policy_slug:
        return None
    if policy_slug == "sad-model-update":
        # Lazy import keeps the package dependency graph acyclic.
        from . import business_plan
        if not business_plan.sad_model_update_requires_approval(
                session, int(task["project_id"])):
            return None
    return policy_slug


def approval_required(session, task: Optional[Dict[str, Any]],
                      detail_slug: Optional[str] = None) -> bool:
    return approval_detail_slug(session, task, detail_slug) is not None


def _is_assignee(session, task: Dict[str, Any], actor_name: Optional[str]) -> bool:
    wanted = str(actor_name or "").strip().lower()
    if not wanted:
        return False
    assignees = task.get("assignees")
    if assignees is None:
        names = domain_roles.get_assignee_names(session, int(task["task_id"]))
    else:
        names = [member.get("name") for member in assignees]
    return any(str(name or "").strip().lower() == wanted for name in names)


def actor_may_edit(session, task: Dict[str, Any], role: Optional[str],
                   actor_name: Optional[str]) -> bool:
    """Return the role/assignment half of content authorization.

    Workflow state and system locks are intentionally excluded so transition
    callers can distinguish a forbidden actor (403) from an authorized actor
    clicking an action in the wrong state (400).
    """
    return bool(role in {"supervisor", "staff"}
                or (role == "employee" and _is_assignee(session, task, actor_name)))


def task_permissions(session, task: Dict[str, Any], role: Optional[str],
                     actor_name: Optional[str], detail_slug: Optional[str] = None,
                     system_locked: bool = False) -> Dict[str, bool]:
    required = approval_required(session, task, detail_slug)
    status = task.get("status") or "Not Assigned"
    approval_locked = required and status in {"Ready", "Approved"}
    can_edit = bool(actor_may_edit(session, task, role, actor_name)
                    and not approval_locked and not system_locked)
    draft = status in {"Not Assigned", "In Progress"}
    supervisor = role == "supervisor"
    return {
        "approval_required": required,
        "approval_locked": approval_locked,
        "can_edit": can_edit,
        "can_submit": bool(required and can_edit and draft),
        "can_approve": bool(required and supervisor and status == "Ready"),
        "can_return": bool(required and supervisor and status == "Ready"),
        "can_reopen": bool(required and supervisor and status == "Approved"),
        "can_manage_assignments": role in {"supervisor", "staff"},
    }


def attach_permissions(session, task: Optional[Dict[str, Any]], role: Optional[str],
                       actor_name: Optional[str], detail_slug: Optional[str] = None,
                       system_locked: bool = False) -> Optional[Dict[str, Any]]:
    if task:
        task["permissions"] = task_permissions(
            session, task, role, actor_name, detail_slug, system_locked)
    return task


def require_content_edit(session, task: Dict[str, Any], role: Optional[str],
                         actor_name: Optional[str], detail_slug: Optional[str] = None,
                         system_locked: bool = False) -> None:
    permissions = task_permissions(
        session, task, role, actor_name, detail_slug, system_locked)
    if permissions["approval_locked"]:
        if (task.get("status") or "") == "Ready":
            raise ValueError("This pending step must be returned before its content can change.")
        raise ValueError("This completed step must be reopened before its content can change.")
    if system_locked:
        raise ValueError("This step is controlled by the Business Plan workflow and cannot be edited.")
    if not permissions["can_edit"]:
        raise PermissionError("Forbidden: you do not have permission to edit this step.")


def reject_generic_bpe_write(session, task: Dict[str, Any]) -> None:
    if (task_is_bpe(session, task)
            and (task.get("task_name") or "") in BPE_CONTENT_TASKS):
        raise ValueError(
            "Business Plan content must be changed through the Business Plan step data API.")
