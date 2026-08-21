from __future__ import annotations

import random
from datetime import datetime

from evoagent.benchmark_evidence.models import (
    BenchmarkRunEvidence,
    SameModelCrossAgentReport,
)
from evoagent.benchmark_evidence.package import (
    BenchmarkComparisonPackageManager,
    BenchmarkComparisonPackageManifest,
)
from evoagent.champion.models import (
    ChampionBootstrapEvidence,
    ChampionComparatorEvidence,
    ChampionDecisionAction,
    ChampionPromotionPolicy,
    ChampionRoundAssessment,
    ChampionRoundStatus,
    ChampionSelectionDecision,
    ChampionStopRecommendation,
    ChampionTaskDelta,
    ChampionUsageComparison,
)
from evoagent.model_registry.models import canonical_sha256


class ChampionPromotionGate:
    """Derive a deterministic best-admissible Challenger from v1.7 evidence."""

    def evaluate(
        self,
        package: BenchmarkComparisonPackageManifest,
        *,
        policy: ChampionPromotionPolicy,
        decision_id: str,
        decision_actor_id: str,
        decided_at: datetime,
        comparator_reports: dict[str, SameModelCrossAgentReport] | None = None,
    ) -> ChampionSelectionDecision:
        BenchmarkComparisonPackageManager().verify(package)
        comparator_reports = comparator_reports or {}
        by_id = {item.evidence_id: item for item in package.runs}
        longitudinal = package.longitudinal
        try:
            ordered = tuple(by_id[run_id] for run_id in longitudinal.run_ids)
        except KeyError as exc:
            raise ValueError("Champion gate longitudinal evidence is incomplete.") from exc
        baseline = ordered[0]
        final_round = ordered[-1].contract.agent.evolution_round
        assessments = tuple(
            self._assess_round(
                baseline=baseline,
                candidate=candidate,
                policy=policy,
                final_round=final_round,
                comparator_report=(
                    comparator_reports.get(candidate.evidence_id)
                    or (
                        package.same_model_cross_agent
                        if package.same_model_cross_agent.anchor_run_id
                        == candidate.evidence_id
                        else None
                    )
                ),
            )
            for candidate in ordered[1:]
        )
        eligible = [
            item for item in assessments if item.status == ChampionRoundStatus.ELIGIBLE
        ]
        if eligible:
            selected = max(eligible, key=self._selection_key)
            action = ChampionDecisionAction.PROMOTE
            later = [
                item
                for item in assessments
                if item.evolution_round > selected.evolution_round
            ]
            stop = (
                ChampionStopRecommendation.STOP
                if (
                    any(item.status == ChampionRoundStatus.REJECTED for item in later)
                    or (
                        selected.evolution_round < final_round
                        and len(later) >= policy.patience_rounds
                    )
                )
                else ChampionStopRecommendation.CONTINUE
            )
            reason = (
                f"Selected round {selected.evolution_round} as the highest-scoring "
                "eligible Challenger under immutable regression, confidence, error, "
                "and resource gates."
            )
            selected_values = (
                selected.run_id,
                selected.snapshot_id,
                selected.evolution_round,
            )
        else:
            has_insufficient = any(
                item.status == ChampionRoundStatus.INSUFFICIENT_EVIDENCE
                for item in assessments
            )
            action = (
                ChampionDecisionAction.HOLD
                if has_insufficient
                else ChampionDecisionAction.REJECT
            )
            stop = (
                ChampionStopRecommendation.HOLD
                if has_insufficient
                else ChampionStopRecommendation.STOP
            )
            reason = (
                "No evolved round has sufficient evidence for promotion."
                if has_insufficient
                else "Every evolved round violated at least one hard promotion gate."
            )
            selected_values = (None, None, None)

        payload = {
            "decision_id": decision_id,
            "benchmark_package_hash": package.package_hash,
            "longitudinal_report_hash": longitudinal.report_hash,
            "policy": policy,
            "baseline_run_id": baseline.evidence_id,
            "baseline_snapshot_id": baseline.contract.agent.snapshot_id,
            "assessments": assessments,
            "selected_run_id": selected_values[0],
            "selected_snapshot_id": selected_values[1],
            "selected_round": selected_values[2],
            "action": action,
            "stop_recommendation": stop,
            "continue_evolution": stop == ChampionStopRecommendation.CONTINUE,
            "reason": reason,
            "decision_actor_id": decision_actor_id,
            "decided_at": decided_at,
        }
        return ChampionSelectionDecision(
            **payload,
            decision_hash=canonical_sha256(payload),
        )

    def _assess_round(
        self,
        *,
        baseline: BenchmarkRunEvidence,
        candidate: BenchmarkRunEvidence,
        policy: ChampionPromotionPolicy,
        final_round: int,
        comparator_report: SameModelCrossAgentReport | None,
    ) -> ChampionRoundAssessment:
        baseline_tasks = {
            item.task_name: item.score for item in baseline.task_aggregates
        }
        candidate_tasks = {
            item.task_name: item.score for item in candidate.task_aggregates
        }
        if set(candidate_tasks) != set(baseline_tasks):
            raise ValueError("Champion candidate changed the frozen Task manifest.")
        task_deltas = tuple(
            self._task_delta(
                name,
                baseline_tasks[name],
                candidate_tasks[name],
            )
            for name in sorted(baseline_tasks)
        )
        deltas = tuple(item.delta for item in task_deltas)
        gain = sum(deltas) / len(deltas)
        bootstrap = self._bootstrap(
            deltas,
            policy=policy,
            evolution_round=candidate.contract.agent.evolution_round,
        )
        improved = sum(value > 1e-12 for value in deltas)
        regressed = sum(value < -1e-12 for value in deltas)
        tied = len(deltas) - improved - regressed
        input_usage = self._usage(
            "input_tokens",
            baseline.total_input_tokens,
            candidate.total_input_tokens,
        )
        output_usage = self._usage(
            "output_tokens",
            baseline.total_output_tokens,
            candidate.total_output_tokens,
        )
        cost_usage = self._usage(
            "cost_usd",
            baseline.total_cost_usd,
            candidate.total_cost_usd,
        )
        comparator = self._comparator_evidence(
            candidate.evidence_id,
            comparator_report,
        )

        hard_reasons: list[str] = []
        insufficient_reasons: list[str] = []
        if gain < policy.minimum_score_gain - 1e-12:
            hard_reasons.append("minimum_score_gain_not_met")
        if bootstrap.lower_bound < policy.minimum_gain_lower_bound - 1e-12:
            insufficient_reasons.append("bootstrap_lower_bound_not_met")
        if regressed > policy.maximum_regressed_tasks:
            hard_reasons.append("maximum_regressed_tasks_exceeded")
        regression_fraction = regressed / len(deltas)
        if regression_fraction > policy.maximum_regression_fraction + 1e-12:
            hard_reasons.append("maximum_regression_fraction_exceeded")
        error_delta = candidate.error_rate - baseline.error_rate
        if error_delta > policy.maximum_error_rate_delta + 1e-12:
            hard_reasons.append("maximum_error_rate_delta_exceeded")
        self._apply_usage_gate(
            input_usage,
            required=policy.require_token_evidence,
            maximum_growth=policy.maximum_input_token_growth_ratio,
            label="input_token",
            hard_reasons=hard_reasons,
            insufficient_reasons=insufficient_reasons,
        )
        self._apply_usage_gate(
            output_usage,
            required=policy.require_token_evidence,
            maximum_growth=policy.maximum_output_token_growth_ratio,
            label="output_token",
            hard_reasons=hard_reasons,
            insufficient_reasons=insufficient_reasons,
        )
        self._apply_usage_gate(
            cost_usage,
            required=policy.require_cost_evidence,
            maximum_growth=policy.maximum_cost_growth_ratio,
            label="cost",
            hard_reasons=hard_reasons,
            insufficient_reasons=insufficient_reasons,
        )
        if (
            not policy.allow_non_final_round
            and candidate.contract.agent.evolution_round != final_round
        ):
            hard_reasons.append("non_final_round_disallowed")
        if policy.require_same_model_comparator:
            if comparator is None:
                insufficient_reasons.append("same_model_comparator_missing")
            else:
                if comparator.anchor_rank > policy.maximum_anchor_rank:
                    hard_reasons.append("maximum_anchor_rank_exceeded")
                if (
                    comparator.score_delta
                    < policy.minimum_pairwise_score_delta - 1e-12
                ):
                    hard_reasons.append("minimum_pairwise_score_delta_not_met")
                if comparator.losses > policy.maximum_pairwise_losses:
                    hard_reasons.append("maximum_pairwise_losses_exceeded")

        if hard_reasons:
            status = ChampionRoundStatus.REJECTED
            reasons = tuple(dict.fromkeys(hard_reasons + insufficient_reasons))
        elif insufficient_reasons:
            status = ChampionRoundStatus.INSUFFICIENT_EVIDENCE
            reasons = tuple(dict.fromkeys(insufficient_reasons))
        else:
            status = ChampionRoundStatus.ELIGIBLE
            reasons = ()

        payload = {
            "run_id": candidate.evidence_id,
            "snapshot_id": candidate.contract.agent.snapshot_id,
            "evolution_round": candidate.contract.agent.evolution_round,
            "score": candidate.score,
            "gain": gain,
            "task_deltas": task_deltas,
            "improved_tasks": improved,
            "regressed_tasks": regressed,
            "tied_tasks": tied,
            "regression_fraction": regression_fraction,
            "error_rate_delta": error_delta,
            "input_tokens": input_usage,
            "output_tokens": output_usage,
            "cost": cost_usage,
            "bootstrap": bootstrap,
            "comparator": comparator,
            "status": status,
            "reasons": reasons,
        }
        return ChampionRoundAssessment(
            **payload,
            assessment_hash=canonical_sha256(payload),
        )

    @staticmethod
    def _task_delta(
        task_name: str,
        baseline_score: float,
        candidate_score: float,
    ) -> ChampionTaskDelta:
        payload = {
            "task_name": task_name,
            "baseline_score": baseline_score,
            "candidate_score": candidate_score,
            "delta": candidate_score - baseline_score,
        }
        return ChampionTaskDelta(
            **payload,
            delta_hash=canonical_sha256(payload),
        )

    @staticmethod
    def _bootstrap(
        deltas: tuple[float, ...],
        *,
        policy: ChampionPromotionPolicy,
        evolution_round: int,
    ) -> ChampionBootstrapEvidence:
        if not deltas:
            raise ValueError("Champion bootstrap requires Task deltas.")
        seed = policy.bootstrap_seed + evolution_round * 1_000_003
        rng = random.Random(seed)
        count = len(deltas)
        sample_means = [
            sum(deltas[rng.randrange(count)] for _ in range(count)) / count
            for _ in range(policy.bootstrap_resamples)
        ]
        sample_means.sort()
        alpha = (1.0 - policy.bootstrap_confidence) / 2.0
        lower_index = int(alpha * (len(sample_means) - 1))
        upper_index = int((1.0 - alpha) * (len(sample_means) - 1))
        payload = {
            "confidence_level": policy.bootstrap_confidence,
            "resamples": policy.bootstrap_resamples,
            "seed": seed,
            "observed_mean": sum(deltas) / count,
            "lower_bound": sample_means[lower_index],
            "upper_bound": sample_means[upper_index],
            "sample_means_hash": canonical_sha256(sample_means),
        }
        return ChampionBootstrapEvidence(
            **payload,
            evidence_hash=canonical_sha256(payload),
        )

    @staticmethod
    def _usage(
        metric: str,
        baseline: int | float | None,
        candidate: int | float | None,
    ) -> ChampionUsageComparison:
        complete = baseline is not None and candidate is not None
        if complete:
            baseline_value = float(baseline)
            candidate_value = float(candidate)
            unbounded = baseline_value == 0.0 and candidate_value > 0.0
            ratio = (
                None
                if unbounded
                else (
                    0.0
                    if baseline_value == 0.0
                    else candidate_value / baseline_value - 1.0
                )
            )
        else:
            baseline_value = None
            candidate_value = None
            unbounded = False
            ratio = None
        payload = {
            "metric": metric,
            "baseline_value": baseline_value,
            "candidate_value": candidate_value,
            "evidence_complete": complete,
            "unbounded_growth": unbounded,
            "growth_ratio": ratio,
        }
        return ChampionUsageComparison(
            **payload,
            comparison_hash=canonical_sha256(payload),
        )

    @staticmethod
    def _apply_usage_gate(
        comparison: ChampionUsageComparison,
        *,
        required: bool,
        maximum_growth: float,
        label: str,
        hard_reasons: list[str],
        insufficient_reasons: list[str],
    ) -> None:
        if not comparison.evidence_complete:
            if required:
                insufficient_reasons.append(f"{label}_evidence_missing")
            return
        if comparison.unbounded_growth:
            hard_reasons.append(f"{label}_growth_unbounded")
        elif (
            comparison.growth_ratio is not None
            and comparison.growth_ratio > maximum_growth + 1e-12
        ):
            hard_reasons.append(f"{label}_growth_exceeded")

    @staticmethod
    def _comparator_evidence(
        run_id: str,
        report: SameModelCrossAgentReport | None,
    ) -> ChampionComparatorEvidence | None:
        if report is None:
            return None
        if report.anchor_run_id != run_id:
            raise ValueError("Champion comparator report is bound to another anchor run.")
        ranking = {item.run_id: item for item in report.ranking}
        anchor = ranking[run_id]
        if len(report.pairwise) != 1:
            raise ValueError(
                "Champion gate currently requires exactly one same-model comparator."
            )
        pair = report.pairwise[0]
        payload = {
            "report_hash": report.report_hash,
            "anchor_run_id": run_id,
            "anchor_rank": anchor.rank,
            "comparator_run_id": pair.comparator_run_id,
            "score_delta": pair.score_delta,
            "wins": pair.wins,
            "losses": pair.losses,
            "ties": pair.ties,
        }
        return ChampionComparatorEvidence(
            **payload,
            evidence_hash=canonical_sha256(payload),
        )

    @staticmethod
    def _selection_key(item: ChampionRoundAssessment):
        cost_growth = (
            item.cost.growth_ratio
            if item.cost.growth_ratio is not None
            else float("inf")
        )
        return (
            item.score,
            item.bootstrap.lower_bound,
            -item.regressed_tasks,
            -cost_growth,
            -item.evolution_round,
        )


def build_champion_policy(
    *,
    policy_id: str = "champion-policy:zero-regression-v1",
    minimum_score_gain: float = 0.10,
    bootstrap_confidence: float = 0.80,
    bootstrap_resamples: int = 4096,
    bootstrap_seed: int = 17,
    minimum_gain_lower_bound: float = 0.0,
    maximum_regressed_tasks: int = 0,
    maximum_regression_fraction: float = 0.0,
    maximum_error_rate_delta: float = 0.0,
    maximum_input_token_growth_ratio: float = 0.50,
    maximum_output_token_growth_ratio: float = 0.50,
    maximum_cost_growth_ratio: float = 0.50,
    require_token_evidence: bool = True,
    require_cost_evidence: bool = True,
    allow_non_final_round: bool = True,
    patience_rounds: int = 1,
    require_same_model_comparator: bool = False,
    maximum_anchor_rank: int = 2,
    minimum_pairwise_score_delta: float = -1.0,
    maximum_pairwise_losses: int = 1_000_000,
) -> ChampionPromotionPolicy:
    payload = {
        "policy_id": policy_id,
        "minimum_score_gain": minimum_score_gain,
        "bootstrap_confidence": bootstrap_confidence,
        "bootstrap_resamples": bootstrap_resamples,
        "bootstrap_seed": bootstrap_seed,
        "minimum_gain_lower_bound": minimum_gain_lower_bound,
        "maximum_regressed_tasks": maximum_regressed_tasks,
        "maximum_regression_fraction": maximum_regression_fraction,
        "maximum_error_rate_delta": maximum_error_rate_delta,
        "maximum_input_token_growth_ratio": maximum_input_token_growth_ratio,
        "maximum_output_token_growth_ratio": maximum_output_token_growth_ratio,
        "maximum_cost_growth_ratio": maximum_cost_growth_ratio,
        "require_token_evidence": require_token_evidence,
        "require_cost_evidence": require_cost_evidence,
        "allow_non_final_round": allow_non_final_round,
        "patience_rounds": patience_rounds,
        "require_same_model_comparator": require_same_model_comparator,
        "maximum_anchor_rank": maximum_anchor_rank,
        "minimum_pairwise_score_delta": minimum_pairwise_score_delta,
        "maximum_pairwise_losses": maximum_pairwise_losses,
    }
    return ChampionPromotionPolicy(
        **payload,
        policy_hash=canonical_sha256(payload),
    )


__all__ = [
    "ChampionPromotionGate",
    "build_champion_policy",
]
