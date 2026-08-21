import json
from pathlib import Path

import pytest

from scripts.prepare_skillevolbench_runtime import main, prepare_runtime_script


PINNED_FRAGMENT = """configure_apt_mirror() {
  local mirror="http://us-east-1.ec2.archive.ubuntu.com/ubuntu"
}
install_system_packages() {
  configure_apt_mirror
  apt-get update
  apt-get install -y --no-install-recommends \\
    curl
}
prewarm_openclaw() {
  local workspace_dir="/tmp/openclaw-doctor/workspace"
  OPENCLAW_STATE_DIR="/tmp/openclaw-doctor" \\
    openclaw --no-color setup --workspace "$workspace_dir"
}
"""


def test_prepare_runtime_script_keeps_default_mirror_and_adds_bounded_retries(
    tmp_path,
):
    script = tmp_path / "install-agent-runtime.sh"
    script.write_text(PINNED_FRAGMENT, encoding="utf-8")

    before, after, patch = prepare_runtime_script(script)

    prepared = script.read_text(encoding="utf-8")
    assert before != after
    assert "configure_apt_mirror\n  apt-get update" not in prepared
    assert "Acquire::Retries=5" in prepared
    assert "Acquire::http::Timeout=30" in prepared
    assert 'setup --baseline --workspace "$workspace_dir"' in prepared
    assert 'setup --workspace "$workspace_dir"' not in prepared
    assert "ubuntu_base_image_defaults" not in prepared
    assert "--- a/docker/agent-build/install-agent-runtime.sh" in patch


def test_prepare_runtime_script_is_idempotent(tmp_path):
    script = tmp_path / "install-agent-runtime.sh"
    script.write_text(PINNED_FRAGMENT, encoding="utf-8")
    prepare_runtime_script(script)

    before, after, patch = prepare_runtime_script(script)

    assert before == after
    assert patch == ""


def test_prepare_runtime_script_rejects_unknown_upstream_shape(tmp_path):
    script = tmp_path / "install-agent-runtime.sh"
    script.write_text("apt-get update\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="expected hosted-runtime contract"):
        prepare_runtime_script(script)


def test_cli_writes_sanitized_evidence(monkeypatch, tmp_path):
    script = tmp_path / "install-agent-runtime.sh"
    evidence = tmp_path / "evidence" / "runtime-build.json"
    patch = tmp_path / "evidence" / "runtime-build.patch"
    script.write_text(PINNED_FRAGMENT, encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        [
            "prepare_skillevolbench_runtime.py",
            "--script",
            str(script),
            "--evidence",
            str(evidence),
            "--patch-evidence",
            str(patch),
            "--claude-code-version",
            "2.1.235",
            "--openclaw-version",
            "2026.7.1-2",
        ],
    )

    assert main() == 0
    record = json.loads(evidence.read_text(encoding="utf-8"))
    assert record["target"] == "docker/agent-build/install-agent-runtime.sh"
    assert record["mirror_policy"] == "ubuntu_base_image_defaults"
    assert record["apt_retries"] == 5
    assert record["apt_http_timeout_seconds"] == 30
    assert record["openclaw_setup_policy"] == "baseline_noninteractive"
    assert record["claude_code_version"] == "2.1.235"
    assert record["openclaw_version"] == "2026.7.1-2"
    assert record["changed"] is True
    assert str(tmp_path) not in evidence.read_text(encoding="utf-8")
    patch_text = patch.read_text(encoding="utf-8")
    assert str(tmp_path) not in patch_text
    assert "-  configure_apt_mirror" in patch_text
    assert "+  apt-get -o Acquire::Retries=5" in patch_text
    assert "+    openclaw --no-color setup --baseline" in patch_text
