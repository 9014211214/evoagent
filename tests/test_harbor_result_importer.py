from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from evoagent.benchmark_evidence import (
    HarborResultImportError,
    HarborResultImporter,
)
from evoagent.lab import AuthoritativeBenchmarkEvidenceLab


def _setup(tmp_path):
    lab = AuthoritativeBenchmarkEvidenceLab(
        tmp_path / "benchmark-lab",
        source_commit="a" * 40,
    )
    suite = lab._suite()
    contracts = lab._contracts(suite)
    payloads = lab._fixture_payloads(contracts)
    hashes = lab._write_or_verify_fixtures(payloads)
    return lab, contracts, payloads, hashes


def _write_payload(root: Path, payload: dict) -> tuple[str, str]:
    path = root / "case" / "result.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
        + "\n"
    ).encode("utf-8")
    path.write_bytes(encoded)
    return "case/result.json", hashlib.sha256(encoded).hexdigest()


def test_safe_import_counts_error_as_zero_and_drops_sensitive_diagnostics(tmp_path):
    lab, contracts, _, hashes = _setup(tmp_path)
    run = HarborResultImporter(lab.fixtures_root).import_file(
        "a0/result.json",
        expected_sha256=hashes["a0"],
        evidence_id="benchmark-run:test-a0",
        contract=contracts["a0"],
    )
    assert run.score == 0.25
    assert run.error_rate == 0.25
    assert run.n_errored_trials == 1
    errored = next(item for item in run.trials if item.error_type)
    assert errored.primary_reward == 0.0
    assert errored.verifier_evidence_present is False
    assert run.total_input_tokens == 400
    assert run.total_cache_tokens == 40
    assert run.total_output_tokens == 200
    assert run.total_cost_usd == pytest.approx(0.04)

    safe_json = run.model_dump_json().lower()
    for forbidden in (
        "exception_message",
        "exception_traceback",
        "traceback (synthetic",
        "prompt",
        "trajectory",
        "chain_of_thought",
        "hidden_reasoning",
        "scratchpad",
    ):
        assert forbidden not in safe_json


def test_import_rejects_duplicate_trial_name(tmp_path):
    _, contracts, payloads, _ = _setup(tmp_path)
    payload = json.loads(json.dumps(payloads["a1"]))
    payload["trial_results"][1]["trial_name"] = payload["trial_results"][0][
        "trial_name"
    ]
    relative, digest = _write_payload(tmp_path / "import", payload)
    with pytest.raises(ValueError, match="duplicate trial names"):
        HarborResultImporter(tmp_path / "import").import_file(
            relative,
            expected_sha256=digest,
            evidence_id="benchmark-run:duplicate",
            contract=contracts["a1"],
        )


def test_import_rejects_task_checksum_drift(tmp_path):
    _, contracts, payloads, _ = _setup(tmp_path)
    payload = json.loads(json.dumps(payloads["a1"]))
    payload["trial_results"][0]["task_checksum"] = "f" * 64
    relative, digest = _write_payload(tmp_path / "import", payload)
    with pytest.raises(ValueError, match="checksum drift"):
        HarborResultImporter(tmp_path / "import").import_file(
            relative,
            expected_sha256=digest,
            evidence_id="benchmark-run:checksum-drift",
            contract=contracts["a1"],
        )


def test_import_rejects_declared_trial_count_mismatch(tmp_path):
    _, contracts, payloads, _ = _setup(tmp_path)
    payload = json.loads(json.dumps(payloads["a1"]))
    payload["n_total_trials"] = 5
    relative, digest = _write_payload(tmp_path / "import", payload)
    with pytest.raises(HarborResultImportError, match="declared total"):
        HarborResultImporter(tmp_path / "import").import_file(
            relative,
            expected_sha256=digest,
            evidence_id="benchmark-run:count-mismatch",
            contract=contracts["a1"],
        )


def test_import_rejects_malformed_or_missing_primary_reward(tmp_path):
    _, contracts, payloads, _ = _setup(tmp_path)
    malformed = json.loads(json.dumps(payloads["a1"]))
    malformed["trial_results"][0]["verifier_result"]["rewards"]["reward"] = "one"
    relative, digest = _write_payload(tmp_path / "malformed", malformed)
    with pytest.raises(HarborResultImportError, match="must be numeric"):
        HarborResultImporter(tmp_path / "malformed").import_file(
            relative,
            expected_sha256=digest,
            evidence_id="benchmark-run:malformed-reward",
            contract=contracts["a1"],
        )

    missing = json.loads(json.dumps(payloads["a1"]))
    missing["trial_results"][0]["verifier_result"]["rewards"] = {
        "other": 1.0
    }
    relative, digest = _write_payload(tmp_path / "missing", missing)
    with pytest.raises(HarborResultImportError, match="primary reward key"):
        HarborResultImporter(tmp_path / "missing").import_file(
            relative,
            expected_sha256=digest,
            evidence_id="benchmark-run:missing-primary-reward",
            contract=contracts["a1"],
        )


def test_import_rejects_secret_even_inside_dropped_traceback(tmp_path):
    _, contracts, payloads, _ = _setup(tmp_path)
    payload = json.loads(json.dumps(payloads["a0"]))
    errored = next(
        item for item in payload["trial_results"] if item["exception_info"]
    )
    errored["exception_info"]["exception_traceback"] = (
        "failed with sk-abcdefghijklmnopqrstuvwxyz123456"
    )
    relative, digest = _write_payload(tmp_path / "import", payload)
    with pytest.raises(HarborResultImportError, match="potential credential"):
        HarborResultImporter(tmp_path / "import").import_file(
            relative,
            expected_sha256=digest,
            evidence_id="benchmark-run:secret",
            contract=contracts["a0"],
        )


def test_import_rejects_agent_or_model_identity_drift(tmp_path):
    _, contracts, payloads, _ = _setup(tmp_path)
    payload = json.loads(json.dumps(payloads["a1"]))
    payload["trial_results"][0]["agent_info"]["name"] = "other-agent"
    relative, digest = _write_payload(tmp_path / "agent", payload)
    with pytest.raises(ValueError, match="Agent identity differs"):
        HarborResultImporter(tmp_path / "agent").import_file(
            relative,
            expected_sha256=digest,
            evidence_id="benchmark-run:agent-drift",
            contract=contracts["a1"],
        )

    payload = json.loads(json.dumps(payloads["a1"]))
    payload["trial_results"][0]["agent_info"]["model_info"]["name"] = (
        "other-model"
    )
    relative, digest = _write_payload(tmp_path / "model", payload)
    with pytest.raises(ValueError, match="Model identity differs"):
        HarborResultImporter(tmp_path / "model").import_file(
            relative,
            expected_sha256=digest,
            evidence_id="benchmark-run:model-drift",
            contract=contracts["a1"],
        )


def test_import_rejects_wrong_hash_path_and_symlink(tmp_path):
    lab, contracts, _, hashes = _setup(tmp_path)
    importer = HarborResultImporter(lab.fixtures_root)
    with pytest.raises(HarborResultImportError, match="SHA-256 mismatch"):
        importer.import_file(
            "a1/result.json",
            expected_sha256="0" * 64,
            evidence_id="benchmark-run:wrong-hash",
            contract=contracts["a1"],
        )
    with pytest.raises(HarborResultImportError, match="only result.json"):
        importer.import_file(
            "a1/not-result.json",
            expected_sha256=hashes["a1"],
            evidence_id="benchmark-run:wrong-name",
            contract=contracts["a1"],
        )

    target = lab.fixtures_root / "a1" / "result.json"
    symlink_dir = lab.fixtures_root / "linked"
    symlink_dir.mkdir(parents=True)
    link = symlink_dir / "result.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("Filesystem does not permit symlink creation.")
    with pytest.raises(HarborResultImportError, match="symlinks"):
        importer.import_file(
            "linked/result.json",
            expected_sha256=hashes["a1"],
            evidence_id="benchmark-run:symlink",
            contract=contracts["a1"],
        )
