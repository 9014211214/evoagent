from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from evoagent.compliance import (
    ComplianceError,
    IntegrationMethod,
    ThirdPartyComponent,
    ThirdPartyComplianceVerifier,
    ThirdPartyLock,
)


ROOT = Path(__file__).resolve().parents[1]


def rehash(payload: dict) -> dict:
    material = dict(payload)
    material.pop("lock_hash", None)
    canonical = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    payload["lock_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return payload


def test_repository_third_party_lock_and_notices_verify():
    result = ThirdPartyComplianceVerifier().verify(
        lock_path=ROOT / "THIRD_PARTY_LOCK.json",
        notices_path=ROOT / "THIRD_PARTY_NOTICES.md",
    )
    assert result.verified is True
    assert result.components_verified == 9


def test_lock_hash_and_notice_omission_are_rejected(tmp_path):
    lock_payload = json.loads((ROOT / "THIRD_PARTY_LOCK.json").read_text(encoding="utf-8"))
    lock_payload["components"][0]["purpose"] = "tampered"
    lock_path = tmp_path / "lock.json"
    lock_path.write_text(json.dumps(lock_payload), encoding="utf-8")
    with pytest.raises(ComplianceError, match="hash mismatch"):
        ThirdPartyComplianceVerifier().load_lock(lock_path)

    valid_lock = tmp_path / "valid-lock.json"
    valid_lock.write_text(
        (ROOT / "THIRD_PARTY_LOCK.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    notices = tmp_path / "notices.md"
    notices.write_text(
        (ROOT / "THIRD_PARTY_NOTICES.md")
        .read_text(encoding="utf-8")
        .replace("550a209701701e6a9ac7cac70b8dbd508822d467", "missing"),
        encoding="utf-8",
    )
    with pytest.raises(ComplianceError, match="incomplete"):
        ThirdPartyComplianceVerifier().verify(
            lock_path=valid_lock,
            notices_path=notices,
        )


def test_duplicate_components_and_unsafe_copy_metadata_are_rejected():
    payload = json.loads((ROOT / "THIRD_PARTY_LOCK.json").read_text(encoding="utf-8"))
    payload["components"].append(dict(payload["components"][0]))
    payload = rehash(payload)
    with pytest.raises(ValidationError, match="unique"):
        ThirdPartyLock.model_validate(payload)

    component = dict(
        name="Copied Project",
        repository="https://github.com/example/copied-project",
        reviewed_commit="a" * 40,
        license_spdx="Apache-2.0",
        license_path="LICENSE",
        license_git_blob_sha="b" * 40,
        integration_method=IntegrationMethod.CLI_ADAPTER,
        source_copied=True,
        modified=False,
        required_attribution="Retain required notices.",
        purpose="Test copy policy.",
    )
    with pytest.raises(ValidationError, match="source-bearing"):
        ThirdPartyComponent.model_validate(component)

    component.update(
        integration_method=IntegrationMethod.SOURCE_COPY,
        modified=True,
    )
    with pytest.raises(ValidationError, match="modifications summary"):
        ThirdPartyComponent.model_validate(component)


def test_same_repository_supports_distinct_reviewed_runtime_pins():
    payload = json.loads((ROOT / "THIRD_PARTY_LOCK.json").read_text(encoding="utf-8"))
    harbor = next(item for item in payload["components"] if item["name"] == "Harbor")
    second_pin = dict(harbor)
    second_pin.update(
        name="Harbor distinct runtime",
        reviewed_commit="f" * 40,
        integration_method=IntegrationMethod.EXTERNAL_CHECKOUT,
    )
    payload["components"].append(second_pin)

    lock = ThirdPartyLock.model_validate(rehash(payload))

    assert lock.components[-1].repository == lock.components[0].repository
    assert lock.components[-1].reviewed_commit == "f" * 40


def test_exact_duplicate_integration_pin_is_rejected_even_with_an_alias():
    payload = json.loads((ROOT / "THIRD_PARTY_LOCK.json").read_text(encoding="utf-8"))
    duplicate = dict(payload["components"][0])
    duplicate["name"] = "Ambiguous Harbor alias"
    payload["components"].append(duplicate)

    with pytest.raises(ValidationError, match="integration pins must be unique"):
        ThirdPartyLock.model_validate(rehash(payload))


def test_rehashed_lock_still_requires_valid_component_schema(tmp_path):
    payload = json.loads((ROOT / "THIRD_PARTY_LOCK.json").read_text(encoding="utf-8"))
    payload["components"][0]["integration_method"] = "invented_integration"
    lock_path = tmp_path / "lock.json"
    lock_path.write_text(json.dumps(rehash(payload)), encoding="utf-8")
    with pytest.raises(ComplianceError, match="invalid"):
        ThirdPartyComplianceVerifier().load_lock(lock_path)
