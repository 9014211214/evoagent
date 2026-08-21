from __future__ import annotations

from datetime import datetime

from evoagent.program.hashing import program_payload_hash
from evoagent.program_rl.schema_attestation import NativeLocalRLProjectionSpec


_PROGRAM_PROJECTION_PATHS = {
    "local_rl_package_id": ("local_rl_package_id",),
    "local_rl_package_hash": ("local_rl_package_hash",),
    "local_rl_run_id": ("local_rl_run_id",),
    "optimizer_config_hash": ("optimizer_config_hash",),
    "training_task_set_hash": ("training_task_set_hash",),
    "heldout_task_set_hash": ("heldout_task_set_hash",),
    "initial_checkpoint_hash": ("initial_checkpoint_hash",),
    "selected_checkpoint_hash": ("selected_checkpoint_hash",),
    "optimizer_evidence_hash": ("optimizer_evidence_hash",),
    "heldout_evaluation_hash": ("heldout_evaluation_hash",),
    "iterations": ("iterations",),
    "rollouts": ("rollouts",),
    "tokens": ("tokens",),
    "cost_usd": ("cost_usd",),
    "heldout_reward_delta": ("heldout_reward_delta",),
    "heldout_success_delta": ("heldout_success_delta",),
    "unsafe_action_count": ("unsafe_action_count",),
    "regression_count": ("regression_count",),
}


def build_program_local_rl_projection_spec(
    *,
    created_by: str,
    created_at: datetime,
    spec_id: str = "native-local-rl-projection-spec:evoagent-program-v1",
) -> NativeLocalRLProjectionSpec:
    payload = {
        "spec_id": spec_id,
        "schema_name": "evoagent-program-local-rl-projection",
        "schema_version": "1.0",
        "paths": _PROGRAM_PROJECTION_PATHS,
        "created_by": created_by,
        "created_at": created_at,
    }
    return NativeLocalRLProjectionSpec(
        **payload,
        spec_hash=program_payload_hash(payload),
    )


__all__ = ["build_program_local_rl_projection_spec"]
