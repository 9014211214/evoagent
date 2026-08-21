from __future__ import annotations

from datetime import datetime

from evoagent.program.hashing import program_payload_hash
from evoagent.program.models import (
    GenerationOutcome,
    GenerationPlan,
    ProgramLearningSignal,
)
from evoagent.release.models import ReleaseDecisionAction, ReleaseState
from evoagent.release.package import (
    ReleaseEvidencePackageManager,
    ReleaseEvidencePackageManifest,
)


class ReleaseFeedbackExtractor:
    """Turn verified terminal release evidence into non-causal Program inputs."""

    def extract(
        self,
        package: ReleaseEvidencePackageManifest,
        *,
        program_id: str,
        generation_index: int,
        signal_id: str,
        created_at: datetime,
    ) -> ProgramLearningSignal:
        ReleaseEvidencePackageManager().verify(package)
        decision, assessment, batch = self._terminal_evidence(package)
        if decision.action not in {
            ReleaseDecisionAction.ROLLBACK,
            ReleaseDecisionAction.HOLD,
        }:
            raise ValueError("Only rollback or hold release evidence creates feedback.")
        affected = tuple(
            sorted(
                {
                    item.segment_id
                    for item in assessment.segment_assessments
                    if item.regressed or item.challenger_safety_violations > 0
                }
            )
        )
        protected = tuple(
            sorted(
                item.segment_id
                for item in assessment.segment_assessments
                if item.protected
                and (item.regressed or item.challenger_safety_violations > 0)
            )
        )
        payload = {
            "signal_id": signal_id,
            "program_id": program_id,
            "generation_index": generation_index,
            "source_release_package_hash": package.package_hash,
            "source_release_plan_hash": package.plan.plan_hash,
            "source_batch_hash": batch.evidence_hash,
            "source_assessment_hash": assessment.assessment_hash,
            "source_decision_hash": decision.decision_hash,
            "source_stage_id": decision.stage_id,
            "family_id": package.plan.family_id,
            "incumbent_snapshot_id": package.plan.incumbent_snapshot_id,
            "challenger_snapshot_id": package.plan.challenger_snapshot_id,
            "runtime_config_sha256": package.plan.runtime_config_sha256,
            "tool_contract_sha256": package.plan.tool_contract_sha256,
            "terminal_action": decision.action,
            "terminal_state": package.final_head.state,
            "reasons": tuple(assessment.reasons),
            "affected_segments": affected,
            "protected_segments": protected,
            "safety_violation_count": assessment.challenger_safety_violations,
            "evidence_producer_id": decision.evidence_producer_id,
            "created_at": created_at,
            "trust_level": "verified",
            "causal_attribution_claimed": False,
        }
        return ProgramLearningSignal(
            **payload,
            signal_hash=program_payload_hash(payload),
        )

    def generation_outcome(
        self,
        package: ReleaseEvidencePackageManifest,
        *,
        program_id: str,
        generation_id: str,
        generation_index: int,
        outcome_id: str,
        completed_at: datetime,
        plan: GenerationPlan | None = None,
    ) -> GenerationOutcome:
        ReleaseEvidencePackageManager().verify(package)
        decision, assessment, _ = self._terminal_evidence(package)
        if decision.action not in {
            ReleaseDecisionAction.READY,
            ReleaseDecisionAction.ROLLBACK,
            ReleaseDecisionAction.HOLD,
        }:
            raise ValueError("Program generation requires terminal release evidence.")
        if generation_index == 0:
            if plan is not None:
                raise ValueError("Observed Generation 0 cannot have a Program plan.")
        else:
            if plan is None:
                raise ValueError("Successor generation requires its authorized plan.")
            if (
                package.package_hash != plan.expected_release_package_hash
                or package.plan.plan_hash != plan.expected_release_plan_hash
                or package.plan.runtime_config_sha256
                != plan.target_runtime_config_sha256
                or package.plan.tool_contract_sha256
                != plan.target_tool_contract_sha256
            ):
                raise ValueError("Child release package widens or differs from GenerationPlan.")
        pair_count = sum(item.pair_count for item in package.batches)
        total_tokens = sum(
            (event.input_tokens or 0) + (event.output_tokens or 0)
            for batch in package.batches
            for event in batch.events
        )
        total_cost = sum(
            event.cost_usd or 0.0
            for batch in package.batches
            for event in batch.events
        )
        affected = tuple(
            sorted(
                {
                    item.segment_id
                    for item in assessment.segment_assessments
                    if item.regressed or item.challenger_safety_violations > 0
                }
            )
        )
        agent_identity_hash = program_payload_hash(
            {
                "champion_package_hash": package.champion_package.package_hash,
                "snapshot_id": package.plan.challenger_snapshot_id,
                "runtime_config_sha256": package.plan.runtime_config_sha256,
                "tool_contract_sha256": package.plan.tool_contract_sha256,
            }
        )
        if plan is not None and agent_identity_hash != plan.target_agent_identity_hash:
            raise ValueError(
                "Verified child release identity differs from GenerationPlan target identity."
            )
        payload = {
            "outcome_id": outcome_id,
            "program_id": program_id,
            "generation_id": generation_id,
            "generation_index": generation_index,
            "plan_id": plan.plan_id if plan else None,
            "plan_hash": plan.plan_hash if plan else None,
            "release_package_hash": package.package_hash,
            "release_plan_hash": package.plan.plan_hash,
            "champion_package_hash": package.champion_package.package_hash,
            "agent_identity_hash": agent_identity_hash,
            "runtime_config_sha256": package.plan.runtime_config_sha256,
            "tool_contract_sha256": package.plan.tool_contract_sha256,
            "release_action": decision.action,
            "release_state": package.final_head.state,
            "pair_count": pair_count,
            "total_tokens": total_tokens,
            "total_cost_usd": total_cost,
            "quality_delta": assessment.quality_delta,
            "safety_violation_count": assessment.challenger_safety_violations,
            "affected_segments": affected,
            "reasons": tuple(assessment.reasons),
            "completed_at": completed_at,
            "external_model_call_performed_by_evoagent": False,
            "training_executed_by_evoagent": False,
            "external_rollout_performed_by_evoagent": False,
            "production_deployment_performed": False,
        }
        return GenerationOutcome(
            **payload,
            outcome_hash=program_payload_hash(payload),
        )

    @staticmethod
    def _terminal_evidence(package: ReleaseEvidencePackageManifest):
        last_stage = package.plan.stages[-1]
        decisions = {item.stage_id: item for item in package.decisions}
        assessments = {item.stage_id: item for item in package.assessments}
        batches = {item.stage_id: item for item in package.batches}
        decision = decisions[last_stage.stage_id]
        assessment = assessments[last_stage.stage_id]
        batch = batches[last_stage.stage_id]
        expected_state = {
            ReleaseDecisionAction.READY: ReleaseState.READY,
            ReleaseDecisionAction.ROLLBACK: ReleaseState.ROLLED_BACK,
            ReleaseDecisionAction.HOLD: ReleaseState.HOLD,
        }.get(decision.action)
        if expected_state is None or package.final_head.state != expected_state:
            raise ValueError("Release package terminal decision and final head differ.")
        return decision, assessment, batch


__all__ = ["ReleaseFeedbackExtractor"]
