"""Frozen, strictly validated four-component harness data models."""

from __future__ import annotations

from dataclasses import dataclass
import re
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from .canonical import sha256_json


SNAPSHOT_SCHEMA = "evoagent-seagym-harness-v1"
UPDATE_MODEL_ID = "xiaomi/mimo-v2.5"
CANONICAL_MODEL_ID = "xiaomi/mimo-v2.5-20260422"
HARBOR_MODEL_ID = "openrouter/xiaomi/mimo-v2.5"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SAFE_NAME = re.compile(r"^[a-z][a-z0-9_]{1,47}$")
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
SECRET_PATTERNS = (
    re.compile(r"\bsk-(?:or-v1-)?[A-Za-z0-9_-]{12,}\b", re.IGNORECASE),
    re.compile(r"\bgh[oprsu]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{12,}", re.IGNORECASE),
    re.compile(r"\b(?:api[_ -]?key|secret|password)\s*[:=]\s*\S+", re.IGNORECASE),
)
FORBIDDEN_OUTPUT_TERMS = (
    "task_id",
    "task id",
    "attempt_id",
    "attempt id",
    "canary",
    "raw trace",
    "raw prompt",
    "raw response",
    "reasoning_content",
    "openrouter_api_key",
    "-----begin",
    "http://",
    "https://",
    "{{",
    "}}",
    "{%",
    "%}",
    "ignore previous",
    "ignore the system",
    "bypass safety",
    "disable safety",
    "disable verification",
    "exfiltrate",
    "upload credentials",
    "send credentials",
    "reveal credentials",
    "reveal hidden",
)


def _exact_object(raw: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must be an object")
    actual = set(raw)
    if actual != keys:
        missing = sorted(keys - actual)
        extra = sorted(actual - keys)
        raise ValueError(f"{label} has invalid fields (missing={missing}, extra={extra})")
    return raw


def _safe_text(
    value: Any,
    *,
    label: str,
    max_chars: int,
    forbidden_fragments: Iterable[str] = (),
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    if value != value.strip() or not value or len(value) > max_chars:
        raise ValueError(f"{label} must be non-empty, trimmed, and at most {max_chars} characters")
    if CONTROL.search(value):
        raise ValueError(f"{label} contains control characters")
    lowered = value.casefold()
    if any(term in lowered for term in FORBIDDEN_OUTPUT_TERMS):
        raise ValueError(f"{label} contains a forbidden privacy term")
    for pattern in SECRET_PATTERNS:
        if pattern.search(value):
            raise ValueError(f"{label} resembles secret material")
    for fragment in forbidden_fragments:
        fragment = fragment.strip().casefold()
        if len(fragment) >= 3 and fragment in lowered:
            raise ValueError(f"{label} contains an evaluation identifier")
    return value


@dataclass(frozen=True)
class Skill:
    name: str
    guidance: str

    @classmethod
    def from_dict(cls, raw: Any, *, forbidden_fragments: Iterable[str] = ()) -> "Skill":
        data = _exact_object(raw, {"name", "guidance"}, "skill")
        name = _safe_text(data["name"], label="skill.name", max_chars=48, forbidden_fragments=forbidden_fragments)
        if not SAFE_NAME.fullmatch(name):
            raise ValueError("skill.name must use lower snake_case")
        return cls(
            name=name,
            guidance=_safe_text(
                data["guidance"],
                label="skill.guidance",
                max_chars=600,
                forbidden_fragments=forbidden_fragments,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "guidance": self.guidance}


@dataclass(frozen=True)
class Memory:
    topic: str
    lesson: str

    @classmethod
    def from_dict(cls, raw: Any, *, forbidden_fragments: Iterable[str] = ()) -> "Memory":
        data = _exact_object(raw, {"topic", "lesson"}, "memory")
        return cls(
            topic=_safe_text(data["topic"], label="memory.topic", max_chars=80, forbidden_fragments=forbidden_fragments),
            lesson=_safe_text(data["lesson"], label="memory.lesson", max_chars=500, forbidden_fragments=forbidden_fragments),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"topic": self.topic, "lesson": self.lesson}


@dataclass(frozen=True)
class Route:
    condition: str
    skill: str

    @classmethod
    def from_dict(cls, raw: Any, *, forbidden_fragments: Iterable[str] = ()) -> "Route":
        data = _exact_object(raw, {"condition", "skill"}, "route")
        skill = _safe_text(data["skill"], label="route.skill", max_chars=48, forbidden_fragments=forbidden_fragments)
        if not SAFE_NAME.fullmatch(skill):
            raise ValueError("route.skill must use lower snake_case")
        return cls(
            condition=_safe_text(data["condition"], label="route.condition", max_chars=240, forbidden_fragments=forbidden_fragments),
            skill=skill,
        )

    def to_dict(self) -> dict[str, Any]:
        return {"condition": self.condition, "skill": self.skill}


@dataclass(frozen=True)
class Policy:
    planning: str
    verification: str
    recovery: str
    max_iterations: int

    @classmethod
    def from_dict(cls, raw: Any, *, forbidden_fragments: Iterable[str] = ()) -> "Policy":
        data = _exact_object(raw, {"planning", "verification", "recovery", "max_iterations"}, "policy")
        iterations = data["max_iterations"]
        if isinstance(iterations, bool) or not isinstance(iterations, int) or not 1 <= iterations <= 32:
            raise ValueError("policy.max_iterations must be an integer in [1, 32]")
        return cls(
            planning=_safe_text(data["planning"], label="policy.planning", max_chars=500, forbidden_fragments=forbidden_fragments),
            verification=_safe_text(data["verification"], label="policy.verification", max_chars=500, forbidden_fragments=forbidden_fragments),
            recovery=_safe_text(data["recovery"], label="policy.recovery", max_chars=500, forbidden_fragments=forbidden_fragments),
            max_iterations=iterations,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "planning": self.planning,
            "verification": self.verification,
            "recovery": self.recovery,
            "max_iterations": self.max_iterations,
        }


@dataclass(frozen=True)
class HarnessComponents:
    skills: tuple[Skill, ...]
    memory: tuple[Memory, ...]
    router: tuple[Route, ...]
    policy: Policy

    @classmethod
    def from_dict(
        cls,
        raw: Any,
        *,
        forbidden_fragments: Iterable[str] = (),
    ) -> "HarnessComponents":
        data = _exact_object(raw, {"skills", "memory", "router", "policy"}, "components")
        if not isinstance(data["skills"], list) or not 1 <= len(data["skills"]) <= 8:
            raise ValueError("components.skills must contain 1 to 8 entries")
        if not isinstance(data["memory"], list) or not 1 <= len(data["memory"]) <= 8:
            raise ValueError("components.memory must contain 1 to 8 entries")
        if not isinstance(data["router"], list) or not 1 <= len(data["router"]) <= 12:
            raise ValueError("components.router must contain 1 to 12 entries")
        fragments = tuple(forbidden_fragments)
        skills = tuple(Skill.from_dict(item, forbidden_fragments=fragments) for item in data["skills"])
        if len({skill.name for skill in skills}) != len(skills):
            raise ValueError("skill names must be unique")
        memory = tuple(Memory.from_dict(item, forbidden_fragments=fragments) for item in data["memory"])
        router = tuple(Route.from_dict(item, forbidden_fragments=fragments) for item in data["router"])
        known_skills = {skill.name for skill in skills}
        if any(route.skill not in known_skills for route in router):
            raise ValueError("every route must reference a declared skill")
        return cls(
            skills=skills,
            memory=memory,
            router=router,
            policy=Policy.from_dict(data["policy"], forbidden_fragments=fragments),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "skills": [item.to_dict() for item in self.skills],
            "memory": [item.to_dict() for item in self.memory],
            "router": [item.to_dict() for item in self.router],
            "policy": self.policy.to_dict(),
        }

    def hashes(self) -> dict[str, str]:
        data = self.to_dict()
        return {name: sha256_json(data[name]) for name in ("skills", "memory", "router", "policy")}


@dataclass(frozen=True)
class HarnessSnapshot:
    schema_version: str
    generation: int
    parent_snapshot_sha256: str | None
    model_id: str
    evidence_sha256: str
    components: HarnessComponents
    component_sha256: Mapping[str, str]
    evaluation_only: bool
    causal_attribution_claimed: bool
    promotion_claimed: bool
    snapshot_sha256: str

    @classmethod
    def create(
        cls,
        *,
        generation: int,
        parent_snapshot_sha256: str | None,
        evidence_sha256: str,
        components: HarnessComponents,
    ) -> "HarnessSnapshot":
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
            raise ValueError("generation must be a non-negative integer")
        if generation == 0 and parent_snapshot_sha256 is not None:
            raise ValueError("A0 cannot have a parent")
        if generation > 0 and not _is_hash(parent_snapshot_sha256):
            raise ValueError("non-A0 snapshots require a parent hash")
        if not _is_hash(evidence_sha256):
            raise ValueError("evidence_sha256 must be a SHA-256 digest")
        unsigned = {
            "schema_version": SNAPSHOT_SCHEMA,
            "generation": generation,
            "parent_snapshot_sha256": parent_snapshot_sha256,
            "model_id": UPDATE_MODEL_ID,
            "evidence_sha256": evidence_sha256,
            "components": components.to_dict(),
            "component_sha256": components.hashes(),
            "evaluation_only": True,
            "causal_attribution_claimed": False,
            "promotion_claimed": False,
        }
        return cls(
            schema_version=SNAPSHOT_SCHEMA,
            generation=generation,
            parent_snapshot_sha256=parent_snapshot_sha256,
            model_id=UPDATE_MODEL_ID,
            evidence_sha256=evidence_sha256,
            components=components,
            component_sha256=MappingProxyType(components.hashes()),
            evaluation_only=True,
            causal_attribution_claimed=False,
            promotion_claimed=False,
            snapshot_sha256=sha256_json(unsigned),
        )

    @classmethod
    def from_dict(
        cls,
        raw: Any,
        *,
        forbidden_fragments: Iterable[str] = (),
    ) -> "HarnessSnapshot":
        keys = {
            "schema_version",
            "generation",
            "parent_snapshot_sha256",
            "model_id",
            "evidence_sha256",
            "components",
            "component_sha256",
            "evaluation_only",
            "causal_attribution_claimed",
            "promotion_claimed",
            "snapshot_sha256",
        }
        data = _exact_object(raw, keys, "snapshot")
        if data["schema_version"] != SNAPSHOT_SCHEMA or data["model_id"] != UPDATE_MODEL_ID:
            raise ValueError("snapshot schema or model lock does not match")
        if data["evaluation_only"] is not True:
            raise ValueError("snapshot must remain evaluation-only")
        if data["causal_attribution_claimed"] is not False or data["promotion_claimed"] is not False:
            raise ValueError("snapshot cannot claim causality or promotion")
        components = HarnessComponents.from_dict(data["components"], forbidden_fragments=forbidden_fragments)
        expected = cls.create(
            generation=data["generation"],
            parent_snapshot_sha256=data["parent_snapshot_sha256"],
            evidence_sha256=data["evidence_sha256"],
            components=components,
        )
        if data["component_sha256"] != dict(expected.component_sha256):
            raise ValueError("component hash mismatch")
        if data["snapshot_sha256"] != expected.snapshot_sha256:
            raise ValueError("snapshot hash mismatch")
        return expected

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generation": self.generation,
            "parent_snapshot_sha256": self.parent_snapshot_sha256,
            "model_id": self.model_id,
            "evidence_sha256": self.evidence_sha256,
            "components": self.components.to_dict(),
            "component_sha256": dict(self.component_sha256),
            "evaluation_only": self.evaluation_only,
            "causal_attribution_claimed": self.causal_attribution_claimed,
            "promotion_claimed": self.promotion_claimed,
            "snapshot_sha256": self.snapshot_sha256,
        }


def default_a0() -> HarnessSnapshot:
    components = HarnessComponents.from_dict(
        {
            "skills": [
                {"name": "inspect_environment", "guidance": "Inspect the available workspace and constraints before taking an action."},
                {"name": "make_minimal_change", "guidance": "Prefer the smallest reversible action that satisfies the verifier-visible requirement."},
                {"name": "verify_result", "guidance": "Use available deterministic checks after each material change and before completion."},
            ],
            "memory": [
                {"topic": "Task isolation", "lesson": "Treat each run as independent and rely only on information available in that run."},
                {"topic": "Failure handling", "lesson": "When a check fails, inspect observable evidence and revise the narrowest unsupported assumption."},
            ],
            "router": [
                {"condition": "Before the first material action", "skill": "inspect_environment"},
                {"condition": "When a bounded change can satisfy the requirement", "skill": "make_minimal_change"},
                {"condition": "After a material change or before completion", "skill": "verify_result"},
            ],
            "policy": {
                "planning": "Identify the required artifact, constraints, and a short sequence of reversible actions.",
                "verification": "Prefer deterministic checks that directly exercise the requested outcome.",
                "recovery": "On failure, preserve valid work, inspect structural evidence, and retry only with a bounded correction.",
                "max_iterations": 12,
            },
        }
    )
    return HarnessSnapshot.create(
        generation=0,
        parent_snapshot_sha256=None,
        evidence_sha256=sha256_json({"type": "a0", "source": "static-reviewed-default"}),
        components=components,
    )


def candidate_json_schema() -> dict[str, Any]:
    short_string = {"type": "string", "minLength": 1, "maxLength": 600}
    return {
        "name": "evoagent_harness_components",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["skills", "memory", "router", "policy"],
            "properties": {
                "skills": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 8,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["name", "guidance"],
                        "properties": {"name": {"type": "string", "pattern": "^[a-z][a-z0-9_]{1,47}$"}, "guidance": short_string},
                    },
                },
                "memory": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 8,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["topic", "lesson"],
                        "properties": {"topic": short_string, "lesson": short_string},
                    },
                },
                "router": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 12,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["condition", "skill"],
                        "properties": {"condition": short_string, "skill": {"type": "string", "pattern": "^[a-z][a-z0-9_]{1,47}$"}},
                    },
                },
                "policy": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["planning", "verification", "recovery", "max_iterations"],
                    "properties": {
                        "planning": short_string,
                        "verification": short_string,
                        "recovery": short_string,
                        "max_iterations": {"type": "integer", "minimum": 1, "maximum": 32},
                    },
                },
            },
        },
    }


def _is_hash(value: Any) -> bool:
    return isinstance(value, str) and bool(HEX64.fullmatch(value))
