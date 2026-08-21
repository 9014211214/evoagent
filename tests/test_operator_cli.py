import json

from evoagent.campaigns import (
    CampaignGovernanceService,
    CampaignRisk,
    CampaignState,
    CampaignType,
    SQLiteCampaignRepository,
)
from evoagent.cli import main
from evoagent.skills import SQLiteSkillRegistry, SkillSpec


def parse_stdout(capsys):
    return json.loads(capsys.readouterr().out)


def test_skill_cli_read_commands_do_not_mutate_registry(tmp_path, capsys):
    database = tmp_path / "skills.db"
    registry = SQLiteSkillRegistry(database)
    registry.register_initial(
        SkillSpec(
            skill_id="decision_skill",
            name="Decision Skill",
            version="1.0.0",
            description="Handle safe cases.",
            rules=("accept_safe",),
        )
    )
    checkpoint = registry.checkpoint()

    assert main(["skill", "list", "--db", str(database)]) == 0
    listing = parse_stdout(capsys)
    assert listing[0]["active_version"] == "1.0.0"

    assert main(
        [
            "skill",
            "show",
            "--db",
            str(database),
            "--skill-id",
            "decision_skill",
        ]
    ) == 0
    shown = parse_stdout(capsys)
    assert shown["spec"]["version"] == "1.0.0"
    assert SQLiteSkillRegistry(database).checkpoint() == checkpoint


def test_skill_cli_export_and_import_round_trip(tmp_path, capsys):
    source_db = tmp_path / "source.db"
    target_db = tmp_path / "target.db"
    bundle_path = tmp_path / "skills.json"
    source = SQLiteSkillRegistry(source_db)
    source.register_initial(
        SkillSpec(
            skill_id="decision_skill",
            name="Decision Skill",
            version="1.0.0",
            description="Handle safe cases.",
        )
    )

    assert main(
        ["skill", "export", "--db", str(source_db), "--out", str(bundle_path)]
    ) == 0
    exported = parse_stdout(capsys)
    assert exported["manifest_hash"]

    assert main(
        ["skill", "import", "--db", str(target_db), "--input", str(bundle_path)]
    ) == 0
    imported = parse_stdout(capsys)
    assert imported["manifest_hash"] == exported["manifest_hash"]
    assert SQLiteSkillRegistry(target_db).active("decision_skill").spec.version == "1.0.0"


def test_campaign_cli_requires_explicit_distinct_approvals_and_does_not_deploy(tmp_path, capsys):
    database = tmp_path / "campaigns.db"
    repository = SQLiteCampaignRepository(database)
    governance = CampaignGovernanceService(repository)
    reservation = governance.reserve(
        campaign_type=CampaignType.MODEL,
        target_key="model:public/model-v0:planning",
        fingerprint_source={"method": "sft"},
        risk=CampaignRisk.HIGH,
        generated_by="generator",
    )
    candidate = governance.attach_candidate(
        reservation.campaign,
        candidate_ref="model-candidate://planning",
        artifact_payload={"kind": "model_candidate"},
    )
    pending = governance.submit_evaluation(
        candidate.campaign_id,
        passed=True,
        expected_revision=candidate.revision,
        actor_id="evaluator",
        reason="evaluation passed",
    )

    assert main(
        [
            "campaign",
            "approve",
            "--db",
            str(database),
            "--campaign-id",
            pending.campaign_id,
            "--actor",
            "reviewer-a",
            "--reason",
            "risk review passed",
            "--expected-revision",
            str(pending.revision),
        ]
    ) == 0
    first = parse_stdout(capsys)
    assert first["state"] == CampaignState.APPROVAL_PENDING.value

    current = repository.get(pending.campaign_id)
    assert main(
        [
            "campaign",
            "approve",
            "--db",
            str(database),
            "--campaign-id",
            pending.campaign_id,
            "--actor",
            "reviewer-b",
            "--reason",
            "security review passed",
            "--expected-revision",
            str(current.revision),
        ]
    ) == 0
    second = parse_stdout(capsys)
    assert second["state"] == CampaignState.AUTHORIZED.value
    assert second["state"] != CampaignState.COMPLETED.value

    assert main(["campaign", "list", "--db", str(database)]) == 0
    listing = parse_stdout(capsys)
    assert listing[0]["campaign_id"] == pending.campaign_id
