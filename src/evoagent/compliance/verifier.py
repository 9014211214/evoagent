from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import ValidationError

from evoagent.compliance.models import ComplianceVerification, ThirdPartyLock


class ComplianceError(ValueError):
    pass


def _lock_payload(lock: ThirdPartyLock | dict) -> dict:
    payload = lock.model_dump(mode="json") if isinstance(lock, ThirdPartyLock) else dict(lock)
    payload.pop("lock_hash", None)
    return payload


def _lock_hash(lock: ThirdPartyLock | dict) -> str:
    canonical = json.dumps(
        _lock_payload(lock),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ThirdPartyComplianceVerifier:
    def load_lock(self, path: str | Path) -> ThirdPartyLock:
        try:
            lock = ThirdPartyLock.model_validate_json(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValidationError, ValueError) as exc:
            raise ComplianceError("Third-party lock is invalid.") from exc
        if lock.lock_hash != _lock_hash(lock):
            raise ComplianceError("Third-party lock hash mismatch.")
        return lock

    def verify(
        self,
        *,
        lock_path: str | Path,
        notices_path: str | Path,
    ) -> ComplianceVerification:
        lock_file = Path(lock_path)
        notices_file = Path(notices_path)
        lock = self.load_lock(lock_file)
        try:
            notices = notices_file.read_text(encoding="utf-8")
        except OSError as exc:
            raise ComplianceError("Third-party notices file is unavailable.") from exc

        for component in lock.components:
            required_tokens = (
                component.name,
                component.repository,
                component.reviewed_commit,
                component.license_spdx,
                component.license_path,
                component.license_git_blob_sha,
                component.integration_method.value,
                f"Source copied: {str(component.source_copied).lower()}",
                f"Modified: {str(component.modified).lower()}",
                component.required_attribution,
            )
            missing = [token for token in required_tokens if token not in notices]
            if missing:
                raise ComplianceError(
                    f"Third-party notices are incomplete for {component.name}: {missing}."
                )
            if component.notice_path:
                for token in (component.notice_path, component.notice_git_blob_sha):
                    if token not in notices:
                        raise ComplianceError(
                            f"Pinned NOTICE metadata is missing for {component.name}."
                        )
            elif "Upstream NOTICE at reviewed commit: none found" not in self._component_section(
                notices, component.name
            ):
                raise ComplianceError(
                    f"NOTICE review result is missing for {component.name}."
                )
            if component.modified and component.modifications_summary not in notices:
                raise ComplianceError(
                    f"Modification disclosure is missing for {component.name}."
                )

        return ComplianceVerification(
            components_verified=len(lock.components),
            notices_file=str(notices_file.resolve()),
            lock_file=str(lock_file.resolve()),
            lock_hash=lock.lock_hash,
        )

    @staticmethod
    def _component_section(notices: str, name: str) -> str:
        marker = f"## {name}\n"
        start = notices.find(marker)
        if start < 0:
            return ""
        next_section = notices.find("\n## ", start + len(marker))
        return notices[start:] if next_section < 0 else notices[start:next_section]


__all__ = ["ComplianceError", "ThirdPartyComplianceVerifier"]
