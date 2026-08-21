from __future__ import annotations

from evoagent.benchmark_evidence.builders import (
    frozen_benchmark_contract_hash,
)
from evoagent.benchmark_evidence.models import (
    BenchmarkComparisonMode,
    BenchmarkRoundTaskDelta,
    BenchmarkRunEvidence,
    BenchmarkRunRole,
    BenchmarkSnapshotPoint,
    BenchmarkSubmissionEligibility,
    CrossAgentScore,
    HARBOR_REVIEWED_COMMIT,
    LongitudinalComparisonReport,
    PairwiseTaskComparison,
    SameModelCrossAgentReport,
    TERMINAL_BENCH_2_1,
    TERMINAL_BENCH_2_1_REVIEWED_COMMIT,
)
from evoagent.model_registry.models import canonical_sha256


class BenchmarkComparisonError(ValueError):
    pass


class BenchmarkComparator:
    def longitudinal(
        self,
        runs: tuple[BenchmarkRunEvidence, ...],
        *,
        comparison_id: str,
    ) -> LongitudinalComparisonReport:
        if len(runs) < 2:
            raise BenchmarkComparisonError(
                "Longitudinal comparison requires A0 and at least one evolved run."
            )
        ordered = tuple(
            sorted(runs, key=lambda item: item.contract.agent.evolution_round)
        )
        run_ids = [item.evidence_id for item in ordered]
        if len(set(run_ids)) != len(run_ids):
            raise BenchmarkComparisonError(
                "Longitudinal comparison contains duplicate run evidence IDs."
            )
        rounds = [item.contract.agent.evolution_round for item in ordered]
        if rounds != list(range(len(ordered))):
            raise BenchmarkComparisonError(
                "Longitudinal comparison rounds must be consecutive from zero."
            )
        baseline = ordered[0]
        if baseline.contract.role != BenchmarkRunRole.BASELINE:
            raise BenchmarkComparisonError(
                "Longitudinal comparison round zero must have the baseline role."
            )
        if any(
            item.contract.role != BenchmarkRunRole.EVOLVED
            for item in ordered[1:]
        ):
            raise BenchmarkComparisonError(
                "Longitudinal rounds after A0 must have the evolved role."
            )
        family_id = baseline.contract.agent.family_id
        if any(item.contract.agent.family_id != family_id for item in ordered):
            raise BenchmarkComparisonError(
                "Longitudinal comparison requires one Agent family."
            )
        for previous, current in zip(ordered, ordered[1:], strict=False):
            if (
                current.contract.agent.parent_snapshot_id
                != previous.contract.agent.snapshot_id
            ):
                raise BenchmarkComparisonError(
                    "Longitudinal Agent snapshot parent chain is broken."
                )
        frozen_hash = frozen_benchmark_contract_hash(baseline.contract)
        if any(
            frozen_benchmark_contract_hash(item.contract) != frozen_hash
            for item in ordered[1:]
        ):
            raise BenchmarkComparisonError(
                "Longitudinal comparison changed the frozen benchmark or Model contract."
            )
        if any(
            item.contract.model != baseline.contract.model
            or item.contract.suite != baseline.contract.suite
            for item in ordered[1:]
        ):
            raise BenchmarkComparisonError(
                "Longitudinal comparison requires the exact same Model and suite."
            )

        points = tuple(
            BenchmarkSnapshotPoint(
                run_id=item.evidence_id,
                snapshot_id=item.contract.agent.snapshot_id,
                evolution_round=item.contract.agent.evolution_round,
                score=item.score,
                gain_from_baseline=item.score - baseline.score,
                error_rate=item.error_rate,
                total_input_tokens=item.total_input_tokens,
                total_output_tokens=item.total_output_tokens,
                total_cost_usd=item.total_cost_usd,
            )
            for item in ordered
        )
        baseline_tasks = {
            item.task_name: item.score for item in baseline.task_aggregates
        }
        task_deltas = tuple(
            BenchmarkRoundTaskDelta(
                run_id=item.evidence_id,
                evolution_round=item.contract.agent.evolution_round,
                task_name=aggregate.task_name,
                baseline_score=baseline_tasks[aggregate.task_name],
                round_score=aggregate.score,
                delta=aggregate.score - baseline_tasks[aggregate.task_name],
            )
            for item in ordered[1:]
            for aggregate in item.task_aggregates
        )
        final = ordered[-1]
        final_deltas = [
            item
            for item in task_deltas
            if item.evolution_round == final.contract.agent.evolution_round
        ]
        improved = sum(item.delta > 1e-12 for item in final_deltas)
        regressed = sum(item.delta < -1e-12 for item in final_deltas)
        tied = len(final_deltas) - improved - regressed
        downward_round_count = sum(
            current.score < previous.score - 1e-12
            for previous, current in zip(ordered, ordered[1:], strict=False)
        )
        best = max(
            points,
            key=lambda item: (item.score, -item.evolution_round),
        )
        report_payload = {
            "comparison_id": comparison_id,
            "mode": BenchmarkComparisonMode.LONGITUDINAL,
            "run_ids": tuple(run_ids),
            "agent_family_id": family_id,
            "model_identity_hash": baseline.contract.model.identity_hash,
            "suite_hash": baseline.contract.suite.suite_hash,
            "frozen_contract_hash": frozen_hash,
            "points": points,
            "task_deltas": task_deltas,
            "best_round": best.evolution_round,
            "final_round": final.contract.agent.evolution_round,
            "baseline_score": baseline.score,
            "final_score": final.score,
            "final_gain": final.score - baseline.score,
            "improved_tasks": improved,
            "regressed_tasks": regressed,
            "tied_tasks": tied,
            "monotonic_score": downward_round_count == 0,
            "downward_round_count": downward_round_count,
            "error_rate_delta": final.error_rate - baseline.error_rate,
            "input_token_delta": _optional_int_delta(
                final.total_input_tokens,
                baseline.total_input_tokens,
            ),
            "output_token_delta": _optional_int_delta(
                final.total_output_tokens,
                baseline.total_output_tokens,
            ),
            "cost_delta_usd": _optional_float_delta(
                final.total_cost_usd,
                baseline.total_cost_usd,
            ),
        }
        return LongitudinalComparisonReport(
            **report_payload,
            report_hash=canonical_sha256(report_payload),
        )

    def same_model_cross_agent(
        self,
        runs: tuple[BenchmarkRunEvidence, ...],
        *,
        anchor_run_id: str,
        comparison_id: str,
    ) -> SameModelCrossAgentReport:
        if len(runs) < 2:
            raise BenchmarkComparisonError(
                "Same-model cross-Agent comparison requires at least two runs."
            )
        by_id = {item.evidence_id: item for item in runs}
        if len(by_id) != len(runs):
            raise BenchmarkComparisonError(
                "Same-model comparison contains duplicate run evidence IDs."
            )
        try:
            anchor = by_id[anchor_run_id]
        except KeyError as exc:
            raise BenchmarkComparisonError(
                "Same-model comparison anchor run is missing."
            ) from exc
        frozen_hash = frozen_benchmark_contract_hash(anchor.contract)
        for item in runs:
            if item.contract.model != anchor.contract.model:
                raise BenchmarkComparisonError(
                    "Same-model cross-Agent comparison rejected a Model identity mismatch."
                )
            if item.contract.suite != anchor.contract.suite:
                raise BenchmarkComparisonError(
                    "Same-model cross-Agent comparison requires the exact same suite."
                )
            if frozen_benchmark_contract_hash(item.contract) != frozen_hash:
                raise BenchmarkComparisonError(
                    "Same-model cross-Agent comparison changed a frozen execution setting."
                )
        agent_keys = {
            (
                item.contract.agent.family_id,
                item.contract.agent.name,
                item.contract.agent.version,
                item.contract.agent.snapshot_id,
            )
            for item in runs
        }
        if len(agent_keys) != len(runs):
            raise BenchmarkComparisonError(
                "Same-model comparison contains duplicate Agent identities."
            )
        ordered = tuple(
            sorted(
                runs,
                key=lambda item: (
                    -item.score,
                    item.error_rate,
                    item.contract.agent.name,
                    item.evidence_id,
                ),
            )
        )
        ranking = tuple(
            CrossAgentScore(
                rank=index,
                run_id=item.evidence_id,
                agent_name=item.contract.agent.name,
                agent_version=item.contract.agent.version,
                snapshot_id=item.contract.agent.snapshot_id,
                score=item.score,
                error_rate=item.error_rate,
                total_input_tokens=item.total_input_tokens,
                total_output_tokens=item.total_output_tokens,
                total_cost_usd=item.total_cost_usd,
            )
            for index, item in enumerate(ordered, start=1)
        )
        anchor_tasks = {
            item.task_name: item.score for item in anchor.task_aggregates
        }
        pairwise: list[PairwiseTaskComparison] = []
        for comparator in sorted(
            (item for item in runs if item.evidence_id != anchor_run_id),
            key=lambda item: item.evidence_id,
        ):
            comparator_tasks = {
                item.task_name: item.score
                for item in comparator.task_aggregates
            }
            deltas = [
                anchor_tasks[name] - comparator_tasks[name]
                for name in sorted(anchor_tasks)
            ]
            pairwise.append(
                PairwiseTaskComparison(
                    comparator_run_id=comparator.evidence_id,
                    wins=sum(value > 1e-12 for value in deltas),
                    losses=sum(value < -1e-12 for value in deltas),
                    ties=sum(abs(value) <= 1e-12 for value in deltas),
                    score_delta=anchor.score - comparator.score,
                    error_rate_delta=(
                        anchor.error_rate - comparator.error_rate
                    ),
                    input_token_delta=_optional_int_delta(
                        anchor.total_input_tokens,
                        comparator.total_input_tokens,
                    ),
                    output_token_delta=_optional_int_delta(
                        anchor.total_output_tokens,
                        comparator.total_output_tokens,
                    ),
                    cost_delta_usd=_optional_float_delta(
                        anchor.total_cost_usd,
                        comparator.total_cost_usd,
                    ),
                )
            )
        report_payload = {
            "comparison_id": comparison_id,
            "mode": BenchmarkComparisonMode.SAME_MODEL_CROSS_AGENT,
            "anchor_run_id": anchor_run_id,
            "run_ids": tuple(sorted(by_id)),
            "model_identity_hash": anchor.contract.model.identity_hash,
            "suite_hash": anchor.contract.suite.suite_hash,
            "frozen_contract_hash": frozen_hash,
            "same_model_verified": True,
            "ranking": ranking,
            "pairwise": tuple(pairwise),
        }
        return SameModelCrossAgentReport(
            **report_payload,
            report_hash=canonical_sha256(report_payload),
        )


def assess_submission_eligibility(
    run: BenchmarkRunEvidence,
) -> BenchmarkSubmissionEligibility:
    contract = run.contract
    exact_pinned_suite = (
        contract.suite.dataset_ref == TERMINAL_BENCH_2_1
        and contract.suite.harbor_reviewed_commit == HARBOR_REVIEWED_COMMIT
        and contract.suite.benchmark_reviewed_commit
        == TERMINAL_BENCH_2_1_REVIEWED_COMMIT
    )
    default_settings = (
        contract.default_execution_settings_attested
        and abs(contract.timeout_multiplier - 1.0) <= 1e-12
        and not contract.agent_timeout_override
        and not contract.verifier_timeout_override
        and not contract.resource_overrides
    )
    complete_coverage = (
        len(run.task_aggregates) == len(contract.suite.tasks)
        and run.n_total_trials
        == len(contract.suite.tasks) * contract.trials_per_task
    )
    minimum_trials = contract.trials_per_task >= 5
    public_uploaded = (
        contract.upload
        and contract.public
        and contract.harbor_hub_job_uri is not None
    )
    trajectories = contract.trajectories_available
    synthetic = contract.source.value == "synthetic_fixture"
    checks = (
        (exact_pinned_suite, "pinned_suite_not_verified"),
        (
            contract.suite.canonical_task_manifest_attested,
            "canonical_task_manifest_not_attested",
        ),
        (default_settings, "default_execution_settings_not_attested"),
        (complete_coverage, "task_coverage_incomplete"),
        (minimum_trials, "fewer_than_five_trials_per_task"),
        (public_uploaded, "public_uploaded_harbor_job_missing"),
        (trajectories, "reviewable_trajectories_missing"),
        (not synthetic, "synthetic_fixture_is_not_submission_evidence"),
    )
    reasons = tuple(reason for passed, reason in checks if not passed)
    payload = {
        "evidence_id": run.evidence_id,
        "exact_pinned_suite": exact_pinned_suite,
        "canonical_task_manifest_attested": (
            contract.suite.canonical_task_manifest_attested
        ),
        "default_execution_settings": default_settings,
        "complete_task_coverage": complete_coverage,
        "minimum_trials_per_task_met": minimum_trials,
        "public_uploaded_job": public_uploaded,
        "trajectories_available": trajectories,
        "synthetic_fixture": synthetic,
        "submission_prerequisites_met": not reasons,
        "reasons": reasons,
        "official_submission_performed": False,
        "official_submission_accepted": False,
    }
    return BenchmarkSubmissionEligibility(
        **payload,
        assessment_hash=canonical_sha256(payload),
    )


def _optional_int_delta(
    left: int | None,
    right: int | None,
) -> int | None:
    if left is None or right is None:
        return None
    return left - right


def _optional_float_delta(
    left: float | None,
    right: float | None,
) -> float | None:
    if left is None or right is None:
        return None
    return left - right


__all__ = [
    "BenchmarkComparator",
    "BenchmarkComparisonError",
    "assess_submission_eligibility",
]
