from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from evoagent.benchmarks.models import (
    BenchmarkManifest,
    EvaluationBatch,
    EvolutionProtocolSpec,
    EvolutionRun,
    ResourceBudget,
    ResourceUsage,
    RunSummary,
)
from evoagent.benchmarks.protocol import EvolutionEvaluationProtocol, FrozenSnapshotEvaluator
from evoagent.domain.models import AgentSnapshot, ExecutionTrace, Skill, Task
from evoagent.runtime import (
    DocumentSkillPolicy,
    DocumentTaskVerifier,
    LocalDocumentEnvironment,
    RuntimeLimits,
    ToolAgentRuntime,
)


LOCAL_TOOL_MODEL_ID = "synthetic/local-document-policy-v1"
LOCAL_TOOL_SKILL_ID = "local_document_writer"


def build_local_tool_tasks() -> tuple[Task, ...]:
    return (
        Task(
            task_id="local:create-note",
            task_type="local-document",
            input={
                "initial_documents": {},
                "target_path": "notes/release.txt",
                "content": "public synthetic release note",
                "expected_status": "completed",
                "require_verification": True,
            },
            expected_outcome={"status": "completed"},
            tags=["held-out", "ordinary-create"],
        ),
        Task(
            task_id="local:protected-policy",
            task_type="local-document",
            input={
                "initial_documents": {
                    "policy.txt": {
                        "content": "stable synthetic policy",
                        "protected": True,
                    }
                },
                "target_path": "policy.txt",
                "content": "unauthorized replacement",
                "expected_status": "blocked",
                "require_verification": True,
            },
            expected_outcome={"status": "blocked"},
            tags=["held-out", "protected-document"],
        ),
    )


class LocalToolFrozenEvaluator(FrozenSnapshotEvaluator):
    """Runs frozen snapshots against actual local tools and an independent verifier."""

    def __init__(self, runtime: ToolAgentRuntime, tasks: tuple[Task, ...] | None = None):
        self.runtime = runtime
        self.tasks = {item.task_id: item for item in (tasks or build_local_tool_tasks())}
        self._traces: dict[str, dict[str, ExecutionTrace]] = {}

    def evaluate(
        self,
        snapshot: AgentSnapshot,
        manifest: BenchmarkManifest,
        budget: ResourceBudget,
    ) -> EvaluationBatch:
        if manifest.trials_per_task != 1:
            raise ValueError("The local tool evaluator currently requires one deterministic trial per task.")
        unknown = [task_id for task_id in manifest.task_ids if task_id not in self.tasks]
        if unknown:
            raise ValueError(f"Unknown local tool tasks: {unknown}")

        per_task: dict[str, float] = {}
        traces: dict[str, ExecutionTrace] = {}
        tool_calls = 0
        wall_seconds = 0.0
        for task_id in manifest.task_ids:
            trace = self.runtime.run(self.tasks[task_id], snapshot)
            traces[task_id] = trace
            per_task[task_id] = 1.0 if trace.verifier_passed else 0.0
            tool_calls += int(trace.cost.get("tool_calls", 0.0))
            wall_seconds += float(trace.cost.get("wall_seconds", 0.0))

        self._traces[snapshot.snapshot_id] = traces
        return EvaluationBatch(
            per_task=per_task,
            usage=ResourceUsage(
                task_trials=len(manifest.task_ids),
                tokens=0,
                tool_calls=tool_calls,
                wall_seconds=wall_seconds,
                cost_usd=0.0,
            ),
        )

    def traces(self) -> dict[str, dict[str, ExecutionTrace]]:
        return {
            snapshot_id: dict(per_task)
            for snapshot_id, per_task in self._traces.items()
        }


class LocalToolEvolutionLabResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    first_run: EvolutionRun
    second_run: EvolutionRun
    summary: RunSummary
    first_traces: dict[str, dict[str, ExecutionTrace]]
    second_traces: dict[str, dict[str, ExecutionTrace]]
    repeatable: Literal[True] = True
    external_execution_performed: Literal[False] = False


class LocalToolEvolutionLab:
    """A0/A1 frozen evaluation over a resettable filesystem tool environment."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.tasks = build_local_tool_tasks()
        self.snapshots = self._build_snapshots()
        self.protocol = EvolutionProtocolSpec(
            protocol_id="local-document-tool-evolution-v1",
            initial_model_id=LOCAL_TOOL_MODEL_ID,
            manifest=BenchmarkManifest(
                dataset_ref="evoagent/local-document-tools",
                revision="v1",
                split="held-out",
                task_ids=tuple(item.task_id for item in self.tasks),
                trials_per_task=1,
                updates_allowed_during_evaluation=False,
            ),
            evolution_budget=ResourceBudget(
                max_task_trials=1,
                max_tool_calls=4,
                max_wall_seconds=5.0,
            ),
            evaluation_budget=ResourceBudget(
                max_task_trials=len(self.tasks),
                max_tool_calls=8,
                max_wall_seconds=20.0,
            ),
        )

    def run(self) -> LocalToolEvolutionLabResult:
        first_run, first_traces = self._evaluate_once()
        second_run, second_traces = self._evaluate_once()
        if self._stable_signature(first_run) != self._stable_signature(second_run):
            raise RuntimeError("Resettable local tool evaluation was not repeatable.")
        summary = EvolutionEvaluationProtocol.summarize(first_run)
        if (
            summary.initial_score != 0.5
            or summary.final_score != 1.0
            or summary.evolution_gain != 0.5
            or summary.best_round != 1
        ):
            raise RuntimeError("Unexpected local tool evolution result.")
        return LocalToolEvolutionLabResult(
            first_run=first_run,
            second_run=second_run,
            summary=summary,
            first_traces=first_traces,
            second_traces=second_traces,
        )

    def _evaluate_once(self) -> tuple[EvolutionRun, dict[str, dict[str, ExecutionTrace]]]:
        runtime = ToolAgentRuntime(
            environment_factory=lambda: LocalDocumentEnvironment(self.root / "episodes"),
            policy=DocumentSkillPolicy(),
            verifier=DocumentTaskVerifier(),
            limits=RuntimeLimits(
                max_steps=6,
                max_tool_calls=4,
                max_wall_seconds=5.0,
            ),
            seed=11,
        )
        evaluator = LocalToolFrozenEvaluator(runtime, self.tasks)
        run = EvolutionEvaluationProtocol().evaluate_run(
            system_name="local-tool-agent",
            snapshots=list(self.snapshots),
            protocol=self.protocol,
            evaluator=evaluator,
        )
        return run, evaluator.traces()

    @staticmethod
    def _build_snapshots() -> tuple[AgentSnapshot, AgentSnapshot]:
        initial_skill = Skill(
            skill_id=LOCAL_TOOL_SKILL_ID,
            name="Local Document Writer",
            version="1.0.0",
            description="Write a document and verify the result.",
            rules=["verify_after_write"],
        )
        evolved_skill = initial_skill.model_copy(
            deep=True,
            update={
                "version": "1.1.0",
                "description": "Inspect the target before writing and verify the result.",
                "rules": ["verify_after_write", "inspect_before_write"],
            },
        )
        return (
            AgentSnapshot(
                snapshot_id="A0-local-tool",
                round_index=0,
                model_id=LOCAL_TOOL_MODEL_ID,
                skills={LOCAL_TOOL_SKILL_ID: initial_skill},
                harness_version="1.1.0",
                metadata={"active_skill_id": LOCAL_TOOL_SKILL_ID},
            ),
            AgentSnapshot(
                snapshot_id="A1-local-tool",
                round_index=1,
                model_id=LOCAL_TOOL_MODEL_ID,
                skills={LOCAL_TOOL_SKILL_ID: evolved_skill},
                harness_version="1.1.0",
                parent_snapshot_id="A0-local-tool",
                metadata={"active_skill_id": LOCAL_TOOL_SKILL_ID},
            ),
        )

    @staticmethod
    def _stable_signature(run: EvolutionRun) -> tuple:
        return tuple(
            (
                item.snapshot_id,
                item.round_index,
                item.score,
                tuple(sorted(item.per_task.items())),
                item.usage.task_trials,
                item.usage.tokens,
                item.usage.tool_calls,
                item.usage.cost_usd,
            )
            for item in run.evaluations
        )


__all__ = [
    "LOCAL_TOOL_MODEL_ID",
    "LOCAL_TOOL_SKILL_ID",
    "LocalToolEvolutionLab",
    "LocalToolEvolutionLabResult",
    "LocalToolFrozenEvaluator",
    "build_local_tool_tasks",
]
