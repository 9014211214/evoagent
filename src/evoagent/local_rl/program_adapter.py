from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evoagent.domain.models import EvolutionAction, FailureLayer
from evoagent.model_registry.models import canonical_sha256, validate_safe_content
from evoagent.program.hashing import program_payload_hash
from evoagent.program.models import (
    AttributionReceipt,
    EvolutionProgramPolicy,
    GenerationOutcome,
    GenerationPlan,
    ProgramAction,
    ProgramDecision,
    ProgramLearningSignal,
)


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SAFE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"
_FORBIDDEN_TRUE_KEYS = {
    "activation_authorized",
    "automatic_activation_authorized",
    "checkpoint_promotion_authorized",
    "deployment_authorized",
    "production_deployment_authorized",
    "production_deployment_performed",
    "upload_performed",
}


class ProgramLocalRLBindingError(ValueError):
    pass


def _require_timezone(value: datetime, *, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone.")
    return value


def _package_payload(package: Any) -> dict[str, Any]:
    if isinstance(package, BaseModel):
        payload = package.model_dump(mode="json")
    elif isinstance(package, Mapping):
        payload = dict(package)
    elif hasattr(package, "model_dump"):
        payload = package.model_dump(mode="json")
    else:
        raise ProgramLocalRLBindingError(
            "Local RL package must expose a JSON-compatible model_dump payload."
        )
    if not isinstance(payload, dict):
        raise ProgramLocalRLBindingError(
            "Local RL package payload must be a JSON object."
        )
    validate_safe_content(payload)
    return payload


def _verify_generic_package_hash(payload: Mapping[str, Any]) -> str:
    package_hash = payload.get("package_hash")
    if (
        not isinstance(package_hash, str)
        or len(package_hash) != 64
        or any(character not in "0123456789abcdef" for character in package_hash)
    ):
        raise ProgramLocalRLBindingError(
            "Local RL package lacks a lowercase SHA-256 package_hash."
        )
    expected = program_payload_hash(
        {key: value for key, value in payload.items() if key != "package_hash"}
    )
    if package_hash != expected:
        raise ProgramLocalRLBindingError(
            "Local RL package hash differs from its normalized payload."
        )
    return package_hash


def _walk(value: Any, *, path: tuple[str, ...] = ()):
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            yield from _walk(item, path=(*path, key_text))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _walk(item, path=(*path, str(index)))
    else:
        yield path, value


def _all_string_values(payload: Mapping[str, Any]) -> frozenset[str]:
    return frozenset(
        value
        for _, value in _walk(payload)
        if isinstance(value, str)
    )


def _verify_no_implicit_authority(payload: Mapping[str, Any]) -> None:
    violations = tuple(
        ".".join(path)
        for path, value in _walk(payload)
        if path and path[-1] in _FORBIDDEN_TRUE_KEYS and value is not False
    )
    if violations:
        raise ProgramLocalRLBindingError(
            "Local RL package contains forbidden activation, promotion, deployment, "
            f"or upload authority: {', '.join(violations)}."
        )


def _local_package_id(payload: Mapping[str, Any]) -> str:
    for key in ("package_id", "run_id", "training_run_id", "experiment_id"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    raise ProgramLocalRLBindingError(
        "Local RL package requires package_id, run_id, training_run_id, or experiment_id."
    )


class ProgramLocalRLIntent(BaseModel):
    model_config = ConfigDict(frozen=True)

    intent_id: str = Field(pattern=_SAFE_ID_PATTERN)
    program_id: str = Field(pattern=_SAFE_ID_PATTERN)
    generation_id: str = Field(pattern=_SAFE_ID_PATTERN)
    generation_index: int = Field(ge=1)
    parent_generation_id: str = Field(pattern=_SAFE_ID_PATTERN)
    policy_id: str = Field(pattern=_SAFE_ID_PATTERN)
    policy_hash: str = Field(pattern=_SHA256_PATTERN)
    parent_outcome_id: str = Field(pattern=_SAFE_ID_PATTERN)
    parent_outcome_hash: str = Field(pattern=_SHA256_PATTERN)
    signal_id: str = Field(pattern=_SAFE_ID_PATTERN)
    signal_hash: str = Field(pattern=_SHA256_PATTERN)
    attribution_receipt_id: str = Field(pattern=_SAFE_ID_PATTERN)
    attribution_receipt_hash: str = Field(pattern=_SHA256_PATTERN)
    continue_decision_id: str = Field(pattern=_SAFE_ID_PATTERN)
    continue_decision_hash: str = Field(pattern=_SHA256_PATTERN)
    generation_plan_id: str = Field(pattern=_SAFE_ID_PATTERN)
    generation_plan_hash: str = Field(pattern=_SHA256_PATTERN)
    intervention_layer: FailureLayer
    intervention_action: EvolutionAction
    parent_agent_identity_hash: str = Field(pattern=_SHA256_PATTERN)
    target_agent_identity_hash: str = Field(pattern=_SHA256_PATTERN)
    expected_release_package_hash: str = Field(pattern=_SHA256_PATTERN)
    expected_release_plan_hash: str = Field(pattern=_SHA256_PATTERN)
    local_rl_run_id: str = Field(pattern=_SAFE_ID_PATTERN)
    local_rl_spec_hash: str = Field(pattern=_SHA256_PATTERN)
    release_evidence_producer_id: str = Field(pattern=_SAFE_ID_PATTERN)
    attributor_id: str = Field(pattern=_SAFE_ID_PATTERN)
    decision_planner_id: str = Field(pattern=_SAFE_ID_PATTERN)
    created_by: str = Field(pattern=_SAFE_ID_PATTERN)
    created_at: datetime
    optimizer_execution_authorized: Literal[False] = False
    checkpoint_promotion_authorized: Literal[False] = False
    activation_authorized: Literal[False] = False
    production_deployment_authorized: Literal[False] = False
    intent_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("created_at")
    @classmethod
    def timezone(cls, value: datetime) -> datetime:
        return _require_timezone(value, label="Program-to-local-RL intent time")

    @model_validator(mode="after")
    def validate_intent(self):
        if self.parent_agent_identity_hash == self.target_agent_identity_hash:
            raise ValueError("Local RL intent target identity must differ from its parent.")
        payload = self.model_dump(mode="json", exclude={"intent_hash"})
        validate_safe_content(payload)
        if self.intent_hash != program_payload_hash(payload):
            raise ValueError("Program-to-local-RL intent hash mismatch.")
        return self


class ProgramLocalRLExecutionAuthorization(BaseModel):
    model_config = ConfigDict(frozen=True)

    authorization_id: str = Field(pattern=_SAFE_ID_PATTERN)
    intent_id: str = Field(pattern=_SAFE_ID_PATTERN)
    intent_hash: str = Field(pattern=_SHA256_PATTERN)
    approval_actor_ids: tuple[str, ...] = Field(min_length=2)
    approved_at: datetime
    max_optimizer_iterations: int = Field(ge=1)
    max_rollouts: int = Field(ge=1)
    max_unsafe_action_count: int = Field(default=0, ge=0)
    max_regression_count: int = Field(default=0, ge=0)
    local_optimizer_execution_authorized: Literal[True] = True
    foundation_model_training_authorized: Literal[False] = False
    checkpoint_promotion_authorized: Literal[False] = False
    activation_authorized: Literal[False] = False
    production_deployment_authorized: Literal[False] = False
    authorization_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("approved_at")
    @classmethod
    def timezone(cls, value: datetime) -> datetime:
        return _require_timezone(value, label="Program-to-local-RL authorization time")

    @model_validator(mode="after")
    def validate_authorization(self):
        if len(set(self.approval_actor_ids)) != len(self.approval_actor_ids):
            raise ValueError("Local RL authorization approval actors must be unique.")
        payload = self.model_dump(mode="json", exclude={"authorization_hash"})
        validate_safe_content(payload)
        if self.authorization_hash != program_payload_hash(payload):
            raise ValueError("Program-to-local-RL authorization hash mismatch.")
        return self


class ProgramLocalRLEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    evidence_id: str = Field(pattern=_SAFE_ID_PATTERN)
    intent_id: str = Field(pattern=_SAFE_ID_PATTERN)
    intent_hash: str = Field(pattern=_SHA256_PATTERN)
    authorization_id: str = Field(pattern=_SAFE_ID_PATTERN)
    authorization_hash: str = Field(pattern=_SHA256_PATTERN)
    local_rl_package_id: str = Field(pattern=_SAFE_ID_PATTERN)
    local_rl_package_hash: str = Field(pattern=_SHA256_PATTERN)
    initial_checkpoint_hash: str = Field(pattern=_SHA256_PATTERN)
    selected_checkpoint_hash: str = Field(pattern=_SHA256_PATTERN)
    held_out_evaluation_hash: str = Field(pattern=_SHA256_PATTERN)
    optimizer_audit_checkpoint_hash: str = Field(pattern=_SHA256_PATTERN)
    optimizer_iterations: int = Field(ge=1)
    rollout_count: int = Field(ge=1)
    unsafe_action_count: int = Field(ge=0)
    regression_count: int = Field(ge=0)
    selected_strictly_improved: bool
    held_out_gate_passed: bool
    evidence_producer_id: str = Field(pattern=_SAFE_ID_PATTERN)
    completed_at: datetime
    local_optimizer_executed: Literal[True] = True
    foundation_model_weights_modified: Literal[False] = False
    external_model_call_performed: Literal[False] = False
    production_traffic_observed: Literal[False] = False
    checkpoint_promotion_authorized: Literal[False] = False
    activation_authorized: Literal[False] = False
    production_deployment_authorized: Literal[False] = False
    evidence_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("completed_at")
    @classmethod
    def timezone(cls, value: datetime) -> datetime:
        return _require_timezone(value, label="Program-to-local-RL evidence time")

    @model_validator(mode="after")
    def validate_evidence(self):
        if self.initial_checkpoint_hash == self.selected_checkpoint_hash:
            raise ValueError("Local RL evidence requires an actually changed checkpoint.")
        expected_gate = (
            self.selected_strictly_improved
            and self.unsafe_action_count == 0
            and self.regression_count == 0
        )
        if self.held_out_gate_passed != expected_gate:
            raise ValueError("Local RL held-out gate result differs from its evidence.")
        payload = self.model_dump(mode="json", exclude={"evidence_hash"})
        validate_safe_content(payload)
        if self.evidence_hash != program_payload_hash(payload):
            raise ValueError("Program-to-local-RL evidence hash mismatch.")
        return self


class ProgramLocalRLBindingPackage(BaseModel):
    model_config = ConfigDict(frozen=True)

    package_id: str = Field(pattern=_SAFE_ID_PATTERN)
    framework_version: str
    source_repository: str
    source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    intent: ProgramLocalRLIntent
    authorization: ProgramLocalRLExecutionAuthorization
    evidence: ProgramLocalRLEvidence
    created_at: datetime
    optimizer_execution_authorized_by_package: Literal[False] = False
    checkpoint_promotion_authorized: Literal[False] = False
    activation_authorized: Literal[False] = False
    production_deployment_authorized: Literal[False] = False
    package_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("created_at")
    @classmethod
    def timezone(cls, value: datetime) -> datetime:
        return _require_timezone(value, label="Program/local-RL binding package time")

    @model_validator(mode="after")
    def validate_package(self):
        if (
            self.authorization.intent_id != self.intent.intent_id
            or self.authorization.intent_hash != self.intent.intent_hash
        ):
            raise ValueError("Local RL authorization is bound to another Program intent.")
        if (
            self.evidence.intent_id != self.intent.intent_id
            or self.evidence.intent_hash != self.intent.intent_hash
            or self.evidence.authorization_id != self.authorization.authorization_id
            or self.evidence.authorization_hash != self.authorization.authorization_hash
        ):
            raise ValueError(
                "Local RL evidence is bound to another Program intent or authorization."
            )
        if self.created_at < self.evidence.completed_at:
            raise ValueError("Binding package predates its local RL evidence.")
        payload = self.model_dump(mode="json", exclude={"package_hash"})
        validate_safe_content(payload)
        if self.package_hash != program_payload_hash(payload):
            raise ValueError("Program/local-RL binding package hash mismatch.")
        return self


class ProgramLocalRLBindingPackageManager:
    """Verify and serialize evidence binding without granting execution or promotion."""

    def verify(self, package: ProgramLocalRLBindingPackage) -> bool:
        ProgramLocalRLIntent.model_validate(package.intent.model_dump(mode="json"))
        ProgramLocalRLExecutionAuthorization.model_validate(
            package.authorization.model_dump(mode="json")
        )
        ProgramLocalRLEvidence.model_validate(package.evidence.model_dump(mode="json"))
        ProgramLocalRLBindingPackage.model_validate(package.model_dump(mode="json"))
        forbidden = (
            package.intent.optimizer_execution_authorized,
            package.intent.checkpoint_promotion_authorized,
            package.intent.activation_authorized,
            package.intent.production_deployment_authorized,
            package.evidence.checkpoint_promotion_authorized,
            package.evidence.activation_authorized,
            package.evidence.production_deployment_authorized,
            package.optimizer_execution_authorized_by_package,
            package.checkpoint_promotion_authorized,
            package.activation_authorized,
            package.production_deployment_authorized,
        )
        if any(forbidden):
            raise ProgramLocalRLBindingError(
                "Program/local-RL binding package cannot authorize execution, promotion, "
                "activation, or deployment."
            )
        return True

    def export_file(
        self,
        package: ProgramLocalRLBindingPackage,
        path: str | Path,
    ) -> Path:
        self.verify(package)
        target = Path(path)
        if target.exists() and target.is_symlink():
            raise ProgramLocalRLBindingError(
                "Program/local-RL package path must not be a symlink."
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        encoded = (
            json.dumps(package.model_dump(mode="json"), sort_keys=True, indent=2)
            + "\n"
        )
        if target.exists() and target.read_text(encoding="utf-8") != encoded:
            raise ProgramLocalRLBindingError(
                "Existing Program/local-RL package differs from immutable evidence."
            )
        target.write_text(encoded, encoding="utf-8")
        return target

    def load_file(self, path: str | Path) -> ProgramLocalRLBindingPackage:
        source = Path(path)
        if source.is_symlink():
            raise ProgramLocalRLBindingError(
                "Program/local-RL package path must not be a symlink."
            )
        package = ProgramLocalRLBindingPackage.model_validate_json(
            source.read_text(encoding="utf-8")
        )
        self.verify(package)
        return package


def build_program_local_rl_intent(
    *,
    intent_id: str,
    policy: EvolutionProgramPolicy,
    parent_outcome: GenerationOutcome,
    signal: ProgramLearningSignal,
    attribution: AttributionReceipt,
    decision: ProgramDecision,
    plan: GenerationPlan,
    local_rl_run_id: str,
    local_rl_spec: Mapping[str, Any],
    created_by: str,
    created_at: datetime,
) -> ProgramLocalRLIntent:
    if decision.action != ProgramAction.CONTINUE:
        raise ProgramLocalRLBindingError(
            "Local RL intent requires an exact CONTINUE Program decision."
        )
    if (
        decision.program_id != parent_outcome.program_id
        or decision.generation_id != parent_outcome.generation_id
        or decision.generation_index != parent_outcome.generation_index
        or decision.source_outcome_hash != parent_outcome.outcome_hash
    ):
        raise ProgramLocalRLBindingError(
            "CONTINUE decision differs from the exact parent GenerationOutcome."
        )
    if (
        signal.program_id != parent_outcome.program_id
        or signal.generation_index != parent_outcome.generation_index
        or signal.source_release_package_hash != parent_outcome.release_package_hash
        or signal.source_release_plan_hash != parent_outcome.release_plan_hash
        or signal.runtime_config_sha256 != parent_outcome.runtime_config_sha256
        or signal.tool_contract_sha256 != parent_outcome.tool_contract_sha256
    ):
        raise ProgramLocalRLBindingError(
            "Learning signal differs from the exact parent release outcome."
        )
    if (
        attribution.signal_id != signal.signal_id
        or attribution.signal_hash != signal.signal_hash
        or attribution.attributor_id == signal.evidence_producer_id
    ):
        raise ProgramLocalRLBindingError(
            "Attribution is not exactly bound and independent."
        )
    if (
        attribution.failure_layer not in policy.allowed_automatic_layers
        or attribution.confidence < policy.minimum_attribution_confidence
        or (
            policy.require_single_supported_experiment
            and len(attribution.supported_experiment_hashes) != 1
        )
    ):
        raise ProgramLocalRLBindingError(
            "Attribution does not satisfy the immutable Program policy."
        )
    if (
        plan.program_id != parent_outcome.program_id
        or plan.parent_generation_id != parent_outcome.generation_id
        or plan.generation_index != parent_outcome.generation_index + 1
        or plan.generation_index != decision.next_generation_index
        or plan.source_signal_id != signal.signal_id
        or plan.source_signal_hash != signal.signal_hash
        or plan.attribution_receipt_id != attribution.receipt_id
        or plan.attribution_receipt_hash != attribution.receipt_hash
        or plan.intervention_layer != attribution.failure_layer
        or plan.intervention_action != attribution.action
        or plan.parent_agent_identity_hash != parent_outcome.agent_identity_hash
        or plan.created_by != decision.decided_by
    ):
        raise ProgramLocalRLBindingError(
            "GenerationPlan differs from its decision, signal, attribution, or parent identity."
        )
    if (
        plan.external_execution_authorized
        or plan.production_deployment_authorized
    ):
        raise ProgramLocalRLBindingError(
            "GenerationPlan cannot carry external execution or deployment authority."
        )
    if created_by in {
        signal.evidence_producer_id,
        attribution.attributor_id,
        plan.created_by,
    }:
        raise ProgramLocalRLBindingError(
            "Local RL binding actor must differ from evidence, attribution, and planning actors."
        )
    timeline = (
        parent_outcome.completed_at,
        signal.created_at,
        attribution.created_at,
        decision.decided_at,
        plan.created_at,
        created_at,
    )
    if timeline != tuple(sorted(timeline)):
        raise ProgramLocalRLBindingError(
            "Program-to-local-RL intent chronology is not monotonic."
        )
    spec_payload = dict(local_rl_spec)
    validate_safe_content(spec_payload)
    payload = {
        "intent_id": intent_id,
        "program_id": plan.program_id,
        "generation_id": plan.generation_id,
        "generation_index": plan.generation_index,
        "parent_generation_id": plan.parent_generation_id,
        "policy_id": policy.policy_id,
        "policy_hash": policy.policy_hash,
        "parent_outcome_id": parent_outcome.outcome_id,
        "parent_outcome_hash": parent_outcome.outcome_hash,
        "signal_id": signal.signal_id,
        "signal_hash": signal.signal_hash,
        "attribution_receipt_id": attribution.receipt_id,
        "attribution_receipt_hash": attribution.receipt_hash,
        "continue_decision_id": decision.decision_id,
        "continue_decision_hash": decision.decision_hash,
        "generation_plan_id": plan.plan_id,
        "generation_plan_hash": plan.plan_hash,
        "intervention_layer": plan.intervention_layer,
        "intervention_action": plan.intervention_action,
        "parent_agent_identity_hash": plan.parent_agent_identity_hash,
        "target_agent_identity_hash": plan.target_agent_identity_hash,
        "expected_release_package_hash": plan.expected_release_package_hash,
        "expected_release_plan_hash": plan.expected_release_plan_hash,
        "local_rl_run_id": local_rl_run_id,
        "local_rl_spec_hash": program_payload_hash(spec_payload),
        "release_evidence_producer_id": signal.evidence_producer_id,
        "attributor_id": attribution.attributor_id,
        "decision_planner_id": plan.created_by,
        "created_by": created_by,
        "created_at": created_at,
        "optimizer_execution_authorized": False,
        "checkpoint_promotion_authorized": False,
        "activation_authorized": False,
        "production_deployment_authorized": False,
    }
    return ProgramLocalRLIntent(
        **payload,
        intent_hash=program_payload_hash(payload),
    )


def build_program_local_rl_execution_authorization(
    intent: ProgramLocalRLIntent,
    *,
    authorization_id: str,
    approval_actor_ids: tuple[str, ...],
    approved_at: datetime,
    max_optimizer_iterations: int,
    max_rollouts: int,
    max_unsafe_action_count: int = 0,
    max_regression_count: int = 0,
) -> ProgramLocalRLExecutionAuthorization:
    if approved_at < intent.created_at:
        raise ProgramLocalRLBindingError(
            "Local RL execution authorization predates its Program intent."
        )
    if len(approval_actor_ids) < 2 or len(set(approval_actor_ids)) != len(
        approval_actor_ids
    ):
        raise ProgramLocalRLBindingError(
            "Local RL execution authorization requires two unique approval actors."
        )
    disallowed = {
        intent.release_evidence_producer_id,
        intent.attributor_id,
        intent.decision_planner_id,
        intent.created_by,
    }
    if any(actor_id in disallowed for actor_id in approval_actor_ids):
        raise ProgramLocalRLBindingError(
            "Local RL execution approval actors must be independent from Program actors."
        )
    payload = {
        "authorization_id": authorization_id,
        "intent_id": intent.intent_id,
        "intent_hash": intent.intent_hash,
        "approval_actor_ids": approval_actor_ids,
        "approved_at": approved_at,
        "max_optimizer_iterations": max_optimizer_iterations,
        "max_rollouts": max_rollouts,
        "max_unsafe_action_count": max_unsafe_action_count,
        "max_regression_count": max_regression_count,
        "local_optimizer_execution_authorized": True,
        "foundation_model_training_authorized": False,
        "checkpoint_promotion_authorized": False,
        "activation_authorized": False,
        "production_deployment_authorized": False,
    }
    return ProgramLocalRLExecutionAuthorization(
        **payload,
        authorization_hash=program_payload_hash(payload),
    )


def build_program_local_rl_evidence(
    intent: ProgramLocalRLIntent,
    authorization: ProgramLocalRLExecutionAuthorization,
    local_rl_package: Any,
    *,
    evidence_id: str,
    initial_checkpoint_hash: str,
    selected_checkpoint_hash: str,
    held_out_evaluation_hash: str,
    optimizer_audit_checkpoint_hash: str,
    optimizer_iterations: int,
    rollout_count: int,
    unsafe_action_count: int,
    regression_count: int,
    selected_strictly_improved: bool,
    evidence_producer_id: str,
    completed_at: datetime,
) -> ProgramLocalRLEvidence:
    if (
        authorization.intent_id != intent.intent_id
        or authorization.intent_hash != intent.intent_hash
        or not authorization.local_optimizer_execution_authorized
    ):
        raise ProgramLocalRLBindingError(
            "Local RL execution authorization is not bound to this Program intent."
        )
    if (
        optimizer_iterations > authorization.max_optimizer_iterations
        or rollout_count > authorization.max_rollouts
        or unsafe_action_count > authorization.max_unsafe_action_count
        or regression_count > authorization.max_regression_count
    ):
        raise ProgramLocalRLBindingError(
            "Local RL execution exceeded its separate authorization."
        )
    payload_view = _package_payload(local_rl_package)
    package_hash = _verify_generic_package_hash(payload_view)
    _verify_no_implicit_authority(payload_view)
    values = _all_string_values(payload_view)
    required_hashes = {
        initial_checkpoint_hash,
        selected_checkpoint_hash,
        held_out_evaluation_hash,
        optimizer_audit_checkpoint_hash,
    }
    if not required_hashes.issubset(values):
        raise ProgramLocalRLBindingError(
            "Local RL binding hashes are not all present in the verified package payload."
        )
    if evidence_producer_id in {
        intent.release_evidence_producer_id,
        intent.attributor_id,
        intent.decision_planner_id,
        intent.created_by,
        *authorization.approval_actor_ids,
    }:
        raise ProgramLocalRLBindingError(
            "Local RL evidence producer is not independent from Program control actors."
        )
    if completed_at < authorization.approved_at:
        raise ProgramLocalRLBindingError(
            "Local RL evidence predates its execution authorization."
        )
    held_out_gate_passed = (
        selected_strictly_improved
        and unsafe_action_count == 0
        and regression_count == 0
    )
    payload = {
        "evidence_id": evidence_id,
        "intent_id": intent.intent_id,
        "intent_hash": intent.intent_hash,
        "authorization_id": authorization.authorization_id,
        "authorization_hash": authorization.authorization_hash,
        "local_rl_package_id": _local_package_id(payload_view),
        "local_rl_package_hash": package_hash,
        "initial_checkpoint_hash": initial_checkpoint_hash,
        "selected_checkpoint_hash": selected_checkpoint_hash,
        "held_out_evaluation_hash": held_out_evaluation_hash,
        "optimizer_audit_checkpoint_hash": optimizer_audit_checkpoint_hash,
        "optimizer_iterations": optimizer_iterations,
        "rollout_count": rollout_count,
        "unsafe_action_count": unsafe_action_count,
        "regression_count": regression_count,
        "selected_strictly_improved": selected_strictly_improved,
        "held_out_gate_passed": held_out_gate_passed,
        "evidence_producer_id": evidence_producer_id,
        "completed_at": completed_at,
        "local_optimizer_executed": True,
        "foundation_model_weights_modified": False,
        "external_model_call_performed": False,
        "production_traffic_observed": False,
        "checkpoint_promotion_authorized": False,
        "activation_authorized": False,
        "production_deployment_authorized": False,
    }
    return ProgramLocalRLEvidence(
        **payload,
        evidence_hash=program_payload_hash(payload),
    )


def build_program_local_rl_binding_package(
    *,
    package_id: str,
    framework_version: str,
    source_repository: str,
    source_commit: str,
    intent: ProgramLocalRLIntent,
    authorization: ProgramLocalRLExecutionAuthorization,
    evidence: ProgramLocalRLEvidence,
    created_at: datetime,
) -> ProgramLocalRLBindingPackage:
    payload = {
        "package_id": package_id,
        "framework_version": framework_version,
        "source_repository": source_repository,
        "source_commit": source_commit,
        "intent": intent,
        "authorization": authorization,
        "evidence": evidence,
        "created_at": created_at,
        "optimizer_execution_authorized_by_package": False,
        "checkpoint_promotion_authorized": False,
        "activation_authorized": False,
        "production_deployment_authorized": False,
    }
    package = ProgramLocalRLBindingPackage(
        **payload,
        package_hash=program_payload_hash(payload),
    )
    ProgramLocalRLBindingPackageManager().verify(package)
    return package


__all__ = [
    "ProgramLocalRLBindingError",
    "ProgramLocalRLBindingPackage",
    "ProgramLocalRLBindingPackageManager",
    "ProgramLocalRLEvidence",
    "ProgramLocalRLExecutionAuthorization",
    "ProgramLocalRLIntent",
    "build_program_local_rl_binding_package",
    "build_program_local_rl_evidence",
    "build_program_local_rl_execution_authorization",
    "build_program_local_rl_intent",
]
