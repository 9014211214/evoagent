from __future__ import annotations

import json
import os

import pytest

from evoagent.local_rl import (
    LocalRLPackageManager,
    LocalRLRegistryCheckpoint,
    LocalRLEventType,
    ProgramLocalRLBindingError,
    SQLiteLocalRLRepository,
)
from evoagent.model_registry.models import canonical_sha256
from tests.test_program_local_rl_binding import bound_context


def _rehash_bound_package(package):
    payload = package.model_dump(mode="json", exclude={"package_hash"})
    return package.model_copy(
        update={"package_hash": canonical_sha256(payload)}
    )


def _replace_audit_reason(package, *, event_type, reason):
    previous_hash = "0" * 64
    events = []
    for event in package.audit_events:
        event_reason = reason if event.event_type == event_type else event.reason
        event_hash = SQLiteLocalRLRepository._event_hash(
            sequence=event.sequence,
            event_id=event.event_id,
            event_type=event.event_type,
            run_id=event.run_id,
            actor_id=event.actor_id,
            reason=event_reason,
            payload=event.payload,
            created_at=event.created_at,
            previous_hash=previous_hash,
        )
        events.append(
            event.model_copy(
                update={
                    "reason": event_reason,
                    "previous_hash": previous_hash,
                    "event_hash": event_hash,
                }
            )
        )
        previous_hash = event_hash
    checkpoint = LocalRLRegistryCheckpoint(
        event_count=len(events),
        head_hash=previous_hash,
    )
    forged = package.model_copy(
        update={
            "audit_events": tuple(events),
            "audit_checkpoint": checkpoint,
        }
    )
    payload = forged.model_dump(mode="json", exclude={"package_hash"})
    return forged.model_copy(
        update={"package_hash": canonical_sha256(payload)}
    )


def test_program_bound_package_exports_and_loads_exactly(bound_context, tmp_path):
    manager = bound_context["manager"]
    package = bound_context["bound_package"]
    path = manager.export_file(
        package,
        tmp_path / "program-bound-local-rl-package.json",
    )

    loaded = manager.load_file(path)

    assert loaded == package
    assert loaded.package_hash == package.package_hash
    assert manager.verify(loaded) is True


def test_program_bound_package_rejects_symlink_input(bound_context, tmp_path):
    manager = bound_context["manager"]
    package = bound_context["bound_package"]
    target = manager.export_file(package, tmp_path / "target.json")
    link = tmp_path / "linked-package.json"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable in this test environment")

    with pytest.raises(
        ProgramLocalRLBindingError,
        match="regular non-symlink",
    ):
        manager.load_file(link)


def test_loaded_package_rejects_rehashed_release_authority(
    bound_context,
    tmp_path,
):
    manager = bound_context["manager"]
    payload = bound_context["bound_package"].model_dump(mode="json")
    payload["release_authorized"] = True
    payload["package_hash"] = canonical_sha256(
        {key: value for key, value in payload.items() if key != "package_hash"}
    )
    path = tmp_path / "forged-release-authority.json"
    path.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ProgramLocalRLBindingError,
        match="invalid",
    ):
        manager.load_file(path)


def test_rehashed_local_audit_reason_is_rejected_by_public_binding_manager(
    bound_context,
):
    local_package = _replace_audit_reason(
        bound_context["local_package"],
        event_type=LocalRLEventType.TRAINING_COMPLETED,
        reason="coherently forged local optimization claim",
    )
    assert LocalRLPackageManager().verify(local_package) is True

    outer = bound_context["bound_package"].model_copy(
        update={"local_rl_package": local_package}
    )
    outer = _rehash_bound_package(outer)

    with pytest.raises(
        ProgramLocalRLBindingError,
        match="audit reasons differ",
    ):
        bound_context["manager"].verify(outer)
