from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from evoagent.benchmarks import BenchmarkManifest, ResourceBudget
from evoagent.runs import (
    ExternalSignatureReference,
    ReproducibleRunBundleManager,
    ReproducibleRunManifest,
    ReproducibleRunSpec,
    RunArtifactKind,
    RunArtifactRecord,
    RunArtifactSource,
    RunBundleError,
    RunEnvironmentSpec,
    RunStatus,
)
from evoagent.runs.bundle import _manifest_hash


def run_spec() -> ReproducibleRunSpec:
    return ReproducibleRunSpec(
        run_id="terminal-bench-dev-001",
        created_at=datetime(2026, 8, 9, 23, 50, tzinfo=timezone.utc),
        framework_version="1.0.0rc1",
        source_repository="https://github.com/9014211214/evoagent",
        source_commit="e" * 40,
        dirty_worktree=False,
        system_name="evoagent-development",
        initial_model_id="public/model-v0",
        snapshot_ids=("A0", "A1"),
        benchmark=BenchmarkManifest(
            dataset_ref="terminal-bench/terminal-bench-2-1",
            revision="pinned-development-revision",
            split="development",
            task_ids=("task-1", "task-2"),
            trials_per_task=1,
        ),
        evolution_budget=ResourceBudget(max_task_trials=10, max_tokens=1000),
        evaluation_budget=ResourceBudget(max_task_trials=2, max_tokens=500),
        command=("harbor", "run", "--dry-run"),
        environment=RunEnvironmentSpec(
            python_version="3.11.15",
            platform="linux-x86_64",
            packages={"auto-evolving-agent": "1.0.0rc1"},
            tools={"harbor": "pinned-by-environment"},
            network_access=False,
        ),
        random_seeds={"agent": 7, "environment": 11},
        provenance=("public benchmark", "synthetic development artifacts"),
        status=RunStatus.DRY_RUN,
    )


def build_bundle(tmp_path: Path, name: str = "bundle"):
    config = tmp_path / f"{name}-config.json"
    results = tmp_path / f"{name}-results.json"
    config.write_text('{"temperature":0}', encoding="utf-8")
    results.write_text('{"task-1":1.0,"task-2":0.5}', encoding="utf-8")
    manager = ReproducibleRunBundleManager()
    bundle = tmp_path / name
    manifest = manager.build(
        spec=run_spec(),
        artifact_sources=(
            RunArtifactSource(
                logical_name="config.json",
                kind=RunArtifactKind.CONFIG,
                source_path=str(config),
                media_type="application/json",
            ),
            RunArtifactSource(
                logical_name="results.json",
                kind=RunArtifactKind.RESULTS,
                source_path=str(results),
                media_type="application/json",
            ),
        ),
        output_directory=bundle,
    )
    return manager, bundle, manifest


def test_identical_inputs_produce_identical_manifest_hash(tmp_path):
    manager_a, bundle_a, manifest_a = build_bundle(tmp_path, "bundle-a")
    manager_b, bundle_b, manifest_b = build_bundle(tmp_path, "bundle-b")

    assert manifest_a.manifest_hash == manifest_b.manifest_hash
    assert manager_a.verify(bundle_a).artifacts_verified == 2
    assert manager_b.verify(bundle_b).manifest_hash == manifest_b.manifest_hash


def test_artifact_extra_file_and_symlink_tampering_are_rejected(tmp_path):
    manager, bundle, manifest = build_bundle(tmp_path)
    artifact = bundle / manifest.artifacts[0].relative_path
    artifact.write_text("modified", encoding="utf-8")
    with pytest.raises(RunBundleError, match="mismatch"):
        manager.verify(bundle)

    manager, bundle, _ = build_bundle(tmp_path, "extra")
    (bundle / "unexpected.txt").write_text("extra", encoding="utf-8")
    with pytest.raises(RunBundleError, match="file set"):
        manager.verify(bundle)

    manager, bundle, _ = build_bundle(tmp_path, "symlink")
    target = bundle / "artifacts" / "target.txt"
    target.write_text("target", encoding="utf-8")
    link = bundle / "artifacts" / "link.txt"
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("Symlinks are unavailable in this environment.")
    with pytest.raises(RunBundleError, match="Symlink"):
        manager.verify(bundle)


def test_secret_and_symlink_sources_are_blocked(tmp_path):
    manager = ReproducibleRunBundleManager()
    secret = tmp_path / "secret.txt"
    secret.write_text("sk-abcdefghijklmnop", encoding="utf-8")
    with pytest.raises(RunBundleError, match="secret"):
        manager.build(
            spec=run_spec(),
            artifact_sources=(
                RunArtifactSource(
                    logical_name="secret.txt",
                    kind=RunArtifactKind.LOG,
                    source_path=str(secret),
                ),
            ),
            output_directory=tmp_path / "secret-bundle",
        )

    regular = tmp_path / "regular.txt"
    regular.write_text("public content", encoding="utf-8")
    link = tmp_path / "source-link.txt"
    try:
        os.symlink(regular, link)
    except OSError:
        pytest.skip("Symlinks are unavailable in this environment.")
    with pytest.raises(RunBundleError, match="non-symlink"):
        manager.build(
            spec=run_spec(),
            artifact_sources=(
                RunArtifactSource(
                    logical_name="linked.txt",
                    kind=RunArtifactKind.OTHER,
                    source_path=str(link),
                ),
            ),
            output_directory=tmp_path / "linked-bundle",
        )


def test_external_checkpoint_detects_fully_rehashed_bundle(tmp_path):
    manager, bundle, manifest = build_bundle(tmp_path)
    checkpoint = manager.checkpoint(bundle)

    artifact_path = bundle / manifest.artifacts[0].relative_path
    changed = b'{"temperature":1}'
    artifact_path.write_bytes(changed)

    payload = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    payload["artifacts"][0]["size_bytes"] = len(changed)
    payload["artifacts"][0]["sha256"] = hashlib.sha256(changed).hexdigest()
    provisional = ReproducibleRunManifest.model_validate(
        {**payload, "manifest_hash": "0" * 64}
    )
    updated_artifacts = tuple(
        RunArtifactRecord.model_validate(item) for item in payload["artifacts"]
    )
    payload["manifest_hash"] = _manifest_hash(provisional.spec, updated_artifacts)
    (bundle / "manifest.json").write_text(
        json.dumps(payload, sort_keys=True), encoding="utf-8"
    )

    assert manager.verify(bundle).manifest_hash == payload["manifest_hash"]
    with pytest.raises(RunBundleError, match="external checkpoint"):
        manager.verify(bundle, checkpoint=checkpoint)


def test_external_signature_reference_is_bound_but_not_claimed_verified(tmp_path):
    manager, bundle, manifest = build_bundle(tmp_path)
    with pytest.raises(RunBundleError, match="not bound"):
        manager.attach_external_signature(
            bundle,
            ExternalSignatureReference(
                signed_manifest_hash="0" * 64,
                algorithm="sigstore-reference",
                signer_identity="test@example.invalid",
                signature_uri="file:///external/signature.sig",
            ),
        )

    manager.attach_external_signature(
        bundle,
        ExternalSignatureReference(
            signed_manifest_hash=manifest.manifest_hash,
            algorithm="sigstore-reference",
            signer_identity="test@example.invalid",
            signature_uri="file:///external/signature.sig",
            verification_instructions="Verify outside evoagent with the named signer policy.",
        ),
    )
    result = manager.verify(bundle)
    assert result.external_signature_reference_present is True
    assert result.external_signature_cryptographically_verified is False


def test_externally_validated_bundle_requires_atomic_signature_and_evidence(tmp_path):
    results = tmp_path / "results.json"
    lock = tmp_path / "third-party-lock.json"
    results.write_text('{"task-1":1.0,"task-2":1.0}', encoding="utf-8")
    lock.write_text('{"lock":"pinned"}', encoding="utf-8")
    sources = (
        RunArtifactSource(
            logical_name="results.json",
            kind=RunArtifactKind.RESULTS,
            source_path=str(results),
            media_type="application/json",
        ),
        RunArtifactSource(
            logical_name="third-party-lock.json",
            kind=RunArtifactKind.THIRD_PARTY_LOCK,
            source_path=str(lock),
            media_type="application/json",
        ),
    )
    spec = run_spec().model_copy(
        update={
            "status": RunStatus.EXTERNALLY_VALIDATED,
            "external_validation_reference": "https://validator.example.invalid/result/1",
            "external_signature_required": True,
        }
    )
    manager = ReproducibleRunBundleManager()
    preview = manager.preview(spec=spec, artifact_sources=sources)

    with pytest.raises(RunBundleError, match="requires an external signature"):
        manager.build(
            spec=spec,
            artifact_sources=sources,
            output_directory=tmp_path / "unsigned",
        )

    reference = ExternalSignatureReference(
        signed_manifest_hash=preview.manifest_hash,
        algorithm="sigstore-reference",
        signer_identity="validator@example.invalid",
        signature_uri="https://validator.example.invalid/signature/1",
    )
    bundle = tmp_path / "validated"
    manifest = manager.build(
        spec=spec,
        artifact_sources=sources,
        output_directory=bundle,
        external_signature_reference=reference,
    )
    assert manifest.manifest_hash == preview.manifest_hash
    assert manager.verify(bundle).external_signature_reference_present is True

    (bundle / "external-signature.json").unlink()
    with pytest.raises(RunBundleError, match="requires an external signature"):
        manager.verify(bundle)


def test_path_traversal_and_naive_external_validation_are_rejected():
    with pytest.raises(ValueError):
        RunArtifactRecord(
            logical_name="bad",
            kind=RunArtifactKind.OTHER,
            relative_path="artifacts/../escape",
            media_type="text/plain",
            size_bytes=0,
            sha256="0" * 64,
        )

    values = run_spec().model_dump()
    values["created_at"] = datetime(2026, 8, 9, 23, 50)
    with pytest.raises(ValueError, match="timezone"):
        ReproducibleRunSpec.model_validate(values)

    values = run_spec().model_dump()
    values["status"] = RunStatus.EXTERNALLY_VALIDATED
    values["external_validation_reference"] = "https://validator.example.invalid/result/1"
    values["external_signature_required"] = True
    values["dirty_worktree"] = True
    with pytest.raises(ValueError, match="dirty worktree"):
        ReproducibleRunSpec.model_validate(values)
