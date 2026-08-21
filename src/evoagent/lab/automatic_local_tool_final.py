from __future__ import annotations

from evoagent.skills import build_controlled_document_skill_v1

from .automatic_local_tool import (
    AutomaticLocalToolEvolutionLab as _BaseAutomaticLocalToolEvolutionLab,
    AutomaticLocalToolEvolutionResult,
    AutomaticLocalToolPhase,
    IdempotentJsonlTraceStore,
)


class AutomaticLocalToolEvolutionLab(_BaseAutomaticLocalToolEvolutionLab):
    """Final controlled Skill Lab sharing one public immutable S0 contract."""

    @staticmethod
    def _initial_skill():
        return build_controlled_document_skill_v1()


__all__ = [
    "AutomaticLocalToolEvolutionLab",
    "AutomaticLocalToolEvolutionResult",
    "AutomaticLocalToolPhase",
    "IdempotentJsonlTraceStore",
]
