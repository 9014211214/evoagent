from datetime import timedelta

from evoagent.lab.release_control import ShadowCanaryReleaseLab
from evoagent.release import ReleaseEvidencePackageManager

from .evolution_program import (
    MultiGenerationEvolutionLabResult,
    MultiGenerationEvolutionProgramLab as _BaseProgramLab,
)


class MultiGenerationEvolutionProgramLab(_BaseProgramLab):
    """Bind decision/planning authority and derive time after release inputs."""

    PLANNER = _BaseProgramLab.DECISION_ACTOR

    def run(self) -> MultiGenerationEvolutionLabResult:
        release_lab = ShadowCanaryReleaseLab(
            self.release_root,
            source_commit=self.source_commit,
            source_repository=self.source_repository,
        )
        release_result = release_lab.run()
        release_manager = ReleaseEvidencePackageManager()
        drift_package = release_manager.load_file(
            release_result.drift.package_path
        )
        passing_package = release_manager.load_file(
            release_result.passing.package_path
        )
        self.START = max(
            drift_package.created_at,
            passing_package.created_at,
        ) + timedelta(milliseconds=1)
        return super().run()


__all__ = [
    "MultiGenerationEvolutionLabResult",
    "MultiGenerationEvolutionProgramLab",
]
