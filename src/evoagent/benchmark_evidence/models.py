from __future__ import annotations

import math
from datetime import datetime
from enum import Enum
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evoagent.integrations.harbor import (
    HARBOR_REVIEWED_COMMIT,
    TERMINAL_BENCH_2_1,
)
from evoagent.model_registry.models import canonical_sha256, validate_safe_content


TERMINAL_BENCH_2_1_REVIEWED_COMMIT = (
    "ffccbe05ee73a9d59518217f294ad711bda39304"
)
_SHA1_PATTERN = r"^[0-9a-f]{40}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SAFE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"


class BenchmarkEvidenceError(ValueError):
    pass


def _require_timezone(value: datetime, *, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone.")
    return value


def _require_finite(value: float, *, label: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite.")
    return value


class BenchmarkRunRole(str, Enum):
    BASELINE = "baseline"
    EVOLVED = "evolved"
    COMPARATOR = "comparator"


class BenchmarkComparisonMode(str, Enum):
    LONGITUDINAL = "longitudinal"
    SAME_MODEL_CROSS_AGENT = "same_model_cross_agent"


class BenchmarkEvidenceSource(str, Enum):
    EXTERNAL_HARBOR = "external_harbor"
    SYNTHETIC_FIXTURE = "synthetic_fixture"


class BenchmarkEvidenceEventType(str, Enum):
    RUN_IMPORTED = "run_imported"
    LONGITUDINAL_COMPARISON_STORED = "longitudinal_comparison_stored"
    SAME_MODEL_COMPARISON_STORED = "same_model_comparison_stored"


class BenchmarkExecutionBudget(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_trials: int = Field(gt=0)
    max_wall_seconds: int = Field(gt=0)
    max_cost_usd: float = Field(ge=0.0)

    @field_validator("max_cost_usd")
    @classmethod
    def validate_cost(cls, value: float) -> float:
        return _require_finite(value, label="Benchmark execution budget cost")


class BenchmarkTaskIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_name: str = Field(pattern=_SAFE_ID_PATTERN)
    task_id: str
    task_checksum: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("task_id")
    @classmethod
    def validate_task_id(cls, value: str) -> str:
        if not value.strip() or "\x00" in value or len(value) > 4096:
            raise ValueError("Benchmark Task ID must be a bounded non-empty string.")
        return value


class BenchmarkSuiteIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)

    suite_id: str = Field(pattern=_SAFE_ID_PATTERN)
    dataset_ref: str = TERMINAL_BENCH_2_1
    harbor_reviewed_commit: str = Field(pattern=_SHA1_PATTERN)
    benchmark_reviewed_commit: str = Field(pattern=_SHA1_PATTERN)
    primary_reward_key: str = "reward"
    tasks: tuple[BenchmarkTaskIdentity, ...]
    canonical_task_manifest_attested: bool = False
    suite_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("tasks")
    @classmethod
    def validate_tasks(
        cls,
        value: tuple[BenchmarkTaskIdentity, ...],
    ) -> tuple[BenchmarkTaskIdentity, ...]:
        if not value:
            raise ValueError("Benchmark suite requires at least one Task.")
        names = [item.task_name for item in value]
        ids = [item.task_id for item in value]
        if len(set(names)) != len(names) or len(set(ids)) != len(ids):
            raise ValueError("Benchmark suite Task names and IDs must be unique.")
        return value

    @model_validator(mode="after")
    def validate_suite(self):
        if self.dataset_ref != TERMINAL_BENCH_2_1:
            raise ValueError("Benchmark suite must use pinned Terminal-Bench 2.1.")
        if self.harbor_reviewed_commit != HARBOR_REVIEWED_COMMIT:
            raise ValueError("Benchmark suite Harbor commit differs from the reviewed pin.")
        if self.benchmark_reviewed_commit != TERMINAL_BENCH_2_1_REVIEWED_COMMIT:
            raise ValueError(
                "Benchmark suite Terminal-Bench commit differs from the reviewed pin."
            )
        if not self.primary_reward_key.strip():
            raise ValueError("Benchmark suite primary reward key is required.")
        payload = self.model_dump(mode="json", exclude={"suite_hash"})
        validate_safe_content(payload)
        if self.suite_hash != canonical_sha256(payload):
            raise ValueError("Benchmark suite hash mismatch.")
        return self


class BenchmarkAgentIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)

    family_id: str = Field(pattern=_SAFE_ID_PATTERN)
    name: str = Field(pattern=_SAFE_ID_PATTERN)
    version: str
    source_commit: str = Field(pattern=_SHA1_PATTERN)
    config_sha256: str = Field(pattern=_SHA256_PATTERN)
    snapshot_id: str = Field(pattern=_SAFE_ID_PATTERN)
    evolution_round: int = Field(ge=0)
    parent_snapshot_id: str | None = Field(default=None, pattern=_SAFE_ID_PATTERN)
    identity_hash: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_identity(self):
        if not self.version.strip():
            raise ValueError("Benchmark Agent version is required.")
        if self.evolution_round == 0 and self.parent_snapshot_id is not None:
            raise ValueError("Round-zero Agent snapshot must not declare a parent.")
        if self.evolution_round > 0 and self.parent_snapshot_id is None:
            raise ValueError("Evolved Agent snapshot requires a parent snapshot ID.")
        payload = self.model_dump(mode="json", exclude={"identity_hash"})
        validate_safe_content(payload)
        if self.identity_hash != canonical_sha256(payload):
            raise ValueError("Benchmark Agent identity hash mismatch.")
        return self


class BenchmarkModelIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str = Field(pattern=_SAFE_ID_PATTERN)
    name: str = Field(pattern=_SAFE_ID_PATTERN)
    revision: str
    config_sha256: str = Field(pattern=_SHA256_PATTERN)
    inference_settings_sha256: str = Field(pattern=_SHA256_PATTERN)
    identity_hash: str = Field(pattern=_SHA256_PATTERN)
    external_bytes_verified_by_evoagent: Literal[False] = False

    @model_validator(mode="after")
    def validate_identity(self):
        if not self.revision.strip():
            raise ValueError("Benchmark Model revision is required.")
        payload = self.model_dump(mode="json", exclude={"identity_hash"})
        validate_safe_content(payload)
        if self.identity_hash != canonical_sha256(payload):
            raise ValueError("Benchmark Model identity hash mismatch.")
        return self


class BenchmarkRunContract(BaseModel):
    model_config = ConfigDict(frozen=True)

    contract_id: str = Field(pattern=_SAFE_ID_PATTERN)
    role: BenchmarkRunRole
    suite: BenchmarkSuiteIdentity
    agent: BenchmarkAgentIdentity
    model: BenchmarkModelIdentity
    reasoning_effort: str
    trials_per_task: int = Field(gt=0)
    timeout_multiplier: float = 1.0
    agent_timeout_override: bool = False
    verifier_timeout_override: bool = False
    resource_overrides: bool = False
    upload: bool = False
    public: bool = False
    harbor_hub_job_uri: str | None = None
    trajectories_available: bool = False
    default_execution_settings_attested: bool = False
    source: BenchmarkEvidenceSource
    execution_budget: BenchmarkExecutionBudget
    contract_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("timeout_multiplier")
    @classmethod
    def validate_timeout_multiplier(cls, value: float) -> float:
        value = _require_finite(value, label="Benchmark timeout multiplier")
        if value <= 0:
            raise ValueError("Benchmark timeout multiplier must be positive.")
        return value

    @field_validator("reasoning_effort")
    @classmethod
    def validate_reasoning_effort(cls, value: str) -> str:
        if not value.strip() or len(value) > 128:
            raise ValueError("Benchmark reasoning effort must be explicit and bounded.")
        return value

    @field_validator("harbor_hub_job_uri")
    @classmethod
    def validate_hub_uri(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlparse(value)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Harbor Hub job URI must be a credential-free HTTPS URI.")
        validate_safe_content(value, path="harbor_hub_job_uri")
        return value

    @model_validator(mode="after")
    def validate_contract(self):
        expected_trials = len(self.suite.tasks) * self.trials_per_task
        if self.execution_budget.max_trials != expected_trials:
            raise ValueError(
                "Benchmark execution budget trial count must equal the frozen suite contract."
            )
        if self.public and not self.upload:
            raise ValueError("Public benchmark evidence requires upload intent.")
        if self.upload and self.harbor_hub_job_uri is None:
            raise ValueError("Uploaded benchmark evidence requires a Harbor Hub job URI.")
        if self.harbor_hub_job_uri is not None and not self.upload:
            raise ValueError("Harbor Hub job URI requires upload intent.")
        if self.source == BenchmarkEvidenceSource.SYNTHETIC_FIXTURE and (
            self.upload
            or self.public
            or self.harbor_hub_job_uri is not None
            or self.trajectories_available
        ):
            raise ValueError(
                "Synthetic benchmark fixtures cannot claim upload, public visibility, "
                "Hub identity, or reviewable trajectories."
            )
        payload = self.model_dump(mode="json", exclude={"contract_hash"})
        validate_safe_content(payload)
        if self.contract_hash != canonical_sha256(payload):
            raise ValueError("Benchmark run contract hash mismatch.")
        return self


class SafeHarborTrialEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    trial_name: str = Field(pattern=_SAFE_ID_PATTERN)
    task_name: str = Field(pattern=_SAFE_ID_PATTERN)
    task_id: str
    task_checksum: str = Field(pattern=_SHA256_PATTERN)
    source: str
    agent_name: str = Field(pattern=_SAFE_ID_PATTERN)
    agent_version: str
    model_provider: str = Field(pattern=_SAFE_ID_PATTERN)
    model_name: str = Field(pattern=_SAFE_ID_PATTERN)
    rewards: dict[str, float] = Field(default_factory=dict)
    verifier_evidence_present: bool
    primary_reward: float = Field(ge=0.0, le=1.0)
    error_type: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    cache_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0.0)
    duration_seconds: float | None = Field(default=None, ge=0.0)
    evidence_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("task_id", "source", "agent_version")
    @classmethod
    def validate_bounded_strings(cls, value: str) -> str:
        if not value.strip() or "\x00" in value or len(value) > 4096:
            raise ValueError("Harbor trial string field must be bounded and non-empty.")
        return value

    @field_validator("error_type")
    @classmethod
    def validate_error_type(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip() or len(value) > 256 or "\x00" in value:
            raise ValueError("Harbor trial error type must be bounded.")
        return value

    @field_validator("rewards")
    @classmethod
    def validate_rewards(cls, value: dict[str, float]) -> dict[str, float]:
        for key, reward in value.items():
            if not key.strip() or len(key) > 256:
                raise ValueError("Harbor reward keys must be bounded and non-empty.")
            _require_finite(reward, label=f"Harbor reward {key}")
            if reward < 0.0 or reward > 1.0:
                raise ValueError("Terminal-Bench reward values must be between zero and one.")
        return value

    @field_validator("cost_usd", "duration_seconds")
    @classmethod
    def validate_optional_floats(cls, value: float | None) -> float | None:
        if value is not None:
            _require_finite(value, label="Harbor trial numeric evidence")
        return value

    @model_validator(mode="after")
    def validate_trial(self):
        if self.error_type is not None and self.primary_reward != 0.0:
            raise ValueError("Errored Harbor trials must count as zero reward.")
        if not self.verifier_evidence_present and self.primary_reward != 0.0:
            raise ValueError("Trials without verifier evidence must count as zero reward.")
        if self.verifier_evidence_present and not self.rewards:
            raise ValueError("Verifier evidence flag requires a non-empty reward map.")
        payload = self.model_dump(mode="json", exclude={"evidence_hash"})
        validate_safe_content(payload)
        if self.evidence_hash != canonical_sha256(payload):
            raise ValueError("Safe Harbor trial evidence hash mismatch.")
        return self


class BenchmarkTaskAggregate(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_name: str = Field(pattern=_SAFE_ID_PATTERN)
    task_id: str
    task_checksum: str = Field(pattern=_SHA256_PATTERN)
    trial_count: int = Field(gt=0)
    score: float = Field(ge=0.0, le=1.0)
    error_count: int = Field(ge=0)
    aggregate_hash: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_aggregate(self):
        if self.error_count > self.trial_count:
            raise ValueError("Benchmark Task error count exceeds trial count.")
        payload = self.model_dump(mode="json", exclude={"aggregate_hash"})
        if self.aggregate_hash != canonical_sha256(payload):
            raise ValueError("Benchmark Task aggregate hash mismatch.")
        return self


class BenchmarkRunEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    evidence_id: str = Field(pattern=_SAFE_ID_PATTERN)
    harbor_job_id: str
    source_file_name: Literal["result.json"] = "result.json"
    source_file_sha256: str = Field(pattern=_SHA256_PATTERN)
    contract: BenchmarkRunContract
    started_at: datetime
    finished_at: datetime
    n_total_trials: int = Field(gt=0)
    n_errored_trials: int = Field(ge=0)
    n_cancelled_trials: int = Field(ge=0)
    trials: tuple[SafeHarborTrialEvidence, ...]
    task_aggregates: tuple[BenchmarkTaskAggregate, ...]
    score: float = Field(ge=0.0, le=1.0)
    error_rate: float = Field(ge=0.0, le=1.0)
    token_usage_complete: bool
    cost_usage_complete: bool
    total_input_tokens: int | None = Field(default=None, ge=0)
    total_cache_tokens: int | None = Field(default=None, ge=0)
    total_output_tokens: int | None = Field(default=None, ge=0)
    total_cost_usd: float | None = Field(default=None, ge=0.0)
    evidence_hash: str = Field(pattern=_SHA256_PATTERN)
    harbor_execution_performed_by_evoagent: Literal[False] = False
    external_model_call_performed_by_evoagent: Literal[False] = False
    upload_performed_by_evoagent: Literal[False] = False
    official_submission_performed: Literal[False] = False
    official_submission_accepted: Literal[False] = False

    @field_validator("started_at")
    @classmethod
    def validate_started_at(cls, value: datetime) -> datetime:
        return _require_timezone(value, label="Benchmark start time")

    @field_validator("finished_at")
    @classmethod
    def validate_finished_at(cls, value: datetime) -> datetime:
        return _require_timezone(value, label="Benchmark finish time")

    @field_validator("harbor_job_id")
    @classmethod
    def validate_job_id(cls, value: str) -> str:
        if not value.strip() or len(value) > 256 or "\x00" in value:
            raise ValueError("Harbor job ID must be bounded and non-empty.")
        return value

    @field_validator("total_cost_usd")
    @classmethod
    def validate_total_cost(cls, value: float | None) -> float | None:
        if value is not None:
            _require_finite(value, label="Benchmark total cost")
        return value

    @model_validator(mode="after")
    def validate_run(self):
        if self.finished_at < self.started_at:
            raise ValueError("Benchmark finish time predates start time.")
        expected_total = len(self.contract.suite.tasks) * self.contract.trials_per_task
        if self.n_total_trials != expected_total or len(self.trials) != expected_total:
            raise ValueError("Benchmark evidence does not cover the frozen trial contract.")
        trial_names = [item.trial_name for item in self.trials]
        if len(set(trial_names)) != len(trial_names):
            raise ValueError("Benchmark evidence contains duplicate trial names.")
        expected_tasks = {
            item.task_name: item for item in self.contract.suite.tasks
        }
        by_task: dict[str, list[SafeHarborTrialEvidence]] = {
            name: [] for name in expected_tasks
        }
        for trial in self.trials:
            expected = expected_tasks.get(trial.task_name)
            if expected is None:
                raise ValueError("Benchmark evidence contains an unexpected Task.")
            if (
                trial.task_id != expected.task_id
                or trial.task_checksum != expected.task_checksum
            ):
                raise ValueError("Benchmark Task identity or checksum drift detected.")
            if (
                trial.agent_name != self.contract.agent.name
                or trial.agent_version != self.contract.agent.version
            ):
                raise ValueError("Harbor trial Agent identity differs from its contract.")
            if (
                trial.model_provider != self.contract.model.provider
                or trial.model_name != self.contract.model.name
            ):
                raise ValueError("Harbor trial Model identity differs from its contract.")
            by_task[trial.task_name].append(trial)
        if any(
            len(items) != self.contract.trials_per_task
            for items in by_task.values()
        ):
            raise ValueError("Benchmark per-Task trial coverage differs from its contract.")
        errored = sum(item.error_type is not None for item in self.trials)
        cancelled = sum(item.error_type == "CancelledError" for item in self.trials)
        if self.n_errored_trials != errored or self.n_cancelled_trials != cancelled:
            raise ValueError("Benchmark declared error counts differ from trial evidence.")
        expected_score = sum(item.primary_reward for item in self.trials) / len(
            self.trials
        )
        expected_error_rate = errored / len(self.trials)
        if abs(self.score - expected_score) > 1e-12:
            raise ValueError("Benchmark run score differs from trial evidence.")
        if abs(self.error_rate - expected_error_rate) > 1e-12:
            raise ValueError("Benchmark error rate differs from trial evidence.")
        expected_aggregates = tuple(
            _aggregate_task(expected_tasks[name], by_task[name])
            for name in sorted(expected_tasks)
        )
        if self.task_aggregates != expected_aggregates:
            raise ValueError("Benchmark Task aggregates differ from trial evidence.")
        token_fields = (
            "input_tokens",
            "cache_tokens",
            "output_tokens",
        )
        tokens_complete = all(
            getattr(trial, field) is not None
            for trial in self.trials
            for field in token_fields
        )
        if self.token_usage_complete != tokens_complete:
            raise ValueError("Benchmark token-completeness flag differs from trial evidence.")
        expected_token_totals = (
            sum(item.input_tokens or 0 for item in self.trials),
            sum(item.cache_tokens or 0 for item in self.trials),
            sum(item.output_tokens or 0 for item in self.trials),
        )
        actual_token_totals = (
            self.total_input_tokens,
            self.total_cache_tokens,
            self.total_output_tokens,
        )
        if tokens_complete:
            if actual_token_totals != expected_token_totals:
                raise ValueError("Benchmark token totals differ from trial evidence.")
        elif any(value is not None for value in actual_token_totals):
            raise ValueError("Incomplete token evidence must not expose aggregate totals.")
        cost_complete = all(item.cost_usd is not None for item in self.trials)
        if self.cost_usage_complete != cost_complete:
            raise ValueError("Benchmark cost-completeness flag differs from trial evidence.")
        if cost_complete:
            expected_cost = sum(item.cost_usd or 0.0 for item in self.trials)
            if self.total_cost_usd is None or abs(self.total_cost_usd - expected_cost) > 1e-12:
                raise ValueError("Benchmark cost total differs from trial evidence.")
        elif self.total_cost_usd is not None:
            raise ValueError("Incomplete cost evidence must not expose an aggregate total.")
        payload = self.model_dump(mode="json", exclude={"evidence_hash"})
        validate_safe_content(payload)
        if self.evidence_hash != canonical_sha256(payload):
            raise ValueError("Benchmark run evidence hash mismatch.")
        return self


class BenchmarkSubmissionEligibility(BaseModel):
    model_config = ConfigDict(frozen=True)

    evidence_id: str = Field(pattern=_SAFE_ID_PATTERN)
    exact_pinned_suite: bool
    canonical_task_manifest_attested: bool
    default_execution_settings: bool
    complete_task_coverage: bool
    minimum_trials_per_task_met: bool
    public_uploaded_job: bool
    trajectories_available: bool
    synthetic_fixture: bool
    submission_prerequisites_met: bool
    reasons: tuple[str, ...]
    assessment_hash: str = Field(pattern=_SHA256_PATTERN)
    official_submission_performed: Literal[False] = False
    official_submission_accepted: Literal[False] = False

    @model_validator(mode="after")
    def validate_assessment(self):
        if len(set(self.reasons)) != len(self.reasons):
            raise ValueError("Benchmark eligibility reasons must be unique.")
        expected = all(
            (
                self.exact_pinned_suite,
                self.canonical_task_manifest_attested,
                self.default_execution_settings,
                self.complete_task_coverage,
                self.minimum_trials_per_task_met,
                self.public_uploaded_job,
                self.trajectories_available,
                not self.synthetic_fixture,
            )
        )
        if self.submission_prerequisites_met != expected:
            raise ValueError("Benchmark submission prerequisite flag is not derived.")
        payload = self.model_dump(mode="json", exclude={"assessment_hash"})
        if self.assessment_hash != canonical_sha256(payload):
            raise ValueError("Benchmark eligibility assessment hash mismatch.")
        return self


class BenchmarkSnapshotPoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str = Field(pattern=_SAFE_ID_PATTERN)
    snapshot_id: str = Field(pattern=_SAFE_ID_PATTERN)
    evolution_round: int = Field(ge=0)
    score: float = Field(ge=0.0, le=1.0)
    gain_from_baseline: float
    error_rate: float = Field(ge=0.0, le=1.0)
    total_input_tokens: int | None = Field(default=None, ge=0)
    total_output_tokens: int | None = Field(default=None, ge=0)
    total_cost_usd: float | None = Field(default=None, ge=0.0)


class BenchmarkRoundTaskDelta(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str = Field(pattern=_SAFE_ID_PATTERN)
    evolution_round: int = Field(ge=1)
    task_name: str = Field(pattern=_SAFE_ID_PATTERN)
    baseline_score: float = Field(ge=0.0, le=1.0)
    round_score: float = Field(ge=0.0, le=1.0)
    delta: float

    @model_validator(mode="after")
    def validate_delta(self):
        if abs(self.delta - (self.round_score - self.baseline_score)) > 1e-12:
            raise ValueError("Benchmark round Task delta is not derived.")
        return self


class LongitudinalComparisonReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    comparison_id: str = Field(pattern=_SAFE_ID_PATTERN)
    mode: Literal[BenchmarkComparisonMode.LONGITUDINAL] = (
        BenchmarkComparisonMode.LONGITUDINAL
    )
    run_ids: tuple[str, ...]
    agent_family_id: str = Field(pattern=_SAFE_ID_PATTERN)
    model_identity_hash: str = Field(pattern=_SHA256_PATTERN)
    suite_hash: str = Field(pattern=_SHA256_PATTERN)
    frozen_contract_hash: str = Field(pattern=_SHA256_PATTERN)
    points: tuple[BenchmarkSnapshotPoint, ...]
    task_deltas: tuple[BenchmarkRoundTaskDelta, ...]
    best_round: int = Field(ge=0)
    final_round: int = Field(ge=1)
    baseline_score: float = Field(ge=0.0, le=1.0)
    final_score: float = Field(ge=0.0, le=1.0)
    final_gain: float
    improved_tasks: int = Field(ge=0)
    regressed_tasks: int = Field(ge=0)
    tied_tasks: int = Field(ge=0)
    monotonic_score: bool
    downward_round_count: int = Field(ge=0)
    error_rate_delta: float
    input_token_delta: int | None = None
    output_token_delta: int | None = None
    cost_delta_usd: float | None = None
    report_hash: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_report(self):
        if len(self.run_ids) < 2 or len(self.run_ids) != len(self.points):
            raise ValueError("Longitudinal comparison requires aligned A0…AN runs.")
        if self.points[0].evolution_round != 0:
            raise ValueError("Longitudinal comparison must begin at round zero.")
        if self.final_round != self.points[-1].evolution_round:
            raise ValueError("Longitudinal final round differs from its final point.")
        if abs(self.baseline_score - self.points[0].score) > 1e-12:
            raise ValueError("Longitudinal baseline score differs from A0.")
        if abs(self.final_score - self.points[-1].score) > 1e-12:
            raise ValueError("Longitudinal final score differs from AN.")
        if abs(self.final_gain - (self.final_score - self.baseline_score)) > 1e-12:
            raise ValueError("Longitudinal final gain is not derived.")
        payload = self.model_dump(mode="json", exclude={"report_hash"})
        validate_safe_content(payload)
        if self.report_hash != canonical_sha256(payload):
            raise ValueError("Longitudinal comparison report hash mismatch.")
        return self


class CrossAgentScore(BaseModel):
    model_config = ConfigDict(frozen=True)

    rank: int = Field(gt=0)
    run_id: str = Field(pattern=_SAFE_ID_PATTERN)
    agent_name: str = Field(pattern=_SAFE_ID_PATTERN)
    agent_version: str
    snapshot_id: str = Field(pattern=_SAFE_ID_PATTERN)
    score: float = Field(ge=0.0, le=1.0)
    error_rate: float = Field(ge=0.0, le=1.0)
    total_input_tokens: int | None = Field(default=None, ge=0)
    total_output_tokens: int | None = Field(default=None, ge=0)
    total_cost_usd: float | None = Field(default=None, ge=0.0)


class PairwiseTaskComparison(BaseModel):
    model_config = ConfigDict(frozen=True)

    comparator_run_id: str = Field(pattern=_SAFE_ID_PATTERN)
    wins: int = Field(ge=0)
    losses: int = Field(ge=0)
    ties: int = Field(ge=0)
    score_delta: float
    error_rate_delta: float
    input_token_delta: int | None = None
    output_token_delta: int | None = None
    cost_delta_usd: float | None = None


class SameModelCrossAgentReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    comparison_id: str = Field(pattern=_SAFE_ID_PATTERN)
    mode: Literal[BenchmarkComparisonMode.SAME_MODEL_CROSS_AGENT] = (
        BenchmarkComparisonMode.SAME_MODEL_CROSS_AGENT
    )
    anchor_run_id: str = Field(pattern=_SAFE_ID_PATTERN)
    run_ids: tuple[str, ...]
    model_identity_hash: str = Field(pattern=_SHA256_PATTERN)
    suite_hash: str = Field(pattern=_SHA256_PATTERN)
    frozen_contract_hash: str = Field(pattern=_SHA256_PATTERN)
    same_model_verified: Literal[True] = True
    ranking: tuple[CrossAgentScore, ...]
    pairwise: tuple[PairwiseTaskComparison, ...]
    report_hash: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_report(self):
        if len(self.run_ids) < 2 or len(self.ranking) != len(self.run_ids):
            raise ValueError("Same-model comparison requires at least two aligned runs.")
        if self.anchor_run_id not in self.run_ids:
            raise ValueError("Same-model comparison anchor is not part of the run set.")
        ranks = [item.rank for item in self.ranking]
        if ranks != list(range(1, len(ranks) + 1)):
            raise ValueError("Same-model comparison ranking must be contiguous.")
        if len(self.pairwise) != len(self.run_ids) - 1:
            raise ValueError("Same-model comparison requires one pairwise result per comparator.")
        payload = self.model_dump(mode="json", exclude={"report_hash"})
        validate_safe_content(payload)
        if self.report_hash != canonical_sha256(payload):
            raise ValueError("Same-model comparison report hash mismatch.")
        return self


BenchmarkComparisonReport = (
    LongitudinalComparisonReport | SameModelCrossAgentReport
)


class BenchmarkEvidenceAuditEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    sequence: int = Field(gt=0)
    event_id: str = Field(pattern=_SAFE_ID_PATTERN)
    event_type: BenchmarkEvidenceEventType
    subject_id: str = Field(pattern=_SAFE_ID_PATTERN)
    payload: dict[str, Any]
    actor_id: str = Field(pattern=_SAFE_ID_PATTERN)
    created_at: datetime
    previous_hash: str = Field(pattern=_SHA256_PATTERN)
    event_hash: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        return _require_timezone(value, label="Benchmark audit event time")


class BenchmarkEvidenceCheckpoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_count: int = Field(ge=0)
    head_hash: str = Field(pattern=_SHA256_PATTERN)


def _aggregate_task(
    identity: BenchmarkTaskIdentity,
    trials: list[SafeHarborTrialEvidence],
) -> BenchmarkTaskAggregate:
    payload = {
        "task_name": identity.task_name,
        "task_id": identity.task_id,
        "task_checksum": identity.task_checksum,
        "trial_count": len(trials),
        "score": sum(item.primary_reward for item in trials) / len(trials),
        "error_count": sum(item.error_type is not None for item in trials),
    }
    return BenchmarkTaskAggregate(
        **payload,
        aggregate_hash=canonical_sha256(payload),
    )


__all__ = [
    "BenchmarkAgentIdentity",
    "BenchmarkComparisonMode",
    "BenchmarkComparisonReport",
    "BenchmarkEvidenceAuditEvent",
    "BenchmarkEvidenceCheckpoint",
    "BenchmarkEvidenceError",
    "BenchmarkEvidenceEventType",
    "BenchmarkEvidenceSource",
    "BenchmarkExecutionBudget",
    "BenchmarkModelIdentity",
    "BenchmarkRoundTaskDelta",
    "BenchmarkRunContract",
    "BenchmarkRunEvidence",
    "BenchmarkRunRole",
    "BenchmarkSnapshotPoint",
    "BenchmarkSubmissionEligibility",
    "BenchmarkSuiteIdentity",
    "BenchmarkTaskAggregate",
    "BenchmarkTaskIdentity",
    "CrossAgentScore",
    "HARBOR_REVIEWED_COMMIT",
    "LongitudinalComparisonReport",
    "PairwiseTaskComparison",
    "SafeHarborTrialEvidence",
    "SameModelCrossAgentReport",
    "TERMINAL_BENCH_2_1",
    "TERMINAL_BENCH_2_1_REVIEWED_COMMIT",
]
