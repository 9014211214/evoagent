from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / ".artifacts" / "v2.2-exact-head-gate.json"
FOCUSED_TESTS = (
    "tests/test_local_policy_promotion_lifecycle.py",
    "tests/test_local_policy_promotion_only_package.py",
    "tests/test_local_policy_promotion_lab.py",
    "tests/test_local_policy_parent_provenance.py",
    "tests/test_local_policy_package_provenance.py",
    "tests/test_local_policy_registry_manifest_type.py",
    "tests/test_local_policy_registry_chronology.py",
    "tests/test_local_policy_isolated_audit_scope.py",
    "tests/test_local_policy_campaign_api_adapter.py",
    "tests/test_local_policy_campaign_semantics.py",
    "tests/test_local_policy_submission_recovery.py",
    "tests/test_local_policy_runtime_role_separation.py",
    "tests/test_local_policy_interface_alignment.py",
    "tests/test_local_policy_promotion_tamper.py",
    "tests/test_local_policy_semantic_tamper.py",
    "tests/test_local_policy_rollback_bypass.py",
    "tests/test_local_policy_time_validation.py",
    "tests/test_local_policy_public_contract.py",
    "tests/test_local_policy_promotion_source.py",
    "tests/test_local_policy_final_source.py",
    "tests/test_v2_2_exact_head_gate_contract.py",
)


class GateFailure(RuntimeError):
    pass


def _run(
    name: str,
    command: list[str],
    *,
    cwd: Path,
    results: list[dict[str, Any]],
) -> None:
    started = datetime.now(timezone.utc)
    process = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    completed = datetime.now(timezone.utc)
    output = process.stdout or ""
    results.append(
        {
            "name": name,
            "command": command,
            "returncode": process.returncode,
            "passed": process.returncode == 0,
            "started_at": started.isoformat(),
            "completed_at": completed.isoformat(),
            "output": output,
        }
    )
    sys.stdout.write(f"\n===== {name} (exit={process.returncode}) =====\n")
    sys.stdout.write(output)
    if output and not output.endswith("\n"):
        sys.stdout.write("\n")
    if process.returncode != 0:
        raise GateFailure(f"v2.2 gate failed at {name}.")


def _capture(command: list[str], *, cwd: Path) -> str:
    process = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if process.returncode != 0:
        raise GateFailure(
            f"Command failed before gate execution: {' '.join(command)}\n"
            f"{process.stderr}"
        )
    return process.stdout.strip()


def _require_clean_checkout() -> str:
    head = _capture(["git", "rev-parse", "HEAD"], cwd=ROOT)
    status = _capture(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=ROOT,
    )
    if status:
        raise GateFailure(
            "Exact-head gate requires a clean checkout; git status is not empty."
        )
    return head


def _venv_python(venv_root: Path) -> Path:
    if os.name == "nt":
        return venv_root / "Scripts" / "python.exe"
    return venv_root / "bin" / "python"


def _installed_api_code() -> str:
    return "\n".join(
        (
            "from evoagent.campaigns import CampaignType",
            "from evoagent.lab import AcceptedLocalPolicyPromotionLab",
            "from evoagent.local_policy import LocalPolicyPromotionLifecycleService, LocalPolicyPromotionPackageManager, SQLiteLocalPolicyRegistry",
            "assert CampaignType.LOCAL_POLICY_PROMOTION.value == 'local_policy_promotion'",
            "assert CampaignType.LOCAL_POLICY_ROLLBACK.value == 'local_policy_rollback'",
            "assert LocalPolicyPromotionLifecycleService.__module__ == 'evoagent.local_policy.lifecycle_recovery_final'",
            "assert LocalPolicyPromotionPackageManager.__module__ == 'evoagent.local_policy.package_semantic_final'",
            "assert SQLiteLocalPolicyRegistry.__module__ == 'evoagent.local_policy.repository_chronology_final'",
            "assert AcceptedLocalPolicyPromotionLab.__module__ == 'evoagent.lab.local_policy_promotion_final'",
            "print('Installed v2.2 public API verified')",
        )
    )


def run_gate(report_path: Path) -> int:
    results: list[dict[str, Any]] = []
    generated_at = datetime.now(timezone.utc)
    head: str | None = None
    status = "failed"
    error: str | None = None

    try:
        if sys.version_info[:2] not in {(3, 11), (3, 12)}:
            raise GateFailure(
                "v2.2 exact-head gate requires Python 3.11 or Python 3.12."
            )
        head = _require_clean_checkout()
        _run(
            "compileall",
            [sys.executable, "-m", "compileall", "-q", "src", "tests", "scripts"],
            cwd=ROOT,
            results=results,
        )
        _run(
            "source_invariants",
            [sys.executable, "scripts/validate_local_policy_promotion_source.py"],
            cwd=ROOT,
            results=results,
        )
        _run(
            "final_source_invariants",
            [
                sys.executable,
                "scripts/validate_local_policy_promotion_final_source.py",
            ],
            cwd=ROOT,
            results=results,
        )
        _run(
            "focused_v2_2_regression",
            [sys.executable, "-m", "pytest", "-q", *FOCUSED_TESTS],
            cwd=ROOT,
            results=results,
        )
        _run(
            "full_regression",
            [sys.executable, "-m", "pytest", "-q"],
            cwd=ROOT,
            results=results,
        )

        for relative in ("build", "dist"):
            target = ROOT / relative
            if target.exists():
                shutil.rmtree(target)
        for target in ROOT.glob("*.egg-info"):
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()

        _run(
            "build_wheel",
            [sys.executable, "-m", "build", "--wheel"],
            cwd=ROOT,
            results=results,
        )
        wheels = tuple((ROOT / "dist").glob("*.whl"))
        if len(wheels) != 1:
            raise GateFailure(
                f"Expected exactly one Wheel, found {len(wheels)}."
            )

        with tempfile.TemporaryDirectory(prefix="evoagent-v2-2-installed-") as directory:
            venv_root = Path(directory) / "venv"
            _run(
                "create_clean_venv",
                [sys.executable, "-m", "venv", str(venv_root)],
                cwd=ROOT,
                results=results,
            )
            installed_python = _venv_python(venv_root)
            _run(
                "install_wheel",
                [
                    str(installed_python),
                    "-m",
                    "pip",
                    "install",
                    str(wheels[0]),
                ],
                cwd=ROOT,
                results=results,
            )
            _run(
                "pip_check",
                [str(installed_python), "-m", "pip", "check"],
                cwd=ROOT,
                results=results,
            )
            _run(
                "installed_public_api",
                [str(installed_python), "-c", _installed_api_code()],
                cwd=ROOT,
                results=results,
            )

        status = "passed"
        return_code = 0
    except Exception as exc:
        error = str(exc)
        return_code = 1
    finally:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "format_version": "evoagent-v2.2-exact-head-gate-v1",
            "generated_at": generated_at.isoformat(),
            "repository_root": str(ROOT),
            "head_sha": head,
            "python": sys.version,
            "platform": sys.platform,
            "status": status,
            "error": error,
            "steps": results,
            "foundation_model_training_performed": False,
            "production_deployment_performed": False,
            "external_rollout_performed": False,
            "official_benchmark_claimed": False,
        }
        report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"\nGate report: {report_path}")
        if error:
            print(f"Gate error: {error}")

    return return_code


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the complete v2.2 exact-head local-policy gate."
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=DEFAULT_REPORT,
        help="JSON report path (default: .artifacts/v2.2-exact-head-gate.json)",
    )
    args = parser.parse_args()
    report_path = args.report
    if not report_path.is_absolute():
        report_path = (ROOT / report_path).resolve()
    return run_gate(report_path)


if __name__ == "__main__":
    raise SystemExit(main())
