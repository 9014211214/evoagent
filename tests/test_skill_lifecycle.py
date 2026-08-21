import pytest

from evoagent.domain.models import EvaluationResult
from evoagent.skills import (
    SkillCandidateBuilder,
    SkillEventType,
    SkillPatch,
    SkillPromotionPolicy,
    SkillRegistry,
    SkillSpec,
    SkillVersionStatus,
    diff_skills,
)


def base_skill() -> SkillSpec:
    return SkillSpec(
        skill_id="decision_skill",
        name="Decision Skill",
        version="1.0.0",
        description="Decide safe cases.",
        rules=("accept_safe", "legacy_rule"),
        source_refs=("public:synthetic",),
    )


def eval_result(snapshot_id: str, outcomes: dict[str, bool]) -> EvaluationResult:
    passed = sum(outcomes.values())
    return EvaluationResult(
        snapshot_id=snapshot_id,
        total=len(outcomes),
        passed=passed,
        score=passed / len(outcomes),
        failed_task_ids=[key for key, value in outcomes.items() if not value],
        per_task=outcomes,
    )


def test_candidate_is_immutable_and_base_is_unchanged():
    base = base_skill()
    before = base.model_dump_json()
    candidate = SkillCandidateBuilder().propose(
        base,
        SkillPatch(
            add_rules=("reject_unsafe",),
            remove_rules=("legacy_rule",),
            evidence_trace_ids=("trace:1",),
        ),
        new_version="1.1.0",
    )

    assert base.model_dump_json() == before
    assert candidate.rules == ("accept_safe", "reject_unsafe")
    assert candidate.source_refs == ("public:synthetic", "trace:1")


def test_diff_reports_added_and_removed_rules():
    base = base_skill()
    candidate = SkillCandidateBuilder().propose(
        base,
        SkillPatch(add_rules=("reject_unsafe",), remove_rules=("legacy_rule",)),
        new_version="1.1.0",
    )
    diff = diff_skills(base, candidate)
    assert diff.added_rules == ("reject_unsafe",)
    assert diff.removed_rules == ("legacy_rule",)


def test_registry_rejects_duplicate_and_invalid_parent():
    registry = SkillRegistry()
    base = base_skill()
    registry.register_initial(base)
    with pytest.raises(ValueError):
        registry.register_initial(base)

    candidate = SkillCandidateBuilder().propose(
        base, SkillPatch(add_rules=("reject_unsafe",)), new_version="1.1.0"
    )
    with pytest.raises(ValueError):
        registry.add_candidate(candidate, parent_version="0.9.0", reason="bad parent")

    registry.add_candidate(candidate, parent_version="1.0.0", reason="verified bad case")
    with pytest.raises(ValueError):
        registry.add_candidate(candidate, parent_version="1.0.0", reason="duplicate")


def test_promotion_updates_active_pointer_and_preserves_history():
    registry = SkillRegistry()
    base = base_skill()
    candidate = SkillCandidateBuilder().propose(
        base, SkillPatch(add_rules=("reject_unsafe",)), new_version="1.1.0"
    )
    registry.register_initial(base)
    registry.add_candidate(candidate, parent_version="1.0.0", reason="verified bad case")

    decision = SkillPromotionPolicy().evaluate(
        base,
        candidate,
        eval_result("A0", {"safe": True, "unsafe": False}),
        eval_result("A1", {"safe": True, "unsafe": True}),
    )
    registry.promote(base.skill_id, candidate.version, decision)

    assert registry.active(base.skill_id).spec.version == "1.1.0"
    assert registry.get(base.skill_id, "1.0.0").status == SkillVersionStatus.SUPERSEDED
    assert len(registry.list_versions(base.skill_id)) == 2


def test_regressing_candidate_is_rejected_and_active_is_unchanged():
    registry = SkillRegistry()
    base = base_skill()
    candidate = SkillCandidateBuilder().propose(
        base, SkillPatch(remove_rules=("accept_safe",)), new_version="1.1.0"
    )
    registry.register_initial(base)
    registry.add_candidate(candidate, parent_version="1.0.0", reason="unsafe proposal")

    decision = SkillPromotionPolicy().evaluate(
        base,
        candidate,
        eval_result("A0", {"safe": True, "legacy": True}),
        eval_result("A1", {"safe": False, "legacy": True}),
    )
    assert decision.promote is False
    registry.reject(base.skill_id, candidate.version, decision)

    assert registry.active(base.skill_id).spec.version == "1.0.0"
    assert registry.get(base.skill_id, "1.1.0").status == SkillVersionStatus.REJECTED


def test_rollback_changes_pointer_without_deleting_newer_version():
    registry = SkillRegistry()
    base = base_skill()
    candidate = SkillCandidateBuilder().propose(
        base, SkillPatch(add_rules=("reject_unsafe",)), new_version="1.1.0"
    )
    registry.register_initial(base)
    registry.add_candidate(candidate, parent_version="1.0.0", reason="verified bad case")
    decision = SkillPromotionPolicy().evaluate(
        base,
        candidate,
        eval_result("A0", {"safe": True, "unsafe": False}),
        eval_result("A1", {"safe": True, "unsafe": True}),
    )
    registry.promote(base.skill_id, candidate.version, decision)
    registry.rollback(base.skill_id, "1.0.0", reason="canary regression")

    assert registry.active(base.skill_id).spec.version == "1.0.0"
    assert registry.get(base.skill_id, "1.1.0").status == SkillVersionStatus.SUPERSEDED
    assert {record.spec.version for record in registry.list_versions(base.skill_id)} == {
        "1.0.0",
        "1.1.0",
    }
    assert [event.event_type for event in registry.events(base.skill_id)] == [
        SkillEventType.REGISTERED,
        SkillEventType.CANDIDATE_CREATED,
        SkillEventType.PROMOTED,
        SkillEventType.ROLLED_BACK,
    ]
