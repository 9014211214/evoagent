from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import pytest

from evoagent.integrations.minimal_scientific_result import (
    MinimalScientificSeedImportReceipt,
    MinimalScientificSeedResultImportError,
    MinimalScientificSeedResultImporter,
)
from evoagent.integrations.minimal_scientific_seed import (
    build_minimal_scientific_seed_plan,
    execute_minimal_scientific_seed,
    lock_minimal_scientific_seed_plan,
)
from evoagent.integrations.openrouter import OpenRouterModelPreset
from evoagent.model_registry.models import canonical_sha256
from scripts.import_minimal_scientific_seed_result import main as import_cli_main


ROOT = Path(__file__).parents[1]
PRESET_PATH = ROOT / "configs/full_agent/openrouter-mimo-v2.5-xiaomi-required.json"
QWEN_PRESET_PATH = ROOT / "configs/full_agent/openrouter-qwen3.8-flash-alibaba.json"
SOURCE_COMMIT = "1" * 40
RESULT_NAME = "minimal-scientific-seed-result.json"
AUTHORIZATION_ANCHOR = "github-actions://example/run/1"
AUTHORIZATION_ANCHOR_HASH = canonical_sha256(AUTHORIZATION_ANCHOR)
REQUESTER_ID = "requester"
APPROVER_IDS = ("owner", "static-budget-policy")


def _matching_transport(payload, _api_key, timeout_seconds):
    assert 0 < timeout_seconds <= 90
    requested = json.loads(payload["messages"][1]["content"])
    return {
        "model": "xiaomi/mimo-v2.5-20260422",
        "provider": "Xiaomi",
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_frozen_action",
                            "type": "function",
                            "function": {
                                "name": requested["required_tool"],
                                "arguments": json.dumps(
                                    requested["required_arguments"],
                                    sort_keys=True,
                                    separators=(",", ":"),
                                ),
                            }
                        }
                    ]
                }
            }
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 10,
            "total_tokens": 110,
            "cost": 0.0001,
        },
    }


@pytest.fixture(scope="module")
def scientific_fixture(tmp_path_factory):
    root = tmp_path_factory.mktemp("scientific-result")
    preset = OpenRouterModelPreset.model_validate_json(
        PRESET_PATH.read_text(encoding="utf-8")
    )
    plan, snapshots = build_minimal_scientific_seed_plan(
        root / "plan",
        preset=preset,
    )
    lock = lock_minimal_scientific_seed_plan(plan)
    result = execute_minimal_scientific_seed(
        root / "execution",
        plan=plan,
        snapshots=snapshots,
        preset=preset,
        api_key="test-only-key",
        source_commit=SOURCE_COMMIT,
        requester_id=REQUESTER_ID,
        approver_ids=APPROVER_IDS,
        authorization_anchor=AUTHORIZATION_ANCHOR,
        transport=_matching_transport,
    )
    payload = result.model_dump(mode="json")
    return payload, plan, lock, preset


def _write(root: Path, payload: dict) -> tuple[Path, str]:
    path = root / RESULT_NAME
    data = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("utf-8")
    path.write_bytes(data)
    return path, hashlib.sha256(data).hexdigest()


def _rehash_result(payload: dict) -> None:
    previous_hash = None
    for report in payload["reports"]:
        report["parent_report_hash"] = previous_hash
        report["report_hash"] = canonical_sha256(
            {key: value for key, value in report.items() if key != "report_hash"}
        )
        previous_hash = report["report_hash"]
    payload["evidence_hash"] = canonical_sha256(
        {key: value for key, value in payload.items() if key != "evidence_hash"}
    )


def _import(
    root: Path,
    digest: str,
    plan,
    lock,
    preset,
    *,
    source_commit: str = SOURCE_COMMIT,
):
    return MinimalScientificSeedResultImporter(root).import_file(
        RESULT_NAME,
        expected_sha256=digest,
        expected_source_commit=source_commit,
        expected_authorization_anchor_hash=AUTHORIZATION_ANCHOR_HASH,
        expected_requester_id=REQUESTER_ID,
        expected_approver_ids=APPROVER_IDS,
        plan=plan,
        lock=lock,
        preset=preset,
    )


def test_valid_result_returns_self_verifying_receipt(
    tmp_path: Path,
    scientific_fixture,
):
    payload, plan, lock, preset = scientific_fixture
    _, digest = _write(tmp_path, payload)

    receipt = _import(tmp_path, digest, plan, lock, preset)

    assert receipt.result_status == "passed"
    assert receipt.source_file_sha256 == digest
    assert receipt.source_commit == SOURCE_COMMIT
    assert receipt.authorization_anchor_hash == AUTHORIZATION_ANCHOR_HASH
    assert receipt.requester_id == REQUESTER_ID
    assert receipt.approver_ids == APPROVER_IDS
    assert receipt.plan_hash == plan.plan_hash
    assert receipt.lock_hash == lock.lock_hash
    assert receipt.model_preset_hash == plan.model_preset_hash
    assert receipt.episode_contract_hash == (
        "f79a3c874d5babe43372e4153254d55e5168c0e83b15aa8aa5cbe4f9ea4278fa"
    )
    assert receipt.report_count == 5
    assert receipt.task_result_count == 60
    assert receipt.total_tool_call_count == receipt.usage.requests
    assert receipt.provider_fallbacks is False
    assert receipt.external_benchmark is False
    assert receipt.official_submission_performed is False
    assert receipt.official_leaderboard_claimed is False
    assert (
        MinimalScientificSeedImportReceipt.model_validate_json(
            receipt.model_dump_json()
        )
        == receipt
    )


def test_receipt_alone_rejects_passed_claim_with_zero_external_usage(
    tmp_path: Path,
    scientific_fixture,
):
    payload, plan, lock, preset = scientific_fixture
    _, digest = _write(tmp_path, payload)
    receipt = _import(tmp_path, digest, plan, lock, preset)
    forged = receipt.model_dump(mode="json")
    forged["usage"] = {
        "requests": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
    }
    forged["total_tool_call_count"] = 0
    forged["receipt_hash"] = canonical_sha256(
        {key: value for key, value in forged.items() if key != "receipt_hash"}
    )

    with pytest.raises(ValueError):
        MinimalScientificSeedImportReceipt.model_validate(forged)


def test_offline_cli_writes_verifiable_receipt(
    tmp_path: Path,
    scientific_fixture,
    monkeypatch,
):
    payload, _plan, lock, _preset = scientific_fixture
    controlled_root = tmp_path / "input"
    controlled_root.mkdir()
    _, digest = _write(controlled_root, payload)
    lock_path = tmp_path / "frozen.lock.json"
    lock_path.write_text(lock.model_dump_json(indent=2) + "\n", encoding="utf-8")
    receipt_path = tmp_path / "receipt.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "import_minimal_scientific_seed_result.py",
            "--controlled-root",
            str(controlled_root),
            "--result",
            RESULT_NAME,
            "--expected-sha256",
            digest,
            "--expected-source-commit",
            SOURCE_COMMIT,
            "--expected-authorization-anchor-hash",
            AUTHORIZATION_ANCHOR_HASH,
            "--expected-requester-id",
            REQUESTER_ID,
            "--expected-approver-id",
            APPROVER_IDS[0],
            "--expected-approver-id",
            APPROVER_IDS[1],
            "--preset",
            str(PRESET_PATH),
            "--frozen-lock",
            str(lock_path),
            "--workspace",
            str(tmp_path / "workspace"),
            "--receipt",
            str(receipt_path),
        ],
    )

    assert import_cli_main() == 0
    receipt = MinimalScientificSeedImportReceipt.model_validate_json(
        receipt_path.read_text(encoding="utf-8")
    )
    assert receipt.source_file_sha256 == digest
    assert receipt.task_result_count == 60


def test_rejects_59_of_60_even_when_all_internal_hashes_are_recomputed(
    tmp_path: Path,
    scientific_fixture,
):
    source, plan, lock, preset = scientific_fixture
    payload = json.loads(json.dumps(source))
    final_report = payload["reports"][-1]
    final_report["results"].pop()
    final_report["usage"]["task_trials"] = 11
    _rehash_result(payload)
    _, digest = _write(tmp_path, payload)

    with pytest.raises(
        MinimalScientificSeedResultImportError,
        match="exact 12 frozen Tasks",
    ):
        _import(tmp_path, digest, plan, lock, preset)


def test_rejects_task_substitution_after_coherent_rehash(
    tmp_path: Path,
    scientific_fixture,
):
    source, plan, lock, preset = scientific_fixture
    payload = json.loads(json.dumps(source))
    replacement = payload["reports"][-1]["results"][-1]
    replacement["task_id"] = "science:composition:replacement"
    replacement["task_hash"] = "f" * 64
    replacement["result_hash"] = canonical_sha256(
        {key: value for key, value in replacement.items() if key != "result_hash"}
    )
    _rehash_result(payload)
    _, digest = _write(tmp_path, payload)

    with pytest.raises(
        MinimalScientificSeedResultImportError,
        match="exact 12 frozen Tasks",
    ):
        _import(tmp_path, digest, plan, lock, preset)


def test_rejects_tampered_pydantic_evidence_hash(
    tmp_path: Path,
    scientific_fixture,
):
    source, plan, lock, preset = scientific_fixture
    payload = json.loads(json.dumps(source))
    payload["evidence_hash"] = "0" * 64
    _, digest = _write(tmp_path, payload)

    with pytest.raises(
        MinimalScientificSeedResultImportError,
        match="evidence hash",
    ):
        _import(tmp_path, digest, plan, lock, preset)


def test_rejects_caller_hash_and_exact_source_commit_drift(
    tmp_path: Path,
    scientific_fixture,
):
    payload, plan, lock, preset = scientific_fixture
    _, digest = _write(tmp_path, payload)

    with pytest.raises(MinimalScientificSeedResultImportError, match="SHA-256"):
        _import(tmp_path, "0" * 64, plan, lock, preset)
    with pytest.raises(
        MinimalScientificSeedResultImportError,
        match="exact source",
    ):
        _import(
            tmp_path,
            digest,
            plan,
            lock,
            preset,
            source_commit="2" * 40,
        )


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("authorization_anchor_hash", "f" * 64, "exact source, plan, or lock"),
        ("requester_id", "forged-requester", "exact source, plan, or lock"),
        ("approver_ids", ["forged-owner", "forged-policy"], "exact source, plan, or lock"),
    ],
)
def test_rejects_forged_governance_after_coherent_rehash(
    tmp_path: Path,
    scientific_fixture,
    field: str,
    value,
    error: str,
):
    source, plan, lock, preset = scientific_fixture
    payload = json.loads(json.dumps(source))
    payload[field] = value
    _rehash_result(payload)
    _, digest = _write(tmp_path, payload)

    with pytest.raises(MinimalScientificSeedResultImportError, match=error):
        _import(tmp_path, digest, plan, lock, preset)


def test_rejects_duplicate_json_key_before_projection(
    tmp_path: Path,
    scientific_fixture,
):
    payload, plan, lock, preset = scientific_fixture
    serialized = json.dumps(payload, sort_keys=True)
    duplicate = serialized.replace(
        '"requester_id": "requester"',
        '"requester_id": "RAW_PROVIDER_RESPONSE", "requester_id": "requester"',
        1,
    )
    assert duplicate != serialized
    path = tmp_path / RESULT_NAME
    raw = (duplicate + "\n").encode("utf-8")
    path.write_bytes(raw)

    with pytest.raises(MinimalScientificSeedResultImportError, match="valid finite JSON"):
        _import(
            tmp_path,
            hashlib.sha256(raw).hexdigest(),
            plan,
            lock,
            preset,
        )


def test_rejects_plan_lock_preset_drift(
    tmp_path: Path,
    scientific_fixture,
):
    payload, plan, lock, _preset = scientific_fixture
    _, digest = _write(tmp_path, payload)
    qwen = OpenRouterModelPreset.model_validate_json(
        QWEN_PRESET_PATH.read_text(encoding="utf-8")
    )

    with pytest.raises(
        MinimalScientificSeedResultImportError,
        match="plan, lock, and model preset",
    ):
        _import(tmp_path, digest, plan, lock, qwen)


def test_rejects_usage_cap_bypass_after_coherent_rehash(
    tmp_path: Path,
    scientific_fixture,
):
    source, plan, lock, preset = scientific_fixture
    payload = json.loads(json.dumps(source))
    capped_completion = (
        payload["usage"]["requests"]
        * plan.budget.max_output_tokens_per_request
        + 1
    )
    extra_tokens = capped_completion - payload["usage"]["completion_tokens"]
    payload["usage"]["completion_tokens"] = capped_completion
    payload["usage"]["total_tokens"] = (
        payload["usage"]["prompt_tokens"] + capped_completion
    )
    payload["reports"][-1]["usage"]["tokens"] += extra_tokens
    _rehash_result(payload)
    _, digest = _write(tmp_path, payload)

    with pytest.raises(
        MinimalScientificSeedResultImportError,
        match="usage or wall-time cap",
    ):
        _import(tmp_path, digest, plan, lock, preset)


def test_rejects_passed_result_with_no_external_model_requests(
    tmp_path: Path,
    scientific_fixture,
):
    source, plan, lock, preset = scientific_fixture
    payload = json.loads(json.dumps(source))
    for report in payload["reports"]:
        for result in report["results"]:
            result["tool_calls"] = 0
            result["result_hash"] = canonical_sha256(
                {key: value for key, value in result.items() if key != "result_hash"}
            )
        report["usage"]["tokens"] = 0
        report["usage"]["tool_calls"] = 0
        report["usage"]["cost_usd"] = 0.0
    payload["usage"] = {
        "requests": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
    }
    _rehash_result(payload)
    _, digest = _write(tmp_path, payload)

    with pytest.raises(
        MinimalScientificSeedResultImportError,
        match="frozen episode contract",
    ):
        _import(tmp_path, digest, plan, lock, preset)


def test_rejects_self_consistent_episode_step_or_pass_matrix_drift(
    tmp_path: Path,
    scientific_fixture,
):
    source, plan, lock, preset = scientific_fixture
    payload = json.loads(json.dumps(source))
    task = payload["reports"][0]["results"][0]
    task["episode_steps"] += 1
    task["result_hash"] = canonical_sha256(
        {key: value for key, value in task.items() if key != "result_hash"}
    )
    _rehash_result(payload)
    _, digest = _write(tmp_path, payload)

    with pytest.raises(
        MinimalScientificSeedResultImportError,
        match="frozen episode contract",
    ):
        _import(tmp_path, digest, plan, lock, preset)


def test_rejects_fallback_claim_even_after_evidence_rehash(
    tmp_path: Path,
    scientific_fixture,
):
    source, plan, lock, preset = scientific_fixture
    payload = json.loads(json.dumps(source))
    payload["provider_fallbacks"] = True
    _rehash_result(payload)
    _, digest = _write(tmp_path, payload)

    with pytest.raises(
        MinimalScientificSeedResultImportError,
        match="strict schema",
    ):
        _import(tmp_path, digest, plan, lock, preset)


def test_rejects_secret_before_schema_projection(
    tmp_path: Path,
    scientific_fixture,
):
    source, plan, lock, preset = scientific_fixture
    payload = json.loads(json.dumps(source))
    payload["api_key"] = "sk-or-v1-abcdefghijklmnop"
    _, digest = _write(tmp_path, payload)

    with pytest.raises(
        MinimalScientificSeedResultImportError,
        match="potential credential",
    ):
        _import(tmp_path, digest, plan, lock, preset)


@pytest.mark.parametrize("raw_key", ["raw_response", "trajectory", "reasoning"])
def test_rejects_nested_raw_evidence_fields_before_pydantic_projection(
    tmp_path: Path,
    scientific_fixture,
    raw_key: str,
):
    source, plan, lock, preset = scientific_fixture
    payload = json.loads(json.dumps(source))
    payload["reports"][0]["results"][0][raw_key] = "not retained"
    _, digest = _write(tmp_path, payload)

    with pytest.raises(
        MinimalScientificSeedResultImportError,
        match="prohibited raw evidence field",
    ):
        _import(tmp_path, digest, plan, lock, preset)


@pytest.mark.parametrize("value", ["1", 1.0, True])
def test_rejects_non_integer_tool_call_evidence_before_coercion(
    tmp_path: Path,
    scientific_fixture,
    value,
):
    source, plan, lock, preset = scientific_fixture
    payload = json.loads(json.dumps(source))
    payload["reports"][0]["results"][0]["tool_calls"] = value
    _, digest = _write(tmp_path, payload)

    with pytest.raises(
        MinimalScientificSeedResultImportError,
        match="tool_calls must be an integer",
    ):
        _import(tmp_path, digest, plan, lock, preset)


@pytest.mark.parametrize(
    ("container", "field", "value"),
    [
        ("usage", "prompt_tokens", "100"),
        ("report_usage", "tokens", "1200"),
        ("task", "episode_steps", "3"),
    ],
)
def test_rejects_primitive_type_coercion_before_evidence_admission(
    tmp_path: Path,
    scientific_fixture,
    container: str,
    field: str,
    value: str,
):
    source, plan, lock, preset = scientific_fixture
    payload = json.loads(json.dumps(source))
    if container == "usage":
        payload["usage"][field] = value
    elif container == "report_usage":
        payload["reports"][0]["usage"][field] = value
    else:
        payload["reports"][0]["results"][0][field] = value
    _rehash_result(payload)
    _, digest = _write(tmp_path, payload)

    with pytest.raises(
        MinimalScientificSeedResultImportError,
        match="strict schema",
    ):
        _import(tmp_path, digest, plan, lock, preset)


def test_rejects_symlink_nonregular_and_oversized_files(
    tmp_path: Path,
    scientific_fixture,
):
    payload, plan, lock, preset = scientific_fixture
    outside = tmp_path / "outside.json"
    data = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
    outside.write_bytes(data)
    link_root = tmp_path / "controlled"
    link_root.mkdir()
    link = link_root / RESULT_NAME
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("File symlinks are unavailable on this platform.")
    digest = hashlib.sha256(data).hexdigest()

    with pytest.raises(MinimalScientificSeedResultImportError, match="symlink"):
        _import(link_root, digest, plan, lock, preset)

    regular_root = tmp_path / "regular"
    regular_root.mkdir()
    _, regular_digest = _write(regular_root, payload)
    with pytest.raises(MinimalScientificSeedResultImportError, match="size limit"):
        MinimalScientificSeedResultImporter(
            regular_root,
            max_bytes=10,
        ).import_file(
            RESULT_NAME,
            expected_sha256=regular_digest,
            expected_source_commit=SOURCE_COMMIT,
            expected_authorization_anchor_hash=AUTHORIZATION_ANCHOR_HASH,
            expected_requester_id=REQUESTER_ID,
            expected_approver_ids=APPROVER_IDS,
            plan=plan,
            lock=lock,
            preset=preset,
        )


def test_rejects_unsafe_path_and_wrong_filename(
    tmp_path: Path,
    scientific_fixture,
):
    payload, plan, lock, preset = scientific_fixture
    _, digest = _write(tmp_path, payload)
    importer = MinimalScientificSeedResultImporter(tmp_path)

    with pytest.raises(MinimalScientificSeedResultImportError, match="unsafe"):
        importer.import_file(
            f"../{RESULT_NAME}",
            expected_sha256=digest,
            expected_source_commit=SOURCE_COMMIT,
            expected_authorization_anchor_hash=AUTHORIZATION_ANCHOR_HASH,
            expected_requester_id=REQUESTER_ID,
            expected_approver_ids=APPROVER_IDS,
            plan=plan,
            lock=lock,
            preset=preset,
        )
    with pytest.raises(MinimalScientificSeedResultImportError, match="accepts only"):
        importer.import_file(
            "result.json",
            expected_sha256=digest,
            expected_source_commit=SOURCE_COMMIT,
            expected_authorization_anchor_hash=AUTHORIZATION_ANCHOR_HASH,
            expected_requester_id=REQUESTER_ID,
            expected_approver_ids=APPROVER_IDS,
            plan=plan,
            lock=lock,
            preset=preset,
        )
