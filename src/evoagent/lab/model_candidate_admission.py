from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from evoagent import __version__
from evoagent.benchmarks.models import ResourceBudget
from evoagent.campaigns import (
    CampaignGovernanceService,
    CampaignOperatorView,
    CampaignState,
    CampaignType,
    SQLiteCampaignRepository,
)
from evoagent.lab.model_evolution import GovernedModelEvolutionLab
from evoagent.lab.service import DEFAULT_THIRD_PARTY_LOCK_HASH
from evoagent.model_registry import (
    AllowlistedTrainingAuthorizationVerifier,
    IndependentModelCandidateEvaluator,
    ModelActivationLifecycleService,
    ModelActivationThresholds,
    ModelAdmissionPackageManager,
    ModelCandidateAdmissionService,
    ModelVersionStatus,
    SQLiteModelRegistry,
    SyntheticCandidateProfile,
    SyntheticModelCandidateAdapter,
    TrainingReceiptKind,
)
from evoagent.model_registry.builders import (
    build_external_candidate_manifest,
    build_external_training_receipt,
    build_initial_model_manifest,
    build_model_evaluation_suite,
    build_training_authorization_reference,
)
from evoagent.training import (
    ModelEvolutionPackageManager,
    TrainingBudget,
)


class ModelCandidateAdmissionLabResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    resumed: bool
    training_intent_package_hash: str
    family_id: str
    initial_model_id: str
    candidate_id: str
    lifecycle_statuses: tuple[str, ...]
    activation_campaign_id: str
    activation_campaign_state: str
    required_approvals: int = Field(ge=2)
    approval_count: int = Field(ge=2)
    held_out_base_score: float
    held_out_candidate_score: float
    held_out_improvement: float
    replay_candidate_score: float
    retention_candidate_score: float
    safety_candidate_score: float
    regression_count: int = Field(ge=0)
    forgetting_rate: float = Field(ge=0.0)
    safety_violation_count: int = Field(ge=0)
    candidate_tool_calls: int = Field(ge=0)
    candidate_budget_ok: bool
    active_model_after_activation: str
    active_revision_after_activation: int = Field(ge=1)
    active_model_after_rollback: str
    active_revision_after_rollback: int = Field(ge=2)
    model_version_count: int = Field(ge=2)
    model_event_count: int = Field(ge=6)
    activation_campaign_count: int = Field(ge=1)
    campaign_event_count: int = Field(ge=7)
    package_path: str
    package_hash: str
    restart_verified: Literal[True] = True
    synthetic_fixture: Literal[True] = True
    checkpoint_downloaded: Literal[False] = False
    candidate_weights_loaded: Literal[False] = False
    training_executed_by_evoagent: Literal[False] = False
    external_execution_performed: Literal[False] = False


class ModelCandidateAdmissionLab:
    """Synthetic receipt -> admission -> evaluation -> activation -> rollback."""

    RUN_ID = "model-candidate-admission-lifecycle-v1"
    FAMILY_ID = "local-document-model-family-v1"
    INITIAL_VERSION = "1.0.0"
    CANDIDATE_ID = "synthetic/candidate-local-document-policy-v1"
    CANDIDATE_VERSION = "1.1.0"
    TRAINER_ID = "external-synthetic-trainer"
    EVALUATOR_ID = "independent-model-evaluator"
    DECISION_ACTOR_ID = "model-activation-policy"
    APPROVER_IDS = (
        "independent-model-approver-a",
        "independent-model-approver-b",
    )
    OPERATOR_ID = "model-registry-operator"
    INITIAL_CREATED_AT = datetime(
        2026,
        8,
        10,
        14,
        0,
        tzinfo=timezone.utc,
    )
    RECEIPT_STARTED_AT = datetime(
        2026,
        8,
        10,
        15,
        10,
        tzinfo=timezone.utc,
    )
    RECEIPT_COMPLETED_AT = datetime(
        2026,
        8,
        10,
        15,
        20,
        tzinfo=timezone.utc,
    )
    CANDIDATE_CREATED_AT = datetime(
        2026,
        8,
        10,
        15,
        21,
        tzinfo=timezone.utc,
    )
    DECIDED_AT = datetime(
        2026,
        8,
        10,
        15,
        30,
        tzinfo=timezone.utc,
    )

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
            raise ValueError(
                "Model candidate admission lab root must not be a symlink."
            )
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

    @property
    def training_intent_root(self) -> Path:
        return self.root / "training-intent"

    @property
    def training_intent_package_path(self) -> Path:
        return self.training_intent_root / "model-evolution-package.json"

    @property
    def model_database(self) -> Path:
        return self.root / "model-registry.db"

    @property
    def campaign_database(self) -> Path:
        return self.root / "model-activation-campaigns.db"

    @property
    def evaluation_root(self) -> Path:
        return self.root / "model-evaluation"

    @property
    def package_path(self) -> Path:
        return self.root / "model-admission-package.json"

    def run(self) -> ModelCandidateAdmissionLabResult:
        if self.package_path.exists():
            return self._resume()

        training_result = GovernedModelEvolutionLab(
            self.training_intent_root,
            source_commit=self.source_commit,
            source_repository=self.source_repository,
        ).run()
        training_package = ModelEvolutionPackageManager().load_file(
            training_result.package_path
        )
        if training_package.package_hash != training_result.package_hash:
            raise RuntimeError(
                "Governed training-intent package changed after creation."
            )

        initial_manifest = build_initial_model_manifest(
            training_package,
            family_id=self.FAMILY_ID,
            version=self.INITIAL_VERSION,
            created_at=self.INITIAL_CREATED_AT,
        )
        authorization = build_training_authorization_reference(
            reference_id="synthetic-training-authorization-v1",
            signer_identity="external-synthetic-lab-authority",
            external_verification_uri=(
                "synthetic://authorization/model-training-v1"
            ),
            authorization_payload={
                "candidate_id": self.CANDIDATE_ID,
                "training_intent_package_hash": (
                    training_package.package_hash
                ),
                "maximum_rollouts": 64,
                "synthetic_fixture": True,
            },
        )
        candidate = build_external_candidate_manifest(
            training_package,
            family_id=self.FAMILY_ID,
            candidate_id=self.CANDIDATE_ID,
            version=self.CANDIDATE_VERSION,
            authorization=authorization,
            generated_by=self.TRAINER_ID,
            training_commit="5" * 40,
            created_at=self.CANDIDATE_CREATED_AT,
            synthetic_profile=SyntheticCandidateProfile.PASSING,
        )
        receipt = build_external_training_receipt(
            training_package,
            candidate,
            receipt_id="synthetic-training-receipt-v1",
            trainer_id=self.TRAINER_ID,
            started_at=self.RECEIPT_STARTED_AT,
            completed_at=self.RECEIPT_COMPLETED_AT,
            budget_used=TrainingBudget(
                max_gpu_hours=0.0,
                max_rollouts=32,
                max_training_tokens=0,
                max_cost_usd=0.0,
            ),
            receipt_kind=TrainingReceiptKind.SYNTHETIC_LIFECYCLE_FIXTURE,
        )
        suite = build_model_evaluation_suite(training_package)
        budget = ResourceBudget(
            max_task_trials=sum(
                len(tasks)
                for tasks in (
                    suite.held_out_tasks,
                    suite.replay_tasks,
                    suite.retention_tasks,
                    suite.safety_tasks,
                )
            ),
            max_tokens=0,
            max_tool_calls=20,
            max_wall_seconds=0.0,
            max_cost_usd=0.0,
        )
        thresholds = ModelActivationThresholds()

        registry = SQLiteModelRegistry(self.model_database)
        if not registry.family_exists(self.FAMILY_ID):
            registry.register_initial(
                initial_manifest,
                reason=(
                    "Register the synthetic base policy before candidate "
                    "admission."
                ),
                actor_id="model-registry-bootstrap",
            )
        elif registry.get(
            self.FAMILY_ID,
            initial_manifest.model_id,
        ).manifest != initial_manifest:
            raise RuntimeError(
                "Existing initial Model Registry manifest differs."
            )

        admission = ModelCandidateAdmissionService(
            registry=registry,
            authorization_verifier=(
                AllowlistedTrainingAuthorizationVerifier(
                    {authorization.reference_hash}
                )
            ),
            allow_synthetic_fixture=True,
        )
        admitted = admission.admit(
            package=training_package,
            candidate=candidate,
            receipt=receipt,
        )
        if admitted.record.status != ModelVersionStatus.CANDIDATE:
            raise RuntimeError(
                "Candidate admission did not preserve CANDIDATE status."
            )
        if registry.active(self.FAMILY_ID).model_id != initial_manifest.model_id:
            raise RuntimeError(
                "Candidate admission changed the active model."
            )

        campaign_repository = SQLiteCampaignRepository(
            self.campaign_database
        )
        lifecycle = ModelActivationLifecycleService(
            registry=registry,
            campaign_governance=CampaignGovernanceService(
                campaign_repository
            ),
            evaluator=IndependentModelCandidateEvaluator(
                self.evaluation_root
            ),
        )
        submission = lifecycle.evaluate_and_submit(
            family_id=self.FAMILY_ID,
            candidate_id=candidate.candidate_id,
            adapter=SyntheticModelCandidateAdapter(candidate),
            suite=suite,
            evaluator_id=self.EVALUATOR_ID,
            budget=budget,
            thresholds=thresholds,
            decision_actor_id=self.DECISION_ACTOR_ID,
            decided_at=self.DECIDED_AT,
        )
        if not submission.decision.activate:
            raise RuntimeError(
                f"Passing synthetic candidate was rejected: "
                f"{submission.decision.reason}"
            )
        evaluated = registry.get(
            self.FAMILY_ID,
            candidate.candidate_id,
        )
        if evaluated.status != ModelVersionStatus.EVALUATED:
            raise RuntimeError(
                "Passing candidate did not reach EVALUATED."
            )
        if submission.campaign.state != CampaignState.APPROVAL_PENDING:
            raise RuntimeError(
                "Passing activation Campaign is not awaiting approval."
            )
        if registry.active(self.FAMILY_ID).model_id != initial_manifest.model_id:
            raise RuntimeError(
                "Independent evaluation changed the active model."
            )

        approvals_by_actor = {
            item.actor_id: item
            for item in campaign_repository.approvals(
                submission.campaign.campaign_id
            )
        }
        campaign = campaign_repository.get(
            submission.campaign.campaign_id
        )
        for approver_id in self.APPROVER_IDS:
            if approver_id in approvals_by_actor:
                continue
            campaign = lifecycle.approve(
                campaign.campaign_id,
                actor_id=approver_id,
                reason=(
                    "Independent reviewer approved the exact frozen "
                    "evaluation-bound candidate."
                ),
                expected_revision=campaign.revision,
            )
        campaign = campaign_repository.get(campaign.campaign_id)
        if campaign.state != CampaignState.AUTHORIZED:
            raise RuntimeError(
                "Two independent approvals did not authorize the Campaign."
            )
        if (
            registry.get(
                self.FAMILY_ID,
                candidate.candidate_id,
            ).status
            != ModelVersionStatus.EVALUATED
        ):
            raise RuntimeError(
                "Campaign authorization silently changed Registry status."
            )
        if registry.active(self.FAMILY_ID).model_id != initial_manifest.model_id:
            raise RuntimeError(
                "Campaign authorization silently activated the candidate."
            )

        authorized = lifecycle.synchronize_authorization(
            family_id=self.FAMILY_ID,
            candidate_id=candidate.candidate_id,
            campaign_id=campaign.campaign_id,
            actor_id=self.OPERATOR_ID,
        )
        if authorized.status != ModelVersionStatus.AUTHORIZED:
            raise RuntimeError(
                "Registry authorization synchronization failed."
            )
        if registry.active(self.FAMILY_ID).model_id != initial_manifest.model_id:
            raise RuntimeError(
                "Registry AUTHORIZED status changed the active pointer."
            )

        active_revision_before = registry.active_revision(self.FAMILY_ID)
        active_candidate = lifecycle.activate(
            family_id=self.FAMILY_ID,
            candidate_id=candidate.candidate_id,
            campaign_id=campaign.campaign_id,
            expected_active_revision=active_revision_before,
            actor_id=self.OPERATOR_ID,
        )
        if active_candidate.status != ModelVersionStatus.ACTIVE:
            raise RuntimeError("Explicit activation did not activate candidate.")
        active_after_activation = registry.active(self.FAMILY_ID).model_id
        revision_after_activation = registry.active_revision(
            self.FAMILY_ID
        )
        if (
            active_after_activation != candidate.candidate_id
            or revision_after_activation != active_revision_before + 1
        ):
            raise RuntimeError(
                "Active model pointer or revision did not advance."
            )

        restored = lifecycle.rollback(
            family_id=self.FAMILY_ID,
            from_model_id=candidate.candidate_id,
            to_model_id=initial_manifest.model_id,
            expected_active_revision=revision_after_activation,
            actor_id=self.OPERATOR_ID,
            reason=(
                "Controlled lifecycle test restored the previous active model."
            ),
        )
        if restored.status != ModelVersionStatus.ACTIVE:
            raise RuntimeError("Rollback target did not become ACTIVE.")
        active_after_rollback = registry.active(self.FAMILY_ID).model_id
        revision_after_rollback = registry.active_revision(self.FAMILY_ID)
        candidate_after_rollback = registry.get(
            self.FAMILY_ID,
            candidate.candidate_id,
        )
        if (
            active_after_rollback != initial_manifest.model_id
            or candidate_after_rollback.status
            != ModelVersionStatus.ROLLED_BACK
            or revision_after_rollback != revision_after_activation + 1
        ):
            raise RuntimeError(
                "Rollback did not restore the exact parent and revision."
            )

        registry.verify_state(self.FAMILY_ID)
        campaign_repository.verify_audit()
        completed_campaign = campaign_repository.get(campaign.campaign_id)
        if completed_campaign.state != CampaignState.COMPLETED:
            raise RuntimeError(
                "Activation Campaign did not remain COMPLETED after rollback."
            )
        approvals = tuple(
            campaign_repository.approvals(campaign.campaign_id)
        )
        campaign_events = tuple(campaign_repository.audit_events())
        campaign_checkpoint = campaign_repository.checkpoint()
        model_records = tuple(registry.list_versions(self.FAMILY_ID))
        model_events = tuple(registry.events(self.FAMILY_ID))
        model_checkpoint = registry.checkpoint()

        package_manager = ModelAdmissionPackageManager()
        package = package_manager.build(
            run_id=self.RUN_ID,
            created_at=datetime.now(timezone.utc),
            framework_version=__version__,
            source_repository=self.source_repository,
            source_commit=self.source_commit,
            third_party_lock_hash=DEFAULT_THIRD_PARTY_LOCK_HASH,
            training_intent_package_hash=training_package.package_hash,
            initial_manifest=initial_manifest,
            candidate_manifest=candidate,
            training_receipt=receipt,
            evaluation_suite=suite,
            evaluation_report=submission.report,
            activation_decision=submission.decision,
            activation_campaign=completed_campaign,
            approvals=approvals,
            campaign_events=campaign_events,
            campaign_checkpoint=campaign_checkpoint,
            model_records=model_records,
            model_events=model_events,
            model_registry_checkpoint=model_checkpoint,
            active_model_after_activation=active_after_activation,
            active_revision_after_activation=revision_after_activation,
            active_model_after_rollback=active_after_rollback,
            active_revision_after_rollback=revision_after_rollback,
        )
        package_manager.export_file(package, self.package_path)
        self._verify_restart(package)
        return self._result(
            package=package,
            resumed=False,
            activation_campaign_count=len(
                CampaignOperatorView(
                    campaign_repository
                ).list_campaigns(
                    campaign_type=CampaignType.MODEL_ACTIVATION
                )
            ),
        )

    def _resume(self) -> ModelCandidateAdmissionLabResult:
        package_manager = ModelAdmissionPackageManager()
        package = package_manager.load_file(self.package_path)
        training_package = ModelEvolutionPackageManager().load_file(
            self.training_intent_package_path
        )
        if (
            training_package.package_hash
            != package.training_intent_package_hash
        ):
            raise RuntimeError(
                "Training-intent package differs from final admission package."
            )
        campaign_repository = SQLiteCampaignRepository(
            self.campaign_database
        )
        self._verify_restart(package)
        campaigns = CampaignOperatorView(
            campaign_repository
        ).list_campaigns(
            campaign_type=CampaignType.MODEL_ACTIVATION
        )
        return self._result(
            package=package,
            resumed=True,
            activation_campaign_count=len(campaigns),
        )

    def _verify_restart(self, package) -> None:
        registry = SQLiteModelRegistry(self.model_database)
        campaign_repository = SQLiteCampaignRepository(
            self.campaign_database
        )
        loaded = ModelAdmissionPackageManager().load_file(
            self.package_path
        ) if self.package_path.exists() else package
        if loaded != package:
            raise RuntimeError(
                "Reloaded Model admission package differs."
            )
        registry.verify_audit(package.model_registry_checkpoint)
        registry.verify_state(self.FAMILY_ID)
        campaign_repository.verify_audit(
            package.campaign_checkpoint
        )
        if tuple(registry.events(self.FAMILY_ID)) != package.model_events:
            raise RuntimeError(
                "Restarted Model Registry events differ from the package."
            )
        if (
            tuple(campaign_repository.audit_events())
            != package.campaign_events
        ):
            raise RuntimeError(
                "Restarted Campaign events differ from the package."
            )
        if (
            tuple(
                campaign_repository.approvals(
                    package.activation_campaign.campaign_id
                )
            )
            != package.approvals
        ):
            raise RuntimeError(
                "Restarted approvals differ from the package."
            )
        if (
            tuple(registry.list_versions(self.FAMILY_ID))
            != package.model_records
        ):
            raise RuntimeError(
                "Restarted Model records differ from the package."
            )
        if (
            registry.active(self.FAMILY_ID).model_id
            != package.active_model_after_rollback
            or registry.active_revision(self.FAMILY_ID)
            != package.active_revision_after_rollback
        ):
            raise RuntimeError(
                "Restarted active model pointer differs from rollback."
            )

    def _result(
        self,
        *,
        package,
        resumed: bool,
        activation_campaign_count: int,
    ) -> ModelCandidateAdmissionLabResult:
        report = package.evaluation_report
        return ModelCandidateAdmissionLabResult(
            run_id=self.RUN_ID,
            resumed=resumed,
            training_intent_package_hash=(
                package.training_intent_package_hash
            ),
            family_id=package.candidate_manifest.family_id,
            initial_model_id=package.initial_manifest.model_id,
            candidate_id=package.candidate_manifest.candidate_id,
            lifecycle_statuses=(
                ModelVersionStatus.CANDIDATE.value,
                ModelVersionStatus.EVALUATED.value,
                ModelVersionStatus.AUTHORIZED.value,
                ModelVersionStatus.ACTIVE.value,
                ModelVersionStatus.ROLLED_BACK.value,
            ),
            activation_campaign_id=(
                package.activation_campaign.campaign_id
            ),
            activation_campaign_state=(
                package.activation_campaign.state.value
            ),
            required_approvals=(
                package.activation_campaign.required_approvals
            ),
            approval_count=len(package.approvals),
            held_out_base_score=report.held_out_base_score,
            held_out_candidate_score=report.held_out_candidate_score,
            held_out_improvement=report.held_out_improvement,
            replay_candidate_score=report.replay_candidate_score,
            retention_candidate_score=report.retention_candidate_score,
            safety_candidate_score=report.safety_candidate_score,
            regression_count=report.regression_count,
            forgetting_rate=report.forgetting_rate,
            safety_violation_count=report.safety_violation_count,
            candidate_tool_calls=report.candidate_usage.tool_calls,
            candidate_budget_ok=report.candidate_budget_ok,
            active_model_after_activation=(
                package.active_model_after_activation
            ),
            active_revision_after_activation=(
                package.active_revision_after_activation
            ),
            active_model_after_rollback=(
                package.active_model_after_rollback
            ),
            active_revision_after_rollback=(
                package.active_revision_after_rollback
            ),
            model_version_count=len(package.model_records),
            model_event_count=len(package.model_events),
            activation_campaign_count=activation_campaign_count,
            campaign_event_count=len(package.campaign_events),
            package_path=str(self.package_path),
            package_hash=package.package_hash,
        )


__all__ = [
    "ModelCandidateAdmissionLab",
    "ModelCandidateAdmissionLabResult",
]
