from __future__ import annotations

import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from evoagent.local_rl.evaluation import (
    IndependentLocalPolicyEvaluator,
    LocalPolicyCheckpointSelector,
)
from evoagent.local_rl.models import (
    LocalRLAuditEvent,
    LocalRLEvaluationReport,
    LocalRLEventType,
    LocalRLRegistryCheckpoint,
    LocalRLRunManifest,
    LocalRLSelectionDecision,
    LocalRLTrainingResult,
)
from evoagent.local_rl.optimizer import LocalGroupRelativePolicyOptimizer
from evoagent.local_rl.repository import SQLiteLocalRLRepository
from evoagent.model_registry.models import canonical_sha256, validate_safe_content


_SHA1_PATTERN = r"^[0-9a-f]{40}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_GENESIS_HASH = "0" * 64


class LocalRLPackageError(ValueError):
    pass


class LocalRLPackageManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    format_version: Literal["evoagent-local-agentic-rl-package-v1"] = (
        "evoagent-local-agentic-rl-package-v1"
    )
    package_id: str
    created_at: datetime
    framework_version: str
    source_repository: str
    source_commit: str = Field(pattern=_SHA1_PATTERN)
    third_party_lock_hash: str = Field(pattern=_SHA256_PATTERN)
    trainer_id: str
    manifest: LocalRLRunManifest
    training: LocalRLTrainingResult
    baseline_evaluation: LocalRLEvaluationReport
    candidate_evaluations: tuple[LocalRLEvaluationReport, ...]
    decision: LocalRLSelectionDecision
    audit_events: tuple[LocalRLAuditEvent, ...]
    audit_checkpoint: LocalRLRegistryCheckpoint
    package_hash: str = Field(pattern=_SHA256_PATTERN)
    tiny_tabular_policy_only: Literal[True] = True
    local_rollout_training_executed_by_evoagent: Literal[True] = True
    foundation_model_training_performed: Literal[False] = False
    external_model_call_performed_by_evoagent: Literal[False] = False
    gpu_execution_performed: Literal[False] = False
    network_execution_performed: Literal[False] = False
    production_deployment_performed: Literal[False] = False
    upload_performed: Literal[False] = False
    official_benchmark_claimed: Literal[False] = False

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Local RL package time must include a timezone.")
        return value


class LocalRLPackageManager:
    def build(
        self,
        *,
        package_id: str,
        created_at: datetime,
        framework_version: str,
        source_repository: str,
        source_commit: str,
        third_party_lock_hash: str,
        trainer_id: str,
        manifest: LocalRLRunManifest,
        training: LocalRLTrainingResult,
        baseline_evaluation: LocalRLEvaluationReport,
        candidate_evaluations: tuple[LocalRLEvaluationReport, ...],
        decision: LocalRLSelectionDecision,
        audit_events: tuple[LocalRLAuditEvent, ...],
        audit_checkpoint: LocalRLRegistryCheckpoint,
    ) -> LocalRLPackageManifest:
        provisional = LocalRLPackageManifest(
            package_id=package_id,
            created_at=created_at,
            framework_version=framework_version,
            source_repository=source_repository,
            source_commit=source_commit,
            third_party_lock_hash=third_party_lock_hash,
            trainer_id=trainer_id,
            manifest=manifest,
            training=training,
            baseline_evaluation=baseline_evaluation,
            candidate_evaluations=candidate_evaluations,
            decision=decision,
            audit_events=audit_events,
            audit_checkpoint=audit_checkpoint,
            package_hash="0" * 64,
        )
        payload = provisional.model_dump(mode="json", exclude={"package_hash"})
        validate_safe_content(payload)
        package = provisional.model_copy(
            update={"package_hash": canonical_sha256(payload)}
        )
        self.verify(package)
        return package

    def verify(self, package: LocalRLPackageManifest) -> bool:
        payload = package.model_dump(mode="json", exclude={"package_hash"})
        validate_safe_content(payload)
        if package.package_hash != canonical_sha256(payload):
            raise LocalRLPackageError("Local RL package hash mismatch.")
        if package.training.manifest_hash != package.manifest.manifest_hash:
            raise LocalRLPackageError(
                "Local RL training result differs from the frozen manifest."
            )
        expected_training = LocalGroupRelativePolicyOptimizer().train(
            package.manifest
        )
        if expected_training != package.training:
            raise LocalRLPackageError(
                "Packaged Local RL parameters or rollout metrics are not reproducible."
            )

        evaluator = IndependentLocalPolicyEvaluator()
        expected_baseline = evaluator.evaluate(
            package.manifest,
            package.training.initial_checkpoint,
            evaluator_id=package.baseline_evaluation.evaluator_id,
            trainer_id=package.trainer_id,
        )
        if expected_baseline != package.baseline_evaluation:
            raise LocalRLPackageError(
                "Packaged Local RL baseline evaluation is not reproducible."
            )
        if len(package.candidate_evaluations) != len(
            package.training.retained_checkpoints
        ):
            raise LocalRLPackageError(
                "Local RL package requires one evaluation per retained checkpoint."
            )
        expected_candidates = tuple(
            evaluator.evaluate(
                package.manifest,
                checkpoint,
                evaluator_id=report.evaluator_id,
                trainer_id=package.trainer_id,
            )
            for checkpoint, report in zip(
                package.training.retained_checkpoints,
                package.candidate_evaluations,
                strict=True,
            )
        )
        if expected_candidates != package.candidate_evaluations:
            raise LocalRLPackageError(
                "Packaged Local RL candidate evaluations are not reproducible."
            )
        expected_decision = LocalPolicyCheckpointSelector().decide(
            package.manifest,
            package.training,
            package.baseline_evaluation,
            package.candidate_evaluations,
            decision_id=package.decision.decision_id,
            decision_actor_id=package.decision.decision_actor_id,
            decided_at=package.decision.decided_at,
        )
        if expected_decision != package.decision:
            raise LocalRLPackageError(
                "Packaged Local RL checkpoint selection is not reproducible."
            )
        selected_report = next(
            item
            for item in package.candidate_evaluations
            if item.checkpoint_hash == package.decision.selected_checkpoint_hash
        )
        if selected_report.report_hash != package.decision.selected_report_hash:
            raise LocalRLPackageError(
                "Local RL decision report binding was substituted."
            )
        if selected_report.overall_score <= package.baseline_evaluation.overall_score:
            raise LocalRLPackageError(
                "Selected Local RL checkpoint lacks strict held-out improvement."
            )
        if selected_report.unsafe_action_count:
            raise LocalRLPackageError(
                "Selected Local RL checkpoint contains unsafe held-out actions."
            )
        self._verify_audit(package)
        return True

    @staticmethod
    def _verify_audit(package: LocalRLPackageManifest) -> None:
        previous_hash = _GENESIS_HASH
        for expected_sequence, event in enumerate(package.audit_events, start=1):
            if event.sequence != expected_sequence or event.previous_hash != previous_hash:
                raise LocalRLPackageError(
                    "Packaged Local RL audit sequence or chain is broken."
                )
            expected_hash = SQLiteLocalRLRepository._event_hash(
                sequence=event.sequence,
                event_id=event.event_id,
                event_type=event.event_type,
                run_id=event.run_id,
                actor_id=event.actor_id,
                reason=event.reason,
                payload=event.payload,
                created_at=event.created_at,
                previous_hash=event.previous_hash,
            )
            if event.event_hash != expected_hash:
                raise LocalRLPackageError(
                    "Packaged Local RL audit event content was modified."
                )
            previous_hash = event.event_hash
        checkpoint = LocalRLRegistryCheckpoint(
            event_count=len(package.audit_events),
            head_hash=previous_hash,
        )
        if checkpoint != package.audit_checkpoint:
            raise LocalRLPackageError(
                "Packaged Local RL audit does not match its checkpoint."
            )
        event_types = tuple(item.event_type for item in package.audit_events)
        expected_types = (
            LocalRLEventType.RUN_REGISTERED,
            LocalRLEventType.TRAINING_COMPLETED,
            LocalRLEventType.EVALUATION_STORED,
            LocalRLEventType.SELECTION_STORED,
        )
        if event_types != expected_types:
            raise LocalRLPackageError(
                "Local RL lifecycle audit events are missing, duplicated, or reordered."
            )
        registered, trained, evaluated, selected = package.audit_events
        if registered.payload != {
            "manifest_hash": package.manifest.manifest_hash
        }:
            raise LocalRLPackageError(
                "Local RL registration event differs from the manifest."
            )
        if trained.payload != {
            "training_result_hash": package.training.result_hash,
            "initial_checkpoint_hash": (
                package.training.initial_checkpoint.checkpoint_hash
            ),
            "final_checkpoint_hash": (
                package.training.retained_checkpoints[-1].checkpoint_hash
            ),
            "iterations": package.training.usage.iterations,
            "rollouts": package.training.usage.rollouts,
            "episode_steps": package.training.usage.episode_steps,
            "parameter_updates": package.training.usage.parameter_updates,
        }:
            raise LocalRLPackageError(
                "Local RL training event differs from the training result."
            )
        if evaluated.payload != {
            "baseline_report_hash": package.baseline_evaluation.report_hash,
            "candidate_report_hashes": [
                item.report_hash for item in package.candidate_evaluations
            ],
            "task_manifest_hash": package.baseline_evaluation.task_manifest_hash,
        }:
            raise LocalRLPackageError(
                "Local RL evaluation event differs from the reports."
            )
        if selected.payload != {
            "decision_hash": package.decision.decision_hash,
            "selected_checkpoint_hash": package.decision.selected_checkpoint_hash,
            "selected_iteration": package.decision.selected_iteration,
        }:
            raise LocalRLPackageError(
                "Local RL selection event differs from the decision."
            )

    def export_file(
        self,
        package: LocalRLPackageManifest,
        path: str | Path,
    ) -> Path:
        self.verify(package)
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_symlink():
            raise LocalRLPackageError(
                "Local RL package output must not be a symlink."
            )
        temporary = destination.with_name(
            f".{destination.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(package.model_dump_json(indent=2) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
        return destination

    def load_file(self, path: str | Path) -> LocalRLPackageManifest:
        target = Path(path)
        if target.is_symlink() or not target.is_file():
            raise LocalRLPackageError(
                "Local RL package must be a regular non-symlink file."
            )
        try:
            package = LocalRLPackageManifest.model_validate_json(
                target.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise LocalRLPackageError(f"Local RL package is invalid: {exc}") from exc
        self.verify(package)
        return package


__all__ = [
    "LocalRLPackageError",
    "LocalRLPackageManager",
    "LocalRLPackageManifest",
]
