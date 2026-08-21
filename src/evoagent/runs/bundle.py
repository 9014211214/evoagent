from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from evoagent.runs.models import (
    ExternalSignatureReference,
    ReproducibleRunManifest,
    ReproducibleRunSpec,
    RunArtifactRecord,
    RunArtifactSource,
    RunBundleVerification,
    RunManifestCheckpoint,
)


_MANIFEST_NAME = "manifest.json"
_SIGNATURE_NAME = "external-signature.json"
_SECRET_PATTERNS = (
    re.compile(rb"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(rb"\bhf_[A-Za-z0-9]{20,}\b"),
    re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


class RunBundleError(ValueError):
    pass


def _canonical_json(value: Any) -> bytes:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _contains_secret(value: bytes) -> bool:
    return any(pattern.search(value) for pattern in _SECRET_PATTERNS)


def _manifest_hash(spec: ReproducibleRunSpec, artifacts: tuple[RunArtifactRecord, ...]) -> str:
    return _sha256_bytes(
        _canonical_json(
            {
                "format_version": "evoagent-run-bundle-v1",
                "spec": spec.model_dump(mode="json"),
                "artifacts": [item.model_dump(mode="json") for item in artifacts],
            }
        )
    )


def _atomic_write(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


class ReproducibleRunBundleManager:
    def preview(
        self,
        *,
        spec: ReproducibleRunSpec,
        artifact_sources: tuple[RunArtifactSource, ...],
    ) -> ReproducibleRunManifest:
        prepared = self._prepare_sources(artifact_sources)
        artifacts = tuple(item[2] for item in prepared)
        return ReproducibleRunManifest(
            spec=spec,
            artifacts=artifacts,
            manifest_hash=_manifest_hash(spec, artifacts),
        )

    def build(
        self,
        *,
        spec: ReproducibleRunSpec,
        artifact_sources: tuple[RunArtifactSource, ...],
        output_directory: str | Path,
        external_signature_reference: ExternalSignatureReference | None = None,
    ) -> ReproducibleRunManifest:
        destination = Path(output_directory)
        if destination.exists():
            raise RunBundleError("Run bundle destination must not already exist.")
        prepared = self._prepare_sources(artifact_sources)
        artifacts = tuple(item[2] for item in prepared)
        manifest = ReproducibleRunManifest(
            spec=spec,
            artifacts=artifacts,
            manifest_hash=_manifest_hash(spec, artifacts),
        )
        self._validate_signature_requirement(manifest, external_signature_reference)

        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.parent / f".{destination.name}.{uuid.uuid4().hex}.tmp"
        temporary.mkdir(mode=0o700)
        artifacts_directory = temporary / "artifacts"
        artifacts_directory.mkdir()
        try:
            for _, source_bytes, record in prepared:
                target = temporary / record.relative_path
                with target.open("wb") as handle:
                    handle.write(source_bytes)
                    handle.flush()
                    os.fsync(handle.fileno())

            manifest_bytes = manifest.model_dump_json(indent=2).encode("utf-8") + b"\n"
            if _contains_secret(manifest_bytes):
                raise RunBundleError("Potential secret detected in run manifest.")
            _atomic_write(temporary / _MANIFEST_NAME, manifest_bytes)

            if external_signature_reference is not None:
                signature_bytes = (
                    external_signature_reference.model_dump_json(indent=2).encode("utf-8")
                    + b"\n"
                )
                if _contains_secret(signature_bytes):
                    raise RunBundleError(
                        "Potential secret detected in external signature metadata."
                    )
                _atomic_write(temporary / _SIGNATURE_NAME, signature_bytes)

            os.replace(temporary, destination)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise

        self.verify(destination)
        return manifest

    def checkpoint(self, bundle_directory: str | Path) -> RunManifestCheckpoint:
        verification = self.verify(bundle_directory)
        return RunManifestCheckpoint(manifest_hash=verification.manifest_hash)

    def attach_external_signature(
        self,
        bundle_directory: str | Path,
        reference: ExternalSignatureReference,
    ) -> None:
        root = Path(bundle_directory)
        manifest = self.load_manifest(root)
        if manifest.spec.external_signature_required:
            raise RunBundleError(
                "A signature-required bundle must be built atomically with its signature reference."
            )
        verification = self.verify(root)
        if reference.signed_manifest_hash != verification.manifest_hash:
            raise RunBundleError(
                "External signature reference is not bound to the verified manifest hash."
            )
        target = root / _SIGNATURE_NAME
        if target.exists():
            raise RunBundleError("Run bundle already contains an external signature reference.")
        content = reference.model_dump_json(indent=2).encode("utf-8") + b"\n"
        if _contains_secret(content):
            raise RunBundleError("Potential secret detected in external signature metadata.")
        _atomic_write(target, content)
        self.verify(root)

    def load_manifest(self, bundle_directory: str | Path) -> ReproducibleRunManifest:
        root = Path(bundle_directory)
        manifest_path = root / _MANIFEST_NAME
        if root.is_symlink() or not root.is_dir():
            raise RunBundleError("Run bundle must be a regular directory.")
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise RunBundleError("Run bundle manifest is missing or symlinked.")
        try:
            manifest = ReproducibleRunManifest.model_validate_json(
                manifest_path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError, ValueError) as exc:
            raise RunBundleError("Run bundle manifest is invalid.") from exc
        return manifest

    def verify(
        self,
        bundle_directory: str | Path,
        *,
        checkpoint: RunManifestCheckpoint | None = None,
    ) -> RunBundleVerification:
        root = Path(bundle_directory)
        manifest = self.load_manifest(root)
        expected_manifest_hash = _manifest_hash(manifest.spec, manifest.artifacts)
        if manifest.manifest_hash != expected_manifest_hash:
            raise RunBundleError("Run manifest hash mismatch.")
        if checkpoint is not None and checkpoint.manifest_hash != manifest.manifest_hash:
            raise RunBundleError("Run manifest does not match the external checkpoint.")

        expected_files = {_MANIFEST_NAME}
        expected_directories = {"artifacts"}
        for record in manifest.artifacts:
            expected_files.add(record.relative_path)
            parent = Path(record.relative_path).parent
            while str(parent) not in {"", "."}:
                expected_directories.add(parent.as_posix())
                parent = parent.parent

        signature_path = root / _SIGNATURE_NAME
        signature_present = signature_path.exists()
        if manifest.spec.external_signature_required and not signature_present:
            raise RunBundleError("Run manifest requires an external signature reference.")
        if signature_present:
            expected_files.add(_SIGNATURE_NAME)

        actual_files: set[str] = set()
        actual_directories: set[str] = set()
        for path in root.rglob("*"):
            if path.is_symlink():
                raise RunBundleError(f"Symlink is not allowed in run bundle: {path.name}")
            relative = path.relative_to(root).as_posix()
            if path.is_dir():
                actual_directories.add(relative)
            elif path.is_file():
                actual_files.add(relative)
            else:
                raise RunBundleError(f"Unsupported filesystem entry in run bundle: {relative}")

        if actual_files != expected_files:
            missing = sorted(expected_files - actual_files)
            extra = sorted(actual_files - expected_files)
            raise RunBundleError(
                f"Run bundle file set mismatch; missing={missing}, extra={extra}."
            )
        if actual_directories != expected_directories:
            missing = sorted(expected_directories - actual_directories)
            extra = sorted(actual_directories - expected_directories)
            raise RunBundleError(
                f"Run bundle directory set mismatch; missing={missing}, extra={extra}."
            )

        manifest_bytes = (root / _MANIFEST_NAME).read_bytes()
        if _contains_secret(manifest_bytes):
            raise RunBundleError("Potential secret detected in run manifest.")

        for record in manifest.artifacts:
            path = root / record.relative_path
            try:
                path.resolve(strict=True).relative_to(root.resolve(strict=True))
            except (OSError, ValueError) as exc:
                raise RunBundleError("Artifact path escapes the run bundle.") from exc
            if path.is_symlink() or not path.is_file():
                raise RunBundleError(f"Run artifact is missing or symlinked: {record.logical_name}")
            if path.stat().st_size != record.size_bytes:
                raise RunBundleError(f"Run artifact size mismatch: {record.logical_name}")
            if _sha256_file(path) != record.sha256:
                raise RunBundleError(f"Run artifact digest mismatch: {record.logical_name}")
            if _contains_secret(path.read_bytes()):
                raise RunBundleError(f"Potential secret detected in artifact: {record.logical_name}")

        if signature_present:
            if signature_path.is_symlink() or not signature_path.is_file():
                raise RunBundleError("External signature reference must be a regular file.")
            try:
                reference = ExternalSignatureReference.model_validate_json(
                    signature_path.read_text(encoding="utf-8")
                )
            except (OSError, ValidationError, ValueError) as exc:
                raise RunBundleError("External signature reference is invalid.") from exc
            if reference.signed_manifest_hash != manifest.manifest_hash:
                raise RunBundleError(
                    "External signature reference is not bound to the verified manifest hash."
                )
            if _contains_secret(signature_path.read_bytes()):
                raise RunBundleError("Potential secret detected in external signature metadata.")

        return RunBundleVerification(
            bundle_path=str(root.resolve()),
            manifest_hash=manifest.manifest_hash,
            external_checkpoint_matched=checkpoint is not None,
            artifacts_verified=len(manifest.artifacts),
            external_signature_reference_present=signature_present,
            external_signature_cryptographically_verified=False,
        )

    @staticmethod
    def _validate_signature_requirement(
        manifest: ReproducibleRunManifest,
        reference: ExternalSignatureReference | None,
    ) -> None:
        if manifest.spec.external_signature_required and reference is None:
            raise RunBundleError(
                "This run status requires an external signature reference during atomic build."
            )
        if reference is not None and reference.signed_manifest_hash != manifest.manifest_hash:
            raise RunBundleError(
                "External signature reference is not bound to the prepared manifest hash."
            )

    @staticmethod
    def _prepare_sources(
        artifact_sources: tuple[RunArtifactSource, ...],
    ) -> tuple[tuple[RunArtifactSource, bytes, RunArtifactRecord], ...]:
        if not artifact_sources:
            raise RunBundleError("At least one artifact source is required.")
        logical_names = [item.logical_name for item in artifact_sources]
        if len(logical_names) != len(set(logical_names)):
            raise RunBundleError("Run artifact logical names must be unique.")

        prepared: list[tuple[RunArtifactSource, bytes, RunArtifactRecord]] = []
        for index, source in enumerate(
            sorted(artifact_sources, key=lambda item: item.logical_name), start=1
        ):
            source_path = Path(source.source_path)
            if source_path.is_symlink() or not source_path.is_file():
                raise RunBundleError(
                    f"Artifact source must be a regular non-symlink file: {source.logical_name}"
                )
            source_bytes = source_path.read_bytes()
            if _contains_secret(source_bytes):
                raise RunBundleError(
                    f"Potential secret detected in artifact: {source.logical_name}"
                )
            relative_path = f"artifacts/{index:03d}-{source.logical_name}"
            prepared.append(
                (
                    source,
                    source_bytes,
                    RunArtifactRecord(
                        logical_name=source.logical_name,
                        kind=source.kind,
                        relative_path=relative_path,
                        media_type=source.media_type,
                        required=source.required,
                        size_bytes=len(source_bytes),
                        sha256=_sha256_bytes(source_bytes),
                    ),
                )
            )
        return tuple(prepared)
