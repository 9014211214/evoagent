from __future__ import annotations

from evoagent.program.feedback import ReleaseFeedbackExtractor
from evoagent.program.models import (
    GenerationStatus,
    ProgramAction,
    ProgramEventType,
    ProgramHead,
    ProgramState,
)
from evoagent.program.package import (
    EvolutionProgramPackageError,
    EvolutionProgramPackageManager,
    EvolutionProgramPackageManifest,
    ProgramControlEvidence,
)
from evoagent.release.models import ReleaseDecisionAction
from evoagent.release.package import ReleaseEvidencePackageManifest


class HardenedEvolutionProgramPackageManager(
    EvolutionProgramPackageManager
):
    """Add cross-record identity and event-binding checks to Program packages."""

    def verify(self, manifest: EvolutionProgramPackageManifest) -> bool:
        super().verify(manifest)
        continue_decisions = tuple(
            item
            for item in manifest.decisions
            if item.action == ProgramAction.CONTINUE
        )
        if len(continue_decisions) != 1:
            raise EvolutionProgramPackageError(
                "Program package requires exactly one CONTINUE decision."
            )
        decision = continue_decisions[0]
        plan = manifest.generations[1].plan
        if plan is None or (
            plan.created_by != decision.decided_by
            or plan.parent_generation_id != decision.generation_id
            or plan.generation_index != decision.next_generation_index
        ):
            raise EvolutionProgramPackageError(
                "Generation plan is not bound to the exact CONTINUE decision actor."
            )
        approval_actors = {
            item.actor_id for item in manifest.generation_approvals
        }
        if decision.decided_by in approval_actors:
            raise EvolutionProgramPackageError(
                "Program decision/planning actor approved its own generation."
            )
        self._verify_control_release_binding(
            manifest.budget_control,
            manifest.drift_release_package,
        )
        self._verify_control_release_binding(
            manifest.ambiguous_control,
            manifest.drift_release_package,
        )
        self._verify_main_event_bindings(manifest)
        self._verify_control_event_bindings(manifest.budget_control)
        self._verify_control_event_bindings(manifest.ambiguous_control)
        return True

    @staticmethod
    def _verify_control_release_binding(
        control: ProgramControlEvidence,
        release_package: ReleaseEvidencePackageManifest,
    ) -> None:
        if (
            len(control.generations) != 1
            or len(control.signals) != 1
            or len(control.decisions) != 1
        ):
            raise EvolutionProgramPackageError(
                "Program control requires one observed generation, signal and decision."
            )
        generation = control.generations[0]
        signal = control.signals[0]
        decision = control.decisions[0]
        if (
            generation.generation_index != 0
            or generation.status != GenerationStatus.ROLLED_BACK
            or generation.outcome is None
        ):
            raise EvolutionProgramPackageError(
                "Program control Generation 0 is not the verified rollback outcome."
            )
        extractor = ReleaseFeedbackExtractor()
        expected_outcome = extractor.generation_outcome(
            release_package,
            program_id=generation.program_id,
            generation_id=generation.generation_id,
            generation_index=0,
            outcome_id=generation.outcome.outcome_id,
            completed_at=generation.outcome.completed_at,
        )
        expected_signal = extractor.extract(
            release_package,
            program_id=generation.program_id,
            generation_index=0,
            signal_id=signal.signal_id,
            created_at=signal.created_at,
        )
        if generation.outcome != expected_outcome or signal != expected_signal:
            raise EvolutionProgramPackageError(
                "Program control evidence differs from the verified drift release package."
            )
        expected_state = {
            ProgramAction.STOP_BUDGET: ProgramState.BUDGET_EXHAUSTED,
            ProgramAction.ESCALATE: ProgramState.ESCALATED,
        }.get(decision.action)
        if expected_state is None:
            raise EvolutionProgramPackageError(
                "Program control decision is not a governed terminal control action."
            )
        expected_head = ProgramHead(
            program_id=generation.program_id,
            state=expected_state,
            current_generation_index=0,
            active_generation_id=generation.generation_id,
            revision=1,
            rollback_count=int(
                generation.outcome.release_action
                == ReleaseDecisionAction.ROLLBACK
            ),
            hold_count=int(
                generation.outcome.release_action == ReleaseDecisionAction.HOLD
            ),
            generation_campaign_count=0,
            total_pairs=generation.outcome.pair_count,
            total_tokens=generation.outcome.total_tokens,
            total_cost_usd=generation.outcome.total_cost_usd,
            last_decision_id=decision.decision_id,
            updated_at=decision.decided_at,
        )
        if (
            control.final_head != expected_head
            or control.generation_campaign_count != 0
        ):
            raise EvolutionProgramPackageError(
                "Program control head differs from verified immutable evidence."
            )

    @staticmethod
    def _verify_main_event_bindings(
        manifest: EvolutionProgramPackageManifest,
    ) -> None:
        g0, g1 = manifest.generations
        d0, d1 = manifest.decisions
        plan = g1.plan
        if g0.outcome is None or g1.outcome is None or plan is None:
            raise EvolutionProgramPackageError(
                "Program event verification requires complete generation evidence."
            )
        expected = (
            (
                ProgramEventType.PROGRAM_REGISTERED,
                None,
                {"policy_hash": manifest.policy.policy_hash},
            ),
            (
                ProgramEventType.GENERATION_OBSERVED,
                g0.generation_id,
                {
                    "outcome_hash": g0.outcome.outcome_hash,
                    "release_action": g0.outcome.release_action.value,
                },
            ),
            (
                ProgramEventType.SIGNAL_STORED,
                g0.generation_id,
                {
                    "signal_id": manifest.signal.signal_id,
                    "signal_hash": manifest.signal.signal_hash,
                    "causal_attribution_claimed": False,
                },
            ),
            (
                ProgramEventType.ATTRIBUTION_STORED,
                None,
                {
                    "receipt_id": manifest.attribution.receipt_id,
                    "receipt_hash": manifest.attribution.receipt_hash,
                    "failure_layer": manifest.attribution.failure_layer.value,
                    "action": manifest.attribution.action.value,
                },
            ),
            (
                ProgramEventType.DECISION_STORED,
                g0.generation_id,
                {
                    "decision_id": d0.decision_id,
                    "decision_hash": d0.decision_hash,
                    "action": d0.action.value,
                },
            ),
            (
                ProgramEventType.GENERATION_PLANNED,
                g1.generation_id,
                {
                    "plan_id": plan.plan_id,
                    "plan_hash": plan.plan_hash,
                    "parent_generation_id": plan.parent_generation_id,
                },
            ),
            (
                ProgramEventType.GENERATION_CAMPAIGN_BOUND,
                g1.generation_id,
                {"campaign_id": manifest.generation_campaign.campaign_id},
            ),
            (
                ProgramEventType.GENERATION_AUTHORIZED,
                g1.generation_id,
                {"campaign_id": manifest.generation_campaign.campaign_id},
            ),
            (
                ProgramEventType.GENERATION_STARTED,
                g1.generation_id,
                {"plan_hash": plan.plan_hash},
            ),
            (
                ProgramEventType.GENERATION_COMPLETED,
                g1.generation_id,
                {
                    "outcome_hash": g1.outcome.outcome_hash,
                    "release_action": g1.outcome.release_action.value,
                    "release_package_hash": g1.outcome.release_package_hash,
                },
            ),
            (
                ProgramEventType.DECISION_STORED,
                g1.generation_id,
                {
                    "decision_id": d1.decision_id,
                    "decision_hash": d1.decision_hash,
                    "action": d1.action.value,
                },
            ),
            (
                ProgramEventType.PROGRAM_COMPLETED,
                g1.generation_id,
                {"decision_hash": d1.decision_hash},
            ),
        )
        if len(manifest.program_events) != len(expected):
            raise EvolutionProgramPackageError(
                "Program audit event count differs from immutable lifecycle evidence."
            )
        for event, (event_type, generation_id, payload) in zip(
            manifest.program_events,
            expected,
            strict=True,
        ):
            if (
                event.program_id != manifest.final_head.program_id
                or event.event_type != event_type
                or event.generation_id != generation_id
                or event.payload != payload
            ):
                raise EvolutionProgramPackageError(
                    "Program audit event differs from immutable lifecycle evidence."
                )
        if (
            manifest.program_events[3].actor_id
            != manifest.attribution.attributor_id
            or manifest.program_events[4].actor_id != d0.decided_by
            or manifest.program_events[5].actor_id != plan.created_by
            or manifest.program_events[10].actor_id != d1.decided_by
            or manifest.program_events[11].actor_id != d1.decided_by
        ):
            raise EvolutionProgramPackageError(
                "Program audit actor differs from attribution, plan, or decision identity."
            )

    @staticmethod
    def _verify_control_event_bindings(
        control: ProgramControlEvidence,
    ) -> None:
        generation = control.generations[0]
        decision = control.decisions[0]
        signal = control.signals[0]
        if generation.outcome is None:
            raise EvolutionProgramPackageError(
                "Program control is missing Generation 0 outcome."
            )
        expected = [
            (
                ProgramEventType.PROGRAM_REGISTERED,
                None,
                {"policy_hash": control.policy.policy_hash},
            ),
            (
                ProgramEventType.GENERATION_OBSERVED,
                generation.generation_id,
                {
                    "outcome_hash": generation.outcome.outcome_hash,
                    "release_action": generation.outcome.release_action.value,
                },
            ),
            (
                ProgramEventType.SIGNAL_STORED,
                generation.generation_id,
                {
                    "signal_id": signal.signal_id,
                    "signal_hash": signal.signal_hash,
                    "causal_attribution_claimed": False,
                },
            ),
        ]
        if control.attributions:
            attribution = control.attributions[0]
            expected.append(
                (
                    ProgramEventType.ATTRIBUTION_STORED,
                    None,
                    {
                        "receipt_id": attribution.receipt_id,
                        "receipt_hash": attribution.receipt_hash,
                        "failure_layer": attribution.failure_layer.value,
                        "action": attribution.action.value,
                    },
                )
            )
        expected.extend(
            (
                (
                    ProgramEventType.DECISION_STORED,
                    generation.generation_id,
                    {
                        "decision_id": decision.decision_id,
                        "decision_hash": decision.decision_hash,
                        "action": decision.action.value,
                    },
                ),
                (
                    {
                        ProgramAction.STOP_BUDGET:
                            ProgramEventType.PROGRAM_BUDGET_EXHAUSTED,
                        ProgramAction.ESCALATE:
                            ProgramEventType.PROGRAM_ESCALATED,
                    }[decision.action],
                    generation.generation_id,
                    {"decision_hash": decision.decision_hash},
                ),
            )
        )
        if len(control.events) != len(expected):
            raise EvolutionProgramPackageError(
                "Program control event count differs from immutable evidence."
            )
        for event, (event_type, generation_id, payload) in zip(
            control.events,
            expected,
            strict=True,
        ):
            if (
                event.program_id != control.final_head.program_id
                or event.event_type != event_type
                or event.generation_id != generation_id
                or event.payload != payload
            ):
                raise EvolutionProgramPackageError(
                    "Program control audit event differs from immutable evidence."
                )


__all__ = ["HardenedEvolutionProgramPackageManager"]
