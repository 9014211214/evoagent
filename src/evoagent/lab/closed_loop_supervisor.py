from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from evoagent import __version__
from evoagent.domain.models import FailureLayer
from evoagent.lab.automatic_local_tool import AutomaticLocalToolEvolutionLab
from evoagent.lab.cross_layer import (
    ExecutableCrossLayerAttributionLab,
    ExecutableLayerDispatchResult,
)
from evoagent.lab.model_candidate_admission import ModelCandidateAdmissionLab
from evoagent.lab.service import DEFAULT_THIRD_PARTY_LOCK_HASH
from evoagent.supervisor import (
    ClosedLoopEvolutionPackageManager,
    PersistentEvolutionSupervisor,
    SQLiteSupervisorRepository,
    SupervisorBudget,
    SupervisorCase,
    SupervisorCaseRecord,
    SupervisorCaseStatus,
    SupervisorOutcome,
    SupervisorPolicy,
    SupervisorRunStatus,
    SupervisorScoreSummary,
    SupervisorTrack,
    build_supervisor_case,
    canonical_sha256,
)


class ClosedLoopEvolutionLabResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    resumed: bool
    run_status: str
    case_ids: tuple[str, ...]
    tracks: tuple[str, ...]
    case_statuses: tuple[str, ...]
    skill_child_run_id: str
    model_child_run_id: str
    skill_result_hash: str
    model_result_hash: str
    training_intent_package_hash: str
    model_admission_package_hash: str
    skill_initial_score: float
    skill_final_score: float
    model_initial_score: float
    model_final_score: float
    composite_initial_score: float
    composite_final_score: float
    composite_gain: float
    escalation_count: int = Field(ge=0)
    supervisor_case_count: int = Field(ge=0)
    supervisor_event_count: int = Field(ge=0)
    supervisor_checkpoint: dict
    package_path: str
    package_hash: str
    restart_verified: Literal[True] = True
    synthetic_fixture: Literal[True] = True
    training_executed_by_evoagent: Literal[False] = False
    external_execution_performed: Literal[False] = False
    production_deployment_performed: Literal[False] = False


class _SkillEvolutionExecutor:
    executor_id = "governed-skill-evolution-executor-v1"
    track = SupervisorTrack.SKILL
    idempotent = True
    COMPLETED_AT = datetime(2026, 8, 11, 6, 10, tzinfo=timezone.utc)

    def __init__(self, root: Path):
        self.root = root

    def execute(self, case: SupervisorCase) -> SupervisorOutcome:
        result = AutomaticLocalToolEvolutionLab(self.root).run()
        result_hash = canonical_sha256(result.model_dump(mode="json"))
        payload = {
            "case_id": case.case_id,
            "track": self.track,
            "status": SupervisorCaseStatus.COMPLETED,
            "reason": (
                "Actual Skill attribution entered the existing evidence-gated "
                "candidate, frozen evaluation, approval and promotion lifecycle."
            ),
            "executor_id": self.executor_id,
            "child_run_id": result.run_id,
            "artifact_refs": (
                f"skill:{result.skill_id}@{result.active_version}",
                f"campaign:{result.campaign_id}",
            ),
            "artifact_hashes": {
                "skill_result": result_hash,
                "skill_checkpoint": canonical_sha256(result.skill_checkpoint),
                "campaign_checkpoint": canonical_sha256(result.campaign_checkpoint),
                "trace_checkpoint": canonical_sha256(result.trace_checkpoint),
            },
            "metrics": {
                "initial_score": result.summary.initial_score,
                "final_score": result.summary.final_score,
                "evolution_gain": result.summary.evolution_gain,
                "regression_count": float(result.regression_count),
            },
            "completed_at": self.COMPLETED_AT,
            "skill_promoted": True,
            "model_candidate_evaluated": False,
            "model_candidate_activated": False,
            "model_rollback_verified": False,
            "training_executed_by_evoagent": False,
            "external_execution_performed": False,
        }
        return SupervisorOutcome(
            **payload,
            outcome_hash=canonical_sha256(payload),
        )


class _ModelEvolutionExecutor:
    executor_id = "governed-model-lifecycle-executor-v1"
    track = SupervisorTrack.MODEL
    idempotent = True
    COMPLETED_AT = datetime(2026, 8, 11, 6, 20, tzinfo=timezone.utc)

    def __init__(
        self,
        root: Path,
        *,
        source_commit: str,
        source_repository: str,
    ):
        self.root = root
        self.source_commit = source_commit
        self.source_repository = source_repository

    def execute(self, case: SupervisorCase) -> SupervisorOutcome:
        result = ModelCandidateAdmissionLab(
            self.root,
            source_commit=self.source_commit,
            source_repository=self.source_repository,
        ).run()
        result_hash = canonical_sha256(result.model_dump(mode="json"))
        payload = {
            "case_id": case.case_id,
            "track": self.track,
            "status": SupervisorCaseStatus.COMPLETED,
            "reason": (
                "Actual Model attribution entered the governed evidence package, "
                "external candidate admission, independent evaluation, approval, "
                "explicit activation and rollback-verification lifecycle."
            ),
            "executor_id": self.executor_id,
            "child_run_id": result.run_id,
            "artifact_refs": (
                f"model-candidate:{result.candidate_id}",
                f"campaign:{result.activation_campaign_id}",
                f"model-admission-package:{result.package_hash}",
            ),
            "artifact_hashes": {
                "model_result": result_hash,
                "training_intent_package": result.training_intent_package_hash,
                "model_admission_package": result.package_hash,
            },
            "metrics": {
                "held_out_base_score": result.held_out_base_score,
                "held_out_candidate_score": result.held_out_candidate_score,
                "held_out_improvement": result.held_out_improvement,
                "replay_candidate_score": result.replay_candidate_score,
                "retention_candidate_score": result.retention_candidate_score,
                "safety_candidate_score": result.safety_candidate_score,
                "regression_count": float(result.regression_count),
                "forgetting_rate": result.forgetting_rate,
            },
            "completed_at": self.COMPLETED_AT,
            "skill_promoted": False,
            "model_candidate_evaluated": True,
            "model_candidate_activated": True,
            "model_rollback_verified": True,
            "training_executed_by_evoagent": False,
            "external_execution_performed": False,
        }
        return SupervisorOutcome(
            **payload,
            outcome_hash=canonical_sha256(payload),
        )


class ClosedLoopEvolutionSupervisorLab:
    """Causal Skill/Model routing plus a safe Environment escalation."""

    RUN_ID = "closed-loop-evolution-supervisor-v1"
    CASE_CREATED_AT = datetime(2026, 8, 11, 6, 0, tzinfo=timezone.utc)

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
            raise ValueError("Closed-loop Supervisor lab root must not be a symlink.")
        if len(source_commit) != 40 or any(
            character not in "0123456789abcdef" for character in source_commit
        ):
            raise ValueError("source_commit must be lowercase 40-character Git hex.")
        self.root = raw_root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.source_commit = source_commit
        self.source_repository = source_repository

    @property
    def supervisor_database(self) -> Path:
        return self.root / "supervisor.db"

    @property
    def package_path(self) -> Path:
        return self.root / "closed-loop-evolution-package.json"

    @property
    def cross_layer_root(self) -> Path:
        return self.root / "cross-layer"

    @property
    def skill_root(self) -> Path:
        return self.root / "skill-track"

    @property
    def model_root(self) -> Path:
        return self.root / "model-track"

    @property
    def policy(self) -> SupervisorPolicy:
        return SupervisorPolicy(
            policy_id="closed-loop-supervisor-v1",
            budget=SupervisorBudget(
                max_cases=3,
                max_rounds=3,
                max_skill_executions=1,
                max_model_executions=1,
                max_external_repair_tickets=0,
            ),
            automatic_skill=True,
            automatic_model=True,
            automatic_external_repair=False,
            stop_on_quarantine=True,
        )

    def run(self) -> ClosedLoopEvolutionLabResult:
        matrix = ExecutableCrossLayerAttributionLab(self.cross_layer_root).run()
        matrix_hash = canonical_sha256(matrix.model_dump(mode="json"))
        cases = self._cases(matrix.results)
        repository = SQLiteSupervisorRepository(self.supervisor_database)
        existed = self.package_path.exists()
        supervisor = PersistentEvolutionSupervisor(
            repository=repository,
            run_id=self.RUN_ID,
            policy=self.policy,
            executors={
                SupervisorTrack.SKILL: _SkillEvolutionExecutor(self.skill_root),
                SupervisorTrack.MODEL: _ModelEvolutionExecutor(
                    self.model_root,
                    source_commit=self.source_commit,
                    source_repository=self.source_repository,
                ),
            },
            actor_id="closed-loop-supervisor-lab",
        )
        run = supervisor.process(cases)
        records = tuple(repository.list_cases(self.RUN_ID))
        if run.status != SupervisorRunStatus.COMPLETED_WITH_ESCALATIONS:
            raise RuntimeError(
                f"Controlled mixed run reached {run.status.value}, expected completed_with_escalations."
            )
        self._validate_records(records, matrix_hash)
        score_summary = self._score_summary(records)
        checkpoint = repository.checkpoint()
        events = tuple(repository.events(self.RUN_ID))

        manager = ClosedLoopEvolutionPackageManager()
        if not existed:
            package = manager.build(
                run_id=self.RUN_ID,
                created_at=datetime.now(timezone.utc),
                framework_version=__version__,
                source_repository=self.source_repository,
                source_commit=self.source_commit,
                third_party_lock_hash=DEFAULT_THIRD_PARTY_LOCK_HASH,
                policy=self.policy,
                run=run,
                cases=records,
                events=events,
                checkpoint=checkpoint,
                score_summary=score_summary,
            )
            manager.export_file(package, self.package_path)
        else:
            package = manager.load_file(self.package_path)
            if (
                package.run != run
                or package.cases != records
                or package.events != events
                or package.checkpoint != checkpoint
                or package.score_summary != score_summary
            ):
                raise RuntimeError(
                    "Read-only Supervisor resume differs from the persisted package."
                )
            self._verify_child_resume(records)

        self._verify_restart(package)
        return self._result(package, resumed=existed)

    def _cases(
        self,
        results: tuple[ExecutableLayerDispatchResult, ...],
    ) -> tuple[SupervisorCase, ...]:
        by_layer = {
            item.attribution.root_cause_layer: item for item in results
        }
        selected = (
            by_layer[FailureLayer.SKILL],
            by_layer[FailureLayer.MODEL],
            by_layer[FailureLayer.ENVIRONMENT],
        )
        return tuple(self._case(item) for item in selected)

    def _case(self, item: ExecutableLayerDispatchResult) -> SupervisorCase:
        attribution_hash = canonical_sha256(item.attribution.model_dump(mode="json"))
        evidence_hash = canonical_sha256(
            {
                "scenario_id": item.scenario_id,
                "baseline_trace_id": item.baseline_trace_id,
                "baseline_feedback": item.baseline_feedback,
                "supported_experiments": item.supported_experiments,
                "counterfactual_trace_ids": item.counterfactual_trace_ids,
                "decision": item.decision,
            }
        )
        return build_supervisor_case(
            case_id=f"supervisor-case:{item.attribution.root_cause_layer.value}",
            trace_id=item.baseline_trace_id,
            task_id=item.scenario_id,
            failure_layer=item.attribution.root_cause_layer,
            action=item.decision.action,
            attribution_hash=attribution_hash,
            evidence_hash=evidence_hash,
            source="synthetic-executable-cross-layer-matrix",
            trust_level="verified",
            created_at=self.CASE_CREATED_AT,
        )

    @staticmethod
    def _validate_records(
        records: tuple[SupervisorCaseRecord, ...],
        matrix_hash: str,
    ) -> None:
        if len(records) != 3:
            raise RuntimeError("Controlled closed-loop run must persist exactly three cases.")
        by_track = {record.track: record for record in records}
        if set(by_track) != {
            SupervisorTrack.SKILL,
            SupervisorTrack.MODEL,
            SupervisorTrack.ESCALATION,
        }:
            raise RuntimeError("Controlled closed-loop run routed unexpected tracks.")
        skill = by_track[SupervisorTrack.SKILL]
        model = by_track[SupervisorTrack.MODEL]
        escalation = by_track[SupervisorTrack.ESCALATION]
        if skill.status != SupervisorCaseStatus.COMPLETED or not skill.outcome.skill_promoted:
            raise RuntimeError("Skill Supervisor case did not complete governed promotion.")
        if (
            model.status != SupervisorCaseStatus.COMPLETED
            or not model.outcome.model_candidate_evaluated
            or not model.outcome.model_candidate_activated
            or not model.outcome.model_rollback_verified
        ):
            raise RuntimeError("Model Supervisor case did not complete the governed lifecycle.")
        if escalation.status != SupervisorCaseStatus.ESCALATED:
            raise RuntimeError("Environment Supervisor case was not escalated safely.")
        if any(
            record.outcome.training_executed_by_evoagent
            or record.outcome.external_execution_performed
            for record in records
        ):
            raise RuntimeError("Controlled Supervisor run performed prohibited execution.")
        # The selected case evidence is derived from the complete repeated matrix.
        if not matrix_hash or len(matrix_hash) != 64:
            raise RuntimeError("Cross-layer matrix result hash is invalid.")

    @staticmethod
    def _score_summary(
        records: tuple[SupervisorCaseRecord, ...],
    ) -> SupervisorScoreSummary:
        skill = next(item for item in records if item.track == SupervisorTrack.SKILL)
        model = next(item for item in records if item.track == SupervisorTrack.MODEL)
        skill_initial = skill.outcome.metrics["initial_score"]
        skill_final = skill.outcome.metrics["final_score"]
        model_initial = model.outcome.metrics["held_out_base_score"]
        model_final = model.outcome.metrics["held_out_candidate_score"]
        return SupervisorScoreSummary(
            skill_initial_score=skill_initial,
            skill_final_score=skill_final,
            model_initial_score=model_initial,
            model_final_score=model_final,
            composite_initial_score=(skill_initial + model_initial) / 2.0,
            composite_final_score=(skill_final + model_final) / 2.0,
            composite_gain=(skill_final + model_final - skill_initial - model_initial) / 2.0,
            escalation_count=sum(
                item.status == SupervisorCaseStatus.ESCALATED for item in records
            ),
        )

    def _verify_child_resume(
        self,
        records: tuple[SupervisorCaseRecord, ...],
    ) -> None:
        skill = AutomaticLocalToolEvolutionLab(self.skill_root).run()
        model = ModelCandidateAdmissionLab(
            self.model_root,
            source_commit=self.source_commit,
            source_repository=self.source_repository,
        ).run()
        if not skill.resumed or not model.resumed:
            raise RuntimeError("Child governed lifecycle did not report read-only resume.")
        skill_record = next(item for item in records if item.track == SupervisorTrack.SKILL)
        model_record = next(item for item in records if item.track == SupervisorTrack.MODEL)
        if skill_record.outcome.artifact_hashes["skill_result"] != canonical_sha256(
            skill.model_dump(mode="json")
        ):
            raise RuntimeError("Resumed Skill result differs from Supervisor evidence.")
        if model_record.outcome.artifact_hashes["model_result"] != canonical_sha256(
            model.model_dump(mode="json")
        ):
            raise RuntimeError("Resumed Model result differs from Supervisor evidence.")
        if model_record.outcome.artifact_hashes["model_admission_package"] != model.package_hash:
            raise RuntimeError("Resumed Model package differs from Supervisor evidence.")

    def _verify_restart(self, package) -> None:
        repository = SQLiteSupervisorRepository(self.supervisor_database)
        repository.verify_audit(package.checkpoint)
        repository.verify_state(self.RUN_ID)
        if repository.get_run(self.RUN_ID) != package.run:
            raise RuntimeError("Restarted Supervisor run differs from the package.")
        if tuple(repository.list_cases(self.RUN_ID)) != package.cases:
            raise RuntimeError("Restarted Supervisor cases differ from the package.")
        if tuple(repository.events(self.RUN_ID)) != package.events:
            raise RuntimeError("Restarted Supervisor events differ from the package.")
        loaded = ClosedLoopEvolutionPackageManager().load_file(self.package_path)
        if loaded != package:
            raise RuntimeError("Reloaded closed-loop package differs.")

    def _result(self, package, *, resumed: bool) -> ClosedLoopEvolutionLabResult:
        skill = next(item for item in package.cases if item.track == SupervisorTrack.SKILL)
        model = next(item for item in package.cases if item.track == SupervisorTrack.MODEL)
        return ClosedLoopEvolutionLabResult(
            run_id=package.run_id,
            resumed=resumed,
            run_status=package.run.status.value,
            case_ids=tuple(item.case.case_id for item in package.cases),
            tracks=tuple(item.track.value for item in package.cases),
            case_statuses=tuple(item.status.value for item in package.cases),
            skill_child_run_id=skill.outcome.child_run_id,
            model_child_run_id=model.outcome.child_run_id,
            skill_result_hash=skill.outcome.artifact_hashes["skill_result"],
            model_result_hash=model.outcome.artifact_hashes["model_result"],
            training_intent_package_hash=model.outcome.artifact_hashes[
                "training_intent_package"
            ],
            model_admission_package_hash=model.outcome.artifact_hashes[
                "model_admission_package"
            ],
            skill_initial_score=package.score_summary.skill_initial_score,
            skill_final_score=package.score_summary.skill_final_score,
            model_initial_score=package.score_summary.model_initial_score,
            model_final_score=package.score_summary.model_final_score,
            composite_initial_score=package.score_summary.composite_initial_score,
            composite_final_score=package.score_summary.composite_final_score,
            composite_gain=package.score_summary.composite_gain,
            escalation_count=package.score_summary.escalation_count,
            supervisor_case_count=len(package.cases),
            supervisor_event_count=len(package.events),
            supervisor_checkpoint=package.checkpoint.model_dump(mode="json"),
            package_path=str(self.package_path),
            package_hash=package.package_hash,
        )


__all__ = [
    "ClosedLoopEvolutionLabResult",
    "ClosedLoopEvolutionSupervisorLab",
]
