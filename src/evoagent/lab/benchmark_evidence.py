from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from evoagent import __version__
from evoagent.benchmark_evidence.builders import (
    build_agent_identity,
    build_benchmark_suite,
    build_model_identity,
    build_run_contract,
)
from evoagent.benchmark_evidence.comparison import (
    BenchmarkComparator,
    BenchmarkComparisonError,
    assess_submission_eligibility,
)
from evoagent.benchmark_evidence.importer import HarborResultImporter
from evoagent.benchmark_evidence.models import (
    BenchmarkEvidenceSource,
    BenchmarkRunContract,
    BenchmarkRunEvidence,
    BenchmarkRunRole,
    BenchmarkTaskIdentity,
    TERMINAL_BENCH_2_1,
)
from evoagent.benchmark_evidence.package import (
    BenchmarkComparisonPackageManager,
)
from evoagent.benchmark_evidence.repository import (
    SQLiteBenchmarkEvidenceRepository,
)
from evoagent.lab.service import DEFAULT_THIRD_PARTY_LOCK_HASH
from evoagent.model_registry.models import canonical_sha256


class AuthoritativeBenchmarkEvidenceLabResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    resumed: bool
    evidence_ids: tuple[str, ...]
    comparison_ids: tuple[str, ...]
    a0_score: float
    a1_score: float
    a2_score: float
    final_gain: float
    best_round: int = Field(ge=0)
    monotonic_score: bool
    improved_tasks: int = Field(ge=0)
    regressed_tasks: int = Field(ge=0)
    tied_tasks: int = Field(ge=0)
    comparator_score: float
    anchor_rank: int = Field(gt=0)
    anchor_wins: int = Field(ge=0)
    anchor_losses: int = Field(ge=0)
    anchor_ties: int = Field(ge=0)
    mismatched_model_rejected: Literal[True] = True
    submission_prerequisites_met_count: int = Field(ge=0)
    registry_event_count: int = Field(ge=0)
    registry_checkpoint: dict
    package_path: str
    package_hash: str
    restart_verified: Literal[True] = True
    synthetic_fixture: Literal[True] = True
    harbor_execution_performed_by_evoagent: Literal[False] = False
    external_model_call_performed_by_evoagent: Literal[False] = False
    checkpoint_downloaded_or_loaded: Literal[False] = False
    upload_performed: Literal[False] = False
    official_submission_performed: Literal[False] = False
    official_submission_accepted: Literal[False] = False
    production_deployment_performed: Literal[False] = False


class AuthoritativeBenchmarkEvidenceLab:
    """Offline Harbor-shaped evidence for fair A0…AN and same-model comparison."""

    RUN_ID = "authoritative-benchmark-evidence-lab-v1"
    PACKAGE_ID = "benchmark-comparison-package-v1"
    LONGITUDINAL_ID = "comparison:longitudinal-a0-a2"
    SAME_MODEL_ID = "comparison:same-model-a2-vs-comparator"
    STARTED_AT = datetime(2026, 8, 11, 9, 0, tzinfo=timezone.utc)

    def __init__(
        self,
        root: str | Path,
        *,
        source_commit: str = "0" * 40,
        source_repository: str = (
            "https://github.com/9014211214/evoagent"
        ),
    ):
        raw_root = Path(root).expanduser()
        if raw_root.is_symlink():
            raise ValueError("Benchmark evidence lab root must not be a symlink.")
        if len(source_commit) != 40 or any(
            character not in "0123456789abcdef"
            for character in source_commit
        ):
            raise ValueError("source_commit must be lowercase 40-character Git hex.")
        self.root = raw_root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.source_commit = source_commit
        self.source_repository = source_repository

    @property
    def fixtures_root(self) -> Path:
        return self.root / "harbor-fixtures"

    @property
    def registry_database(self) -> Path:
        return self.root / "benchmark-evidence.db"

    @property
    def package_path(self) -> Path:
        return self.root / "benchmark-comparison-package.json"

    def run(self) -> AuthoritativeBenchmarkEvidenceLabResult:
        suite = self._suite()
        contracts = self._contracts(suite)
        fixture_payloads = self._fixture_payloads(contracts)
        fixture_hashes = self._write_or_verify_fixtures(fixture_payloads)
        importer = HarborResultImporter(self.fixtures_root)
        imported = {
            key: importer.import_file(
                f"{key}/result.json",
                expected_sha256=fixture_hashes[key],
                evidence_id=f"benchmark-run:{key}",
                contract=contracts[key],
            )
            for key in sorted(contracts)
        }
        repository = SQLiteBenchmarkEvidenceRepository(self.registry_database)
        package_existed = self.package_path.exists()
        for key in sorted(imported):
            stored, reused = repository.import_run(imported[key])
            if stored != imported[key]:
                raise RuntimeError("Benchmark Registry changed imported evidence.")
            if package_existed != reused:
                raise RuntimeError(
                    "Benchmark run import reuse does not match package restart state."
                )

        comparator = BenchmarkComparator()
        longitudinal = comparator.longitudinal(
            (imported["a0"], imported["a1"], imported["a2"]),
            comparison_id=self.LONGITUDINAL_ID,
        )
        same_model = comparator.same_model_cross_agent(
            (imported["a2"], imported["comparator"]),
            anchor_run_id=imported["a2"].evidence_id,
            comparison_id=self.SAME_MODEL_ID,
        )
        mismatched_rejected = False
        try:
            comparator.same_model_cross_agent(
                (imported["a2"], imported["mismatch"]),
                anchor_run_id=imported["a2"].evidence_id,
                comparison_id="comparison:mismatched-model-control",
            )
        except BenchmarkComparisonError:
            mismatched_rejected = True
        if not mismatched_rejected:
            raise RuntimeError("Same-model comparison accepted a mismatched Model.")

        for report in (longitudinal, same_model):
            stored, reused = repository.store_comparison(report)
            if stored != report:
                raise RuntimeError("Benchmark Registry changed comparison evidence.")
            if package_existed != reused:
                raise RuntimeError(
                    "Benchmark comparison reuse does not match package restart state."
                )
        eligibility = tuple(
            assess_submission_eligibility(imported[key])
            for key in sorted(imported)
        )
        if any(item.submission_prerequisites_met for item in eligibility):
            raise RuntimeError(
                "Synthetic Harbor-shaped fixtures cannot meet submission prerequisites."
            )
        repository.verify_state()
        events = tuple(repository.events())
        checkpoint = repository.checkpoint()
        runs = tuple(repository.list_runs())
        comparisons = {
            item.comparison_id: item
            for item in repository.list_comparisons()
        }
        if comparisons != {
            longitudinal.comparison_id: longitudinal,
            same_model.comparison_id: same_model,
        }:
            raise RuntimeError("Persistent benchmark comparisons differ.")

        manager = BenchmarkComparisonPackageManager()
        if not package_existed:
            package = manager.build(
                package_id=self.PACKAGE_ID,
                created_at=datetime.now(timezone.utc),
                framework_version=__version__,
                source_repository=self.source_repository,
                source_commit=self.source_commit,
                third_party_lock_hash=DEFAULT_THIRD_PARTY_LOCK_HASH,
                suite=suite,
                runs=runs,
                longitudinal=longitudinal,
                same_model_cross_agent=same_model,
                eligibility=eligibility,
                audit_events=events,
                audit_checkpoint=checkpoint,
            )
            manager.export_file(package, self.package_path)
        else:
            package = manager.load_file(self.package_path)
            if (
                package.runs != runs
                or package.longitudinal != longitudinal
                or package.same_model_cross_agent != same_model
                or package.eligibility != eligibility
                or package.audit_events != events
                or package.audit_checkpoint != checkpoint
            ):
                raise RuntimeError(
                    "Read-only benchmark evidence resume differs from the package."
                )
        self._verify_restart(package)
        return self._result(
            package,
            resumed=package_existed,
            mismatched_model_rejected=mismatched_rejected,
        )

    def _suite(self):
        tasks = tuple(
            BenchmarkTaskIdentity(
                task_name=f"synthetic-terminal-task-{letter}",
                task_id=f"synthetic-task-id-{letter}",
                task_checksum=canonical_sha256(
                    {
                        "kind": "synthetic-terminal-bench-task",
                        "task": letter,
                    }
                ),
            )
            for letter in ("a", "b", "c", "d")
        )
        return build_benchmark_suite(
            suite_id="terminal-bench-2.1-synthetic-manifest-v1",
            tasks=tasks,
            primary_reward_key="reward",
            canonical_task_manifest_attested=False,
        )

    def _contracts(self, suite) -> dict[str, BenchmarkRunContract]:
        model = build_model_identity(
            provider="synthetic-provider",
            name="synthetic-model-4b",
            revision="fixture-revision-v1",
            config_sha256=canonical_sha256("synthetic-model-config-v1"),
            inference_settings_sha256=canonical_sha256(
                {"temperature": 0.0, "seed": 17}
            ),
        )
        mismatched_model = build_model_identity(
            provider="synthetic-provider",
            name="different-synthetic-model-4b",
            revision="fixture-revision-v1",
            config_sha256=canonical_sha256("different-model-config-v1"),
            inference_settings_sha256=canonical_sha256(
                {"temperature": 0.0, "seed": 17}
            ),
        )
        agents = {
            "a0": build_agent_identity(
                family_id="evoagent-benchmark-family",
                name="evoagent-reference",
                version="a0",
                source_commit=self.source_commit,
                config_sha256=canonical_sha256("evoagent-a0-config"),
                snapshot_id="evoagent-a0",
                evolution_round=0,
                parent_snapshot_id=None,
            ),
            "a1": build_agent_identity(
                family_id="evoagent-benchmark-family",
                name="evoagent-reference",
                version="a1",
                source_commit=self.source_commit,
                config_sha256=canonical_sha256("evoagent-a1-config"),
                snapshot_id="evoagent-a1",
                evolution_round=1,
                parent_snapshot_id="evoagent-a0",
            ),
            "a2": build_agent_identity(
                family_id="evoagent-benchmark-family",
                name="evoagent-reference",
                version="a2",
                source_commit=self.source_commit,
                config_sha256=canonical_sha256("evoagent-a2-config"),
                snapshot_id="evoagent-a2",
                evolution_round=2,
                parent_snapshot_id="evoagent-a1",
            ),
            "comparator": build_agent_identity(
                family_id="external-comparator-family",
                name="reference-comparator",
                version="1.0.0",
                source_commit="c" * 40,
                config_sha256=canonical_sha256("comparator-config"),
                snapshot_id="comparator-v1",
                evolution_round=0,
                parent_snapshot_id=None,
            ),
            "mismatch": build_agent_identity(
                family_id="mismatch-comparator-family",
                name="mismatched-model-comparator",
                version="1.0.0",
                source_commit="d" * 40,
                config_sha256=canonical_sha256("mismatch-comparator-config"),
                snapshot_id="mismatch-comparator-v1",
                evolution_round=0,
                parent_snapshot_id=None,
            ),
        }
        roles = {
            "a0": BenchmarkRunRole.BASELINE,
            "a1": BenchmarkRunRole.EVOLVED,
            "a2": BenchmarkRunRole.EVOLVED,
            "comparator": BenchmarkRunRole.COMPARATOR,
            "mismatch": BenchmarkRunRole.COMPARATOR,
        }
        return {
            key: build_run_contract(
                contract_id=f"benchmark-contract:{key}",
                role=roles[key],
                suite=suite,
                agent=agents[key],
                model=(mismatched_model if key == "mismatch" else model),
                reasoning_effort="medium",
                trials_per_task=1,
                max_wall_seconds=3600,
                max_cost_usd=1.0,
                source=BenchmarkEvidenceSource.SYNTHETIC_FIXTURE,
                timeout_multiplier=1.0,
                agent_timeout_override=False,
                verifier_timeout_override=False,
                resource_overrides=False,
                upload=False,
                public=False,
                harbor_hub_job_uri=None,
                trajectories_available=False,
                default_execution_settings_attested=True,
            )
            for key in agents
        }

    def _fixture_payloads(
        self,
        contracts: dict[str, BenchmarkRunContract],
    ) -> dict[str, dict]:
        rewards = {
            "a0": (0.0, 0.0, 1.0, None),
            "a1": (1.0, 0.0, 1.0, 0.0),
            "a2": (1.0, 1.0, 0.0, 1.0),
            "comparator": (1.0, 0.0, 1.0, 1.0),
            "mismatch": (1.0, 1.0, 1.0, 1.0),
        }
        per_trial_usage = {
            "a0": (100, 10, 50, 0.010),
            "a1": (110, 11, 55, 0.011),
            "a2": (120, 12, 60, 0.012),
            "comparator": (100, 8, 40, 0.010),
            "mismatch": (130, 13, 65, 0.013),
        }
        return {
            key: self._job_payload(
                key,
                contract=contracts[key],
                rewards=rewards[key],
                per_trial_usage=per_trial_usage[key],
            )
            for key in contracts
        }

    def _job_payload(
        self,
        key: str,
        *,
        contract: BenchmarkRunContract,
        rewards: tuple[float | None, ...],
        per_trial_usage: tuple[int, int, int, float],
    ) -> dict:
        started = self.STARTED_AT + timedelta(minutes=10 * list(
            ("a0", "a1", "a2", "comparator", "mismatch")
        ).index(key))
        trial_results = []
        for index, (task, reward) in enumerate(
            zip(contract.suite.tasks, rewards, strict=True),
            start=1,
        ):
            trial_started = started + timedelta(seconds=index * 10)
            trial_finished = trial_started + timedelta(seconds=5)
            errored = reward is None
            trial = {
                "task_name": task.task_name,
                "trial_name": f"{key}-trial-{index}",
                "task_id": task.task_id,
                "source": TERMINAL_BENCH_2_1,
                "task_checksum": task.task_checksum,
                "agent_info": {
                    "name": contract.agent.name,
                    "version": contract.agent.version,
                    "model_info": {
                        "name": contract.model.name,
                        "provider": contract.model.provider,
                    },
                },
                "verifier_result": (
                    None if errored else {"rewards": {"reward": reward}}
                ),
                "exception_info": (
                    {
                        "exception_type": "SyntheticTaskError",
                        "exception_message": (
                            "synthetic fixture failure; not persisted"
                        ),
                        "exception_traceback": (
                            "Traceback (synthetic fixture only); not persisted"
                        ),
                    }
                    if errored
                    else None
                ),
                "agent_result": {
                    "n_input_tokens": per_trial_usage[0],
                    "n_cache_tokens": per_trial_usage[1],
                    "n_output_tokens": per_trial_usage[2],
                    "cost_usd": per_trial_usage[3],
                },
                "started_at": trial_started.isoformat(),
                "finished_at": trial_finished.isoformat(),
            }
            trial_results.append(trial)
        errored_count = sum(reward is None for reward in rewards)
        return {
            "id": f"synthetic-harbor-job-{key}",
            "started_at": started.isoformat(),
            "updated_at": (started + timedelta(minutes=2)).isoformat(),
            "finished_at": (started + timedelta(minutes=2)).isoformat(),
            "n_total_trials": len(trial_results),
            "stats": {
                "n_completed_trials": len(trial_results),
                "n_errored_trials": errored_count,
                "n_running_trials": 0,
                "n_pending_trials": 0,
                "n_cancelled_trials": 0,
                "n_retries": 0,
                "n_input_tokens": per_trial_usage[0] * len(trial_results),
                "n_cache_tokens": per_trial_usage[1] * len(trial_results),
                "n_output_tokens": per_trial_usage[2] * len(trial_results),
                "cost_usd": per_trial_usage[3] * len(trial_results),
                "evals": {},
            },
            "trial_results": trial_results,
        }

    def _write_or_verify_fixtures(
        self,
        payloads: dict[str, dict],
    ) -> dict[str, str]:
        hashes: dict[str, str] = {}
        for key, payload in payloads.items():
            path = self.fixtures_root / key / "result.json"
            encoded = (
                json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
                + "\n"
            ).encode("utf-8")
            if path.exists():
                if path.is_symlink() or path.read_bytes() != encoded:
                    raise RuntimeError(
                        f"Existing Harbor-shaped fixture differs: {key}."
                    )
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_name(
                    f".{path.name}.{uuid.uuid4().hex}.tmp"
                )
                try:
                    with temporary.open("wb") as handle:
                        handle.write(encoded)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temporary, path)
                finally:
                    if temporary.exists():
                        temporary.unlink()
            hashes[key] = hashlib.sha256(encoded).hexdigest()
        return hashes

    def _verify_restart(self, package) -> None:
        repository = SQLiteBenchmarkEvidenceRepository(
            self.registry_database
        )
        repository.verify_audit(package.audit_checkpoint)
        repository.verify_state()
        if tuple(repository.list_runs()) != package.runs:
            raise RuntimeError(
                "Restarted benchmark run evidence differs from the package."
            )
        comparisons = {
            item.comparison_id: item
            for item in repository.list_comparisons()
        }
        if comparisons != {
            package.longitudinal.comparison_id: package.longitudinal,
            package.same_model_cross_agent.comparison_id: (
                package.same_model_cross_agent
            ),
        }:
            raise RuntimeError(
                "Restarted benchmark comparison evidence differs."
            )
        if tuple(repository.events()) != package.audit_events:
            raise RuntimeError(
                "Restarted benchmark audit differs from the package."
            )
        loaded = BenchmarkComparisonPackageManager().load_file(
            self.package_path
        )
        if loaded != package:
            raise RuntimeError("Reloaded benchmark comparison package differs.")

    def _result(
        self,
        package,
        *,
        resumed: bool,
        mismatched_model_rejected: bool,
    ) -> AuthoritativeBenchmarkEvidenceLabResult:
        points = {
            item.evolution_round: item
            for item in package.longitudinal.points
        }
        anchor_ranking = next(
            item
            for item in package.same_model_cross_agent.ranking
            if item.run_id == package.same_model_cross_agent.anchor_run_id
        )
        pairwise = package.same_model_cross_agent.pairwise[0]
        comparator_run = next(
            item
            for item in package.runs
            if item.evidence_id == pairwise.comparator_run_id
        )
        return AuthoritativeBenchmarkEvidenceLabResult(
            run_id=self.RUN_ID,
            resumed=resumed,
            evidence_ids=tuple(item.evidence_id for item in package.runs),
            comparison_ids=(
                package.longitudinal.comparison_id,
                package.same_model_cross_agent.comparison_id,
            ),
            a0_score=points[0].score,
            a1_score=points[1].score,
            a2_score=points[2].score,
            final_gain=package.longitudinal.final_gain,
            best_round=package.longitudinal.best_round,
            monotonic_score=package.longitudinal.monotonic_score,
            improved_tasks=package.longitudinal.improved_tasks,
            regressed_tasks=package.longitudinal.regressed_tasks,
            tied_tasks=package.longitudinal.tied_tasks,
            comparator_score=comparator_run.score,
            anchor_rank=anchor_ranking.rank,
            anchor_wins=pairwise.wins,
            anchor_losses=pairwise.losses,
            anchor_ties=pairwise.ties,
            mismatched_model_rejected=mismatched_model_rejected,
            submission_prerequisites_met_count=sum(
                item.submission_prerequisites_met
                for item in package.eligibility
            ),
            registry_event_count=len(package.audit_events),
            registry_checkpoint=package.audit_checkpoint.model_dump(
                mode="json"
            ),
            package_path=str(self.package_path),
            package_hash=package.package_hash,
        )


__all__ = [
    "AuthoritativeBenchmarkEvidenceLab",
    "AuthoritativeBenchmarkEvidenceLabResult",
]
