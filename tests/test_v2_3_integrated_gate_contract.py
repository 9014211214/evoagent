from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_integrated_gate_includes_all_real_evolution_proofs():
    source = (ROOT / "scripts/run_v2_3_integrated_gate.py").read_text(
        encoding="utf-8"
    )
    required = (
        "validate_local_policy_promotion_final_source.py",
        "validate_v2_3_composite_source.py",
        "validate_v2_3_integrated_source.py",
        "test_program_running_attestation.py",
        "test_program_local_rl_projection_package.py",
        "test_program_local_rl_acceptance_lab.py",
        "test_integrated_real_executors.py",
        "test_integrated_repository_semantic_hardening.py",
        "test_integrated_multitrack_lab.py",
        "test_integrated_runtime_public_contract.py",
        "V2_3_EXPECTED_HEAD_SHA",
        "V2_3_FULL_REGRESSION",
    )
    for token in required:
        assert token in source


def test_integrated_workflow_runs_python_matrix_and_clean_wheel_install():
    source = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    required = (
        'python-version: ["3.11", "3.12"]',
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
        "github.event.pull_request.head.sha",
        "persist-credentials: false",
        "python scripts/validate_v2_3_composite_source.py",
        "python scripts/validate_v2_3_integrated_source.py",
        "run: pytest -q",
        "python -m pip wheel . --no-deps --wheel-dir dist",
        "python -m venv .wheel-venv",
        "python -m pip install dist/*.whl",
        "python -m pip check",
        "group: ci-${{ github.workflow }}-${{ github.ref }}",
    )
    for token in required:
        assert token in source


def test_integrated_workflow_runs_for_every_pull_request_without_path_filters():
    source = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "pull_request:" in source
    assert "paths:" not in source


def test_integrated_source_validator_tracks_final_public_lab_and_audit_hardening():
    source = (
        ROOT / "scripts/validate_v2_3_integrated_source.py"
    ).read_text(encoding="utf-8")
    required = (
        "src/evoagent/lab/integrated_multitrack_final.py",
        "integrated_multitrack_final",
        "tests/test_integrated_repository_semantic_hardening.py",
        "In-flight integrated claim differs from crash-recovery evidence.",
        "Integrated FAILED state lacks a governed failure lifecycle.",
        "checkpoint_promotion_authorized: Literal[False]",
        "ProgramLocalRLAcceptanceManager().accept",
        "manager.verify(package)",
    )
    for token in required:
        assert token in source
    assert (
        'if "integrated_multitrack_hardened" not in lab_init'
        not in source
    )
    assert "checkpoint_promotion_authorized is False" not in source
    assert "LocalRLPackageManager().verify" not in source


def test_workflow_contains_no_release_or_publication_authority():
    source = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8").lower()
    forbidden = (
        "pypi",
        "twine upload",
        "gh release",
        "git tag",
        "docker push",
        "workflow_run",
        "contents: write",
        "packages: write",
    )
    for token in forbidden:
        assert token not in source
