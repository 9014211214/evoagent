from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_serializer,
    model_validator,
)


ProcedureKind = Literal["calculation", "action", "observe", "confirm"]


class SkillVersionStatus(str, Enum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class SkillEventType(str, Enum):
    REGISTERED = "registered"
    CANDIDATE_CREATED = "candidate_created"
    PROMOTED = "promoted"
    REJECTED = "rejected"
    ROLLED_BACK = "rolled_back"


class SkillSpec(BaseModel):
    """Immutable semantic content of one Skill version."""

    model_config = ConfigDict(frozen=True)

    skill_id: str
    name: str
    version: str
    description: str
    rules: tuple[str, ...] = ()
    preconditions: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    procedure: tuple[str, ...] = ()
    procedure_kinds: tuple[ProcedureKind, ...] = ()
    success_criteria: tuple[str, ...] = ()
    failure_handling: tuple[str, ...] = ()
    provenance: str = "independent"
    source_refs: tuple[str, ...] = ()
    generated_by: str = "human"

    @model_validator(mode="after")
    def validate_procedure_kinds(self):
        if self.procedure_kinds and len(self.procedure_kinds) != len(self.procedure):
            raise ValueError(
                "Skill procedure_kinds must be empty for legacy content or align one-to-one "
                "with procedure steps."
            )
        return self

    @model_serializer(mode="wrap")
    def serialize_with_legacy_hash_compatibility(self, handler):
        payload = handler(self)
        if not self.procedure_kinds:
            payload.pop("procedure_kinds", None)
        return payload


class SkillPatch(BaseModel):
    model_config = ConfigDict(frozen=True)

    add_rules: tuple[str, ...] = ()
    remove_rules: tuple[str, ...] = ()
    description: str | None = None
    preconditions: tuple[str, ...] | None = None
    allowed_tools: tuple[str, ...] | None = None
    procedure: tuple[str, ...] | None = None
    procedure_kinds: tuple[ProcedureKind, ...] | None = None
    success_criteria: tuple[str, ...] | None = None
    failure_handling: tuple[str, ...] | None = None
    evidence_trace_ids: tuple[str, ...] = ()
    generated_by: str = "native_skill_evolver"

    @model_validator(mode="after")
    def validate_procedure_patch(self):
        if self.procedure_kinds is not None:
            if self.procedure is None:
                raise ValueError(
                    "A Skill patch cannot replace procedure_kinds without replacing procedure."
                )
            if len(self.procedure_kinds) != len(self.procedure):
                raise ValueError(
                    "Skill patch procedure_kinds must align one-to-one with procedure steps."
                )
        return self


class SkillDiff(BaseModel):
    model_config = ConfigDict(frozen=True)

    base_version: str
    candidate_version: str
    added_rules: tuple[str, ...] = ()
    removed_rules: tuple[str, ...] = ()
    description_changed: bool = False
    changed_sections: tuple[str, ...] = ()


class SkillEvaluationDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    skill_id: str
    base_version: str
    candidate_version: str
    promote: bool
    base_score: float = Field(ge=0.0, le=1.0)
    candidate_score: float = Field(ge=0.0, le=1.0)
    regression_count: int = Field(ge=0)
    reason: str


class SkillVersionRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    spec: SkillSpec
    parent_version: str | None
    status: SkillVersionStatus
    content_hash: str
    evaluation: SkillEvaluationDecision | None = None


class SkillLifecycleEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    sequence: int
    event_type: SkillEventType
    skill_id: str
    version: str
    from_version: str | None = None
    to_version: str | None = None
    reason: str
    metadata: dict[str, Any] = Field(default_factory=dict)
