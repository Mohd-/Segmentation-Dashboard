"""Focused contracts for development data under the consolidated lead step."""


def test_lead_assessment_seed_payload_completes_all_four_checkpoints(app_modules):
    import seed_dev
    from workflow.constants import LEAD_ASSESSMENT_CHECKPOINTS, lead_assessment_checkpoint_met

    fields = seed_dev._prospect_step_fields("Lead Assessment")

    assert fields
    assert all(lead_assessment_checkpoint_met(label, fields)
               for label in LEAD_ASSESSMENT_CHECKPOINTS), fields
    assert seed_dev._prospect_step_fields("Area Definition") is None
    assert seed_dev._prospect_step_fields("Thickness Estimation") is None
    assert seed_dev._prospect_step_fields("GRV Inputs") is None
    assert seed_dev._prospect_step_fields("Resource Assessment") is None
