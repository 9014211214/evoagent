from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from evoagent.benchmarks import BenchmarkManifest, ResourceBudget
from evoagent.runs import (
    ReproducibleRunBundleManager,
    ReproducibleRunSpec,
    RunArtifactKind,
    RunArtifactSource,
    RunEnvironmentSpec,
    RunStatus,
)

with TemporaryDirectory() as directory:
    root = Path(directory)
    results = root / "results.json"
    results.write_text('{"task-a":1.0,"task-b":0.5}', encoding="utf-8")

    manager = ReproducibleRunBundleManager()
    bundle = root / "terminal-bench-development-run"
    manifest = manager.build(
        spec=ReproducibleRunSpec(
            run_id="terminal-bench-development-run",
            created_at=datetime(2026, 8, 9, 23, 55, tzinfo=timezone.utc),
            framework_version="1.5.0",
            source_repository="https://github.com/9014211214/evoagent",
            source_commit="e" * 40,
            system_name="evoagent-development",
            initial_model_id="public/model-v0",
            snapshot_ids=("A0", "A1"),
            benchmark=BenchmarkManifest(
                dataset_ref="terminal-bench/terminal-bench-2-1",
                revision="pinned-development-revision",
                split="development",
                task_ids=("task-a", "task-b"),
            ),
            evolution_budget=ResourceBudget(max_task_trials=10, max_tokens=1000),
            evaluation_budget=ResourceBudget(max_task_trials=2, max_tokens=500),
            command=("harbor", "run", "--development-plan-only"),
            environment=RunEnvironmentSpec(
                python_version="3.11",
                platform="linux-x86_64",
                packages={"auto-evolving-agent": "1.5.0"},
                network_access=False,
            ),
            random_seeds={"agent": 7},
            provenance=("public benchmark reference", "synthetic result artifact"),
            status=RunStatus.DRY_RUN,
        ),
        artifact_sources=(
            RunArtifactSource(
                logical_name="results.json",
                kind=RunArtifactKind.RESULTS,
                source_path=str(results),
                media_type="application/json",
            ),
        ),
        output_directory=bundle,
    )
    checkpoint = manager.checkpoint(bundle)
    verified = manager.verify(bundle, checkpoint=checkpoint)

    print("manifest hash:", manifest.manifest_hash)
    print("verified:", verified.verified)
    print("artifacts verified:", verified.artifacts_verified)
    print("external checkpoint matched:", verified.external_checkpoint_matched)
    print("official benchmark claimed:", False)
