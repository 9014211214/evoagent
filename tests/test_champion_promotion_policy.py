from datetime import datetime, timezone

from evoagent.benchmark_evidence import BenchmarkComparisonPackageManager
from evoagent.champion import (
    ChampionDecisionAction,
    ChampionPromotionGate,
    ChampionRoundStatus,
    ChampionStopRecommendation,
    build_champion_policy,
)
from evoagent.lab import AuthoritativeBenchmarkEvidenceLab


def benchmark_package(tmp_path, commit="a"):
    lab = AuthoritativeBenchmarkEvidenceLab(
        tmp_path / "benchmark",
        source_commit=commit * 40,
    )
    lab.run()
    return BenchmarkComparisonPackageManager().load_file(lab.package_path)


def evaluate(package, policy, *, decision_id="champion-decision:test"):
    return ChampionPromotionGate().evaluate(
        package,
        policy=policy,
        decision_id=decision_id,
        decision_actor_id="policy-evaluator",
        decided_at=datetime(2026, 8, 11, 10, 0, tzinfo=timezone.utc),
    )


def test_zero_regression_policy_selects_a1_instead_of_higher_scoring_a2(tmp_path):
    package = benchmark_package(tmp_path)
    decision = evaluate(package, build_champion_policy())
    assessments = {item.evolution_round: item for item in decision.assessments}

    assert decision.action == ChampionDecisionAction.PROMOTE
    assert decision.selected_run_id == "benchmark-run:a1"
    assert decision.selected_snapshot_id == "evoagent-a1"
    assert decision.selected_round == 1
    assert assessments[1].status == ChampionRoundStatus.ELIGIBLE
    assert assessments[1].score == 0.5
    assert assessments[1].regressed_tasks == 0
    assert assessments[2].score == 0.75
    assert assessments[2].status == ChampionRoundStatus.REJECTED
    assert assessments[2].regressed_tasks == 1
    assert "maximum_regressed_tasks_exceeded" in assessments[2].reasons
    assert decision.stop_recommendation == ChampionStopRecommendation.STOP
    assert decision.continue_evolution is False


def test_bootstrap_is_deterministic_and_bound_to_round_and_policy(tmp_path):
    package = benchmark_package(tmp_path, "b")
    policy = build_champion_policy(
        bootstrap_seed=123,
        bootstrap_resamples=1024,
    )
    first = evaluate(package, policy)
    second = evaluate(package, policy)

    assert first == second
    assert first.decision_hash == second.decision_hash
    assert first.assessments[0].bootstrap == second.assessments[0].bootstrap
    assert first.assessments[0].bootstrap.resamples == 1024
    assert first.assessments[0].bootstrap.seed == 123 + 1_000_003
    assert first.assessments[0].bootstrap.lower_bound <= 0.25
    assert first.assessments[0].bootstrap.upper_bound >= 0.25


def test_policy_can_disallow_non_final_champion_selection(tmp_path):
    package = benchmark_package(tmp_path, "c")
    policy = build_champion_policy(allow_non_final_round=False)
    decision = evaluate(package, policy)
    assessments = {item.evolution_round: item for item in decision.assessments}

    assert decision.action == ChampionDecisionAction.REJECT
    assert decision.selected_run_id is None
    assert "non_final_round_disallowed" in assessments[1].reasons
    assert "maximum_regressed_tasks_exceeded" in assessments[2].reasons


def test_required_same_model_comparator_holds_when_selected_round_lacks_one(tmp_path):
    package = benchmark_package(tmp_path, "d")
    policy = build_champion_policy(require_same_model_comparator=True)
    decision = evaluate(package, policy)
    assessments = {item.evolution_round: item for item in decision.assessments}

    assert assessments[1].status == ChampionRoundStatus.INSUFFICIENT_EVIDENCE
    assert "same_model_comparator_missing" in assessments[1].reasons
    assert assessments[2].comparator is not None
    assert assessments[2].status == ChampionRoundStatus.REJECTED
    assert decision.action == ChampionDecisionAction.HOLD
    assert decision.stop_recommendation == ChampionStopRecommendation.HOLD


def test_missing_usage_evidence_obeys_required_flag():
    gate = ChampionPromotionGate()
    comparison = gate._usage("input_tokens", None, None)

    hard_reasons = []
    insufficient_reasons = []
    gate._apply_usage_gate(
        comparison,
        required=True,
        maximum_growth=0.5,
        label="input_token",
        hard_reasons=hard_reasons,
        insufficient_reasons=insufficient_reasons,
    )
    assert hard_reasons == []
    assert insufficient_reasons == ["input_token_evidence_missing"]

    hard_reasons = []
    insufficient_reasons = []
    gate._apply_usage_gate(
        comparison,
        required=False,
        maximum_growth=0.5,
        label="input_token",
        hard_reasons=hard_reasons,
        insufficient_reasons=insufficient_reasons,
    )
    assert hard_reasons == []
    assert insufficient_reasons == []
