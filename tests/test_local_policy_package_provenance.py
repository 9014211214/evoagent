from __future__ import annotations

import pytest

from evoagent.local_policy import (
    LocalPolicyPromotionPackageError,
    LocalPolicyPromotionPackageManager,
)
from tests.test_local_policy_promotion_tamper import (
    _completed_package,
    _rehash_package,
)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("framework_version", "9.9.9-forged"),
        (
            "source_repository",
            "https://example.invalid/forged/local-policy",
        ),
        ("third_party_lock_hash", "0" * 64),
    ),
)
def test_rehashed_top_level_provenance_substitution_is_rejected(
    tmp_path,
    monkeypatch,
    field,
    value,
):
    _, package = _completed_package(tmp_path, monkeypatch)
    forged = package.model_copy(update={field: value})
    forged = _rehash_package(forged)

    with pytest.raises(
        LocalPolicyPromotionPackageError,
        match="provenance differs",
    ):
        LocalPolicyPromotionPackageManager.verify(forged)


def test_v2_2_source_commit_remains_successor_build_provenance(
    tmp_path,
    monkeypatch,
):
    _, package = _completed_package(tmp_path, monkeypatch)
    base = (
        package.accepted_program_package.runtime_attested_package
        .schema_attested_package.attested_package.base_package
    )

    assert package.source_commit != base.source_commit
    assert LocalPolicyPromotionPackageManager.verify(package) is True
