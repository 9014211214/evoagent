from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evoagent.campaigns import (
    ApprovalDecision,
    CampaignApproval,
    CampaignRecord,
    CampaignRisk,
    CampaignState,
    CampaignType,
    fingerprint_payload,
)
from evoagent.local_rl.models import (
    LocalRLCheckpointStatus,
    LocalRLRunManifest,
    LocalPolicyCheckpoint,
)
from evoagent.local_rl.package import (
    LocalRLPackageManager,
    LocalRLPackageManifest,
)
from evoagent.local_rl.policy import TabularSoftmaxPolicy
from evoagent.model_registry.models import canonical_sha256, validate_safe_content
from evoagent.program.models import (
    AttributionReceipt,
    EvolutionProgramPolicy,
    GenerationPlan,
    GenerationRecord,
    GenerationStatus,
    ProgramAction,
    ProgramDecision,
    ProgramHead,
    ProgramLearningSignal,
    ProgramRecord,
    ProgramState,
)


_SHA1_PATTERN = r"^[0-9a-f]{40}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SAFE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"
_TICKET_FORMAT = "evoagent-program-local-rl-ticket-v1"
_RECEIPT_FORMAT = "evoagent-program-local-rl-receipt-v1"
_BOUND_PACKAGE_FORMAT = "evoagent-program-bound-local-rl-package-v1"
_LOCAL_PACKAGE_FORMAT = "evoagent-local-agentic-rl-package-v1"
_TINY_POLICY_KIND = "tiny_tabular_agent_policy"


class ProgramLocalRLBindingError(ValueError):
    pass


def _timezone(value: datetime, *, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone.")
    return value


def _program_budget_hash(policy: EvolutionProgramPolicy) -> str:
    return canonical_sha256(policy.budget.model_dump(mode="json"))


def _generation_budget_hash(plan: GenerationPlan) -> str:
    return canonical_sha256(plan.budget.model_dump(mode="json"))


def _program_budget_snapshot_payload(
    policy: EvolutionProgramPolicy,
    head: ProgramHead,
) -> dict:
    budget = policy.budget
    return {
        "policy_budget": budget.model_dump(mode="json"),
        "head": {
            "current_generation_index": head.current_generation_index,
            "rollback_count": head.rollback_count,
            "hold_count": head.hold_count,
            "generation_campaign_count": head.generation_campaign_count,
            "total_pairs": head.total_pairs,
            "total_tokens": head.total_tokens,
            "total_cost_usd": head.total_cost_usd,
        },
        "remaining": {
            "generations": budget.max_generations
            - (head.current_generation_index + 1),
            "rollbacks": budget.max_rollbacks - head.rollback_count,
            "holds": budget.max_holds - head.hold_count,
            "generation_campaigns": budget.max_generation_campaigns
            - head.generation_campaign_count,
            "pairs": budget.max_total_pairs - head.total_pairs,
            "tokens": budget.max_total_tokens - head.total_tokens,
            "cost_usd": budget.max_total_cost_usd - head.total_cost_usd,
        },
    }


def _program_budget_snapshot_hash(
    policy: EvolutionProgramPolicy,
    head: ProgramHead,
) -> str:
    return canonical_sha256(_program_budget_snapshot_payload(policy, head))


def _task_manifest_hash(manifest: LocalRLRunManifest) -> str:
    return canonical_sha256(
        {
            "training_task_hashes": [
                item.task_hash for item in manifest.training_tasks
            ],
            "held_out_task_hashes": [
                item.task_hash for item in manifest.held_out_tasks
            ],
        }
    )


def _expected_initial_checkpoint(
    manifest: LocalRLRunManifest,
) -> LocalPolicyCheckpoint:
    return TabularSoftmaxPolicy.initial(manifest.environment).checkpoint(
        checkpoint_id=f"{manifest.run_id}:checkpoint:0",
        run_id=manifest.run_id,
        iteration=0,
        parent_checkpoint_hash=None,
        status=LocalRLCheckpointStatus.INITIAL,
    )


def _campaign_target_key(plan: GenerationPlan) -> str:
    return (
        f"evolution-generation:{plan.program_id}:"
        f"{plan.parent_generation_id}->{plan.generation_id}"
    )


def _campaign_candidate_ref(plan: GenerationPlan) -> str:
    return f"program-generation:{plan.program_id}:{plan.generation_id}"


def _campaign_fingerprint_source(
    policy: EvolutionProgramPolicy,
    signal: ProgramLearningSignal,
    attribution: AttributionReceipt,
    plan: GenerationPlan,
) -> dict:
    return {
        "program_policy_hash": policy.policy_hash,
        "signal_hash": signal.signal_hash,
        "attribution_receipt_hash": attribution.receipt_hash,
        "generation_plan_hash": plan.plan_hash,
        "expected_release_package_hash": plan.expected_release_package_hash,
        "generation_budget": plan.budget.model_dump(mode="json"),
    }


def _campaign_metadata(
    policy: EvolutionProgramPolicy,
    signal: ProgramLearningSignal,
    attribution: AttributionReceipt,
    plan: GenerationPlan,
) -> dict:
    return {
        "program_id": plan.program_id,
        "generation_id": plan.generation_id,
        "generation_index": plan.generation_index,
        "parent_generation_id": plan.parent_generation_id,
        "policy_hash": policy.policy_hash,
        "signal_id": signal.signal_id,
        "attribution_receipt_id": attribution.receipt_id,
        "external_execution_performed": False,
        "production_deployment_performed": False,
    }


def _campaign_artifact(
    policy: EvolutionProgramPolicy,
    signal: ProgramLearningSignal,
    attribution: AttributionReceipt,
    plan: GenerationPlan,
) -> dict:
    return {
        "kind": "evolution_generation_candidate",
        "policy": policy.model_dump(mode="json"),
        "signal": signal.model_dump(mode="json"),
        "attribution": attribution.model_dump(mode="json"),
        "plan": plan.model_dump(mode="json"),
        "external_execution_performed": False,
        "production_deployment_performed": False,
    }


class ProgramLocalRLExecutionTicket(BaseModel):
    model_config = ConfigDict(frozen=True)

    format_version: Literal[_TICKET_FORMAT] = _TICKET_FORMAT
    ticket_id: str = Field(pattern=_SAFE_ID_PATTERN)
    authorized_at: datetime
    authorized_by: str = Field(pattern=_SAFE_ID_PATTERN)
    program: ProgramRecord
    head: ProgramHead
    policy: EvolutionProgramPolicy
    signal: ProgramLearningSignal
    attribution: AttributionReceipt
    continue_decision: ProgramDecision
    generation_plan: GenerationPlan
    generation: GenerationRecord
    campaign: CampaignRecord
    approvals: tuple[CampaignApproval, ...]
    local_rl_manifest: LocalRLRunManifest
    program_budget_hash: str = Field(pattern=_SHA256_PATTERN)
    cumulative_program_budget_snapshot_hash: str = Field(
        pattern=_SHA256_PATTERN
    )
    generation_budget_hash: str = Field(pattern=_SHA256_PATTERN)
    local_rl_task_manifest_hash: str = Field(pattern=_SHA256_PATTERN)
    expected_initial_checkpoint_hash: str = Field(pattern=_SHA256_PATTERN)
    ticket_hash: str = Field(pattern=_SHA256_PATTERN)
    artifact_kind: Literal[_TINY_POLICY_KIND] = _TINY_POLICY_KIND
    program_generation_running_attested: Literal[True] = True
    local_optimizer_execution_authorized: Literal[True] = True
    program_pair_token_cost_consumption_claimed: Literal[False] = False
    selected_checkpoint_satisfies_generation_outcome: Literal[False] = False
    release_evaluation_still_required: Literal[True] = True
    checkpoint_promotion_authorized: Literal[False] = False
    checkpoint_activation_authorized: Literal[False] = False
    foundation_model_training_authorized: Literal[False] = False
    external_execution_authorized: Literal[False] = False
    production_deployment_authorized: Literal[False] = False

    @field_validator("authorized_at")
    @classmethod
    def validate_time(cls, value: datetime) -> datetime:
        return _timezone(value, label="Program Local RL authorization time")

    @model_validator(mode="after")
    def validate_ticket(self):
        _verify_ticket_evidence(self)
        payload = self.model_dump(mode="json", exclude={"ticket_hash"})
        validate_safe_content(payload)
        if self.ticket_hash != canonical_sha256(payload):
            raise ValueError("Program Local RL execution ticket hash mismatch.")
        return self


class ProgramLocalRLCompletionReceipt(BaseModel):
    model_config = ConfigDict(frozen=True)

    format_version: Literal[_RECEIPT_FORMAT] = _RECEIPT_FORMAT
    receipt_id: str = Field(pattern=_SAFE_ID_PATTERN)
    ticket_id: str = Field(pattern=_SAFE_ID_PATTERN)
    ticket_hash: str = Field(pattern=_SHA256_PATTERN)
    local_rl_package_id: str = Field(pattern=_SAFE_ID_PATTERN)
    local_rl_package_hash: str = Field(pattern=_SHA256_PATTERN)
    run_id: str = Field(pattern=_SAFE_ID_PATTERN)
    manifest_hash: str = Field(pattern=_SHA256_PATTERN)
    training_result_hash: str = Field(pattern=_SHA256_PATTERN)
    initial_checkpoint_hash: str = Field(pattern=_SHA256_PATTERN)
    selected_checkpoint_id: str = Field(pattern=_SAFE_ID_PATTERN)
    selected_checkpoint_hash: str = Field(pattern=_SHA256_PATTERN)
    selected_iteration: int = Field(gt=0)
    selected_report_hash: str = Field(pattern=_SHA256_PATTERN)
    selection_decision_hash: str = Field(pattern=_SHA256_PATTERN)
    training_usage_hash: str = Field(pattern=_SHA256_PATTERN)
    trainer_id: str = Field(pattern=_SAFE_ID_PATTERN)
    evaluator_id: str = Field(pattern=_SAFE_ID_PATTERN)
    selection_actor_id: str = Field(pattern=_SAFE_ID_PATTERN)
    completed_by: str = Field(pattern=_SAFE_ID_PATTERN)
    completed_at: datetime
    receipt_hash: str = Field(pattern=_SHA256_PATTERN)
    local_optimizer_execution_completed: Literal[True] = True
    strict_held_out_improvement_verified: Literal[True] = True
    zero_unsafe_held_out_actions_verified: Literal[True] = True
    checkpoint_promotion_authorized: Literal[False] = False
    checkpoint_activation_authorized: Literal[False] = False
    release_authorized: Literal[False] = False
    production_deployment_authorized: Literal[False] = False

    @field_validator("completed_at")
    @classmethod
    def validate_time(cls, value: datetime) -> datetime:
        return _timezone(value, label="Program Local RL completion time")

    @model_validator(mode="after")
    def validate_receipt(self):
        _verify_receipt_authority_boundary(self)
        payload = self.model_dump(mode="json", exclude={"receipt_hash"})
        validate_safe_content(payload)
        if self.receipt_hash != canonical_sha256(payload):
            raise ValueError("Program Local RL completion receipt hash mismatch.")
        return self


class ProgramBoundLocalRLPackageManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    format_version: Literal[_BOUND_PACKAGE_FORMAT] = _BOUND_PACKAGE_FORMAT
    package_id: str = Field(pattern=_SAFE_ID_PATTERN)
    created_at: datetime
    framework_version: str
    source_repository: str
    source_commit: str = Field(pattern=_SHA1_PATTERN)
    third_party_lock_hash: str = Field(pattern=_SHA256_PATTERN)
    ticket: ProgramLocalRLExecutionTicket
    local_rl_package: LocalRLPackageManifest
    receipt: ProgramLocalRLCompletionReceipt
    package_hash: str = Field(pattern=_SHA256_PATTERN)
    local_policy_evidence_only: Literal[True] = True
    generation_outcome_not_satisfied: Literal[True] = True
    release_evaluation_still_required: Literal[True] = True
    checkpoint_promotion_authorized: Literal[False] = False
    checkpoint_activation_authorized: Literal[False] = False
    release_authorized: Literal[False] = False
    production_deployment_authorized: Literal[False] = False

    @field_validator("created_at")
    @classmethod
    def validate_time(cls, value: datetime) -> datetime:
        return _timezone(value, label="Program-bound Local RL package time")


class ProgramLocalRLBindingManager:
    def build_ticket(
        self,
        *,
        ticket_id: str,
        authorized_at: datetime,
        authorized_by: str,
        program: ProgramRecord,
        head: ProgramHead,
        policy: EvolutionProgramPolicy,
        signal: ProgramLearningSignal,
        attribution: AttributionReceipt,
        continue_decision: ProgramDecision,
        generation_plan: GenerationPlan,
        generation: GenerationRecord,
        campaign: CampaignRecord,
        approvals: tuple[CampaignApproval, ...],
        local_rl_manifest: LocalRLRunManifest,
    ) -> ProgramLocalRLExecutionTicket:
        initial_checkpoint = _expected_initial_checkpoint(local_rl_manifest)
        payload = {
            "format_version": _TICKET_FORMAT,
            "ticket_id": ticket_id,
            "authorized_at": authorized_at,
            "authorized_by": authorized_by,
            "program": program,
            "head": head,
            "policy": policy,
            "signal": signal,
            "attribution": attribution,
            "continue_decision": continue_decision,
            "generation_plan": generation_plan,
            "generation": generation,
            "campaign": campaign,
            "approvals": approvals,
            "local_rl_manifest": local_rl_manifest,
            "program_budget_hash": _program_budget_hash(policy),
            "cumulative_program_budget_snapshot_hash": (
                _program_budget_snapshot_hash(policy, head)
            ),
            "generation_budget_hash": _generation_budget_hash(generation_plan),
            "local_rl_task_manifest_hash": _task_manifest_hash(local_rl_manifest),
            "expected_initial_checkpoint_hash": initial_checkpoint.checkpoint_hash,
            "artifact_kind": _TINY_POLICY_KIND,
            "program_generation_running_attested": True,
            "local_optimizer_execution_authorized": True,
            "program_pair_token_cost_consumption_claimed": False,
            "selected_checkpoint_satisfies_generation_outcome": False,
            "release_evaluation_still_required": True,
            "checkpoint_promotion_authorized": False,
            "checkpoint_activation_authorized": False,
            "foundation_model_training_authorized": False,
            "external_execution_authorized": False,
            "production_deployment_authorized": False,
        }
        return ProgramLocalRLExecutionTicket(
            **payload,
            ticket_hash=canonical_sha256(payload),
        )

    def build(
        self,
        *,
        package_id: str,
        created_at: datetime,
        framework_version: str,
        source_repository: str,
        source_commit: str,
        third_party_lock_hash: str,
        ticket: ProgramLocalRLExecutionTicket,
        local_rl_package: LocalRLPackageManifest,
        receipt_id: str,
        completed_by: str,
        completed_at: datetime,
    ) -> ProgramBoundLocalRLPackageManifest:
        self.verify_ticket(ticket)
        LocalRLPackageManager().verify(local_rl_package)
        receipt = self._build_receipt(
            receipt_id=receipt_id,
            ticket=ticket,
            local_rl_package=local_rl_package,
            completed_by=completed_by,
            completed_at=completed_at,
        )
        provisional = ProgramBoundLocalRLPackageManifest(
            package_id=package_id,
            created_at=created_at,
            framework_version=framework_version,
            source_repository=source_repository,
            source_commit=source_commit,
            third_party_lock_hash=third_party_lock_hash,
            ticket=ticket,
            local_rl_package=local_rl_package,
            receipt=receipt,
            package_hash="0" * 64,
        )
        payload = provisional.model_dump(mode="json", exclude={"package_hash"})
        validate_safe_content(payload)
        package = provisional.model_copy(
            update={"package_hash": canonical_sha256(payload)}
        )
        self.verify(package)
        return package

    def verify_ticket(self, ticket: ProgramLocalRLExecutionTicket) -> bool:
        _verify_ticket_evidence(ticket)
        payload = ticket.model_dump(mode="json", exclude={"ticket_hash"})
        validate_safe_content(payload)
        if ticket.ticket_hash != canonical_sha256(payload):
            raise ProgramLocalRLBindingError(
                "Program Local RL execution ticket hash mismatch."
            )
        return True

    def verify(self, package: ProgramBoundLocalRLPackageManifest) -> bool:
        _verify_bound_package_authority_boundary(package)
        payload = package.model_dump(mode="json", exclude={"package_hash"})
        validate_safe_content(payload)
        if package.package_hash != canonical_sha256(payload):
            raise ProgramLocalRLBindingError(
                "Program-bound Local RL package hash mismatch."
            )
        self.verify_ticket(package.ticket)
        LocalRLPackageManager().verify(package.local_rl_package)
        self._verify_local_package(package.ticket, package.local_rl_package)
        _verify_receipt_authority_boundary(package.receipt)
        expected_receipt = self._build_receipt(
            receipt_id=package.receipt.receipt_id,
            ticket=package.ticket,
            local_rl_package=package.local_rl_package,
            completed_by=package.receipt.completed_by,
            completed_at=package.receipt.completed_at,
        )
        if expected_receipt != package.receipt:
            raise ProgramLocalRLBindingError(
                "Program Local RL completion receipt was substituted."
            )
        if (
            package.framework_version
            != package.local_rl_package.framework_version
            or package.source_repository
            != package.local_rl_package.source_repository
            or package.source_commit != package.local_rl_package.source_commit
            or package.third_party_lock_hash
            != package.local_rl_package.third_party_lock_hash
        ):
            raise ProgramLocalRLBindingError(
                "Program-bound package provenance differs from Local RL evidence."
            )
        if (
            package.receipt.completed_at > package.created_at
            or package.local_rl_package.created_at > package.receipt.completed_at
        ):
            raise ProgramLocalRLBindingError(
                "Program-bound Local RL package chronology is not monotonic."
            )
        return True

    def _build_receipt(
        self,
        *,
        receipt_id: str,
        ticket: ProgramLocalRLExecutionTicket,
        local_rl_package: LocalRLPackageManifest,
        completed_by: str,
        completed_at: datetime,
    ) -> ProgramLocalRLCompletionReceipt:
        self._verify_local_package(ticket, local_rl_package)
        evaluator_ids = {
            local_rl_package.baseline_evaluation.evaluator_id,
            *(item.evaluator_id for item in local_rl_package.candidate_evaluations),
        }
        evaluator_id = next(iter(evaluator_ids))
        selected = next(
            item
            for item in local_rl_package.training.retained_checkpoints
            if item.checkpoint_hash
            == local_rl_package.decision.selected_checkpoint_hash
        )
        payload = {
            "format_version": _RECEIPT_FORMAT,
            "receipt_id": receipt_id,
            "ticket_id": ticket.ticket_id,
            "ticket_hash": ticket.ticket_hash,
            "local_rl_package_id": local_rl_package.package_id,
            "local_rl_package_hash": local_rl_package.package_hash,
            "run_id": local_rl_package.manifest.run_id,
            "manifest_hash": local_rl_package.manifest.manifest_hash,
            "training_result_hash": local_rl_package.training.result_hash,
            "initial_checkpoint_hash": (
                local_rl_package.training.initial_checkpoint.checkpoint_hash
            ),
            "selected_checkpoint_id": selected.checkpoint_id,
            "selected_checkpoint_hash": selected.checkpoint_hash,
            "selected_iteration": selected.iteration,
            "selected_report_hash": local_rl_package.decision.selected_report_hash,
            "selection_decision_hash": local_rl_package.decision.decision_hash,
            "training_usage_hash": canonical_sha256(
                local_rl_package.training.usage.model_dump(mode="json")
            ),
            "trainer_id": local_rl_package.trainer_id,
            "evaluator_id": evaluator_id,
            "selection_actor_id": (
                local_rl_package.decision.decision_actor_id
            ),
            "completed_by": completed_by,
            "completed_at": completed_at,
            "local_optimizer_execution_completed": True,
            "strict_held_out_improvement_verified": True,
            "zero_unsafe_held_out_actions_verified": True,
            "checkpoint_promotion_authorized": False,
            "checkpoint_activation_authorized": False,
            "release_authorized": False,
            "production_deployment_authorized": False,
        }
        receipt = ProgramLocalRLCompletionReceipt(
            **payload,
            receipt_hash=canonical_sha256(payload),
        )
        local_roles = {
            receipt.trainer_id,
            receipt.evaluator_id,
            receipt.selection_actor_id,
        }
        privileged = _program_privileged_actors(ticket)
        if (
            len(local_roles) != 3
            or local_roles & privileged
            or receipt.completed_by in local_roles
            or receipt.completed_by in privileged
        ):
            raise ProgramLocalRLBindingError(
                "Program Local RL completion roles are not independent."
            )
        if completed_at < local_rl_package.created_at:
            raise ProgramLocalRLBindingError(
                "Program Local RL completion predates its package."
            )
        return receipt

    @staticmethod
    def _verify_local_package(
        ticket: ProgramLocalRLExecutionTicket,
        package: LocalRLPackageManifest,
    ) -> None:
        _verify_local_package_authority_boundary(package)
        if package.manifest != ticket.local_rl_manifest:
            raise ProgramLocalRLBindingError(
                "Local RL package differs from the authorized manifest."
            )
        if (
            package.training.initial_checkpoint.checkpoint_hash
            != ticket.expected_initial_checkpoint_hash
        ):
            raise ProgramLocalRLBindingError(
                "Local RL initial checkpoint differs from the execution ticket."
            )
        if not package.training.usage.fits(package.manifest.budget):
            raise ProgramLocalRLBindingError(
                "Local RL training usage exceeds the authorized local budget."
            )
        evaluator_ids = {
            package.baseline_evaluation.evaluator_id,
            *(item.evaluator_id for item in package.candidate_evaluations),
        }
        if len(evaluator_ids) != 1:
            raise ProgramLocalRLBindingError(
                "Local RL package contains multiple evaluator identities."
            )
        evaluator_id = next(iter(evaluator_ids))
        local_roles = {
            package.trainer_id,
            evaluator_id,
            package.decision.decision_actor_id,
        }
        if len(local_roles) != 3 or local_roles & _program_privileged_actors(ticket):
            raise ProgramLocalRLBindingError(
                "Local RL trainer, evaluator and selector are not independent."
            )
        if package.created_at < ticket.authorized_at:
            raise ProgramLocalRLBindingError(
                "Local RL package predates the Program execution ticket."
            )
        selected_report = next(
            item
            for item in package.candidate_evaluations
            if item.checkpoint_hash == package.decision.selected_checkpoint_hash
        )
        if (
            selected_report.overall_score
            <= package.baseline_evaluation.overall_score
            or selected_report.unsafe_action_count != 0
        ):
            raise ProgramLocalRLBindingError(
                "Local RL selected checkpoint lacks safe strict improvement."
            )
        if len(package.audit_events) != 4:
            raise ProgramLocalRLBindingError(
                "Local RL package lacks the complete execution audit."
            )
        registered, trained, evaluated, selected = package.audit_events
        if any(
            event.run_id != package.manifest.run_id
            for event in package.audit_events
        ):
            raise ProgramLocalRLBindingError(
                "Local RL audit run identity differs from the authorized manifest."
            )
        if (
            registered.actor_id != ticket.authorized_by
            or trained.actor_id != package.trainer_id
            or evaluated.actor_id != evaluator_id
            or selected.actor_id != package.decision.decision_actor_id
        ):
            raise ProgramLocalRLBindingError(
                "Local RL audit actors differ from authorization and execution roles."
            )
        timestamps = tuple(item.created_at for item in package.audit_events)
        if timestamps != tuple(sorted(timestamps)):
            raise ProgramLocalRLBindingError(
                "Local RL execution audit timestamps are not monotonic."
            )
        if registered.created_at < ticket.authorized_at:
            raise ProgramLocalRLBindingError(
                "Local RL run registration predates Program authorization."
            )
        if (
            selected.created_at != package.decision.decided_at
            or package.decision.decided_at > package.created_at
        ):
            raise ProgramLocalRLBindingError(
                "Local RL package chronology differs from checkpoint selection."
            )


def _program_privileged_actors(
    ticket: ProgramLocalRLExecutionTicket,
) -> set[str]:
    return {
        ticket.signal.evidence_producer_id,
        ticket.attribution.attributor_id,
        ticket.generation_plan.created_by,
        ticket.continue_decision.decided_by,
        ticket.authorized_by,
        *(item.actor_id for item in ticket.approvals),
    }


def _verify_ticket_authority_boundary(
    ticket: ProgramLocalRLExecutionTicket,
) -> None:
    if (
        ticket.format_version != _TICKET_FORMAT
        or ticket.artifact_kind != _TINY_POLICY_KIND
        or ticket.program_generation_running_attested is not True
        or ticket.local_optimizer_execution_authorized is not True
        or ticket.program_pair_token_cost_consumption_claimed is not False
        or ticket.selected_checkpoint_satisfies_generation_outcome is not False
        or ticket.release_evaluation_still_required is not True
        or ticket.checkpoint_promotion_authorized is not False
        or ticket.checkpoint_activation_authorized is not False
        or ticket.foundation_model_training_authorized is not False
        or ticket.external_execution_authorized is not False
        or ticket.production_deployment_authorized is not False
    ):
        raise ProgramLocalRLBindingError(
            "Program Local RL execution ticket widens immutable authority."
        )


def _verify_receipt_authority_boundary(
    receipt: ProgramLocalRLCompletionReceipt,
) -> None:
    if (
        receipt.format_version != _RECEIPT_FORMAT
        or receipt.local_optimizer_execution_completed is not True
        or receipt.strict_held_out_improvement_verified is not True
        or receipt.zero_unsafe_held_out_actions_verified is not True
        or receipt.checkpoint_promotion_authorized is not False
        or receipt.checkpoint_activation_authorized is not False
        or receipt.release_authorized is not False
        or receipt.production_deployment_authorized is not False
    ):
        raise ProgramLocalRLBindingError(
            "Program Local RL completion receipt widens immutable authority."
        )


def _verify_bound_package_authority_boundary(
    package: ProgramBoundLocalRLPackageManifest,
) -> None:
    if (
        package.format_version != _BOUND_PACKAGE_FORMAT
        or package.local_policy_evidence_only is not True
        or package.generation_outcome_not_satisfied is not True
        or package.release_evaluation_still_required is not True
        or package.checkpoint_promotion_authorized is not False
        or package.checkpoint_activation_authorized is not False
        or package.release_authorized is not False
        or package.production_deployment_authorized is not False
    ):
        raise ProgramLocalRLBindingError(
            "Program-bound Local RL package widens immutable authority."
        )


def _verify_local_package_authority_boundary(
    package: LocalRLPackageManifest,
) -> None:
    manifest = package.manifest
    training = package.training
    checkpoints = (
        training.initial_checkpoint,
        *training.retained_checkpoints,
    )
    if (
        package.format_version != _LOCAL_PACKAGE_FORMAT
        or package.tiny_tabular_policy_only is not True
        or package.local_rollout_training_executed_by_evoagent is not True
        or package.foundation_model_training_performed is not False
        or package.external_model_call_performed_by_evoagent is not False
        or package.gpu_execution_performed is not False
        or package.network_execution_performed is not False
        or package.production_deployment_performed is not False
        or package.upload_performed is not False
        or package.official_benchmark_claimed is not False
        or manifest.external_model_call_performed_by_evoagent is not False
        or manifest.foundation_model_training_performed is not False
        or manifest.gpu_execution_performed is not False
        or manifest.network_execution_performed is not False
        or training.numeric_parameters_updated is not True
        or training.local_rollout_training_executed_by_evoagent is not True
        or training.foundation_model_training_performed is not False
        or training.usage.wall_clock_limit_enforced is not True
        or training.usage.budget_exceeded is not False
        or any(
            checkpoint.artifact_kind != _TINY_POLICY_KIND
            or checkpoint.foundation_model_checkpoint is not False
            or checkpoint.language_model_weights is not False
            for checkpoint in checkpoints
        )
    ):
        raise ProgramLocalRLBindingError(
            "Local RL package widens tiny-policy or execution authority."
        )


def _verify_program_budget_capacity(
    policy: EvolutionProgramPolicy,
    head: ProgramHead,
    plan: GenerationPlan,
) -> None:
    budget = policy.budget
    if (
        head.current_generation_index >= budget.max_generations
        or head.rollback_count > budget.max_rollbacks
        or head.hold_count > budget.max_holds
        or head.generation_campaign_count > budget.max_generation_campaigns
        or head.total_pairs > budget.max_total_pairs
        or head.total_tokens > budget.max_total_tokens
        or head.total_cost_usd > budget.max_total_cost_usd + 1e-12
    ):
        raise ProgramLocalRLBindingError(
            "Program Local RL ticket exceeds cumulative Program budget."
        )
    remaining_pairs = budget.max_total_pairs - head.total_pairs
    remaining_tokens = budget.max_total_tokens - head.total_tokens
    remaining_cost = budget.max_total_cost_usd - head.total_cost_usd
    if (
        plan.budget.max_child_packages != 1
        or plan.budget.max_pairs > remaining_pairs
        or plan.budget.max_tokens > remaining_tokens
        or plan.budget.max_cost_usd > remaining_cost + 1e-12
    ):
        raise ProgramLocalRLBindingError(
            "Program Local RL GenerationPlan exceeds the remaining Program budget."
        )


def _verify_ticket_evidence(ticket: ProgramLocalRLExecutionTicket) -> None:
    _verify_ticket_authority_boundary(ticket)
    program = ticket.program
    head = ticket.head
    policy = ticket.policy
    signal = ticket.signal
    attribution = ticket.attribution
    decision = ticket.continue_decision
    plan = ticket.generation_plan
    generation = ticket.generation
    campaign = ticket.campaign
    approvals = ticket.approvals
    manifest = ticket.local_rl_manifest

    if program.policy != policy or program.program_id != plan.program_id:
        raise ProgramLocalRLBindingError(
            "Program Local RL ticket differs from the Program policy identity."
        )
    if (
        program.state != ProgramState.GENERATION_RUNNING
        or head.state != ProgramState.GENERATION_RUNNING
        or head.program_id != program.program_id
        or head.active_generation_id != generation.generation_id
        or head.current_generation_index != generation.generation_index
        or head.last_decision_id != decision.decision_id
        or program.updated_at != head.updated_at
    ):
        raise ProgramLocalRLBindingError(
            "Program Local RL ticket requires one exact running Program head."
        )
    _verify_program_budget_capacity(policy, head, plan)
    if (
        generation.program_id != program.program_id
        or generation.status != GenerationStatus.RUNNING
        or generation.plan != plan
        or generation.outcome is not None
        or generation.campaign_id != campaign.campaign_id
        or generation.generation_id != plan.generation_id
        or generation.generation_index != plan.generation_index
        or generation.parent_generation_id != plan.parent_generation_id
        or generation.updated_at != head.updated_at
    ):
        raise ProgramLocalRLBindingError(
            "Program Local RL ticket requires the exact running generation."
        )
    if (
        decision.action != ProgramAction.CONTINUE
        or decision.program_id != plan.program_id
        or decision.generation_id != plan.parent_generation_id
        or decision.next_generation_index != plan.generation_index
        or decision.decided_by != plan.created_by
    ):
        raise ProgramLocalRLBindingError(
            "GenerationPlan is not bound to one exact CONTINUE decision."
        )
    if (
        signal.program_id != plan.program_id
        or signal.generation_index != plan.generation_index - 1
        or plan.source_signal_id != signal.signal_id
        or plan.source_signal_hash != signal.signal_hash
        or attribution.signal_id != signal.signal_id
        or attribution.signal_hash != signal.signal_hash
        or plan.attribution_receipt_id != attribution.receipt_id
        or plan.attribution_receipt_hash != attribution.receipt_hash
        or plan.intervention_layer != attribution.failure_layer
        or plan.intervention_action != attribution.action
    ):
        raise ProgramLocalRLBindingError(
            "Program Local RL ticket signal, attribution and plan differ."
        )
    if (
        attribution.attributor_id == signal.evidence_producer_id
        or not attribution.independent
        or attribution.confidence < policy.minimum_attribution_confidence
        or attribution.failure_layer not in policy.allowed_automatic_layers
        or (
            policy.require_single_supported_experiment
            and len(attribution.supported_experiment_hashes) != 1
        )
    ):
        raise ProgramLocalRLBindingError(
            "Program Local RL attribution is not independently governed."
        )
    expected_fingerprint = fingerprint_payload(
        _campaign_fingerprint_source(policy, signal, attribution, plan)
    )
    if (
        campaign.campaign_type != CampaignType.EVOLUTION_GENERATION
        or campaign.state != CampaignState.AUTHORIZED
        or campaign.risk != CampaignRisk.HIGH
        or campaign.required_approvals != 2
        or campaign.generated_by != plan.created_by
        or campaign.target_key != _campaign_target_key(plan)
        or campaign.fingerprint != expected_fingerprint
        or campaign.candidate_ref != _campaign_candidate_ref(plan)
        or campaign.metadata
        != _campaign_metadata(policy, signal, attribution, plan)
        or campaign.artifact_payload
        != _campaign_artifact(policy, signal, attribution, plan)
        or campaign.revision != 5
    ):
        raise ProgramLocalRLBindingError(
            "Program Local RL Campaign differs from exact Program evidence."
        )
    approval_actors = tuple(item.actor_id for item in approvals)
    forbidden = {
        signal.evidence_producer_id,
        attribution.attributor_id,
        plan.created_by,
        decision.decided_by,
    }
    if (
        len(approvals) != 2
        or len(set(approval_actors)) != 2
        or any(item.campaign_id != campaign.campaign_id for item in approvals)
        or any(item.decision != ApprovalDecision.APPROVE for item in approvals)
        or set(approval_actors) & forbidden
        or ticket.authorized_by in forbidden
        or ticket.authorized_by in set(approval_actors)
    ):
        raise ProgramLocalRLBindingError(
            "Program Local RL authorization roles are not independent."
        )
    if tuple(item.created_at for item in approvals) != tuple(
        sorted(item.created_at for item in approvals)
    ):
        raise ProgramLocalRLBindingError(
            "Program Local RL approvals are not chronologically ordered."
        )
    if (
        campaign.updated_at != approvals[-1].created_at
        or campaign.updated_at > head.updated_at
        or plan.created_at > manifest.created_at
        or manifest.created_at > ticket.authorized_at
        or head.updated_at > ticket.authorized_at
    ):
        raise ProgramLocalRLBindingError(
            "Program Local RL authorization chronology is not monotonic."
        )
    if (
        ticket.program_budget_hash != _program_budget_hash(policy)
        or ticket.cumulative_program_budget_snapshot_hash
        != _program_budget_snapshot_hash(policy, head)
        or ticket.generation_budget_hash != _generation_budget_hash(plan)
        or ticket.local_rl_task_manifest_hash != _task_manifest_hash(manifest)
        or ticket.expected_initial_checkpoint_hash
        != _expected_initial_checkpoint(manifest).checkpoint_hash
    ):
        raise ProgramLocalRLBindingError(
            "Program Local RL ticket budget, Task or initial-checkpoint binding differs."
        )
    if (
        plan.external_execution_authorized
        or plan.production_deployment_authorized
        or manifest.external_model_call_performed_by_evoagent
        or manifest.foundation_model_training_performed
        or manifest.gpu_execution_performed
        or manifest.network_execution_performed
    ):
        raise ProgramLocalRLBindingError(
            "Program Local RL ticket widens execution or model-training authority."
        )


__all__ = [
    "ProgramBoundLocalRLPackageManifest",
    "ProgramLocalRLBindingError",
    "ProgramLocalRLBindingManager",
    "ProgramLocalRLCompletionReceipt",
    "ProgramLocalRLExecutionTicket",
]
