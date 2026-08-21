from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

FOCUSED_TESTS = (
    "tests/test_program_running_attestation.py",
    "tests/test_program_local_rl_projection_package.py",
    "tests/test_program_local_rl_acceptance_lab.py",
    "tests/test_composite_snapshot_registry.py",
    "tests/test_composite_snapshot_service.py",
    "tests/test_composite_evaluation.py",
    "tests/test_composite_evaluation_repository.py",
    "tests/test_integrated_case_routing.py",
    "tests/test_integrated_model_invariants.py",
    "tests/test_integrated_repository.py",
    "tests/test_integrated_repository_semantic_hardening.py",
    "tests/test_integrated_supervisor_service.py",
    "tests/test_integrated_real_executors.py",
    "tests/test_integrated_multitrack_lab.py",
    "tests/test_integrated_public_contract.py",
    "tests/test_integrated_runtime_public_contract.py",
)

SOURCE_VALIDATORS = (
    "scripts/validate_local_policy_promotion_final_source.py",
    "scripts/validate_v2_3_composite_source.py",
    "scripts/validate_v2_3_integrated_source.py",
)


def _run(*command: str) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def _git(*arguments: str) -> str:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout.strip()


def _verify_head() -> None:
    expected = os.environ.get("V2_3_EXPECTED_HEAD_SHA", "").strip()
    actual = _git("rev-parse", "HEAD")
    if expected and actual != expected:
        raise SystemExit(
            f"exact-Head mismatch: expected {expected}, checked out {actual}"
        )
    status = _git("status", "--porcelain")
    if status:
        raise SystemExit("v2.3 exact gate requires a clean worktree")
    print(f"v2.3 exact Head: {actual}")


def main() -> int:
    _verify_head()
    _run(sys.executable, "-m", "compileall", "-q", "src", "tests", "scripts")
    for validator in SOURCE_VALIDATORS:
        if not (ROOT / validator).is_file():
            raise SystemExit(f"missing source validator: {validator}")
        _run(sys.executable, validator)
    _run(
        sys.executable,
        "-m",
        "pytest",
        "-q",
        *FOCUSED_TESTS,
        "--tb=short",
    )
    if os.environ.get("V2_3_FULL_REGRESSION", "1") == "1":
        _run(sys.executable, "-m", "pytest", "-q", "--tb=short")
    print("v2.3 integrated gate passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
