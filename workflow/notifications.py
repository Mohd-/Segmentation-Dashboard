"""In-app notifications: who gets told about a lifecycle transition, and the
per-recipient feed the header bell reads (Card 1F).

WHY A MODULE OF ITS OWN
-----------------------
The fan-out rules ("a submit tells the supervisors, an approval tells the
person who did the work") are a policy, not a lifecycle mechanic. Keeping them
here means :func:`workflow.lifecycle.transition_task` has exactly ONE extra
line and the policy can change without touching the state machine.

TRANSACTION CONTRACT
--------------------
:func:`notify_transition` writes with plain ``db.execute`` and opens NO
transaction of its own: it is called from inside ``transition_task``'s and
``workflow.business_plan.transition_approval``'s ``db.write_transaction``
blocks, next to the ``task_history`` write, so the notification and the
transition it announces commit or roll back TOGETHER. A transition that fails
after this point (a stale revision, a failed commit) leaves no orphan
notification, and there is no window in which the bell shows an event the
board does not.

IDENTITY
--------
Recipients and actors are display-name strings -- ``users.name``, the same
value that lands in ``task_history.changed_by`` and
``project_tasks.assigned_to``. Every read here is scoped by ``recipient``, so
one user can never see, mark or count another user's rows: there is no
"notification by id" read that is not also filtered by the caller's own name.

THE AUTOMATION IDENTITY
-----------------------
``users.SYSTEM_USER`` ('System') walks the state machine for auto-completed
steps. It is never a RECIPIENT (nobody reads that bell) and its actions still
notify the human they affect: a System approval of a step assigned to a person
tells that person. The auto-complete walk assigns the step to System first, so
its own approve resolves to "recipient == actor" and is suppressed -- the
generic rule, not a special case.
"""
from __future__ import annotations

from typing import Any, Dict, List

import db
from helpers import utc_now_str

from .users import SYSTEM_USER

# transition action -> the stored ``event`` value and the verb its message uses.
# The keys are workflow.constants.TASK_TRANSITIONS' keys plus "reopen", which
# only the Business Plan Execution state machine exposes (an un-approve back to
# In Progress); an action missing here simply produces no notification.
#
# The stored EVENT vocabulary is fixed by models.Notification's CHECK
# constraint ('submitted','approved','returned','assigned') -- the bell renders
# a row per event, and an unknown string would render untitled. A reopen is
# therefore filed under 'returned' (both send the step back for update; the
# fan-out is identical) and carries its own VERB, which is what the message
# actually says.
_EVENTS = {
    "submit": ("submitted", "submitted"),
    "approve": ("approved", "approved"),
    "return": ("returned", "returned for update"),
    "reopen": ("returned", "reopened for update"),
}


def _clean(value) -> str:
    return str(value or "").strip()


def _same_person(a, b) -> bool:
    """Name equality, case-insensitively -- the same comparison
    ``transition_task`` uses for its assignee checks."""
    return _clean(a).lower() == _clean(b).lower()


def _active_supervisors(session, exclude) -> List[str]:
    """Active supervisor names, minus ``exclude`` and minus the automation user.

    'System' is seeded as a supervisor precisely so no role gate can block an
    automated walk (see users.ensure_system_user); it is not a person with a
    bell, so it never appears in a fan-out.
    """
    rows = db.fetch_all(session, """
        SELECT name FROM users
        WHERE is_active = 1 AND role = 'supervisor'
        ORDER BY name
    """)
    return [row["name"] for row in rows
            if not _same_person(row["name"], exclude) and not _same_person(row["name"], SYSTEM_USER)]


def _insert(session, recipient, actor, event, task, project_name, message) -> None:
    db.execute(session, """
        INSERT INTO notifications (
            created_at, recipient, actor, event, project_id, task_id,
            task_name, project_name, message, read_at
        ) VALUES (
            :created_at, :recipient, :actor, :event, :project_id, :task_id,
            :task_name, :project_name, :message, NULL
        )
    """, {"created_at": utc_now_str(), "recipient": recipient, "actor": actor,
          "event": event, "project_id": task.get("project_id"),
          "task_id": task.get("task_id"), "task_name": task.get("task_name"),
          "project_name": project_name, "message": message})


def notify_transition(session, task, action, actor, automated=False) -> List[str]:
    """Record the notifications one submit/approve/return/reopen transition produces.

    Fan-out rules (the whole policy, in one place):

    - ``submit``  -> every ACTIVE SUPERVISOR except the actor. Submitting asks
      for approval, and any supervisor can grant it, so the request goes to all
      of them. The actor is excluded even when a supervisor submits their own
      work -- nobody needs to be told what they just did. A submit BY the
      automation user, or any submit flagged ``automated``, notifies no one
      (see the inline note).
    - ``approve`` / ``return`` / ``reopen`` -> the component's ASSIGNEE, when there is one,
      it is not the actor, and the name still matches an active user (an
      assignee who has since been deactivated gets nothing rather than a row
      no one will ever read).

    ``automated`` marks a transition that a DRIVER performed as part of a
    multi-step walk rather than a human clicking the button -- today the
    field-completion engine's save -> submit -> approve. It generalizes the
    SYSTEM_USER rule below to walks that (deliberately) run under a real
    person's name.

    A recipient that resolves to blank produces NO row: ``recipient`` is NOT
    NULL by design, so "nobody to tell" is the absence of a notification, never
    an empty-string one.

    Writes inside the CALLER's transaction (see the module docstring). Returns
    the recipient names written, so callers and tests can assert the fan-out
    without re-querying.
    """
    mapping = _EVENTS.get(_clean(action).lower())
    if not mapping or not task:
        return []
    event, verb = mapping
    actor_name = _clean(actor)
    task_name = _clean(task.get("task_name")) or "a component"

    project = db.fetch_one(session, "SELECT project_name FROM projects WHERE project_id = :project_id",
                           {"project_id": task.get("project_id")}) or {}
    project_name = _clean(project.get("project_name"))

    if event == "submitted":
        # An AUTOMATED submit is not a request for approval: the driving walk
        # approves the same step microseconds later, so telling every
        # supervisor "X submitted Y" would fill their bell with approvals that
        # were never theirs to grant. Two ways to be automated, one rule:
        # the SYSTEM_USER identity (the non-prospective auto-complete walk), or
        # an explicit ``automated`` flag from a driver running under a real
        # person's name (the field-completion engine, whose whole point is that
        # the audit trail carries the SAVING USER). APPROVALS still notify the
        # human who owns the step (the branch below) -- that is news either way.
        recipients = [] if automated or _same_person(actor_name, SYSTEM_USER) \
            else _active_supervisors(session, actor_name)
    else:
        # For approve/return/reopen, notify all assignees (not just legacy assigned_to)
        assignees = db.fetch_all(session, """
            SELECT assignee_name FROM task_assignees WHERE task_id = :task_id
        """, {"task_id": task.get("task_id")})
        if assignees:
            recipients = []
            for a in assignees:
                name = _clean(a["assignee_name"])
                if name and not _same_person(name, actor_name) and not _same_person(name, SYSTEM_USER):
                    row = db.fetch_one(session, """
                        SELECT name FROM users WHERE LOWER(name) = LOWER(:name) AND is_active = 1
                    """, {"name": name})
                    if row:
                        recipients.append(row["name"])
        else:
            # Fallback to legacy assigned_to if no task_assignees rows
            assignee = _clean(task.get("assigned_to"))
            recipients = []
            if assignee and not _same_person(assignee, actor_name) and not _same_person(assignee, SYSTEM_USER):
                row = db.fetch_one(session, """
                    SELECT name FROM users WHERE LOWER(name) = LOWER(:name) AND is_active = 1
                """, {"name": assignee})
                if row:
                    recipients = [row["name"]]

    message = f"{actor_name or 'Someone'} {verb} {task_name}" + (f" on {project_name}" if project_name else "")
    for recipient in recipients:
        _insert(session, recipient, actor_name, event, task, project_name, message)
    return recipients


def notify_assignment(session, task, assignee_names: List[str], actor: str) -> List[str]:
    """Record the assignment notifications for one task.

    Called when a task becomes the next unapproved step (activation) or when
    manual assignees are added to an active task. Each recipient gets one
    notification with event='assigned'. Every active assignee is included,
    including the person who initiated the assignment: role membership means
    every group member receives the same reached-step alert. The automation
    user is excluded because it has no bell. Inactive users are skipped.

    Writes inside the CALLER's transaction (see the module docstring). Returns
    the recipient names written, so callers can assert the fan-out.
    """
    if not assignee_names or not task:
        return []
    actor_name = _clean(actor)
    task_name = _clean(task.get("task_name")) or "a component"

    project = db.fetch_one(session, "SELECT project_name FROM projects WHERE project_id = :project_id",
                           {"project_id": task.get("project_id")}) or {}
    project_name = _clean(project.get("project_name"))

    message = f"{actor_name or 'Someone'} assigned {task_name}" + (f" on {project_name}" if project_name else "")

    recipients = []
    for name in assignee_names:
        clean_name = _clean(name)
        if not clean_name or _same_person(clean_name, SYSTEM_USER):
            continue
        row = db.fetch_one(session, """
            SELECT name FROM users WHERE LOWER(name) = LOWER(:name) AND is_active = 1
        """, {"name": clean_name})
        if row:
            _insert(session, row["name"], actor_name, "assigned", task, project_name, message)
            recipients.append(row["name"])
    return recipients


# ---------------------------------------------------------------------------
# The per-recipient feed (every read is scoped by the caller's own name)
# ---------------------------------------------------------------------------

def list_notifications(session, recipient, limit: int = 50) -> List[Dict[str, Any]]:
    """The recipient's notifications, newest first (id breaks timestamp ties).

    ``pipeline_type`` is read LIVE from ``projects`` (never stored on the row):
    it is the board the click-through must open, and a lead promoted to BP
    after the event now lives on the other one. It is NULL when the project has
    since been deleted -- the client falls back to the prospect board.

    A blank recipient (no signed-in identity) yields an empty feed rather than
    an error: the bell polls this endpoint on every board refresh, and an open
    dev instance must not spew failures.
    """
    name = _clean(recipient)
    if not name:
        return []
    try:
        row_limit = max(1, min(int(limit), 200))
    except (TypeError, ValueError):
        row_limit = 50
    return db.fetch_all(session, """
        SELECT n.id, n.created_at, n.recipient, n.actor, n.event, n.project_id,
               n.task_id, n.task_name, n.project_name, n.message, n.read_at,
               p.pipeline_type AS pipeline_type
        FROM notifications n
        LEFT JOIN projects p ON p.project_id = n.project_id
        WHERE n.recipient = :recipient
        ORDER BY n.created_at DESC, n.id DESC
        LIMIT :limit
    """, {"recipient": name, "limit": row_limit})


def unread_count(session, recipient) -> int:
    """How many of the recipient's notifications are unread (0 when anonymous)."""
    name = _clean(recipient)
    if not name:
        return 0
    row = db.fetch_one(session, """
        SELECT COUNT(*) AS unread FROM notifications
        WHERE recipient = :recipient AND read_at IS NULL
    """, {"recipient": name})
    return int((row or {}).get("unread") or 0)


def mark_read(session, recipient, notification_id) -> None:
    """Mark ONE of the recipient's own notifications read.

    The row must exist AND belong to ``recipient``; anything else -- an unknown
    id, another user's id, a non-integer id, or no signed-in identity at all --
    raises ValueError (HTTP 400) with the SAME message, so the endpoint cannot
    be used to probe whether a given notification id exists.

    Idempotent: re-marking an already-read row succeeds and leaves the original
    ``read_at`` intact (the WHERE clause's ``read_at IS NULL`` guard). Nothing
    is ever deleted.
    """
    name = _clean(recipient)
    try:
        row_id = int(notification_id)
    except (TypeError, ValueError):
        raise ValueError("Notification not found.")
    owned = db.fetch_one(session, """
        SELECT id FROM notifications WHERE id = :id AND recipient = :recipient
    """, {"id": row_id, "recipient": name}) if name else None
    if not owned:
        raise ValueError("Notification not found.")
    with db.write_transaction(session):
        db.execute(session, """
            UPDATE notifications SET read_at = :now
            WHERE id = :id AND recipient = :recipient AND read_at IS NULL
        """, {"now": utc_now_str(), "id": row_id, "recipient": name})


def mark_all_read(session, recipient) -> int:
    """Mark every unread notification of ``recipient`` read; return how many.

    Idempotent (a second call updates 0 rows) and a no-op for a blank identity.
    Never touches another user's rows and never deletes.
    """
    name = _clean(recipient)
    if not name:
        return 0
    with db.write_transaction(session):
        result = db.execute(session, """
            UPDATE notifications SET read_at = :now
            WHERE recipient = :recipient AND read_at IS NULL
        """, {"now": utc_now_str(), "recipient": name})
    return int(result.rowcount or 0)


def notification_feed(session, recipient, limit: int = 50) -> Dict[str, Any]:
    """The bell's whole payload in ONE call: the list plus the unread count.

    The dot and the list must never disagree, and the client updates both from
    a single round trip (every mutation route returns the same ``unread_count``
    key for exactly that reason).
    """
    return {"notifications": list_notifications(session, recipient, limit),
            "unread_count": unread_count(session, recipient)}


__all__ = ["notify_transition", "notify_assignment", "list_notifications", "unread_count",
           "mark_read", "mark_all_read", "notification_feed"]
