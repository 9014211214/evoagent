import pytest

from evoagent.integrations import HarborCLIAdapter, TERMINAL_BENCH_2_1


def test_harbor_development_run_is_private_and_disabled():
    adapter = HarborCLIAdapter(execution_enabled=False)
    spec = adapter.build_run(
        agent="evoagent",
        model="provider/model",
        workspace="/tmp/harbor",
        required_environment_variables=("MODEL_API_KEY",),
    )
    assert spec.dataset_ref == TERMINAL_BENCH_2_1
    assert spec.upload is False
    assert spec.public is False
    assert "--upload" not in spec.command
    assert spec.execution_enabled is False
    with pytest.raises(PermissionError):
        adapter.execute(spec, environment={"MODEL_API_KEY": "secret"})


def test_leaderboard_mode_requires_five_trials_and_explicitly_uploads():
    adapter = HarborCLIAdapter()
    with pytest.raises(ValueError):
        adapter.build_run(
            agent="evoagent",
            model="provider/model",
            workspace="/tmp/harbor",
            trials_per_task=1,
            leaderboard_mode=True,
        )
    spec = adapter.build_run(
        agent="evoagent",
        model="provider/model",
        workspace="/tmp/harbor",
        trials_per_task=5,
        leaderboard_mode=True,
    )
    assert spec.upload is True and spec.public is True
    assert spec.command[-2:] == ("--upload", "--public")
