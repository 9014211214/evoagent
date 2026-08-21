from __future__ import annotations

from evoagent.program.constraints import validate_hardened_program_policy
from evoagent.program.controller_hardened import (
    HardenedEvolutionProgramGate as _HardenedEvolutionProgramGate,
)
from evoagent.program.models import (
    AttributionReceipt,
    EvolutionProgramPolicy,
    ProgramLearningSignal,
)


class HardenedEvolutionProgramGate(_HardenedEvolutionProgramGate):
    """Final Gate that cannot be weakened through a rehashed policy."""

    def decide(self, **kwargs):
        validate_hardened_program_policy(kwargs["policy"])
        return super().decide(**kwargs)

    @staticmethod
    def _attribution_failure(
        policy: EvolutionProgramPolicy,
        signal: ProgramLearningSignal,
        attribution: AttributionReceipt,
    ) -> str | None:
        if (
            not attribution.independent
            or attribution.attributor_id == signal.evidence_producer_id
        ):
            return (
                "Attributor is not independent from the release evidence "
                "producer."
            )
        if len(attribution.supported_experiment_hashes) != 1:
            return (
                "Attribution must contain exactly one supported causal "
                "experiment."
            )
        return _HardenedEvolutionProgramGate._attribution_failure(
            policy,
            signal,
            attribution,
        )


__all__ = ["HardenedEvolutionProgramGate"]
