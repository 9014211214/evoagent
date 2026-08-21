from __future__ import annotations

import re

from evoagent.acquisition.models import (
    AcceptanceCase,
    CompilationFinding,
    DemonstrationAction,
    DemonstrationArtifact,
    SkillAcquisitionCandidate,
)
from evoagent.acquisition.validator import DemonstrationValidator
from evoagent.skills.models import ProcedureKind, SkillSpec


class AcquisitionValidationError(ValueError):
    def __init__(self, findings: tuple[CompilationFinding, ...]):
        self.findings = findings
        codes = ", ".join(item.code.value for item in findings if item.severity.value == "error")
        super().__init__(f"Demonstration failed acquisition validation: {codes}")


class DemonstrationSkillCompiler:
    def __init__(self, validator: DemonstrationValidator | None = None):
        self.validator = validator or DemonstrationValidator()

    def compile(
        self,
        demonstration: DemonstrationArtifact,
        *,
        skill_id: str | None = None,
        version: str = "0.1.0",
    ) -> SkillAcquisitionCandidate:
        findings = self.validator.validate(demonstration)
        if self.validator.has_errors(findings):
            raise AcquisitionValidationError(findings)

        resolved_skill_id = skill_id or self._slug(demonstration.task_intent)
        procedure = tuple(self._compile_step(step) for step in demonstration.steps)
        procedure_kinds = tuple(self._procedure_kind(step.action) for step in demonstration.steps)
        tools = list(demonstration.allowed_tools)
        for step in demonstration.steps:
            if step.tool_name and step.tool_name not in tools:
                tools.append(step.tool_name)

        source_refs = tuple(
            f"{source.source_id}|{source.license_id}|{source.checksum}"
            for source in demonstration.sources
        )
        skill = SkillSpec(
            skill_id=resolved_skill_id,
            name=self._title(demonstration.task_intent),
            version=version,
            description=demonstration.task_intent.strip(),
            rules=(
                "follow_procedure_in_order",
                "verify_success_criteria_before_completion",
            ),
            preconditions=demonstration.preconditions,
            allowed_tools=tuple(tools),
            procedure=procedure,
            procedure_kinds=procedure_kinds,
            success_criteria=demonstration.success_criteria,
            failure_handling=demonstration.failure_handling,
            provenance="demonstration",
            source_refs=source_refs,
            generated_by="demonstration-compiler:v0.6",
        )
        cases: list[AcceptanceCase] = [
            AcceptanceCase(
                case_id=f"{demonstration.demonstration_id}:success",
                kind="success",
                description="Reproduce the demonstrated task through semantic actions.",
                expected_conditions=demonstration.success_criteria,
            )
        ]
        for index, failure_rule in enumerate(demonstration.failure_handling, start=1):
            cases.append(
                AcceptanceCase(
                    case_id=f"{demonstration.demonstration_id}:failure:{index}",
                    kind="failure",
                    description=f"Exercise failure handling rule {index}.",
                    expected_conditions=(failure_rule,),
                )
            )

        return SkillAcquisitionCandidate(
            candidate_id=f"acq:{demonstration.demonstration_id}:{version}",
            demonstration_id=demonstration.demonstration_id,
            skill=skill,
            acceptance_cases=tuple(cases),
            findings=findings,
        )

    @staticmethod
    def _compile_step(step) -> str:
        if step.action == DemonstrationAction.TOOL_CALL:
            target = f"tool:{step.tool_name}"
        else:
            target = step.semantic_target or step.action.value
        expectation = f" -> expect: {step.expected_observation}" if step.expected_observation else ""
        return f"{step.index}. {step.action.value} {target}{expectation}"

    @staticmethod
    def _procedure_kind(action: DemonstrationAction) -> ProcedureKind:
        if action in {DemonstrationAction.TOOL_CALL, DemonstrationAction.UI_ACTION}:
            return "action"
        if action == DemonstrationAction.OBSERVE:
            return "observe"
        return "confirm"

    @staticmethod
    def _slug(value: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
        if not slug:
            raise ValueError("Task intent cannot be converted into a Skill ID.")
        return slug[:80]

    @staticmethod
    def _title(value: str) -> str:
        return " ".join(part.capitalize() for part in value.strip().split())
