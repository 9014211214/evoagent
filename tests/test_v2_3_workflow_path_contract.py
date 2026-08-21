from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def test_v2_3_workflow_runs_for_every_pull_request():
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "pull_request:" in source
    assert "paths:" not in source


def test_v2_3_workflow_remains_read_only_and_exact_head_bound():
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "permissions:\n  contents: read" in source
    assert "contents: write" not in source
    assert "persist-credentials: false" in source
    assert "github.event.pull_request.head.sha || github.sha" in source
    assert "git rev-parse HEAD" in source
    assert "python scripts/validate_v2_3_composite_source.py" in source
    assert "python scripts/validate_v2_3_integrated_source.py" in source
    assert "run: pytest -q" in source
