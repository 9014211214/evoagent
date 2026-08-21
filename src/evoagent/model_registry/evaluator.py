from __future__ import annotations

from pathlib import Path

from evoagent.benchmarks.models import ResourceBudget, ResourceUsage
from evoagent.domain.models import AgentSnapshot, Task
from evoagent.model_registry.adapters import (
    ModelCandidateAdapter,
    ModelEvaluationError,
    RetentionAwareBasePolicy,
    evaluation_skill,
)
from evoagent.model_registry.models import (
    ExternalModelCandidateManifest,
    ModelArtifactFormat,
    ModelCandidateEvaluationReport,
    ModelEvaluationSuite,
    ModelTaskEvaluation,
    canonical_sha256,
)
from evoagent.runtime import (
    DocumentTaskVerifier,
    LocalDocumentEnvironment,
    RuntimeLimits,
    TaskVerifier,
    ToolAgentPolicy,
    ToolAgentRuntime,
    VerificationContext,
    VerificationResult,
    snapshot_from_skill_spec,
)


class AdmissionVerifier(TaskVerifier):
    def __init__(self):
        self.document_verifier = DocumentTaskVerifier()

    def verify(
        self,
        task: Task,
        context: VerificationContext,
    ) -> VerificationResult:
        if task.task_type == "model-retention":
            expected_capability = task.input.get("capability", "baseline")
            passed = (
                context.limit_exceeded is None
                and context.final_output.get("status") == "retained"
                and context.final_output.get("capability") == expected_capability
            )
            return VerificationResult(
                passed=passed,
                score=1.0 if passed else 0.0,
                feedback="retention_verified" if passed else "retention_regression",
                evidence=(
                    f"expected_capability={expected_capability}",
                    f"actual_status={context.final_output.get('status')}",
                    f"actual_capability={context.final_output.get('capability')}",
                ),
            )
        return self.document_verifier.verify(task, context)


class IndependentModelCandidateEvaluator:
    """Compare a base policy and explicit candidate adapter on frozen Tasks."""

    def __init__(
        self,
        root: str | Path,
        *,
        limits: RuntimeLimits | None = None,
        seed: int = 67,
    ):
        raw_root = Path(root).expanduser()
        if raw_root.is_symlink():
            raise ModelEvaluationError("Evaluation root must not be a symlink.")
        self.root = raw_root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.limits = limits or RuntimeLimits(
            max_steps=12,
            max_tool_calls=10,
            max_wall_seconds=10.0,
        )
        self.seed = seed

    def evaluate(
        self,
        *,
        candidate: ExternalModelCandidateManifest,
        adapter: ModelCandidateAdapter,
        suite: ModelEvaluationSuite,
        evaluator_id: str,
        trainer_id: str,
        budget: ResourceBudget,
    ) -> ModelCandidateEvaluationReport:
        if evaluator_id == trainer_id:
            raise ModelEvaluationError(
                "Independent evaluation requires evaluator and trainer identities to differ."
            )
        self._validate_adapter(candidate, adapter)
        held_out_ids = tuple(task.task_id for task in suite.held_out_tasks)
        if held_out_ids != candidate.held_out_task_ids:
            raise ModelEvaluationError(
                "Evaluation held-out Tasks differ from the candidate manifest."
            )

        base_snapshot = snapshot_from_skill_spec(
            evaluation_skill(safe=True),
            snapshot_id=f"model-eval:base:{suite.suite_id}",
            round_index=0,
            model_id=candidate.base_model_id,
        )
        candidate_snapshot = snapshot_from_skill_spec(
            adapter.build_skill(),
            snapshot_id=f"model-eval:candidate:{suite.suite_id}",
            round_index=0,
            model_id=candidate.candidate_id,
        )

        results: list[ModelTaskEvaluation] = []
        for suite_name, tasks in (
            ("held_out", suite.held_out_tasks),
            ("replay", suite.replay_tasks),
            ("retention", suite.retention_tasks),
            ("safety", suite.safety_tasks),
        ):
            for task in tasks:
                results.append(
                    self._evaluate_task(
                        task=task,
                        suite_name=suite_name,
                        base_policy=RetentionAwareBasePolicy(),
                        candidate_policy=adapter.build_policy(),
                        base_snapshot=base_snapshot,
                        candidate_snapshot=candidate_snapshot,
                    )
                )

        held_out = [item for item in results if item.suite == "held_out"]
        replay = [item for item in results if item.suite == "replay"]
        retention = [item for item in results if item.suite == "retention"]
        safety = [item for item in results if item.suite == "safety"]
        base_usage = _sum_usage(item.base_usage for item in results)
        candidate_usage = _sum_usage(item.candidate_usage for item in results)
        base_pass_count = sum(item.base_passed for item in results)
        regression_count = sum(
            item.base_passed and not item.candidate_passed for item in results
        )
        safety_violations = sum(
            len(item.candidate_safety_violations) for item in results
        )
        held_out_base_score = _score(item.base_passed for item in held_out)
        held_out_candidate_score = _score(
            item.candidate_passed for item in held_out
        )
        payload = {
            "report_id": f"model-evaluation:{candidate.candidate_id}:{suite.suite_id}",
            "family_id": candidate.family_id,
            "base_model_id": candidate.base_model_id,
            "candidate_id": candidate.candidate_id,
            "candidate_manifest_hash": candidate.manifest_hash,
            "adapter_id": adapter.adapter_id,
            "adapter_hash": adapter.adapter_hash,
            "evaluator_id": evaluator_id,
            "trainer_id": trainer_id,
            "suite_hash": suite.suite_hash,
            "budget": budget,
            "task_results": tuple(results),
            "held_out_base_score": held_out_base_score,
            "held_out_candidate_score": held_out_candidate_score,
            "held_out_improvement": held_out_candidate_score
            - held_out_base_score,
            "replay_candidate_score": _score(
                item.candidate_passed for item in replay
            ),
            "retention_candidate_score": _score(
                item.candidate_passed for item in retention
            ),
            "safety_candidate_score": _score(
                item.candidate_passed for item in safety
            ),
            "regression_count": regression_count,
            "forgetting_rate": (
                regression_count / base_pass_count if base_pass_count else 0.0
            ),
            "safety_violation_count": safety_violations,
            "base_usage": base_usage,
            "candidate_usage": candidate_usage,
            "tool_call_delta": candidate_usage.tool_calls - base_usage.tool_calls,
            "token_delta": candidate_usage.tokens - base_usage.tokens,
            "cost_delta_usd": candidate_usage.cost_usd - base_usage.cost_usd,
            "candidate_budget_ok": candidate_usage.fits(budget),
        }
        return ModelCandidateEvaluationReport(
            **payload,
            report_hash=canonical_sha256(payload),
        )

    @staticmethod
    def _validate_adapter(
        candidate: ExternalModelCandidateManifest,
        adapter: ModelCandidateAdapter,
    ) -> None:
        if adapter.candidate_id != candidate.candidate_id:
            raise ModelEvaluationError("Candidate adapter model ID mismatch.")
        if adapter.candidate_manifest_hash != candidate.manifest_hash:
            raise ModelEvaluationError("Candidate adapter manifest hash mismatch.")
        if adapter.generated_by != candidate.generated_by:
            raise ModelEvaluationError("Candidate adapter generator mismatch.")
        if candidate.artifact_format == ModelArtifactFormat.SYNTHETIC_POLICY:
            if not adapter.synthetic:
                raise ModelEvaluationError(
                    "Synthetic candidate requires an explicitly synthetic adapter."
                )
        elif adapter.synthetic:
            raise ModelEvaluationError(
                "A synthetic adapter cannot evaluate a real checkpoint manifest."
            )
        expected_hash = canonical_sha256(
            {
                "adapter_id": adapter.adapter_id,
                "candidate_id": adapter.candidate_id,
                "candidate_manifest_hash": adapter.candidate_manifest_hash,
                "profile": (
                    candidate.synthetic_profile.value
                    if candidate.synthetic_profile is not None
                    else None
                ),
                "synthetic": adapter.synthetic,
            }
        )
        if adapter.adapter_hash != expected_hash:
            raise ModelEvaluationError("Candidate adapter hash mismatch.")

    def _evaluate_task(
        self,
        *,
        task: Task,
        suite_name: str,
        base_policy: ToolAgentPolicy,
        candidate_policy: ToolAgentPolicy,
        base_snapshot: AgentSnapshot,
        candidate_snapshot: AgentSnapshot,
    ) -> ModelTaskEvaluation:
        frozen_hash = canonical_sha256(task.model_dump(mode="json"))
        base_trace = self._run(
            task=task,
            snapshot=base_snapshot,
            policy=base_policy,
            role="base",
        )
        candidate_trace = self._run(
            task=task,
            snapshot=candidate_snapshot,
            policy=candidate_policy,
            role="candidate",
        )
        if canonical_sha256(task.model_dump(mode="json")) != frozen_hash:
            raise ModelEvaluationError("Evaluation mutated a frozen suite Task.")
        if base_trace.task != task or candidate_trace.task != task:
            raise ModelEvaluationError(
                "A policy or Runtime mutated the Task used for evaluation."
            )
        if base_trace.model_id != base_snapshot.model_id:
            raise ModelEvaluationError("Base trace model binding changed.")
        if candidate_trace.model_id != candidate_snapshot.model_id:
            raise ModelEvaluationError("Candidate trace model binding changed.")

        payload = {
            "task_id": task.task_id,
            "suite": suite_name,
            "task_hash": frozen_hash,
            "base_trace_id": base_trace.trace_id,
            "candidate_trace_id": candidate_trace.trace_id,
            "base_passed": base_trace.verifier_passed,
            "candidate_passed": candidate_trace.verifier_passed,
            "base_final_output": base_trace.final_output,
            "candidate_final_output": candidate_trace.final_output,
            "base_usage": _usage(base_trace.cost),
            "candidate_usage": _usage(candidate_trace.cost),
            "candidate_safety_violations": _safety_violations(candidate_trace),
        }
        return ModelTaskEvaluation(
            **payload,
            result_hash=canonical_sha256(payload),
        )

    def _run(
        self,
        *,
        task: Task,
        snapshot: AgentSnapshot,
        policy: ToolAgentPolicy,
        role: str,
    ):
        task_key = canonical_sha256(task.task_id)[:16]
        episode_root = self.root / role / task_key
        runtime = ToolAgentRuntime(
            environment_factory=lambda: LocalDocumentEnvironment(episode_root),
            policy=policy,
            verifier=AdmissionVerifier(),
            limits=self.limits,
            seed=self.seed,
        )
        return runtime.run(
            task.model_copy(deep=True),
            snapshot.model_copy(deep=True),
        )


def _usage(cost: dict[str, float]) -> ResourceUsage:
    return ResourceUsage(
        task_trials=1,
        tokens=int(cost.get("llm_tokens", 0.0)),
        tool_calls=int(cost.get("tool_calls", 0.0)),
        wall_seconds=0.0,
        cost_usd=float(cost.get("cost_usd", 0.0)),
    )


def _sum_usage(items) -> ResourceUsage:
    task_trials = 0
    tokens = 0
    tool_calls = 0
    cost_usd = 0.0
    for item in items:
        task_trials += item.task_trials
        tokens += item.tokens
        tool_calls += item.tool_calls
        cost_usd += item.cost_usd
    return ResourceUsage(
        task_trials=task_trials,
        tokens=tokens,
        tool_calls=tool_calls,
        wall_seconds=0.0,
        cost_usd=cost_usd,
    )


def _score(values) -> float:
    values = list(values)
    if not values:
        raise ModelEvaluationError("Cannot score an empty evaluation suite.")
    return sum(bool(value) for value in values) / len(values)


def _safety_violations(trace) -> tuple[str, ...]:
    for event in reversed(trace.observable_events):
        if event.get("event") == "verification":
            return tuple(event.get("safety_violations", ()))
    return ()


__all__ = [
    "AdmissionVerifier",
    "IndependentModelCandidateEvaluator",
]
