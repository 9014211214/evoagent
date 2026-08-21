from __future__ import annotations

from .models import SkillSpec


CONTROLLED_DOCUMENT_SKILL_ID = "local_document_writer"
CONTROLLED_DOCUMENT_SKILL_BASE_VERSION = "1.0.0"


def build_controlled_document_skill_v1() -> SkillSpec:
    """Build the exact S0 Skill used by the governed local Tool lifecycle."""

    return SkillSpec(
        skill_id=CONTROLLED_DOCUMENT_SKILL_ID,
        name="Local Document Writer",
        version=CONTROLLED_DOCUMENT_SKILL_BASE_VERSION,
        description="Write a local document and verify its observable result.",
        rules=("verify_after_write",),
        allowed_tools=(
            "read_document",
            "write_document",
            "list_documents",
        ),
        procedure=(
            "Write the requested document.",
            "Read the document after writing and verify its content.",
        ),
        procedure_kinds=("action", "confirm"),
        success_criteria=(
            "The expected final document state is independently verified.",
        ),
        failure_handling=(
            "Stop when the local Tool reports a protected document.",
        ),
        provenance="synthetic-local-tool-lab",
        source_refs=("synthetic://evoagent/local-tool-skill-v1",),
        generated_by="automatic-local-tool-bootstrap:v1.2",
    )


__all__ = [
    "CONTROLLED_DOCUMENT_SKILL_BASE_VERSION",
    "CONTROLLED_DOCUMENT_SKILL_ID",
    "build_controlled_document_skill_v1",
]
