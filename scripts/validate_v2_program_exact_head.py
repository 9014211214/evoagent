from __future__ import annotations

import inspect
import tempfile
from pathlib import Path

import evoagent
from evoagent.lab import MultiGenerationEvolutionProgramLab
from evoagent.program import (
    EvolutionProgramController,
    EvolutionProgramPackageManager,
    SQLiteEvolutionProgramRepository,
)


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION = "2.0.0"
EXPECTED_CONTROLLER_MODULES = {
    "evoagent.program.controller_program_attestation_final",
    "evoagent.program.controller_public_final",
    "evoagent.program.controller_final_hardened",
}
EXPECTED_PACKAGE_MODULES = {
    "evoagent.program.package_provenance_hardened_final",
    "evoagent.program.package_provenance_hardened",
}


def fail(message: str) -> None:
    raise SystemExit(message)


def require_source_marker(path: str, marker: str) -> None:
    target = ROOT / path
    if not target.is_file():
        fail(f"required exact-head source file missing: {path}")
    if marker not in target.read_text(encoding="utf-8"):
        fail(f"required exact-head marker missing from {path}: {marker}")


def validate_public_api() -> None:
    if evoagent.__version__ != EXPECTED_VERSION:
        fail(
            f"exact-head version mismatch: expected {EXPECTED_VERSION}, "
            f"found {evoagent.__version__}"
        )
    if EvolutionProgramController.__module__ not in EXPECTED_CONTROLLER_MODULES:
        fail(
            "public Program Controller is not the final hardened implementation: "
            f"{EvolutionProgramController.__module__}"
        )
    if EvolutionProgramPackageManager.__module__ not in EXPECTED_PACKAGE_MODULES:
        fail(
            "public Program Package Manager is not the final hardened implementation: "
            f"{EvolutionProgramPackageManager.__module__}"
        )
    controller_mro = {
        f"{item.__module__}.{item.__name__}"
        for item in inspect.getmro(EvolutionProgramController)
    }
    required_controller_layers = {
        "evoagent.program.controller_retry_hardened.RetryHardenedEvolutionProgramController",
        "evoagent.program.controller_hardened.HardenedEvolutionProgramController",
    }
    if not required_controller_layers.issubset(controller_mro):
        fail(
            "public Program Controller MRO omits retry or governance hardening: "
            f"{sorted(controller_mro)}"
        )
    package_mro = {
        f"{item.__module__}.{item.__name__}"
        for item in inspect.getmro(EvolutionProgramPackageManager)
    }
    if not any("package_audit_hardened" in item for item in package_mro):
        fail("public Program Package Manager MRO omits audit hardening")
    if not any("package_policy_hardened" in item for item in package_mro):
        fail("public Program Package Manager MRO omits policy hardening")


def validate_source_contract() -> None:
    for path, marker in (
        (
            "src/evoagent/program/controller_retry_hardened.py",
            "cannot ingest its own Program feedback",
        ),
        (
            "src/evoagent/program/controller_evidence_hardened.py",
            "Program decision signal is not the exact persisted",
        ),
        (
            "src/evoagent/program/repository_hardened.py",
            "current_generation_index=record.generation_index",
        ),
        (
            "src/evoagent/program/package_audit_hardened.py",
            "feedback ingestion actor equals",
        ),
        (
            "src/evoagent/program/package_provenance_hardened.py",
            "causal chronology",
        ),
        (
            "src/evoagent/program/constraints.py",
            "validate_single_release_package_budget",
        ),
    ):
        require_source_marker(path, marker)
    license_path = ROOT / "LICENSE"
    if not license_path.is_file() or "Apache License" not in license_path.read_text(
        encoding="utf-8"
    ):
        fail("root Apache-2.0 LICENSE is missing after the owner license decision")


def validate_executable_lifecycle() -> None:
    with tempfile.TemporaryDirectory(prefix="evoagent-v2-exact-head-") as root:
        lab = MultiGenerationEvolutionProgramLab(
            Path(root) / "program-lab",
            source_commit="0" * 40,
        )
        first = lab.run()
        second = lab.run()
        if first.resumed is not False or second.resumed is not True:
            fail("Program lab did not expose first-run/write then second-run/read-only")
        if first.package_hash != second.package_hash:
            fail("Program package hash changed across exact read-only resume")
        if second.program_state != "completed":
            fail(f"Program did not complete: {second.program_state}")
        if second.decision_actions != ("continue", "stop_success"):
            fail(f"Program decisions differ: {second.decision_actions}")
        if second.generation_statuses != ("rolled_back", "completed"):
            fail(f"Program generation states differ: {second.generation_statuses}")
        package = EvolutionProgramPackageManager().load_file(first.package_path)
        if package.package_hash != first.package_hash:
            fail("exported Program package differs from lab result")
        repository = SQLiteEvolutionProgramRepository(lab.program_database)
        if repository.verify_state(package.final_head.program_id) is not True:
            fail("Program Registry state verification did not return True")
        if repository.verify_audit(package.program_checkpoint) is not True:
            fail("Program audit checkpoint verification did not return True")


def main() -> None:
    validate_public_api()
    validate_source_contract()
    validate_executable_lifecycle()
    print(
        "v2.0 exact-head Program contract verified: "
        f"controller={EvolutionProgramController.__module__}, "
        f"package_manager={EvolutionProgramPackageManager.__module__}"
    )


if __name__ == "__main__":
    main()
