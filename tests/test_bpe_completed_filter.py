"""Focused contract tests for the BPE dashboard's Completed filter."""
from __future__ import annotations

from workflow import business_plan


def _well_with_states(completed_keys):
    states = {}
    for stage in business_plan.STAGES:
        for key, _label, _detail_slug in stage["items"]:
            states[key] = {
                "status": "Completed" if key in completed_keys else "In Progress",
                "source": "manual",
            }
    current_keys = [key for key, _label, _detail_slug in business_plan.STAGES[0]["items"]]
    return {
        "assignees": [],
        "field": "MDFT",
        "business_plan_year": 2026,
        "items": [dict(states[key], key=key) for key in current_keys],
        "all_states": states,
    }


def _filters(status, step="all"):
    return {
        "assignee": "All Assignees",
        "field": "All Fields",
        "year": 2026,
        "step": step,
        "status": status,
    }


def test_completed_filter_rejects_a_well_with_only_one_completed_current_item():
    well = _well_with_states({"business-plan-gate"})

    assert business_plan._matches_filters(well, _filters("Completed")) is False


def test_completed_filter_requires_and_accepts_all_eighteen_effective_items():
    keys = set()
    for stage in business_plan.STAGES:
        keys.update(key for key, _label, _detail_slug in stage["items"])
    assert len(keys) == 18
    well = _well_with_states(keys)

    # Effective completion has already normalized manual, approved, system,
    # and non-applicable outcomes. The dashboard filter consumes that one
    # canonical status while retaining its provenance.
    sources = ("manual", "approval", "system")
    for index, state in enumerate(well["all_states"].values()):
        state["source"] = sources[index % len(sources)]

    assert business_plan._matches_filters(well, _filters("Completed")) is True

    well["all_states"]["final-logs"]["status"] = "In Progress"
    assert business_plan._matches_filters(well, _filters("Completed")) is False


def test_other_statuses_keep_the_existing_current_stage_semantics():
    well = _well_with_states({"business-plan-gate"})

    assert business_plan._matches_filters(well, _filters("In Progress")) is True
    assert business_plan._matches_filters(
        well, _filters("In Progress", step="site-preparation")) is True
