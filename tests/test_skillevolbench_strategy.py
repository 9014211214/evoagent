from evoagent.benchmarks import decide_skillevolbench_skill_action


def test_evaluation_roles_are_immutable():
    for role in ("context-shift", "adversarial", "composition"):
        decision = decide_skillevolbench_skill_action(
            role=role,
            verifier_passed=False,
            family_id="E1-LS1",
            family_has_seed=True,
            actually_used_skill_ids=("E1-LS1.skill",),
        )
        assert decision.action == "noop"
        assert decision.reason == "frozen_evaluation_block"


def test_canonical_first_encounter_induces_initial_skill():
    decision = decide_skillevolbench_skill_action(
        role="canonical",
        verifier_passed=True,
        family_id="E1-LS1",
        family_has_seed=False,
    )
    assert decision.action == "induce"
    assert decision.reason == "initial_family_skill_induction"


def test_passed_learning_case_does_not_revise_existing_skill():
    decision = decide_skillevolbench_skill_action(
        role="enriched",
        verifier_passed=True,
        family_id="E1-LS1",
        family_has_seed=True,
        actually_used_skill_ids=("E1-LS1.skill",),
    )
    assert decision.action == "noop"
    assert decision.reason == "no_bad_case_no_revision"


def test_failed_case_requires_unique_same_family_target():
    decision = decide_skillevolbench_skill_action(
        role="variant",
        verifier_passed=False,
        family_id="E1-LS1",
        family_has_seed=True,
        actually_used_skill_ids=(
            "E1-LS1.target",
            "E2-LS1.cross-environment",
        ),
        retrieved_skill_ids=("E1-LS1.other",),
    )
    assert decision.action == "revise"
    assert decision.target_skill_id == "E1-LS1.target"
    assert decision.reason == "unique_same_family_target:actually_used"


def test_ambiguous_used_evidence_fails_closed_without_falling_through():
    decision = decide_skillevolbench_skill_action(
        role="enriched",
        verifier_passed=False,
        family_id="E1-LS1",
        family_has_seed=True,
        actually_used_skill_ids=("E1-LS1.a", "E1-LS1.b"),
        retrieved_skill_ids=("E1-LS1.a",),
    )
    assert decision.action == "noop"
    assert decision.reason == "ambiguous_same_family_attribution:actually_used"


def test_retrieval_and_library_are_lower_priority_evidence_tiers():
    retrieval = decide_skillevolbench_skill_action(
        role="variant",
        verifier_passed=False,
        family_id="E3-LS2",
        family_has_seed=True,
        retrieved_skill_ids=("E3-LS2.only",),
        family_skill_ids=("E3-LS2.only", "E3-LS2.other"),
    )
    assert retrieval.action == "revise"
    assert retrieval.target_skill_id == "E3-LS2.only"
    assert retrieval.reason == "unique_same_family_target:retrieved"

    library = decide_skillevolbench_skill_action(
        role="variant",
        verifier_passed=False,
        family_id="E3-LS2",
        family_has_seed=True,
        family_skill_ids=("E3-LS2.only",),
    )
    assert library.action == "revise"
    assert library.target_skill_id == "E3-LS2.only"
    assert library.reason == "unique_same_family_target:family_library"


def test_missing_supported_target_is_noop():
    decision = decide_skillevolbench_skill_action(
        role="variant",
        verifier_passed=False,
        family_id="E4-LS4",
        family_has_seed=True,
        actually_used_skill_ids=("E5-LS1.cross",),
        retrieved_skill_ids=(),
        family_skill_ids=(),
    )
    assert decision.action == "noop"
    assert decision.reason == "no_supported_same_family_target"
