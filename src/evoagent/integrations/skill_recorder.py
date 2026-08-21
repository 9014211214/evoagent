from __future__ import annotations

import hashlib
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evoagent.acquisition.models import AcceptanceCase, SkillAcquisitionCandidate
from evoagent.skills.models import SkillSpec


SKILL_RECORDER_REPOSITORY = "https://github.com/microsoft/skill-recorder"
SKILL_RECORDER_RELEASE = "0.4.2"
SKILL_RECORDER_COMMIT = "93b3ccf887a46d3e3b91ed856d888d399b02c6e4"
_SUPPORTED_ARCHITECTURES = {"scout", "cowork", "agent-skill"}
_TOKEN_RE = re.compile(r"\{\{\s*([a-z0-9_]+)\s*\}\}", re.IGNORECASE)
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
_SAFE_SESSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SAFE_SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,59}$")
_SAFE_VALUE_ID = re.compile(r"^[a-z0-9_]{1,40}$")


class SkillRecorderImportError(ValueError):
    pass


class RecorderValue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    name: str = ""
    value: str = ""

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _SAFE_VALUE_ID.fullmatch(normalized):
            raise ValueError("Skill Recorder value ID is not normalized.")
        return normalized


class RecorderPlanStep(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["calculation", "action"]
    title: str = ""
    text: str
    tool: str = ""

    @field_validator("text")
    @classmethod
    def require_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Skill Recorder plan step text must not be empty.")
        return normalized


class RecorderSkillPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    architecture: str
    name: str
    title: str
    description: str
    summary: str = ""
    generalization: str = ""
    values: tuple[RecorderValue, ...] = ()
    steps: tuple[RecorderPlanStep, ...]
    allowedTools: tuple[str, ...] = ()

    @field_validator("architecture")
    @classmethod
    def validate_architecture(cls, value: str) -> str:
        if value not in _SUPPORTED_ARCHITECTURES:
            raise ValueError(f"Unsupported Skill Recorder architecture: {value}")
        return value

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not _SAFE_SKILL_NAME.fullmatch(value):
            raise ValueError("Skill Recorder name must be safe kebab-case.")
        return value

    @field_validator("title", "description")
    @classmethod
    def require_plan_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Skill Recorder plan title and description must not be empty.")
        return normalized

    @field_validator("steps")
    @classmethod
    def require_steps(
        cls, value: tuple[RecorderPlanStep, ...]
    ) -> tuple[RecorderPlanStep, ...]:
        if not value:
            raise ValueError("Skill Recorder plan must contain at least one step.")
        return value

    @model_validator(mode="after")
    def validate_values(self):
        ids = [item.id for item in self.values]
        if len(ids) != len(set(ids)):
            raise ValueError("Skill Recorder values must have unique IDs.")
        return self


class RecorderBuiltSkill(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1]
    sessionId: str
    architecture: str
    name: str
    description: str
    allowedTools: tuple[str, ...] = ()
    body: str
    values: tuple[RecorderValue, ...] = ()
    plan: RecorderSkillPlan
    createdAt: int = Field(ge=0)
    exportedPath: str | None = None
    exportedAt: int | None = Field(default=None, ge=0)

    @field_validator("sessionId")
    @classmethod
    def validate_session_id(cls, value: str) -> str:
        if not _SAFE_SESSION.fullmatch(value):
            raise ValueError("Skill Recorder session ID is unsafe.")
        return value

    @field_validator("architecture")
    @classmethod
    def validate_architecture(cls, value: str) -> str:
        if value not in _SUPPORTED_ARCHITECTURES:
            raise ValueError(f"Unsupported Skill Recorder architecture: {value}")
        return value

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not _SAFE_SKILL_NAME.fullmatch(value):
            raise ValueError("Skill Recorder name must be safe kebab-case.")
        return value

    @field_validator("description", "body")
    @classmethod
    def require_content(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Skill Recorder description and body must not be empty.")
        return normalized

    @field_validator("exportedPath")
    @classmethod
    def validate_exported_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if "\x00" in value:
            raise ValueError("Skill Recorder exportedPath contains a NUL byte.")
        windows_path = PureWindowsPath(value)
        posix_path = PurePosixPath(value)
        if windows_path.is_absolute():
            parts = windows_path.parts
        elif posix_path.is_absolute():
            parts = posix_path.parts
        else:
            raise ValueError(
                "Skill Recorder exportedPath must be an absolute Windows or POSIX path."
            )
        if ".." in parts:
            raise ValueError("Skill Recorder exportedPath must not traverse parent directories.")
        return value

    @model_validator(mode="after")
    def validate_consistency(self):
        if self.plan.architecture != self.architecture:
            raise ValueError("Skill Recorder plan architecture does not match built skill.")
        if self.plan.name != self.name:
            raise ValueError("Skill Recorder plan name does not match built skill.")
        if self.plan.description.strip() != self.description.strip():
            raise ValueError("Skill Recorder plan description does not match built skill.")
        if self.exportedAt is not None and self.exportedPath is None:
            raise ValueError("Skill Recorder exportedAt requires exportedPath.")
        if self.exportedAt is not None and self.exportedAt < self.createdAt:
            raise ValueError("Skill Recorder exportedAt cannot precede createdAt.")

        top_level = {item.id: item for item in self.values}
        plan_values = {item.id: item for item in self.plan.values}
        if len(top_level) != len(self.values) or len(plan_values) != len(self.plan.values):
            raise ValueError("Skill Recorder values must have unique IDs.")
        for value_id in set(top_level) & set(plan_values):
            if top_level[value_id] != plan_values[value_id]:
                raise ValueError(
                    "Skill Recorder top-level and plan values conflict for "
                    f"{value_id}."
                )
        return self


class SkillRecorderImportSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    skill_json_path: str
    checksum: str
    consent_to_process: bool
    source_uri: str = "local://skill-recorder/skill.json"
    version: str = "0.1.0"

    @field_validator("checksum")
    @classmethod
    def validate_checksum(cls, value: str) -> str:
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
            raise ValueError("Skill Recorder checksum must be sha256:<64 lowercase hex>.")
        return value

    @field_validator("skill_json_path", "source_uri", "version")
    @classmethod
    def require_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or "\x00" in normalized:
            raise ValueError("Skill Recorder import fields must be non-empty and NUL-free.")
        return normalized


class SkillRecorderAdapter:
    """Import a persisted Microsoft Skill Recorder `skill.json` as a candidate only."""

    def import_candidate(self, spec: SkillRecorderImportSpec) -> SkillAcquisitionCandidate:
        if not spec.consent_to_process:
            raise SkillRecorderImportError("Consent is required to process Skill Recorder output.")
        raw_path = Path(spec.skill_json_path).expanduser()
        if ".." in raw_path.parts:
            raise SkillRecorderImportError(
                "Skill Recorder input path must not traverse parent directories."
            )
        path = raw_path.resolve()
        if path.is_symlink() or raw_path.is_symlink() or not path.is_file():
            raise SkillRecorderImportError(
                "Skill Recorder input must be a regular non-symlink skill.json file."
            )
        if path.name != "skill.json":
            raise SkillRecorderImportError("Skill Recorder persisted input must be named skill.json.")
        data = path.read_bytes()
        if len(data) > 10 * 1024 * 1024:
            raise SkillRecorderImportError("Skill Recorder input exceeds the 10 MiB limit.")
        actual = "sha256:" + hashlib.sha256(data).hexdigest()
        if actual != spec.checksum:
            raise SkillRecorderImportError("Skill Recorder checksum mismatch.")
        try:
            built = RecorderBuiltSkill.model_validate_json(data)
        except ValueError as exc:
            raise SkillRecorderImportError("Skill Recorder skill.json is invalid.") from exc

        surfaces = [
            spec.source_uri,
            built.sessionId,
            built.name,
            built.description,
            built.body,
            built.plan.title,
            built.plan.summary,
            built.plan.generalization,
            built.exportedPath or "",
            *built.allowedTools,
            *built.plan.allowedTools,
            *(item.name for item in built.values),
            *(item.value for item in built.values),
            *(item.name for item in built.plan.values),
            *(item.value for item in built.plan.values),
            *(item.text for item in built.plan.steps),
            *(item.title for item in built.plan.steps),
            *(item.tool for item in built.plan.steps),
        ]
        if any(self._contains_secret(value) for value in surfaces):
            raise SkillRecorderImportError("Potential secret detected in Skill Recorder output.")

        value_map = self._merge_values(built.plan.values, built.values)
        self._require_resolved(built.body, value_map, surface="body")
        rendered_body = self._render_values(built.body, value_map)

        procedure: list[str] = []
        procedure_kinds: list[Literal["calculation", "action"]] = []
        for index, step in enumerate(built.plan.steps, start=1):
            self._require_resolved(step.text, value_map, surface=f"plan step {index}")
            self._require_resolved(step.title, value_map, surface=f"plan step {index} title")
            self._require_resolved(step.tool, value_map, surface=f"plan step {index} tool")
            rendered = self._render_values(step.text, value_map)
            title = self._render_values(step.title, value_map).strip()
            tool = self._render_values(step.tool, value_map).strip()
            label = f"{title}: " if title else ""
            tool_clause = f" [tool={tool}]" if tool else ""
            procedure.append(f"{index}. [{step.kind}] {label}{rendered}{tool_clause}")
            procedure_kinds.append(step.kind)

        rendered_surfaces = [rendered_body, *procedure]
        if any(self._contains_secret(value) for value in rendered_surfaces):
            raise SkillRecorderImportError(
                "Potential secret detected after rendering Skill Recorder values."
            )

        allowed_tools = tuple(
            dict.fromkeys(
                item.strip()
                for item in (*built.allowedTools, *built.plan.allowedTools)
                if item.strip()
            )
        )
        rendered_body_hash = hashlib.sha256(rendered_body.encode("utf-8")).hexdigest()
        source_ref = (
            f"microsoft-skill-recorder|{SKILL_RECORDER_RELEASE}|{SKILL_RECORDER_COMMIT}|"
            f"{built.sessionId}|{spec.checksum}|MIT|{spec.source_uri}"
        )
        skill = SkillSpec(
            skill_id=built.name.replace("-", "_")[:80],
            name=built.plan.title.strip() or built.name.replace("-", " ").title(),
            version=spec.version,
            description=built.description,
            rules=(
                "follow_approved_skill_recorder_plan_in_order",
                "preserve_calculation_and_action_boundaries",
                "verify_observable_outcome_before_completion",
                "stop_before_unapproved_or_unverifiable_side_effects",
            ),
            preconditions=(
                "Use only the approved Skill Recorder plan, fixed values, and declared tools.",
            ),
            allowed_tools=allowed_tools,
            procedure=tuple(procedure),
            procedure_kinds=tuple(procedure_kinds),
            success_criteria=(
                "All approved plan steps complete and the task-specific observable outcome is verified.",
            ),
            failure_handling=(
                "Stop and escalate when an action cannot be completed safely or verified.",
            ),
            provenance="microsoft-skill-recorder",
            source_refs=(source_ref, f"rendered-body-sha256:{rendered_body_hash}"),
            generated_by=f"microsoft-skill-recorder:{SKILL_RECORDER_RELEASE}+evoagent-import:1",
        )
        cases = (
            AcceptanceCase(
                case_id=f"skill-recorder:{built.sessionId}:success",
                kind="success",
                description="Execute the approved generalized plan and verify its observable outcome.",
                expected_conditions=skill.success_criteria,
            ),
            AcceptanceCase(
                case_id=f"skill-recorder:{built.sessionId}:side-effect-boundary",
                kind="failure",
                description="Encounter an unapproved or unverifiable action.",
                expected_conditions=skill.failure_handling,
            ),
        )
        return SkillAcquisitionCandidate(
            candidate_id=f"acq:skill-recorder:{built.sessionId}:{spec.version}",
            demonstration_id=f"skill-recorder:{built.sessionId}",
            skill=skill,
            acceptance_cases=cases,
            findings=(),
        )

    @staticmethod
    def _merge_values(
        plan_values: tuple[RecorderValue, ...],
        top_level_values: tuple[RecorderValue, ...],
    ) -> dict[str, str]:
        merged: dict[str, RecorderValue] = {}
        for item in (*plan_values, *top_level_values):
            existing = merged.get(item.id)
            if existing is not None and existing != item:
                raise SkillRecorderImportError(
                    "Skill Recorder top-level and plan values conflict for "
                    f"{item.id}."
                )
            merged[item.id] = item
        return {value_id: item.value for value_id, item in merged.items()}

    @staticmethod
    def _require_resolved(text: str, values: dict[str, str], *, surface: str) -> None:
        unresolved = sorted(
            {
                match.group(1).lower()
                for match in _TOKEN_RE.finditer(text)
                if match.group(1).lower() not in values
            }
        )
        if unresolved:
            raise SkillRecorderImportError(
                f"Unresolved Skill Recorder value tokens in {surface}: {', '.join(unresolved)}"
            )

    @staticmethod
    def _render_values(text: str, values: dict[str, str]) -> str:
        return _TOKEN_RE.sub(lambda match: values[match.group(1).lower()], text)

    @staticmethod
    def _contains_secret(value: str) -> bool:
        return any(pattern.search(value) for pattern in _SECRET_PATTERNS)


__all__ = [
    "SKILL_RECORDER_COMMIT",
    "SKILL_RECORDER_RELEASE",
    "SKILL_RECORDER_REPOSITORY",
    "RecorderBuiltSkill",
    "SkillRecorderAdapter",
    "SkillRecorderImportError",
    "SkillRecorderImportSpec",
]
