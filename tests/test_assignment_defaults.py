"""Creation auto-assignment (owner items 6-9).

At PROSPECT lead creation every step of the operating pipeline is assigned
automatically -- which moves it Not Assigned -> In Progress through the SAME
lifecycle.assign_task mechanism POST /api/tasks/<id>/assign uses -- on this
resolution order (workflow.projects._resolve_creation_assignee):

    1. explicit per-step rule   config.STEP_ASSIGNMENT_RULES[step]["assignees"]
                                (item 6: Seismic Signature Validation -> Tahira)
    2. stage rule               config.PRE_WELL_ASSIGNEES for every Pre-Well
                                Delivery step (item 9: Saad/Salem)
    3. role rule                config.STEP_ASSIGNMENT_RULES[step]["role"] via
                                config.STEP_ROLE_POOLS (item 8; the pools SHIP
                                EMPTY until Nawaf's sheet arrives, and an empty
                                pool means the rule does not fire)
    4. creator default          the changed_by actor (item 7); blank/"System"/
                                unknown names leave the step Not Assigned.

Multi-candidate tiers pick via the module-level ``random`` in
workflow.projects, so tests monkeypatch/seed through that attribute.
BP-pipeline records are never touched.
"""
from __future__ import annotations

import pytest

from conftest import create_project, get_task_by_name, get_tasks, raw_sqlite_connect

PRE_WELL_STEPS = ["Moving Tolerance", "Approval to Stake",
                  "Well Site Location", "Pre-Drilling GeoX Assessment"]
SSV = "Seismic Signature Validation"

# The prospect steps covered by NO rule: these take the creator default.
CREATOR_DEFAULT_STEPS = ["Lead Assessment", "Reservoir CoS",
                         "Trap and Seal CoS", "Segmentation Slides"]


def login(client, name):
    resp = client.post("/api/login", json={"name": name})
    assert resp.status_code == 200, resp.get_json()


def history(client, task_id):
    conn = raw_sqlite_connect(client.db_path)
    try:
        rows = conn.execute(
            "SELECT action_type, old_status, new_status, changed_by, comment "
            "FROM task_history WHERE task_id = ? ORDER BY history_id", (task_id,)
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Item 7 -- creator default on every (otherwise unruled) prospect step
# ---------------------------------------------------------------------------

def test_creator_default_assigns_every_unruled_prospect_step(client):
    login(client, "Employee")
    pid = create_project(client, "AD-CREATOR-1")
    for step in CREATOR_DEFAULT_STEPS:
        task = get_task_by_name(client, pid, step)
        assert task["status"] == "In Progress", step
        assert task["assigned_to"] == "Employee", step
        assert task["actual_start"], "assignment stamps the start date"


def test_rule_steps_beat_the_creator(client):
    """Items 6/9 outrank item 7: the creator only takes the unruled steps."""
    login(client, "Employee")
    pid = create_project(client, "AD-CREATOR-2")
    assert get_task_by_name(client, pid, SSV)["assigned_to"] == "Tahira"
    for step in PRE_WELL_STEPS:
        assert get_task_by_name(client, pid, step)["assigned_to"] in {"Saad", "Salem"}, step


def test_anonymous_or_system_creator_leaves_unruled_steps_not_assigned(client):
    """No login -> actor "Web User" (not a users row); the unruled steps stay
    Not Assigned instead of being pinned on a placeholder identity. The rule
    steps still fire -- their assignees do not depend on the creator."""
    pid = create_project(client, "AD-ANON-1")
    for step in CREATOR_DEFAULT_STEPS:
        task = get_task_by_name(client, pid, step)
        assert task["status"] == "Not Assigned", step
        assert task["assigned_to"] is None, step
    assert get_task_by_name(client, pid, SSV)["status"] == "In Progress"


def test_a_system_creator_is_never_an_assignee(app_modules, tmp_path):
    """Direct add_project with the default changed_by="System": rule steps
    fire, everything else stays Not Assigned (System is an automation
    identity, not a person)."""
    _main, db = app_modules
    db.reset_for_tests()
    db.init_db(str(tmp_path / "ad-system.db"))
    import workflow
    session = db.new_session()
    try:
        pid = workflow.add_project(session, "AD-SYSTEM-1")
        for task in workflow.get_project_tasks(session, pid):
            assert task["assigned_to"] != "System", task["task_name"]
            if task["task_name"] in CREATOR_DEFAULT_STEPS:
                assert task["status"] == "Not Assigned", task["task_name"]
    finally:
        session.close()
        db.reset_for_tests()


# ---------------------------------------------------------------------------
# Item 6 -- the explicit step rule
# ---------------------------------------------------------------------------

def test_seismic_signature_validation_goes_to_tahira(client):
    login(client, "Supervisor")
    pid = create_project(client, "AD-SSV-1")
    task = get_task_by_name(client, pid, SSV)
    assert (task["status"], task["assigned_to"]) == ("In Progress", "Tahira")


# ---------------------------------------------------------------------------
# Item 9 -- the Pre-Well Delivery stage rule (random Saad-or-Salem)
# ---------------------------------------------------------------------------

def test_every_pre_well_step_gets_saad_or_salem(client, monkeypatch):
    """Deterministic pick: seed the module-level random. Each of the four
    Pre-Well Delivery steps is assigned independently from the pool."""
    from workflow import projects

    monkeypatch.setattr(projects.random, "choice", lambda seq: sorted(seq)[0])
    pid = create_project(client, "AD-PREWELL-1")
    for step in PRE_WELL_STEPS:
        task = get_task_by_name(client, pid, step)
        assert task["status"] == "In Progress", step
        assert task["assigned_to"] == "Saad", step  # deterministic first pick


def test_pre_well_picks_are_drawn_per_step_from_the_pool(client, monkeypatch):
    from workflow import projects

    picks = iter(["Tahira", "Salem", "Saad", "Salem", "Saad"])  # SSV first, then 4 pre-well

    def scripted_choice(seq):
        value = next(picks)
        assert value in seq or value == "Tahira"
        return value

    monkeypatch.setattr(projects.random, "choice", scripted_choice)
    pid = create_project(client, "AD-PREWELL-2")
    assigned = [get_task_by_name(client, pid, step)["assigned_to"] for step in PRE_WELL_STEPS]
    assert assigned == ["Salem", "Saad", "Salem", "Saad"]


# ---------------------------------------------------------------------------
# Item 8 -- role rules through STEP_ROLE_POOLS
# ---------------------------------------------------------------------------

@pytest.fixture()
def role_rule(monkeypatch):
    """A role rule on Reservoir CoS, with the pool controlled per-test."""
    import config

    monkeypatch.setitem(config.STEP_ASSIGNMENT_RULES, "Reservoir CoS",
                        {"role": "petrophysicist"})
    return config


def test_a_role_rule_resolves_through_a_non_empty_pool(client, role_rule, monkeypatch):
    monkeypatch.setitem(role_rule.STEP_ROLE_POOLS, "petrophysicist", ["Staff Member"])
    login(client, "Employee")
    pid = create_project(client, "AD-ROLE-1")
    task = get_task_by_name(client, pid, "Reservoir CoS")
    assert (task["status"], task["assigned_to"]) == ("In Progress", "Staff Member")


def test_an_empty_role_pool_is_skipped_and_falls_to_the_creator(client, role_rule):
    """The shipped state: pools are empty until Nawaf's sheet lands, so the
    role rule stands down and the creator default takes the step."""
    assert role_rule.STEP_ROLE_POOLS == {}
    login(client, "Employee")
    pid = create_project(client, "AD-ROLE-2")
    task = get_task_by_name(client, pid, "Reservoir CoS")
    assert (task["status"], task["assigned_to"]) == ("In Progress", "Employee")


def test_an_explicit_assignees_rule_beats_the_stage_rule(client, monkeypatch):
    """Resolution order tier 1 > tier 2: a per-step "assignees" rule on a
    Pre-Well Delivery step wins over the Saad/Salem stage pool."""
    import config

    monkeypatch.setitem(config.STEP_ASSIGNMENT_RULES, "Moving Tolerance",
                        {"assignees": ["Employee"]})
    pid = create_project(client, "AD-ORDER-1")
    assert get_task_by_name(client, pid, "Moving Tolerance")["assigned_to"] == "Employee"
    # The other three Pre-Well steps still draw from the stage pool.
    for step in PRE_WELL_STEPS[1:]:
        assert get_task_by_name(client, pid, step)["assigned_to"] in {"Saad", "Salem"}, step


def test_a_rule_naming_an_unknown_user_leaves_the_step_not_assigned(client, monkeypatch):
    """A mistyped config name must surface as an unassigned step, never fail
    lead creation (assign_task would 400 on an unknown assignee)."""
    import config

    monkeypatch.setitem(config.STEP_ASSIGNMENT_RULES, "Segmentation Slides",
                        {"assignees": ["Nobody Real"]})
    pid = create_project(client, "AD-UNKNOWN-1")
    task = get_task_by_name(client, pid, "Segmentation Slides")
    assert (task["status"], task["assigned_to"]) == ("Not Assigned", None)


# ---------------------------------------------------------------------------
# Mechanism: the real assign walk, history, and no completion side effects
# ---------------------------------------------------------------------------

def test_auto_assignment_logs_one_component_assigned_event_per_step(client):
    login(client, "Supervisor")
    pid = create_project(client, "AD-HISTORY-1")
    task = get_task_by_name(client, pid, SSV)
    events = [row for row in history(client, task["task_id"])
              if row["action_type"] == "Component Assigned"]
    assert len(events) == 1
    assert (events[0]["old_status"], events[0]["new_status"]) == ("Not Assigned", "In Progress")
    assert events[0]["changed_by"] == "Supervisor"  # the creator is the audit actor
    assert events[0]["comment"] == "Assigned to Tahira (auto-assigned at creation)."


def test_auto_assignment_never_completes_or_submits_anything(client):
    """Assignment moves steps to In Progress ONLY: a fresh lead has empty
    fields, so nothing may auto-approve, and no engine event may exist."""
    login(client, "Supervisor")
    pid = create_project(client, "AD-NOHOOKS-1")
    for task in get_tasks(client, pid):
        assert task["status"] in ("Not Assigned", "In Progress"), task["task_name"]
        assert not [row for row in history(client, task["task_id"])
                    if row["action_type"] in ("Field Completion", "Field Reopen",
                                              "Component Submitted", "Component Approved")]


def test_the_board_derives_the_new_assignees_and_owner_filter_sees_them(client):
    login(client, "Employee")
    pid = create_project(client, "AD-BOARD-1")
    rows = client.get("/api/projects?pipeline_filter=prospect").get_json()
    row = next(r for r in rows if r["project_id"] == pid)
    # Sequence order: creator on step 1 first, Tahira at step 4, then the
    # Pre-Well picks; deduped.
    assert row["assignees"][0] == "Employee"
    assert "Tahira" in row["assignees"]
    assert set(row["assignees"]) <= {"Employee", "Tahira", "Saad", "Salem"}
    # current_owner (the first open step's assignee) now resolves -> the
    # board's Assignee filter matches the lead.
    assert row["current_owner"] == "Employee"
    filtered = client.get("/api/projects?pipeline_filter=prospect&owner_filter=Employee").get_json()
    assert pid in [r["project_id"] for r in filtered]


# ---------------------------------------------------------------------------
# Scope: BP records and the BP half of a prospect are untouched
# ---------------------------------------------------------------------------

def test_bp_creation_is_untouched(client):
    login(client, "Supervisor")
    pid = create_project(client, "AD-BP-1", pipeline_type="bp",
                         business_plan_enabled=True, business_plan_year=2027)
    for task in get_tasks(client, pid):
        assert task["status"] == "Not Assigned", task["task_name"]
        assert task["assigned_to"] is None, task["task_name"]


def test_a_prospects_bp_stage_rows_are_not_assigned_at_creation(client):
    login(client, "Supervisor")
    pid = create_project(client, "AD-BPROWS-1")
    for task in get_tasks(client, pid):
        if task["stage_group"] in ("Well Delivery", "Post-Drilling", "Post-Testing"):
            assert task["status"] == "Not Assigned", task["task_name"]
            assert task["assigned_to"] is None, task["task_name"]


# ---------------------------------------------------------------------------
# The three named assignees are real, listed users
# ---------------------------------------------------------------------------

def test_the_users_listing_offers_tahira_saad_and_salem(client):
    names = [u["name"] for u in client.get("/api/users").get_json()]
    for name in ("Tahira", "Saad", "Salem"):
        assert name in names
    roles = {u["name"]: u["role"] for u in client.get("/api/users").get_json()}
    assert {roles["Tahira"], roles["Saad"], roles["Salem"]} == {"employee"}
