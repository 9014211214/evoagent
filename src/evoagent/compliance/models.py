from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_GIT_SHA_PATTERN = r"^[0-9a-f]{40}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SPDX_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9.+-]*$"


class IntegrationMethod(str, Enum):
    CLI_ADAPTER = "cli_adapter"
    DATASET_REFERENCE = "dataset_reference"
    EXTERNAL_CHECKOUT = "external_checkout"
    PACKAGE_DEPENDENCY = "package_dependency"
    SUBPROCESS_ADAPTER = "subprocess_adapter"
    GIT_SUBMODULE = "git_submodule"
    FORK = "fork"
    SOURCE_COPY = "source_copy"


class ThirdPartyComponent(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    repository: str
    reviewed_commit: str = Field(pattern=_GIT_SHA_PATTERN)
    license_spdx: str = Field(pattern=_SPDX_PATTERN)
    license_path: str
    license_git_blob_sha: str = Field(pattern=_GIT_SHA_PATTERN)
    notice_path: str | None = None
    notice_git_blob_sha: str | None = Field(default=None, pattern=_GIT_SHA_PATTERN)
    integration_method: IntegrationMethod
    source_copied: bool = False
    modified: bool = False
    modifications_summary: str | None = None
    required_attribution: str
    purpose: str

    @field_validator(
        "name",
        "license_spdx",
        "license_path",
        "required_attribution",
        "purpose",
    )
    @classmethod
    def require_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Third-party lock fields must not be empty.")
        return normalized

    @field_validator("repository")
    @classmethod
    def validate_repository(cls, value: str) -> str:
        if not value.startswith("https://github.com/"):
            raise ValueError("Third-party repository must be an HTTPS GitHub URL.")
        return value.rstrip("/")

    @field_validator("license_path", "notice_path")
    @classmethod
    def validate_repository_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.replace("\\", "/")
        if normalized.startswith("/") or any(
            part in {"", ".", ".."} for part in normalized.split("/")
        ):
            raise ValueError("Third-party license/notice path must be repository-relative.")
        return normalized

    @model_validator(mode="after")
    def validate_copy_and_notice_rules(self):
        if self.modified and not self.modifications_summary:
            raise ValueError("Modified third-party code requires a modifications summary.")
        if self.notice_path is None and self.notice_git_blob_sha is not None:
            raise ValueError("NOTICE blob SHA cannot be set without a NOTICE path.")
        if self.notice_path is not None and self.notice_git_blob_sha is None:
            raise ValueError("NOTICE path requires a pinned NOTICE blob SHA.")
        if self.source_copied and self.integration_method not in {
            IntegrationMethod.SOURCE_COPY,
            IntegrationMethod.FORK,
            IntegrationMethod.GIT_SUBMODULE,
        }:
            raise ValueError("Copied source requires an explicit source-bearing integration method.")
        return self


class ThirdPartyLock(BaseModel):
    model_config = ConfigDict(frozen=True)

    format_version: Literal["evoagent-third-party-lock-v1"] = (
        "evoagent-third-party-lock-v1"
    )
    reviewed_at: datetime
    components: tuple[ThirdPartyComponent, ...]
    lock_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("components")
    @classmethod
    def validate_components(
        cls, value: tuple[ThirdPartyComponent, ...]
    ) -> tuple[ThirdPartyComponent, ...]:
        if not value:
            raise ValueError("Third-party lock requires at least one component.")
        names = [item.name.casefold() for item in value]
        repositories = [item.repository.casefold() for item in value]
        if len(set(names)) != len(names):
            raise ValueError("Third-party component names must be unique.")
        if len(set(repositories)) != len(repositories):
            raise ValueError("Third-party repositories must be unique.")
        return value


class ComplianceVerification(BaseModel):
    model_config = ConfigDict(frozen=True)

    components_verified: int = Field(ge=0)
    notices_file: str
    lock_file: str
    lock_hash: str = Field(pattern=_SHA256_PATTERN)
    verified: bool = True
