from __future__ import annotations

from abc import ABC, abstractmethod

from evoagent.benchmarks.models import (
    BenchmarkManifest,
    EvaluationBatch,
    EvolutionProtocolSpec,
    EvolutionRun,
    ResourceBudget,
    RunSummary,
    SameStartComparison,
    SnapshotEvaluation,
)
from evoagent.domain.models import AgentSnapshot


class FrozenSnapshotEvaluator(ABC):
    @abstractmethod
    def evaluate(
        self,
        snapshot: AgentSnapshot,
        manifest: BenchmarkManifest,
        budget: ResourceBudget,
    ) -> EvaluationBatch:
        raise NotImplementedError


class EvolutionEvaluationProtocol:
    def evaluate_run(
        self,
        *,
        system_name: str,
        snapshots: list[AgentSnapshot],
        protocol: EvolutionProtocolSpec,
        evaluator: FrozenSnapshotEvaluator,
    ) -> EvolutionRun:
        if not snapshots:
            raise ValueError("At least one snapshot is required.")
        ordered = sorted(snapshots, key=lambda item: item.round_index)
        rounds = [item.round_index for item in ordered]
        if rounds[0] != 0 or rounds != sorted(set(rounds)):
            raise ValueError("Snapshots must have unique rounds starting at zero.")
        if ordered[0].model_id != protocol.initial_model_id:
            raise ValueError("A0 model does not match the protocol initial checkpoint.")
        if protocol.manifest.updates_allowed_during_evaluation:
            raise ValueError("Frozen evaluation cannot allow agent updates.")

        evaluations: list[SnapshotEvaluation] = []
        expected_tasks = set(protocol.manifest.task_ids)
        for snapshot in ordered:
            before = snapshot.model_dump_json()
            batch = evaluator.evaluate(snapshot, protocol.manifest, protocol.evaluation_budget)
            after = snapshot.model_dump_json()
            if before != after:
                raise RuntimeError("Evaluator mutated a frozen Agent snapshot.")
            if set(batch.per_task) != expected_tasks:
                raise ValueError("Evaluation result does not match the frozen task manifest.")
            if not batch.usage.fits(protocol.evaluation_budget):
                raise ValueError("Evaluation exceeded the fixed resource budget.")
            score = sum(batch.per_task.values()) / len(batch.per_task)
            evaluations.append(
                SnapshotEvaluation(
                    snapshot_id=snapshot.snapshot_id,
                    round_index=snapshot.round_index,
                    model_id=snapshot.model_id,
                    manifest_fingerprint=protocol.manifest.fingerprint,
                    score=score,
                    per_task=dict(batch.per_task),
                    usage=batch.usage,
                )
            )
        return EvolutionRun(
            system_name=system_name,
            protocol=protocol,
            evaluations=tuple(evaluations),
        )

    @staticmethod
    def summarize(run: EvolutionRun) -> RunSummary:
        if not run.evaluations:
            raise ValueError("Cannot summarize an empty evolution run.")
        initial = run.evaluations[0]
        final = run.evaluations[-1]
        best = max(run.evaluations, key=lambda item: item.score)
        return RunSummary(
            system_name=run.system_name,
            initial_score=initial.score,
            final_score=final.score,
            evolution_gain=final.score - initial.score,
            best_score=best.score,
            best_round=best.round_index,
            final_round=final.round_index,
        )


class SameStartComparator:
    def compare(self, runs: list[EvolutionRun]) -> SameStartComparison:
        if not runs:
            raise ValueError("At least one run is required.")
        reference = runs[0].protocol
        for run in runs:
            if run.protocol != reference:
                raise ValueError(
                    "Same-start comparison requires identical initial model, manifests and budgets."
                )
        summaries = [EvolutionEvaluationProtocol.summarize(run) for run in runs]
        summaries.sort(key=lambda item: (item.final_score, item.evolution_gain), reverse=True)
        return SameStartComparison(
            protocol_id=reference.protocol_id,
            initial_model_id=reference.initial_model_id,
            rankings=tuple(summaries),
        )
