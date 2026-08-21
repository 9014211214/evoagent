from __future__ import annotations

from datetime import datetime

from evoagent.local_rl.builders import (
    build_evaluation_report,
    build_evaluation_task_result,
    build_selection_decision,
)
from evoagent.local_rl.environment import LocalSafeDocumentMDP
from evoagent.local_rl.models import (
    LocalRLCheckpointAssessment,
    LocalRLEvaluationReport,
    LocalRLError,
    LocalRLRunManifest,
    LocalRLSelectionDecision,
    LocalRLTrainingResult,
    LocalPolicyCheckpoint,
)
from evoagent.local_rl.policy import TabularSoftmaxPolicy
from evoagent.model_registry.models import canonical_sha256


class IndependentLocalPolicyEvaluator:
    """Deterministic greedy evaluator separated from the rollout optimizer."""

    def evaluate(
        self,
        manifest: LocalRLRunManifest,
        checkpoint: LocalPolicyCheckpoint,
        *,
        evaluator_id: str,
        trainer_id: str,
    ) -> LocalRLEvaluationReport:
        if evaluator_id == trainer_id:
            raise LocalRLError(
                "Local RL evaluator must be independent from the trainer identity."
            )
        if checkpoint.run_id != manifest.run_id:
            raise LocalRLError("Local RL checkpoint belongs to another run.")
        if (
            checkpoint.state_keys != manifest.environment.state_keys
            or checkpoint.actions != manifest.environment.actions
        ):
            raise LocalRLError(
                "Local RL checkpoint differs from the frozen environment contract."
            )
        policy = TabularSoftmaxPolicy.from_checkpoint(checkpoint)
        environment = LocalSafeDocumentMDP(manifest.environment)
        results = []
        for task in manifest.held_out_tasks:
            state = environment.reset(task)
            actions = []
            rewards = []
            unsafe_count = 0
            success = False
            for step_index in range(manifest.environment.max_episode_steps):
                action, _, _, _ = policy.greedy_action(state.key)
                actions.append(action)
                transition = environment.step(
                    state,
                    action,
                    step_index=step_index,
                )
                rewards.append(transition.reward)
                unsafe_count += int(transition.unsafe)
                success = transition.success
                state = transition.state
                if transition.done:
                    break
            results.append(
                build_evaluation_task_result(
                    task_id=task.task_id,
                    task_hash=task.task_hash,
                    kind=task.kind,
                    actions=tuple(actions),
                    total_reward=sum(rewards),
                    success=success,
                    unsafe_action_count=unsafe_count,
                    episode_steps=len(actions),
                )
            )
        task_manifest_hash = canonical_sha256(
            [item.model_dump(mode="json") for item in manifest.held_out_tasks]
        )
        return build_evaluation_report(
            report_id=f"{manifest.run_id}:evaluation:{checkpoint.iteration}",
            run_id=manifest.run_id,
            checkpoint=checkpoint,
            evaluator_id=evaluator_id,
            task_manifest_hash=task_manifest_hash,
            task_results=tuple(results),
        )


class LocalPolicyCheckpointSelector:
    """Select the earliest highest-scoring safe, non-regressing checkpoint."""

    def decide(
        self,
        manifest: LocalRLRunManifest,
        training: LocalRLTrainingResult,
        baseline: LocalRLEvaluationReport,
        candidate_reports: tuple[LocalRLEvaluationReport, ...],
        *,
        decision_id: str,
        decision_actor_id: str,
        decided_at: datetime,
    ) -> LocalRLSelectionDecision:
        if training.manifest_hash != manifest.manifest_hash:
            raise LocalRLError("Local RL training result differs from the manifest.")
        if baseline.checkpoint_hash != training.initial_checkpoint.checkpoint_hash:
            raise LocalRLError("Local RL baseline report differs from P0.")
        if baseline.task_manifest_hash != canonical_sha256(
            [item.model_dump(mode="json") for item in manifest.held_out_tasks]
        ):
            raise LocalRLError("Local RL held-out Task manifest drifted.")
        by_hash = {
            checkpoint.checkpoint_hash: checkpoint
            for checkpoint in training.retained_checkpoints
        }
        if len(candidate_reports) != len(by_hash):
            raise LocalRLError(
                "Local RL selection requires one report per retained checkpoint."
            )
        reports_by_hash = {report.checkpoint_hash: report for report in candidate_reports}
        if set(reports_by_hash) != set(by_hash):
            raise LocalRLError(
                "Local RL evaluation reports do not match retained checkpoints."
            )

        baseline_by_task = {
            item.task_id: item for item in baseline.task_results
        }
        assessments = []
        for checkpoint in training.retained_checkpoints:
            report = reports_by_hash[checkpoint.checkpoint_hash]
            if report.task_manifest_hash != baseline.task_manifest_hash:
                raise LocalRLError(
                    "Local RL candidate report used another held-out manifest."
                )
            candidate_by_task = {
                item.task_id: item for item in report.task_results
            }
            if set(candidate_by_task) != set(baseline_by_task):
                raise LocalRLError(
                    "Local RL candidate report Task set differs from baseline."
                )
            regression_count = sum(
                baseline_by_task[task_id].success
                and not candidate_by_task[task_id].success
                for task_id in baseline_by_task
            )
            reasons = []
            improvement = report.overall_score - baseline.overall_score
            if improvement <= 0.0:
                reasons.append("no_strict_held_out_improvement")
            if regression_count:
                reasons.append("held_out_regression_detected")
            if report.unsafe_action_count:
                reasons.append("unsafe_action_detected")
            if not training.usage.fits(manifest.budget):
                reasons.append("training_budget_exceeded")
            payload = {
                "checkpoint_id": checkpoint.checkpoint_id,
                "checkpoint_hash": checkpoint.checkpoint_hash,
                "iteration": checkpoint.iteration,
                "score": report.overall_score,
                "improvement": improvement,
                "regression_count": regression_count,
                "unsafe_action_count": report.unsafe_action_count,
                "eligible": not reasons,
                "reasons": tuple(reasons),
            }
            assessments.append(
                LocalRLCheckpointAssessment(
                    **payload,
                    assessment_hash=canonical_sha256(payload),
                )
            )

        eligible = [item for item in assessments if item.eligible]
        if not eligible:
            raise LocalRLError(
                "Local RL produced no safe, improving, non-regressing checkpoint."
            )
        selected = sorted(
            eligible,
            key=lambda item: (-item.score, item.iteration, item.checkpoint_hash),
        )[0]
        selected_report = reports_by_hash[selected.checkpoint_hash]
        return build_selection_decision(
            decision_id=decision_id,
            run_id=manifest.run_id,
            manifest_hash=manifest.manifest_hash,
            baseline_report_hash=baseline.report_hash,
            assessments=tuple(assessments),
            selected_checkpoint_id=selected.checkpoint_id,
            selected_checkpoint_hash=selected.checkpoint_hash,
            selected_iteration=selected.iteration,
            selected_report_hash=selected_report.report_hash,
            decision_actor_id=decision_actor_id,
            decided_at=decided_at,
        )


__all__ = [
    "IndependentLocalPolicyEvaluator",
    "LocalPolicyCheckpointSelector",
]
