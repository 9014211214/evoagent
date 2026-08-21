from pathlib import Path
from tempfile import TemporaryDirectory

from evoagent.campaigns import (
    ApprovalDecision,
    CampaignGovernanceService,
    CampaignRisk,
    CampaignType,
    SQLiteCampaignRepository,
)

with TemporaryDirectory() as directory:
    repository = SQLiteCampaignRepository(Path(directory) / "campaigns.db")
    governance = CampaignGovernanceService(repository)

    reservation = governance.reserve(
        campaign_type=CampaignType.MODEL,
        target_key="model:public/model-v0:multi-step-planning",
        fingerprint_source={"method": "sft", "budget": {"gpu_hours": 2}},
        risk=CampaignRisk.HIGH,
        generated_by="model-generator",
    )
    campaign = governance.attach_candidate(
        reservation.campaign,
        candidate_ref="model-candidate://multi-step-planning",
        artifact_payload={"kind": "model_candidate", "status": "candidate"},
    )
    campaign = governance.submit_evaluation(
        campaign.campaign_id,
        passed=True,
        expected_revision=campaign.revision,
        actor_id="independent-evaluator",
        reason="Held-out, regression and safety suites passed.",
    )
    campaign = governance.approve(
        campaign.campaign_id,
        actor_id="reviewer-a",
        decision=ApprovalDecision.APPROVE,
        reason="Capability and cost review passed.",
        expected_revision=campaign.revision,
    )
    campaign = governance.approve(
        campaign.campaign_id,
        actor_id="reviewer-b",
        decision=ApprovalDecision.APPROVE,
        reason="Security and data-governance review passed.",
        expected_revision=campaign.revision,
    )

    print("required approvals:", campaign.required_approvals)
    print("state:", campaign.state.value)
    print("deployed:", campaign.state.value == "completed")
    print("audit verified:", repository.verify_audit())
