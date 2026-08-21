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
DEFAULT_REPORT = ROOT / ".artifacts" / "v2.3-integrated-exact-head-gate.json"
FOCUSED_TESTS = (
    "tests/test_program_attestation_public_contract.py",
    "tests/test_program_running_attestation.py",
    "tests/test_program_local_rl_projection_package.py",
    "tests/test_program_local_rl_acceptance_lab.py",
    "tests/test_composite_snapshot_registry.py",
    "tests/test_composite_snapshot_service.py",
    "tests/test_composite_public_contract.py",
    "tests/test_composite_evaluation.py",
    "tests/test_composite_evaluation_repository.py",
    "tests/test_composite_evaluation_public_tamper.py",
    "tests/test_integrated_case_routing.py",
    "tests/test_integrated_model_invariants.py",
    "tests/test_integrated_repository.py",
    "tests/test_integrated_repository_semantic_hardening.py",
    "tests/test_integrated_supervisor_service.py",
    "tests/test_integrated_real_executors.py",
    "tests/test_integrated_multitrack_lab.py",
    "tests/test_integrated_public_contract.py",
    "tests/test_integrated_runtime_public_contract.py",
    "tests/test_v2_3_composite_source.py",
    "tests/test_v2_3_exact_head_gate_contract.py",
    "tests/test_v2_3_integrated_gate_contract.py",
    "tests/test_v2_3_workflow_path_contract.py",
)
SOURCE_GATES = (
    "scripts/validate_local_policy_promotion_source.py",
    "scripts/validate_local_policy_promotion_final_source.py",
    "scripts/validate_v2_3_composite_source.py",
    "scripts/validate_v2_3_integrated_source.py",
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
        raise GateFailure(f"v2.3 exact-head gate failed at {name}.")


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
            "v2.3 exact-head gate requires a clean checkout."
        )
    return head


def _venv_python(venv_root: Path) -> Path:
    if os.name == "nt":
        return venv_root / "Scripts" / "python.exe"
    return venv_root / "bin" / "python"


def _installed_api_code() -> str:
    return "\n".join(
        (
            "from evoagent.composite import CompositeComponentDriftError, CompositeEvaluationService, CompositeSnapshotManifest, CompositeSnapshotService, SQLiteCompositeEvaluationRepository, SQLiteCompositeSnapshotRegistry, StaleCompositeRevision",
            "from evoagent.integrated import ControlledCompositeRuntimeEvaluator, GovernedLocalPolicyEvolutionExecutor, GovernedSkillEvolutionExecutor, IntegratedEvolutionPackageManager, IntegratedEvolutionPackageManifest, IntegratedSupervisorService, SQLiteIntegratedEvolutionRepository",
            "from evoagent.lab import IntegratedMultiTrackEvolutionLab, IntegratedMultiTrackLabResult",
            "from evoagent.local_policy import LocalPolicyPromotionLifecycleService, LocalPolicyPromotionPackageManager, SQLiteLocalPolicyRegistry",
            "from evoagent.program import EvolutionProgramController, ProgramExecutionCheckpoint, RunningGenerationAttestation",
            "assert SQLiteCompositeSnapshotRegistry.__module__ == 'evoagent.composite.repository'",
            "assert CompositeSnapshotService.__module__ == 'evoagent.composite.service'",
            "assert CompositeSnapshotManifest.__module__ == 'evoagent.composite.models'",
            "assert CompositeComponentDriftError.__module__ == 'evoagent.composite.service'",
            "assert StaleCompositeRevision.__module__ == 'evoagent.composite.repository'",
            "assert CompositeEvaluationService.__module__ == 'evoagent.composite.evaluation_service'",
            "assert SQLiteCompositeEvaluationRepository.__module__ == 'evoagent.composite.evaluation_repository'",
            "assert SQLiteIntegratedEvolutionRepository.__module__ == 'evoagent.integrated.repository_hardened'",
            "assert IntegratedSupervisorService.__module__ == 'evoagent.integrated.service_hardened'",
            "assert GovernedSkillEvolutionExecutor.__module__ == 'evoagent.integrated.executors'",
            "assert GovernedLocalPolicyEvolutionExecutor.__module__ == 'evoagent.integrated.executors'",
            "assert ControlledCompositeRuntimeEvaluator.__module__ == 'evoagent.integrated.controlled_runtime'",
            "assert IntegratedEvolutionPackageManifest.__module__ == 'evoagent.integrated.package'",
            "assert IntegratedEvolutionPackageManager.__module__ == 'evoagent.integrated.package_hardened'",
            "assert IntegratedMultiTrackEvolutionLab.__module__ == 'evoagent.lab.integrated_multitrack_final'",
            "assert IntegratedMultiTrackLabResult.__module__ == 'evoagent.lab.integrated_multitrack'",
            "assert EvolutionProgramController.__module__ == 'evoagent.program.controller_program_attestation_final'",
            "assert ProgramExecutionCheckpoint.__module__ == 'evoagent.program.execution_attestation'",
            "assert RunningGenerationAttestation.__module__ == 'evoagent.program.execution_attestation'",
            "assert LocalPolicyPromotionLifecycleService.__module__ == 'evoagent.local_policy.lifecycle_recovery_final'",
            "assert LocalPolicyPromotionPackageManager.__module__ == 'evoagent.local_policy.package_semantic_final'",
            "assert SQLiteLocalPolicyRegistry.__module__ == 'evoagent.local_policy.repository_chronology_final'",
            "print('Installed v2.3 integrated multi-track public API verified')",
        )
    )


def _clean_build_outputs() -> None:
    for relative in ("build", "dist"):
        target = ROOT / relative
        if target.exists():
            shutil.rmtree(target)
    for pattern in ("*.egg-info", "src/*.egg-info"):
        for target in ROOT.glob(pattern):
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()


def run_gate(report_path: Path) -> int:
    results: list[dict[str, Any]] = []
    generated_at = datetime.now(timezone.utc)
    head: str | None = None
    status = "failed"
    error: str | None = None

    try:
        if sys.version_info[:2] not in {(3, 11), (3, 12)}:
            raise GateFailure(
                "v2.3 exact-head gate requires Python 3.11 or Python 3.12."
            )
        head = _require_clean_checkout()
        _run(
            "compileall",
            [
                sys.executable,
                "-m",
                "compileall",
                "-q",
                "src",
                "tests",
                "scripts",
            ],
            cwd=ROOT,
            results=results,
        )
        for source_gate in SOURCE_GATES:
            _run(
                f"source:{Path(source_gate).name}",
                [sys.executable, source_gate],
                cwd=ROOT,
                results=results,
            )
        _run(
            "focused_v2_3_integrated_regression",
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

        _clean_build_outputs()
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

        with tempfile.TemporaryDirectory(
            prefix="evoagent-v2-3-installed-"
        ) as directory:
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
        passed = status == "passed"
        report = {
            "format_version": "evoagent-v2.3-integrated-exact-head-gate-v1",
            "generated_at": generated_at.isoformat(),
            "repository_root": str(ROOT),
            "head_sha": head,
            "python": sys.version,
            "platform": sys.platform,
            "status": status,
            "error": error,
            "steps": results,
            "mixed_case_supervisor_completed": passed,
            "composite_a0_a1_a2_completed": passed,
            "local_skill_evolution_performed": passed,
            "local_policy_optimization_performed": passed,
            "local_policy_pointer_activation_performed": passed,
            "second_run_read_only_verified": passed,
            "foundation_model_training_performed": False,
            "production_activation_performed": False,
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
        description="Run the v2.3 integrated multi-track exact-head gate."
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=DEFAULT_REPORT,
        help=(
            "JSON report path "
            "(default: .artifacts/v2.3-integrated-exact-head-gate.json)"
        ),
    )
    args = parser.parse_args()
    report_path = args.report
    if not report_path.is_absolute():
        report_path = (ROOT / report_path).resolve()
    return run_gate(report_path)


if __name__ == "__main__":
    raise SystemExit(main())
