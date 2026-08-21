from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evoagent.benchmarks import (
    LOCAL_TOOL_MODEL_ID,
    BenchmarkManifest,
    LocalToolFrozenEvaluator,
    ResourceBudget,
    build_local_tool_tasks,
)
from evoagent.composite import (
    CompositeSnapshotEvaluation,
    CompositeSnapshotManifest,
    CompositeTaskOutcome,
    CompositeTaskTrack,
    build_composite_evaluation,
)
from evoagent.local_rl import (
    IndependentLocalPolicyEvaluator,
    LocalRLRunManifest,
    LocalRLTaskKind,
    LocalPolicyCheckpoint,
    build_run_manifest,
)
from evoagent.model_registry.models import canonical_sha256
from evoagent.runtime import (
    DocumentSkillPolicy,
    DocumentTaskVerifier,
    LocalDocumentEnvironment,
    RuntimeLimits,
    ToolAgentRuntime,
    snapshot_from_skill_spec,
)
from evoagent.skills import SkillVersionRecord, SkillVersionStatus


_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class ControlledCompositeContracts(BaseModel):
    """Exact hashes frozen across A0, A1 and A2."""

    model_config = ConfigDict(frozen=True)

    format_version: Literal["evoagent-controlled-composite-contract-v1"] = (
        "evoagent-controlled-composite-contract-v1"
    )
    runtime_hash: str = Field(pattern=_SHA256_PATTERN)
    tool_contract_hash: str = Field(pattern=_SHA256_PATTERN)
    verifier_hash: str = Field(pattern=_SHA256_PATTERN)
    task_manifest_hash: str = Field(pattern=_SHA256_PATTERN)
    budget_hash: str = Field(pattern=_SHA256_PATTERN)
    contract_hash: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_hash(self):
        payload = self.model_dump(mode="json", exclude={"contract_hash"})
        if self.contract_hash != canonical_sha256(payload):
            raise ValueError("Controlled composite contract hash mismatch.")
        return self


class ControlledCompositeRuntimeEvaluator:
    """Run Skill Tools and local-policy MDP Tasks without component mutation."""

    SKILL_SEED = 37
    SKILL_MAX_STEPS = 6
    SKILL_MAX_TOOL_CALLS = 4
    SKILL_MAX_WALL_SECONDS = 5.0
    POLICY_EVALUATOR_ID = "independent-composite-local-policy-evaluator"
    POLICY_TRAINER_ID = "integrated-local-policy-executor"

    def __init__(
        self,
        root: str | Path,
        *,
        local_rl_manifest: LocalRLRunManifest,
    ):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.local_rl_manifest = local_rl_manifest
        self.skill_tasks = build_local_tool_tasks()
        self.policy_tasks = self._representative_policy_tasks(
            local_rl_manifest
        )
        self.policy_evaluation_manifest = build_run_manifest(
            run_id=local_rl_manifest.run_id,
            created_at=local_rl_manifest.created_at,
            environment=local_rl_manifest.environment,
            training_tasks=local_rl_manifest.training_tasks,
            held_out_tasks=self.policy_tasks,
            hyperparameters=local_rl_manifest.hyperparameters,
            budget=local_rl_manifest.budget,
        )
        self.contracts = self._build_contracts()

    @staticmethod
    def _representative_policy_tasks(
        manifest: LocalRLRunManifest,
    ):
        """Freeze one pre-existing held-out Task for each policy Task kind."""

        selected = []
        for kind in (
            LocalRLTaskKind.NORMAL,
            LocalRLTaskKind.PROTECTED,
        ):
            matches = tuple(
                task for task in manifest.held_out_tasks if task.kind == kind
            )
            if not matches:
                raise ValueError(
                    "Controlled composite evaluation lacks one local-policy Task kind."
                )
            selected.append(
                sorted(matches, key=lambda task: task.task_id)[0]
            )
        tasks = tuple(selected)
        if any(task in manifest.training_tasks for task in tasks):
            raise ValueError(
                "Controlled composite policy Task overlaps the training set."
            )
        return tasks

    def evaluate(
        self,
        snapshot: CompositeSnapshotManifest,
        *,
        skill_record: SkillVersionRecord,
        policy_checkpoint: LocalPolicyCheckpoint,
        evaluation_id: str,
        evaluator_id: str,
        evaluated_at: datetime,
        parent: CompositeSnapshotEvaluation | None = None,
    ) -> CompositeSnapshotEvaluation:
        self._validate_component_bindings(
            snapshot,
            skill_record=skill_record,
            policy_checkpoint=policy_checkpoint,
        )
        outcomes = (
            *self._evaluate_skill(snapshot, skill_record),
            *self._evaluate_policy(policy_checkpoint),
        )
        return build_composite_evaluation(
            snapshot,
            evaluation_id=evaluation_id,
            outcomes=tuple(outcomes),
            evaluator_id=evaluator_id,
            evaluated_at=evaluated_at,
            parent=parent,
        )

    def snapshot_contract_kwargs(self) -> dict[str, str]:
        return {
            "runtime_hash": self.contracts.runtime_hash,
            "tool_contract_hash": self.contracts.tool_contract_hash,
            "verifier_hash": self.contracts.verifier_hash,
            "task_manifest_hash": self.contracts.task_manifest_hash,
            "budget_hash": self.contracts.budget_hash,
        }

    def _evaluate_skill(
        self,
        snapshot: CompositeSnapshotManifest,
        skill_record: SkillVersionRecord,
    ) -> tuple[CompositeTaskOutcome, ...]:
        runtime_snapshot = snapshot_from_skill_spec(
            skill_record.spec,
            snapshot_id=f"runtime:{snapshot.snapshot_id}:skill",
            round_index=snapshot.round_index,
            model_id=LOCAL_TOOL_MODEL_ID,
            parent_snapshot_id=snapshot.parent_snapshot_id,
            harness_version="v2.3-composite",
        )
        runtime = ToolAgentRuntime(
            environment_factory=lambda: LocalDocumentEnvironment(
                self.root
                / "skill-episodes"
                / snapshot.snapshot_id.replace(":", "_")
            ),
            policy=DocumentSkillPolicy(),
            verifier=DocumentTaskVerifier(),
            limits=RuntimeLimits(
                max_steps=self.SKILL_MAX_STEPS,
                max_tool_calls=self.SKILL_MAX_TOOL_CALLS,
                max_wall_seconds=self.SKILL_MAX_WALL_SECONDS,
            ),
            seed=self.SKILL_SEED,
        )
        evaluator = LocalToolFrozenEvaluator(runtime, self.skill_tasks)
        manifest = BenchmarkManifest(
            dataset_ref="evoagent/local-document-tools",
            revision="v1-held-out-disjoint-from-training",
            split="held-out",
            task_ids=tuple(item.task_id for item in self.skill_tasks),
            trials_per_task=1,
            updates_allowed_during_evaluation=False,
        )
        evaluator.evaluate(
            runtime_snapshot,
            manifest,
            ResourceBudget(
                max_task_trials=len(self.skill_tasks),
                max_tool_calls=8,
                max_wall_seconds=20.0,
            ),
        )
        traces = evaluator.traces()[runtime_snapshot.snapshot_id]
        return tuple(
            self._skill_outcome(task.task_id, traces[task.task_id])
            for task in self.skill_tasks
        )

    def _evaluate_policy(
        self,
        checkpoint: LocalPolicyCheckpoint,
    ) -> tuple[CompositeTaskOutcome, ...]:
        report = IndependentLocalPolicyEvaluator().evaluate(
            self.policy_evaluation_manifest,
            checkpoint,
            evaluator_id=self.POLICY_EVALUATOR_ID,
            trainer_id=self.POLICY_TRAINER_ID,
        )
        return tuple(
            CompositeTaskOutcome(
                task_id=f"composite:local-policy:{item.task_id}",
                track=CompositeTaskTrack.LOCAL_POLICY,
                passed=item.success,
                score=1.0 if item.success else 0.0,
                unsafe_action_count=item.unsafe_action_count,
                tool_calls=0,
                episode_steps=item.episode_steps,
                deterministic_cost=0.001 * item.episode_steps,
                trace_hash=item.result_hash,
                verifier_hash=self.contracts.verifier_hash,
            )
            for item in report.task_results
        )

    def _skill_outcome(self, task_id: str, trace) -> CompositeTaskOutcome:
        verification_events = tuple(
            event
            for event in trace.observable_events
            if event.get("event") == "verification"
        )
        if len(verification_events) != 1:
            raise RuntimeError(
                "Controlled Skill Trace lacks one exact verification event."
            )
        verification = verification_events[0]
        stable_trace = {
            "trace_id": trace.trace_id,
            "task": trace.task.model_dump(mode="json"),
            "model_id": trace.model_id,
            "skill_id": trace.skill_id,
            "skill_version": trace.skill_version,
            "observable_events": trace.observable_events,
            "final_output": trace.final_output,
            "verifier_passed": trace.verifier_passed,
            "verifier_feedback": trace.verifier_feedback,
            "steps": int(trace.cost.get("steps", 0.0)),
            "tool_calls": int(trace.cost.get("tool_calls", 0.0)),
        }
        steps = stable_trace["steps"]
        tool_calls = stable_trace["tool_calls"]
        return CompositeTaskOutcome(
            task_id=f"composite:skill:{task_id}",
            track=CompositeTaskTrack.SKILL,
            passed=trace.verifier_passed,
            score=1.0 if trace.verifier_passed else 0.0,
            unsafe_action_count=len(
                verification.get("safety_violations", ())
            ),
            tool_calls=tool_calls,
            episode_steps=steps,
            deterministic_cost=0.001 * (steps + tool_calls),
            trace_hash=canonical_sha256(stable_trace),
            verifier_hash=self.contracts.verifier_hash,
        )

    def _validate_component_bindings(
        self,
        snapshot: CompositeSnapshotManifest,
        *,
        skill_record: SkillVersionRecord,
        policy_checkpoint: LocalPolicyCheckpoint,
    ) -> None:
        if skill_record.status != SkillVersionStatus.ACTIVE:
            raise ValueError(
                "Controlled composite evaluation requires the active Skill."
            )
        if (
            snapshot.skill.skill_id != skill_record.spec.skill_id
            or snapshot.skill.version != skill_record.spec.version
            or snapshot.skill.content_hash != skill_record.content_hash
        ):
            raise ValueError(
                "Composite snapshot Skill binding differs from runtime evidence."
            )
        if (
            snapshot.local_policy.checkpoint_hash
            != policy_checkpoint.checkpoint_hash
            or policy_checkpoint.run_id != self.local_rl_manifest.run_id
            or policy_checkpoint.state_keys
            != self.local_rl_manifest.environment.state_keys
            or policy_checkpoint.actions
            != self.local_rl_manifest.environment.actions
        ):
            raise ValueError(
                "Composite snapshot local-policy binding differs from frozen checkpoint evidence."
            )
        expected = self.snapshot_contract_kwargs()
        if any(getattr(snapshot, key) != value for key, value in expected.items()):
            raise ValueError(
                "Composite snapshot differs from the frozen runtime contract."
            )

    def _build_contracts(self) -> ControlledCompositeContracts:
        skill_task_payloads = tuple(
            task.model_dump(mode="json") for task in self.skill_tasks
        )
        policy_task_payloads = tuple(
            task.model_dump(mode="json") for task in self.policy_tasks
        )
        payload = {
            "format_version": "evoagent-controlled-composite-contract-v1",
            "runtime_hash": canonical_sha256(
                {
                    "tool_runtime": "ToolAgentRuntime",
                    "tool_policy": "DocumentSkillPolicy",
                    "local_policy_runtime": "LocalSafeDocumentMDP",
                    "environment_contract_hash": (
                        self.local_rl_manifest.environment.contract_hash
                    ),
                    "skill_seed": self.SKILL_SEED,
                }
            ),
            "tool_contract_hash": canonical_sha256(
                {
                    "skill_tools": (
                        "list_documents",
                        "read_document",
                        "write_document",
                    ),
                    "local_policy_actions": (
                        self.local_rl_manifest.environment.actions
                    ),
                }
            ),
            "verifier_hash": canonical_sha256(
                {
                    "skill_verifier": "DocumentTaskVerifier",
                    "local_policy_evaluator": (
                        "IndependentLocalPolicyEvaluator"
                    ),
                    "binary_score_contract": "pass=1,fail=0",
                }
            ),
            "task_manifest_hash": canonical_sha256(
                {
                    "skill_tasks": skill_task_payloads,
                    "local_policy_tasks": policy_task_payloads,
                    "policy_task_selection": (
                        "lexicographically_first_heldout_task_per_kind"
                    ),
                }
            ),
            "budget_hash": canonical_sha256(
                {
                    "skill_max_steps": self.SKILL_MAX_STEPS,
                    "skill_max_tool_calls": self.SKILL_MAX_TOOL_CALLS,
                    "skill_max_wall_seconds": (
                        self.SKILL_MAX_WALL_SECONDS
                    ),
                    "local_policy_max_episode_steps": (
                        self.local_rl_manifest.environment.max_episode_steps
                    ),
                    "local_policy_task_count": len(self.policy_tasks),
                }
            ),
        }
        return ControlledCompositeContracts(
            **payload,
            contract_hash=canonical_sha256(payload),
        )


__all__ = [
    "ControlledCompositeContracts",
    "ControlledCompositeRuntimeEvaluator",
]
