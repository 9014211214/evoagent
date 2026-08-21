from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence


_LEARNING_ROLES = frozenset({"canonical", "enriched", "variant"})
_EVALUATION_ROLES = frozenset({"context-shift", "adversarial", "composition"})


@dataclass(frozen=True)
class SkillEvolBenchAttributionDecision:
    action: Literal["induce", "revise", "noop"]
    reason: str
    target_skill_id: str | None = None


def _same_family(skill_id: str, family_id: str) -> bool:
    return bool(skill_id) and skill_id.split(".", 1)[0] == family_id


def _unique_same_family(
    skill_ids: Sequence[str],
    family_id: str,
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            skill_id
            for skill_id in skill_ids
            if _same_family(skill_id, family_id)
        )
    )


def decide_skillevolbench_skill_action(
    *,
    role: str,
    verifier_passed: bool,
    family_id: str,
    family_has_seed: bool,
    actually_used_skill_ids: Sequence[str] = (),
    retrieved_skill_ids: Sequence[str] = (),
    family_skill_ids: Sequence[str] = (),
) -> SkillEvolBenchAttributionDecision:
    """Conservative SkillEvolBench routing for the EvoAgent condition.

    The benchmark's T4-T6 deployment block is immutable. T1 may induce the
    family's first Skill from experience. Later mutation is allowed only for
    a failed learning trial with exactly one same-family target supported by
    observable usage, retrieval, or (last) the family library itself.

    More than one same-family candidate is treated as ambiguous attribution
    and fails closed to ``noop`` rather than choosing an arbitrary Skill.
    """

    normalized_role = str(role)
    if normalized_role in _EVALUATION_ROLES:
        return SkillEvolBenchAttributionDecision(
            action="noop",
            reason="frozen_evaluation_block",
        )
    if normalized_role not in _LEARNING_ROLES:
        return SkillEvolBenchAttributionDecision(
            action="noop",
            reason=f"unsupported_role:{normalized_role}",
        )

    if normalized_role == "canonical" and not family_has_seed:
        return SkillEvolBenchAttributionDecision(
            action="induce",
            reason="initial_family_skill_induction",
        )

    if verifier_passed:
        return SkillEvolBenchAttributionDecision(
            action="noop",
            reason="no_bad_case_no_revision",
        )

    evidence_tiers = (
        ("actually_used", actually_used_skill_ids),
        ("retrieved", retrieved_skill_ids),
        ("family_library", family_skill_ids),
    )
    for label, values in evidence_tiers:
        candidates = _unique_same_family(values, family_id)
        if len(candidates) == 1:
            return SkillEvolBenchAttributionDecision(
                action="revise",
                reason=f"unique_same_family_target:{label}",
                target_skill_id=candidates[0],
            )
        if len(candidates) > 1:
            return SkillEvolBenchAttributionDecision(
                action="noop",
                reason=f"ambiguous_same_family_attribution:{label}",
            )

    return SkillEvolBenchAttributionDecision(
        action="noop",
        reason="no_supported_same_family_target",
    )


def install_skillevolbench_strategy_patch() -> None:
    """Install the EvoAgent strategy into a pinned external SkillEvolBench run.

    SkillEvolBench remains an optional external benchmark. Imports occur only
    when this function is called, so ``auto-evolving-agent`` does not take a
    package dependency on or redistribute the upstream benchmark.

    The pinned upstream ``LifelongRunner`` constructs ``BaselineRuntime`` via
    its classmethod ``build``. We wrap that factory in-process, preserve every
    upstream store/runtime/Harbor component, and replace only the evolution
    strategy object with the conservative EvoAgent policy above.
    """

    try:
        from skillevolbench.baselines import BaselineRuntime
        from skillevolbench.components.skill_author import PatchGenerationFailure
        from skillevolbench.strategies.base import (
            ApplyPatch,
            EvolutionStrategy,
            NoOp,
        )
    except ImportError as exc:  # pragma: no cover - external integration path
        raise RuntimeError(
            "SkillEvolBench is not importable. Run this bridge from the "
            "separately obtained pinned SkillEvolBench checkout."
        ) from exc

    if getattr(BaselineRuntime, "_evoagent_strategy_patch_installed", False):
        return

    original_descriptor = BaselineRuntime.__dict__["build"]
    original_build = original_descriptor.__func__

    class EvoAgentSkillEvolutionStrategy(EvolutionStrategy):
        name = "evoagent_unique_attribution"

        @staticmethod
        def _role(ctx) -> str:
            role = getattr(ctx.task, "role", "")
            return str(getattr(role, "value", role))

        @staticmethod
        def _retrieved_ids(ctx) -> tuple[str, ...]:
            retrieval = getattr(ctx, "pre_retrieval", None)
            skills = getattr(retrieval, "skills", ()) if retrieval else ()
            return tuple(
                str(getattr(item, "skill_id", ""))
                for item in skills
                if getattr(item, "skill_id", None)
            )

        def _decision(self, ctx) -> SkillEvolBenchAttributionDecision:
            family_id = str(getattr(ctx.task, "family_id", ""))
            family_records = tuple(self.library.skills_in_family(family_id))
            family_ids = tuple(
                str(getattr(item, "skill_id", ""))
                for item in family_records
                if getattr(item, "skill_id", None)
            )
            return decide_skillevolbench_skill_action(
                role=self._role(ctx),
                verifier_passed=bool(getattr(ctx.outcome, "verifier_passed", False)),
                family_id=family_id,
                family_has_seed=bool(self.library.has_seed_for(family_id)),
                actually_used_skill_ids=tuple(
                    str(item) for item in getattr(ctx, "skills_actually_used", ())
                ),
                retrieved_skill_ids=self._retrieved_ids(ctx),
                family_skill_ids=family_ids,
            )

        def _decide_impl(self, ctx):
            decision = self._decision(ctx)
            if decision.action == "noop":
                return NoOp(reason=f"evoagent:{decision.reason}")

            try:
                if decision.action == "induce":
                    patch = self.evolver.induce_skill(
                        family_id=str(ctx.task.family_id),
                        latent_skill_id=str(ctx.task.latent_skill_id),
                        compacted=ctx.compacted,
                        outcome=ctx.outcome,
                    )
                else:
                    target = decision.target_skill_id
                    if target is None:
                        return NoOp(reason="evoagent:missing_unique_target")
                    patch = self.evolver.propose(
                        library=self.library,
                        compacted=ctx.compacted,
                        outcome=ctx.outcome,
                        target_skill_ids=[target],
                        suggested_target_skill_ids=[target],
                        mode="minimal_edit",
                    )
            except PatchGenerationFailure as exc:
                self.event_store.record(
                    "evoagent_candidate_failed",
                    {
                        "task_id": str(getattr(ctx.task, "task_id", "?")),
                        "strategy": self.name,
                        "error_type": type(exc).__name__,
                    },
                )
                return NoOp(
                    reason=f"evoagent:patch_generation_failed:{type(exc).__name__}"
                )

            self.event_store.record_patch_proposed(patch, self.name)
            return ApplyPatch(patch=patch)

    def patched_build(cls, config, **kwargs):
        runtime = original_build(cls, config, **kwargs)
        runtime.strategy = EvoAgentSkillEvolutionStrategy(
            evolver=runtime.evolver,
            retriever=runtime.retriever,
            library=runtime.library,
            replay_store=runtime.replay_store,
            event_store=runtime.event_store,
            config=runtime.strategy_config,
        )
        return runtime

    BaselineRuntime.build = classmethod(patched_build)
    BaselineRuntime._evoagent_strategy_patch_installed = True


__all__ = [
    "SkillEvolBenchAttributionDecision",
    "decide_skillevolbench_skill_action",
    "install_skillevolbench_strategy_patch",
]
