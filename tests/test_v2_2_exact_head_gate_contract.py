from __future__ import annotations

import runpy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_v2_2_exact_head_gate_covers_final_contract():
    gate_path = ROOT / "scripts" / "run_v2_2_exact_head_gate.py"
    namespace = runpy.run_path(
        str(gate_path),
        run_name="v2_2_exact_head_gate_contract",
    )
    focused = set(namespace["FOCUSED_TESTS"])

    required = {
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
    }
    assert required.issubset(focused)
    assert namespace["DEFAULT_REPORT"].name == "v2.2-exact-head-gate.json"

    gate_source = gate_path.read_text(encoding="utf-8")
    assert "validate_local_policy_promotion_source.py" in gate_source
    assert "validate_local_policy_promotion_final_source.py" in gate_source
    assert '"full_regression"' in gate_source
    assert '"build_wheel"' in gate_source
    assert '"install_wheel"' in gate_source
    assert '"pip_check"' in gate_source
    assert '"installed_public_api"' in gate_source

    installed_code = namespace["_installed_api_code"]()
    assert "from evoagent.lab import AcceptedLocalPolicyPromotionLab" in (
        installed_code
    )
    assert "repository_chronology_final" in installed_code
    assert "lifecycle_recovery_final" in installed_code
    assert "package_semantic_final" in installed_code
    assert "local_policy_promotion_final" in installed_code
