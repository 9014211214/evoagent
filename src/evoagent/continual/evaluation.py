from __future__ import annotations

from pathlib import Path
from typing import Literal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evoagent.benchmarks import ResourceBudget, ResourceUsage
from evoagent.domain.models import ExecutionTrace, Task
from evoagent.model_registry.models import canonical_sha256
from evoagent.runtime import RuntimeLimits

from .builders import validate_one_component_transition
from .models import ContinualComponent, ContinualTaskRole, UnifiedAgentSnapshot
from .runtime import UnifiedDocumentAgentRuntime


_HASH = r"^[0-9a-f]{64}$"
_SAFE_ID = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"


class ContinualTaskSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    task: Task
    role: ContinualTaskRole
    task_hash: str = Field(pattern=_HASH)

    @model_validator(mode="after")
    def validate_hash(self):
        payload = self.model_dump(mode="json", exclude={"task_hash"})
        if self.task_hash != canonical_sha256(payload):
            raise ValueError("Continual Task hash mismatch.")
        return self


class ContinualTaskManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    manifest_id: str = Field(pattern=_SAFE_ID)
    dataset_ref: str
    revision: str
    tasks: tuple[ContinualTaskSpec, ...]
    model_id: str
    seed: int = Field(ge=0)
    runtime_limits: RuntimeLimits
    evaluation_budget: ResourceBudget
    updates_allowed_during_evaluation: bool = False
    manifest_hash: str = Field(pattern=_HASH)

    @model_validator(mode="after")
    def validate_manifest(self):
        task_ids = [item.task.task_id for item in self.tasks]
        if not task_ids or len(set(task_ids)) != len(task_ids):
            raise ValueError("Continual manifest Task IDs must be non-empty and unique.")
        roles = {item.role for item in self.tasks}
        if roles != set(ContinualTaskRole):
            raise ValueError("Continual manifest must cover every evaluation role.")
        if self.updates_allowed_during_evaluation:
            raise ValueError("Frozen continual evaluation cannot permit updates.")
        if self.evaluation_budget.max_task_trials != len(self.tasks):
            raise ValueError("Evaluation budget must bind one exact trial per Task.")
        payload = self.model_dump(mode="json", exclude={"manifest_hash"})
        if self.manifest_hash != canonical_sha256(payload):
            raise ValueError("Continual manifest hash mismatch.")
        return self


class ContinualTaskResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str = Field(pattern=_SAFE_ID)
    task_hash: str = Field(pattern=_HASH)
    role: ContinualTaskRole
    passed: bool
    score: float = Field(ge=0.0, le=1.0)
    safety_violation_count: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    episode_steps: int = Field(ge=0)
    trace_hash: str = Field(pattern=_HASH)
    result_hash: str = Field(pattern=_HASH)

    @model_validator(mode="after")
    def validate_result(self):
        if self.score != (1.0 if self.passed else 0.0):
            raise ValueError("Reference continual Task score must be binary and derived.")
        payload = self.model_dump(mode="json", exclude={"result_hash"})
        if self.result_hash != canonical_sha256(payload):
            raise ValueError("Continual Task result hash mismatch.")
        return self


class ContinualEvaluationReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    report_id: str = Field(pattern=_SAFE_ID)
    snapshot_id: str = Field(pattern=_SAFE_ID)
    snapshot_hash: str = Field(pattern=_HASH)
    round_index: int = Field(ge=0)
    model_id: str
    manifest_hash: str = Field(pattern=_HASH)
    parent_report_hash: str | None = Field(default=None, pattern=_HASH)
    results: tuple[ContinualTaskResult, ...]
    overall_score: float = Field(ge=0.0, le=1.0)
    role_scores: dict[ContinualTaskRole, float]
    regression_count: int = Field(ge=0)
    forgetting_rate: float = Field(ge=0.0, le=1.0)
    safety_violation_count: int = Field(ge=0)
    usage: ResourceUsage
    report_hash: str = Field(pattern=_HASH)
    external_benchmark: Literal[False] = False

    @model_validator(mode="after")
    def validate_report(self):
        if not self.results:
            raise ValueError("Continual report requires Task results.")
        expected = sum(item.score for item in self.results) / len(self.results)
        if abs(self.overall_score - expected) > 1e-12:
            raise ValueError("Continual overall score is not derived.")
        for role in ContinualTaskRole:
            items = tuple(item for item in self.results if item.role == role)
            if not items:
                raise ValueError("Continual report lacks one evaluation role.")
            score = sum(item.score for item in items) / len(items)
            if abs(self.role_scores.get(role, -1.0) - score) > 1e-12:
                raise ValueError("Continual role score is not derived.")
        if self.safety_violation_count != sum(
            item.safety_violation_count for item in self.results
        ):
            raise ValueError("Continual safety count is not derived.")
        if self.usage.task_trials != len(self.results):
            raise ValueError("Continual usage does not bind every Task result.")
        payload = self.model_dump(mode="json", exclude={"report_hash"})
        if self.report_hash != canonical_sha256(payload):
            raise ValueError("Continual evaluation report hash mismatch.")
        return self


class ContinualGatePolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    policy_id: str = Field(pattern=_SAFE_ID)
    minimum_target_gain: float = Field(default=0.01, ge=0.0, le=1.0)
    maximum_retention_drop: float = Field(default=0.0, ge=0.0, le=1.0)
    maximum_regressions: int = Field(default=0, ge=0)
    require_zero_safety_violations: bool = True
    maximum_safety_violation_growth: int = Field(default=0, ge=0)
    maximum_tool_call_growth: float = Field(default=1.0, ge=0.0)
    policy_hash: str = Field(pattern=_HASH)

    @model_validator(mode="after")
    def validate_hash(self):
        payload = self.model_dump(mode="json", exclude={"policy_hash"})
        if self.policy_hash != canonical_sha256(payload):
            raise ValueError("Continual gate policy hash mismatch.")
        return self


class ContinualPromotionDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision_id: str = Field(pattern=_SAFE_ID)
    parent_report_hash: str = Field(pattern=_HASH)
    candidate_report_hash: str = Field(pattern=_HASH)
    candidate_snapshot_hash: str = Field(pattern=_HASH)
    changed_component: ContinualComponent
    target_roles: tuple[ContinualTaskRole, ...]
    target_gain: float
    retention_drop: float
    regression_count: int = Field(ge=0)
    safety_violation_count: int = Field(ge=0)
    safety_violation_growth: int
    eligible: bool
    reasons: tuple[str, ...]
    policy_hash: str = Field(pattern=_HASH)
    decision_hash: str = Field(pattern=_HASH)

    @model_validator(mode="after")
    def validate_decision(self):
        if self.eligible == bool(self.reasons):
            raise ValueError("Eligible decision/reasons shape is inconsistent.")
        payload = self.model_dump(mode="json", exclude={"decision_hash"})
        if self.decision_hash != canonical_sha256(payload):
            raise ValueError("Continual promotion decision hash mismatch.")
        return self


class ContinualLoopAction(str, Enum):
    CONTINUE = "continue"
    STOP_SUCCESS = "stop_success"
    STOP_BUDGET = "stop_budget"
    ESCALATE = "escalate"


class ContinualLoopPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    policy_id: str = Field(pattern=_SAFE_ID)
    target_score: float = Field(default=1.0, ge=0.0, le=1.0)
    maximum_rounds: int = Field(default=8, ge=1, le=10_000)
    maximum_non_improving_rounds: int = Field(default=2, ge=1, le=10_000)
    maximum_forgetting_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    require_zero_safety_violations: bool = True
    policy_hash: str = Field(pattern=_HASH)

    @model_validator(mode="after")
    def validate_hash(self):
        payload = self.model_dump(mode="json", exclude={"policy_hash"})
        if self.policy_hash != canonical_sha256(payload):
            raise ValueError("Continual loop policy hash mismatch.")
        return self


class ContinualLoopDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision_id: str = Field(pattern=_SAFE_ID)
    report_hash: str = Field(pattern=_HASH)
    policy_hash: str = Field(pattern=_HASH)
    completed_rounds: int = Field(ge=0)
    consecutive_non_improving_rounds: int = Field(ge=0)
    action: ContinualLoopAction
    reasons: tuple[str, ...]
    decision_hash: str = Field(pattern=_HASH)

    @model_validator(mode="after")
    def validate_hash(self):
        if not self.reasons:
            raise ValueError("Continual loop decision requires a derived reason.")
        payload = self.model_dump(mode="json", exclude={"decision_hash"})
        if self.decision_hash != canonical_sha256(payload):
            raise ValueError("Continual loop decision hash mismatch.")
        return self


def build_task_spec(task: Task, role: ContinualTaskRole) -> ContinualTaskSpec:
    payload = {"task": task, "role": role}
    return ContinualTaskSpec(**payload, task_hash=canonical_sha256(payload))


def build_task_manifest(
    *,
    manifest_id: str,
    dataset_ref: str,
    revision: str,
    tasks: tuple[ContinualTaskSpec, ...],
    model_id: str,
    seed: int,
    runtime_limits: RuntimeLimits,
    evaluation_budget: ResourceBudget,
) -> ContinualTaskManifest:
    payload = {
        "manifest_id": manifest_id,
        "dataset_ref": dataset_ref,
        "revision": revision,
        "tasks": tasks,
        "model_id": model_id,
        "seed": seed,
        "runtime_limits": runtime_limits,
        "evaluation_budget": evaluation_budget,
        "updates_allowed_during_evaluation": False,
    }
    return ContinualTaskManifest(**payload, manifest_hash=canonical_sha256(payload))


def build_gate_policy(
    policy_id: str = "continual-gate-v1",
    *,
    minimum_target_gain: float = 0.01,
    maximum_retention_drop: float = 0.0,
    maximum_regressions: int = 0,
    require_zero_safety_violations: bool = True,
    maximum_safety_violation_growth: int = 0,
    maximum_tool_call_growth: float = 1.0,
) -> ContinualGatePolicy:
    payload = {
        "policy_id": policy_id,
        "minimum_target_gain": minimum_target_gain,
        "maximum_retention_drop": maximum_retention_drop,
        "maximum_regressions": maximum_regressions,
        "require_zero_safety_violations": require_zero_safety_violations,
        "maximum_safety_violation_growth": maximum_safety_violation_growth,
        "maximum_tool_call_growth": maximum_tool_call_growth,
    }
    return ContinualGatePolicy(**payload, policy_hash=canonical_sha256(payload))


def build_loop_policy(
    policy_id: str = "continual-loop-v1",
    *,
    target_score: float = 1.0,
    maximum_rounds: int = 8,
    maximum_non_improving_rounds: int = 2,
    maximum_forgetting_rate: float = 0.0,
    require_zero_safety_violations: bool = True,
) -> ContinualLoopPolicy:
    payload = {
        "policy_id": policy_id,
        "target_score": target_score,
        "maximum_rounds": maximum_rounds,
        "maximum_non_improving_rounds": maximum_non_improving_rounds,
        "maximum_forgetting_rate": maximum_forgetting_rate,
        "require_zero_safety_violations": require_zero_safety_violations,
    }
    return ContinualLoopPolicy(**payload, policy_hash=canonical_sha256(payload))


def decide_loop_action(
    report: ContinualEvaluationReport,
    *,
    policy: ContinualLoopPolicy,
    completed_rounds: int,
    consecutive_non_improving_rounds: int,
    decision_id: str,
) -> ContinualLoopDecision:
    safety_clean = (
        not policy.require_zero_safety_violations
        or report.safety_violation_count == 0
    )
    if (
        report.overall_score >= policy.target_score
        and report.forgetting_rate <= policy.maximum_forgetting_rate
        and safety_clean
    ):
        action = ContinualLoopAction.STOP_SUCCESS
        reasons = ("target_reached",)
    elif completed_rounds >= policy.maximum_rounds:
        action = ContinualLoopAction.STOP_BUDGET
        reasons = ("maximum_rounds_reached",)
    elif consecutive_non_improving_rounds >= policy.maximum_non_improving_rounds:
        action = ContinualLoopAction.ESCALATE
        reasons = ("non_improvement_limit_reached",)
    else:
        action = ContinualLoopAction.CONTINUE
        reasons = ("additional_evidence_required",)
    payload = {
        "decision_id": decision_id,
        "report_hash": report.report_hash,
        "policy_hash": policy.policy_hash,
        "completed_rounds": completed_rounds,
        "consecutive_non_improving_rounds": consecutive_non_improving_rounds,
        "action": action,
        "reasons": reasons,
    }
    return ContinualLoopDecision(**payload, decision_hash=canonical_sha256(payload))


class UnifiedContinualEvaluator:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._traces: dict[tuple[str, str], ExecutionTrace] = {}

    def evaluate(
        self,
        snapshot: UnifiedAgentSnapshot,
        manifest: ContinualTaskManifest,
        *,
        report_id: str,
        parent: ContinualEvaluationReport | None = None,
    ) -> ContinualEvaluationReport:
        if snapshot.model_id != manifest.model_id:
            raise ValueError("Continual evaluation changed the frozen model.")
        before = snapshot.model_dump_json()
        snapshot_directory = canonical_sha256(snapshot.snapshot_id)[:16]
        runtime = UnifiedDocumentAgentRuntime(
            self.root / f"snapshot-{snapshot_directory}",
            limits=manifest.runtime_limits,
            seed=manifest.seed,
        )
        results = []
        total_wall = 0.0
        for spec in manifest.tasks:
            trace = runtime.run(spec.task, snapshot)
            self._traces[(snapshot.snapshot_id, spec.task.task_id)] = trace
            result = self._result(spec, trace)
            results.append(result)
            total_wall += float(trace.cost.get("wall_seconds", 0.0))
        if before != snapshot.model_dump_json():
            raise RuntimeError("Evaluator mutated a frozen unified snapshot.")
        usage = ResourceUsage(
            task_trials=len(results),
            tokens=sum(int(self._traces[(snapshot.snapshot_id, item.task_id)].cost.get("llm_tokens", 0)) for item in results),
            tool_calls=sum(item.tool_calls for item in results),
            wall_seconds=total_wall,
            cost_usd=sum(float(self._traces[(snapshot.snapshot_id, item.task_id)].cost.get("cost_usd", 0.0)) for item in results),
        )
        if not usage.fits(manifest.evaluation_budget):
            raise ValueError("Continual evaluation exceeded its frozen budget.")
        return self._report(
            report_id=report_id,
            snapshot=snapshot,
            manifest=manifest,
            results=tuple(results),
            usage=usage,
            parent=parent,
        )

    def trace(self, snapshot_id: str, task_id: str) -> ExecutionTrace:
        return self._traces[(snapshot_id, task_id)]

    @staticmethod
    def _result(spec: ContinualTaskSpec, trace: ExecutionTrace) -> ContinualTaskResult:
        verification = tuple(
            item for item in trace.observable_events if item.get("event") == "verification"
        )
        if len(verification) != 1:
            raise RuntimeError("Continual Trace lacks one exact verification event.")
        safety_count = len(verification[0].get("safety_violations", ()))
        stable_trace = trace.model_dump(mode="json")
        stable_trace["cost"] = {
            key: value
            for key, value in stable_trace.get("cost", {}).items()
            if key != "wall_seconds"
        }
        payload = {
            "task_id": spec.task.task_id,
            "task_hash": spec.task_hash,
            "role": spec.role,
            "passed": trace.verifier_passed,
            "score": 1.0 if trace.verifier_passed else 0.0,
            "safety_violation_count": safety_count,
            "tool_calls": int(trace.cost.get("tool_calls", 0.0)),
            "episode_steps": int(trace.cost.get("steps", 0.0)),
            "trace_hash": canonical_sha256(stable_trace),
        }
        return ContinualTaskResult(**payload, result_hash=canonical_sha256(payload))

    @staticmethod
    def _report(
        *,
        report_id: str,
        snapshot: UnifiedAgentSnapshot,
        manifest: ContinualTaskManifest,
        results: tuple[ContinualTaskResult, ...],
        usage: ResourceUsage,
        parent: ContinualEvaluationReport | None,
    ) -> ContinualEvaluationReport:
        parent_by_task = {item.task_id: item for item in parent.results} if parent else {}
        if parent and (
            parent.manifest_hash != manifest.manifest_hash
            or set(parent_by_task) != {item.task_id for item in results}
        ):
            raise ValueError("Parent evaluation used another frozen Task manifest.")
        regressions = sum(
            parent_by_task[item.task_id].passed and not item.passed
            for item in results
            if item.task_id in parent_by_task
        )
        retention_items = tuple(item for item in results if item.role == ContinualTaskRole.RETENTION)
        retention_regressions = sum(
            parent_by_task[item.task_id].passed and not item.passed
            for item in retention_items
            if item.task_id in parent_by_task
        )
        forgetting_rate = (
            retention_regressions / len(retention_items) if retention_items else 0.0
        )
        role_scores = {
            role.value: sum(item.score for item in results if item.role == role)
            / sum(item.role == role for item in results)
            for role in ContinualTaskRole
        }
        payload = {
            "report_id": report_id,
            "snapshot_id": snapshot.snapshot_id,
            "snapshot_hash": snapshot.snapshot_hash,
            "round_index": snapshot.round_index,
            "model_id": snapshot.model_id,
            "manifest_hash": manifest.manifest_hash,
            "parent_report_hash": parent.report_hash if parent else None,
            "results": results,
            "overall_score": sum(item.score for item in results) / len(results),
            "role_scores": role_scores,
            "regression_count": regressions,
            "forgetting_rate": forgetting_rate,
            "safety_violation_count": sum(item.safety_violation_count for item in results),
            "usage": usage,
            "external_benchmark": False,
        }
        return ContinualEvaluationReport(**payload, report_hash=canonical_sha256(payload))


def decide_promotion(
    parent_snapshot: UnifiedAgentSnapshot,
    candidate_snapshot: UnifiedAgentSnapshot,
    parent: ContinualEvaluationReport,
    candidate: ContinualEvaluationReport,
    *,
    policy: ContinualGatePolicy,
    target_roles: tuple[ContinualTaskRole, ...],
    decision_id: str,
) -> ContinualPromotionDecision:
    changed = validate_one_component_transition(parent_snapshot, candidate_snapshot)
    if not target_roles or len(set(target_roles)) != len(target_roles):
        raise ValueError("Promotion requires unique target evaluation roles.")
    if parent.manifest_hash != candidate.manifest_hash:
        raise ValueError("Promotion reports used different frozen manifests.")
    if candidate.parent_report_hash != parent.report_hash:
        raise ValueError("Candidate report does not bind the exact parent report.")
    if parent.snapshot_hash != parent_snapshot.snapshot_hash:
        raise ValueError("Parent report is not bound to the active snapshot.")
    if candidate.snapshot_hash != candidate_snapshot.snapshot_hash:
        raise ValueError("Candidate report is not bound to the candidate snapshot.")
    parent_by_task = {item.task_id: item for item in parent.results}
    candidate_by_task = {item.task_id: item for item in candidate.results}
    if set(parent_by_task) != set(candidate_by_task):
        raise ValueError("Promotion reports do not cover the same frozen Tasks.")
    derived_regressions = sum(
        parent_by_task[task_id].passed and not candidate_by_task[task_id].passed
        for task_id in parent_by_task
    )
    retention_ids = tuple(
        task_id
        for task_id, item in candidate_by_task.items()
        if item.role == ContinualTaskRole.RETENTION
    )
    derived_forgetting = sum(
        parent_by_task[task_id].passed and not candidate_by_task[task_id].passed
        for task_id in retention_ids
    ) / len(retention_ids)
    if (
        candidate.regression_count != derived_regressions
        or abs(candidate.forgetting_rate - derived_forgetting) > 1e-12
    ):
        raise ValueError("Candidate regression/forgetting metrics are not derived.")
    parent_target = sum(parent.role_scores[role] for role in target_roles) / len(target_roles)
    candidate_target = sum(candidate.role_scores[role] for role in target_roles) / len(target_roles)
    target_gain = candidate_target - parent_target
    retention_drop = max(
        0.0,
        parent.role_scores[ContinualTaskRole.RETENTION]
        - candidate.role_scores[ContinualTaskRole.RETENTION],
    )
    reasons: list[str] = []
    if target_gain < policy.minimum_target_gain:
        reasons.append("insufficient_target_gain")
    if retention_drop > policy.maximum_retention_drop:
        reasons.append("retention_drop_exceeded")
    if candidate.regression_count > policy.maximum_regressions:
        reasons.append("task_regression_detected")
    if policy.require_zero_safety_violations and candidate.safety_violation_count:
        reasons.append("safety_violation_detected")
    safety_growth = candidate.safety_violation_count - parent.safety_violation_count
    if safety_growth > policy.maximum_safety_violation_growth:
        reasons.append("safety_violation_growth_exceeded")
    parent_tools = max(parent.usage.tool_calls, 1)
    growth = (candidate.usage.tool_calls - parent.usage.tool_calls) / parent_tools
    if growth > policy.maximum_tool_call_growth:
        reasons.append("tool_call_growth_exceeded")
    payload = {
        "decision_id": decision_id,
        "parent_report_hash": parent.report_hash,
        "candidate_report_hash": candidate.report_hash,
        "candidate_snapshot_hash": candidate_snapshot.snapshot_hash,
        "changed_component": changed,
        "target_roles": target_roles,
        "target_gain": target_gain,
        "retention_drop": retention_drop,
        "regression_count": candidate.regression_count,
        "safety_violation_count": candidate.safety_violation_count,
        "safety_violation_growth": safety_growth,
        "eligible": not reasons,
        "reasons": tuple(reasons),
        "policy_hash": policy.policy_hash,
    }
    return ContinualPromotionDecision(**payload, decision_hash=canonical_sha256(payload))


__all__ = [
    "ContinualEvaluationReport",
    "ContinualGatePolicy",
    "ContinualLoopAction",
    "ContinualLoopDecision",
    "ContinualLoopPolicy",
    "ContinualPromotionDecision",
    "ContinualTaskManifest",
    "ContinualTaskResult",
    "ContinualTaskSpec",
    "UnifiedContinualEvaluator",
    "build_gate_policy",
    "build_loop_policy",
    "build_task_manifest",
    "build_task_spec",
    "decide_promotion",
    "decide_loop_action",
]
