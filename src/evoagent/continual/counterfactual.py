from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evoagent.domain.models import ExecutionTrace, Task
from evoagent.model_registry.models import canonical_sha256
from evoagent.runtime import RuntimeLimits

from .builders import validate_one_component_transition
from .models import ContinualComponent, UnifiedAgentSnapshot
from .runtime import UnifiedDocumentAgentRuntime


_HASH = r"^[0-9a-f]{64}$"
_SAFE_ID = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"


class UnifiedCounterfactualExperiment(BaseModel):
    model_config = ConfigDict(frozen=True)

    experiment_id: str = Field(pattern=_SAFE_ID)
    component: ContinualComponent
    candidate_snapshot_hash: str = Field(pattern=_HASH)
    verifier_passed: bool
    safety_violation_count: int = Field(ge=0)
    supports_hypothesis: bool
    trace_hash: str = Field(pattern=_HASH)
    experiment_hash: str = Field(pattern=_HASH)

    @model_validator(mode="after")
    def validate_hash(self):
        if self.supports_hypothesis != (
            self.verifier_passed and self.safety_violation_count == 0
        ):
            raise ValueError("Counterfactual support is not safety-derived.")
        payload = self.model_dump(mode="json", exclude={"experiment_hash"})
        if self.experiment_hash != canonical_sha256(payload):
            raise ValueError("Unified counterfactual experiment hash mismatch.")
        return self


class UnifiedAttributionReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    report_id: str = Field(pattern=_SAFE_ID)
    task_id: str = Field(pattern=_SAFE_ID)
    baseline_snapshot_hash: str = Field(pattern=_HASH)
    baseline_trace_hash: str = Field(pattern=_HASH)
    experiments: tuple[UnifiedCounterfactualExperiment, ...]
    supported_component: ContinualComponent | None
    actionable: bool
    conflict: bool
    reason: str
    report_hash: str = Field(pattern=_HASH)

    @model_validator(mode="after")
    def validate_report(self):
        experiment_ids = tuple(item.experiment_id for item in self.experiments)
        components = tuple(item.component for item in self.experiments)
        if (
            not self.experiments
            or len(set(experiment_ids)) != len(experiment_ids)
            or len(set(components)) != len(components)
        ):
            raise ValueError("Counterfactual experiments must be non-empty and unique.")
        successful = tuple(item for item in self.experiments if item.supports_hypothesis)
        if len(successful) == 1:
            if (
                not self.actionable
                or self.conflict
                or self.supported_component != successful[0].component
            ):
                raise ValueError("Unique counterfactual support was not derived.")
        elif self.actionable or self.supported_component is not None:
            raise ValueError("Ambiguous or absent support cannot be actionable.")
        if self.conflict != (len(successful) > 1):
            raise ValueError("Counterfactual conflict flag is not derived.")
        expected_reason = (
            f"unique_successful_intervention:{successful[0].component.value}"
            if len(successful) == 1
            else "causal_conflict"
            if len(successful) > 1
            else "insufficient_counterfactual_evidence"
        )
        if self.reason != expected_reason:
            raise ValueError("Counterfactual reason is not derived.")
        payload = self.model_dump(mode="json", exclude={"report_hash"})
        if self.report_hash != canonical_sha256(payload):
            raise ValueError("Unified attribution report hash mismatch.")
        return self


class UnifiedCounterfactualRunner:
    """Replay one failed Task in fresh Environments with one-component candidates."""

    def __init__(
        self,
        root: str | Path,
        *,
        seed: int = 0,
        limits: RuntimeLimits | None = None,
    ):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.seed = seed
        self.limits = limits or RuntimeLimits(max_steps=8, max_tool_calls=5, max_wall_seconds=5.0)

    def run(
        self,
        task: Task,
        baseline: UnifiedAgentSnapshot,
        interventions: dict[ContinualComponent, UnifiedAgentSnapshot],
        *,
        report_id: str,
    ) -> UnifiedAttributionReport:
        if not interventions:
            raise ValueError("Attribution requires at least one counterfactual.")
        baseline_trace = self._execute("baseline", task, baseline)
        if baseline_trace.verifier_passed:
            raise ValueError("Counterfactual attribution requires an actually failed baseline.")
        experiments = []
        for component in sorted(interventions, key=lambda item: item.value):
            candidate = interventions[component]
            actual = validate_one_component_transition(baseline, candidate)
            if actual != component:
                raise ValueError("Counterfactual key differs from the changed component.")
            trace = self._execute(component.value, task, candidate)
            safety = self._safety_count(trace)
            payload = {
                "experiment_id": f"{report_id}:{component.value}",
                "component": component,
                "candidate_snapshot_hash": candidate.snapshot_hash,
                "verifier_passed": trace.verifier_passed,
                "safety_violation_count": safety,
                "supports_hypothesis": trace.verifier_passed and safety == 0,
                "trace_hash": self._trace_hash(trace),
            }
            experiments.append(
                UnifiedCounterfactualExperiment(
                    **payload,
                    experiment_hash=canonical_sha256(payload),
                )
            )
        successful = tuple(item for item in experiments if item.supports_hypothesis)
        supported = successful[0].component if len(successful) == 1 else None
        reason = (
            f"unique_successful_intervention:{supported.value}"
            if supported
            else "causal_conflict"
            if len(successful) > 1
            else "insufficient_counterfactual_evidence"
        )
        payload = {
            "report_id": report_id,
            "task_id": task.task_id,
            "baseline_snapshot_hash": baseline.snapshot_hash,
            "baseline_trace_hash": self._trace_hash(baseline_trace),
            "experiments": tuple(experiments),
            "supported_component": supported,
            "actionable": supported is not None,
            "conflict": len(successful) > 1,
            "reason": reason,
        }
        return UnifiedAttributionReport(**payload, report_hash=canonical_sha256(payload))

    def _execute(
        self,
        label: str,
        task: Task,
        snapshot: UnifiedAgentSnapshot,
    ) -> ExecutionTrace:
        runtime = UnifiedDocumentAgentRuntime(
            self.root / label,
            seed=self.seed,
            limits=self.limits,
        )
        return runtime.run(task, snapshot)

    @staticmethod
    def _safety_count(trace: ExecutionTrace) -> int:
        verification = tuple(
            item for item in trace.observable_events if item.get("event") == "verification"
        )
        if len(verification) != 1:
            raise RuntimeError("Counterfactual Trace lacks one verification event.")
        return len(verification[0].get("safety_violations", ()))

    @staticmethod
    def _trace_hash(trace: ExecutionTrace) -> str:
        payload = trace.model_dump(mode="json")
        payload["cost"] = {
            key: value for key, value in payload.get("cost", {}).items() if key != "wall_seconds"
        }
        return canonical_sha256(payload)


__all__ = [
    "UnifiedAttributionReport",
    "UnifiedCounterfactualExperiment",
    "UnifiedCounterfactualRunner",
]
