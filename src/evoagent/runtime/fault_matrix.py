from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from pydantic import BaseModel, ConfigDict, field_validator

from evoagent.diagnosis.counterfactual import (
    CounterfactualExperiment,
    CounterfactualRunner,
    ExperimentResult,
    ExperimentType,
)
from evoagent.domain.models import (
    AgentSnapshot,
    EvolutionAction,
    ExecutionTrace,
    FailureLayer,
    Skill,
    Task,
)
from evoagent.runtime.interfaces import TaskVerifier, ToolAgentPolicy
from evoagent.runtime.local_documents import LocalDocumentEnvironment
from evoagent.runtime.models import (
    AgentAction,
    AgentContext,
    EnvironmentObservation,
    RuntimeLimits,
    ToolCall,
    ToolResult,
    VerificationContext,
    VerificationResult,
)
from evoagent.runtime.policies import DocumentSkillPolicy
from evoagent.runtime.tool_agent import ToolAgentRuntime
from evoagent.runtime.verifiers import DocumentTaskVerifier


_EXPERIMENT_LAYER = {
    ExperimentType.REPLACE_SKILL: FailureLayer.SKILL,
    ExperimentType.FORCE_ROUTER: FailureLayer.ROUTER,
    ExperimentType.REPLAY_TOOL: FailureLayer.TOOL,
    ExperimentType.COMPLETE_CONTEXT: FailureLayer.CONTEXT,
    ExperimentType.ORACLE_VERIFIER: FailureLayer.VERIFIER,
    ExperimentType.RESET_ENVIRONMENT: FailureLayer.ENVIRONMENT,
    ExperimentType.REFERENCE_MODEL: FailureLayer.MODEL,
}
_ACTION_BY_LAYER = {
    FailureLayer.SKILL: EvolutionAction.UPDATE_SKILL,
    FailureLayer.ROUTER: EvolutionAction.UPDATE_ROUTER,
    FailureLayer.TOOL: EvolutionAction.REPAIR_TOOL,
    FailureLayer.CONTEXT: EvolutionAction.UPDATE_CONTEXT,
    FailureLayer.VERIFIER: EvolutionAction.REPAIR_VERIFIER,
    FailureLayer.ENVIRONMENT: EvolutionAction.ESCALATE,
    FailureLayer.MODEL: EvolutionAction.TRAIN_MODEL,
}
_SINGLE_LAYERS = tuple(_ACTION_BY_LAYER)


class ExecutableFaultScenario(BaseModel):
    model_config = ConfigDict(frozen=True)

    scenario_id: str
    fault_layers: tuple[FailureLayer, ...]
    seed: int = 53

    @field_validator("fault_layers")
    @classmethod
    def validate_fault_layers(
        cls, value: tuple[FailureLayer, ...]
    ) -> tuple[FailureLayer, ...]:
        if not value:
            raise ValueError("An executable fault scenario requires at least one layer.")
        if len(value) != len(set(value)):
            raise ValueError("Executable fault layers must be unique.")
        if any(layer not in _SINGLE_LAYERS for layer in value):
            raise ValueError("Unsupported executable fault layer.")
        return value

    @property
    def expected_layer(self) -> FailureLayer:
        return self.fault_layers[0] if len(self.fault_layers) == 1 else FailureLayer.UNKNOWN

    @property
    def expected_action(self) -> EvolutionAction:
        return (
            _ACTION_BY_LAYER[self.fault_layers[0]]
            if len(self.fault_layers) == 1
            else EvolutionAction.ESCALATE
        )


class FailingWriteEnvironment(LocalDocumentEnvironment):
    """A real environment whose write implementation is faulty."""

    def _write(self, call: ToolCall) -> ToolResult:
        self._require_keys(call.arguments, required={"path", "content"})
        normalized, _ = self._target(call.arguments["path"], create_parents=True)
        self._attempted_writes.append(normalized)
        return self._failure(
            call,
            "tool_backend_failure",
            "The injected write Tool backend failed before changing state.",
            output={"path": normalized},
        )


class ConflictingResetEnvironment(LocalDocumentEnvironment):
    """Inject a protected conflicting document during environment reset."""

    def reset(self, task: Task, *, seed: int) -> EnvironmentObservation:
        changed = deepcopy(task.input)
        target = changed.get("target_path")
        if not isinstance(target, str):
            return super().reset(task, seed=seed)
        initial = deepcopy(changed.get("initial_documents", {}))
        if not isinstance(initial, dict):
            initial = {}
        initial[target] = {
            "content": "environment-injected protected conflict",
            "protected": True,
        }
        changed["initial_documents"] = initial
        return super().reset(
            task.model_copy(deep=True, update={"input": changed}),
            seed=seed,
        )


class FalseNegativeDocumentVerifier(TaskVerifier):
    """Reject a correct execution to isolate verifier-layer failure."""

    def __init__(self):
        self.reference = DocumentTaskVerifier()

    def verify(self, task: Task, context: VerificationContext) -> VerificationResult:
        reference = self.reference.verify(task, context)
        return VerificationResult(
            passed=False,
            score=0.0,
            feedback="verifier_fault: false_negative",
            evidence=(
                f"reference_verifier_passed={reference.passed}",
                "Injected verifier returned a false negative.",
            ),
            safety_violations=reference.safety_violations,
        )


class MissingContextDocumentPolicy(ToolAgentPolicy):
    """Withhold one required field from the policy-visible context only."""

    def __init__(self):
        self.reference = DocumentSkillPolicy()

    def next_action(self, context: AgentContext) -> AgentAction:
        visible_input = dict(context.task.input)
        visible_input.pop("content", None)
        visible_task = context.task.model_copy(
            deep=True,
            update={"input": visible_input},
        )
        visible_context = context.model_copy(
            deep=True,
            update={"task": visible_task},
        )
        return self.reference.next_action(visible_context)


class IncapableDocumentPolicy(ToolAgentPolicy):
    """A model-policy stand-in that cannot produce the required action."""

    def next_action(self, context: AgentContext) -> AgentAction:
        return AgentAction.finish(
            status="failed",
            error_code="model_capability_failure",
        )


class ExecutableCrossLayerCounterfactualRunner(CounterfactualRunner):
    """Run actual local Tool counterfactuals for one or more injected layers."""

    def __init__(self, *, root: str | Path, scenario: ExecutableFaultScenario):
        raw_root = Path(root).expanduser()
        if raw_root.is_symlink():
            raise ValueError("Executable fault root must not be a symlink.")
        self.root = raw_root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.scenario = scenario
        self._traces: dict[str, ExecutionTrace] = {}
        self.baseline_task = self._task_for(None)
        self.baseline_snapshot = self._snapshot_for(None)
        self.baseline_trace = self._runtime_for(None).run(
            self.baseline_task,
            self.baseline_snapshot,
        )
        if self.baseline_trace.verifier_passed:
            raise RuntimeError(
                f"Injected scenario unexpectedly passed: {scenario.scenario_id}"
            )

    def run(self, experiment: CounterfactualExperiment) -> ExperimentResult:
        task = self._task_for(experiment.experiment_type)
        snapshot = self._snapshot_for(experiment.experiment_type)
        trace = self._runtime_for(experiment.experiment_type).run(task, snapshot)
        self._traces[experiment.experiment_id] = trace
        supports = not self.baseline_trace.verifier_passed and trace.verifier_passed
        layer = _EXPERIMENT_LAYER[experiment.experiment_type]
        return ExperimentResult(
            experiment_id=experiment.experiment_id,
            hypothesis_id=experiment.hypothesis_id,
            experiment_type=experiment.experiment_type,
            baseline_success=self.baseline_trace.verifier_passed,
            counterfactual_success=trace.verifier_passed,
            supports_hypothesis=supports,
            confidence=1.0,
            evidence=self._evidence(experiment.experiment_type, layer, trace, supports),
            metadata={
                "scenario_id": self.scenario.scenario_id,
                "injected_layers": [item.value for item in self.scenario.fault_layers],
                "intervened_layer": layer.value,
                "baseline_trace_id": self.baseline_trace.trace_id,
                "counterfactual_trace_id": trace.trace_id,
                "baseline_status": self.baseline_trace.final_output.get("status"),
                "counterfactual_status": trace.final_output.get("status"),
                "baseline_model_id": self.baseline_trace.model_id,
                "counterfactual_model_id": trace.model_id,
                "baseline_skill_id": self.baseline_trace.skill_id,
                "counterfactual_skill_id": trace.skill_id,
                "tool_calls": int(trace.cost.get("tool_calls", 0.0)),
                "final_state_fingerprint": self._final_fingerprint(trace),
            },
        )

    def traces(self) -> dict[str, ExecutionTrace]:
        return {
            experiment_id: trace.model_copy(deep=True)
            for experiment_id, trace in self._traces.items()
        }

    def _runtime_for(self, intervention: ExperimentType | None) -> ToolAgentRuntime:
        fault_layers = set(self.scenario.fault_layers)
        tool_fault_active = (
            FailureLayer.TOOL in fault_layers
            and intervention != ExperimentType.REPLAY_TOOL
        )
        context_fault_active = (
            FailureLayer.CONTEXT in fault_layers
            and intervention != ExperimentType.COMPLETE_CONTEXT
        )
        environment_fault_active = (
            FailureLayer.ENVIRONMENT in fault_layers
            and intervention != ExperimentType.RESET_ENVIRONMENT
        )
        verifier_fault_active = (
            FailureLayer.VERIFIER in fault_layers
            and intervention != ExperimentType.ORACLE_VERIFIER
        )
        model_fault_active = (
            FailureLayer.MODEL in fault_layers
            and intervention != ExperimentType.REFERENCE_MODEL
        )

        if tool_fault_active:
            environment_type = FailingWriteEnvironment
        elif environment_fault_active:
            environment_type = ConflictingResetEnvironment
        else:
            environment_type = LocalDocumentEnvironment

        verifier: TaskVerifier = (
            FalseNegativeDocumentVerifier()
            if verifier_fault_active
            else DocumentTaskVerifier()
        )
        if model_fault_active:
            policy: ToolAgentPolicy = IncapableDocumentPolicy()
        elif context_fault_active:
            policy = MissingContextDocumentPolicy()
        else:
            policy = DocumentSkillPolicy()

        return ToolAgentRuntime(
            environment_factory=lambda: environment_type(
                self.root / self.scenario.scenario_id / "episodes"
            ),
            policy=policy,
            verifier=verifier,
            limits=RuntimeLimits(
                max_steps=6,
                max_tool_calls=4,
                max_wall_seconds=5.0,
            ),
            seed=self.scenario.seed,
        )

    def _task_for(self, intervention: ExperimentType | None) -> Task:
        fault_layers = set(self.scenario.fault_layers)
        if fault_layers & {FailureLayer.SKILL, FailureLayer.ROUTER}:
            return self._protected_task()
        return self._create_task()

    def _snapshot_for(self, intervention: ExperimentType | None) -> AgentSnapshot:
        fault_layers = set(self.scenario.fault_layers)
        safe_skill = self._safe_skill()
        unsafe_skill = self._unsafe_skill()
        model_id = "synthetic/local-document-policy-v1"

        if FailureLayer.ROUTER in fault_layers:
            skills = {
                safe_skill.skill_id: safe_skill,
                unsafe_skill.skill_id: unsafe_skill,
            }
            active_id = unsafe_skill.skill_id
            if intervention == ExperimentType.FORCE_ROUTER:
                active_id = safe_skill.skill_id
            if (
                FailureLayer.SKILL in fault_layers
                and intervention == ExperimentType.REPLACE_SKILL
            ):
                patched = unsafe_skill.model_copy(deep=True)
                patched.rules.append("inspect_before_write")
                patched.version = "1.1.0-counterfactual"
                skills[unsafe_skill.skill_id] = patched
        elif FailureLayer.SKILL in fault_layers:
            active = unsafe_skill.model_copy(deep=True)
            if intervention == ExperimentType.REPLACE_SKILL:
                active.rules.append("inspect_before_write")
                active.version = "1.1.0-counterfactual"
            skills = {active.skill_id: active}
            active_id = active.skill_id
        else:
            skills = {safe_skill.skill_id: safe_skill}
            active_id = safe_skill.skill_id

        if (
            FailureLayer.MODEL in fault_layers
            and intervention != ExperimentType.REFERENCE_MODEL
        ):
            model_id = "synthetic/incapable-local-document-policy-v1"
        elif FailureLayer.MODEL in fault_layers:
            model_id = "synthetic/reference-local-document-policy-v1"

        suffix = intervention.value if intervention else "baseline"
        return AgentSnapshot(
            snapshot_id=f"{self.scenario.scenario_id}:{suffix}",
            round_index=0,
            model_id=model_id,
            skills=skills,
            harness_version="1.3.0",
            metadata={
                "active_skill_id": active_id,
                "reference_skill_id": safe_skill.skill_id,
                "scenario_id": self.scenario.scenario_id,
                "intervention": suffix,
            },
        )

    def _evidence(
        self,
        experiment_type: ExperimentType,
        layer: FailureLayer,
        trace: ExecutionTrace,
        supports: bool,
    ) -> list[str]:
        if supports:
            return [
                f"Actual baseline Trace {self.baseline_trace.trace_id} failed.",
                (
                    f"Changing only the declared {experiment_type.value} control "
                    "changed the task to success."
                ),
                f"This supports the executable {layer.value} root-cause hypothesis.",
            ]
        return [
            f"Actual baseline Trace {self.baseline_trace.trace_id} failed.",
            (
                f"Changing only the declared {experiment_type.value} control "
                f"left the task failed with status {trace.final_output.get('status')}."
            ),
            f"This does not support the executable {layer.value} hypothesis.",
        ]

    def _protected_task(self) -> Task:
        return Task(
            task_id=f"matrix:{self.scenario.scenario_id}:protected",
            task_type="executable-cross-layer-protected",
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
            tags=["cross-layer", *[item.value for item in self.scenario.fault_layers]],
        )

    def _create_task(self) -> Task:
        return Task(
            task_id=f"matrix:{self.scenario.scenario_id}:create",
            task_type="executable-cross-layer-create",
            input={
                "initial_documents": {},
                "target_path": "result.txt",
                "content": "synthetic cross-layer result",
                "expected_status": "completed",
                "require_verification": True,
            },
            expected_outcome={"status": "completed"},
            tags=["cross-layer", *[item.value for item in self.scenario.fault_layers]],
        )

    @staticmethod
    def _safe_skill() -> Skill:
        return Skill(
            skill_id="safe_document_writer",
            name="Safe Document Writer",
            version="1.0.0",
            description="Inspect before writing and verify after writing.",
            rules=["inspect_before_write", "verify_after_write"],
        )

    @staticmethod
    def _unsafe_skill() -> Skill:
        return Skill(
            skill_id="unsafe_document_writer",
            name="Unsafe Document Writer",
            version="1.0.0",
            description="Write before inspecting and verify after writing.",
            rules=["verify_after_write"],
        )

    @staticmethod
    def _final_fingerprint(trace: ExecutionTrace) -> str | None:
        for event in reversed(trace.observable_events):
            if event.get("event") == "verification":
                value = event.get("final_state_fingerprint")
                return value if isinstance(value, str) else None
        return None


def build_executable_fault_scenario(layer: FailureLayer) -> ExecutableFaultScenario:
    if layer not in _SINGLE_LAYERS:
        raise ValueError(f"Unsupported executable fault layer: {layer.value}")
    return ExecutableFaultScenario(
        scenario_id=f"fault-{layer.value}",
        fault_layers=(layer,),
    )


def build_conflicting_skill_router_scenario() -> ExecutableFaultScenario:
    return ExecutableFaultScenario(
        scenario_id="fault-conflict-skill-router",
        fault_layers=(FailureLayer.SKILL, FailureLayer.ROUTER),
    )


__all__ = [
    "ConflictingResetEnvironment",
    "ExecutableCrossLayerCounterfactualRunner",
    "ExecutableFaultScenario",
    "FailingWriteEnvironment",
    "FalseNegativeDocumentVerifier",
    "IncapableDocumentPolicy",
    "MissingContextDocumentPolicy",
    "build_conflicting_skill_router_scenario",
    "build_executable_fault_scenario",
]
