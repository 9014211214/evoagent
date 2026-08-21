from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from evoagent.benchmarks import BenchmarkManifest, ResourceBudget
from evoagent.cli import main
from evoagent.runs import (
    ReproducibleRunBundleManager,
    ReproducibleRunSpec,
    RunArtifactKind,
    RunArtifactSource,
    RunEnvironmentSpec,
    RunStatus,
)


ROOT = Path(__file__).resolve().parents[1]


def file_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def build_bundle(tmp_path: Path) -> Path:
    artifact = tmp_path / "results.json"
    artifact.write_text('{"task":1.0}', encoding="utf-8")
    bundle = tmp_path / "run-bundle"
    ReproducibleRunBundleManager().build(
        spec=ReproducibleRunSpec(
            run_id="release-cli-test",
            created_at=datetime(2026, 8, 9, 23, 55, tzinfo=timezone.utc),
            framework_version="1.0.0",
            source_repository="https://github.com/9014211214/evoagent",
            source_commit="f" * 40,
            system_name="release-cli-test",
            initial_model_id="public/model-v0",
            snapshot_ids=("A0",),
            benchmark=BenchmarkManifest(
                dataset_ref="synthetic/public",
                revision="v1",
                split="held-out",
                task_ids=("task",),
            ),
            evolution_budget=ResourceBudget(max_task_trials=1),
            evaluation_budget=ResourceBudget(max_task_trials=1),
            command=("evoagent", "run", "verify"),
            environment=RunEnvironmentSpec(
                python_version="3.11",
                platform="linux-x86_64",
            ),
            status=RunStatus.DRY_RUN,
        ),
        artifact_sources=(
            RunArtifactSource(
                logical_name="results.json",
                kind=RunArtifactKind.RESULTS,
                source_path=str(artifact),
                media_type="application/json",
            ),
        ),
        output_directory=bundle,
    )
    return bundle


def test_run_cli_checkpoint_show_and_verify_are_read_only(tmp_path, capsys):
    bundle = build_bundle(tmp_path)
    before = file_hashes(bundle)
    checkpoint = tmp_path / "run-checkpoint.json"

    assert main(
        ["run", "checkpoint", "--bundle", str(bundle), "--out", str(checkpoint)]
    ) == 0
    checkpoint_result = json.loads(capsys.readouterr().out)
    assert checkpoint_result["manifest_hash"]

    assert main(
        [
            "run",
            "verify",
            "--bundle",
            str(bundle),
            "--checkpoint",
            str(checkpoint),
        ]
    ) == 0
    verification = json.loads(capsys.readouterr().out)
    assert verification["verified"] is True
    assert verification["external_checkpoint_matched"] is True

    assert main(["run", "show", "--bundle", str(bundle)]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["manifest"]["spec"]["run_id"] == "release-cli-test"
    assert file_hashes(bundle) == before


def test_compliance_cli_show_and_verify(tmp_path, capsys):
    lock = ROOT / "THIRD_PARTY_LOCK.json"
    notices = ROOT / "THIRD_PARTY_NOTICES.md"

    assert main(["compliance", "show", "--lock", str(lock)]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert len(shown["components"]) == 5

    assert main(
        [
            "compliance",
            "verify",
            "--lock",
            str(lock),
            "--notices",
            str(notices),
        ]
    ) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["verified"] is True
    assert verified["components_verified"] == 5
