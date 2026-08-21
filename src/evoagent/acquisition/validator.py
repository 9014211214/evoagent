from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from evoagent.acquisition.models import (
    CompilationFinding,
    DemonstrationAction,
    DemonstrationArtifact,
    FindingCode,
    FindingSeverity,
    SourceTrustLevel,
)


_SECRET_KEY_PATTERN = re.compile(
    r"(?:password|passwd|api[_-]?key|access[_-]?token|auth[_-]?token|secret|private[_-]?key)",
    re.IGNORECASE,
)
_SECRET_VALUE_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


class DemonstrationValidator:
    def validate(self, demonstration: DemonstrationArtifact) -> tuple[CompilationFinding, ...]:
        findings: list[CompilationFinding] = []

        if not demonstration.sources:
            findings.append(
                self._finding(
                    FindingCode.MISSING_SOURCE,
                    FindingSeverity.ERROR,
                    "At least one licensed and consented source artifact is required.",
                )
            )

        for source in demonstration.sources:
            if not source.license_id.strip() or source.license_id.lower() in {"unknown", "none"}:
                findings.append(
                    self._finding(
                        FindingCode.MISSING_LICENSE,
                        FindingSeverity.ERROR,
                        "Source artifact does not declare a usable license.",
                        source_id=source.source_id,
                    )
                )
            if not source.consent_to_process:
                findings.append(
                    self._finding(
                        FindingCode.CONSENT_REQUIRED,
                        FindingSeverity.ERROR,
                        "Source artifact is not approved for processing.",
                        source_id=source.source_id,
                    )
                )
            if source.trust_level == SourceTrustLevel.UNTRUSTED:
                findings.append(
                    self._finding(
                        FindingCode.UNTRUSTED_SOURCE,
                        FindingSeverity.WARNING,
                        "Source is marked untrusted and requires additional review.",
                        source_id=source.source_id,
                    )
                )
            if self._contains_secret((source.uri, source.metadata)):
                findings.append(
                    self._finding(
                        FindingCode.SECRET_DETECTED,
                        FindingSeverity.ERROR,
                        "Potential secret detected in source metadata; value was not retained in the finding.",
                        source_id=source.source_id,
                    )
                )

        text_surfaces = (
            demonstration.task_intent,
            demonstration.preconditions,
            demonstration.allowed_tools,
            demonstration.success_criteria,
            demonstration.failure_handling,
            demonstration.observed_success_evidence,
            tuple(
                (
                    step.semantic_target,
                    step.tool_name,
                    step.expected_observation,
                    step.narration,
                )
                for step in demonstration.steps
            ),
        )
        if self._contains_secret(text_surfaces):
            findings.append(
                self._finding(
                    FindingCode.SECRET_DETECTED,
                    FindingSeverity.ERROR,
                    "Potential secret detected in demonstration text; value was not retained in the finding.",
                )
            )

        if not demonstration.steps:
            findings.append(
                self._finding(
                    FindingCode.MISSING_STEPS,
                    FindingSeverity.ERROR,
                    "A demonstration requires at least one observable step.",
                )
            )
        if not demonstration.success_criteria:
            findings.append(
                self._finding(
                    FindingCode.MISSING_SUCCESS_CRITERIA,
                    FindingSeverity.ERROR,
                    "A candidate Skill requires machine- or human-checkable success criteria.",
                )
            )
        if not demonstration.observed_success or not demonstration.observed_success_evidence:
            findings.append(
                self._finding(
                    FindingCode.SUCCESS_NOT_OBSERVED,
                    FindingSeverity.ERROR,
                    "The demonstration did not include observable evidence of successful completion.",
                )
            )

        for step in demonstration.steps:
            if self._contains_secret(step.parameters):
                findings.append(
                    self._finding(
                        FindingCode.SECRET_DETECTED,
                        FindingSeverity.ERROR,
                        "Potential secret detected in step parameters; value was not retained in the finding.",
                        step_index=step.index,
                    )
                )
            if step.action == DemonstrationAction.UI_ACTION:
                coordinate_keys = {"x", "y"}.intersection(step.parameters)
                if coordinate_keys and not step.semantic_target:
                    findings.append(
                        self._finding(
                            FindingCode.AMBIGUOUS_COORDINATE_ACTION,
                            FindingSeverity.ERROR,
                            "Coordinate-only UI action has no semantic target and cannot safely generalize.",
                            step_index=step.index,
                        )
                    )
                elif not step.semantic_target:
                    findings.append(
                        self._finding(
                            FindingCode.MISSING_SEMANTIC_TARGET,
                            FindingSeverity.ERROR,
                            "UI action requires a stable semantic target.",
                            step_index=step.index,
                        )
                    )
            if step.action == DemonstrationAction.TOOL_CALL and not step.tool_name:
                findings.append(
                    self._finding(
                        FindingCode.MISSING_SEMANTIC_TARGET,
                        FindingSeverity.ERROR,
                        "Tool-call step requires an explicit tool name.",
                        step_index=step.index,
                    )
                )

        return tuple(findings)

    @staticmethod
    def has_errors(findings: tuple[CompilationFinding, ...]) -> bool:
        return any(item.severity == FindingSeverity.ERROR for item in findings)

    @classmethod
    def _contains_secret(cls, value: Any, key: str | None = None) -> bool:
        if key and _SECRET_KEY_PATTERN.search(key):
            return True
        if isinstance(value, Mapping):
            return any(cls._contains_secret(item, str(item_key)) for item_key, item in value.items())
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return any(cls._contains_secret(item) for item in value)
        if isinstance(value, str):
            return any(pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS)
        return False

    @staticmethod
    def _finding(
        code: FindingCode,
        severity: FindingSeverity,
        message: str,
        *,
        source_id: str | None = None,
        step_index: int | None = None,
    ) -> CompilationFinding:
        return CompilationFinding(
            code=code,
            severity=severity,
            message=message,
            source_id=source_id,
            step_index=step_index,
        )
