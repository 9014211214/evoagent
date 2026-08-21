from __future__ import annotations

from packaging.version import Version

from evoagent.cycles.badcases import BadCaseDetector, BadCaseDisposition
from evoagent.cycles.model_evidence import ModelEvidenceAccumulator
from evoagent.cycles.models import (
    CycleStatus,
    EvolutionCyclePolicy,
    EvolutionCycleRequest,
    EvolutionCycleResult,
)
from evoagent.cycles.skill_backend import SkillEvolutionBackend, SkillPatchUnavailable
from evoagent.diagnosis.counterfactual import CounterfactualRunner
from evoagent.diagnosis.counterfactual_engine import CounterfactualAttributionEngine
from evoagent.domain.models import EvolutionAction
from evoagent.evolution.controller import EvolutionController
from evoagent.skills.builder import SkillCandidateBuilder
from evoagent.skills.registry import SkillRegistry
from evoagent.traces.store import JsonlTraceStore
from evoagent.training.orchestrator import ModelEvolutionBackend, ModelEvolutionOrchestrator
from evoagent.training.ticket import ModelTicketFactory


class EvolutionCycleService:
    """Route one observable execution through a bounded evolution cycle.

    The service may create candidate artifacts and tickets. It never promotes a
    Skill, executes training by itself, or deploys a model candidate.
    """

    def __init__(
        self,
        *,
        trace_store: JsonlTraceStore,
        skill_registry: SkillRegistry,
        skill_backend: SkillEvolutionBackend,
        policy: EvolutionCyclePolicy | None = None,
        attributor: CounterfactualAttributionEngine | None = None,
        controller: EvolutionController | None = None,
        evidence_accumulator: ModelEvidenceAccumulator | None = None,
        model_ticket_factory: ModelTicketFactory | None = None,
        model_orchestrator: ModelEvolutionOrchestrator | None = None,
    ):
        self.trace_store = trace_store
        self.skill_registry = skill_registry
        self.skill_backend = skill_backend
        self.policy = policy or EvolutionCyclePolicy()
        self.attributor = attributor or CounterfactualAttributionEngine()
        self.controller = controller or EvolutionController()
        self.detector = BadCaseDetector()
        self.evidence_accumulator = evidence_accumulator or ModelEvidenceAccumulator()
        self.model_ticket_factory = model_ticket_factory or ModelTicketFactory()
        self.model_orchestrator = model_orchestrator or ModelEvolutionOrchestrator()
        self.skill_builder = SkillCandidateBuilder()

    def process(
        self,
        request: EvolutionCycleRequest,
        *,
        counterfactual_runner: CounterfactualRunner | None = None,
        model_backend: ModelEvolutionBackend | None = None,
    ) -> EvolutionCycleResult:
        envelope = self.trace_store.append(
            request.trace,
            source=request.source,
            trust_level=request.trust_level,
            safety_flags=request.safety_flags,
        )
        badcase = self.detector.detect(
            request.trace,
            trust_level=request.trust_level,
            safety_flags=request.safety_flags,
            policy=self.policy,
        )

        if badcase.disposition == BadCaseDisposition.SUCCESS:
            return self._result(
                request,
                envelope.record_hash,
                CycleStatus.NO_ACTION,
                badcase.reason,
            )
        if badcase.disposition == BadCaseDisposition.QUARANTINE:
            return self._result(
                request,
                envelope.record_hash,
                CycleStatus.QUARANTINED,
                badcase.reason,
            )
        if counterfactual_runner is None:
            return self._result(
                request,
                envelope.record_hash,
                CycleStatus.ESCALATED,
                "Failed trace requires a counterfactual runner before automatic evolution.",
            )

        report = self.attributor.diagnose(counterfactual_runner)
        decision = self.controller.decide_attribution(report)
        if not report.actionable or decision.action == EvolutionAction.ESCALATE:
            return self._result(
                request,
                envelope.record_hash,
                CycleStatus.ESCALATED,
                report.reason,
                attribution=report,
                decision=decision,
            )

        if decision.action == EvolutionAction.UPDATE_SKILL:
            return self._handle_skill(
                request,
                envelope.record_hash,
                report,
                decision,
            )
        if decision.action == EvolutionAction.TRAIN_MODEL:
            return self._handle_model(
                request,
                envelope.record_hash,
                report,
                decision,
                model_backend=model_backend,
            )

        ticket = self.controller.create_ticket(
            report,
            ticket_id=f"ticket:{request.trace.trace_id}",
            target_id=self._target_id(request, decision.action),
            evidence_trace_ids=[request.trace.trace_id],
        )
        return self._result(
            request,
            envelope.record_hash,
            CycleStatus.TICKET_CREATED,
            "Verified failure was routed to an intervention ticket; no repair was executed.",
            attribution=report,
            decision=decision,
            evolution_ticket=ticket,
        )

    def _handle_skill(self, request, record_hash, report, decision) -> EvolutionCycleResult:
        trace = request.trace
        if not trace.skill_id or not trace.skill_version:
            return self._result(
                request,
                record_hash,
                CycleStatus.ESCALATED,
                "Skill attribution requires the executed Skill ID and version.",
                attribution=report,
                decision=decision,
            )
        try:
            active = self.skill_registry.active(trace.skill_id)
        except KeyError:
            return self._result(
                request,
                record_hash,
                CycleStatus.ESCALATED,
                "Executed Skill is not registered in the lifecycle registry.",
                attribution=report,
                decision=decision,
            )
        if active.spec.version != trace.skill_version:
            return self._result(
                request,
                record_hash,
                CycleStatus.ESCALATED,
                "Trace references a stale Skill version; reproduce against the active version first.",
                attribution=report,
                decision=decision,
            )
        try:
            patch = self.skill_backend.propose(report, trace, active.spec)
        except SkillPatchUnavailable as exc:
            return self._result(
                request,
                record_hash,
                CycleStatus.ESCALATED,
                str(exc),
                attribution=report,
                decision=decision,
            )

        candidate_version = self._next_skill_version(trace.skill_id)
        candidate = self.skill_builder.propose(
            active.spec,
            patch,
            new_version=candidate_version,
        )
        self.skill_registry.add_candidate(
            candidate,
            parent_version=active.spec.version,
            reason=f"Verified Skill attribution from trace {trace.trace_id}.",
        )
        ticket = self.controller.create_ticket(
            report,
            ticket_id=f"ticket:{trace.trace_id}",
            target_id=f"{trace.skill_id}@{candidate.version}",
            evidence_trace_ids=[trace.trace_id],
        )
        return self._result(
            request,
            record_hash,
            CycleStatus.SKILL_CANDIDATE,
            "Immutable Skill candidate created; active Skill was not changed.",
            attribution=report,
            decision=decision,
            evolution_ticket=ticket,
            skill_candidate=candidate,
            skill_patch=patch,
        )

    def _handle_model(
        self,
        request,
        record_hash,
        report,
        decision,
        *,
        model_backend: ModelEvolutionBackend | None,
    ) -> EvolutionCycleResult:
        settings = request.model_settings
        problem_cluster = settings.problem_cluster if settings else request.trace.task.task_type
        evidence = self.evidence_accumulator.add(
            request.trace,
            problem_cluster=problem_cluster,
            trust_level=request.trust_level,
            policy=self.policy,
        )
        if not evidence.ready:
            return self._result(
                request,
                record_hash,
                CycleStatus.MODEL_EVIDENCE_ACCUMULATED,
                (
                    f"Model evidence accumulated: {len(evidence.trace_ids)}/"
                    f"{self.policy.model_min_traces} traces and "
                    f"{len(evidence.task_ids)}/{self.policy.model_min_distinct_tasks} distinct tasks."
                ),
                attribution=report,
                decision=decision,
                model_evidence=evidence,
            )
        if settings is None:
            return self._result(
                request,
                record_hash,
                CycleStatus.ESCALATED,
                "Model evidence threshold was reached, but no bounded model-evolution settings were supplied.",
                attribution=report,
                decision=decision,
                model_evidence=evidence,
            )

        model_ticket = self.model_ticket_factory.create(
            report,
            ticket_id=f"model-ticket:{problem_cluster}:{len(evidence.trace_ids)}",
            base_model_id=request.trace.model_id,
            problem_cluster=problem_cluster,
            evidence_trace_ids=evidence.trace_ids,
            target_metrics=settings.target_metrics,
            dataset_signals=settings.dataset_signals,
            allowed_methods=settings.allowed_methods,
            budget=settings.budget,
            replay_environment=settings.replay_environment,
            safety_constraints=settings.safety_constraints,
            regression_suite=settings.regression_suite,
            evidence_dataset_uri=settings.evidence_dataset_uri,
            evidence_manifest_hash=settings.evidence_manifest_hash,
            held_out_task_ids=settings.held_out_task_ids,
        )
        if model_backend is None:
            return self._result(
                request,
                record_hash,
                CycleStatus.TICKET_CREATED,
                "Model improvement ticket created; no training backend was authorized.",
                attribution=report,
                decision=decision,
                model_evidence=evidence,
                model_ticket=model_ticket,
            )
        candidate = self.model_orchestrator.run(model_ticket, model_backend)
        return self._result(
            request,
            record_hash,
            CycleStatus.MODEL_CANDIDATE,
            "Dry-run model candidate created; it remains unevaluated and undeployed.",
            attribution=report,
            decision=decision,
            model_evidence=evidence,
            model_ticket=model_ticket,
            model_candidate=candidate,
        )

    def _next_skill_version(self, skill_id: str) -> str:
        versions = [Version(record.spec.version) for record in self.skill_registry.list_versions(skill_id)]
        latest = max(versions)
        return f"{latest.major}.{latest.minor + 1}.0"

    @staticmethod
    def _target_id(request: EvolutionCycleRequest, action: EvolutionAction) -> str | None:
        if action in {EvolutionAction.UPDATE_ROUTER, EvolutionAction.REPAIR_TOOL}:
            return request.trace.task.task_type
        if action == EvolutionAction.UPDATE_CONTEXT:
            return f"context:{request.trace.task.task_type}"
        if action == EvolutionAction.REPAIR_VERIFIER:
            return f"verifier:{request.trace.task.task_type}"
        return None

    @staticmethod
    def _result(
        request: EvolutionCycleRequest,
        record_hash: str,
        status: CycleStatus,
        reason: str,
        **kwargs,
    ) -> EvolutionCycleResult:
        return EvolutionCycleResult(
            status=status,
            trace_id=request.trace.trace_id,
            trace_record_hash=record_hash,
            reason=reason,
            **kwargs,
        )
