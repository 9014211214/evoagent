from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ResourceBudget(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_task_trials: int = Field(gt=0)
    max_tokens: int = Field(default=0, ge=0)
    max_tool_calls: int = Field(default=0, ge=0)
    max_wall_seconds: float = Field(default=0.0, ge=0.0)
    max_cost_usd: float = Field(default=0.0, ge=0.0)


class ResourceUsage(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_trials: int = Field(default=0, ge=0)
    tokens: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    wall_seconds: float = Field(default=0.0, ge=0.0)
    cost_usd: float = Field(default=0.0, ge=0.0)

    def fits(self, budget: ResourceBudget) -> bool:
        comparisons = [self.task_trials <= budget.max_task_trials]
        if budget.max_tokens:
            comparisons.append(self.tokens <= budget.max_tokens)
        if budget.max_tool_calls:
            comparisons.append(self.tool_calls <= budget.max_tool_calls)
        if budget.max_wall_seconds:
            comparisons.append(self.wall_seconds <= budget.max_wall_seconds)
        if budget.max_cost_usd:
            comparisons.append(self.cost_usd <= budget.max_cost_usd)
        return all(comparisons)


class BenchmarkManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    dataset_ref: str
    revision: str
    split: str
    task_ids: tuple[str, ...]
    trials_per_task: int = Field(default=1, gt=0)
    updates_allowed_during_evaluation: bool = False

    @field_validator("task_ids")
    @classmethod
    def validate_task_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("A frozen benchmark manifest requires task IDs.")
        if len(set(value)) != len(value):
            raise ValueError("Benchmark task IDs must be unique.")
        return value

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class EvolutionProtocolSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    protocol_id: str
    initial_model_id: str
    manifest: BenchmarkManifest
    evolution_budget: ResourceBudget
    evaluation_budget: ResourceBudget


class EvaluationBatch(BaseModel):
    model_config = ConfigDict(frozen=True)

    per_task: dict[str, float]
    usage: ResourceUsage

    @field_validator("per_task")
    @classmethod
    def validate_scores(cls, value: dict[str, float]) -> dict[str, float]:
        if any(score < 0.0 or score > 1.0 for score in value.values()):
            raise ValueError("Per-task benchmark scores must be in [0, 1].")
        return value


class SnapshotEvaluation(BaseModel):
    model_config = ConfigDict(frozen=True)

    snapshot_id: str
    round_index: int = Field(ge=0)
    model_id: str
    manifest_fingerprint: str
    score: float = Field(ge=0.0, le=1.0)
    per_task: dict[str, float]
    usage: ResourceUsage


class EvolutionRun(BaseModel):
    model_config = ConfigDict(frozen=True)

    system_name: str
    protocol: EvolutionProtocolSpec
    evaluations: tuple[SnapshotEvaluation, ...]


class RunSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    system_name: str
    initial_score: float
    final_score: float
    evolution_gain: float
    best_score: float
    best_round: int
    final_round: int


class SameStartComparison(BaseModel):
    model_config = ConfigDict(frozen=True)

    protocol_id: str
    initial_model_id: str
    rankings: tuple[RunSummary, ...]
