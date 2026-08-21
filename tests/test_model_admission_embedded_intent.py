from __future__ import annotations

import json

import pytest

from evoagent.lab import ModelCandidateAdmissionLab
from evoagent.model_registry import (
    ModelAdmissionPackageManager,
    canonical_sha256,
)
from evoagent.training import ModelEvolutionPackageManager


def _rehash_outer(payload: dict) -> None:
    payload["package_hash"] = canonical_sha256(
        {
            key: value
            for key, value in payload.items()
            if key != "package_hash"
        }
    )


def test_admission_package_embeds_exact_governed_training_intent(tmp_path):
    lab = ModelCandidateAdmissionLab(
        tmp_path / "model-admission-lab",
        source_commit="e" * 40,
    )
    result = lab.run()

    admission = ModelAdmissionPackageManager().load_file(result.package_path)
    sidecar = ModelEvolutionPackageManager().load_file(
        lab.training_intent_package_path
    )

    assert admission.training_intent_package == sidecar
    assert admission.training_intent_package.package_hash == (
        admission.training_intent_package_hash
    )
    assert admission.training_intent_package_hash == (
        result.training_intent_package_hash
    )
    assert admission.candidate_manifest.base_model_id == (
        sidecar.ticket.base_model_id
    )
    assert admission.candidate_manifest.training_method == (
        sidecar.candidate.method
    )
    assert admission.candidate_manifest.evidence_manifest_hash == (
        sidecar.dataset.manifest_hash
    )
    assert admission.candidate_manifest.held_out_task_ids == tuple(
        task.task_id for task in sidecar.held_out_tasks
    )


def test_rehashed_outer_package_rejects_modified_embedded_intent(tmp_path):
    lab = ModelCandidateAdmissionLab(
        tmp_path / "model-admission-lab",
        source_commit="f" * 40,
    )
    lab.run()
    path = lab.package_path
    payload = json.loads(path.read_text(encoding="utf-8"))

    payload["training_intent_package"]["package_hash"] = "f" * 64
    _rehash_outer(payload)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        ModelAdmissionPackageManager().load_file(path)


def test_rehashed_embedded_budget_change_breaks_authorization_scope(tmp_path):
    lab = ModelCandidateAdmissionLab(
        tmp_path / "model-admission-lab",
        source_commit="1" * 40,
    )
    lab.run()
    path = lab.package_path
    payload = json.loads(path.read_text(encoding="utf-8"))

    intent = payload["training_intent_package"]
    intent["ticket"]["budget"]["max_rollouts"] = 63
    intent["candidate"]["task_spec"]["rollout_budget"] = 63
    intent["candidate"]["task_spec"]["runtime_config"][
        "rollout_budget"
    ] = 63
    intent["package_hash"] = canonical_sha256(
        {
            key: value
            for key, value in intent.items()
            if key != "package_hash"
        }
    )
    payload["training_intent_package_hash"] = intent["package_hash"]
    _rehash_outer(payload)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        ModelAdmissionPackageManager().load_file(path)
