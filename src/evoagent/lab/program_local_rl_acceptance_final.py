from __future__ import annotations

from datetime import timedelta

from evoagent.campaigns import (
    CampaignCheckpoint,
    CampaignGovernanceService,
    CampaignState,
    SQLiteCampaignRepository,
)
from evoagent.lab.evolution_program_hardened import (
    MultiGenerationEvolutionProgramLab,
)
from evoagent.lab.local_agentic_rl import LocalAgenticRLTrainingLab
from evoagent.local_rl import LocalRLPackageManager
from evoagent.program import (
    EvolutionProgramController,
    EvolutionProgramPackageManager,
    ProgramCheckpoint,
    ProgramEventType,
    ProgramExecutionCheckpoint,
    ProgramState,
    RunningGenerationAttestation,
    SQLiteEvolutionProgramRepository,
)

from .program_local_rl_acceptance import (
    ProgramLocalRLAcceptanceLab as _BaseAcceptanceLab,
    ProgramLocalRLAcceptanceLabResult,
    ProgramLocalRLAcceptedEvidenceBundle,
    ProgramLocalRLAcceptedEvidenceError,
    ProgramLocalRLAcceptedEvidenceManager,
)


class ProgramLocalRLAcceptanceLab(_BaseAcceptanceLab):
    """Final acceptance Lab with hardened Program evidence and scoped actors."""

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
        running_attestation = self._running_attestation(source_package)
        native_result = LocalAgenticRLTrainingLab(
            self.native_local_rl_root,
            created_at=running_attestation.attested_at + timedelta(milliseconds=3),
            decided_at=running_attestation.attested_at + timedelta(milliseconds=8),
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
                or bundle.running_attestation != running_attestation
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

        projection_package = self._projection_package(native_package)
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
        package,
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
                ProgramEventType.PROGRAM_REGISTERED,
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

    @staticmethod
    def _projection_package(native_package):
        from evoagent.local_rl import ProgramLocalRLProjectionPackageManager

        return ProgramLocalRLProjectionPackageManager().build(
            native_package,
            projection_package_id=(
                "program-local-rl-projection:accepted-v2.3"
            ),
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
        campaign_checkpoint = CampaignCheckpoint(
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
            or repository.checkpoint() != program_checkpoint
            or campaigns.checkpoint() != campaign_checkpoint
        ):
            raise RuntimeError(
                "Persistent running Program state differs from accepted anchors."
            )

    @staticmethod
    def _program_actor(
        package,
        event_type: ProgramEventType,
        *,
        generation_id: str | None = None,
    ) -> str:
        program_id = package.generations[0].program_id
        matches = tuple(
            item
            for item in package.program_events
            if item.program_id == program_id
            and item.event_type == event_type
            and (
                generation_id is None
                or item.generation_id == generation_id
            )
        )
        if len(matches) != 1:
            raise RuntimeError(
                "Source Program package lacks one exact Program-scoped "
                f"{event_type.value} actor."
            )
        return matches[0].actor_id

    def _result(
        self,
        bundle: ProgramLocalRLAcceptedEvidenceBundle,
        *,
        resumed: bool,
        optimizer_invoked: bool,
    ) -> ProgramLocalRLAcceptanceLabResult:
        return ProgramLocalRLAcceptanceLabResult(
            run_id=self.RUN_ID,
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
            bundle_path=str(self.bundle_path),
            bundle_hash=bundle.bundle_hash,
        )


__all__ = [
    "ProgramLocalRLAcceptanceLab",
    "ProgramLocalRLAcceptanceLabResult",
    "ProgramLocalRLAcceptedEvidenceBundle",
    "ProgramLocalRLAcceptedEvidenceError",
    "ProgramLocalRLAcceptedEvidenceManager",
]
