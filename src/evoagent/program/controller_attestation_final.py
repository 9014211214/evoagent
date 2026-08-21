from __future__ import annotations

from datetime import datetime

from evoagent.campaigns import ApprovalDecision, CampaignState
from evoagent.program.controller_public_contract_final import (
    RetryHardenedEvolutionProgramController as _PublicController,
)
from evoagent.program.execution_attestation import (
    ProgramExecutionCheckpoint,
    RunningGenerationAttestation,
    RunningGenerationRoles,
    build_running_generation_attestation,
)
from evoagent.program.models import (
    GenerationStatus,
    ProgramAction,
    ProgramEventType,
    ProgramState,
)


class RetryHardenedEvolutionProgramController(_PublicController):
    """Final public Controller with persistent running-generation attestation."""

    def attest_running_generation(
        self,
        *,
        program_id: str,
        generation_id: str,
        attested_by: str,
        attested_at: datetime,
        attestation_id: str | None = None,
    ) -> RunningGenerationAttestation:
        program_checkpoint = self.repository.checkpoint()
        campaign_repository = self.campaign_governance.repository
        campaign_checkpoint = campaign_repository.checkpoint()
        if self.repository.verify_audit(program_checkpoint) is not True:
            raise RuntimeError("Program audit verification did not pass.")
        if campaign_repository.verify_audit(campaign_checkpoint) is not True:
            raise RuntimeError("Generation Campaign audit verification did not pass.")
        if self.repository.verify_state(program_id) is not True:
            raise RuntimeError("Program Registry state verification did not pass.")

        generation = self.repository.get_generation(program_id, generation_id)
        head = self.repository.head(program_id)
        if (
            generation.status != GenerationStatus.RUNNING
            or generation.plan is None
            or generation.campaign_id is None
            or generation.outcome is not None
            or head.state != ProgramState.GENERATION_RUNNING
            or head.active_generation_id != generation_id
            or head.current_generation_index != generation.generation_index
        ):
            raise ValueError(
                "Running Generation attestation requires the exact active Registry state."
            )
        plan = generation.plan
        signals, attributions = self._generation_evidence_set(
            program_id,
            generation.generation_index - 1,
        )
        if (
            len(signals) != 1
            or len(attributions) != 1
            or signals[0].signal_id != plan.source_signal_id
            or signals[0].signal_hash != plan.source_signal_hash
            or attributions[0].receipt_id != plan.attribution_receipt_id
            or attributions[0].receipt_hash != plan.attribution_receipt_hash
        ):
            raise ValueError(
                "Running Generation does not bind one exact persisted evidence set."
            )
        signal = signals[0]
        attribution = attributions[0]
        decision = self._continue_decision(plan)
        if (
            decision.action != ProgramAction.CONTINUE
            or decision.decided_by != plan.created_by
        ):
            raise ValueError(
                "Running Generation plan differs from its CONTINUE decision."
            )

        campaign = campaign_repository.get(generation.campaign_id)
        if campaign.state != CampaignState.AUTHORIZED:
            raise ValueError(
                "Running Generation requires an AUTHORIZED Generation Campaign."
            )
        self._validate_existing_campaign(campaign)
        approvals = tuple(campaign_repository.approvals(campaign.campaign_id))
        if (
            len(approvals) != 2
            or any(item.decision != ApprovalDecision.APPROVE for item in approvals)
        ):
            raise ValueError(
                "Running Generation requires exactly two approving Campaign records."
            )

        program_events = tuple(
            item
            for item in self.repository.events()
            if item.program_id == program_id
        )
        signal_event = self._one_program_event(
            program_events,
            event_type=ProgramEventType.SIGNAL_STORED,
            predicate=lambda item: item.payload.get("signal_id") == signal.signal_id,
            label="learning signal",
        )
        attribution_event = self._one_program_event(
            program_events,
            event_type=ProgramEventType.ATTRIBUTION_STORED,
            predicate=lambda item: item.payload.get("receipt_id")
            == attribution.receipt_id,
            label="Attribution",
        )
        decision_event = self._one_program_event(
            program_events,
            event_type=ProgramEventType.DECISION_STORED,
            predicate=lambda item: item.payload.get("decision_id")
            == decision.decision_id,
            label="CONTINUE decision",
        )
        binding_event = self._one_program_event(
            program_events,
            event_type=ProgramEventType.GENERATION_CAMPAIGN_BOUND,
            predicate=lambda item: item.generation_id == generation_id
            and item.payload.get("campaign_id") == campaign.campaign_id,
            label="Generation Campaign binding",
        )
        authorization_event = self._one_program_event(
            program_events,
            event_type=ProgramEventType.GENERATION_AUTHORIZED,
            predicate=lambda item: item.generation_id == generation_id
            and item.payload.get("campaign_id") == campaign.campaign_id,
            label="Generation authorization",
        )
        start_event = self._one_program_event(
            program_events,
            event_type=ProgramEventType.GENERATION_STARTED,
            predicate=lambda item: item.generation_id == generation_id
            and item.payload.get("plan_hash") == plan.plan_hash,
            label="Generation start",
        )
        if (
            signal_event.created_at != signal.created_at
            or attribution_event.created_at != attribution.created_at
            or decision_event.created_at != decision.decided_at
            or attribution_event.actor_id != attribution.attributor_id
            or decision_event.actor_id != decision.decided_by
            or binding_event.actor_id
            not in self._generation_evaluation_actors(campaign.campaign_id)
        ):
            raise ValueError(
                "Running Generation audit identity or time differs from immutable evidence."
            )
        evaluation_actors = self._generation_evaluation_actors(
            campaign.campaign_id
        )
        if len(evaluation_actors) != 1:
            raise ValueError(
                "Running Generation Campaign requires one exact evaluator."
            )
        evaluator = next(iter(evaluation_actors))
        roles = RunningGenerationRoles(
            release_evidence_producer_id=signal.evidence_producer_id,
            feedback_ingestor_id=signal_event.actor_id,
            causal_attributor_id=attribution.attributor_id,
            decision_planner_id=decision.decided_by,
            generation_evaluator_id=evaluator,
            generation_approver_ids=(
                approvals[0].actor_id,
                approvals[1].actor_id,
            ),
            authorization_actor_id=authorization_event.actor_id,
            start_actor_id=start_event.actor_id,
        )
        latest_evidence_time = max(
            head.updated_at,
            campaign.updated_at,
            signal_event.created_at,
            attribution_event.created_at,
            decision_event.created_at,
            binding_event.created_at,
            authorization_event.created_at,
            start_event.created_at,
            *(item.created_at for item in approvals),
        )
        if attested_at < latest_evidence_time:
            raise ValueError(
                "Running Generation attestation time precedes Registry evidence."
            )
        payload = {
            "attestation_id": attestation_id
            or f"running-generation-attestation:{program_id}:{generation.generation_index}",
            "program_id": program_id,
            "generation_id": generation_id,
            "generation_index": generation.generation_index,
            "program_state": ProgramState.GENERATION_RUNNING.value,
            "program_head_revision": head.revision,
            "program_checkpoint": ProgramExecutionCheckpoint(
                event_count=program_checkpoint.event_count,
                head_hash=program_checkpoint.head_hash,
            ),
            "campaign_id": campaign.campaign_id,
            "campaign_state": CampaignState.AUTHORIZED.value,
            "campaign_revision": campaign.revision,
            "campaign_checkpoint": ProgramExecutionCheckpoint(
                event_count=campaign_checkpoint.event_count,
                head_hash=campaign_checkpoint.head_hash,
            ),
            "plan_id": plan.plan_id,
            "plan_hash": plan.plan_hash,
            "source_signal_id": signal.signal_id,
            "source_signal_hash": signal.signal_hash,
            "attribution_receipt_id": attribution.receipt_id,
            "attribution_receipt_hash": attribution.receipt_hash,
            "intervention_layer": plan.intervention_layer,
            "intervention_action": plan.intervention_action,
            "parent_agent_identity_hash": plan.parent_agent_identity_hash,
            "target_agent_identity_hash": plan.target_agent_identity_hash,
            "expected_release_package_hash": plan.expected_release_package_hash,
            "expected_release_plan_hash": plan.expected_release_plan_hash,
            "roles": roles,
            "attested_by": attested_by,
            "attested_at": attested_at,
            "optimizer_execution_authorized": False,
            "checkpoint_promotion_authorized": False,
            "production_activation_authorized": False,
        }
        return build_running_generation_attestation(**payload)

    @staticmethod
    def _one_program_event(
        events,
        *,
        event_type: ProgramEventType,
        predicate,
        label: str,
    ):
        matches = tuple(
            item
            for item in events
            if item.event_type == event_type and predicate(item)
        )
        if len(matches) != 1:
            raise ValueError(
                f"Running Generation requires one exact {label} audit event."
            )
        return matches[0]


__all__ = ["RetryHardenedEvolutionProgramController"]
