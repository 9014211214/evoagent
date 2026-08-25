import subprocess
from pathlib import Path

from scripts import verify_release_readiness


def _release_root(tmp_path: Path) -> Path:
    (tmp_path / "OPEN_SOURCE_READINESS.md").write_text("ready\n", encoding="utf-8")
    (tmp_path / "LICENSE").write_text("Apache License\n", encoding="utf-8")
    return tmp_path


def test_release_scan_allows_a_same_line_synthetic_fixture(tmp_path, monkeypatch):
    root = _release_root(tmp_path)
    (root / "fixture.py").write_text(
        'token = "hf_abcdefghijklmnopqrstuv"  # synthetic-secret-fixture\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(verify_release_readiness, "ROOT", root)

    assert verify_release_readiness.main() == 0


def test_release_scan_does_not_apply_fixture_marker_to_the_whole_file(
    tmp_path, monkeypatch, capsys
):
    root = _release_root(tmp_path)
    (root / "fixture.py").write_text(
        "# synthetic-secret-fixture\n"
        'token = "hf_abcdefghijklmnopqrstuv"\n',  # synthetic-secret-fixture
        encoding="utf-8",
    )
    monkeypatch.setattr(verify_release_readiness, "ROOT", root)

    assert verify_release_readiness.main() == 1
    assert "possible huggingface_token: fixture.py" in capsys.readouterr().out


def test_release_scan_ignores_generated_untracked_environments(tmp_path, monkeypatch):
    root = _release_root(tmp_path)
    tracked = root / "tracked.py"
    tracked.write_text("safe = True\n", encoding="utf-8")
    generated = root / ".release-wheel-venv" / "lib"
    generated.mkdir(parents=True)
    (generated / "auth.py").write_text(
        'password = "generated-dependency-fixture"  # synthetic-secret-fixture\n',
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "init", "--quiet"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "add", "LICENSE", "OPEN_SOURCE_READINESS.md", "tracked.py"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    monkeypatch.setattr(verify_release_readiness, "ROOT", root)

    assert verify_release_readiness.main() == 0


def test_release_scan_checks_untracked_unignored_source_files(
    tmp_path, monkeypatch, capsys
):
    root = _release_root(tmp_path)
    subprocess.run(
        ["git", "init", "--quiet"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    (root / "new_source.py").write_text(
        'token = "hf_abcdefghijklmnopqrstuv"\n',  # synthetic-secret-fixture
        encoding="utf-8",
    )
    monkeypatch.setattr(verify_release_readiness, "ROOT", root)

    assert verify_release_readiness.main() == 1
    assert "possible huggingface_token: new_source.py" in capsys.readouterr().out


def test_pull_request_benchmark_mode_is_hardwired_to_preflight():
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github"
        / "workflows"
        / "skillevolbench-benchmark.yml"
    ).read_text(encoding="utf-8")

    assert "mode=preflight" in workflow
    assert ".github/skillevolbench-mode" not in workflow
    assert 'if [ "$EVENT_NAME" = "workflow_dispatch" ]; then' in workflow
