from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from evoagent.program.hashing import program_payload_hash
from evoagent.program_rl.models import (
    ProgramLocalRLAuthorization,
    ProgramLocalRLBindingPackage,
    ProgramLocalRLIntent,
    ProgramLocalRLResultBinding,
)


class ProgramLocalRLPackageError(ValueError):
    pass


class ProgramLocalRLPackageManager:
    """Build and verify an offline optimizer binding without promotion rights."""

    def build(
        self,
        *,
        package_id: str,
        framework_version: str,
        source_repository: str,
        source_commit: str,
        third_party_lock_hash: str,
        intent: ProgramLocalRLIntent,
        authorization: ProgramLocalRLAuthorization,
        result: ProgramLocalRLResultBinding,
        created_at: datetime,
    ) -> ProgramLocalRLBindingPackage:
        payload = {
            "package_id": package_id,
            "framework_version": framework_version,
            "source_repository": source_repository,
            "source_commit": source_commit,
            "third_party_lock_hash": third_party_lock_hash,
            "intent": intent,
            "authorization": authorization,
            "result": result,
            "created_at": created_at,
            "external_model_call_performed_by_evoagent": False,
            "foundation_model_weights_updated": False,
            "checkpoint_promotion_performed": False,
            "production_activation_performed": False,
            "external_rollout_performed_by_evoagent": False,
            "upload_performed": False,
            "official_benchmark_claimed": False,
        }
        package = ProgramLocalRLBindingPackage(
            **payload,
            package_hash=program_payload_hash(payload),
        )
        self.verify(package)
        return package

    @staticmethod
    def verify(package: ProgramLocalRLBindingPackage) -> bool:
        intent = package.intent
        authorization = package.authorization
        result = package.result
        if authorization.authorized_by in {
            *intent.governed_actor_ids,
            intent.created_by,
        }:
            raise ProgramLocalRLPackageError(
                "Local-RL authorizer overlaps Program or intent actors."
            )
        if result.executed_by in {
            *intent.governed_actor_ids,
            intent.created_by,
            authorization.authorized_by,
        }:
            raise ProgramLocalRLPackageError(
                "Local-RL executor overlaps governed, intent or authorization actors."
            )
        if result.started_at < authorization.authorized_at:
            raise ProgramLocalRLPackageError(
                "Local-RL execution predates explicit optimizer authorization."
            )
        if (
            authorization.expires_at is not None
            and result.completed_at > authorization.expires_at
        ):
            raise ProgramLocalRLPackageError(
                "Local-RL execution exceeded authorization expiry."
            )
        budget = authorization.budget
        usage = result.usage
        if (
            usage.iterations > budget.max_iterations
            or usage.rollouts > budget.max_rollouts
            or usage.tokens > budget.max_tokens
            or usage.cost_usd > budget.max_cost_usd + 1e-12
        ):
            raise ProgramLocalRLPackageError(
                "Local-RL package exceeds its immutable execution budget."
            )
        if (
            result.heldout_reward_delta <= 0.0
            or result.heldout_success_delta <= 0.0
            or result.unsafe_action_count != 0
            or result.regression_count != 0
        ):
            raise ProgramLocalRLPackageError(
                "Local-RL package lacks strict safe held-out improvement."
            )
        if (
            package.external_model_call_performed_by_evoagent
            or package.foundation_model_weights_updated
            or package.checkpoint_promotion_performed
            or package.production_activation_performed
            or package.external_rollout_performed_by_evoagent
            or package.upload_performed
            or package.official_benchmark_claimed
        ):
            raise ProgramLocalRLPackageError(
                "Local-RL binding package widens its offline non-promotion boundary."
            )
        expected_hash = program_payload_hash(
            package.model_dump(mode="json", exclude={"package_hash"})
        )
        if package.package_hash != expected_hash:
            raise ProgramLocalRLPackageError(
                "Program local-RL binding package hash mismatch."
            )
        return True

    def export_file(
        self,
        package: ProgramLocalRLBindingPackage,
        path: str | Path,
    ) -> Path:
        self.verify(package)
        target = Path(path).expanduser()
        if target.exists() and target.is_symlink():
            raise ProgramLocalRLPackageError(
                "Program local-RL package target must not be a symlink."
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        encoded = (
            json.dumps(package.model_dump(mode="json"), sort_keys=True, indent=2)
            + "\n"
        ).encode("utf-8")
        if target.exists():
            if target.read_bytes() != encoded:
                raise ProgramLocalRLPackageError(
                    "Existing Program local-RL package differs from immutable evidence."
                )
        else:
            target.write_bytes(encoded)
        return target

    def load_file(self, path: str | Path) -> ProgramLocalRLBindingPackage:
        target = Path(path).expanduser()
        if target.is_symlink() or not target.is_file():
            raise ProgramLocalRLPackageError(
                "Program local-RL package path must be a regular file."
            )
        package = ProgramLocalRLBindingPackage.model_validate_json(
            target.read_text(encoding="utf-8")
        )
        self.verify(package)
        return package


__all__ = [
    "ProgramLocalRLPackageError",
    "ProgramLocalRLPackageManager",
]
