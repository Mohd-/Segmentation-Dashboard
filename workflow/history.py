"""Task history: the append-only audit-trail writer."""
from __future__ import annotations

import db
from helpers import utc_now_str


def log_task_event(session, task_id, project_id, task_name, action_type, old_status, new_status, changed_by, comment):
    """Append one row to the task_history audit trail (no commit)."""
    db.execute(session, """
        INSERT INTO task_history (
            task_id, project_id, task_name, action_type, old_status, new_status, changed_at, changed_by, comment
        ) VALUES (:task_id, :project_id, :task_name, :action_type, :old_status, :new_status, :changed_at, :changed_by, :comment)
    """, {"task_id": task_id, "project_id": project_id, "task_name": task_name,
          "action_type": action_type, "old_status": old_status, "new_status": new_status,
          "changed_at": utc_now_str(), "changed_by": changed_by, "comment": comment})
