from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evoagent import __version__
from evoagent.composite import (
    CompositeAuditEvent,
    CompositeEventType,
    CompositeHead,
    CompositeRegistryCheckpoint,
    CompositeSnapshotRecord,
    CompositeSnapshotStatus,
    CompositeStopAction,
    CompositeEvaluationAuditEvent,
    CompositeEvaluationCheckpoint,
    CompositeEvaluationPolicyRecord,
    CompositeEvaluationRecord,
    CompositeDecisionRecord,
    changed_component,
    build_composite_stop_decision,
)
from evoagent.composite.repository import SQLiteCompositeSnapshotRegistry
from evoagent.composite.evaluation_repository import (
    SQLiteCompositeEvaluationRepository,
)
from evoagent.lab.automatic_local_tool import (
    AutomaticLocalToolEvolutionResult,
)
from evoagent.lab.program_local_rl_acceptance import (
    ProgramLocalRLAcceptedEvidenceBundle,
    ProgramLocalRLAcceptedEvidenceManager,
)
from evoagent.local_policy import (
    LocalPolicyPromotionPackageManager,
    LocalPolicyPromotionPackageManifest,
)
from evoagent.model_registry.models import canonical_sha256, validate_safe_content
from evoagent.skills import SkillRegistryBundle, SkillStateBundleManager

from .models import (
    IntegratedAuditEvent,
    IntegratedCaseRecord,
    IntegratedCaseStatus,
    IntegratedCheckpoint,
    IntegratedEventType,
    IntegratedRunRecord,
    IntegratedRunStatus,
    IntegratedTrack,
    IntegratedTrackResult,
)
from .repository import SQLiteIntegratedEvolutionRepository


_SHA1_PATTERN = r"^[0-9a-f]{40}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SAFE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"


class IntegratedEvolutionPackageManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    format_version: Literal["evoagent-integrated-evolution-package-v1"] = (
        "evoagent-integrated-evolution-package-v1"
    )
    package_id: str = Field(pattern=_SAFE_ID_PATTERN)
    framework_version: str
    source_repository: str
    source_commit: str = Field(pattern=_SHA1_PATTERN)
    third_party_lock_hash: str = Field(pattern=_SHA256_PATTERN)

    run: IntegratedRunRecord
    cases: tuple[IntegratedCaseRecord, ...]
    track_results: tuple[IntegratedTrackResult, ...]
    integrated_events: tuple[IntegratedAuditEvent, ...]
    integrated_checkpoint: IntegratedCheckpoint

    composite_snapshots: tuple[CompositeSnapshotRecord, ...]
    composite_head: CompositeHead
    composite_events: tuple[CompositeAuditEvent, ...]
    composite_checkpoint: CompositeRegistryCheckpoint

    evaluation_policy: CompositeEvaluationPolicyRecord
    evaluations: tuple[CompositeEvaluationRecord, ...]
    stop_decisions: tuple[CompositeDecisionRecord, ...]
    evaluation_events: tuple[CompositeEvaluationAuditEvent, ...]
    evaluation_checkpoint: CompositeEvaluationCheckpoint

    skill_state: SkillRegistryBundle
    skill_child_result: AutomaticLocalToolEvolutionResult
    accepted_program_local_rl: ProgramLocalRLAcceptedEvidenceBundle
    local_policy_promotion: LocalPolicyPromotionPackageManifest

    created_at: datetime
    synthetic_controlled_lab: Literal[True] = True
    local_skill_evolution_performed: Literal[True] = True
    local_policy_optimization_performed: Literal[True] = True
    local_policy_pointer_activation_performed: Literal[True] = True
    foundation_model_training_performed: Literal[False] = False
    production_activation_performed: Literal[False] = False
    production_deployment_performed: Literal[False] = False
    external_rollout_performed: Literal[False] = False
    official_benchmark_claimed: Literal[False] = False
    package_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("created_at")
    @classmethod
    def validate_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Integrated package time must include a timezone.")
        return value

    @model_validator(mode="after")
    def validate_hash(self):
        payload = self.model_dump(mode="json", exclude={"package_hash"})
        validate_safe_content(payload)
        if self.package_hash != canonical_sha256(payload):
            raise ValueError("Integrated evolution package hash mismatch.")
        return self


class IntegratedEvolutionPackageError(ValueError):
    pass


class IntegratedEvolutionPackageManager:
    """Build and recursively verify one complete A0 -> A1 -> A2 run."""

    def build(self, **kwargs) -> IntegratedEvolutionPackageManifest:
        payload = {
            "format_version": "evoagent-integrated-evolution-package-v1",
            "framework_version": __version__,
            **kwargs,
            "synthetic_controlled_lab": True,
            "local_skill_evolution_performed": True,
            "local_policy_optimization_performed": True,
            "local_policy_pointer_activation_performed": True,
            "foundation_model_training_performed": False,
            "production_activation_performed": False,
            "production_deployment_performed": False,
            "external_rollout_performed": False,
            "official_benchmark_claimed": False,
        }
        package = IntegratedEvolutionPackageManifest(
            **payload,
            package_hash=canonical_sha256(payload),
        )
        self.verify(package)
        return package

    @classmethod
    def verify(cls, package: IntegratedEvolutionPackageManifest) -> bool:
        SkillStateBundleManager().verify(package.skill_state)
        ProgramLocalRLAcceptedEvidenceManager.verify(
            package.accepted_program_local_rl
        )
        LocalPolicyPromotionPackageManager.verify(
            package.local_policy_promotion
        )
        cls._verify_child_evidence(package)
        cls._verify_integrated_state(package)
        cls._verify_composite_state(package)
        cls._verify_evaluation_state(package)
        cls._verify_cross_bindings(package)
        evidence_times = [
            package.run.updated_at,
            package.composite_head.updated_at,
            package.evaluation_policy.registered_at,
            package.skill_state.exported_at,
            package.accepted_program_local_rl.created_at,
            package.local_policy_promotion.created_at,
            *(item.updated_at for item in package.cases),
            *(item.completed_at for item in package.track_results),
            *(item.committed_at for item in package.composite_snapshots),
            *(item.recorded_at for item in package.evaluations),
            *(item.recorded_at for item in package.stop_decisions),
        ]
        if package.created_at < max(evidence_times):
            raise IntegratedEvolutionPackageError(
                "Integrated package predates complete evidence."
            )
        if package.created_at > datetime.now(timezone.utc):
            raise IntegratedEvolutionPackageError(
                "Integrated package time must not be in the future."
            )
        if (
            package.foundation_model_training_performed
            or package.production_activation_performed
            or package.production_deployment_performed
            or package.external_rollout_performed
            or package.official_benchmark_claimed
        ):
            raise IntegratedEvolutionPackageError(
                "Integrated package widens its controlled local authority."
            )
        payload = package.model_dump(mode="json", exclude={"package_hash"})
        if package.package_hash != canonical_sha256(payload):
            raise IntegratedEvolutionPackageError(
                "Integrated evolution package hash mismatch."
            )
        return True

    @staticmethod
    def _verify_child_evidence(package) -> None:
        skill = package.skill_child_result
        active_version = package.skill_state.active_versions.get(skill.skill_id)
        active_revision = package.skill_state.active_revisions.get(skill.skill_id)
        active_records = tuple(
            item
            for item in package.skill_state.records
            if item.spec.skill_id == skill.skill_id
            and item.spec.version == active_version
        )
        if (
            len(active_records) != 1
            or active_version != skill.active_version
            or active_revision != 1
            or active_records[0].status.value != "active"
            or active_records[0].evaluation is None
            or not active_records[0].evaluation.promote
            or skill.summary.initial_score != 0.5
            or skill.summary.final_score != 1.0
            or skill.regression_count != 0
        ):
            raise IntegratedEvolutionPackageError(
                "Integrated Skill child evidence differs from one governed promotion."
            )
        accepted = package.accepted_program_local_rl
        promotion = package.local_policy_promotion
        if (
            promotion.accepted_program_package
            != accepted.fully_attested_package
            or promotion.trusted_anchors != accepted.trusted_anchors
            or promotion.acceptance_receipt
            != accepted.acceptance_receipt
            or promotion.rollback_campaign is not None
            or promotion.final_head.revision != 1
            or promotion.final_head.active_policy_id
            != promotion.candidate_record.policy_id
            or promotion.candidate_record.manifest.selected_checkpoint_hash
            != accepted.native_local_rl_package.decision.selected_checkpoint_hash
        ):
            raise IntegratedEvolutionPackageError(
                "Integrated local-policy child evidence differs from v2.1/v2.2 lineage."
            )

    @classmethod
    def _verify_integrated_state(cls, package) -> None:
        run = package.run
        cases = tuple(sorted(package.cases, key=lambda item: item.case.case_id))
        results = tuple(
            sorted(package.track_results, key=lambda item: item.track.value)
        )
        if (
            run.status != IntegratedRunStatus.STOPPED
            or run.round_index != 2
            or run.skill_execution_count != 1
            or run.policy_execution_count != 1
            or len(results) != 2
            or {item.track for item in results}
            != {IntegratedTrack.SKILL, IntegratedTrack.LOCAL_POLICY}
            or any(
                item.status != IntegratedCaseStatus.COMPLETED
                for item in cases
            )
        ):
            raise IntegratedEvolutionPackageError(
                "Integrated run does not represent two completed component rounds."
            )
        by_result = {item.result_id: item for item in results}
        if len(by_result) != 2:
            raise IntegratedEvolutionPackageError(
                "Integrated package contains duplicate track results."
            )
        for case in cases:
            result = by_result.get(case.result_id)
            if (
                result is None
                or case.case.case_id not in result.case_ids
                or case.case.track != result.track
                or case.claimed_by != result.executor_id
            ):
                raise IntegratedEvolutionPackageError(
                    "Integrated Case differs from its exact track result."
                )
        cls._verify_event_chain(
            package.integrated_events,
            package.integrated_checkpoint,
            SQLiteIntegratedEvolutionRepository._event_hash,
            "integrated",
        )
        event_types = tuple(item.event_type for item in package.integrated_events)
        if (
            event_types.count(IntegratedEventType.RUN_CREATED) != 1
            or event_types.count(IntegratedEventType.CASE_ADMITTED) != len(cases)
            or event_types.count(IntegratedEventType.CASES_CLAIMED) != 2
            or event_types.count(IntegratedEventType.TRACK_RESULT_RECORDED) != 2
            or event_types.count(IntegratedEventType.RUN_COMPLETED) != 1
        ):
            raise IntegratedEvolutionPackageError(
                "Integrated audit lifecycle event counts differ."
            )

    @classmethod
    def _verify_composite_state(cls, package) -> None:
        snapshots = tuple(
            sorted(
                package.composite_snapshots,
                key=lambda item: item.manifest.round_index,
            )
        )
        if len(snapshots) != 3 or tuple(
            item.manifest.round_index for item in snapshots
        ) != (0, 1, 2):
            raise IntegratedEvolutionPackageError(
                "Composite package requires contiguous A0, A1 and A2 snapshots."
            )
        if (
            snapshots[0].manifest.parent_snapshot_id is not None
            or snapshots[1].manifest.parent_snapshot_id
            != snapshots[0].snapshot_id
            or snapshots[2].manifest.parent_snapshot_id
            != snapshots[1].snapshot_id
            or changed_component(
                snapshots[0].manifest,
                snapshots[1].manifest,
            )
            != "skill"
            or changed_component(
                snapshots[1].manifest,
                snapshots[2].manifest,
            )
            != "local_policy"
            or tuple(item.status for item in snapshots)
            != (
                CompositeSnapshotStatus.SUPERSEDED,
                CompositeSnapshotStatus.SUPERSEDED,
                CompositeSnapshotStatus.ACTIVE,
            )
            or package.composite_head.active_snapshot_id
            != snapshots[2].snapshot_id
            or package.composite_head.revision != 2
        ):
            raise IntegratedEvolutionPackageError(
                "Composite snapshot pointer or one-component lineage differs."
            )
        cls._verify_event_chain(
            package.composite_events,
            package.composite_checkpoint,
            SQLiteCompositeSnapshotRegistry._event_hash,
            "composite",
        )
        if tuple(item.event_type for item in package.composite_events) != (
            CompositeEventType.REGISTERED,
            CompositeEventType.COMMITTED,
            CompositeEventType.COMMITTED,
        ):
            raise IntegratedEvolutionPackageError(
                "Composite audit does not match A0 -> A1 -> A2 commits."
            )

    @classmethod
    def _verify_evaluation_state(cls, package) -> None:
        evaluations = tuple(
            sorted(
                package.evaluations,
                key=lambda item: item.evaluation.round_index,
            )
        )
        decisions = tuple(
            sorted(
                package.stop_decisions,
                key=lambda item: item.decision.round_index,
            )
        )
        if len(evaluations) != 3 or len(decisions) != 3:
            raise IntegratedEvolutionPackageError(
                "Integrated package requires one Evaluation/Decision per snapshot."
            )
        scores = tuple(item.evaluation.composite_score for item in evaluations)
        actions = tuple(item.decision.action for item in decisions)
        if scores != (0.25, 0.5, 1.0) or actions != (
            CompositeStopAction.CONTINUE,
            CompositeStopAction.CONTINUE,
            CompositeStopAction.STOP,
        ):
            raise IntegratedEvolutionPackageError(
                "Composite score or bounded stop sequence differs from target."
            )
        for index, (evaluation_record, decision_record) in enumerate(
            zip(evaluations, decisions, strict=True)
        ):
            evaluation = evaluation_record.evaluation
            decision = decision_record.decision
            if (
                evaluation.snapshot_id
                != package.composite_snapshots[index].snapshot_id
                or decision.snapshot_id != evaluation.snapshot_id
                or decision.evaluation_hash != evaluation.evaluation_hash
                or decision.policy_hash
                != package.evaluation_policy.policy.policy_hash
            ):
                raise IntegratedEvolutionPackageError(
                    "Evaluation or stop decision differs from snapshot/policy evidence."
                )
            expected = build_composite_stop_decision(
                evaluation,
                package.evaluation_policy.policy,
                decision_id=decision.decision_id,
                actionable_case_ids=decision.actionable_case_ids,
                budget_exhausted=decision.budget_exhausted,
                decided_by=decision.decided_by,
                decided_at=decision.decided_at,
            )
            if expected != decision:
                raise IntegratedEvolutionPackageError(
                    "Persisted stop decision differs from deterministic policy."
                )
        if (
            evaluations[0].evaluation.parent_evaluation_hash is not None
            or evaluations[1].evaluation.parent_evaluation_hash
            != evaluations[0].evaluation.evaluation_hash
            or evaluations[2].evaluation.parent_evaluation_hash
            != evaluations[1].evaluation.evaluation_hash
            or evaluations[2].evaluation.safety_violation_count != 0
            or any(item.evaluation.regression_count for item in evaluations)
        ):
            raise IntegratedEvolutionPackageError(
                "Composite evaluation parent, safety or regression evidence differs."
            )
        cls._verify_event_chain(
            package.evaluation_events,
            package.evaluation_checkpoint,
            SQLiteCompositeEvaluationRepository._event_hash,
            "evaluation",
        )

    @staticmethod
    def _verify_cross_bindings(package) -> None:
        results = {item.track: item for item in package.track_results}
        snapshots = sorted(
            package.composite_snapshots,
            key=lambda item: item.manifest.round_index,
        )
        skill_result = results[IntegratedTrack.SKILL]
        policy_result = results[IntegratedTrack.LOCAL_POLICY]
        if (
            snapshots[1].manifest.source_case_ids != skill_result.case_ids
            or not set(skill_result.source_decision_hashes).issubset(
                snapshots[1].manifest.source_decision_hashes
            )
            or not set(skill_result.source_package_hashes).issubset(
                snapshots[1].manifest.source_package_hashes
            )
            or snapshots[1].manifest.skill.content_hash
            != skill_result.component_hash
            or snapshots[2].manifest.source_case_ids != policy_result.case_ids
            or not set(policy_result.source_decision_hashes).issubset(
                snapshots[2].manifest.source_decision_hashes
            )
            or not set(policy_result.source_package_hashes).issubset(
                snapshots[2].manifest.source_package_hashes
            )
            or snapshots[2].manifest.local_policy.checkpoint_hash
            != policy_result.component_hash
            or package.run.terminal_decision_hash
            != package.stop_decisions[-1].decision.decision_hash
        ):
            raise IntegratedEvolutionPackageError(
                "Integrated results, snapshots or terminal decision are not cross-bound."
            )

    @staticmethod
    def _verify_event_chain(events, checkpoint, hash_function, label: str) -> None:
        previous = "0" * 64
        for expected_sequence, event in enumerate(events, start=1):
            if event.sequence != expected_sequence or event.previous_hash != previous:
                raise IntegratedEvolutionPackageError(
                    f"{label} audit sequence or previous hash differs."
                )
            payload = event.model_dump(mode="python", exclude={"event_hash"})
            try:
                expected_hash = hash_function(**payload)
            except TypeError:
                # Each Repository hash function uses the same event fields but
                # enum values are accepted by the Pydantic-backed implementations.
                expected_hash = hash_function(
                    **event.model_dump(mode="python", exclude={"event_hash"})
                )
            if event.event_hash != expected_hash:
                raise IntegratedEvolutionPackageError(
                    f"{label} audit event content hash differs."
                )
            previous = event.event_hash
        if (
            checkpoint.event_count != len(events)
            or checkpoint.head_hash != previous
        ):
            raise IntegratedEvolutionPackageError(
                f"{label} audit checkpoint differs from the complete chain."
            )

    def export_file(
        self,
        package: IntegratedEvolutionPackageManifest,
        path: str | Path,
    ) -> Path:
        self.verify(package)
        destination = Path(path).expanduser()
        if destination.is_symlink():
            raise IntegratedEvolutionPackageError(
                "Integrated package output must not be a symlink."
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        encoded = package.model_dump_json(indent=2) + "\n"
        if destination.exists():
            if (
                not destination.is_file()
                or destination.read_text(encoding="utf-8") != encoded
            ):
                raise IntegratedEvolutionPackageError(
                    "Existing integrated package differs from immutable evidence."
                )
            return destination
        destination.write_text(encoded, encoding="utf-8")
        return destination

    def load_file(
        self,
        path: str | Path,
    ) -> IntegratedEvolutionPackageManifest:
        destination = Path(path).expanduser()
        if destination.is_symlink() or not destination.is_file():
            raise IntegratedEvolutionPackageError(
                "Integrated package must be a regular file."
            )
        try:
            package = IntegratedEvolutionPackageManifest.model_validate_json(
                destination.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise IntegratedEvolutionPackageError(
                f"Integrated package is invalid: {exc}"
            ) from exc
        self.verify(package)
        return package


__all__ = [
    "IntegratedEvolutionPackageError",
    "IntegratedEvolutionPackageManager",
    "IntegratedEvolutionPackageManifest",
]
