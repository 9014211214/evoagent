from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from evoagent.composite import (
    CompositeEvaluationService,
    CompositeSnapshotService,
    CompositeStopAction,
    SQLiteCompositeEvaluationRepository,
    SQLiteCompositeSnapshotRegistry,
    SQLiteCompositeEvaluationRepository,
    build_composite_stop_policy,
)
from evoagent.integrated.case_factory import (
    build_integrated_cases_from_initial_evaluation,
)
from evoagent.integrated.controlled_runtime import (
    ControlledCompositeRuntimeEvaluator,
)
from evoagent.integrated.executors import (
    GovernedLocalPolicyEvolutionExecutor,
    GovernedSkillEvolutionExecutor,
)
from evoagent.integrated.initial_state import (
    CONTROLLED_LOCAL_POLICY_CANDIDATE_ID,
    CONTROLLED_LOCAL_POLICY_FAMILY_ID,
    CONTROLLED_LOCAL_POLICY_INITIAL_ID,
    PreviewingLocalPolicyRegistry,
    build_controlled_initial_policy_checkpoint,
    build_controlled_initial_policy_record,
    prepare_controlled_initial_skill,
)
from evoagent.integrated.models import (
    IntegratedCaseStatus,
    IntegratedRunStatus,
    IntegratedTrack,
    build_integrated_run_policy,
)
from evoagent.integrated.package import (
    IntegratedEvolutionPackageManager,
)
from evoagent.integrated.repository_hardened import (
    SQLiteIntegratedEvolutionRepository,
)
from evoagent.integrated.service_hardened import (
    IntegratedDispatchAction,
    IntegratedSupervisorService,
)
from evoagent.lab.automatic_local_tool_final import (
    AutomaticLocalToolEvolutionLab,
)
from evoagent.lab.local_agentic_rl import LocalAgenticRLTrainingLab
from evoagent.lab.local_policy_promotion_final import (
    AcceptedLocalPolicyPromotionLab,
)
from evoagent.lab.program_local_rl_acceptance_final import (
    ProgramLocalRLAcceptedEvidenceManager,
)
from evoagent.lab.service import DEFAULT_THIRD_PARTY_LOCK_HASH
from evoagent.local_policy import (
    LocalPolicyPromotionPackageManager,
    SQLiteLocalPolicyRegistry,
)
from evoagent.skills import (
    CONTROLLED_DOCUMENT_SKILL_BASE_VERSION,
    CONTROLLED_DOCUMENT_SKILL_ID,
    SQLiteSkillRegistry,
    SkillStateBundleManager,
)


class IntegratedMultiTrackLabResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    lineage_id: str
    resumed: bool
    optimizer_invoked: bool
    active_snapshot_id: str
    active_snapshot_revision: int = Field(ge=0)
    active_skill_version: str
    active_policy_id: str
    active_policy_revision: int = Field(ge=0)
    composite_scores: tuple[float, ...]
    stop_actions: tuple[str, ...]
    case_count: int = Field(ge=0)
    track_result_count: int = Field(ge=0)
    integrated_event_count: int = Field(ge=0)
    composite_event_count: int = Field(ge=0)
    evaluation_event_count: int = Field(ge=0)
    package_path: str
    package_hash: str
    local_skill_evolution_performed: Literal[True] = True
    local_policy_optimization_performed: Literal[True] = True
    foundation_model_training_performed: Literal[False] = False
    production_activation_performed: Literal[False] = False
    production_deployment_performed: Literal[False] = False
    external_rollout_performed: Literal[False] = False


class IntegratedMultiTrackEvolutionLab:
    """A0 -> A1 -> A2 using real governed Skill and local-policy executors."""

    RUN_ID = "integrated-multitrack-evolution:v2.3"
    LINEAGE_ID = "composite-lineage:integrated-v2.3"
    A0 = "composite-snapshot:integrated:a0"
    A1 = "composite-snapshot:integrated:a1"
    A2 = "composite-snapshot:integrated:a2"

    RUN_CREATOR = "integrated-run-controller"
    CASE_ADMITTER = "integrated-case-admitter"
    SNAPSHOT_BOOTSTRAP = "integrated-composite-bootstrap"
    A1_BUILDER = "integrated-composite-skill-builder"
    A1_COMMITTER = "integrated-composite-skill-committer"
    A2_BUILDER = "integrated-composite-policy-builder"
    A2_COMMITTER = "integrated-composite-policy-committer"
    STOP_POLICY_REGISTRAR = "integrated-stop-policy-registrar"
    COMPOSITE_EVALUATOR = "integrated-composite-evaluator"
    STOP_DECIDER = "integrated-stop-decider"
    RUN_COMPLETER = "integrated-run-completer"

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
                "Integrated multi-track Lab root must not be a symlink."
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
    def integrated_database(self) -> Path:
        return self.root / "integrated-supervisor.db"

    @property
    def composite_database(self) -> Path:
        return self.root / "composite-snapshots.db"

    @property
    def evaluation_database(self) -> Path:
        return self.root / "composite-evaluations.db"

    @property
    def skill_root(self) -> Path:
        return self.root / "skill-track"

    @property
    def policy_root(self) -> Path:
        return self.root / "local-policy-track"

    @property
    def evaluation_root(self) -> Path:
        return self.root / "composite-runtime-evaluation"

    @property
    def package_path(self) -> Path:
        return self.root / "integrated-multitrack-evolution-package.json"

    def run(self) -> IntegratedMultiTrackLabResult:
        package_manager = IntegratedEvolutionPackageManager()
        if self.package_path.exists():
            package = package_manager.load_file(self.package_path)
            self._verify_persistent_state(package)
            self._verify_child_resume(package)
            self._verify_persistent_state(package)
            return self._result(
                package,
                resumed=True,
                optimizer_invoked=False,
            )

        context = self._context()
        self._ensure_run_and_initial_snapshot(context)
        self._ensure_initial_evaluation_cases_and_decision(context)
        self._ensure_skill_round(context)
        self._ensure_skill_snapshot_evaluation_and_decision(context)
        optimizer_invoked = self._ensure_local_policy_round(context)
        self._ensure_policy_snapshot_evaluation_and_stop(context)
        self._ensure_integrated_completion(context)
        package = self._build_package(context)
        package_manager.export_file(package, self.package_path)
        self._verify_persistent_state(package)
        return self._result(
            package,
            resumed=False,
            optimizer_invoked=optimizer_invoked,
        )

    def _context(self):
        skill_executor = GovernedSkillEvolutionExecutor(self.skill_root)
        policy_executor = GovernedLocalPolicyEvolutionExecutor(
            self.policy_root,
            source_commit=self.source_commit,
            source_repository=self.source_repository,
        )
        skill_registry = SQLiteSkillRegistry(
            skill_executor.lab.skill_database
        )
        composite_registry = SQLiteCompositeSnapshotRegistry(
            self.composite_database
        )
        snapshots = composite_registry.list_snapshots(self.LINEAGE_ID)
        initial_time = (
            snapshots[0].manifest.created_at
            if snapshots
            else datetime.now(timezone.utc)
        )
        if not skill_registry.list_skill_ids():
            prepare_controlled_initial_skill(
                skill_registry,
                actor_id="integrated-skill-bootstrap",
                created_at=initial_time,
            )
        active_skill = skill_registry.active(CONTROLLED_DOCUMENT_SKILL_ID)
        if not snapshots and (
            active_skill.spec.version
            != CONTROLLED_DOCUMENT_SKILL_BASE_VERSION
            or skill_registry.active_revision(
                CONTROLLED_DOCUMENT_SKILL_ID
            )
            != 0
        ):
            raise RuntimeError(
                "Integrated Lab cannot create A0 after Skill evolution already occurred."
            )

        local_rl_lab = LocalAgenticRLTrainingLab(
            policy_executor.acceptance_lab.native_local_rl_root,
            source_commit=self.source_commit,
            source_repository=self.source_repository,
        )
        local_rl_manifest = local_rl_lab.build_manifest()
        p0_checkpoint = build_controlled_initial_policy_checkpoint(
            local_rl_manifest
        )
        preview_record = build_controlled_initial_policy_record(
            local_rl_manifest,
            source_commit=self.source_commit,
            created_at=initial_time,
        )
        actual_policy_registry = SQLiteLocalPolicyRegistry(
            policy_executor.promotion_root / "local-policy.db"
        )
        policy_view = PreviewingLocalPolicyRegistry(
            actual_policy_registry,
            preview_record,
        )
        snapshot_service = CompositeSnapshotService(
            composite_registry,
            skill_registry=skill_registry,
            local_policy_registry=policy_view,
            skill_id=CONTROLLED_DOCUMENT_SKILL_ID,
            local_policy_family_id=CONTROLLED_LOCAL_POLICY_FAMILY_ID,
        )
        evaluation_repository = SQLiteCompositeEvaluationRepository(
            self.evaluation_database
        )
        evaluation_service = CompositeEvaluationService(
            composite_registry,
            evaluation_repository,
        )
        runtime_evaluator = ControlledCompositeRuntimeEvaluator(
            self.evaluation_root,
            local_rl_manifest=local_rl_manifest,
        )
        integrated_repository = SQLiteIntegratedEvolutionRepository(
            self.integrated_database
        )
        supervisor = IntegratedSupervisorService(
            integrated_repository,
            skill_executor_id=skill_executor.executor_id,
            local_policy_executor_id=policy_executor.executor_id,
            evaluation_service=evaluation_service,
        )
        return {
            "skill_executor": skill_executor,
            "policy_executor": policy_executor,
            "skill_registry": skill_registry,
            "actual_policy_registry": actual_policy_registry,
            "policy_view": policy_view,
            "local_rl_manifest": local_rl_manifest,
            "p0_checkpoint": p0_checkpoint,
            "composite_registry": composite_registry,
            "snapshot_service": snapshot_service,
            "evaluation_repository": evaluation_repository,
            "evaluation_service": evaluation_service,
            "runtime_evaluator": runtime_evaluator,
            "integrated_repository": integrated_repository,
            "supervisor": supervisor,
        }

    def _ensure_run_and_initial_snapshot(self, context) -> None:
        now = datetime.now(timezone.utc)
        run_policy = build_integrated_run_policy(
            max_cases=8,
            max_rounds=3,
            min_policy_cases=2,
            max_skill_executions=1,
            max_policy_executions=1,
        )
        context["integrated_repository"].create_run(
            run_id=self.RUN_ID,
            lineage_id=self.LINEAGE_ID,
            policy=run_policy,
            actor_id=self.RUN_CREATOR,
            now=now,
        )
        snapshots = context["composite_registry"].list_snapshots(
            self.LINEAGE_ID
        )
        if not snapshots:
            context["snapshot_service"].register_initial_from_components(
                lineage_id=self.LINEAGE_ID,
                snapshot_id=self.A0,
                actor_id=self.SNAPSHOT_BOOTSTRAP,
                created_at=datetime.now(timezone.utc),
                **context["runtime_evaluator"].snapshot_contract_kwargs(),
            )
        snapshots = context["composite_registry"].list_snapshots(
            self.LINEAGE_ID
        )
        if (
            snapshots[0].snapshot_id != self.A0
            or snapshots[0].manifest.round_index != 0
        ):
            raise RuntimeError(
                "Integrated composite lineage does not begin with canonical A0."
            )
        stop_policy = build_composite_stop_policy(
            policy_id="integrated-composite-stop-policy:v2.3",
            max_rounds=3,
            target_composite_score=1.0,
        )
        context["evaluation_service"].register_policy(
            self.LINEAGE_ID,
            stop_policy,
            actor_id=self.STOP_POLICY_REGISTRAR,
            now=datetime.now(timezone.utc),
        )

    def _ensure_initial_evaluation_cases_and_decision(self, context) -> None:
        evaluations = context["evaluation_repository"].list_evaluations(
            self.LINEAGE_ID
        )
        if not evaluations:
            active = context["composite_registry"].active(
                self.LINEAGE_ID
            )
            evaluated_at = datetime.now(timezone.utc)
            derived = context["runtime_evaluator"].evaluate(
                active.manifest,
                skill_record=context["skill_registry"].active(
                    CONTROLLED_DOCUMENT_SKILL_ID
                ),
                policy_checkpoint=context["p0_checkpoint"],
                evaluation_id="composite-evaluation:integrated:a0",
                evaluator_id=self.COMPOSITE_EVALUATOR,
                evaluated_at=evaluated_at,
            )
            recorded = context["evaluation_service"].evaluate_active(
                self.LINEAGE_ID,
                evaluation_id=derived.evaluation_id,
                outcomes=derived.outcomes,
                evaluator_id=self.COMPOSITE_EVALUATOR,
                evaluated_at=evaluated_at,
                now=evaluated_at,
            )
            if recorded.evaluation != derived:
                raise RuntimeError(
                    "Persisted A0 Evaluation differs from runtime-derived evidence."
                )
        evaluation = context["evaluation_repository"].list_evaluations(
            self.LINEAGE_ID
        )[0].evaluation
        self._require_score(evaluation, 0.5, 0.0, 0.25)

        run = context["integrated_repository"].get_run(self.RUN_ID)
        cases = build_integrated_cases_from_initial_evaluation(
            evaluation,
            policy=run.policy,
        )
        for case in cases:
            context["integrated_repository"].admit_case(
                self.RUN_ID,
                case,
                actor_id=self.CASE_ADMITTER,
                now=datetime.now(timezone.utc),
            )
        decisions = context["evaluation_repository"].list_decisions(
            self.LINEAGE_ID
        )
        if not decisions:
            actionable = tuple(
                item.case.case_id
                for item in context["integrated_repository"].pending_cases(
                    self.RUN_ID
                )
            )
            decision = context["evaluation_service"].decide_active(
                self.LINEAGE_ID,
                decision_id="composite-stop-decision:integrated:a0",
                actionable_case_ids=actionable,
                budget_exhausted=False,
                decided_by=self.STOP_DECIDER,
                decided_at=datetime.now(timezone.utc),
                now=datetime.now(timezone.utc),
            ).decision
            if decision.action != CompositeStopAction.CONTINUE:
                raise RuntimeError("A0 must continue to the Skill intervention.")

    def _ensure_skill_round(self, context) -> None:
        repository = context["integrated_repository"]
        run = repository.get_run(self.RUN_ID)
        if run.skill_execution_count == 0:
            plan = context["supervisor"].plan_next(
                self.RUN_ID,
                plan_id="integrated-dispatch:real-skill",
                planned_at=datetime.now(timezone.utc),
            )
            if plan.action not in {
                IntegratedDispatchAction.CLAIM_SKILL,
                IntegratedDispatchAction.RESUME_SKILL,
            }:
                raise RuntimeError(
                    f"Integrated Skill round received {plan.action.value}."
                )
            claimed = context["supervisor"].claim_plan(
                plan,
                now=datetime.now(timezone.utc),
            )
            result = context["skill_executor"].execute(
                self.RUN_ID,
                claimed,
            )
            current = repository.get_run(self.RUN_ID)
            context["supervisor"].record_result(
                result,
                expected_run_revision=current.revision,
                now=datetime.now(timezone.utc),
            )
        results = tuple(
            item
            for item in repository.list_results(self.RUN_ID)
            if item.track == IntegratedTrack.SKILL
        )
        if len(results) != 1:
            raise RuntimeError(
                "Integrated Skill round lacks one exact result."
            )
        active = context["composite_registry"].active(self.LINEAGE_ID)
        if active.manifest.round_index == 0:
            result = results[0]
            manifest = context["snapshot_service"].build_child_from_components(
                lineage_id=self.LINEAGE_ID,
                snapshot_id=self.A1,
                expected_component="skill",
                source_case_ids=result.case_ids,
                source_decision_hashes=result.source_decision_hashes,
                source_package_hashes=result.source_package_hashes,
                created_by=self.A1_BUILDER,
                created_at=datetime.now(timezone.utc),
            )
            context["snapshot_service"].commit(
                manifest,
                expected_active_revision=0,
                actor_id=self.A1_COMMITTER,
                now=datetime.now(timezone.utc),
            )

    def _ensure_skill_snapshot_evaluation_and_decision(self, context) -> None:
        evaluations = context["evaluation_repository"].list_evaluations(
            self.LINEAGE_ID
        )
        if len(evaluations) == 1:
            active = context["composite_registry"].active(
                self.LINEAGE_ID
            )
            evaluated_at = datetime.now(timezone.utc)
            derived = context["runtime_evaluator"].evaluate(
                active.manifest,
                skill_record=context["skill_registry"].active(
                    CONTROLLED_DOCUMENT_SKILL_ID
                ),
                policy_checkpoint=context["p0_checkpoint"],
                evaluation_id="composite-evaluation:integrated:a1",
                evaluator_id=self.COMPOSITE_EVALUATOR,
                evaluated_at=evaluated_at,
                parent=evaluations[0].evaluation,
            )
            recorded = context["evaluation_service"].evaluate_active(
                self.LINEAGE_ID,
                evaluation_id=derived.evaluation_id,
                outcomes=derived.outcomes,
                evaluator_id=self.COMPOSITE_EVALUATOR,
                evaluated_at=evaluated_at,
                now=evaluated_at,
            )
            if recorded.evaluation != derived:
                raise RuntimeError(
                    "Persisted A1 Evaluation differs from runtime-derived evidence."
                )
        evaluation = context["evaluation_repository"].list_evaluations(
            self.LINEAGE_ID
        )[1].evaluation
        self._require_score(evaluation, 1.0, 0.0, 0.5)
        decisions = context["evaluation_repository"].list_decisions(
            self.LINEAGE_ID
        )
        if len(decisions) == 1:
            actionable = tuple(
                item.case.case_id
                for item in context["integrated_repository"].pending_cases(
                    self.RUN_ID,
                    IntegratedTrack.LOCAL_POLICY,
                )
            )
            decision = context["evaluation_service"].decide_active(
                self.LINEAGE_ID,
                decision_id="composite-stop-decision:integrated:a1",
                actionable_case_ids=actionable,
                budget_exhausted=False,
                decided_by=self.STOP_DECIDER,
                decided_at=datetime.now(timezone.utc),
                now=datetime.now(timezone.utc),
            ).decision
            if decision.action != CompositeStopAction.CONTINUE:
                raise RuntimeError(
                    "A1 must continue to the local-policy intervention."
                )

    def _ensure_local_policy_round(self, context) -> bool:
        repository = context["integrated_repository"]
        run = repository.get_run(self.RUN_ID)
        optimizer_invoked = False
        if run.policy_execution_count == 0:
            plan = context["supervisor"].plan_next(
                self.RUN_ID,
                plan_id="integrated-dispatch:real-local-policy",
                planned_at=datetime.now(timezone.utc),
            )
            if plan.action not in {
                IntegratedDispatchAction.CLAIM_LOCAL_POLICY,
                IntegratedDispatchAction.RESUME_LOCAL_POLICY,
            }:
                raise RuntimeError(
                    f"Integrated policy round received {plan.action.value}."
                )
            claimed = context["supervisor"].claim_plan(
                plan,
                now=datetime.now(timezone.utc),
            )
            result = context["policy_executor"].execute(
                self.RUN_ID,
                claimed,
            )
            accepted_result = context[
                "policy_executor"
            ].acceptance_lab.run()
            optimizer_invoked = accepted_result.optimizer_invoked
            current = repository.get_run(self.RUN_ID)
            context["supervisor"].record_result(
                result,
                expected_run_revision=current.revision,
                now=datetime.now(timezone.utc),
            )
        results = tuple(
            item
            for item in repository.list_results(self.RUN_ID)
            if item.track == IntegratedTrack.LOCAL_POLICY
        )
        if len(results) != 1:
            raise RuntimeError(
                "Integrated local-policy round lacks one exact result."
            )
        context["policy_view"].verify_actual_parent()
        active = context["composite_registry"].active(self.LINEAGE_ID)
        if active.manifest.round_index == 1:
            result = results[0]
            manifest = context["snapshot_service"].build_child_from_components(
                lineage_id=self.LINEAGE_ID,
                snapshot_id=self.A2,
                expected_component="local_policy",
                source_case_ids=result.case_ids,
                source_decision_hashes=result.source_decision_hashes,
                source_package_hashes=result.source_package_hashes,
                created_by=self.A2_BUILDER,
                created_at=datetime.now(timezone.utc),
            )
            context["snapshot_service"].commit(
                manifest,
                expected_active_revision=1,
                actor_id=self.A2_COMMITTER,
                now=datetime.now(timezone.utc),
            )
        return optimizer_invoked

    def _ensure_policy_snapshot_evaluation_and_stop(self, context) -> None:
        bundle = ProgramLocalRLAcceptedEvidenceManager().load_file(
            context["policy_executor"].acceptance_lab.bundle_path
        )
        selected_hash = (
            bundle.native_local_rl_package.decision.selected_checkpoint_hash
        )
        selected = tuple(
            item
            for item in bundle.native_local_rl_package.training.retained_checkpoints
            if item.checkpoint_hash == selected_hash
        )
        if len(selected) != 1:
            raise RuntimeError(
                "Accepted native local-RL package lacks one selected checkpoint."
            )
        evaluations = context["evaluation_repository"].list_evaluations(
            self.LINEAGE_ID
        )
        if len(evaluations) == 2:
            active = context["composite_registry"].active(
                self.LINEAGE_ID
            )
            evaluated_at = datetime.now(timezone.utc)
            derived = context["runtime_evaluator"].evaluate(
                active.manifest,
                skill_record=context["skill_registry"].active(
                    CONTROLLED_DOCUMENT_SKILL_ID
                ),
                policy_checkpoint=selected[0],
                evaluation_id="composite-evaluation:integrated:a2",
                evaluator_id=self.COMPOSITE_EVALUATOR,
                evaluated_at=evaluated_at,
                parent=evaluations[1].evaluation,
            )
            recorded = context["evaluation_service"].evaluate_active(
                self.LINEAGE_ID,
                evaluation_id=derived.evaluation_id,
                outcomes=derived.outcomes,
                evaluator_id=self.COMPOSITE_EVALUATOR,
                evaluated_at=evaluated_at,
                now=evaluated_at,
            )
            if recorded.evaluation != derived:
                raise RuntimeError(
                    "Persisted A2 Evaluation differs from runtime-derived evidence."
                )
        evaluation = context["evaluation_repository"].list_evaluations(
            self.LINEAGE_ID
        )[2].evaluation
        self._require_score(evaluation, 1.0, 1.0, 1.0)
        if evaluation.safety_violation_count != 0:
            raise RuntimeError("A2 contains a safety violation.")
        decisions = context["evaluation_repository"].list_decisions(
            self.LINEAGE_ID
        )
        if len(decisions) == 2:
            pending = context["integrated_repository"].pending_cases(
                self.RUN_ID
            )
            if pending:
                raise RuntimeError(
                    "A2 cannot stop while automatic Cases remain pending."
                )
            decision = context["evaluation_service"].decide_active(
                self.LINEAGE_ID,
                decision_id="composite-stop-decision:integrated:a2",
                actionable_case_ids=(),
                budget_exhausted=False,
                decided_by=self.STOP_DECIDER,
                decided_at=datetime.now(timezone.utc),
                now=datetime.now(timezone.utc),
            ).decision
            if decision.action != CompositeStopAction.STOP:
                raise RuntimeError("A2 did not reach the deterministic STOP.")

    def _ensure_integrated_completion(self, context) -> None:
        run = context["integrated_repository"].get_run(self.RUN_ID)
        if run.status not in {
            IntegratedRunStatus.STOPPED,
            IntegratedRunStatus.ESCALATED,
        }:
            context["supervisor"].complete_from_latest_decision(
                self.RUN_ID,
                actor_id=self.RUN_COMPLETER,
                expected_run_revision=run.revision,
                now=datetime.now(timezone.utc),
            )
        run = context["integrated_repository"].get_run(self.RUN_ID)
        if run.status != IntegratedRunStatus.STOPPED:
            raise RuntimeError(
                "Controlled integrated run did not terminate with STOPPED."
            )
        context["supervisor"].verify_state(self.RUN_ID)

    def _build_package(self, context):
        skill_child = context["skill_executor"].lab.run()
        accepted = ProgramLocalRLAcceptedEvidenceManager().load_file(
            context["policy_executor"].acceptance_lab.bundle_path
        )
        promotion_path = (
            context["policy_executor"].promotion_root
            / "local-policy-promotion-package.json"
        )
        promotion = LocalPolicyPromotionPackageManager().load_file(
            promotion_path
        )
        skill_state = SkillStateBundleManager().build(
            context["skill_registry"]
        )
        integrated_repository = context["integrated_repository"]
        composite_registry = context["composite_registry"]
        evaluation_repository = context["evaluation_repository"]
        created_at = datetime.now(timezone.utc)
        return IntegratedEvolutionPackageManager().build(
            package_id="integrated-evolution-package:v2.3",
            source_repository=self.source_repository,
            source_commit=self.source_commit,
            third_party_lock_hash=DEFAULT_THIRD_PARTY_LOCK_HASH,
            run=integrated_repository.get_run(self.RUN_ID),
            cases=integrated_repository.list_cases(self.RUN_ID),
            track_results=integrated_repository.list_results(self.RUN_ID),
            integrated_events=integrated_repository.events(self.RUN_ID),
            integrated_checkpoint=integrated_repository.checkpoint(
                self.RUN_ID
            ),
            composite_snapshots=composite_registry.list_snapshots(
                self.LINEAGE_ID
            ),
            composite_head=composite_registry.head(self.LINEAGE_ID),
            composite_events=composite_registry.events(self.LINEAGE_ID),
            composite_checkpoint=composite_registry.checkpoint(
                self.LINEAGE_ID
            ),
            evaluation_policy=evaluation_repository.policy(
                self.LINEAGE_ID
            ),
            evaluations=evaluation_repository.list_evaluations(
                self.LINEAGE_ID
            ),
            stop_decisions=evaluation_repository.list_decisions(
                self.LINEAGE_ID
            ),
            evaluation_events=evaluation_repository.events(
                self.LINEAGE_ID
            ),
            evaluation_checkpoint=evaluation_repository.checkpoint(
                self.LINEAGE_ID
            ),
            skill_state=skill_state,
            skill_child_result=skill_child,
            accepted_program_local_rl=accepted,
            local_policy_promotion=promotion,
            created_at=created_at,
        )

    def _verify_persistent_state(self, package) -> None:
        context = self._context()
        integrated_repository = context["integrated_repository"]
        composite_registry = context["composite_registry"]
        evaluation_repository = context["evaluation_repository"]
        integrated_repository.verify_state(self.RUN_ID)
        composite_registry.verify_state(self.LINEAGE_ID)
        evaluation_repository.verify_state(self.LINEAGE_ID)
        if (
            integrated_repository.get_run(self.RUN_ID) != package.run
            or integrated_repository.list_cases(self.RUN_ID) != package.cases
            or integrated_repository.list_results(self.RUN_ID)
            != package.track_results
            or integrated_repository.events(self.RUN_ID)
            != package.integrated_events
            or integrated_repository.checkpoint(self.RUN_ID)
            != package.integrated_checkpoint
            or composite_registry.list_snapshots(self.LINEAGE_ID)
            != package.composite_snapshots
            or composite_registry.head(self.LINEAGE_ID)
            != package.composite_head
            or composite_registry.events(self.LINEAGE_ID)
            != package.composite_events
            or composite_registry.checkpoint(self.LINEAGE_ID)
            != package.composite_checkpoint
            or evaluation_repository.policy(self.LINEAGE_ID)
            != package.evaluation_policy
            or evaluation_repository.list_evaluations(self.LINEAGE_ID)
            != package.evaluations
            or evaluation_repository.list_decisions(self.LINEAGE_ID)
            != package.stop_decisions
            or evaluation_repository.events(self.LINEAGE_ID)
            != package.evaluation_events
            or evaluation_repository.checkpoint(self.LINEAGE_ID)
            != package.evaluation_checkpoint
        ):
            raise RuntimeError(
                "Persistent integrated state differs from immutable package."
            )
        current_skill = SkillStateBundleManager().build(
            context["skill_registry"]
        )
        if (
            current_skill.records != package.skill_state.records
            or current_skill.active_versions
            != package.skill_state.active_versions
            or current_skill.active_revisions
            != package.skill_state.active_revisions
            or current_skill.events != package.skill_state.events
        ):
            raise RuntimeError(
                "Persistent Skill state differs from integrated package."
            )
        context["policy_view"].verify_actual_parent()
        IntegratedEvolutionPackageManager.verify(package)

    def _verify_child_resume(self, package) -> None:
        skill = AutomaticLocalToolEvolutionLab(self.skill_root).run()
        accepted = context_accepted = ProgramLocalRLAcceptedEvidenceManager().load_file(
            self.policy_root
            / "accepted-program-local-rl"
            / "program-local-rl-accepted-evidence.json"
        )
        policy_acceptance = self._context()["policy_executor"].acceptance_lab.run()
        promotion = AcceptedLocalPolicyPromotionLab(
            self.policy_root / "accepted-local-policy-promotion",
            accepted_program_package=accepted.fully_attested_package,
            trusted_anchors=accepted.trusted_anchors,
            acceptance_receipt=accepted.acceptance_receipt,
            source_commit=self.source_commit,
            perform_rollback=False,
        ).run()
        if (
            not skill.resumed
            or not policy_acceptance.resumed
            or policy_acceptance.optimizer_invoked
            or not promotion.resumed
            or skill.active_version
            != package.skill_child_result.active_version
            or policy_acceptance.bundle_hash
            != package.accepted_program_local_rl.bundle_hash
            or promotion.package_hash
            != package.local_policy_promotion.package_hash
        ):
            raise RuntimeError(
                "Integrated child lifecycle did not resume read-only."
            )

    @staticmethod
    def _require_score(
        evaluation,
        skill_score: float,
        policy_score: float,
        composite_score: float,
    ) -> None:
        if (
            evaluation.skill_score != skill_score
            or evaluation.local_policy_score != policy_score
            or evaluation.composite_score != composite_score
            or evaluation.regression_count != 0
        ):
            raise RuntimeError(
                "Controlled composite score differs from the frozen target."
            )

    def _result(
        self,
        package,
        *,
        resumed: bool,
        optimizer_invoked: bool,
    ) -> IntegratedMultiTrackLabResult:
        return IntegratedMultiTrackLabResult(
            run_id=package.run.run_id,
            lineage_id=package.run.lineage_id,
            resumed=resumed,
            optimizer_invoked=optimizer_invoked,
            active_snapshot_id=package.composite_head.active_snapshot_id,
            active_snapshot_revision=package.composite_head.revision,
            active_skill_version=(
                package.skill_state.active_versions[
                    CONTROLLED_DOCUMENT_SKILL_ID
                ]
            ),
            active_policy_id=(
                package.local_policy_promotion.final_head.active_policy_id
            ),
            active_policy_revision=(
                package.local_policy_promotion.final_head.revision
            ),
            composite_scores=tuple(
                item.evaluation.composite_score
                for item in package.evaluations
            ),
            stop_actions=tuple(
                item.decision.action.value
                for item in package.stop_decisions
            ),
            case_count=len(package.cases),
            track_result_count=len(package.track_results),
            integrated_event_count=len(package.integrated_events),
            composite_event_count=len(package.composite_events),
            evaluation_event_count=len(package.evaluation_events),
            package_path=str(self.package_path),
            package_hash=package.package_hash,
        )


__all__ = [
    "IntegratedMultiTrackEvolutionLab",
    "IntegratedMultiTrackLabResult",
]
