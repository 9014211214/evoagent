import hashlib

from evoagent.acquisition import (
    DemonstrationAction,
    DemonstrationArtifact,
    DemonstrationSkillCompiler,
    DemonstrationStep,
    InitialSkillAcquisitionGate,
    ResourceType,
    SourceArtifact,
    SourceTrustLevel,
    SyntheticAcquisitionSandbox,
)
from evoagent.skills import SkillRegistry

source = SourceArtifact(
    source_id="source:synthetic:note-demo",
    resource_type=ResourceType.DEMONSTRATION_TRACE,
    uri="synthetic://note-demo",
    checksum="sha256:" + hashlib.sha256(b"synthetic note demo").hexdigest(),
    license_id="CC0-1.0",
    consent_to_process=True,
    trust_level=SourceTrustLevel.SYNTHETIC,
)
demonstration = DemonstrationArtifact(
    demonstration_id="demo:note:1",
    task_intent="create a verified note",
    sources=(source,),
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
    failure_handling=("on duplicate title, return existing note identifier",),
    observed_success=True,
    observed_success_evidence=("synthetic verifier passed",),
)

candidate = DemonstrationSkillCompiler().compile(demonstration)
registry = SkillRegistry()
promotion = InitialSkillAcquisitionGate().evaluate_and_register(
    candidate,
    sandbox=SyntheticAcquisitionSandbox(),
    registry=registry,
)
print("candidate:", candidate.candidate_id)
print("registered:", promotion.registered)
print("active skill:", registry.active(candidate.skill.skill_id).spec.version)
print("procedure:", candidate.skill.procedure)
