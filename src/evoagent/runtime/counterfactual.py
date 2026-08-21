from __future__ import annotations

import re
from typing import Callable

from evoagent.diagnosis.counterfactual import (
    CounterfactualExperiment,
    CounterfactualRunner,
    ExperimentResult,
    ExperimentType,
)
from evoagent.domain.models import AgentSnapshot, ExecutionTrace, FailureLayer, Task
from evoagent.runtime.base import AgentRuntime


_RULE_PATTERN = re.compile(
    r"(?:^|\s)missing_skill_rule:\s*([A-Za-z0-9_.-]{1,80})(?:\s|$)"
)
_LAYER_BY_EXPERIMENT = {
    ExperimentType.REPLACE_SKILL: FailureLayer.SKILL,
    ExperimentType.FORCE_ROUTER: FailureLayer.ROUTER,
    ExperimentType.REPLAY_TOOL: FailureLayer.TOOL,
    ExperimentType.COMPLETE_CONTEXT: FailureLayer.CONTEXT,
    ExperimentType.ORACLE_VERIFIER: FailureLayer.VERIFIER,
    ExperimentType.RESET_ENVIRONMENT: FailureLayer.ENVIRONMENT,
    ExperimentType.REFERENCE_MODEL: FailureLayer.MODEL,
}


class LocalToolCounterfactualRunner(CounterfactualRunner):
    """Execute controlled local Tool replays against one observed failed Trace.

    Every experiment receives a fresh resettable environment. Only the Skill
    experiment changes Skill behavior by applying the structured missing-rule
    evidence emitted by the independent verifier. Other experiments keep the
    Skill fixed and vary only their declared reference condition.
    """

    def __init__(
        self,
        *,
        runtime_factory: Callable[[], AgentRuntime],
        task: Task,
        baseline_snapshot: AgentSnapshot,
        baseline_trace: ExecutionTrace,
    ):
        if baseline_trace.task.task_id != task.task_id:
            raise ValueError("Counterfactual task must match the baseline Trace task.")
        if baseline_trace.verifier_passed:
            raise ValueError("Counterfactual attribution requires a failed baseline Trace.")
        if baseline_trace.model_id != baseline_snapshot.model_id:
            raise ValueError("Baseline Trace model does not match the frozen snapshot.")
        self.runtime_factory = runtime_factory
        self.task = task
        self.baseline_snapshot = baseline_snapshot.model_copy(deep=True)
        self.baseline_trace = baseline_trace.model_copy(deep=True)
        self._traces: dict[str, ExecutionTrace] = {}

    @property
    def structured_rule(self) -> str | None:
        match = _RULE_PATTERN.search(self.baseline_trace.verifier_feedback)
        return match.group(1) if match else None

    def run(self, experiment: CounterfactualExperiment) -> ExperimentResult:
        layer = _LAYER_BY_EXPERIMENT[experiment.experiment_type]
        snapshot = self._snapshot_for(experiment)
        trace = self.runtime_factory().run(self.task, snapshot)
        self._traces[experiment.experiment_id] = trace

        baseline_success = self.baseline_trace.verifier_passed
        counterfactual_success = trace.verifier_passed
        supports = not baseline_success and counterfactual_success
        evidence = self._evidence(
            experiment=experiment,
            layer=layer,
            counterfactual_trace=trace,
            supports=supports,
        )
        return ExperimentResult(
            experiment_id=experiment.experiment_id,
            hypothesis_id=experiment.hypothesis_id,
            experiment_type=experiment.experiment_type,
            baseline_success=baseline_success,
            counterfactual_success=counterfactual_success,
            supports_hypothesis=supports,
            confidence=1.0,
            evidence=evidence,
            metadata={
                "baseline_trace_id": self.baseline_trace.trace_id,
                "counterfactual_trace_id": trace.trace_id,
                "intervened_layer": layer.value,
                "structured_rule": self.structured_rule,
                "model_id": trace.model_id,
                "skill_id": trace.skill_id,
                "skill_version": trace.skill_version,
                "tool_calls": int(trace.cost.get("tool_calls", 0.0)),
                "final_status": trace.final_output.get("status"),
                "final_state_fingerprint": self._final_fingerprint(trace),
            },
        )

    def traces(self) -> dict[str, ExecutionTrace]:
        return {
            experiment_id: trace.model_copy(deep=True)
            for experiment_id, trace in self._traces.items()
        }

    def _snapshot_for(self, experiment: CounterfactualExperiment) -> AgentSnapshot:
        snapshot = self.baseline_snapshot.model_copy(deep=True)
        snapshot.snapshot_id = (
            f"{self.baseline_snapshot.snapshot_id}:cf:{experiment.experiment_type.value}"
        )
        snapshot.parent_snapshot_id = self.baseline_snapshot.snapshot_id
        snapshot.metadata = {
            **snapshot.metadata,
            "counterfactual_experiment": experiment.experiment_type.value,
        }

        if experiment.experiment_type == ExperimentType.REPLACE_SKILL:
            rule = self.structured_rule
            active_id = snapshot.metadata.get("active_skill_id")
            if rule and isinstance(active_id, str) and active_id in snapshot.skills:
                skill = snapshot.skills[active_id].model_copy(deep=True)
                if rule not in skill.rules:
                    skill.rules.append(rule)
                skill.version = f"{skill.version}+counterfactual"
                snapshot.skills[active_id] = skill
        elif experiment.experiment_type == ExperimentType.FORCE_ROUTER:
            # The local experiment has one declared active Skill. Reasserting that
            # route changes no Skill content and therefore isolates routing.
            active_id = snapshot.metadata.get("active_skill_id")
            snapshot.metadata["forced_skill_id"] = active_id
        elif experiment.experiment_type == ExperimentType.COMPLETE_CONTEXT:
            snapshot.metadata["context_reference"] = "task-input-complete"
        elif experiment.experiment_type == ExperimentType.ORACLE_VERIFIER:
            snapshot.metadata["verifier_reference"] = "independent-document-verifier"
        elif experiment.experiment_type == ExperimentType.RESET_ENVIRONMENT:
            snapshot.metadata["environment_reference"] = "fresh-deterministic-reset"
        elif experiment.experiment_type == ExperimentType.REPLAY_TOOL:
            snapshot.metadata["tool_reference"] = "fresh-local-tool-replay"
        elif experiment.experiment_type == ExperimentType.REFERENCE_MODEL:
            snapshot.model_id = "synthetic/reference-local-document-policy-v1"
        return snapshot

    def _evidence(
        self,
        *,
        experiment: CounterfactualExperiment,
        layer: FailureLayer,
        counterfactual_trace: ExecutionTrace,
        supports: bool,
    ) -> list[str]:
        if supports and layer == FailureLayer.SKILL:
            return [
                f"Baseline local Tool Trace {self.baseline_trace.trace_id} failed.",
                (
                    "Adding only the verifier-specified Skill rule "
                    f"{self.structured_rule} changed the same task to success."
                ),
                "Model identifier, task input, policy implementation, Tool set, verifier and seed remained controlled.",
            ]
        return [
            f"Baseline local Tool Trace {self.baseline_trace.trace_id} failed.",
            (
                f"Controlled {experiment.experiment_type.value} replay remained "
                f"{'successful' if counterfactual_trace.verifier_passed else 'failed'}."
            ),
            f"The {layer.value} intervention did not independently resolve the failure.",
        ]

    @staticmethod
    def _final_fingerprint(trace: ExecutionTrace) -> str | None:
        for event in reversed(trace.observable_events):
            if event.get("event") == "verification":
                value = event.get("final_state_fingerprint")
                return value if isinstance(value, str) else None
        return None


__all__ = ["LocalToolCounterfactualRunner"]
