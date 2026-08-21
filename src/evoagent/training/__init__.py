from .agentic_rl import AgenticRLPlanner, DryRunAgenticRLBackend
from .evidence import (
    ModelEvidenceDatasetError,
    ModelEvidenceDatasetManager,
    ModelEvidenceDatasetManifest,
    ModelEvidenceExample,
    ObservableTrajectoryRecord,
    PreferenceTrajectoryPair,
    ReplaySeedRecord,
    SupervisedTrajectoryExample,
    canonical_sha256,
)
from .ml_intern import MLInternCLIAdapter
from .models import (
    AgenticRLEnvironmentSpec,
    AgenticRLTaskSpec,
    DatasetSignals,
    MetricTarget,
    MLInternTaskSpec,
    ModelCandidate,
    ModelImprovementTicket,
    RewardComponent,
    RewardSpec,
    RLAlgorithm,
    TrainingBudget,
    TrainingMethod,
    TrainingPlan,
)
from .orchestrator import DryRunMLInternBackend, ModelEvolutionBackend, ModelEvolutionOrchestrator
from .strategy import NoTrainingStrategyError, TrainingStrategySelector
from .ticket import ModelTicketFactory


def __getattr__(name: str):
    if name in {
        "ModelEvolutionPackageError",
        "ModelEvolutionPackageManager",
        "ModelEvolutionPackageManifest",
    }:
        from .package import (
            ModelEvolutionPackageError,
            ModelEvolutionPackageManager,
            ModelEvolutionPackageManifest,
        )

        return {
            "ModelEvolutionPackageError": ModelEvolutionPackageError,
            "ModelEvolutionPackageManager": ModelEvolutionPackageManager,
            "ModelEvolutionPackageManifest": ModelEvolutionPackageManifest,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AgenticRLEnvironmentSpec",
    "AgenticRLPlanner",
    "AgenticRLTaskSpec",
    "DatasetSignals",
    "DryRunAgenticRLBackend",
    "DryRunMLInternBackend",
    "MetricTarget",
    "MLInternCLIAdapter",
    "MLInternTaskSpec",
    "ModelCandidate",
    "ModelEvidenceDatasetError",
    "ModelEvidenceDatasetManager",
    "ModelEvidenceDatasetManifest",
    "ModelEvidenceExample",
    "ModelEvolutionBackend",
    "ModelEvolutionOrchestrator",
    "ModelEvolutionPackageError",
    "ModelEvolutionPackageManager",
    "ModelEvolutionPackageManifest",
    "ModelImprovementTicket",
    "ModelTicketFactory",
    "NoTrainingStrategyError",
    "ObservableTrajectoryRecord",
    "PreferenceTrajectoryPair",
    "ReplaySeedRecord",
    "RewardComponent",
    "RewardSpec",
    "RLAlgorithm",
    "SupervisedTrajectoryExample",
    "TrainingBudget",
    "TrainingMethod",
    "TrainingPlan",
    "TrainingStrategySelector",
    "canonical_sha256",
]
