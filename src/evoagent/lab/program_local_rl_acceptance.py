from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evoagent import __version__
from evoagent.campaigns import (
    CampaignGovernanceService,
    CampaignState,
    SQLiteCampaignRepository,
)
from evoagent.lab.evolution_program import MultiGenerationEvolutionProgramLab
from evoagent.lab.local_agentic_rl import LocalAgenticRLTrainingLab
from evoagent.lab.service import DEFAULT_THIRD_PARTY_LOCK_HASH
from evoagent.local_rl import (
    LocalRLPackageManager,
    LocalRLPackageManifest,
    ProgramLocalRLProjectionPackage,
    ProgramLocalRLProjectionPackageManager,
    build_program_local_rl_projection_spec,
)
from evoagent.model_registry.models import canonical_sha256, validate_safe_content
from evoagent.program import (
    EvolutionProgramController,
    EvolutionProgramPackageManager,
    EvolutionProgramPackageManifest,
    ProgramCheckpoint,
    ProgramEventType,
    ProgramExecutionCheckpoint,
    ProgramState,
    RunningGenerationAttestation,
    SQLiteEvolutionProgramRepository,
)
from evoagent.program_rl import (
    AttestedProgramLocalRLPackageManager,
    FullyAttestedProgramLocalRLBindingPackage,
    FullyAttestedProgramLocalRLPackageManager,
    LocalRLExecutionBudget,
    LocalRLExecutionUsage,
    NativeLocalRLRuntimeContractBuilder,
    ProgramLocalRLAcceptanceManager,
    ProgramLocalRLAcceptanceReceipt,
    ProgramLocalRLAdapter,
    ProgramLocalRLPackageManager,
    ProgramLocalRLTrustedAnchors,
    RunningAttestedProgramLocalRLPackageManager,
    RunningGenerationIntentBindingManager,
    RuntimeAttestedProgramLocalRLPackageManager,
    RuntimeBoundNativeLocalRLAttestor,
    SchemaAttestedProgramLocalRLPackageManager,
    build_trusted_anchors,
)


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SAFE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"


def _timezone(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone.")
    return value


class ProgramLocalRLAcceptedEvidenceBundle(BaseModel):
    """Self-contained evidence accepted before v2.2 policy Promotion."""

    model_config = ConfigDict(frozen=True)

    format_version: Literal["evoagent-program-local-rl-accepted-v1"] = (
        "evoagent-program-local-rl-accepted-v1"
    )
    bundle_id: str = Field(pattern=_SAFE_ID_PATTERN)
    source_program_package: EvolutionProgramPackageManifest
    running_attestation: RunningGenerationAttestation
    native_local_rl_package: LocalRLPackageManifest
    projection_package: ProgramLocalRLProjectionPackage
    fully_attested_package: FullyAttestedProgramLocalRLBindingPackage
    trusted_anchors: ProgramLocalRLTrustedAnchors
    acceptance_receipt: ProgramLocalRLAcceptanceReceipt
    created_at: datetime
    local_policy_optimization_performed: Literal[True] = True
    tiny_tabular_policy_only: Literal[True] = True
    foundation_model_training_performed: Literal[False] = False
    external_model_call_performed: Literal[False] = False
    production_activation_performed: Literal[False] = False
    production_deployment_performed: Literal[False] = False
    external_rollout_performed: Literal[False] = False
    official_benchmark_claimed: Literal[False] = False
    bundle_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("created_at")
    @classmethod
    def validate_time(cls, value: datetime) -> datetime:
        return _timezone(value, "Program local-RL accepted bundle time")

    @model_validator(mode="after")
    def validate_bundle_hash(self):
        payload = self.model_dump(mode="json", exclude={"bundle_hash"})
        validate_safe_content(payload)
        if self.bundle_hash != canonical_sha256(payload):
            raise ValueError("Program local-RL accepted bundle hash mismatch.")
        return self


class ProgramLocalRLAcceptedEvidenceError(ValueError):
    pass


class ProgramLocalRLAcceptedEvidenceManager:
    """Recursively verify and immutably export the complete accepted chain."""

    @staticmethod
    def verify(bundle: ProgramLocalRLAcceptedEvidenceBundle) -> bool:
        EvolutionProgramPackageManager().verify(bundle.source_program_package)
        LocalRLPackageManager().verify(bundle.native_local_rl_package)
        ProgramLocalRLProjectionPackageManager.verify(
            bundle.projection_package
        )
        FullyAttestedProgramLocalRLPackageManager.verify(
            bundle.fully_attested_package
        )
        ProgramLocalRLAcceptanceManager.verify(
            bundle.fully_attested_package,
            bundle.trusted_anchors,
            bundle.acceptance_receipt,
        )
        if (
            bundle.projection_package.source_package
            != bundle.native_local_rl_package
            or bundle.projection_package.local_rl_package_hash
            != bundle.native_local_rl_package.package_hash
        ):
            raise ProgramLocalRLAcceptedEvidenceError(
                "Accepted projection differs from the real native Local RL package."
            )
        running_binding = (
            bundle.fully_attested_package.running_attested_package
            .intent_binding
        )
        if (
            running_binding.running_attestation_hash
            != bundle.running_attestation.attestation_hash
            or running_binding.running_attestation_payload
            != bundle.running_attestation.model_dump(mode="json")
        ):
            raise ProgramLocalRLAcceptedEvidenceError(
                "Accepted running intent binding differs from its attestation."
            )
        generations = tuple(bundle.source_program_package.generations)
        matching = tuple(
            item
            for item in generations
            if item.generation_id == bundle.running_attestation.generation_id
        )
        if len(matching) != 1 or matching[0].plan is None:
            raise ProgramLocalRLAcceptedEvidenceError(
                "Accepted running attestation lacks one source GenerationPlan."
            )
        plan = matching[0].plan
        if (
            plan.plan_hash != bundle.running_attestation.plan_hash
            or plan.source_signal_hash
            != bundle.running_attestation.source_signal_hash
            or plan.attribution_receipt_hash
            != bundle.running_attestation.attribution_receipt_hash
        ):
            raise ProgramLocalRLAcceptedEvidenceError(
                "Accepted running attestation differs from source Program evidence."
            )
        base = (
            bundle.fully_attested_package.runtime_attested_package
            .schema_attested_package.attested_package.base_package
        )
        result = base.result
        if (
            result.local_rl_package_hash
            != bundle.native_local_rl_package.package_hash
            or result.selected_checkpoint_hash
            != bundle.native_local_rl_package.decision.selected_checkpoint_hash
            or result.optimizer_evidence_hash
            != bundle.native_local_rl_package.training.result_hash
            or result.heldout_evaluation_hash
            != bundle.native_local_rl_package.decision.selected_report_hash
        ):
            raise ProgramLocalRLAcceptedEvidenceError(
                "Accepted Program result differs from native optimizer evidence."
            )
        evidence_times = (
            bundle.source_program_package.created_at,
            bundle.running_attestation.attested_at,
            bundle.native_local_rl_package.created_at,
            bundle.fully_attested_package.accepted_at,
            bundle.trusted_anchors.anchored_at,
            bundle.acceptance_receipt.accepted_at,
        )
        if bundle.created_at < max(evidence_times):
            raise ProgramLocalRLAcceptedEvidenceError(
                "Accepted evidence bundle predates complete evidence."
            )
        if (
            bundle.foundation_model_training_performed
            or bundle.external_model_call_performed
            or bundle.production_activation_performed
            or bundle.production_deployment_performed
            or bundle.external_rollout_performed
            or bundle.official_benchmark_claimed
        ):
            raise ProgramLocalRLAcceptedEvidenceError(
                "Accepted evidence bundle widens its offline authority."
            )
        payload = bundle.model_dump(mode="json", exclude={"bundle_hash"})
        if bundle.bundle_hash != canonical_sha256(payload):
            raise ProgramLocalRLAcceptedEvidenceError(
                "Program local-RL accepted bundle hash mismatch."
            )
        return True

    def build(self, **kwargs) -> ProgramLocalRLAcceptedEvidenceBundle:
        payload = {
            "format_version": "evoagent-program-local-rl-accepted-v1",
            **kwargs,
            "local_policy_optimization_performed": True,
            "tiny_tabular_policy_only": True,
            "foundation_model_training_performed": False,
            "external_model_call_performed": False,
            "production_activation_performed": False,
            "production_deployment_performed": False,
            "external_rollout_performed": False,
            "official_benchmark_claimed": False,
        }
        bundle = ProgramLocalRLAcceptedEvidenceBundle(
            **payload,
            bundle_hash=canonical_sha256(payload),
        )
        self.verify(bundle)
        return bundle

    def export_file(
        self,
        bundle: ProgramLocalRLAcceptedEvidenceBundle,
        path: str | Path,
    ) -> Path:
        self.verify(bundle)
        target = Path(path).expanduser()
        if target.is_symlink():
            raise ProgramLocalRLAcceptedEvidenceError(
                "Accepted evidence output must not be a symlink."
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        encoded = bundle.model_dump_json(indent=2) + "\n"
        if target.exists():
            if not target.is_file() or target.read_text(encoding="utf-8") != encoded:
                raise ProgramLocalRLAcceptedEvidenceError(
                    "Existing accepted evidence differs from immutable bundle."
                )
            return target
        target.write_text(encoded, encoding="utf-8")
        return target

    def load_file(
        self,
        path: str | Path,
    ) -> ProgramLocalRLAcceptedEvidenceBundle:
        target = Path(path).expanduser()
        if target.is_symlink() or not target.is_file():
            raise ProgramLocalRLAcceptedEvidenceError(
                "Accepted evidence bundle must be a regular file."
            )
        try:
            bundle = ProgramLocalRLAcceptedEvidenceBundle.model_validate_json(
                target.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise ProgramLocalRLAcceptedEvidenceError(
                f"Accepted evidence bundle is invalid: {exc}"
            ) from exc
        self.verify(bundle)
        return bundle


class ProgramLocalRLAcceptanceLabResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str = Field(pattern=_SAFE_ID_PATTERN)
    resumed: bool
    optimizer_invoked: bool
    program_id: str = Field(pattern=_SAFE_ID_PATTERN)
    generation_id: str = Field(pattern=_SAFE_ID_PATTERN)
    running_attestation_hash: str = Field(pattern=_SHA256_PATTERN)
    native_local_rl_package_hash: str = Field(pattern=_SHA256_PATTERN)
    selected_checkpoint_hash: str = Field(pattern=_SHA256_PATTERN)
    fully_attested_package_hash: str = Field(pattern=_SHA256_PATTERN)
    anchors_hash: str = Field(pattern=_SHA256_PATTERN)
    acceptance_receipt_hash: str = Field(pattern=_SHA256_PATTERN)
    bundle_path: str
    bundle_hash: str = Field(pattern=_SHA256_PATTERN)
    local_policy_optimization_performed: Literal[True] = True
    foundation_model_training_performed: Literal[False] = False
    production_activation_performed: Literal[False] = False
    production_deployment_performed: Literal[False] = False
    external_rollout_performed: Literal[False] = False


class ProgramLocalRLAcceptanceLab:
    """Run real local policy optimization and accept the complete Program lineage."""

    RUN_ID = "program-local-rl-acceptance-lab-v1"
    BUNDLE_ID = "program-local-rl-accepted-evidence:v2.3"

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
                "Program local-RL acceptance Lab root must not be a symlink."
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
    def source_program_root(self) -> Path:
        return self.root / "source-program"

    @property
    def running_program_database(self) -> Path:
        return self.root / "running-program.db"

    @property
    def running_campaign_database(self) -> Path:
        return self.root / "running-program-campaigns.db"

    @property
    def native_local_rl_root(self) -> Path:
        return self.root / "native-local-rl"

    @property
    def bundle_path(self) -> Path:
        return self.root / "program-local-rl-accepted-evidence.json"

    def run(self) -> ProgramLocalRLAcceptanceLabResult:
        manager = ProgramLocalRLAcceptedEvidenceManager()
        source_result = MultiGenerationEvolutionProgramLab(
            self.source_program_root,
            source_commit=self.source_commit,
            source_repository=self.source_repository,
        ).run()
        source_package = EvolutionProgramPackageManager().load_file(
            source_result.package_path
        )
        native_result = LocalAgenticRLTrainingLab(
            self.native_local_rl_root,
            source_commit=self.source_commit,
            source_repository=self.source_repository,
        ).run()
        native_package = LocalRLPackageManager().load_file(
            native_result.package_path
        )

        if self.bundle_path.exists():
            bundle = manager.load_file(self.bundle_path)
            if (
                bundle.source_program_package != source_package
                or bundle.native_local_rl_package != native_package
            ):
                raise RuntimeError(
                    "Accepted evidence resume inputs differ from immutable bundle."
                )
            if not source_result.resumed or not native_result.resumed:
                raise RuntimeError(
                    "Accepted evidence resume re-entered a child lifecycle."
                )
            if native_result.optimizer_invoked:
                raise RuntimeError(
                    "Accepted evidence resume invoked the optimizer again."
                )
            self._verify_persistent_running_state(bundle)
            return self._result(
                bundle,
                resumed=True,
                optimizer_invoked=False,
            )

        running_attestation = self._running_attestation(source_package)
        projection_manager = ProgramLocalRLProjectionPackageManager()
        projection_package = projection_manager.build(
            native_package,
            projection_package_id=(
                "program-local-rl-projection:accepted-v2.3"
            ),
        )
        bundle = self._assemble(
            source_package,
            running_attestation,
            native_package,
            projection_package,
        )
        manager.export_file(bundle, self.bundle_path)
        self._verify_persistent_running_state(bundle)
        return self._result(
            bundle,
            resumed=False,
            optimizer_invoked=native_result.optimizer_invoked,
        )

    def _running_attestation(
        self,
        package: EvolutionProgramPackageManifest,
    ) -> RunningGenerationAttestation:
        g0, g1 = package.generations
        if g0.outcome is None or g1.plan is None:
            raise RuntimeError(
                "Source Program package lacks controlled parent outcome or successor plan."
            )
        d0 = next(
            item
            for item in package.decisions
            if item.generation_id == g0.generation_id
        )
        repository = SQLiteEvolutionProgramRepository(
            self.running_program_database
        )
        campaigns = SQLiteCampaignRepository(
            self.running_campaign_database
        )
        controller = EvolutionProgramController(
            repository=repository,
            campaign_governance=CampaignGovernanceService(campaigns),
        )
        controller.register_from_release(
            package.drift_release_package,
            program_id=g0.program_id,
            policy=package.policy,
            generation_id=g0.generation_id,
            outcome_id=g0.outcome.outcome_id,
            created_by=self._program_actor(
                package,
                ProgramEventType.PROGRAM_CREATED,
            ),
            created_at=g0.created_at,
        )
        signal, _ = controller.store_feedback(
            package.drift_release_package,
            program_id=g0.program_id,
            generation_index=0,
            signal_id=package.signal.signal_id,
            actor_id=self._program_actor(
                package,
                ProgramEventType.SIGNAL_STORED,
            ),
            created_at=package.signal.created_at,
        )
        attribution, _ = controller.store_attribution(
            g0.program_id,
            package.attribution,
            actor_id=package.attribution.attributor_id,
            created_at=package.attribution.created_at,
        )
        decision, _ = controller.decide(
            program_id=g0.program_id,
            generation_id=g0.generation_id,
            decision_id=d0.decision_id,
            decided_by=d0.decided_by,
            decided_at=d0.decided_at,
            signal=signal,
            attribution=attribution,
        )
        if decision != d0:
            raise RuntimeError(
                "Replayed Program CONTINUE decision differs from source evidence."
            )
        evaluator = self._campaign_actor(
            package,
            campaign_id=g1.campaign_id,
            event_type="candidate_attached",
        )
        submission = controller.submit_generation(
            g1.plan,
            evaluation_actor_id=evaluator,
            submitted_at=g1.plan.created_at,
        )
        campaign = submission.campaign
        for approval in package.generation_approvals:
            campaign = controller.approve_generation(
                campaign.campaign_id,
                actor_id=approval.actor_id,
                reason=approval.reason,
                expected_revision=campaign.revision,
            )
        if campaign.state != CampaignState.AUTHORIZED:
            raise RuntimeError(
                "Replayed Generation Campaign did not reach AUTHORIZED."
            )
        controller.synchronize_authorization(
            program_id=g1.plan.program_id,
            generation_id=g1.plan.generation_id,
            campaign_id=campaign.campaign_id,
            actor_id=self._program_actor(
                package,
                ProgramEventType.GENERATION_AUTHORIZED,
                generation_id=g1.generation_id,
            ),
        )
        head = repository.head(g1.plan.program_id)
        expected_revision = (
            head.revision - 1
            if head.state == ProgramState.GENERATION_RUNNING
            else head.revision
        )
        controller.start_generation(
            program_id=g1.plan.program_id,
            generation_id=g1.plan.generation_id,
            campaign_id=campaign.campaign_id,
            expected_revision=expected_revision,
            actor_id=self._program_actor(
                package,
                ProgramEventType.GENERATION_STARTED,
                generation_id=g1.generation_id,
            ),
        )
        running_head = repository.head(g1.plan.program_id)
        if running_head.state != ProgramState.GENERATION_RUNNING:
            raise RuntimeError(
                "Replayed Program did not reach GENERATION_RUNNING."
            )
        program_checkpoint = repository.checkpoint()
        campaign_checkpoint = campaigns.checkpoint()
        program_anchor = ProgramExecutionCheckpoint(
            event_count=program_checkpoint.event_count,
            head_hash=program_checkpoint.head_hash,
        )
        campaign_anchor = ProgramExecutionCheckpoint(
            event_count=campaign_checkpoint.event_count,
            head_hash=campaign_checkpoint.head_hash,
        )
        attested_at = max(
            running_head.updated_at,
            campaigns.get(campaign.campaign_id).updated_at,
        ) + timedelta(milliseconds=1)
        return controller.attest_running_generation(
            program_id=g1.plan.program_id,
            generation_id=g1.plan.generation_id,
            expected_program_checkpoint=program_anchor,
            expected_campaign_checkpoint=campaign_anchor,
            attested_by="independent-running-generation-attestor",
            attested_at=attested_at,
            attestation_id=(
                "running-generation-attestation:accepted-v2.3"
            ),
        )

    def _assemble(
        self,
        source_program_package: EvolutionProgramPackageManifest,
        running_attestation: RunningGenerationAttestation,
        native_package: LocalRLPackageManifest,
        projection_package: ProgramLocalRLProjectionPackage,
    ) -> ProgramLocalRLAcceptedEvidenceBundle:
        projection = ProgramLocalRLProjectionPackageManager._projection(
            native_package
        )
        if running_attestation.attested_at >= native_package.manifest.created_at:
            raise RuntimeError(
                "Native local-RL execution does not follow Program running authorization."
            )
        g1 = next(
            item
            for item in source_program_package.generations
            if item.generation_id == running_attestation.generation_id
        )
        if g1.plan is None:
            raise RuntimeError("Running Generation lacks its source plan.")

        intent_time = running_attestation.attested_at + timedelta(milliseconds=1)
        adapter = ProgramLocalRLAdapter()
        intent = adapter.build_intent_from_attestation(
            running_attestation,
            local_rl_run_id=projection.local_rl_run_id,
            optimizer_config_hash=projection.optimizer_config_hash,
            training_task_set_hash=projection.training_task_set_hash,
            heldout_task_set_hash=projection.heldout_task_set_hash,
            created_by="independent-program-local-rl-intent-author",
            created_at=intent_time,
        )
        intent_binding = RunningGenerationIntentBindingManager().build(
            intent,
            running_attestation,
        )
        authorization = adapter.authorize(
            intent,
            generation_plan=g1.plan,
            budget=LocalRLExecutionBudget(
                max_iterations=projection.usage.iterations,
                max_rollouts=projection.usage.rollouts,
                max_tokens=projection.usage.tokens,
                max_cost_usd=projection.usage.cost_usd,
            ),
            authorized_by="independent-local-rl-execution-authorizer",
            authorized_at=intent_time + timedelta(milliseconds=1),
            expires_at=native_package.created_at + timedelta(hours=1),
        )
        result = adapter.bind_result(
            intent,
            authorization,
            local_rl_package_id=projection.local_rl_package_id,
            local_rl_package_hash=projection.local_rl_package_hash,
            initial_checkpoint_hash=projection.initial_checkpoint_hash,
            selected_checkpoint_hash=projection.selected_checkpoint_hash,
            optimizer_evidence_hash=projection.optimizer_evidence_hash,
            heldout_evaluation_hash=projection.heldout_evaluation_hash,
            usage=LocalRLExecutionUsage(
                iterations=projection.usage.iterations,
                rollouts=projection.usage.rollouts,
                tokens=projection.usage.tokens,
                cost_usd=projection.usage.cost_usd,
            ),
            heldout_reward_delta=projection.heldout_reward_delta,
            heldout_success_delta=projection.heldout_success_delta,
            unsafe_action_count=projection.unsafe_action_count,
            regression_count=projection.regression_count,
            executed_by=native_package.trainer_id,
            started_at=native_package.manifest.created_at,
            completed_at=native_package.created_at,
        )
        base_created = native_package.created_at + timedelta(milliseconds=1)
        base_package = ProgramLocalRLPackageManager().build(
            package_id="program-local-rl-package:accepted-v2.3",
            framework_version=__version__,
            source_repository=self.source_repository,
            source_commit=self.source_commit,
            third_party_lock_hash=DEFAULT_THIRD_PARTY_LOCK_HASH,
            intent=intent,
            authorization=authorization,
            result=result,
            created_at=base_created,
        )
        running_package = RunningAttestedProgramLocalRLPackageManager().build(
            package_id="running-attested-local-rl-package:accepted-v2.3",
            base_package=base_package,
            intent_binding=intent_binding,
            created_at=base_created + timedelta(milliseconds=1),
        )

        spec = build_program_local_rl_projection_spec(
            created_by="independent-program-projection-schema-author",
            created_at=intent_time + timedelta(milliseconds=2),
        )
        contract = NativeLocalRLRuntimeContractBuilder().build(
            package_type=ProgramLocalRLProjectionPackage,
            manager_type=ProgramLocalRLProjectionPackageManager,
            projection_spec=spec,
            reviewed_by="independent-native-runtime-contract-reviewer",
            reviewed_at=intent_time + timedelta(milliseconds=3),
            contract_id="native-local-rl-runtime-contract:accepted-v2.3",
        )
        verified_at = native_package.created_at + timedelta(milliseconds=2)
        runtime_attestation = RuntimeBoundNativeLocalRLAttestor().attest(
            projection_package,
            manager=ProgramLocalRLProjectionPackageManager(),
            contract=contract,
            projection_spec=spec,
            verified_by="independent-native-runtime-package-verifier",
            verified_at=verified_at,
            attestation_id="native-local-rl-runtime-attestation:accepted-v2.3",
            runtime_receipt_id="native-local-rl-runtime-receipt:accepted-v2.3",
            projection_receipt_id=(
                "native-local-rl-projection-receipt:accepted-v2.3"
            ),
        )
        attested_package = AttestedProgramLocalRLPackageManager().build(
            package_id="attested-program-local-rl-package:accepted-v2.3",
            base_package=base_package,
            native_attestation=(
                runtime_attestation.schema_attestation.base_attestation
            ),
            bound_by="independent-program-result-binder",
            bound_at=verified_at + timedelta(milliseconds=1),
            created_at=verified_at + timedelta(milliseconds=2),
        )
        schema_package = SchemaAttestedProgramLocalRLPackageManager().build(
            package_id="schema-attested-local-rl-package:accepted-v2.3",
            attested_package=attested_package,
            schema_attestation=runtime_attestation.schema_attestation,
            created_at=verified_at + timedelta(milliseconds=3),
        )
        runtime_package = RuntimeAttestedProgramLocalRLPackageManager().build(
            package_id="runtime-attested-local-rl-package:accepted-v2.3",
            schema_attested_package=schema_package,
            runtime_attestation=runtime_attestation,
            accepted_by="independent-runtime-evidence-acceptor",
            accepted_at=verified_at + timedelta(milliseconds=4),
        )
        fully_package = FullyAttestedProgramLocalRLPackageManager().build(
            package_id="fully-attested-local-rl-package:accepted-v2.3",
            running_attested_package=running_package,
            runtime_attested_package=runtime_package,
            accepted_by="independent-full-evidence-assembler",
            accepted_at=max(
                running_package.created_at,
                runtime_package.accepted_at,
            )
            + timedelta(milliseconds=1),
        )
        anchored_at = fully_package.accepted_at + timedelta(milliseconds=1)
        anchors = build_trusted_anchors(
            anchors_id="program-local-rl-trusted-anchors:accepted-v2.3",
            running_attestation_hash=running_attestation.attestation_hash,
            program_checkpoint=ProgramCheckpoint(
                event_count=running_attestation.program_checkpoint.event_count,
                head_hash=running_attestation.program_checkpoint.head_hash,
            ),
            campaign_checkpoint=ProgramCheckpoint(
                event_count=running_attestation.campaign_checkpoint.event_count,
                head_hash=running_attestation.campaign_checkpoint.head_hash,
            ),
            native_runtime_contract_hash=contract.contract_hash,
            native_projection_spec_hash=spec.spec_hash,
            native_local_rl_package_hash=native_package.package_hash,
            optimizer_evidence_hash=native_package.training.result_hash,
            heldout_evaluation_hash=native_package.decision.selected_report_hash,
            anchored_by="independent-external-anchor-store",
            anchored_at=anchored_at,
        )
        receipt = ProgramLocalRLAcceptanceManager().accept(
            fully_package,
            anchors,
            accepted_by="independent-final-evidence-acceptor",
            accepted_at=anchored_at + timedelta(milliseconds=1),
            receipt_id="program-local-rl-acceptance:accepted-v2.3",
        )
        created_at = max(
            source_program_package.created_at,
            running_attestation.attested_at,
            native_package.created_at,
            fully_package.accepted_at,
            anchors.anchored_at,
            receipt.accepted_at,
        ) + timedelta(milliseconds=1)
        return ProgramLocalRLAcceptedEvidenceManager().build(
            bundle_id=self.BUNDLE_ID,
            source_program_package=source_program_package,
            running_attestation=running_attestation,
            native_local_rl_package=native_package,
            projection_package=projection_package,
            fully_attested_package=fully_package,
            trusted_anchors=anchors,
            acceptance_receipt=receipt,
            created_at=created_at,
        )

    def _verify_persistent_running_state(
        self,
        bundle: ProgramLocalRLAcceptedEvidenceBundle,
    ) -> None:
        repository = SQLiteEvolutionProgramRepository(
            self.running_program_database
        )
        campaigns = SQLiteCampaignRepository(
            self.running_campaign_database
        )
        program_checkpoint = ProgramCheckpoint(
            event_count=bundle.running_attestation.program_checkpoint.event_count,
            head_hash=bundle.running_attestation.program_checkpoint.head_hash,
        )
        campaign_checkpoint = ProgramCheckpoint(
            event_count=bundle.running_attestation.campaign_checkpoint.event_count,
            head_hash=bundle.running_attestation.campaign_checkpoint.head_hash,
        )
        if (
            repository.verify_audit(program_checkpoint) is not True
            or campaigns.verify_audit(campaign_checkpoint) is not True
            or repository.verify_state(bundle.running_attestation.program_id)
            is not True
        ):
            raise RuntimeError(
                "Persistent running Program evidence did not verify."
            )
        head = repository.head(bundle.running_attestation.program_id)
        if (
            head.state != ProgramState.GENERATION_RUNNING
            or head.active_generation_id
            != bundle.running_attestation.generation_id
            or repository.checkpoint().event_count
            != program_checkpoint.event_count
            or campaigns.checkpoint().event_count
            != campaign_checkpoint.event_count
        ):
            raise RuntimeError(
                "Persistent running Program state differs from accepted anchors."
            )

    @staticmethod
    def _program_actor(
        package: EvolutionProgramPackageManifest,
        event_type: ProgramEventType,
        *,
        generation_id: str | None = None,
    ) -> str:
        matches = tuple(
            item
            for item in package.program_events
            if item.event_type == event_type
            and (
                generation_id is None
                or item.generation_id == generation_id
            )
        )
        if len(matches) != 1:
            raise RuntimeError(
                f"Source Program package lacks one exact {event_type.value} actor."
            )
        return matches[0].actor_id

    @staticmethod
    def _campaign_actor(
        package: EvolutionProgramPackageManifest,
        *,
        campaign_id: str | None,
        event_type: str,
    ) -> str:
        if campaign_id is None:
            raise RuntimeError("Source Generation lacks a Campaign ID.")
        matches = tuple(
            item
            for item in package.campaign_events
            if item.campaign_id == campaign_id
            and item.event_type == event_type
        )
        if len(matches) != 1:
            raise RuntimeError(
                f"Source Program package lacks one exact {event_type} Campaign actor."
            )
        return matches[0].actor_id

    @staticmethod
    def _result(
        bundle: ProgramLocalRLAcceptedEvidenceBundle,
        *,
        resumed: bool,
        optimizer_invoked: bool,
    ) -> ProgramLocalRLAcceptanceLabResult:
        return ProgramLocalRLAcceptanceLabResult(
            run_id=ProgramLocalRLAcceptanceLab.RUN_ID,
            resumed=resumed,
            optimizer_invoked=optimizer_invoked,
            program_id=bundle.running_attestation.program_id,
            generation_id=bundle.running_attestation.generation_id,
            running_attestation_hash=(
                bundle.running_attestation.attestation_hash
            ),
            native_local_rl_package_hash=(
                bundle.native_local_rl_package.package_hash
            ),
            selected_checkpoint_hash=(
                bundle.native_local_rl_package.decision.selected_checkpoint_hash
            ),
            fully_attested_package_hash=(
                bundle.fully_attested_package.package_hash
            ),
            anchors_hash=bundle.trusted_anchors.anchors_hash,
            acceptance_receipt_hash=(
                bundle.acceptance_receipt.receipt_hash
            ),
            bundle_path="",
            bundle_hash=bundle.bundle_hash,
        ).model_copy(update={"bundle_path": ""})


__all__ = [
    "ProgramLocalRLAcceptanceLab",
    "ProgramLocalRLAcceptanceLabResult",
    "ProgramLocalRLAcceptedEvidenceBundle",
    "ProgramLocalRLAcceptedEvidenceError",
    "ProgramLocalRLAcceptedEvidenceManager",
]
