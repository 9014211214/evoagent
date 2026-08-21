from __future__ import annotations

from evoagent.campaigns import CampaignState, CampaignType
from evoagent.program.package import (
    EvolutionProgramPackageError,
    EvolutionProgramPackageManifest,
)
from evoagent.program.package_policy_hardened import (
    PolicyHardenedEvolutionProgramPackageManager,
)


_EVALUATION_RESULT_REASONS = {
    "GenerationPlan matches verified feedback, attribution and Program budget.",
    "Recovered exact GenerationPlan evaluation after partial submission.",
}
_COMPLETION_REASONS = {
    "Exact authorized generation completed with verified child evidence.",
    "Recovered exact completed generation after partial cross-registry commit.",
}
_PROGRAM_CAMPAIGN_BIND_REASONS = {
    "High-risk Generation Campaign bound to exact plan.",
    "Recovered and bound the exact partially created Generation Campaign.",
}


class AuditHardenedEvolutionProgramPackageManager(
    PolicyHardenedEvolutionProgramPackageManager
):
    """Cross-bind complete Program and Campaign audit lifecycle semantics."""

    def verify(self, manifest: EvolutionProgramPackageManifest) -> bool:
        super().verify(manifest)
        self._verify_program_event_semantics(manifest)
        self._verify_campaign_lifecycle_events(manifest)
        return True

    @staticmethod
    def _verify_program_event_semantics(
        manifest: EvolutionProgramPackageManifest,
    ) -> None:
        if len(manifest.program_events) != 12:
            raise EvolutionProgramPackageError(
                "Program requires exactly twelve lifecycle audit events."
            )
        events = manifest.program_events
        g0, g1 = manifest.generations
        g1_plan = g1.plan
        d0, d1 = manifest.decisions
        if g0.outcome is None or g1.outcome is None or g1_plan is None:
            raise EvolutionProgramPackageError(
                "Program audit verification requires complete generation evidence."
            )
        expected_reasons = (
            "Persistent multi-generation Program registered.",
            "Observed terminal release evidence recorded as Generation 0.",
            "Verified release rollback/hold evidence stored without claiming a root cause.",
            "Independent causal attribution receipt stored.",
            d0.reason,
            "Exact successor GenerationPlan persisted.",
            events[6].reason,
            "Exact Generation Campaign authorization synchronized locally.",
            "Explicit local start of the exact authorized generation.",
            "Authorized child release evidence completed the Program generation.",
            d1.reason,
            d1.reason,
        )
        if events[6].reason not in _PROGRAM_CAMPAIGN_BIND_REASONS:
            raise EvolutionProgramPackageError(
                "Program Campaign-binding audit reason was substituted."
            )
        if tuple(item.reason for item in events) != expected_reasons:
            raise EvolutionProgramPackageError(
                "Program audit reason differs from immutable lifecycle semantics."
            )
        if events[0].actor_id != events[1].actor_id:
            raise EvolutionProgramPackageError(
                "Program registration and observed root generation actors differ."
            )
        if events[2].actor_id == manifest.signal.evidence_producer_id:
            raise EvolutionProgramPackageError(
                "Program feedback ingestion actor equals the release evidence producer."
            )
        if (
            events[3].actor_id != manifest.attribution.attributor_id
            or events[4].actor_id != d0.decided_by
            or events[5].actor_id != g1_plan.created_by
            or events[10].actor_id != d1.decided_by
            or events[11].actor_id != d1.decided_by
        ):
            raise EvolutionProgramPackageError(
                "Program audit actor differs from attribution, plan, or decision identity."
            )
        forbidden = {
            manifest.signal.evidence_producer_id,
            manifest.attribution.attributor_id,
            g1_plan.created_by,
        }
        forbidden.update(
            item.actor_id for item in manifest.generation_approvals
        )
        for event in (
            events[6],
            events[7],
            events[8],
            events[9],
        ):
            if event.actor_id in forbidden:
                raise EvolutionProgramPackageError(
                    "Program authorization or execution actor violates role separation."
                )
        exact_times = {
            0: g0.created_at,
            1: g0.created_at,
            2: manifest.signal.created_at,
            3: manifest.attribution.created_at,
            4: d0.decided_at,
            5: g1.created_at,
            9: g1.outcome.completed_at,
            10: d1.decided_at,
            11: d1.decided_at,
        }
        for index, expected_time in exact_times.items():
            if events[index].created_at != expected_time:
                raise EvolutionProgramPackageError(
                    "Program audit time differs from immutable lifecycle evidence."
                )
        if (
            g0.created_at != g0.outcome.completed_at
            or g1.updated_at != g1.outcome.completed_at
            or manifest.final_head.updated_at != d1.decided_at
        ):
            raise EvolutionProgramPackageError(
                "Program record time differs from immutable outcome or decision evidence."
            )
        timestamps = tuple(item.created_at for item in events)
        if timestamps != tuple(sorted(timestamps)):
            raise EvolutionProgramPackageError(
                "Program audit timestamps are not monotonic."
            )

    @staticmethod
    def _verify_campaign_lifecycle_events(
        manifest: EvolutionProgramPackageManifest,
    ) -> None:
        if len(manifest.campaign_events) != 7:
            raise EvolutionProgramPackageError(
                "Generation Campaign requires exactly seven lifecycle events."
            )
        (
            created,
            candidate,
            evaluation_started,
            evaluation_completed,
            approval_event_a,
            approval_event_b,
            completed,
        ) = manifest.campaign_events
        campaign = manifest.generation_campaign
        approvals = manifest.generation_approvals
        plan = manifest.generations[1].plan
        if plan is None or len(approvals) != 2:
            raise EvolutionProgramPackageError(
                "Generation Campaign audit verification lacks plan or approvals."
            )
        if campaign.revision != 6:
            raise EvolutionProgramPackageError(
                "Generation Campaign revision differs from its lifecycle."
            )
        if (
            created.actor_id != "evoagent-system"
            or created.created_at != campaign.created_at
            or created.payload
            != {
                "campaign_type": CampaignType.EVOLUTION_GENERATION.value,
                "target_key": campaign.target_key,
                "fingerprint": campaign.fingerprint,
                "state": CampaignState.OPEN.value,
            }
        ):
            raise EvolutionProgramPackageError(
                "Generation Campaign creation event differs from its record."
            )
        if candidate.payload != {"candidate_ref": campaign.candidate_ref}:
            raise EvolutionProgramPackageError(
                "Generation Campaign candidate event differs from its artifact."
            )
        if evaluation_started.payload != {
            "from_state": CampaignState.CANDIDATE_READY.value,
            "to_state": CampaignState.EVALUATION_PENDING.value,
            "reason": "Independent evaluation started.",
            "cooldown_until": None,
        }:
            raise EvolutionProgramPackageError(
                "Generation Campaign evaluation-start event was substituted."
            )
        if (
            evaluation_completed.payload.get("from_state")
            != CampaignState.EVALUATION_PENDING.value
            or evaluation_completed.payload.get("to_state")
            != CampaignState.APPROVAL_PENDING.value
            or evaluation_completed.payload.get("cooldown_until") is not None
            or evaluation_completed.payload.get("reason")
            not in _EVALUATION_RESULT_REASONS
            or set(evaluation_completed.payload)
            != {"from_state", "to_state", "reason", "cooldown_until"}
        ):
            raise EvolutionProgramPackageError(
                "Generation Campaign evaluation-result event was substituted."
            )
        approval_events = (approval_event_a, approval_event_b)
        expected_resulting_states = (
            CampaignState.APPROVAL_PENDING.value,
            CampaignState.AUTHORIZED.value,
        )
        for event, approval, resulting_state in zip(
            approval_events,
            approvals,
            expected_resulting_states,
            strict=True,
        ):
            if (
                event.campaign_id != approval.campaign_id
                or event.actor_id != approval.actor_id
                or event.created_at != approval.created_at
                or event.payload
                != {
                    "decision": approval.decision.value,
                    "reason": approval.reason,
                    "resulting_state": resulting_state,
                }
            ):
                raise EvolutionProgramPackageError(
                    "Generation Campaign approval identity, reason, time, or state was substituted."
                )
        if (
            completed.created_at != campaign.updated_at
            or completed.payload.get("from_state")
            != CampaignState.AUTHORIZED.value
            or completed.payload.get("to_state")
            != CampaignState.COMPLETED.value
            or completed.payload.get("cooldown_until") is not None
            or completed.payload.get("reason") not in _COMPLETION_REASONS
            or set(completed.payload)
            != {"from_state", "to_state", "reason", "cooldown_until"}
        ):
            raise EvolutionProgramPackageError(
                "Generation Campaign completion event was substituted."
            )
        forbidden = {
            manifest.signal.evidence_producer_id,
            manifest.attribution.attributor_id,
            plan.created_by,
        }
        forbidden.update(item.actor_id for item in approvals)
        for event in (
            candidate,
            evaluation_started,
            evaluation_completed,
            completed,
        ):
            if event.actor_id in forbidden:
                raise EvolutionProgramPackageError(
                    "Generation Campaign execution actor violates role separation."
                )
        timestamps = tuple(item.created_at for item in manifest.campaign_events)
        if timestamps != tuple(sorted(timestamps)):
            raise EvolutionProgramPackageError(
                "Generation Campaign audit timestamps are not monotonic."
            )


__all__ = ["AuditHardenedEvolutionProgramPackageManager"]
