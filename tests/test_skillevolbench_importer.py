from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import pytest

from evoagent.benchmarks.skillevolbench import (
    SkillEvolBenchImportError,
    compare_skillevolbench_runs,
    import_skillevolbench_report,
)


def _write_report(tmp_path, *, baseline_name="no_skill", overall=0.4):
    tmp_path.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": "1",
        "run_id": f"run:{baseline_name}",
        "baseline_name": baseline_name,
        "strategy_name": "chain",
        "order_seed": "A",
        "n_tasks_attempted": 180,
        "task_success": {
            "learning_sr": 0.5,
            "evaluation_sr": 0.4,
            "overall_sr": overall,
            "t4_transfer": 0.3,
            "t5_pass_rate": 0.35,
            "t6_composition_rate": 0.25,
        },
        "library_health": {"active_skill_count": 4},
        "revision_safety": {"revision_hurt_rate": 0.1},
        "transfer": {
            "final_retention_rate": 0.8,
            "forgetting_rate": 0.2,
            "negative_transfer_rate": 0.1,
        },
    }
    path = tmp_path / "full_report.json"
    raw = json.dumps(report, sort_keys=True).encode("utf-8")
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest()


def test_imports_pinned_skillevolbench_observable_metrics(tmp_path):
    path, digest = _write_report(tmp_path)
    evidence = import_skillevolbench_report(
        path,
        expected_sha256=digest,
        imported_at=datetime(2026, 8, 18, tzinfo=timezone.utc),
    )
    assert evidence.run_id == "run:no_skill"
    assert evidence.n_tasks_attempted == 180
    assert evidence.metrics.overall_sr == 0.4
    assert evidence.metrics.context_shift_sr == 0.3
    assert evidence.metrics.active_skill_count == 4
    assert evidence.official_submission_performed is False
    assert evidence.official_leaderboard_claimed is False


def test_rejects_wrong_hash_and_wrong_filename(tmp_path):
    path, digest = _write_report(tmp_path)
    with pytest.raises(SkillEvolBenchImportError, match="SHA-256 mismatch"):
        import_skillevolbench_report(path, expected_sha256="0" * 64)
    renamed = tmp_path / "report.json"
    renamed.write_bytes(path.read_bytes())
    with pytest.raises(SkillEvolBenchImportError, match="full_report.json"):
        import_skillevolbench_report(renamed, expected_sha256=digest)


def test_same_seed_comparison_reports_evolution_deltas(tmp_path):
    baseline_path, baseline_hash = _write_report(
        tmp_path / "baseline", baseline_name="no_skill", overall=0.4
    )
    evolved_path, evolved_hash = _write_report(
        tmp_path / "evolved", baseline_name="selfgen_experience_always", overall=0.55
    )
    baseline = import_skillevolbench_report(
        baseline_path,
        expected_sha256=baseline_hash,
        imported_at=datetime(2026, 8, 18, tzinfo=timezone.utc),
    )
    evolved = import_skillevolbench_report(
        evolved_path,
        expected_sha256=evolved_hash,
        imported_at=datetime(2026, 8, 18, tzinfo=timezone.utc),
    )
    delta = compare_skillevolbench_runs(baseline, evolved)
    assert delta["overall_sr_delta"] == pytest.approx(0.15)


def test_comparison_rejects_different_order_seed(tmp_path):
    path, digest = _write_report(tmp_path)
    baseline = import_skillevolbench_report(
        path,
        expected_sha256=digest,
        imported_at=datetime(2026, 8, 18, tzinfo=timezone.utc),
    )
    document = json.loads(path.read_text(encoding="utf-8"))
    document["run_id"] = "run:evolved"
    document["order_seed"] = "B"
    raw = json.dumps(document, sort_keys=True).encode("utf-8")
    evolved_dir = tmp_path / "evolved"
    evolved_dir.mkdir()
    evolved_path = evolved_dir / "full_report.json"
    evolved_path.write_bytes(raw)
    evolved = import_skillevolbench_report(
        evolved_path,
        expected_sha256=hashlib.sha256(raw).hexdigest(),
        imported_at=datetime(2026, 8, 18, tzinfo=timezone.utc),
    )
    with pytest.raises(SkillEvolBenchImportError, match="same order seed"):
        compare_skillevolbench_runs(baseline, evolved)
