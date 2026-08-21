from __future__ import annotations

from typing import Protocol

from evoagent.model_registry.models import (
    ExternalModelCandidateManifest,
    ModelArtifactFormat,
    SyntheticCandidateProfile,
    canonical_sha256,
)
from evoagent.runtime import (
    AgentAction,
    AgentContext,
    DocumentSkillPolicy,
    IncapableDocumentPolicy,
    ToolAgentPolicy,
)
from evoagent.skills.models import SkillSpec


class ModelEvaluationError(ValueError):
    pass


class ModelCandidateAdapter(Protocol):
    adapter_id: str
    candidate_id: str
    candidate_manifest_hash: str
    generated_by: str
    synthetic: bool
    adapter_hash: str

    def build_policy(self) -> ToolAgentPolicy:
        ...

    def build_skill(self) -> SkillSpec:
        ...


class RetentionAwareBasePolicy(ToolAgentPolicy):
    def __init__(self):
        self.fallback = IncapableDocumentPolicy()

    def next_action(self, context: AgentContext) -> AgentAction:
        if context.task.task_type == "model-retention":
            return AgentAction.finish(
                status="retained",
                capability=context.task.input.get("capability", "baseline"),
            )
        return self.fallback.next_action(context)


class RetentionAwareCandidatePolicy(ToolAgentPolicy):
    def __init__(self, *, regress_retention: bool = False):
        self.fallback = DocumentSkillPolicy()
        self.regress_retention = regress_retention

    def next_action(self, context: AgentContext) -> AgentAction:
        if context.task.task_type == "model-retention":
            if self.regress_retention:
                return AgentAction.finish(
                    status="failed",
                    error_code="candidate_retention_regression",
                )
            return AgentAction.finish(
                status="retained",
                capability=context.task.input.get("capability", "baseline"),
            )
        return self.fallback.next_action(context)


class ChatteringCandidatePolicy(ToolAgentPolicy):
    """Add observable redundant calls, then run the same candidate policy."""

    def __init__(self, *, extra_calls: int = 4):
        self.extra_calls = extra_calls
        self.fallback = RetentionAwareCandidatePolicy()

    def next_action(self, context: AgentContext) -> AgentAction:
        if context.task.task_type != "model-retention":
            chatter_count = sum(
                item.tool_name == "list_documents" for item in context.tool_results
            )
            if chatter_count < self.extra_calls:
                return AgentAction.call(
                    f"{context.task.task_id}:{context.step_index}:budget-probe",
                    "list_documents",
                )
            filtered = tuple(
                item
                for item in context.tool_results
                if item.tool_name != "list_documents"
            )
            context = context.model_copy(
                deep=True,
                update={"tool_results": filtered},
            )
        return self.fallback.next_action(context)


class SyntheticModelCandidateAdapter:
    """Deterministic policy fixture; never represents trained model weights."""

    def __init__(self, candidate: ExternalModelCandidateManifest):
        if candidate.artifact_format != ModelArtifactFormat.SYNTHETIC_POLICY:
            raise ModelEvaluationError(
                "Synthetic adapter requires a synthetic_policy candidate manifest."
            )
        if candidate.synthetic_profile is None:
            raise ModelEvaluationError(
                "Synthetic adapter requires an explicit synthetic candidate profile."
            )
        self.candidate = candidate
        self.adapter_id = f"synthetic-adapter:{candidate.candidate_id}"
        self.candidate_id = candidate.candidate_id
        self.candidate_manifest_hash = candidate.manifest_hash
        self.generated_by = candidate.generated_by
        self.synthetic = True
        self.adapter_hash = canonical_sha256(
            {
                "adapter_id": self.adapter_id,
                "candidate_id": self.candidate_id,
                "candidate_manifest_hash": self.candidate_manifest_hash,
                "profile": candidate.synthetic_profile.value,
                "synthetic": True,
            }
        )

    def build_policy(self) -> ToolAgentPolicy:
        profile = self.candidate.synthetic_profile
        if profile == SyntheticCandidateProfile.OVER_BUDGET:
            return ChatteringCandidatePolicy()
        return RetentionAwareCandidatePolicy(
            regress_retention=profile == SyntheticCandidateProfile.REGRESSING
        )

    def build_skill(self) -> SkillSpec:
        safe = self.candidate.synthetic_profile != SyntheticCandidateProfile.UNSAFE
        return evaluation_skill(safe=safe)


def evaluation_skill(*, safe: bool) -> SkillSpec:
    return SkillSpec(
        skill_id=(
            "model_eval_safe_writer"
            if safe
            else "model_eval_unsafe_writer"
        ),
        name=(
            "Model Evaluation Safe Writer"
            if safe
            else "Model Evaluation Unsafe Writer"
        ),
        version="1.0.0",
        description="Synthetic local document evaluation Skill.",
        rules=(
            ("inspect_before_write", "verify_after_write")
            if safe
            else ("verify_after_write",)
        ),
        allowed_tools=(
            "read_document",
            "write_document",
            "list_documents",
        ),
        provenance="synthetic-model-admission-evaluation",
        generated_by="evoagent-model-evaluator",
    )


__all__ = [
    "ChatteringCandidatePolicy",
    "ModelCandidateAdapter",
    "ModelEvaluationError",
    "RetentionAwareBasePolicy",
    "RetentionAwareCandidatePolicy",
    "SyntheticModelCandidateAdapter",
    "evaluation_skill",
]
