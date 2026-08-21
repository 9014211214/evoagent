from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from evoagent.benchmarks import (
    compare_skillevolbench_runs,
    import_skillevolbench_report,
)


_FULL_TRIAL_COUNTS = {
    "no_skill": 180,
    "selfgen_experience_always": 270,
}


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON file: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _control_identity(config: dict[str, Any]) -> dict[str, Any]:
    baseline = config.get("baseline")
    strategy = config.get("strategy")
    if not isinstance(baseline, dict) or not isinstance(strategy, dict):
        raise ValueError("SkillEvolBench config lacks baseline/strategy objects")
    return {
        "order_seed": config.get("order_seed"),
        "model_name": baseline.get("model_name"),
        "harbor_agent_name": baseline.get("harbor_agent_name"),
        "agent_kwargs": baseline.get("agent_kwargs") or {},
        "strategy": strategy,
        "benchmark_skills_root": config.get("benchmark_skills_root"),
        "benchmark_tasks_root": config.get("benchmark_tasks_root"),
        "harbor_orchestrator_type": config.get("harbor_orchestrator_type"),
        "harbor_n_concurrent_trials": config.get("harbor_n_concurrent_trials"),
        "api_base": config.get("api_base"),
        "api_key_env_var": config.get("api_key_env_var"),
        "dry_run": config.get("dry_run"),
        "max_tasks": config.get("max_tasks"),
    }


def _verify_report_config_identity(
    *,
    report: Any,
    config: dict[str, Any],
    partial_smoke: bool,
) -> None:
    baseline = config["baseline"]
    strategy = config["strategy"]
    if report.baseline_name != baseline.get("name"):
        raise ValueError("report/config baseline identity mismatch")
    if report.strategy_name != strategy.get("name"):
        raise ValueError("report/config strategy identity mismatch")
    if report.order_seed != config.get("order_seed"):
        raise ValueError("report/config order seed mismatch")
    if config.get("dry_run") is not False:
        raise ValueError("comparison report must come from a real, non-dry run")

    max_tasks = config.get("max_tasks")
    if partial_smoke:
        if not isinstance(max_tasks, int) or isinstance(max_tasks, bool) or max_tasks < 1:
            raise ValueError("partial smoke requires a positive max_tasks cap")
        expected_tasks = max_tasks
    else:
        if max_tasks is not None:
            raise ValueError("full comparison must not use a max_tasks cap")
        try:
            expected_tasks = _FULL_TRIAL_COUNTS[report.baseline_name]
        except KeyError as exc:
            raise ValueError("unsupported full-comparison baseline") from exc
    if report.n_tasks_attempted != expected_tasks:
        raise ValueError(
            "incomplete report: "
            f"{report.baseline_name} attempted {report.n_tasks_attempted}, "
            f"expected {expected_tasks}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-report", type=Path, required=True)
    parser.add_argument("--baseline-config", type=Path, required=True)
    parser.add_argument("--evolved-report", type=Path, required=True)
    parser.add_argument("--evolved-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--partial-smoke", action="store_true")
    args = parser.parse_args(argv)

    baseline_config = _load_object(args.baseline_config)
    evolved_config = _load_object(args.evolved_config)
    baseline_control = _control_identity(baseline_config)
    evolved_control = _control_identity(evolved_config)
    for key in baseline_control:
        if baseline_control[key] != evolved_control[key]:
            raise ValueError(f"comparison control mismatch: {key}")

    baseline_sha = _sha256(args.baseline_report)
    evolved_sha = _sha256(args.evolved_report)
    baseline = import_skillevolbench_report(
        args.baseline_report,
        expected_sha256=baseline_sha,
    )
    evolved = import_skillevolbench_report(
        args.evolved_report,
        expected_sha256=evolved_sha,
    )
    if baseline.baseline_name != "no_skill":
        raise ValueError("baseline report is not the no_skill condition")
    if evolved.baseline_name != "selfgen_experience_always":
        raise ValueError("evolved report is not the EvoAgent bridge condition")
    _verify_report_config_identity(
        report=baseline,
        config=baseline_config,
        partial_smoke=args.partial_smoke,
    )
    _verify_report_config_identity(
        report=evolved,
        config=evolved_config,
        partial_smoke=args.partial_smoke,
    )

    output = {
        "format_version": "evoagent-skillevolbench-comparison-v1",
        "publishable_full_benchmark": not args.partial_smoke,
        "claim_boundary": (
            "partial smoke only; not a benchmark score"
            if args.partial_smoke
            else "full same-model same-seed comparison"
        ),
        "controls": baseline_control,
        "baseline": baseline.model_dump(mode="json"),
        "evolved": evolved.model_dump(mode="json"),
        "delta": compare_skillevolbench_runs(baseline, evolved),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"comparison written: {args.output}")
    print(f"baseline report SHA-256: {baseline_sha}")
    print(f"evolved report SHA-256: {evolved_sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
