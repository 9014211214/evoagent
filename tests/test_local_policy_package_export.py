from __future__ import annotations

import pytest

from evoagent.local_policy import (
    LocalPolicyPromotionPackageError,
    LocalPolicyPromotionPackageManager,
)
from tests.test_local_policy_promotion_tamper import _completed_package


def test_exact_package_reexport_is_read_only(tmp_path, monkeypatch):
    _, package = _completed_package(tmp_path, monkeypatch)
    manager = LocalPolicyPromotionPackageManager()
    path = tmp_path / "immutable-promotion-package.json"

    first = manager.export_file(package, path)
    before = path.read_bytes()
    second = manager.export_file(package, path)

    assert first == second == path
    assert path.read_bytes() == before


def test_conflicting_existing_package_is_not_overwritten(
    tmp_path,
    monkeypatch,
):
    _, package = _completed_package(tmp_path, monkeypatch)
    manager = LocalPolicyPromotionPackageManager()
    path = tmp_path / "conflicting-promotion-package.json"
    existing = b'{"foreign":"evidence"}\n'
    path.write_bytes(existing)

    with pytest.raises(
        LocalPolicyPromotionPackageError,
        match="differs from immutable evidence",
    ):
        manager.export_file(package, path)

    assert path.read_bytes() == existing
