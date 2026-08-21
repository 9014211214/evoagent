from __future__ import annotations

from evoagent.program.constraints import (
    validate_bounded_automatic_layers,
    validate_single_release_package_budget,
)
from evoagent.program.gate_final_hardened import HardenedEvolutionProgramGate
from evoagent.program.models import (
    ProgramAction,
    ProgramEventType,
    ProgramHead,
    ProgramState,
)
from evoagent.program.package import (
    EvolutionProgramPackageError,
    EvolutionProgramPackageManifest,
    ProgramControlEvidence,
)
from evoagent.program.package_gate_normalized import (
    GateNormalizedEvolutionProgramPackageManager,
)


class PolicyHardenedEvolutionProgramPackageManager(
    GateNormalizedEvolutionProgramPackageManager
):
    """Recompute packaged decisions and negative controls from exact policy."""

    def verify(self, manifest: EvolutionProgramPackageManifest) -> bool:
        super().verify(manifest)
        self._verify_policy_boundary(manifest.policy)
        self._verify_generation_budget_boundary(manifest)
        if not manifest.policy.require_generation_approvals:
            raise EvolutionProgramPackageError(
                "Packaged high-risk Program cannot disable independent approvals."
            )
        gate = HardenedEvolutionProgramGate()
        g0, g1 = manifest.generations
        d0, d1 = manifest.decisions
        if g0.outcome is None or g1.outcome is None:
            raise EvolutionProgramPackageError(
                "Program policy verification requires both generation outcomes."
            )
        head0 = ProgramHead(
            program_id=g0.program_id,
            state=ProgramState.RUNNING,
            current_generation_index=0,
            active_generation_id=g0.generation_id,
            revision=0,
            rollback_count=1,
            hold_count=0,
            generation_campaign_count=0,
            total_pairs=g0.outcome.pair_count,
            total_tokens=g0.outcome.total_tokens,
            total_cost_usd=g0.outcome.total_cost_usd,
            updated_at=d0.decided_at,
        )
        expected_d0 = gate.decide(
            policy=manifest.policy,
            head=head0,
            outcome=g0.outcome,
            decision_id=d0.decision_id,
            decided_by=d0.decided_by,
            decided_at=d0.decided_at,
            signal=manifest.signal,
            attribution=manifest.attribution,
            consecutive_non_improving_count=1,
        )
        prefinal = manifest.final_head.model_copy(
            update={
                "state": ProgramState.RUNNING,
                "revision": manifest.final_head.revision - 1,
                "last_decision_id": d0.decision_id,
                "updated_at": d1.decided_at,
            }
        )
        expected_d1 = gate.decide(
            policy=manifest.policy,
            head=prefinal,
            outcome=g1.outcome,
            decision_id=d1.decision_id,
            decided_by=d1.decided_by,
            decided_at=d1.decided_at,
            consecutive_non_improving_count=0,
        )
        if expected_d0 != d0 or expected_d1 != d1:
            raise EvolutionProgramPackageError(
                "Packaged decisions differ from hardened Program policy."
            )
        self._verify_control_policy(manifest.budget_control)
        self._verify_control_policy(manifest.ambiguous_control)
        self._verify_control_audit_semantics(manifest.budget_control)
        self._verify_control_audit_semantics(manifest.ambiguous_control)
        return True

    @staticmethod
    def _verify_policy_boundary(policy) -> None:
        try:
            validate_bounded_automatic_layers(policy.allowed_automatic_layers)
        except ValueError as exc:
            raise EvolutionProgramPackageError(
                "Packaged Program policy widens automatic intervention authority."
            ) from exc

    @staticmethod
    def _verify_generation_budget_boundary(
        manifest: EvolutionProgramPackageManifest,
    ) -> None:
        if len(manifest.generations) != 2 or manifest.generations[1].plan is None:
            raise EvolutionProgramPackageError(
                "Program package lacks the exact successor GenerationPlan."
            )
        try:
            validate_single_release_package_budget(
                manifest.generations[1].plan.budget
            )
        except ValueError as exc:
            raise EvolutionProgramPackageError(
                "Packaged GenerationPlan permits an unrepresentable package count."
            ) from exc

    @classmethod
    def _verify_control_policy(cls, control: ProgramControlEvidence) -> None:
        if (
            len(control.generations) != 1
            or len(control.signals) != 1
            or len(control.decisions) != 1
        ):
            raise EvolutionProgramPackageError(
                "Program control policy verification requires one generation, "
                "signal and decision."
            )
        cls._verify_policy_boundary(control.policy)
        generation = control.generations[0]
        signal = control.signals[0]
        decision = control.decisions[0]
        if generation.outcome is None:
            raise EvolutionProgramPackageError(
                "Program control policy verification requires Generation 0 outcome."
            )
        expected_attribution_count = {
            ProgramAction.STOP_BUDGET: 0,
            ProgramAction.ESCALATE: 1,
        }.get(decision.action)
        if (
            expected_attribution_count is None
            or len(control.attributions) != expected_attribution_count
        ):
            raise EvolutionProgramPackageError(
                "Program control Attribution cardinality differs from its action."
            )
        if control.attributions:
            attribution = control.attributions[0]
            if (
                attribution.signal_id != signal.signal_id
                or attribution.signal_hash != signal.signal_hash
                or attribution.attributor_id == signal.evidence_producer_id
            ):
                raise EvolutionProgramPackageError(
                    "Program control Attribution is not bound and independent."
                )
        predecision = control.final_head.model_copy(
            update={
                "state": ProgramState.RUNNING,
                "revision": control.final_head.revision - 1,
                "last_decision_id": None,
                "updated_at": decision.decided_at,
            }
        )
        expected = HardenedEvolutionProgramGate().decide(
            policy=control.policy,
            head=predecision,
            outcome=generation.outcome,
            decision_id=decision.decision_id,
            decided_by=decision.decided_by,
            decided_at=decision.decided_at,
            signal=signal,
            attribution=(control.attributions[0] if control.attributions else None),
            consecutive_non_improving_count=1,
        )
        if expected != decision:
            raise EvolutionProgramPackageError(
                "Program control decision differs from hardened policy."
            )

    @staticmethod
    def _verify_control_audit_semantics(
        control: ProgramControlEvidence,
    ) -> None:
        if (
            len(control.generations) != 1
            or len(control.signals) != 1
            or len(control.decisions) != 1
        ):
            raise EvolutionProgramPackageError(
                "Program control audit verification requires one generation, signal and decision."
            )
        generation = control.generations[0]
        signal = control.signals[0]
        decision = control.decisions[0]
        if generation.outcome is None:
            raise EvolutionProgramPackageError(
                "Program control audit verification requires a terminal outcome."
            )
        expected_terminal = {
            ProgramAction.STOP_BUDGET: ProgramEventType.PROGRAM_BUDGET_EXHAUSTED,
            ProgramAction.ESCALATE: ProgramEventType.PROGRAM_ESCALATED,
        }.get(decision.action)
        if expected_terminal is None:
            raise EvolutionProgramPackageError(
                "Program control audit action is not governed."
            )
        expected_types = [
            ProgramEventType.PROGRAM_REGISTERED,
            ProgramEventType.GENERATION_OBSERVED,
            ProgramEventType.SIGNAL_STORED,
        ]
        if control.attributions:
            expected_types.append(ProgramEventType.ATTRIBUTION_STORED)
        expected_types.extend(
            [ProgramEventType.DECISION_STORED, expected_terminal]
        )
        if tuple(item.event_type for item in control.events) != tuple(expected_types):
            raise EvolutionProgramPackageError(
                "Program control audit event sequence differs from its lifecycle."
            )
        reasons = [
            "Persistent multi-generation Program registered.",
            "Observed terminal release evidence recorded as Generation 0.",
            "Verified release rollback/hold evidence stored without claiming a root cause.",
        ]
        if control.attributions:
            reasons.append("Independent causal attribution receipt stored.")
        reasons.extend([decision.reason, decision.reason])
        if tuple(item.reason for item in control.events) != tuple(reasons):
            raise EvolutionProgramPackageError(
                "Program control audit reason differs from immutable lifecycle semantics."
            )
        registration, observed, signal_event = control.events[:3]
        if registration.actor_id != observed.actor_id:
            raise EvolutionProgramPackageError(
                "Program control registration and observed-generation actors differ."
            )
        if signal_event.actor_id == signal.evidence_producer_id:
            raise EvolutionProgramPackageError(
                "Program control feedback ingestor equals the release evidence producer."
            )
        decision_index = 3
        forbidden_decision_actors = {
            signal.evidence_producer_id,
            signal_event.actor_id,
        }
        if control.attributions:
            attribution = control.attributions[0]
            attribution_event = control.events[3]
            if attribution_event.actor_id != attribution.attributor_id:
                raise EvolutionProgramPackageError(
                    "Program control Attribution actor differs from its receipt."
                )
            if attribution_event.created_at != attribution.created_at:
                raise EvolutionProgramPackageError(
                    "Program control Attribution event time differs from its receipt."
                )
            forbidden_decision_actors.add(attribution.attributor_id)
            decision_index = 4
        decision_event = control.events[decision_index]
        terminal_event = control.events[decision_index + 1]
        if (
            decision_event.actor_id != decision.decided_by
            or terminal_event.actor_id != decision.decided_by
        ):
            raise EvolutionProgramPackageError(
                "Program control terminal actor differs from its decision."
            )
        if decision.decided_by in forbidden_decision_actors:
            raise EvolutionProgramPackageError(
                "Program control decision actor violates role separation."
            )
        if (
            registration.created_at != generation.created_at
            or observed.created_at != generation.created_at
            or generation.created_at != generation.outcome.completed_at
            or signal_event.created_at != signal.created_at
            or decision_event.created_at != decision.decided_at
            or terminal_event.created_at != decision.decided_at
            or control.final_head.updated_at != decision.decided_at
        ):
            raise EvolutionProgramPackageError(
                "Program control audit time differs from immutable lifecycle evidence."
            )
        timestamps = tuple(item.created_at for item in control.events)
        if timestamps != tuple(sorted(timestamps)):
            raise EvolutionProgramPackageError(
                "Program control audit timestamps are not monotonic."
            )


__all__ = ["PolicyHardenedEvolutionProgramPackageManager"]
