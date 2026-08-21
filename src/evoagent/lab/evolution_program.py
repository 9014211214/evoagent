from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from evoagent import __version__
from evoagent.campaigns import (
    CampaignGovernanceService,
    CampaignState,
    SQLiteCampaignRepository,
)
from evoagent.domain.models import EvolutionAction, FailureLayer
from evoagent.lab.release_control import ShadowCanaryReleaseLab
from evoagent.lab.service import DEFAULT_THIRD_PARTY_LOCK_HASH
from evoagent.model_registry.models import canonical_sha256
from evoagent.program import (
    EvolutionProgramController,
    EvolutionProgramPackageManager,
    GenerationBudget,
    ProgramAction,
    ProgramBudget,
    ProgramControlEvidence,
    ProgramState,
    SQLiteEvolutionProgramRepository,
    build_attribution_receipt,
    build_generation_plan,
    build_program_policy,
)
from evoagent.release import ReleaseEvidencePackageManager


class MultiGenerationEvolutionLabResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    resumed: bool
    program_id: str
    program_state: str
    decision_actions: tuple[str, ...]
    generation_statuses: tuple[str, ...]
    generation_count: int = Field(ge=0)
    rollback_count: int = Field(ge=0)
    generation_campaign_count: int = Field(ge=0)
    generation_campaign_state: str
    approval_count: int = Field(ge=0)
    active_generation_id: str
    final_revision: int = Field(ge=0)
    g0_agent_identity_hash: str
    g1_agent_identity_hash: str
    g0_runtime_config_sha256: str
    g1_runtime_config_sha256: str
    same_champion_snapshot: bool
    authorization_started_generation: Literal[False] = False
    budget_control_action: str
    budget_control_state: str
    ambiguous_control_action: str
    ambiguous_control_state: str
    program_event_count: int = Field(ge=0)
    campaign_event_count: int = Field(ge=0)
    package_path: str
    package_hash: str
    second_run_read_only: Literal[True] = True
    synthetic_fixture: Literal[True] = True
    external_model_call_performed_by_evoagent: Literal[False] = False
    training_executed_by_evoagent: Literal[False] = False
    external_rollout_performed_by_evoagent: Literal[False] = False
    production_traffic_observed_by_evoagent: Literal[False] = False
    production_deployment_performed: Literal[False] = False
    upload_performed: Literal[False] = False
    official_benchmark_claimed: Literal[False] = False


class MultiGenerationEvolutionProgramLab:
    """Rollback feedback -> independent attribution -> one successor -> stop success."""

    RUN_ID = "multi-generation-evolution-program-lab-v1"
    PROGRAM_ID = "evolution-program:release-feedback-v1"
    G0 = "program-generation:g0"
    G1 = "program-generation:g1"
    CREATOR = "program-owner"
    SIGNAL_ACTOR = "program-feedback-ingestor"
    ATTRIBUTOR = "independent-context-attributor"
    DECISION_ACTOR = "program-policy-controller"
    PLANNER = "generation-planner"
    EVALUATOR = "generation-plan-evaluator"
    APPROVER_A = "generation-reviewer-a"
    APPROVER_B = "generation-reviewer-b"
    AUTH_SYNC = "generation-authorization-sync"
    OPERATOR = "generation-operator"
    START = datetime(2026, 8, 12, 1, 0, tzinfo=timezone.utc)

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
            raise ValueError("Evolution Program lab root must not be a symlink.")
        if len(source_commit) != 40 or any(
            character not in "0123456789abcdef" for character in source_commit
        ):
            raise ValueError("source_commit must be lowercase 40-character Git hex.")
        self.root = raw_root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.source_commit = source_commit
        self.source_repository = source_repository

    @property
    def release_root(self) -> Path:
        return self.root / "release"

    @property
    def program_database(self) -> Path:
        return self.root / "program-registry.db"

    @property
    def campaign_database(self) -> Path:
        return self.root / "program-campaigns.db"

    @property
    def package_path(self) -> Path:
        return self.root / "evolution-program-package.json"

    def run(self) -> MultiGenerationEvolutionLabResult:
        release_lab = ShadowCanaryReleaseLab(
            self.release_root,
            source_commit=self.source_commit,
            source_repository=self.source_repository,
        )
        release_result = release_lab.run()
        release_manager = ReleaseEvidencePackageManager()
        drift_package = release_manager.load_file(release_result.drift.package_path)
        passing_package = release_manager.load_file(release_result.passing.package_path)
        package_manager = EvolutionProgramPackageManager()
        if self.package_path.exists():
            package = package_manager.load_file(self.package_path)
            if (
                package.drift_release_package != drift_package
                or package.passing_release_package != passing_package
            ):
                raise RuntimeError(
                    "Read-only Program resume differs from frozen release packages."
                )
            self._verify_persistent_state(package)
            return self._result(package, resumed=True)

        policy = self._policy(max_generations=2)
        repository = SQLiteEvolutionProgramRepository(self.program_database)
        campaigns = SQLiteCampaignRepository(self.campaign_database)
        controller = EvolutionProgramController(
            repository=repository,
            campaign_governance=CampaignGovernanceService(campaigns),
        )
        _, g0_outcome, _ = controller.register_from_release(
            drift_package,
            program_id=self.PROGRAM_ID,
            policy=policy,
            generation_id=self.G0,
            outcome_id="program-outcome:g0:rollback",
            created_by=self.CREATOR,
            created_at=self.START,
        )
        signal, _ = controller.store_feedback(
            drift_package,
            program_id=self.PROGRAM_ID,
            generation_index=0,
            signal_id="program-signal:g0:rollback",
            actor_id=self.SIGNAL_ACTOR,
            created_at=self.START + timedelta(milliseconds=1),
        )
        attribution = build_attribution_receipt(
            signal,
            receipt_id="program-attribution:g0:context-policy",
            failure_layer=FailureLayer.CONTEXT,
            action=EvolutionAction.UPDATE_CONTEXT,
            confidence=1.0,
            supported_experiment_hashes=(
                canonical_sha256(
                    {
                        "experiment": "replace-context-policy",
                        "signal_hash": signal.signal_hash,
                        "result": "supported",
                    }
                ),
            ),
            attributor_id=self.ATTRIBUTOR,
            created_at=self.START + timedelta(milliseconds=2),
        )
        controller.store_attribution(
            self.PROGRAM_ID,
            attribution,
            actor_id=self.ATTRIBUTOR,
            created_at=self.START + timedelta(milliseconds=2),
        )
        d0, _ = controller.decide(
            program_id=self.PROGRAM_ID,
            generation_id=self.G0,
            decision_id="program-decision:g0:continue",
            decided_by=self.DECISION_ACTOR,
            decided_at=self.START + timedelta(milliseconds=3),
            signal=signal,
            attribution=attribution,
        )
        if d0.action != ProgramAction.CONTINUE:
            raise RuntimeError("Controlled Program did not continue after exact attribution.")
        plan = build_generation_plan(
            program_id=self.PROGRAM_ID,
            generation_id=self.G1,
            generation_index=1,
            parent_generation_id=self.G0,
            signal=signal,
            attribution=attribution,
            parent_agent_identity_hash=g0_outcome.agent_identity_hash,
            target_release_package=passing_package,
            budget=GenerationBudget(
                max_child_packages=1,
                max_pairs=1000,
                max_tokens=1_000_000,
                max_cost_usd=100.0,
            ),
            created_by=self.PLANNER,
            created_at=self.START + timedelta(milliseconds=4),
        )
        submission = controller.submit_generation(
            plan,
            evaluation_actor_id=self.EVALUATOR,
            submitted_at=self.START + timedelta(milliseconds=5),
        )
        campaign = controller.approve_generation(
            submission.campaign.campaign_id,
            actor_id=self.APPROVER_A,
            reason="Independent feedback and attribution review passed.",
            expected_revision=submission.campaign.revision,
        )
        campaign = controller.approve_generation(
            campaign.campaign_id,
            actor_id=self.APPROVER_B,
            reason="Independent budget and successor-plan review passed.",
            expected_revision=campaign.revision,
        )
        if campaign.state != CampaignState.AUTHORIZED:
            raise RuntimeError("Generation Campaign did not reach AUTHORIZED.")
        before_authorization = repository.head(self.PROGRAM_ID)
        controller.synchronize_authorization(
            program_id=self.PROGRAM_ID,
            generation_id=self.G1,
            campaign_id=campaign.campaign_id,
            actor_id=self.AUTH_SYNC,
        )
        after_authorization = repository.head(self.PROGRAM_ID)
        if (
            after_authorization.active_generation_id
            != before_authorization.active_generation_id
            or after_authorization.state != ProgramState.GENERATION_AUTHORIZED
        ):
            raise RuntimeError("Campaign authorization silently started Generation 1.")
        controller.start_generation(
            program_id=self.PROGRAM_ID,
            generation_id=self.G1,
            campaign_id=campaign.campaign_id,
            expected_revision=after_authorization.revision,
            actor_id=self.OPERATOR,
        )
        running_head = repository.head(self.PROGRAM_ID)
        controller.complete_generation(
            passing_package,
            program_id=self.PROGRAM_ID,
            generation_id=self.G1,
            outcome_id="program-outcome:g1:ready",
            expected_revision=running_head.revision,
            actor_id=self.OPERATOR,
            completed_at=self.START + timedelta(milliseconds=4100),
        )
        d1, _ = controller.decide(
            program_id=self.PROGRAM_ID,
            generation_id=self.G1,
            decision_id="program-decision:g1:stop-success",
            decided_by=self.DECISION_ACTOR,
            decided_at=self.START + timedelta(milliseconds=5200),
        )
        if d1.action != ProgramAction.STOP_SUCCESS:
            raise RuntimeError("Ready Generation 1 did not stop the Program successfully.")
        repository.verify_state(self.PROGRAM_ID)
        campaigns.verify_audit()
        budget_control = self._run_budget_control(drift_package)
        ambiguous_control = self._run_ambiguous_control(drift_package)
        campaign = campaigns.get(campaign.campaign_id)
        package = package_manager.build(
            package_id="evolution-program-package:release-feedback-v1",
            created_at=self.START + timedelta(milliseconds=5300),
            framework_version=__version__,
            source_repository=self.source_repository,
            source_commit=self.source_commit,
            third_party_lock_hash=DEFAULT_THIRD_PARTY_LOCK_HASH,
            drift_release_package=drift_package,
            passing_release_package=passing_package,
            policy=policy,
            signal=signal,
            attribution=attribution,
            generations=tuple(repository.list_generations(self.PROGRAM_ID)),
            decisions=tuple(repository.list_decisions(self.PROGRAM_ID)),
            generation_campaign=campaign,
            generation_approvals=tuple(campaigns.approvals(campaign.campaign_id)),
            final_head=repository.head(self.PROGRAM_ID),
            program_events=tuple(repository.events()),
            program_checkpoint=repository.checkpoint(),
            campaign_events=tuple(campaigns.audit_events()),
            campaign_checkpoint=campaigns.checkpoint(),
            budget_control=budget_control,
            ambiguous_control=ambiguous_control,
        )
        package_manager.export_file(package, self.package_path)
        self._verify_persistent_state(package)
        return self._result(package, resumed=False)

    def _policy(self, *, max_generations: int):
        return build_program_policy(
            policy_id=f"multi-generation-policy:max-{max_generations}",
            budget=ProgramBudget(
                max_generations=max_generations,
                max_rollbacks=2,
                max_holds=1,
                max_generation_campaigns=1,
                max_total_pairs=10_000,
                max_total_tokens=10_000_000,
                max_total_cost_usd=200.0,
            ),
            minimum_attribution_confidence=0.90,
            maximum_consecutive_non_improving=1,
        )

    def _run_budget_control(self, drift_package) -> ProgramControlEvidence:
        program_id = f"{self.PROGRAM_ID}:budget"
        repository = SQLiteEvolutionProgramRepository(
            self.root / "budget-control" / "program.db"
        )
        controller = EvolutionProgramController(
            repository=repository,
            campaign_governance=CampaignGovernanceService(
                SQLiteCampaignRepository(
                    self.root / "budget-control" / "campaigns.db"
                )
            ),
        )
        policy = self._policy(max_generations=1)
        controller.register_from_release(
            drift_package,
            program_id=program_id,
            policy=policy,
            generation_id=f"{self.G0}:budget",
            outcome_id="program-outcome:budget:g0",
            created_by=self.CREATOR,
            created_at=self.START,
        )
        signal, _ = controller.store_feedback(
            drift_package,
            program_id=program_id,
            generation_index=0,
            signal_id="program-signal:budget:g0",
            actor_id=self.SIGNAL_ACTOR,
            created_at=self.START + timedelta(milliseconds=1),
        )
        decision, _ = controller.decide(
            program_id=program_id,
            generation_id=f"{self.G0}:budget",
            decision_id="program-decision:budget:stop",
            decided_by=self.DECISION_ACTOR,
            decided_at=self.START + timedelta(milliseconds=2),
            signal=signal,
        )
        if decision.action != ProgramAction.STOP_BUDGET:
            raise RuntimeError("Budget control did not stop before another generation.")
        return self._control_evidence(
            "budget-control",
            repository,
            program_id,
        )

    def _run_ambiguous_control(self, drift_package) -> ProgramControlEvidence:
        program_id = f"{self.PROGRAM_ID}:ambiguous"
        repository = SQLiteEvolutionProgramRepository(
            self.root / "ambiguous-control" / "program.db"
        )
        controller = EvolutionProgramController(
            repository=repository,
            campaign_governance=CampaignGovernanceService(
                SQLiteCampaignRepository(
                    self.root / "ambiguous-control" / "campaigns.db"
                )
            ),
        )
        policy = self._policy(max_generations=2)
        controller.register_from_release(
            drift_package,
            program_id=program_id,
            policy=policy,
            generation_id=f"{self.G0}:ambiguous",
            outcome_id="program-outcome:ambiguous:g0",
            created_by=self.CREATOR,
            created_at=self.START,
        )
        signal, _ = controller.store_feedback(
            drift_package,
            program_id=program_id,
            generation_index=0,
            signal_id="program-signal:ambiguous:g0",
            actor_id=self.SIGNAL_ACTOR,
            created_at=self.START + timedelta(milliseconds=1),
        )
        attribution = build_attribution_receipt(
            signal,
            receipt_id="program-attribution:ambiguous:g0",
            failure_layer=FailureLayer.CONTEXT,
            action=EvolutionAction.UPDATE_CONTEXT,
            confidence=1.0,
            supported_experiment_hashes=(
                canonical_sha256({"experiment": "context-a"}),
                canonical_sha256({"experiment": "context-b"}),
            ),
            attributor_id=self.ATTRIBUTOR,
            created_at=self.START + timedelta(milliseconds=2),
        )
        controller.store_attribution(
            program_id,
            attribution,
            actor_id=self.ATTRIBUTOR,
            created_at=self.START + timedelta(milliseconds=2),
        )
        decision, _ = controller.decide(
            program_id=program_id,
            generation_id=f"{self.G0}:ambiguous",
            decision_id="program-decision:ambiguous:escalate",
            decided_by=self.DECISION_ACTOR,
            decided_at=self.START + timedelta(milliseconds=3),
            signal=signal,
            attribution=attribution,
        )
        if decision.action != ProgramAction.ESCALATE:
            raise RuntimeError("Ambiguous attribution control did not escalate.")
        return self._control_evidence(
            "ambiguous-control",
            repository,
            program_id,
        )

    @staticmethod
    def _control_evidence(
        control_id: str,
        repository: SQLiteEvolutionProgramRepository,
        program_id: str,
    ) -> ProgramControlEvidence:
        repository.verify_state(program_id)
        provisional = {
            "control_id": control_id,
            "policy": repository.get_program(program_id).policy,
            "generations": tuple(repository.list_generations(program_id)),
            "signals": tuple(repository.list_signals(program_id)),
            "attributions": tuple(repository.list_attributions(program_id)),
            "decisions": tuple(repository.list_decisions(program_id)),
            "final_head": repository.head(program_id),
            "events": tuple(repository.events()),
            "checkpoint": repository.checkpoint(),
            "generation_campaign_count": repository.head(
                program_id
            ).generation_campaign_count,
        }
        return ProgramControlEvidence(
            **provisional,
            control_hash=canonical_sha256(provisional),
        )

    def _verify_persistent_state(self, package) -> None:
        repository = SQLiteEvolutionProgramRepository(self.program_database)
        campaigns = SQLiteCampaignRepository(self.campaign_database)
        repository.verify_audit(package.program_checkpoint)
        repository.verify_state(package.final_head.program_id)
        campaigns.verify_audit(package.campaign_checkpoint)
        if (
            tuple(repository.list_generations(package.final_head.program_id))
            != package.generations
            or tuple(repository.list_signals(package.final_head.program_id))
            != (package.signal,)
            or tuple(repository.list_attributions(package.final_head.program_id))
            != (package.attribution,)
            or tuple(repository.list_decisions(package.final_head.program_id))
            != package.decisions
            or repository.head(package.final_head.program_id) != package.final_head
            or tuple(repository.events()) != package.program_events
            or campaigns.get(package.generation_campaign.campaign_id)
            != package.generation_campaign
            or tuple(campaigns.approvals(package.generation_campaign.campaign_id))
            != package.generation_approvals
            or tuple(campaigns.audit_events()) != package.campaign_events
        ):
            raise RuntimeError(
                "Persistent Program state differs from reproducible package."
            )
        for directory, control in (
            ("budget-control", package.budget_control),
            ("ambiguous-control", package.ambiguous_control),
        ):
            control_repository = SQLiteEvolutionProgramRepository(
                self.root / directory / "program.db"
            )
            program_id = control.final_head.program_id
            control_repository.verify_audit(control.checkpoint)
            control_repository.verify_state(program_id)
            if (
                tuple(control_repository.list_generations(program_id))
                != control.generations
                or tuple(control_repository.list_signals(program_id))
                != control.signals
                or tuple(control_repository.list_attributions(program_id))
                != control.attributions
                or tuple(control_repository.list_decisions(program_id))
                != control.decisions
                or control_repository.head(program_id) != control.final_head
                or tuple(control_repository.events()) != control.events
            ):
                raise RuntimeError(
                    f"Persistent {control.control_id} state differs from package."
                )

    def _result(self, package, *, resumed: bool) -> MultiGenerationEvolutionLabResult:
        g0, g1 = package.generations
        return MultiGenerationEvolutionLabResult(
            run_id=self.RUN_ID,
            resumed=resumed,
            program_id=package.final_head.program_id,
            program_state=package.final_head.state.value,
            decision_actions=tuple(item.action.value for item in package.decisions),
            generation_statuses=tuple(item.status.value for item in package.generations),
            generation_count=len(package.generations),
            rollback_count=package.final_head.rollback_count,
            generation_campaign_count=package.final_head.generation_campaign_count,
            generation_campaign_state=package.generation_campaign.state.value,
            approval_count=len(package.generation_approvals),
            active_generation_id=package.final_head.active_generation_id,
            final_revision=package.final_head.revision,
            g0_agent_identity_hash=g0.outcome.agent_identity_hash,
            g1_agent_identity_hash=g1.outcome.agent_identity_hash,
            g0_runtime_config_sha256=g0.outcome.runtime_config_sha256,
            g1_runtime_config_sha256=g1.outcome.runtime_config_sha256,
            same_champion_snapshot=(
                package.drift_release_package.plan.challenger_snapshot_id
                == package.passing_release_package.plan.challenger_snapshot_id
            ),
            budget_control_action=package.budget_control.decisions[-1].action.value,
            budget_control_state=package.budget_control.final_head.state.value,
            ambiguous_control_action=(
                package.ambiguous_control.decisions[-1].action.value
            ),
            ambiguous_control_state=package.ambiguous_control.final_head.state.value,
            program_event_count=len(package.program_events),
            campaign_event_count=len(package.campaign_events),
            package_path=str(self.package_path),
            package_hash=package.package_hash,
        )


__all__ = [
    "MultiGenerationEvolutionLabResult",
    "MultiGenerationEvolutionProgramLab",
]
