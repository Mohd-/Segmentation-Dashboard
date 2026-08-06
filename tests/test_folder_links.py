"""Card 3AB -- the authoritative stage/step -> shared-folder mapping.

One table, consumed by both detail pages. A step it lists gets the approved
destination; a step it does not list gets no folder component at all. The
mapping is exercised through the real endpoints and by stable identifier
(task name on the Segment Maturation side, detail slug on the Business Plan
Execution side), never by visible label.
"""
from __future__ import annotations

import config
from conftest import create_project, get_task_by_name

ROOT = r"\\aramco.com\ecc\data\NAUGAD"


def _bp_project(client, name="FOLD-BP-1"):
    return create_project(client, name, pipeline_type="bp",
                          business_plan_enabled=True, business_plan_year=2030)


def _component_folder(client, project_id, task_name):
    task = get_task_by_name(client, project_id, task_name)
    return client.get(
        f"/api/projects/{project_id}/component-folder/{task['task_id']}").get_json()


def _step_folder(client, project_id, slug):
    body = client.get(
        f"/api/business-plan/wells/{project_id}/steps/{slug}").get_json()
    return body.get("folder")


# ---------------------------------------------------------------------------
# The nineteen supplied mappings
# ---------------------------------------------------------------------------

def test_every_segment_maturation_mapping_resolves_to_its_approved_destination(client):
    pid = create_project(client, "MDFT-3")
    expected = {
        "Lead Assessment": r"LEADS\MDFT\MDFT-3\POLYGONS_SURFACES",
        "Reservoir CoS": r"LEADS\MDFT\MDFT-3\SEGMENTATION",
        "Trap and Seal CoS": r"LEADS\MDFT\MDFT-3\SEGMENTATION",
        "Seismic Signature Validation": r"LEADS\MDFT\MDFT-3\SEGMENTATION",
        "Segmentation Slides": r"LEADS\MDFT\MDFT-3\SEGMENTATION",
        # The card's "Staking Letters" is this application's consolidated
        # staking page, which renders for both of its tracked items.
        "Approval to Stake": r"WELLS\MDFT\MDFT-3\ADMINISTRATION",
        "Well Site Location": r"WELLS\MDFT\MDFT-3\ADMINISTRATION",
    }
    for task_name, tail in expected.items():
        body = _component_folder(client, pid, task_name)
        assert body["requires_folder"] == 1, task_name
        assert body["unc_path"] == ROOT + "\\" + tail, task_name


def test_every_business_plan_mapping_resolves_to_its_approved_destination(client):
    pid = _bp_project(client, "KELS-1")
    well = r"WELLS\KELS\KELS-1"
    expected = {
        "well-letters": well + r"\GEOLOGY_AND_GEOPHYSICS\WELL_PROPOSAL",       # BP 1
        "gheer-inputs": well + r"\ADMINISTRATION\APPROVED_GHEER",              # BP 3
        "quicklook-logs": well + r"\LOGS\QUICKLOOK_LOGS",                      # BP 4
        "sad-model": well + r"\GEOLOGY_AND_GEOPHYSICS\PDA\SAD_MODEL",          # BP 6
        "summary-slides": well + r"\GEOLOGY_AND_GEOPHYSICS\PDA\EXECUTIVE_SUMMARY",   # BP 7
        "post-drill-learning-review": well + r"\GEOLOGY_AND_GEOPHYSICS\PDA",   # BP 8
        "flowback-results": well + r"\ENGINEERING\CONVENTIONAL_TESTING",       # BP 9
        "sad-model-update": well + r"\GEOLOGY_AND_GEOPHYSICS\PDA\SAD_MODEL_UPDATE",  # BP 10
        "final-summary-slides": well + r"\GEOLOGY_AND_GEOPHYSICS\PDA\EXECUTIVE_SUMMARY",  # BP 11
        "final-log-analysis": well + r"\LOGS\FINAL_PLOTS",                     # BP 12
        "structural-mtr": well + r"\GEOLOGY_AND_GEOPHYSICS\PDA\MTR",           # BP 13
        "pda-booking": well + r"\GEOLOGY_AND_GEOPHYSICS\PDA",                  # BP 14
    }
    for slug, tail in expected.items():
        folder = _step_folder(client, pid, slug)
        assert folder is not None, slug
        assert folder["unc_path"] == ROOT + "\\" + tail, slug


def test_two_slugs_sharing_one_task_take_different_destinations(client):
    """Why the BPE side is keyed by SLUG and not by task name.

    sad-model-update and final-summary-slides are both stored against the "SAD
    Update" task, and the approved mapping sends them to different folders. A
    task-name key could not express that.
    """
    pid = _bp_project(client, "SLUG-1")
    update = _step_folder(client, pid, "sad-model-update")
    summary = _step_folder(client, pid, "final-summary-slides")
    assert update["unc_path"].endswith(r"PDA\SAD_MODEL_UPDATE")
    assert summary["unc_path"].endswith(r"PDA\EXECUTIVE_SUMMARY")
    assert update["unc_path"] != summary["unc_path"]


# ---------------------------------------------------------------------------
# What the mapping does NOT list
# ---------------------------------------------------------------------------

def test_business_plan_gate_and_aramco_picks_get_no_folder(client):
    """BP 5 is intentionally absent from the supplied mapping -- Aramco Picks
    load into PETREL and GeoKnowledge, not a shared folder -- and the Gate has
    never had one."""
    pid = _bp_project(client, "NOFOLD-1")
    assert _step_folder(client, pid, "aramco-approved-pics") is None
    assert _step_folder(client, pid, "business-plan-gate") is None


def test_an_unmapped_segment_maturation_step_reports_no_folder_component(client):
    """Pre-Drilling GeoX Assessment and Moving Tolerance are not in the table.

    requires_folder 0 is how "render nothing" reaches the client; there is no
    path, blank or otherwise, for it to show.
    """
    pid = create_project(client, "NOFOLD-2")
    for task_name in ("Pre-Drilling GeoX Assessment", "Moving Tolerance"):
        body = _component_folder(client, pid, task_name)
        assert body == {"requires_folder": 0}, task_name


# ---------------------------------------------------------------------------
# Placeholders and path safety
# ---------------------------------------------------------------------------

def test_the_field_placeholder_resolves_from_the_records_own_name(client):
    """[FIELD] and [WELL_NAME] come from the application's existing split
    (folders.parse_field_and_well), not from a second parsing rule invented
    here."""
    pid = _bp_project(client, "RUBX-12")
    folder = _step_folder(client, pid, "quicklook-logs")
    assert folder["unc_path"] == ROOT + r"\WELLS\RUBX\RUBX-12\LOGS\QUICKLOOK_LOGS"


def test_a_name_that_cannot_be_split_still_resolves_both_placeholders(client):
    """A single-word record maps field and well to the same value, which is the
    existing convention -- not a missing value."""
    pid = _bp_project(client, "SOLO")
    folder = _step_folder(client, pid, "quicklook-logs")
    assert folder["unc_path"] == ROOT + r"\WELLS\SOLO\SOLO\LOGS\QUICKLOOK_LOGS"


def test_every_destination_is_a_unc_path_under_the_approved_root(client):
    """The source spreadsheet carried Excel smart-card markup pointing at
    http://aramco.com. That is metadata, not a destination: these are internal
    shares and none of them may become a web link."""
    for template in list(config.LEAD_STEP_FOLDER_LINKS.values()) + \
            list(config.BP_STEP_FOLDER_LINKS.values()):
        assert not template.startswith("\\"), template
        assert "http" not in template.lower(), template
        assert "smartCard" not in template, template
    assert config.NAUGAD_SHARE_ROOT == r"\\aramco.com\ecc\data\NAUGAD"


def test_a_stored_name_cannot_inject_a_path_segment(client):
    """Every resolved part goes through the existing _safe_folder_name, so a
    name carrying separators or traversal cannot reach outside its own folder."""
    import folders

    pid = create_project(client, "TRAV-1")
    import db
    session = db.get_session()
    with db.write_transaction(session):
        db.execute(session,
                   "UPDATE projects SET project_name = :name WHERE project_id = :pid",
                   {"name": r"..\..\ELSEWHERE", "pid": pid})
    body = folders.mapped_step_folder(session, pid, task_name="Reservoir CoS")
    # The separators are neutralised, so the name cannot become extra path
    # segments and no segment is a traversal step. It stays one folder name --
    # an ugly one, which is the right outcome for an ugly record name.
    segments = body["unc_path"][len(ROOT) + 1:].split("\\")
    assert segments == ["LEADS", "..-..-ELSEWHERE", "..-..-ELSEWHERE", "SEGMENTATION"]
    assert ".." not in segments


def test_a_missing_placeholder_blocks_the_link_instead_of_shortening_it(client):
    """A half-resolved UNC path points somewhere real and wrong. The step says
    what the record is missing rather than offering it."""
    import db
    import folders

    pid = create_project(client, "BLANK-1")
    session = db.get_session()
    with db.write_transaction(session):
        db.execute(session,
                   "UPDATE projects SET project_name = '' WHERE project_id = :pid",
                   {"pid": pid})
    body = folders.mapped_step_folder(session, pid, task_name="Reservoir CoS")
    assert body["requires_folder"] == 1
    assert body["unc_path"] == ""
    assert body["file_url"] == ""
    assert "Field" in body["blocked"] and "Lead Name" in body["blocked"]
