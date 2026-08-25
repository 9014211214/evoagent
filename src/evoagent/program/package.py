from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evoagent._io import atomic_temporary_path
from evoagent.campaigns import (
    ApprovalDecision,
    CampaignApproval,
    CampaignAuditEvent,
    CampaignCheckpoint,
    CampaignRecord,
    CampaignRisk,
    CampaignState,
    CampaignType,
    SQLiteCampaignRepository,
    fingerprint_payload,
)
from evoagent.model_registry.models import canonical_sha256, validate_safe_content
from evoagent.program.controller import EvolutionProgramController, EvolutionProgramGate
from evoagent.program.feedback import ReleaseFeedbackExtractor
from evoagent.program.models import (
    AttributionReceipt,
    EvolutionProgramPolicy,
    GenerationRecord,
    GenerationStatus,
    ProgramAction,
    ProgramAuditEvent,
    ProgramCheckpoint,
    ProgramDecision,
    ProgramEventType,
    ProgramHead,
    ProgramLearningSignal,
    ProgramState,
)
from evoagent.program.repository import SQLiteEvolutionProgramRepository
from evoagent.release.package import (
    ReleaseEvidencePackageManager,
    ReleaseEvidencePackageManifest,
)


_SHA1_PATTERN = r"^[0-9a-f]{40}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_GENESIS_HASH = "0" * 64


class EvolutionProgramPackageError(ValueError):
    pass


class ProgramControlEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    control_id: str
    policy: EvolutionProgramPolicy
    generations: tuple[GenerationRecord, ...]
    signals: tuple[ProgramLearningSignal, ...]
    attributions: tuple[AttributionReceipt, ...]
    decisions: tuple[ProgramDecision, ...]
    final_head: ProgramHead
    events: tuple[ProgramAuditEvent, ...]
    checkpoint: ProgramCheckpoint
    generation_campaign_count: int = Field(ge=0)
    control_hash: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_hash(self):
        payload = self.model_dump(mode="json", exclude={"control_hash"})
        validate_safe_content(payload)
        if self.control_hash != canonical_sha256(payload):
            raise ValueError("Program control evidence hash mismatch.")
        return self


class EvolutionProgramPackageManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    format_version: Literal["evoagent-evolution-program-package-v1"] = (
        "evoagent-evolution-program-package-v1"
    )
    package_id: str
    created_at: datetime
    framework_version: str
    source_repository: str
    source_commit: str = Field(pattern=_SHA1_PATTERN)
    third_party_lock_hash: str = Field(pattern=_SHA256_PATTERN)
    drift_release_package: ReleaseEvidencePackageManifest
    passing_release_package: ReleaseEvidencePackageManifest
    policy: EvolutionProgramPolicy
    signal: ProgramLearningSignal
    attribution: AttributionReceipt
    generations: tuple[GenerationRecord, ...]
    decisions: tuple[ProgramDecision, ...]
    generation_campaign: CampaignRecord
    generation_approvals: tuple[CampaignApproval, ...]
    final_head: ProgramHead
    program_events: tuple[ProgramAuditEvent, ...]
    program_checkpoint: ProgramCheckpoint
    campaign_events: tuple[CampaignAuditEvent, ...]
    campaign_checkpoint: CampaignCheckpoint
    budget_control: ProgramControlEvidence
    ambiguous_control: ProgramControlEvidence
    package_hash: str = Field(pattern=_SHA256_PATTERN)
    synthetic_fixture: Literal[True] = True
    external_model_call_performed_by_evoagent: Literal[False] = False
    training_executed_by_evoagent: Literal[False] = False
    external_rollout_performed_by_evoagent: Literal[False] = False
    production_traffic_observed_by_evoagent: Literal[False] = False
    production_deployment_performed: Literal[False] = False
    external_rollback_performed: Literal[False] = False
    upload_performed: Literal[False] = False
    official_benchmark_claimed: Literal[False] = False

    @field_validator("created_at")
    @classmethod
    def timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Evolution Program package time must include a timezone.")
        return value


class EvolutionProgramPackageManager:
    def build(
        self,
        *,
        package_id: str,
        created_at: datetime,
        framework_version: str,
        source_repository: str,
        source_commit: str,
        third_party_lock_hash: str,
        drift_release_package: ReleaseEvidencePackageManifest,
        passing_release_package: ReleaseEvidencePackageManifest,
        policy: EvolutionProgramPolicy,
        signal: ProgramLearningSignal,
        attribution: AttributionReceipt,
        generations: tuple[GenerationRecord, ...],
        decisions: tuple[ProgramDecision, ...],
        generation_campaign: CampaignRecord,
        generation_approvals: tuple[CampaignApproval, ...],
        final_head: ProgramHead,
        program_events: tuple[ProgramAuditEvent, ...],
        program_checkpoint: ProgramCheckpoint,
        campaign_events: tuple[CampaignAuditEvent, ...],
        campaign_checkpoint: CampaignCheckpoint,
        budget_control: ProgramControlEvidence,
        ambiguous_control: ProgramControlEvidence,
    ) -> EvolutionProgramPackageManifest:
        provisional = EvolutionProgramPackageManifest(
            package_id=package_id,
            created_at=created_at,
            framework_version=framework_version,
            source_repository=source_repository,
            source_commit=source_commit,
            third_party_lock_hash=third_party_lock_hash,
            drift_release_package=drift_release_package,
            passing_release_package=passing_release_package,
            policy=policy,
            signal=signal,
            attribution=attribution,
            generations=generations,
            decisions=decisions,
            generation_campaign=generation_campaign,
            generation_approvals=generation_approvals,
            final_head=final_head,
            program_events=program_events,
            program_checkpoint=program_checkpoint,
            campaign_events=campaign_events,
            campaign_checkpoint=campaign_checkpoint,
            budget_control=budget_control,
            ambiguous_control=ambiguous_control,
            package_hash="0" * 64,
        )
        payload = provisional.model_dump(mode="json", exclude={"package_hash"})
        validate_safe_content(payload)
        manifest = provisional.model_copy(
            update={"package_hash": canonical_sha256(payload)}
        )
        self.verify(manifest)
        return manifest

    def verify(self, manifest: EvolutionProgramPackageManifest) -> bool:
        payload = manifest.model_dump(mode="json", exclude={"package_hash"})
        validate_safe_content(payload)
        if manifest.package_hash != canonical_sha256(payload):
            raise EvolutionProgramPackageError("Evolution Program package hash mismatch.")
        release_manager = ReleaseEvidencePackageManager()
        release_manager.verify(manifest.drift_release_package)
        release_manager.verify(manifest.passing_release_package)
        if (
            not manifest.drift_release_package.synthetic_fixture
            or not manifest.passing_release_package.synthetic_fixture
        ):
            raise EvolutionProgramPackageError(
                "Controlled Program package requires explicit synthetic release fixtures."
            )
        self._verify_main_program(manifest)
        self._verify_campaign(manifest)
        self._verify_program_audit(manifest)
        self._verify_campaign_audit(manifest)
        self._verify_control(
            manifest.budget_control,
            expected_action=ProgramAction.STOP_BUDGET,
            expected_state=ProgramState.BUDGET_EXHAUSTED,
            require_attribution=False,
        )
        self._verify_control(
            manifest.ambiguous_control,
            expected_action=ProgramAction.ESCALATE,
            expected_state=ProgramState.ESCALATED,
            require_attribution=True,
        )
        return True

    @staticmethod
    def _verify_main_program(manifest: EvolutionProgramPackageManifest) -> None:
        if len(manifest.generations) != 2 or len(manifest.decisions) != 2:
            raise EvolutionProgramPackageError(
                "Main Program package requires exactly Generations 0 and 1."
            )
        g0, g1 = manifest.generations
        d0, d1 = manifest.decisions
        if (
            g0.generation_index != 0
            or g0.status != GenerationStatus.ROLLED_BACK
            or g0.outcome is None
            or g1.generation_index != 1
            or g1.parent_generation_id != g0.generation_id
            or g1.status != GenerationStatus.COMPLETED
            or g1.plan is None
            or g1.outcome is None
        ):
            raise EvolutionProgramPackageError(
                "Program generation lineage or terminal states differ."
            )
        extractor = ReleaseFeedbackExtractor()
        expected_g0 = extractor.generation_outcome(
            manifest.drift_release_package,
            program_id=g0.program_id,
            generation_id=g0.generation_id,
            generation_index=0,
            outcome_id=g0.outcome.outcome_id,
            completed_at=g0.outcome.completed_at,
        )
        expected_signal = extractor.extract(
            manifest.drift_release_package,
            program_id=g0.program_id,
            generation_index=0,
            signal_id=manifest.signal.signal_id,
            created_at=manifest.signal.created_at,
        )
        expected_g1 = extractor.generation_outcome(
            manifest.passing_release_package,
            program_id=g1.program_id,
            generation_id=g1.generation_id,
            generation_index=1,
            outcome_id=g1.outcome.outcome_id,
            completed_at=g1.outcome.completed_at,
            plan=g1.plan,
        )
        if expected_g0 != g0.outcome or expected_signal != manifest.signal or expected_g1 != g1.outcome:
            raise EvolutionProgramPackageError(
                "Packaged Program evidence differs from verified release packages."
            )
        if (
            manifest.attribution.signal_id != manifest.signal.signal_id
            or manifest.attribution.signal_hash != manifest.signal.signal_hash
            or manifest.attribution.attributor_id == manifest.signal.evidence_producer_id
            or len(manifest.attribution.supported_experiment_hashes) != 1
        ):
            raise EvolutionProgramPackageError(
                "Program attribution is not exact, independent, and single-experiment."
            )
        gate = EvolutionProgramGate()
        head0 = ProgramHead(
            program_id=g0.program_id,
            state=ProgramState.RUNNING,
            current_generation_index=0,
            active_generation_id=g0.generation_id,
            revision=0,
            rollback_count=1,
            hold_count=0,
            generation_campaign_count=0,
            total_pairs=g0.outcome.pair_count,
            total_tokens=g0.outcome.total_tokens,
            total_cost_usd=g0.outcome.total_cost_usd,
            updated_at=d0.decided_at,
        )
        expected_d0 = gate.decide(
            policy=manifest.policy,
            head=head0,
            outcome=g0.outcome,
            decision_id=d0.decision_id,
            decided_by=d0.decided_by,
            decided_at=d0.decided_at,
            signal=manifest.signal,
            attribution=manifest.attribution,
        )
        prefinal = manifest.final_head.model_copy(
            update={
                "state": ProgramState.RUNNING,
                "revision": manifest.final_head.revision - 1,
                "last_decision_id": d0.decision_id,
                "updated_at": d1.decided_at,
            }
        )
        expected_d1 = gate.decide(
            policy=manifest.policy,
            head=prefinal,
            outcome=g1.outcome,
            decision_id=d1.decision_id,
            decided_by=d1.decided_by,
            decided_at=d1.decided_at,
        )
        if expected_d0 != d0 or expected_d1 != d1:
            raise EvolutionProgramPackageError(
                "Packaged Program decisions differ from policy and outcomes."
            )
        if d0.action != ProgramAction.CONTINUE or d1.action != ProgramAction.STOP_SUCCESS:
            raise EvolutionProgramPackageError(
                "Controlled Program must continue once and then stop successfully."
            )
        expected_head = ProgramHead(
            program_id=g0.program_id,
            state=ProgramState.COMPLETED,
            current_generation_index=1,
            active_generation_id=g1.generation_id,
            revision=6,
            rollback_count=1,
            hold_count=0,
            generation_campaign_count=1,
            total_pairs=g0.outcome.pair_count + g1.outcome.pair_count,
            total_tokens=g0.outcome.total_tokens + g1.outcome.total_tokens,
            total_cost_usd=g0.outcome.total_cost_usd + g1.outcome.total_cost_usd,
            last_decision_id=d1.decision_id,
            updated_at=manifest.final_head.updated_at,
        )
        if manifest.final_head != expected_head:
            raise EvolutionProgramPackageError(
                "Final Program head differs from two-generation lifecycle."
            )

    @staticmethod
    def _verify_campaign(manifest: EvolutionProgramPackageManifest) -> None:
        g1 = manifest.generations[1]
        plan = g1.plan
        campaign = manifest.generation_campaign
        controller = EvolutionProgramController
        expected_fingerprint = fingerprint_payload(
            controller._fingerprint_source(
                manifest.policy,
                manifest.signal,
                manifest.attribution,
                plan,
            )
        )
        if (
            campaign.campaign_type != CampaignType.EVOLUTION_GENERATION
            or campaign.state != CampaignState.COMPLETED
            or campaign.risk != CampaignRisk.HIGH
            or campaign.required_approvals != 2
            or campaign.generated_by != plan.created_by
            or campaign.target_key != controller._target_key(plan)
            or campaign.fingerprint != expected_fingerprint
            or campaign.candidate_ref != controller._candidate_ref(plan)
            or campaign.metadata
            != controller._metadata(
                manifest.policy,
                manifest.signal,
                manifest.attribution,
                plan,
            )
            or campaign.artifact_payload
            != controller._artifact_payload(
                manifest.policy,
                manifest.signal,
                manifest.attribution,
                plan,
            )
            or g1.campaign_id != campaign.campaign_id
        ):
            raise EvolutionProgramPackageError(
                "Generation Campaign differs from exact Program plan evidence."
            )
        actors = tuple(item.actor_id for item in manifest.generation_approvals)
        forbidden = {
            manifest.signal.evidence_producer_id,
            manifest.attribution.attributor_id,
            plan.created_by,
        }
        if (
            len(manifest.generation_approvals) != 2
            or len(set(actors)) != 2
            or set(actors) & forbidden
            or any(
                item.campaign_id != campaign.campaign_id
                or item.decision != ApprovalDecision.APPROVE
                for item in manifest.generation_approvals
            )
        ):
            raise EvolutionProgramPackageError(
                "Generation package requires two independent approvals."
            )

    @staticmethod
    def _verify_control(
        control: ProgramControlEvidence,
        *,
        expected_action: ProgramAction,
        expected_state: ProgramState,
        require_attribution: bool,
    ) -> None:
        payload = control.model_dump(mode="json", exclude={"control_hash"})
        if control.control_hash != canonical_sha256(payload):
            raise EvolutionProgramPackageError("Program control hash mismatch.")
        if (
            len(control.generations) != 1
            or len(control.decisions) != 1
            or control.decisions[0].action != expected_action
            or control.final_head.state != expected_state
            or control.generation_campaign_count != 0
            or control.final_head.generation_campaign_count != 0
        ):
            raise EvolutionProgramPackageError(
                "Program negative control reached an unexpected lifecycle state."
            )
        if require_attribution != bool(control.attributions):
            raise EvolutionProgramPackageError(
                "Program negative control attribution presence differs."
            )
        EvolutionProgramPackageManager._verify_program_event_chain(
            control.events, control.checkpoint
        )
        expected_terminal = {
            ProgramAction.STOP_BUDGET: ProgramEventType.PROGRAM_BUDGET_EXHAUSTED,
            ProgramAction.ESCALATE: ProgramEventType.PROGRAM_ESCALATED,
        }[expected_action]
        expected_types = [
            ProgramEventType.PROGRAM_REGISTERED,
            ProgramEventType.GENERATION_OBSERVED,
            ProgramEventType.SIGNAL_STORED,
        ]
        if require_attribution:
            expected_types.append(ProgramEventType.ATTRIBUTION_STORED)
        expected_types.extend(
            [ProgramEventType.DECISION_STORED, expected_terminal]
        )
        if tuple(item.event_type for item in control.events) != tuple(expected_types):
            raise EvolutionProgramPackageError(
                "Program negative-control events are missing or reordered."
            )

    @staticmethod
    def _verify_program_audit(manifest: EvolutionProgramPackageManifest) -> None:
        EvolutionProgramPackageManager._verify_program_event_chain(
            manifest.program_events,
            manifest.program_checkpoint,
        )
        expected_types = (
            ProgramEventType.PROGRAM_REGISTERED,
            ProgramEventType.GENERATION_OBSERVED,
            ProgramEventType.SIGNAL_STORED,
            ProgramEventType.ATTRIBUTION_STORED,
            ProgramEventType.DECISION_STORED,
            ProgramEventType.GENERATION_PLANNED,
            ProgramEventType.GENERATION_CAMPAIGN_BOUND,
            ProgramEventType.GENERATION_AUTHORIZED,
            ProgramEventType.GENERATION_STARTED,
            ProgramEventType.GENERATION_COMPLETED,
            ProgramEventType.DECISION_STORED,
            ProgramEventType.PROGRAM_COMPLETED,
        )
        if tuple(item.event_type for item in manifest.program_events) != expected_types:
            raise EvolutionProgramPackageError(
                "Program lifecycle events are missing, duplicated, or reordered."
            )

    @staticmethod
    def _verify_program_event_chain(
        events: tuple[ProgramAuditEvent, ...],
        checkpoint: ProgramCheckpoint,
    ) -> None:
        previous_hash = _GENESIS_HASH
        for expected_sequence, event in enumerate(events, start=1):
            if event.sequence != expected_sequence or event.previous_hash != previous_hash:
                raise EvolutionProgramPackageError("Program audit chain is broken.")
            expected_hash = SQLiteEvolutionProgramRepository._event_hash(
                sequence=event.sequence,
                event_id=event.event_id,
                program_id=event.program_id,
                generation_id=event.generation_id,
                event_type=event.event_type,
                actor_id=event.actor_id,
                reason=event.reason,
                payload=event.payload,
                created_at=event.created_at,
                previous_hash=event.previous_hash,
            )
            if event.event_hash != expected_hash:
                raise EvolutionProgramPackageError(
                    "Program audit event content was modified."
                )
            previous_hash = event.event_hash
        if checkpoint != ProgramCheckpoint(
            event_count=len(events), head_hash=previous_hash
        ):
            raise EvolutionProgramPackageError(
                "Program events do not match external checkpoint."
            )

    @staticmethod
    def _verify_campaign_audit(manifest: EvolutionProgramPackageManifest) -> None:
        previous_hash = _GENESIS_HASH
        campaign_id = manifest.generation_campaign.campaign_id
        for expected_sequence, event in enumerate(manifest.campaign_events, start=1):
            if (
                event.sequence != expected_sequence
                or event.previous_hash != previous_hash
                or event.campaign_id != campaign_id
            ):
                raise EvolutionProgramPackageError(
                    "Generation Campaign audit chain is broken or cross-contaminated."
                )
            expected_hash = SQLiteCampaignRepository._event_hash(
                sequence=event.sequence,
                event_id=event.event_id,
                campaign_id=event.campaign_id,
                event_type=event.event_type,
                actor_id=event.actor_id,
                payload=event.payload,
                created_at=event.created_at,
                previous_hash=event.previous_hash,
            )
            if event.event_hash != expected_hash:
                raise EvolutionProgramPackageError(
                    "Generation Campaign audit event was modified."
                )
            previous_hash = event.event_hash
        if manifest.campaign_checkpoint != CampaignCheckpoint(
            event_count=len(manifest.campaign_events), head_hash=previous_hash
        ):
            raise EvolutionProgramPackageError(
                "Generation Campaign events do not match checkpoint."
            )
        expected_types = (
            "campaign_created",
            "candidate_attached",
            "campaign_transitioned",
            "campaign_transitioned",
            "approval_recorded",
            "approval_recorded",
            "campaign_transitioned",
        )
        if tuple(item.event_type for item in manifest.campaign_events) != expected_types:
            raise EvolutionProgramPackageError(
                "Generation Campaign events are missing, duplicated, or reordered."
            )
        approval_events = [
            item for item in manifest.campaign_events if item.event_type == "approval_recorded"
        ]
        if tuple(item.actor_id for item in approval_events) != tuple(
            item.actor_id for item in manifest.generation_approvals
        ):
            raise EvolutionProgramPackageError(
                "Generation Campaign approval identity was substituted."
            )

    def export_file(
        self,
        manifest: EvolutionProgramPackageManifest,
        path: str | Path,
    ) -> Path:
        self.verify(manifest)
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_symlink():
            raise EvolutionProgramPackageError(
                "Evolution Program package output must not be a symlink."
            )
        temporary = atomic_temporary_path(destination)
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(manifest.model_dump_json(indent=2) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
        return destination

    def load_file(self, path: str | Path) -> EvolutionProgramPackageManifest:
        target = Path(path)
        if target.is_symlink() or not target.is_file():
            raise EvolutionProgramPackageError(
                "Evolution Program package must be a regular non-symlink file."
            )
        try:
            manifest = EvolutionProgramPackageManifest.model_validate_json(
                target.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise EvolutionProgramPackageError(
                f"Evolution Program package is invalid: {exc}"
            ) from exc
        self.verify(manifest)
        return manifest


__all__ = [
    "EvolutionProgramPackageError",
    "EvolutionProgramPackageManager",
    "EvolutionProgramPackageManifest",
    "ProgramControlEvidence",
]
