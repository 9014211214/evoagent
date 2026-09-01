from __future__ import annotations

from pathlib import Path
import shutil
from types import SimpleNamespace
from typing import Any

import pytest


pytest.importorskip("seagym", reason="the optional pinned SEAGym dependency is not installed")

from seagym.logging import ArtifactLayout
from seagym.trainers import ExecutionEngine, TrainerState

from seagym_evoagent.baseline import EvoAgentSEAGymBaseline


class _NoNetworkClient:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, **_kwargs: Any) -> None:
        self.calls += 1
        raise AssertionError("checkpoint integration must not call a model")


def _engine(
    *,
    run_dir: Path,
    baseline: EvoAgentSEAGymBaseline,
    baseline_state: Any,
) -> ExecutionEngine:
    layout = ArtifactLayout.from_run_dir(run_dir)
    context = SimpleNamespace(
        config=SimpleNamespace(
            experiment_id="evoagent-seagym-pilot",
            run_dir=run_dir,
            runtime_scheduling=SimpleNamespace(enabled=False),
        )
    )
    batch_plan = SimpleNamespace(run_id="pilot-run")
    return ExecutionEngine(
        context,
        batch_plan,
        SimpleNamespace(n_concurrent=1),
        agent_id="evoagent",
        baseline=baseline,
        baseline_state=baseline_state,
        layout=layout,
    )


def _trainer_state(checkpoint_id: str, update_index: int) -> TrainerState:
    return TrainerState(
        epoch=0 if update_index == 0 else 1,
        train_batch_index=update_index,
        global_step=update_index,
        updates_completed=update_index,
        num_train_tasks_seen=update_index,
        checkpoint_id=checkpoint_id,
    )


def test_pinned_engine_loads_relocated_initial_and_final_alias(tmp_path: Path) -> None:
    origin = tmp_path / "origin"
    layout = ArtifactLayout.from_run_dir(origin)
    layout.prepare()
    atif_root = origin / "harbor" / "jobs"
    atif_root.mkdir(parents=True, exist_ok=True)
    client = _NoNetworkClient()
    baseline = EvoAgentSEAGymBaseline(
        baseline_id="evoagent",
        state_dir=tmp_path / "live-state",
        atif_root=atif_root,
        model_client=client,
    )
    state = baseline.initialize(origin)
    engine = _engine(run_dir=origin, baseline=baseline, baseline_state=state)

    initial = engine.save_checkpoint(
        "initial",
        checkpoint_type="initial",
        trainer_state=_trainer_state("initial", 0),
        metadata={
            "kind": "initial",
            "epoch_index": 0,
            "train_batch_index": 0,
            "num_train_tasks_seen": 0,
        },
    )
    assert initial["baseline"]["type"] == "evoagent_seagym_checkpoint"

    epoch = engine.save_checkpoint(
        "epoch_0001",
        checkpoint_type="epoch",
        trainer_state=_trainer_state("epoch_0001", 0),
        metadata={
            "kind": "epoch",
            "epoch_index": 1,
            "train_batch_index": 0,
            "num_train_tasks_seen": 0,
        },
    )
    final = engine.alias_checkpoint(
        "final",
        source_checkpoint_id="epoch_0001",
        checkpoint_type="final",
        trainer_state=_trainer_state("final", 0),
        metadata={
            "kind": "final",
            "epoch_index": 1,
            "train_batch_index": 0,
            "num_train_tasks_seen": 0,
            "alias_of": "epoch_0001",
        },
    )
    assert epoch["baseline"]["type"] == "evoagent_seagym_checkpoint"
    assert final["baseline"]["type"] == "baseline_checkpoint_alias"
    assert final["baseline"]["state_ref"].replace("\\", "/") == (
        "../epoch_0001/baseline_state"
    )

    relocated = tmp_path / "relocated"
    shutil.move(str(origin), str(relocated))
    relocated_engine = _engine(
        run_dir=relocated,
        baseline=baseline,
        baseline_state=state,
    )

    initial_load = relocated_engine.load_checkpoint("initial")
    assert initial_load["loaded"] is True
    assert baseline.report(relocated_engine.baseline_state)["update_index"] == 0

    final_load = relocated_engine.load_checkpoint("final")
    assert final_load["loaded"] is True
    final_report = baseline.report(relocated_engine.baseline_state)
    assert final_report["update_index"] == 0
    assert final_report["evaluation_candidate_generation"] == 0
    assert client.calls == 0
