from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evoagent.local_rl.package import (
    LocalRLPackageManager,
    LocalRLPackageManifest,
)
from evoagent.model_registry.models import canonical_sha256, validate_safe_content


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SAFE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"


class ProgramLocalRLProjectionPackage(BaseModel):
    """Flat governed projection that recursively embeds the real native package."""

    model_config = ConfigDict(frozen=True)

    format_version: Literal["evoagent-program-local-rl-projection-v1"] = (
        "evoagent-program-local-rl-projection-v1"
    )
    projection_package_id: str = Field(pattern=_SAFE_ID_PATTERN)
    source_package: LocalRLPackageManifest
    source_package_hash: str = Field(pattern=_SHA256_PATTERN)
    local_rl_package_id: str = Field(pattern=_SAFE_ID_PATTERN)
    local_rl_package_hash: str = Field(pattern=_SHA256_PATTERN)
    local_rl_run_id: str = Field(pattern=_SAFE_ID_PATTERN)
    optimizer_config_hash: str = Field(pattern=_SHA256_PATTERN)
    training_task_set_hash: str = Field(pattern=_SHA256_PATTERN)
    heldout_task_set_hash: str = Field(pattern=_SHA256_PATTERN)
    initial_checkpoint_hash: str = Field(pattern=_SHA256_PATTERN)
    selected_checkpoint_hash: str = Field(pattern=_SHA256_PATTERN)
    optimizer_evidence_hash: str = Field(pattern=_SHA256_PATTERN)
    heldout_evaluation_hash: str = Field(pattern=_SHA256_PATTERN)
    iterations: int = Field(gt=0)
    rollouts: int = Field(gt=0)
    tokens: int = Field(ge=0)
    cost_usd: float = Field(ge=0.0)
    heldout_reward_delta: float = Field(gt=0.0)
    heldout_success_delta: float = Field(gt=0.0)
    unsafe_action_count: int = Field(ge=0)
    regression_count: int = Field(ge=0)
    native_package_hash_recomputed: Literal[True] = True
    optimizer_recomputed: Literal[True] = True
    heldout_evaluation_recomputed: Literal[True] = True
    training_heldout_disjoint: Literal[True] = True
    checkpoint_selection_recomputed: Literal[True] = True
    checkpoint_promotion_authorized: Literal[False] = False
    production_activation_authorized: Literal[False] = False
    production_deployment_authorized: Literal[False] = False
    projection_package_hash: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_package(self):
        if self.source_package_hash != self.source_package.package_hash:
            raise ValueError(
                "Program projection source hash differs from the embedded native package."
            )
        if self.training_task_set_hash == self.heldout_task_set_hash:
            raise ValueError(
                "Program projection training and held-out Task sets overlap."
            )
        if self.initial_checkpoint_hash == self.selected_checkpoint_hash:
            raise ValueError(
                "Program projection contains no actual policy update."
            )
        if self.unsafe_action_count != 0 or self.regression_count != 0:
            raise ValueError(
                "Program projection contains unsafe or regressing held-out evidence."
            )
        payload = self.model_dump(
            mode="json",
            exclude={"projection_package_hash"},
        )
        validate_safe_content(payload)
        if self.projection_package_hash != canonical_sha256(payload):
            raise ValueError("Program local-RL projection package hash mismatch.")
        return self


class ProgramLocalRLProjectionPackageError(ValueError):
    pass


class ProgramLocalRLProjectionPackageManager:
    """Derive every flat Program field from a verified real LocalRL package."""

    @staticmethod
    def _projection(package: LocalRLPackageManifest):
        from evoagent.program_rl.native_contract import (
            EvoagentLocalRLPackageProjector,
        )

        manager = LocalRLPackageManager()
        if manager.verify(package) is not True:
            raise ProgramLocalRLProjectionPackageError(
                "Native Local RL package verification did not pass."
            )
        return EvoagentLocalRLPackageProjector(manager).project(package)

    def build(
        self,
        source_package: LocalRLPackageManifest,
        *,
        projection_package_id: str,
    ) -> ProgramLocalRLProjectionPackage:
        projection = self._projection(source_package)
        payload = {
            "format_version": "evoagent-program-local-rl-projection-v1",
            "projection_package_id": projection_package_id,
            "source_package": source_package,
            "source_package_hash": source_package.package_hash,
            "local_rl_package_id": projection.local_rl_package_id,
            "local_rl_package_hash": projection.local_rl_package_hash,
            "local_rl_run_id": projection.local_rl_run_id,
            "optimizer_config_hash": projection.optimizer_config_hash,
            "training_task_set_hash": projection.training_task_set_hash,
            "heldout_task_set_hash": projection.heldout_task_set_hash,
            "initial_checkpoint_hash": projection.initial_checkpoint_hash,
            "selected_checkpoint_hash": projection.selected_checkpoint_hash,
            "optimizer_evidence_hash": projection.optimizer_evidence_hash,
            "heldout_evaluation_hash": projection.heldout_evaluation_hash,
            "iterations": projection.usage.iterations,
            "rollouts": projection.usage.rollouts,
            "tokens": projection.usage.tokens,
            "cost_usd": projection.usage.cost_usd,
            "heldout_reward_delta": projection.heldout_reward_delta,
            "heldout_success_delta": projection.heldout_success_delta,
            "unsafe_action_count": projection.unsafe_action_count,
            "regression_count": projection.regression_count,
            "native_package_hash_recomputed": True,
            "optimizer_recomputed": True,
            "heldout_evaluation_recomputed": True,
            "training_heldout_disjoint": True,
            "checkpoint_selection_recomputed": True,
            "checkpoint_promotion_authorized": False,
            "production_activation_authorized": False,
            "production_deployment_authorized": False,
        }
        package = ProgramLocalRLProjectionPackage(
            **payload,
            projection_package_hash=canonical_sha256(payload),
        )
        self.verify(package)
        return package

    @classmethod
    def verify(cls, package: ProgramLocalRLProjectionPackage) -> bool:
        if type(package) is not ProgramLocalRLProjectionPackage:
            raise TypeError(
                "Program projection requires the exact projection package type."
            )
        if type(package.source_package) is not LocalRLPackageManifest:
            raise TypeError(
                "Program projection requires the exact native LocalRL package type."
            )
        projection = cls._projection(package.source_package)
        expected = {
            "source_package_hash": package.source_package.package_hash,
            "local_rl_package_id": projection.local_rl_package_id,
            "local_rl_package_hash": projection.local_rl_package_hash,
            "local_rl_run_id": projection.local_rl_run_id,
            "optimizer_config_hash": projection.optimizer_config_hash,
            "training_task_set_hash": projection.training_task_set_hash,
            "heldout_task_set_hash": projection.heldout_task_set_hash,
            "initial_checkpoint_hash": projection.initial_checkpoint_hash,
            "selected_checkpoint_hash": projection.selected_checkpoint_hash,
            "optimizer_evidence_hash": projection.optimizer_evidence_hash,
            "heldout_evaluation_hash": projection.heldout_evaluation_hash,
            "iterations": projection.usage.iterations,
            "rollouts": projection.usage.rollouts,
            "tokens": projection.usage.tokens,
            "cost_usd": projection.usage.cost_usd,
            "heldout_reward_delta": projection.heldout_reward_delta,
            "heldout_success_delta": projection.heldout_success_delta,
            "unsafe_action_count": projection.unsafe_action_count,
            "regression_count": projection.regression_count,
        }
        if any(getattr(package, name) != value for name, value in expected.items()):
            raise ProgramLocalRLProjectionPackageError(
                "Program projection fields differ from recomputed native evidence."
            )
        if (
            package.checkpoint_promotion_authorized
            or package.production_activation_authorized
            or package.production_deployment_authorized
        ):
            raise ProgramLocalRLProjectionPackageError(
                "Program projection widens its evidence-only authority."
            )
        payload = package.model_dump(
            mode="json",
            exclude={"projection_package_hash"},
        )
        if package.projection_package_hash != canonical_sha256(payload):
            raise ProgramLocalRLProjectionPackageError(
                "Program local-RL projection package hash mismatch."
            )
        return True


__all__ = [
    "ProgramLocalRLProjectionPackage",
    "ProgramLocalRLProjectionPackageError",
    "ProgramLocalRLProjectionPackageManager",
]
