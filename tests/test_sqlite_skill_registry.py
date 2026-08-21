import sqlite3

import pytest

from evoagent.skills import (
    SQLiteSkillRegistry,
    SkillAuditIntegrityError,
    SkillEvaluationDecision,
    SkillRegistryConflictError,
    SkillSpec,
    SkillVersionStatus,
    StaleSkillRevision,
)


def base_skill():
    return SkillSpec(
        skill_id="decision_skill",
        name="Decision Skill",
        version="1.0.0",
        description="Handle safe cases.",
        rules=("accept_safe",),
    )


def candidate_skill():
    return SkillSpec(
        skill_id="decision_skill",
        name="Decision Skill",
        version="1.1.0",
        description="Handle safe and unsafe cases.",
        rules=("accept_safe", "reject_unsafe"),
        generated_by="test",
    )


def decision(*, promote=True, candidate="1.1.0", base="1.0.0"):
    return SkillEvaluationDecision(
        skill_id="decision_skill",
        base_version=base,
        candidate_version=candidate,
        promote=promote,
        base_score=0.5,
        candidate_score=1.0 if promote else 0.25,
        regression_count=0 if promote else 1,
        reason="held-out evaluation passed" if promote else "regression detected",
    )


def test_skill_lifecycle_survives_restart_and_rollback(tmp_path):
    path = tmp_path / "skills.db"
    registry = SQLiteSkillRegistry(path)
    registry.register_initial(base_skill())
    registry.add_candidate(candidate_skill(), parent_version="1.0.0", reason="verified Skill failure")
    registry.promote(
        "decision_skill",
        "1.1.0",
        decision(),
        expected_active_revision=0,
    )

    restarted = SQLiteSkillRegistry(path)
    assert restarted.active("decision_skill").spec.version == "1.1.0"
    assert restarted.active_revision("decision_skill") == 1
    assert restarted.get("decision_skill", "1.0.0").status == SkillVersionStatus.SUPERSEDED

    restarted.rollback(
        "decision_skill",
        "1.0.0",
        reason="canary regression",
        expected_active_revision=1,
    )
    again = SQLiteSkillRegistry(path)
    assert again.active("decision_skill").spec.version == "1.0.0"
    assert again.active_revision("decision_skill") == 2
    assert len(again.list_versions("decision_skill")) == 2
    assert again.verify_audit() is True


def test_duplicate_invalid_parent_and_stale_revision_are_rejected(tmp_path):
    registry = SQLiteSkillRegistry(tmp_path / "skills.db")
    registry.register_initial(base_skill())
    with pytest.raises(SkillRegistryConflictError):
        registry.register_initial(base_skill())
    with pytest.raises(ValueError):
        registry.add_candidate(candidate_skill(), parent_version="0.9.0", reason="invalid parent")

    registry.add_candidate(candidate_skill(), parent_version="1.0.0", reason="verified Skill failure")
    with pytest.raises(SkillRegistryConflictError):
        registry.add_candidate(candidate_skill(), parent_version="1.0.0", reason="duplicate")
    with pytest.raises(StaleSkillRevision):
        registry.promote(
            "decision_skill",
            "1.1.0",
            decision(),
            expected_active_revision=99,
        )


def test_rejection_preserves_active_history(tmp_path):
    registry = SQLiteSkillRegistry(tmp_path / "skills.db")
    registry.register_initial(base_skill())
    registry.add_candidate(candidate_skill(), parent_version="1.0.0", reason="proposal")
    registry.reject("decision_skill", "1.1.0", decision(promote=False))

    assert registry.active("decision_skill").spec.version == "1.0.0"
    assert registry.get("decision_skill", "1.1.0").status == SkillVersionStatus.REJECTED
    assert len(registry.events("decision_skill")) == 3


def test_skill_audit_tampering_and_checkpoint_truncation_are_detected(tmp_path):
    path = tmp_path / "skills.db"
    registry = SQLiteSkillRegistry(path)
    registry.register_initial(base_skill())
    registry.add_candidate(candidate_skill(), parent_version="1.0.0", reason="verified failure")
    checkpoint = registry.checkpoint()

    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE skill_audit_events SET reason = ? WHERE sequence = 1",
            ("tampered",),
        )
        connection.commit()
    with pytest.raises(SkillAuditIntegrityError):
        registry.verify_audit()

    second_path = tmp_path / "tail.db"
    second = SQLiteSkillRegistry(second_path)
    second.register_initial(base_skill())
    second.add_candidate(candidate_skill(), parent_version="1.0.0", reason="verified failure")
    second_checkpoint = second.checkpoint()
    with sqlite3.connect(second_path) as connection:
        connection.execute(
            "DELETE FROM skill_audit_events WHERE sequence = "
            "(SELECT MAX(sequence) FROM skill_audit_events)"
        )
        connection.commit()
    assert second.verify_audit() is True
    with pytest.raises(SkillAuditIntegrityError):
        second.verify_audit(second_checkpoint)
    assert checkpoint.event_count == 2


def test_persistent_registry_is_drop_in_for_evolution_cycle(tmp_path):
    from evoagent.cycles import (
        CycleStatus,
        EvolutionCycleRequest,
        EvolutionCycleService,
        StructuredVerifierSkillBackend,
    )
    from evoagent.diagnosis.synthetic import SyntheticCounterfactualRunner, SyntheticFaultScenario
    from evoagent.domain.models import ExecutionTrace, FailureLayer, Task
    from evoagent.traces import JsonlTraceStore, TraceTrustLevel

    database = tmp_path / "skills.db"
    registry = SQLiteSkillRegistry(database)
    registry.register_initial(base_skill())
    service = EvolutionCycleService(
        trace_store=JsonlTraceStore(tmp_path / "traces.jsonl"),
        skill_registry=registry,
        skill_backend=StructuredVerifierSkillBackend(),
    )
    result = service.process(
        EvolutionCycleRequest(
            trace=ExecutionTrace(
                trace_id="trace:skill:persistent",
                task=Task(task_id="task:skill:persistent", task_type="decision", input={}),
                model_id="public/model-v0",
                skill_id="decision_skill",
                skill_version="1.0.0",
                observable_events=[{"event": "synthetic_execution"}],
                final_output={"status": "failure"},
                verifier_passed=False,
                verifier_feedback="missing_skill_rule: reject_unsafe",
                cost={"llm_tokens": 10},
            ),
            source="synthetic-test",
            trust_level=TraceTrustLevel.VERIFIED,
        ),
        counterfactual_runner=SyntheticCounterfactualRunner(
            SyntheticFaultScenario(
                scenario_id="persistent-skill-fault",
                fault_layers={FailureLayer.SKILL},
            )
        ),
    )

    assert result.status == CycleStatus.SKILL_CANDIDATE
    restarted = SQLiteSkillRegistry(database)
    assert restarted.active("decision_skill").spec.version == "1.0.0"
    assert restarted.get("decision_skill", "1.1.0").status == SkillVersionStatus.CANDIDATE
