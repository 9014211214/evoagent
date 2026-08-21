from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from evoagent import __version__
from evoagent.lab.service import DEFAULT_THIRD_PARTY_LOCK_HASH
from evoagent.local_rl import (
    IndependentLocalPolicyEvaluator,
    LocalGroupRelativePolicyOptimizer,
    LocalPolicyCheckpointSelector,
    LocalRLPackageManifest,
    LocalRLPackageManager,
    LocalRLTaskKind,
    SQLiteLocalRLRepository,
    TabularSoftmaxPolicy,
    build_environment_contract,
    build_hyperparameters,
    build_local_rl_task,
    build_run_manifest,
    build_training_budget,
)
from evoagent.model_registry.models import canonical_sha256, validate_safe_content


class LocalAgenticRLLabResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    resumed: bool
    optimizer_invoked: bool
    manifest_hash: str
    training_result_hash: str
    initial_checkpoint_hash: str
    final_checkpoint_hash: str
    selected_checkpoint_hash: str
    selected_iteration: int = Field(gt=0)
    parameter_delta_l2: float = Field(gt=0.0)
    baseline_score: float = Field(ge=0.0, le=1.0)
    selected_score: float = Field(ge=0.0, le=1.0)
    selected_normal_score: float = Field(ge=0.0, le=1.0)
    selected_protected_score: float = Field(ge=0.0, le=1.0)
    baseline_unsafe_actions: int = Field(ge=0)
    selected_unsafe_actions: int = Field(ge=0)
    iterations: int = Field(gt=0)
    rollouts: int = Field(gt=0)
    episode_steps: int = Field(gt=0)
    parameter_updates: int = Field(gt=0)
    retained_checkpoints: int = Field(gt=0)
    audit_event_count: int = Field(gt=0)
    package_path: str
    package_hash: str
    numeric_policy_parameters_updated: Literal[True] = True
    tiny_tabular_policy_only: Literal[True] = True
    local_rollout_training_executed_by_evoagent: Literal[True] = True
    foundation_model_training_performed: Literal[False] = False
    external_model_call_performed_by_evoagent: Literal[False] = False
    gpu_execution_performed: Literal[False] = False
    network_execution_performed: Literal[False] = False
    production_deployment_performed: Literal[False] = False
    official_benchmark_claimed: Literal[False] = False


class LocalAgenticRLTrainingLab:
    """Run real numeric policy updates in a tiny resettable local MDP."""

    RUN_ID = "local-agentic-rl-lab-v1"
    PACKAGE_ID = "local-agentic-rl-package-v1"
    TRAINER_ID = "local-group-relative-optimizer"
    EVALUATOR_ID = "independent-local-policy-evaluator"
    DECISION_ACTOR_ID = "local-policy-selection-gate"
    CREATED_AT = datetime(2026, 8, 12, 16, 0, tzinfo=timezone.utc)
    DECIDED_AT = datetime(2026, 8, 12, 16, 5, tzinfo=timezone.utc)

    def __init__(
        self,
        root: str | Path,
        *,
        created_at: datetime | None = None,
        decided_at: datetime | None = None,
        source_commit: str = "0" * 40,
        source_repository: str = (
            "https://github.com/9014211214/evoagent"
        ),
    ):
        raw_root = Path(root).expanduser()
        if raw_root.is_symlink():
            raise ValueError("Local Agentic RL lab root must not be a symlink.")
        if len(source_commit) != 40 or any(
            character not in "0123456789abcdef"
            for character in source_commit
        ):
            raise ValueError(
                "source_commit must be lowercase 40-character Git hex."
            )
        self.root = raw_root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.source_commit = source_commit
        self.source_repository = source_repository
        self._created_at_override = created_at
        self._decided_at_override = decided_at
        if (
            self.created_at.tzinfo is None
            or self.created_at.utcoffset() is None
            or self.decided_at.tzinfo is None
            or self.decided_at.utcoffset() is None
            or self.decided_at <= self.created_at
        ):
            raise ValueError(
                "Local Agentic RL lab times must be timezone-aware and ordered."
            )

    @property
    def created_at(self) -> datetime:
        return self._created_at_override or self.CREATED_AT

    @property
    def decided_at(self) -> datetime:
        return self._decided_at_override or self.DECIDED_AT

    @property
    def database_path(self) -> Path:
        return self.root / "local-agentic-rl.db"

    @property
    def package_path(self) -> Path:
        return self.root / "local-agentic-rl-package.json"

    def build_manifest(self):
        environment = build_environment_contract()
        training_tasks = (
            build_local_rl_task("local-rl:train:normal:1", LocalRLTaskKind.NORMAL),
            build_local_rl_task("local-rl:train:normal:2", LocalRLTaskKind.NORMAL),
            build_local_rl_task(
                "local-rl:train:protected:1", LocalRLTaskKind.PROTECTED
            ),
            build_local_rl_task(
                "local-rl:train:protected:2", LocalRLTaskKind.PROTECTED
            ),
        )
        held_out_tasks = (
            build_local_rl_task("local-rl:heldout:normal:1", LocalRLTaskKind.NORMAL),
            build_local_rl_task("local-rl:heldout:normal:2", LocalRLTaskKind.NORMAL),
            build_local_rl_task(
                "local-rl:heldout:protected:1", LocalRLTaskKind.PROTECTED
            ),
            build_local_rl_task(
                "local-rl:heldout:protected:2", LocalRLTaskKind.PROTECTED
            ),
        )
        hyperparameters = build_hyperparameters(
            learning_rate=0.4,
            clip_epsilon=0.2,
            entropy_coefficient=0.01,
            max_gradient_norm=1.0,
            update_epochs=4,
            group_size=24,
            seed=17,
            retained_checkpoint_interval=2,
        )
        budget = build_training_budget(
            maximum_iterations=24,
            maximum_rollouts=3_000,
            maximum_episode_steps=6_000,
            maximum_parameter_updates=200,
            maximum_wall_seconds=30.0,
        )
        return build_run_manifest(
            run_id=self.RUN_ID,
            created_at=self.created_at,
            environment=environment,
            training_tasks=training_tasks,
            held_out_tasks=held_out_tasks,
            hyperparameters=hyperparameters,
            budget=budget,
        )

    def run(self) -> LocalAgenticRLLabResult:
        manifest = self.build_manifest()
        repository = SQLiteLocalRLRepository(self.database_path)
        if self.package_path.exists():
            package = self._load_resume_package()
            if package.manifest != manifest:
                raise RuntimeError(
                    "Local Agentic RL resume manifest differs from the frozen lab."
                )
            self._verify_persistent_state(repository, package)
            return self._result(package, resumed=True, optimizer_invoked=False)

        reused = repository.register_manifest(
            manifest,
            actor_id="local-rl-lab",
            now=self.created_at,
        )
        if reused:
            raise RuntimeError(
                "Local RL repository contains an incomplete pre-existing run."
            )
        training = LocalGroupRelativePolicyOptimizer().train(manifest)
        repository.store_training(
            training,
            actor_id=self.TRAINER_ID,
            now=self.created_at,
        )
        evaluator = IndependentLocalPolicyEvaluator()
        baseline = evaluator.evaluate(
            manifest,
            training.initial_checkpoint,
            evaluator_id=self.EVALUATOR_ID,
            trainer_id=self.TRAINER_ID,
        )
        candidates = tuple(
            evaluator.evaluate(
                manifest,
                checkpoint,
                evaluator_id=self.EVALUATOR_ID,
                trainer_id=self.TRAINER_ID,
            )
            for checkpoint in training.retained_checkpoints
        )
        repository.store_evaluations(
            manifest.run_id,
            baseline=baseline,
            candidates=candidates,
            actor_id=self.EVALUATOR_ID,
            now=self.decided_at,
        )
        decision = LocalPolicyCheckpointSelector().decide(
            manifest,
            training,
            baseline,
            candidates,
            decision_id=f"{manifest.run_id}:selection",
            decision_actor_id=self.DECISION_ACTOR_ID,
            decided_at=self.decided_at,
        )
        repository.store_decision(
            decision,
            actor_id=self.DECISION_ACTOR_ID,
            now=self.decided_at,
        )
        repository.verify_state(manifest.run_id)
        selected_report = next(
            item
            for item in candidates
            if item.checkpoint_hash == decision.selected_checkpoint_hash
        )
        if (
            baseline.overall_score >= selected_report.overall_score
            or selected_report.overall_score != 1.0
            or selected_report.normal_score != 1.0
            or selected_report.protected_score != 1.0
            or selected_report.unsafe_action_count != 0
        ):
            raise RuntimeError(
                "Controlled local Agentic RL training did not reach the safe target."
            )
        selected_checkpoint = next(
            item
            for item in training.retained_checkpoints
            if item.checkpoint_hash == decision.selected_checkpoint_hash
        )
        initial_policy = TabularSoftmaxPolicy.from_checkpoint(
            training.initial_checkpoint
        )
        selected_policy = TabularSoftmaxPolicy.from_checkpoint(selected_checkpoint)
        if selected_policy.parameter_l2_distance(initial_policy) <= 0.0:
            raise RuntimeError("Local Agentic RL did not change numeric parameters.")

        package = LocalRLPackageManager().build(
            package_id=self.PACKAGE_ID,
            created_at=self.decided_at,
            framework_version=__version__,
            source_repository=self.source_repository,
            source_commit=self.source_commit,
            third_party_lock_hash=DEFAULT_THIRD_PARTY_LOCK_HASH,
            trainer_id=self.TRAINER_ID,
            manifest=manifest,
            training=training,
            baseline_evaluation=baseline,
            candidate_evaluations=candidates,
            decision=decision,
            audit_events=repository.events(),
            audit_checkpoint=repository.checkpoint(),
        )
        LocalRLPackageManager().export_file(package, self.package_path)
        self._verify_persistent_state(repository, package)
        return self._result(package, resumed=False, optimizer_invoked=True)

    def _load_resume_package(self) -> LocalRLPackageManifest:
        # Resume is intentionally read-only and does not re-enter the optimizer.
        # Full external package loading still uses LocalRLPackageManager.load_file,
        # which deterministically replays training and evaluation.
        if self.package_path.is_symlink() or not self.package_path.is_file():
            raise RuntimeError("Local RL resume package must be a regular file.")
        package = LocalRLPackageManifest.model_validate_json(
            self.package_path.read_text(encoding="utf-8")
        )
        payload = package.model_dump(mode="json", exclude={"package_hash"})
        validate_safe_content(payload)
        if package.package_hash != canonical_sha256(payload):
            raise RuntimeError("Local RL resume package hash mismatch.")
        return package

    @staticmethod
    def _verify_persistent_state(
        repository: SQLiteLocalRLRepository,
        package: LocalRLPackageManifest,
    ) -> None:
        repository.verify_audit(package.audit_checkpoint)
        repository.verify_state(package.manifest.run_id)
        baseline, candidates = repository.load_evaluations(package.manifest.run_id)
        if (
            repository.load_manifest(package.manifest.run_id) != package.manifest
            or repository.load_training(package.manifest.run_id) != package.training
            or baseline != package.baseline_evaluation
            or candidates != package.candidate_evaluations
            or repository.load_decision(package.manifest.run_id) != package.decision
            or repository.events() != package.audit_events
        ):
            raise RuntimeError(
                "Persistent Local RL state differs from the reproducible package."
            )

    def _result(
        self,
        package: LocalRLPackageManifest,
        *,
        resumed: bool,
        optimizer_invoked: bool,
    ) -> LocalAgenticRLLabResult:
        selected_report = next(
            item
            for item in package.candidate_evaluations
            if item.checkpoint_hash == package.decision.selected_checkpoint_hash
        )
        selected_checkpoint = next(
            item
            for item in package.training.retained_checkpoints
            if item.checkpoint_hash == package.decision.selected_checkpoint_hash
        )
        parameter_delta = TabularSoftmaxPolicy.from_checkpoint(
            selected_checkpoint
        ).parameter_l2_distance(
            TabularSoftmaxPolicy.from_checkpoint(
                package.training.initial_checkpoint
            )
        )
        return LocalAgenticRLLabResult(
            run_id=package.manifest.run_id,
            resumed=resumed,
            optimizer_invoked=optimizer_invoked,
            manifest_hash=package.manifest.manifest_hash,
            training_result_hash=package.training.result_hash,
            initial_checkpoint_hash=(
                package.training.initial_checkpoint.checkpoint_hash
            ),
            final_checkpoint_hash=(
                package.training.retained_checkpoints[-1].checkpoint_hash
            ),
            selected_checkpoint_hash=package.decision.selected_checkpoint_hash,
            selected_iteration=package.decision.selected_iteration,
            parameter_delta_l2=parameter_delta,
            baseline_score=package.baseline_evaluation.overall_score,
            selected_score=selected_report.overall_score,
            selected_normal_score=selected_report.normal_score,
            selected_protected_score=selected_report.protected_score,
            baseline_unsafe_actions=(
                package.baseline_evaluation.unsafe_action_count
            ),
            selected_unsafe_actions=selected_report.unsafe_action_count,
            iterations=package.training.usage.iterations,
            rollouts=package.training.usage.rollouts,
            episode_steps=package.training.usage.episode_steps,
            parameter_updates=package.training.usage.parameter_updates,
            retained_checkpoints=len(package.training.retained_checkpoints),
            audit_event_count=len(package.audit_events),
            package_path=str(self.package_path),
            package_hash=package.package_hash,
        )


__all__ = ["LocalAgenticRLLabResult", "LocalAgenticRLTrainingLab"]
