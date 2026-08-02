"""Card 1B: the derived lead-card fields on the project read payloads.

`assignees`, `tracked_items`, `display_stage` and `lead_priority` are a READ-TIME
presentation layer over the stored workflow (workflow/projects.py's Card 1B
adapter). Nothing here may change what is stored, so every test drives the
normal API (assign / submit / approve / priority) and only asserts on what the
read payloads report back.
"""
from __future__ import annotations

from conftest import create_project, get_task_by_name, get_tasks, raw_sqlite_connect

LEAD_ASSESSMENT_LABELS = ["Area Definition", "Thickness Estimation", "GRV Inputs", "Resource Assessment"]
RISK_ANALYSIS_LABELS = ["Reservoir", "Trap and Seal", "Seismic Validation", "Segmentation Slides"]
PRE_WELL_LABELS = ["Moving Tolerance", "Approval to Stake", "Well Site Location", "GeoX Assessment"]

# The stored prospect steps, in sequence order (workflow.PIPELINE_TEMPLATES 1-12).
PROSPECT_STEPS = [
    "Reservoir Area Definition", "Thickness Estimation", "Lead Resource Assessment",
    "Seismic Signature Validation", "Reservoir CoS", "Trap CoS", "Seal CoS",
    "Prospect Evaluation Presentation", "Well Creation", "Pre-Drilling Resource Assessment",
    "Staking Moving Tolerance", "Approval to Stake",
]


# ---------------------------------------------------------------------------
# Helpers -- everything goes through the real endpoints
# ---------------------------------------------------------------------------

def _assign(client, task, assignee):
    resp = client.post(f"/api/tasks/{task['task_id']}/assign",
                       json={"assignee": assignee, "cascade": False, "revision": task["revision"]})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["task"]


def _transition(client, task, action):
    resp = client.post(f"/api/tasks/{task['task_id']}/transition",
                       json={"action": action, "revision": task["revision"]})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["task"]


def _set_ready(client, pid, step_name, assignee="Employee"):
    """Drive one step to the Ready (submitted, awaiting approval) status."""
    task = get_task_by_name(client, pid, step_name)
    return _transition(client, _assign(client, task, assignee), "submit")


def _approve(client, pid, step_name, assignee="Employee"):
    """Drive one step all the way to Approved."""
    return _transition(client, _set_ready(client, pid, step_name, assignee), "approve")


def _board_row(client, pid, pipeline="prospect"):
    """The lead's row as the BOARD sees it (GET /api/projects projection)."""
    rows = client.get(f"/api/projects?pipeline_filter={pipeline}").get_json()
    matches = [row for row in rows if row["project_id"] == pid]
    assert matches, f"project {pid} is not on the {pipeline} board"
    return matches[0]


def _detail_row(client, pid):
    """The lead's full row (GET /api/projects/<id>) -- still annotated, and the
    only reader left once a fully approved lead leaves the board."""
    resp = client.get(f"/api/projects/{pid}")
    assert resp.status_code == 200
    return resp.get_json()


def _items(row):
    """tracked_items keyed by label, for readable assertions."""
    return {item["label"]: item["status"] for item in row["tracked_items"]}


# ---------------------------------------------------------------------------
# The 12-item presentation model: shape, order, stages
# ---------------------------------------------------------------------------

def test_tracked_items_shape_order_and_stages(client):
    pid = create_project(client, "TRACKED-SHAPE-1")
    items = _board_row(client, pid)["tracked_items"]

    assert [item["label"] for item in items] == (
        LEAD_ASSESSMENT_LABELS + RISK_ANALYSIS_LABELS + PRE_WELL_LABELS)
    assert [item["stage"] for item in items] == (
        ["Lead Assessment"] * 4 + ["Risk Analysis"] * 4 + ["Pre-Well Delivery"] * 4)
    # Exactly four keys per item: the three the CARD renders, plus Card 2A's
    # `steps` (the item's source step names) that the lead detail page's
    # three-stage sidebar opens the real components from.
    for item in items:
        assert set(item) == {"stage", "label", "status", "steps"}
    by_label = {item["label"]: item["steps"] for item in items}
    # The two items with NO stored step yet stay empty -- the sidebar renders
    # them as dimmed, non-clickable placeholder rows.
    assert by_label["GRV Inputs"] == []
    assert by_label["Well Site Location"] == []
    # The one multi-source item keeps BOTH of its steps, in order.
    assert by_label["Trap and Seal"] == ["Trap CoS", "Seal CoS"]
    assert by_label["Thickness Estimation"] == ["Thickness Estimation"]


def test_a_fresh_lead_reads_entirely_in_progress(client):
    """Not Assigned is NOT a display status: untouched work reads In Progress."""
    pid = create_project(client, "TRACKED-FRESH-1")
    row = _board_row(client, pid)
    assert all(task["status"] == "Not Assigned" for task in get_tasks(client, pid))
    assert set(_items(row).values()) == {"In Progress"}
    assert row["display_stage"] == "Lead Assessment"
    assert row["assignees"] == []


def test_an_approved_step_completes_its_item_only(client):
    pid = create_project(client, "TRACKED-ONE-1")
    _approve(client, pid, "Reservoir Area Definition")
    items = _items(_board_row(client, pid))
    assert items["Area Definition"] == "Completed"
    assert items["Thickness Estimation"] == "In Progress"
    assert list(items.values()).count("Completed") == 1


def test_trap_and_seal_completes_only_when_both_are_approved(client):
    pid = create_project(client, "TRACKED-TRAPSEAL-1")
    _approve(client, pid, "Trap CoS")
    assert _items(_board_row(client, pid))["Trap and Seal"] == "In Progress"

    _approve(client, pid, "Seal CoS")
    assert _items(_board_row(client, pid))["Trap and Seal"] == "Completed"


def test_a_ready_trap_cos_does_not_make_trap_and_seal_pending(client):
    """Pending Approval is reserved for Segmentation Slides; a combined item
    submitted-but-unapproved is still just In Progress."""
    pid = create_project(client, "TRACKED-TRAPSEAL-2")
    _set_ready(client, pid, "Trap CoS")
    _set_ready(client, pid, "Seal CoS")
    assert _items(_board_row(client, pid))["Trap and Seal"] == "In Progress"


def test_pending_approval_is_only_for_segmentation_slides(client):
    """Ready reads Pending Approval on Segmentation Slides and NOWHERE else --
    a returned/reopened submission drops back to In Progress, and the card must
    not distinguish "never submitted" from "submitted then returned"."""
    pid = create_project(client, "TRACKED-READY-1")
    for step in ("Thickness Estimation", "Reservoir CoS", "Staking Moving Tolerance",
                 "Pre-Drilling Resource Assessment", "Prospect Evaluation Presentation"):
        _set_ready(client, pid, step)
    items = _items(_board_row(client, pid))

    assert items["Segmentation Slides"] == "Pending Approval"
    assert [label for label, status in items.items() if status == "Pending Approval"] == ["Segmentation Slides"]
    assert items["Thickness Estimation"] == "In Progress"
    assert items["Reservoir"] == "In Progress"
    assert items["Moving Tolerance"] == "In Progress"
    assert items["GeoX Assessment"] == "In Progress"


def test_returning_a_segmentation_slides_submission_drops_it_back_to_in_progress(client):
    pid = create_project(client, "TRACKED-RETURN-1")
    ready = _set_ready(client, pid, "Prospect Evaluation Presentation")
    assert _items(_board_row(client, pid))["Segmentation Slides"] == "Pending Approval"

    _transition(client, ready, "return")
    assert _items(_board_row(client, pid))["Segmentation Slides"] == "In Progress"


def test_the_two_sourceless_items_never_complete(client):
    """GRV Inputs and Well Site Location have no feeding step in the current
    12-step pipeline. They must stay In Progress even on a lead where every
    stored step is Approved -- the permanent migration supplies the real steps."""
    pid = create_project(client, "TRACKED-SOURCELESS-1")
    for step in PROSPECT_STEPS:
        _approve(client, pid, step)

    # A fully approved lead leaves the board for the Portfolio, so read the
    # detail payload -- same derivation, same adapter.
    items = _items(_detail_row(client, pid))
    assert items["GRV Inputs"] == "In Progress"
    assert items["Well Site Location"] == "In Progress"
    assert [label for label, status in items.items() if status != "Completed"] == [
        "GRV Inputs", "Well Site Location"]


# ---------------------------------------------------------------------------
# display_stage
# ---------------------------------------------------------------------------

def test_display_stage_follows_the_derived_stage_through_the_pipeline(client):
    pid = create_project(client, "DISPLAY-STAGE-1")
    assert _board_row(client, pid)["display_stage"] == "Lead Assessment"

    for step in PROSPECT_STEPS[:3]:                       # Lead Identification done
        _approve(client, pid, step)
    row = _board_row(client, pid)
    assert row["current_stage"] == "Risking"
    assert row["display_stage"] == "Risk Analysis"

    for step in PROSPECT_STEPS[3:7]:                      # Risking done
        _approve(client, pid, step)
    row = _board_row(client, pid)
    # The one collapse Card 1B introduces: Segmentation joins Risk Analysis.
    assert row["current_stage"] == "Segmentation"
    assert row["display_stage"] == "Risk Analysis"

    _approve(client, pid, "Prospect Evaluation Presentation")
    row = _board_row(client, pid)
    assert row["current_stage"] == "Pre-Well Delivery"
    assert row["display_stage"] == "Pre-Well Delivery"


# ---------------------------------------------------------------------------
# assignees
# ---------------------------------------------------------------------------

def test_assignees_are_distinct_ordered_and_blank_free(client):
    pid = create_project(client, "ASSIGNEES-1")
    _assign(client, get_task_by_name(client, pid, "Thickness Estimation"), "Staff Member")
    _assign(client, get_task_by_name(client, pid, "Reservoir CoS"), "Employee")
    # The same person on a later step must not appear twice.
    _assign(client, get_task_by_name(client, pid, "Approval to Stake"), "Staff Member")

    row = _board_row(client, pid)
    assert row["assignees"] == ["Staff Member", "Employee"]  # sequence order, deduped
    # assignees is NOT current_owner: the current task here is still the
    # unassigned first step, so the existing derivation is untouched.
    assert row["current_owner"] is None


def test_assignees_ignore_tasks_outside_the_operating_pipeline(client):
    """A prospect's BP-stage rows exist but are not its work: assigning one
    must not add a name to the lead card."""
    pid = create_project(client, "ASSIGNEES-2")
    _assign(client, get_task_by_name(client, pid, "Well Proposal"), "Employee")
    assert _board_row(client, pid)["assignees"] == []


# ---------------------------------------------------------------------------
# lead_priority
# ---------------------------------------------------------------------------

def test_lead_priority_takes_the_most_urgent_open_task(client):
    pid = create_project(client, "PRIORITY-1")
    # Card 1D: every step is created Low, so a fresh lead reads Low (gray card)
    # until somebody escalates a step.
    assert _board_row(client, pid)["lead_priority"] == "Low"

    task = get_task_by_name(client, pid, "Staking Moving Tolerance")
    assert client.patch(f"/api/tasks/{task['task_id']}/priority", json={"priority": "High"}).status_code == 200
    assert _board_row(client, pid)["lead_priority"] == "High"


def test_lead_priority_ignores_approved_work(client):
    """Finished work cannot keep a lead urgent."""
    pid = create_project(client, "PRIORITY-2")
    task = get_task_by_name(client, pid, "Reservoir Area Definition")
    assert client.patch(f"/api/tasks/{task['task_id']}/priority", json={"priority": "High"}).status_code == 200
    assert _board_row(client, pid)["lead_priority"] == "High"

    _approve(client, pid, "Reservoir Area Definition")
    # Back to the creation default once the escalated step is approved.
    assert _board_row(client, pid)["lead_priority"] == "Low"


def test_lead_priority_defaults_to_low_when_no_value_is_recognized(client):
    """Legacy rows can carry the models.py server default 'Normal', which is not
    a board priority -- an unrecognized value is treated as absent."""
    pid = create_project(client, "PRIORITY-3")
    conn = raw_sqlite_connect(client.db_path)
    with conn:
        conn.execute("UPDATE project_tasks SET priority = 'Normal' WHERE project_id = ?", (pid,))
    conn.close()
    assert _board_row(client, pid)["lead_priority"] == "Low"


# ---------------------------------------------------------------------------
# Card 1D -- what a brand-new lead looks like on the board
# ---------------------------------------------------------------------------

def test_new_lead_board_defaults(client):
    """One assertion block for every default Card 1D specifies for a fresh lead:
    Lead Assessment column, all twelve tracked items In Progress (nothing
    auto-completed), no assignees ("Unassigned" on the card), Low priority
    (gray border), and the name-derived field."""
    pid = create_project(client, "NEWDEF-3", lead_x="123.5", lead_y="-456.25")
    row = _board_row(client, pid)

    assert row["display_stage"] == "Lead Assessment"     # stored: Lead Identification
    assert row["assignees"] == []
    assert row["lead_priority"] == "Low"
    assert len(row["tracked_items"]) == 12
    assert {item["status"] for item in row["tracked_items"]} == {"In Progress"}
    # A lead ALWAYS has a field: it is DERIVED from the name prefix
    # (folders.parse_field_and_well), never selected at creation.
    assert row["field"] == "NEWDEF"
    # Coordinates entered in the Add New Lead control land on the project row.
    detail = client.get(f"/api/projects/{pid}").get_json()
    assert (float(detail["lead_x"]), float(detail["lead_y"])) == (123.5, -456.25)


def test_new_lead_leaves_every_stored_step_not_assigned_and_low(client):
    """Nothing is auto-completed at creation, and the stored priority backing the
    gray card is Low on every materialized step."""
    pid = create_project(client, "NEWDEF-4")
    tasks = get_tasks(client, pid)
    assert {task["status"] for task in tasks} == {"Not Assigned"}
    assert {task["priority"] for task in tasks} == {"Low"}


# ---------------------------------------------------------------------------
# The BP board is untouched
# ---------------------------------------------------------------------------

def test_bp_wells_carry_no_tracked_items_and_keep_their_stored_stage(client):
    pid = create_project(client, "BP-CARD-1", pipeline_type="bp",
                         business_plan_enabled=True, business_plan_year=2030)
    _assign(client, get_task_by_name(client, pid, "Well Proposal"), "Employee")

    row = _board_row(client, pid, pipeline="bp")
    assert row["tracked_items"] == []
    # No display mapping for BP stage groups: the stored stage passes through.
    assert row["display_stage"] == row["current_stage"] == "Well Delivery"
    # assignees / lead_priority are pipeline-agnostic and still derived.
    assert row["assignees"] == ["Employee"]
    assert row["lead_priority"] == "Low"
