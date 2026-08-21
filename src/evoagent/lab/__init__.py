from .automatic_local_tool_final import (
    AutomaticLocalToolEvolutionLab,
    AutomaticLocalToolEvolutionResult,
    AutomaticLocalToolPhase,
    IdempotentJsonlTraceStore,
)
from .benchmark_evidence import (
    AuthoritativeBenchmarkEvidenceLab,
    AuthoritativeBenchmarkEvidenceLabResult,
)
from .champion_promotion import (
    BenchmarkGatedChampionLab,
    BenchmarkGatedChampionLabResult,
)
from .closed_loop_supervisor_hardened import (
    ClosedLoopEvolutionLabResult,
    ClosedLoopEvolutionSupervisorLab,
)
from .cross_layer import (
    ExecutableCrossLayerAttributionLab,
    ExecutableCrossLayerMatrixResult,
    ExecutableLayerDispatchResult,
)
from .evolution_program_hardened import (
    MultiGenerationEvolutionLabResult,
    MultiGenerationEvolutionProgramLab,
)
from .local_policy_promotion_final import (
    AcceptedLocalPolicyPromotionLab,
    LocalPolicyPromotionLabResult,
)
from .model_candidate_admission import (
    ModelCandidateAdmissionLab,
    ModelCandidateAdmissionLabResult,
)
from .model_evolution import (
    ExecutableModelEvidenceCase,
    GovernedModelEvolutionLab,
    GovernedModelEvolutionResult,
)
from .models import (
    ReferenceCaseResult,
    ReferenceEvaluationResult,
    ReferenceLabPhase,
    ReferenceLabResult,
)
from .program_local_rl_acceptance_final import (
    ProgramLocalRLAcceptanceLab,
    ProgramLocalRLAcceptanceLabResult,
    ProgramLocalRLAcceptedEvidenceBundle,
    ProgramLocalRLAcceptedEvidenceError,
    ProgramLocalRLAcceptedEvidenceManager,
)
from .release_control import (
    ReleaseScenarioResult,
    ShadowCanaryReleaseLab,
    ShadowCanaryReleaseLabResult,
)
from .runtime import ReferenceDecisionRuntime
from .service import (
    DEFAULT_THIRD_PARTY_LOCK_HASH,
    ReferenceEvolutionLab,
    ReferenceLabError,
)


_INTEGRATED_EXPORTS = {
    "IntegratedMultiTrackEvolutionLab",
    "IntegratedMultiTrackLabResult",
}


def __getattr__(name: str):
    if name in _INTEGRATED_EXPORTS:
        from .integrated_multitrack_final import (
            IntegratedMultiTrackEvolutionLab,
            IntegratedMultiTrackLabResult,
        )

        return {
            "IntegratedMultiTrackEvolutionLab": (
                IntegratedMultiTrackEvolutionLab
            ),
            "IntegratedMultiTrackLabResult": IntegratedMultiTrackLabResult,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted({*globals(), *_INTEGRATED_EXPORTS})


__all__ = [
    "AcceptedLocalPolicyPromotionLab",
    "AuthoritativeBenchmarkEvidenceLab",
    "AuthoritativeBenchmarkEvidenceLabResult",
    "AutomaticLocalToolEvolutionLab",
    "AutomaticLocalToolEvolutionResult",
    "AutomaticLocalToolPhase",
    "BenchmarkGatedChampionLab",
    "BenchmarkGatedChampionLabResult",
    "ClosedLoopEvolutionLabResult",
    "ClosedLoopEvolutionSupervisorLab",
    "DEFAULT_THIRD_PARTY_LOCK_HASH",
    "ExecutableCrossLayerAttributionLab",
    "ExecutableCrossLayerMatrixResult",
    "ExecutableLayerDispatchResult",
    "ExecutableModelEvidenceCase",
    "GovernedModelEvolutionLab",
    "GovernedModelEvolutionResult",
    "IdempotentJsonlTraceStore",
    "IntegratedMultiTrackEvolutionLab",
    "IntegratedMultiTrackLabResult",
    "LocalPolicyPromotionLabResult",
    "ModelCandidateAdmissionLab",
    "ModelCandidateAdmissionLabResult",
    "MultiGenerationEvolutionLabResult",
    "MultiGenerationEvolutionProgramLab",
    "ProgramLocalRLAcceptanceLab",
    "ProgramLocalRLAcceptanceLabResult",
    "ProgramLocalRLAcceptedEvidenceBundle",
    "ProgramLocalRLAcceptedEvidenceError",
    "ProgramLocalRLAcceptedEvidenceManager",
    "ReferenceCaseResult",
    "ReferenceDecisionRuntime",
    "ReferenceEvaluationResult",
    "ReferenceEvolutionLab",
    "ReferenceLabError",
    "ReferenceLabPhase",
    "ReferenceLabResult",
    "ReleaseScenarioResult",
    "ShadowCanaryReleaseLab",
    "ShadowCanaryReleaseLabResult",
]
