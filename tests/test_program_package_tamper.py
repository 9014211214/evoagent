import json
import shutil
from datetime import datetime

import pytest

from evoagent.campaigns import SQLiteCampaignRepository
from evoagent.lab import MultiGenerationEvolutionProgramLab
from evoagent.model_registry.models import canonical_sha256
from evoagent.program import (
    EvolutionProgramPackageManager,
    ProgramEventType,
    SQLiteEvolutionProgramRepository,
)


@pytest.fixture(scope="module")
def source_package(tmp_path_factory):
    root = tmp_path_factory.mktemp("program-package")
    result = MultiGenerationEvolutionProgramLab(
        root / "lab",
        source_commit="f" * 40,
    ).run()
    return result.package_path


def _copy_and_rewrite(source, destination, mutate):
    shutil.copyfile(source, destination)
    payload = json.loads(destination.read_text(encoding="utf-8"))
    mutate(payload)
    payload["package_hash"] = canonical_sha256(
        {key: value for key, value in payload.items() if key != "package_hash"}
    )
    destination.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _rehash(record, field):
    record[field] = canonical_sha256(
        {key: value for key, value in record.items() if key != field}
    )


def _parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _rehash_program_chain(payload):
    previous_hash = "0" * 64
    for event in payload["program_events"]:
        event["previous_hash"] = previous_hash
        event["event_hash"] = SQLiteEvolutionProgramRepository._event_hash(
            sequence=event["sequence"],
            event_id=event["event_id"],
            program_id=event["program_id"],
            generation_id=event["generation_id"],
            event_type=ProgramEventType(event["event_type"]),
            actor_id=event["actor_id"],
            reason=event["reason"],
            payload=event["payload"],
            created_at=_parse_time(event["created_at"]),
            previous_hash=previous_hash,
        )
        previous_hash = event["event_hash"]
    payload["program_checkpoint"] = {
        "event_count": len(payload["program_events"]),
        "head_hash": previous_hash,
    }


def _rehash_campaign_chain(payload):
    previous_hash = "0" * 64
    for event in payload["campaign_events"]:
        event["previous_hash"] = previous_hash
        event["event_hash"] = SQLiteCampaignRepository._event_hash(
            sequence=event["sequence"],
            event_id=event["event_id"],
            campaign_id=event["campaign_id"],
            event_type=event["event_type"],
            actor_id=event["actor_id"],
            payload=event["payload"],
            created_at=_parse_time(event["created_at"]),
            previous_hash=previous_hash,
        )
        previous_hash = event["event_hash"]
    payload["campaign_checkpoint"] = {
        "event_count": len(payload["campaign_events"]),
        "head_hash": previous_hash,
    }


def test_package_rejects_feedback_reason_rewrite(source_package, tmp_path):
    path = tmp_path / "signal.json"

    def mutate(payload):
        signal = payload["signal"]
        signal["reasons"].append("forged_reason")
        _rehash(signal, "signal_hash")

    _copy_and_rewrite(source_package, path, mutate)
    with pytest.raises(ValueError):
        EvolutionProgramPackageManager().load_file(path)


def test_package_rejects_attribution_and_plan_rewrite(source_package, tmp_path):
    path = tmp_path / "plan.json"

    def mutate(payload):
        attribution = payload["attribution"]
        attribution["failure_layer"] = "skill"
        attribution["action"] = "update_skill"
        _rehash(attribution, "receipt_hash")
        plan = payload["generations"][1]["plan"]
        plan["intervention_layer"] = "skill"
        plan["intervention_action"] = "update_skill"
        plan["attribution_receipt_hash"] = attribution["receipt_hash"]
        _rehash(plan, "plan_hash")

    _copy_and_rewrite(source_package, path, mutate)
    with pytest.raises(ValueError):
        EvolutionProgramPackageManager().load_file(path)


def test_package_rejects_child_target_identity_rewrite(source_package, tmp_path):
    path = tmp_path / "target.json"

    def mutate(payload):
        outcome = payload["generations"][1]["outcome"]
        outcome["agent_identity_hash"] = "0" * 64
        _rehash(outcome, "outcome_hash")

    _copy_and_rewrite(source_package, path, mutate)
    with pytest.raises(ValueError, match="evidence|identity|outcome"):
        EvolutionProgramPackageManager().load_file(path)


def test_package_rejects_approval_identity_reason_and_time_substitution(
    source_package,
    tmp_path,
):
    for field, value in (
        ("actor_id", "substituted-reviewer"),
        ("reason", "substituted approval rationale"),
        ("created_at", "2026-08-12T23:59:59Z"),
    ):
        path = tmp_path / f"approval-{field}.json"

        def mutate(payload, *, field=field, value=value):
            payload["generation_approvals"][0][field] = value

        _copy_and_rewrite(source_package, path, mutate)
        with pytest.raises(
            ValueError,
            match="approval identity|approval identity, reason, or time|independent approvals",
        ):
            EvolutionProgramPackageManager().load_file(path)


def test_package_rejects_final_head_substitution(source_package, tmp_path):
    path = tmp_path / "head.json"

    def mutate(payload):
        payload["final_head"]["active_generation_id"] = "program-generation:g0"

    _copy_and_rewrite(source_package, path, mutate)
    with pytest.raises(ValueError):
        EvolutionProgramPackageManager().load_file(path)


def test_package_rejects_rehashed_budget_control_outcome(source_package, tmp_path):
    path = tmp_path / "budget-control-outcome.json"

    def mutate(payload):
        control = payload["budget_control"]
        outcome = control["generations"][0]["outcome"]
        outcome["quality_delta"] = outcome["quality_delta"] + 0.125
        _rehash(outcome, "outcome_hash")
        _rehash(control, "control_hash")

    _copy_and_rewrite(source_package, path, mutate)
    with pytest.raises(ValueError, match="verified drift release package"):
        EvolutionProgramPackageManager().load_file(path)


def test_package_rejects_rehashed_ambiguous_control_head(source_package, tmp_path):
    path = tmp_path / "ambiguous-control-head.json"

    def mutate(payload):
        control = payload["ambiguous_control"]
        control["final_head"]["total_pairs"] += 1
        _rehash(control, "control_hash")

    _copy_and_rewrite(source_package, path, mutate)
    with pytest.raises(ValueError, match="control head differs"):
        EvolutionProgramPackageManager().load_file(path)


def test_package_rejects_unbound_extra_control_attribution(source_package, tmp_path):
    path = tmp_path / "ambiguous-extra-attribution.json"

    def mutate(payload):
        control = payload["ambiguous_control"]
        extra = dict(control["attributions"][0])
        extra["receipt_id"] = "program-attribution:ambiguous:g0:extra"
        _rehash(extra, "receipt_hash")
        control["attributions"].append(extra)
        _rehash(control, "control_hash")

    _copy_and_rewrite(source_package, path, mutate)
    with pytest.raises(ValueError, match="Attribution cardinality"):
        EvolutionProgramPackageManager().load_file(path)


def test_package_rejects_rehashed_feedback_ingestion_actor(
    source_package,
    tmp_path,
):
    path = tmp_path / "feedback-ingestion-actor.json"

    def mutate(payload):
        payload["program_events"][2]["actor_id"] = payload["signal"][
            "evidence_producer_id"
        ]
        _rehash_program_chain(payload)

    _copy_and_rewrite(source_package, path, mutate)
    with pytest.raises(ValueError, match="feedback ingestion actor"):
        EvolutionProgramPackageManager().load_file(path)


def test_package_rejects_rehashed_program_reason(source_package, tmp_path):
    path = tmp_path / "program-reason.json"

    def mutate(payload):
        payload["program_events"][6]["reason"] = "forged Campaign binding"
        _rehash_program_chain(payload)

    _copy_and_rewrite(source_package, path, mutate)
    with pytest.raises(ValueError, match="binding audit reason"):
        EvolutionProgramPackageManager().load_file(path)


def test_package_rejects_rehashed_program_actor_substitution(
    source_package,
    tmp_path,
):
    path = tmp_path / "program-actor.json"

    def mutate(payload):
        payload["program_events"][7]["actor_id"] = payload["attribution"][
            "attributor_id"
        ]
        _rehash_program_chain(payload)

    _copy_and_rewrite(source_package, path, mutate)
    with pytest.raises(ValueError, match="role separation"):
        EvolutionProgramPackageManager().load_file(path)


def test_package_rejects_rehashed_campaign_transition_payload(
    source_package,
    tmp_path,
):
    path = tmp_path / "campaign-transition.json"

    def mutate(payload):
        payload["campaign_events"][2]["payload"]["reason"] = (
            "forged evaluation transition"
        )
        _rehash_campaign_chain(payload)

    _copy_and_rewrite(source_package, path, mutate)
    with pytest.raises(ValueError, match="evaluation-start event"):
        EvolutionProgramPackageManager().load_file(path)


def test_package_rejects_rehashed_campaign_actor_substitution(
    source_package,
    tmp_path,
):
    path = tmp_path / "campaign-actor.json"

    def mutate(payload):
        payload["campaign_events"][-1]["actor_id"] = payload["signal"][
            "evidence_producer_id"
        ]
        _rehash_campaign_chain(payload)

    _copy_and_rewrite(source_package, path, mutate)
    with pytest.raises(ValueError, match="role separation"):
        EvolutionProgramPackageManager().load_file(path)


def test_package_rejects_rehashed_campaign_revision(source_package, tmp_path):
    path = tmp_path / "campaign-revision.json"

    def mutate(payload):
        payload["generation_campaign"]["revision"] -= 1

    _copy_and_rewrite(source_package, path, mutate)
    with pytest.raises(ValueError, match="revision differs"):
        EvolutionProgramPackageManager().load_file(path)


def test_package_rejects_reanchored_audit_tail_truncation(source_package, tmp_path):
    path = tmp_path / "tail.json"

    def mutate(payload):
        payload["program_events"] = payload["program_events"][:-1]
        payload["program_checkpoint"] = {
            "event_count": len(payload["program_events"]),
            "head_hash": payload["program_events"][-1]["event_hash"],
        }

    _copy_and_rewrite(source_package, path, mutate)
    with pytest.raises(ValueError, match="events are missing|lifecycle events"):
        EvolutionProgramPackageManager().load_file(path)
