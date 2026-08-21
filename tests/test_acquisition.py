import hashlib

import pytest

from evoagent.acquisition import (
    AcquisitionValidationError,
    DemonstrationAction,
    DemonstrationArtifact,
    DemonstrationSkillCompiler,
    DemonstrationStep,
    FindingCode,
    InitialSkillAcquisitionGate,
    ResourceType,
    SourceArtifact,
    SourceTrustLevel,
    SyntheticAcquisitionSandbox,
)
from evoagent.skills import SkillRegistry


def checksum(value: str = "public synthetic demonstration") -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def source(**overrides) -> SourceArtifact:
    values = dict(
        source_id="source:synthetic:1",
        resource_type=ResourceType.DEMONSTRATION_TRACE,
        uri="synthetic://demo/1",
        checksum=checksum(),
        license_id="CC0-1.0",
        consent_to_process=True,
        trust_level=SourceTrustLevel.SYNTHETIC,
        metadata={"generator": "unit-test"},
    )
    values.update(overrides)
    return SourceArtifact(**values)


def demonstration(**overrides) -> DemonstrationArtifact:
    values = dict(
        demonstration_id="demo:create-note:1",
        task_intent="create a verified note",
        sources=(source(),),
        steps=(
            DemonstrationStep(
                index=1,
                action=DemonstrationAction.TOOL_CALL,
                tool_name="create_note",
                parameters={"title": "example", "body": "public content"},
                expected_observation="note identifier returned",
            ),
            DemonstrationStep(
                index=2,
                action=DemonstrationAction.CONFIRM,
                semantic_target="note status",
                expected_observation="status is created",
            ),
        ),
        preconditions=("workspace is writable",),
        allowed_tools=("create_note",),
        success_criteria=("note status equals created",),
        failure_handling=("on duplicate title, return the existing note identifier",),
        observed_success=True,
        observed_success_evidence=("synthetic verifier passed",),
    )
    values.update(overrides)
    return DemonstrationArtifact(**values)


def test_demonstration_compiles_to_candidate_with_provenance_and_cases():
    candidate = DemonstrationSkillCompiler().compile(demonstration())

    assert candidate.status == "candidate"
    assert candidate.skill.provenance == "demonstration"
    assert candidate.skill.allowed_tools == ("create_note",)
    assert candidate.skill.preconditions == ("workspace is writable",)
    assert candidate.skill.success_criteria == ("note status equals created",)
    assert candidate.skill.source_refs[0].startswith("source:synthetic:1|CC0-1.0|sha256:")
    assert len(candidate.acceptance_cases) == 2
    assert "parameters" not in " ".join(candidate.skill.procedure).lower()


def test_unconsented_unlicensed_or_secret_demo_is_rejected():
    bad_source = source(
        license_id="unknown",
        consent_to_process=False,
        metadata={"api_key": "sk-not-stored-in-finding-123456789"},
    )
    with pytest.raises(AcquisitionValidationError) as exc:
        DemonstrationSkillCompiler().compile(demonstration(sources=(bad_source,)))

    codes = {finding.code for finding in exc.value.findings}
    assert FindingCode.MISSING_LICENSE in codes
    assert FindingCode.CONSENT_REQUIRED in codes
    assert FindingCode.SECRET_DETECTED in codes
    assert "sk-not-stored" not in str(exc.value)


def test_coordinate_only_ui_action_is_rejected_as_ambiguous():
    demo = demonstration(
        steps=(
            DemonstrationStep(
                index=1,
                action=DemonstrationAction.UI_ACTION,
                parameters={"x": 81, "y": 240},
                expected_observation="dialog opens",
            ),
        )
    )
    with pytest.raises(AcquisitionValidationError) as exc:
        DemonstrationSkillCompiler().compile(demo)

    assert FindingCode.AMBIGUOUS_COORDINATE_ACTION in {
        finding.code for finding in exc.value.findings
    }


def test_initial_registration_requires_exact_passing_sandbox_cases():
    candidate = DemonstrationSkillCompiler().compile(demonstration())
    registry = SkillRegistry()
    gate = InitialSkillAcquisitionGate()

    failing = SyntheticAcquisitionSandbox(
        outcomes={case.case_id: False for case in candidate.acceptance_cases}
    )
    with pytest.raises(ValueError):
        gate.evaluate_and_register(candidate, sandbox=failing, registry=registry)

    result = gate.evaluate_and_register(
        candidate,
        sandbox=SyntheticAcquisitionSandbox(),
        registry=registry,
    )
    assert result.registered is True
    assert registry.active(candidate.skill.skill_id).spec == candidate.skill


def test_untrusted_warning_requires_explicit_approval():
    candidate = DemonstrationSkillCompiler().compile(
        demonstration(sources=(source(trust_level=SourceTrustLevel.UNTRUSTED),))
    )
    registry = SkillRegistry()
    gate = InitialSkillAcquisitionGate()
    with pytest.raises(ValueError):
        gate.evaluate_and_register(
            candidate, sandbox=SyntheticAcquisitionSandbox(), registry=registry
        )

    result = gate.evaluate_and_register(
        candidate,
        sandbox=SyntheticAcquisitionSandbox(),
        registry=registry,
        allow_warnings=True,
    )
    assert result.registered is True
