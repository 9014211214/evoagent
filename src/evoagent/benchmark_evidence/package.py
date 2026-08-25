from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from evoagent._io import atomic_temporary_path
from evoagent.benchmark_evidence.comparison import (
    BenchmarkComparator,
    assess_submission_eligibility,
)
from evoagent.benchmark_evidence.models import (
    BenchmarkEvidenceAuditEvent,
    BenchmarkEvidenceCheckpoint,
    BenchmarkEvidenceEventType,
    BenchmarkRunEvidence,
    BenchmarkSubmissionEligibility,
    BenchmarkSuiteIdentity,
    LongitudinalComparisonReport,
    SameModelCrossAgentReport,
)
from evoagent.benchmark_evidence.repository import (
    SQLiteBenchmarkEvidenceRepository,
)
from evoagent.model_registry.models import canonical_sha256, validate_safe_content


_SHA1_PATTERN = r"^[0-9a-f]{40}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_GENESIS_HASH = "0" * 64


class BenchmarkComparisonPackageError(ValueError):
    pass


class BenchmarkComparisonPackageManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    format_version: Literal["evoagent-benchmark-comparison-package-v1"] = (
        "evoagent-benchmark-comparison-package-v1"
    )
    package_id: str
    created_at: datetime
    framework_version: str
    source_repository: str
    source_commit: str = Field(pattern=_SHA1_PATTERN)
    third_party_lock_hash: str = Field(pattern=_SHA256_PATTERN)
    suite: BenchmarkSuiteIdentity
    runs: tuple[BenchmarkRunEvidence, ...]
    longitudinal: LongitudinalComparisonReport
    same_model_cross_agent: SameModelCrossAgentReport
    eligibility: tuple[BenchmarkSubmissionEligibility, ...]
    audit_events: tuple[BenchmarkEvidenceAuditEvent, ...]
    audit_checkpoint: BenchmarkEvidenceCheckpoint
    package_hash: str = Field(pattern=_SHA256_PATTERN)
    synthetic_fixture: bool
    harbor_execution_performed_by_evoagent: Literal[False] = False
    external_model_call_performed_by_evoagent: Literal[False] = False
    checkpoint_downloaded_or_loaded: Literal[False] = False
    upload_performed: Literal[False] = False
    official_submission_performed: Literal[False] = False
    official_submission_accepted: Literal[False] = False
    production_deployment_performed: Literal[False] = False

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Benchmark comparison package time must include a timezone.")
        return value


class BenchmarkComparisonPackageManager:
    def build(
        self,
        *,
        package_id: str,
        created_at: datetime,
        framework_version: str,
        source_repository: str,
        source_commit: str,
        third_party_lock_hash: str,
        suite: BenchmarkSuiteIdentity,
        runs: tuple[BenchmarkRunEvidence, ...],
        longitudinal: LongitudinalComparisonReport,
        same_model_cross_agent: SameModelCrossAgentReport,
        eligibility: tuple[BenchmarkSubmissionEligibility, ...],
        audit_events: tuple[BenchmarkEvidenceAuditEvent, ...],
        audit_checkpoint: BenchmarkEvidenceCheckpoint,
    ) -> BenchmarkComparisonPackageManifest:
        synthetic_fixture = all(
            item.contract.source.value == "synthetic_fixture" for item in runs
        )
        provisional = BenchmarkComparisonPackageManifest(
            package_id=package_id,
            created_at=created_at,
            framework_version=framework_version,
            source_repository=source_repository,
            source_commit=source_commit,
            third_party_lock_hash=third_party_lock_hash,
            suite=suite,
            runs=runs,
            longitudinal=longitudinal,
            same_model_cross_agent=same_model_cross_agent,
            eligibility=eligibility,
            audit_events=audit_events,
            audit_checkpoint=audit_checkpoint,
            package_hash="0" * 64,
            synthetic_fixture=synthetic_fixture,
        )
        payload = provisional.model_dump(mode="json", exclude={"package_hash"})
        validate_safe_content(payload)
        manifest = provisional.model_copy(
            update={"package_hash": canonical_sha256(payload)}
        )
        self.verify(manifest)
        return manifest

    def verify(self, manifest: BenchmarkComparisonPackageManifest) -> bool:
        payload = manifest.model_dump(mode="json", exclude={"package_hash"})
        validate_safe_content(payload)
        if manifest.package_hash != canonical_sha256(payload):
            raise BenchmarkComparisonPackageError(
                "Benchmark comparison package hash mismatch."
            )
        if not manifest.runs:
            raise BenchmarkComparisonPackageError(
                "Benchmark comparison package requires run evidence."
            )
        by_id = {item.evidence_id: item for item in manifest.runs}
        if len(by_id) != len(manifest.runs):
            raise BenchmarkComparisonPackageError(
                "Benchmark comparison package contains duplicate run evidence IDs."
            )
        if any(item.contract.suite != manifest.suite for item in manifest.runs):
            raise BenchmarkComparisonPackageError(
                "Packaged run suite differs from the package suite."
            )
        expected_synthetic = all(
            item.contract.source.value == "synthetic_fixture"
            for item in manifest.runs
        )
        if manifest.synthetic_fixture != expected_synthetic:
            raise BenchmarkComparisonPackageError(
                "Benchmark package synthetic-fixture flag is not derived."
            )

        comparator = BenchmarkComparator()
        try:
            longitudinal_runs = tuple(
                by_id[run_id] for run_id in manifest.longitudinal.run_ids
            )
            same_model_runs = tuple(
                by_id[run_id]
                for run_id in manifest.same_model_cross_agent.run_ids
            )
        except KeyError as exc:
            raise BenchmarkComparisonPackageError(
                "Benchmark report references missing run evidence."
            ) from exc
        expected_longitudinal = comparator.longitudinal(
            longitudinal_runs,
            comparison_id=manifest.longitudinal.comparison_id,
        )
        if expected_longitudinal != manifest.longitudinal:
            raise BenchmarkComparisonPackageError(
                "Packaged longitudinal comparison differs from run evidence."
            )
        expected_same_model = comparator.same_model_cross_agent(
            same_model_runs,
            anchor_run_id=manifest.same_model_cross_agent.anchor_run_id,
            comparison_id=manifest.same_model_cross_agent.comparison_id,
        )
        if expected_same_model != manifest.same_model_cross_agent:
            raise BenchmarkComparisonPackageError(
                "Packaged same-model comparison differs from run evidence."
            )

        eligibility_by_id = {
            item.evidence_id: item for item in manifest.eligibility
        }
        if set(eligibility_by_id) != set(by_id):
            raise BenchmarkComparisonPackageError(
                "Benchmark package requires one eligibility assessment per run."
            )
        for evidence_id, run in by_id.items():
            expected = assess_submission_eligibility(run)
            if eligibility_by_id[evidence_id] != expected:
                raise BenchmarkComparisonPackageError(
                    "Benchmark submission eligibility was rewritten or miscomputed."
                )
            if (
                eligibility_by_id[evidence_id].official_submission_performed
                or eligibility_by_id[evidence_id].official_submission_accepted
            ):
                raise BenchmarkComparisonPackageError(
                    "Internal benchmark evidence cannot claim official submission acceptance."
                )
        self._verify_audit(manifest, by_id)
        return True

    @staticmethod
    def _verify_audit(
        manifest: BenchmarkComparisonPackageManifest,
        by_id: dict[str, BenchmarkRunEvidence],
    ) -> None:
        events = manifest.audit_events
        previous_hash = _GENESIS_HASH
        for expected_sequence, event in enumerate(events, start=1):
            if event.sequence != expected_sequence or event.previous_hash != previous_hash:
                raise BenchmarkComparisonPackageError(
                    "Packaged benchmark audit chain is broken."
                )
            expected_hash = SQLiteBenchmarkEvidenceRepository._event_hash(
                sequence=event.sequence,
                event_id=event.event_id,
                event_type=event.event_type,
                subject_id=event.subject_id,
                payload=event.payload,
                actor_id=event.actor_id,
                created_at=event.created_at,
                previous_hash=event.previous_hash,
            )
            if event.event_hash != expected_hash:
                raise BenchmarkComparisonPackageError(
                    "Packaged benchmark audit event content was modified."
                )
            previous_hash = event.event_hash
        checkpoint = BenchmarkEvidenceCheckpoint(
            event_count=len(events),
            head_hash=previous_hash,
        )
        if checkpoint != manifest.audit_checkpoint:
            raise BenchmarkComparisonPackageError(
                "Packaged benchmark events do not match the checkpoint."
            )
        run_events = {
            event.subject_id: event
            for event in events
            if event.event_type == BenchmarkEvidenceEventType.RUN_IMPORTED
        }
        if set(run_events) != set(by_id):
            raise BenchmarkComparisonPackageError(
                "Benchmark audit does not contain exactly one import per run."
            )
        for evidence_id, run in by_id.items():
            event = run_events[evidence_id]
            if event.payload != {
                "evidence_hash": run.evidence_hash,
                "raw_file_sha256": run.source_file_sha256,
                "contract_hash": run.contract.contract_hash,
            }:
                raise BenchmarkComparisonPackageError(
                    "Benchmark run import event differs from run evidence."
                )
        comparison_events = {
            event.subject_id: event
            for event in events
            if event.event_type
            in {
                BenchmarkEvidenceEventType.LONGITUDINAL_COMPARISON_STORED,
                BenchmarkEvidenceEventType.SAME_MODEL_COMPARISON_STORED,
            }
        }
        expected_reports = {
            manifest.longitudinal.comparison_id: manifest.longitudinal,
            manifest.same_model_cross_agent.comparison_id: (
                manifest.same_model_cross_agent
            ),
        }
        if set(comparison_events) != set(expected_reports):
            raise BenchmarkComparisonPackageError(
                "Benchmark audit does not contain the exact comparison records."
            )
        for comparison_id, report in expected_reports.items():
            event = comparison_events[comparison_id]
            if event.payload != {
                "mode": report.mode.value,
                "report_hash": report.report_hash,
                "run_ids": list(report.run_ids),
            }:
                raise BenchmarkComparisonPackageError(
                    "Benchmark comparison event differs from its report."
                )

    def export_file(
        self,
        manifest: BenchmarkComparisonPackageManifest,
        path: str | Path,
    ) -> Path:
        self.verify(manifest)
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_symlink():
            raise BenchmarkComparisonPackageError(
                "Benchmark package output must not be a symlink."
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

    def load_file(
        self,
        path: str | Path,
    ) -> BenchmarkComparisonPackageManifest:
        target = Path(path)
        if target.is_symlink() or not target.is_file():
            raise BenchmarkComparisonPackageError(
                "Benchmark comparison package must be a regular non-symlink file."
            )
        try:
            manifest = BenchmarkComparisonPackageManifest.model_validate_json(
                target.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise BenchmarkComparisonPackageError(
                "Benchmark comparison package is invalid."
            ) from exc
        self.verify(manifest)
        return manifest


__all__ = [
    "BenchmarkComparisonPackageError",
    "BenchmarkComparisonPackageManager",
    "BenchmarkComparisonPackageManifest",
]
