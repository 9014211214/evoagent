from __future__ import annotations

from evoagent.campaigns.governance import CampaignGovernanceService
from evoagent.campaigns.models import CampaignRisk, CampaignState, CampaignType
from evoagent.campaigns.repository import CampaignConflictError, CampaignCooldownError
from evoagent.cycles.models import CycleStatus, EvolutionCycleRequest, EvolutionCycleResult
from evoagent.cycles.service import EvolutionCycleService
from evoagent.cycles.skill_backend import SkillPatchUnavailable
from evoagent.domain.models import EvolutionAction, EvolutionTicket
from evoagent.skills.models import SkillPatch, SkillSpec
from evoagent.training.models import ModelCandidate, ModelImprovementTicket
from evoagent.training.orchestrator import ModelEvolutionBackend


class GovernedEvolutionCycleResult(EvolutionCycleResult):
    campaign_id: str | None = None
    campaign_state: str | None = None
    reused: bool = False


class GovernedEvolutionCycleService(EvolutionCycleService):
    """Optional persistent Campaign layer around the v0.7 cycle service."""

    def __init__(self, *, campaign_governance: CampaignGovernanceService, **kwargs):
        super().__init__(**kwargs)
        self.campaign_governance = campaign_governance

    def process(
        self,
        request: EvolutionCycleRequest,
        *,
        counterfactual_runner=None,
        model_backend: ModelEvolutionBackend | None = None,
    ) -> GovernedEvolutionCycleResult:
        result = super().process(
            request,
            counterfactual_runner=counterfactual_runner,
            model_backend=model_backend,
        )
        if (
            result.status == CycleStatus.TICKET_CREATED
            and result.evolution_ticket is not None
            and result.model_ticket is None
        ):
            return self._campaignize_external_ticket(request, result)
        if isinstance(result, GovernedEvolutionCycleResult):
            return result
        return GovernedEvolutionCycleResult(**result.model_dump())

    def _handle_skill(self, request, record_hash, report, decision):
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

        try:
            reservation = self.campaign_governance.reserve(
                campaign_type=CampaignType.SKILL,
                target_key=f"skill:{trace.skill_id}@{active.spec.version}",
                fingerprint_source={
                    "parent_version": active.spec.version,
                    "patch": patch.model_dump(mode="json", exclude={"evidence_trace_ids"}),
                },
                risk=CampaignRisk.LOW,
                generated_by="evoagent-cycle:skill",
                metadata={"trace_id": trace.trace_id},
            )
        except (CampaignConflictError, CampaignCooldownError) as exc:
            return self._result(
                request,
                record_hash,
                CycleStatus.ESCALATED,
                str(exc),
                attribution=report,
                decision=decision,
            )

        campaign = reservation.campaign
        if reservation.reused:
            candidate, stored_patch = self._skill_artifact(campaign.artifact_payload)
            return self._result(
                request,
                record_hash,
                CycleStatus.SKILL_CANDIDATE,
                "Existing open Skill Campaign reused; no duplicate candidate was created.",
                attribution=report,
                decision=decision,
                skill_candidate=candidate,
                skill_patch=stored_patch,
                campaign_id=campaign.campaign_id,
                campaign_state=campaign.state.value,
                reused=True,
            )

        candidate_version = self._next_skill_version(trace.skill_id)
        candidate = self.skill_builder.propose(
            active.spec,
            patch,
            new_version=candidate_version,
        )
        campaign = self.campaign_governance.attach_candidate(
            campaign,
            candidate_ref=f"skill:{candidate.skill_id}@{candidate.version}",
            artifact_payload={
                "kind": "skill_candidate",
                "candidate": candidate.model_dump(mode="json"),
                "patch": patch.model_dump(mode="json"),
            },
        )
        try:
            self.skill_registry.add_candidate(
                candidate,
                parent_version=active.spec.version,
                reason=f"Verified Skill attribution from trace {trace.trace_id}.",
            )
        except ValueError:
            existing = self.skill_registry.get(candidate.skill_id, candidate.version)
            if existing.spec != candidate:
                raise

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
            campaign_id=campaign.campaign_id,
            campaign_state=campaign.state.value,
        )

    def _handle_model(
        self,
        request,
        record_hash,
        report,
        decision,
        *,
        model_backend: ModelEvolutionBackend | None,
    ):
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

        try:
            reservation = self.campaign_governance.reserve(
                campaign_type=CampaignType.MODEL,
                target_key=f"model:{request.trace.model_id}:{problem_cluster}",
                fingerprint_source={
                    "base_model_id": request.trace.model_id,
                    "problem_cluster": problem_cluster,
                    "settings": settings.model_dump(mode="json"),
                },
                risk=CampaignRisk.HIGH,
                generated_by="evoagent-cycle:model",
                metadata={"evidence_trace_ids": list(evidence.trace_ids)},
            )
        except (CampaignConflictError, CampaignCooldownError) as exc:
            return self._result(
                request,
                record_hash,
                CycleStatus.ESCALATED,
                str(exc),
                attribution=report,
                decision=decision,
                model_evidence=evidence,
            )

        campaign = reservation.campaign
        if reservation.reused:
            model_ticket, model_candidate = self._model_artifact(campaign.artifact_payload)
            return self._result(
                request,
                record_hash,
                CycleStatus.MODEL_CANDIDATE if model_candidate else CycleStatus.TICKET_CREATED,
                "Existing open model Campaign reused; no duplicate training candidate was created.",
                attribution=report,
                decision=decision,
                model_evidence=evidence,
                model_ticket=model_ticket,
                model_candidate=model_candidate,
                campaign_id=campaign.campaign_id,
                campaign_state=campaign.state.value,
                reused=True,
            )

        try:
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
                campaign = self.campaign_governance.attach_candidate(
                    campaign,
                    candidate_ref=f"model-ticket:{model_ticket.ticket_id}",
                    artifact_payload={
                        "kind": "model_ticket",
                        "ticket": model_ticket.model_dump(mode="json"),
                    },
                )
                return self._result(
                    request,
                    record_hash,
                    CycleStatus.TICKET_CREATED,
                    "Model improvement ticket created; no training backend was authorized.",
                    attribution=report,
                    decision=decision,
                    model_evidence=evidence,
                    model_ticket=model_ticket,
                    campaign_id=campaign.campaign_id,
                    campaign_state=campaign.state.value,
                )
            candidate = self.model_orchestrator.run(model_ticket, model_backend)
        except ValueError as exc:
            current = self.campaign_governance.repository.get(campaign.campaign_id)
            if current.state in {CampaignState.OPEN, CampaignState.EVIDENCE_ACCUMULATING}:
                current = self.campaign_governance.repository.transition(
                    current.campaign_id,
                    to_state=CampaignState.CANCELLED,
                    expected_revision=current.revision,
                    actor_id="evoagent-cycle:model",
                    reason=f"Model planning failed: {exc}",
                )
            return self._result(
                request,
                record_hash,
                CycleStatus.ESCALATED,
                f"Model planning failed: {exc}",
                attribution=report,
                decision=decision,
                model_evidence=evidence,
                campaign_id=current.campaign_id,
                campaign_state=current.state.value,
            )

        campaign = self.campaign_governance.attach_candidate(
            campaign,
            candidate_ref=candidate.artifact_uri,
            artifact_payload={
                "kind": "model_candidate",
                "ticket": model_ticket.model_dump(mode="json"),
                "candidate": candidate.model_dump(mode="json"),
            },
        )
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
            campaign_id=campaign.campaign_id,
            campaign_state=campaign.state.value,
        )

    def _campaignize_external_ticket(
        self,
        request: EvolutionCycleRequest,
        result: EvolutionCycleResult,
    ) -> GovernedEvolutionCycleResult:
        ticket = result.evolution_ticket
        campaign_type = self._campaign_type(result.decision.action)
        target_key = f"{campaign_type.value}:{ticket.target_id or request.trace.task.task_type}"
        try:
            reservation = self.campaign_governance.reserve(
                campaign_type=campaign_type,
                target_key=target_key,
                fingerprint_source={
                    "target_layer": ticket.target_layer.value,
                    "target_id": ticket.target_id,
                    "proposed_action": ticket.proposed_action.value,
                },
                risk=CampaignRisk.MEDIUM,
                generated_by="evoagent-cycle:ticket",
                metadata={"trace_id": request.trace.trace_id},
            )
        except (CampaignConflictError, CampaignCooldownError) as exc:
            return GovernedEvolutionCycleResult(
                **result.model_dump(exclude={"status", "reason"}),
                status=CycleStatus.ESCALATED,
                reason=str(exc),
            )
        campaign = reservation.campaign
        stored_ticket = ticket
        if reservation.reused:
            stored_ticket = self._external_ticket_artifact(campaign.artifact_payload) or ticket
        else:
            campaign = self.campaign_governance.attach_candidate(
                campaign,
                candidate_ref=f"evolution-ticket:{ticket.ticket_id}",
                artifact_payload={
                    "kind": "evolution_ticket",
                    "ticket": ticket.model_dump(mode="json"),
                },
            )
        return GovernedEvolutionCycleResult(
            **result.model_dump(
                exclude={
                    "evolution_ticket",
                    "reason",
                    "campaign_id",
                    "campaign_state",
                    "reused",
                }
            ),
            evolution_ticket=stored_ticket,
            reason=(
                "Existing external-repair Campaign reused; no duplicate ticket was created."
                if reservation.reused
                else result.reason
            ),
            campaign_id=campaign.campaign_id,
            campaign_state=campaign.state.value,
            reused=reservation.reused,
        )

    @staticmethod
    def _skill_artifact(payload):
        if not payload or payload.get("kind") != "skill_candidate":
            return None, None
        return (
            SkillSpec.model_validate(payload["candidate"]),
            SkillPatch.model_validate(payload["patch"]),
        )

    @staticmethod
    def _model_artifact(payload):
        if not payload:
            return None, None
        ticket = (
            ModelImprovementTicket.model_validate(payload["ticket"])
            if payload.get("ticket")
            else None
        )
        candidate = (
            ModelCandidate.model_validate(payload["candidate"])
            if payload.get("kind") == "model_candidate" and payload.get("candidate")
            else None
        )
        return ticket, candidate

    @staticmethod
    def _external_ticket_artifact(payload):
        if not payload or payload.get("kind") != "evolution_ticket":
            return None
        return EvolutionTicket.model_validate(payload["ticket"])

    @staticmethod
    def _campaign_type(action: EvolutionAction) -> CampaignType:
        mapping = {
            EvolutionAction.UPDATE_ROUTER: CampaignType.ROUTER,
            EvolutionAction.REPAIR_TOOL: CampaignType.TOOL,
            EvolutionAction.UPDATE_CONTEXT: CampaignType.CONTEXT,
            EvolutionAction.REPAIR_VERIFIER: CampaignType.VERIFIER,
        }
        return mapping[action]

    @staticmethod
    def _result(
        request: EvolutionCycleRequest,
        record_hash: str,
        status: CycleStatus,
        reason: str,
        **kwargs,
    ) -> GovernedEvolutionCycleResult:
        return GovernedEvolutionCycleResult(
            status=status,
            trace_id=request.trace.trace_id,
            trace_record_hash=record_hash,
            reason=reason,
            **kwargs,
        )
