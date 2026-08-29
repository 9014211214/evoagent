from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from decimal import Decimal
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evoagent.benchmarks import ResourceUsage
from evoagent.continual import (
    ContinualEvaluationReport,
    ContinualTaskResult,
    ContinualTaskRole,
    UnifiedContinualEvaluator,
)
from evoagent.model_registry.models import canonical_sha256

from .minimal_scientific_seed import (
    EXPECTED_LOCAL_SCORES,
    SNAPSHOT_IDS,
    MinimalScientificSeedLock,
    MinimalScientificSeedPlan,
    MinimalScientificSeedResult,
    build_external_snapshot_chain,
    verify_minimal_scientific_seed_lock,
)
from .openrouter import OpenRouterModelPreset, OpenRouterPolicyUsage


_HASH = r"^[0-9a-f]{64}$"
_COMMIT = r"^[0-9a-f]{40}$"
_SAFE_ID = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"
_MAX_FILE_BYTES = 4 * 1024 * 1024
_MAX_JSON_DEPTH = 32
_MAX_JSON_NODES = 50_000
_RESULT_FILE_NAME = "minimal-scientific-seed-result.json"
_EXPECTED_EPISODE_CONTRACT_HASH = (
    "f79a3c874d5babe43372e4153254d55e5168c0e83b15aa8aa5cbe4f9ea4278fa"
)
_EXPECTED_EXTERNAL_TOOL_CALLS = 114
_Digest = Annotated[str, Field(pattern=_HASH)]
_Score = Annotated[float, Field(ge=0.0, le=1.0)]
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
_SECRET_JSON_ASSIGNMENT = re.compile(
    r'(?i)"(?:password|passwd|api[_-]?key|access[_-]?token|auth[_-]?token|secret|private[_-]?key)"\s*:\s*"(?!\*{3}"|\[?redacted\]?"|<redacted>")[^"\r\n]{4,}"'
)
_PROHIBITED_RAW_KEYS = {
    "chain_of_thought",
    "environment_values",
    "hidden_reasoning",
    "logs",
    "messages",
    "prompt",
    "prompts",
    "raw_prompt",
    "raw_prompts",
    "raw_response",
    "raw_responses",
    "raw_trajectory",
    "raw_trajectories",
    "reasoning",
    "reasoning_content",
    "reasoning_details",
    "response",
    "responses",
    "scratchpad",
    "scratchpads",
    "stack_trace",
    "stacktrace",
    "traceback",
    "tracebacks",
    "trajectory",
    "trajectories",
}


class MinimalScientificSeedResultImportError(ValueError):
    pass


class MinimalScientificSeedImportReceipt(BaseModel):
    """Bounded, deterministic receipt for one fully verified result file."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    format_version: Literal["evoagent-minimal-scientific-import-receipt-v1"]
    claim_scope: Literal[
        "controlled_external_mechanism_validation_not_authoritative_benchmark"
    ]
    source_file_name: Literal["minimal-scientific-seed-result.json"]
    source_file_sha256: str = Field(pattern=_HASH)
    source_commit: str = Field(pattern=_COMMIT)
    plan_hash: str = Field(pattern=_HASH)
    lock_hash: str = Field(pattern=_HASH)
    manifest_hash: str = Field(pattern=_HASH)
    model_preset_hash: str = Field(pattern=_HASH)
    result_evidence_hash: str = Field(pattern=_HASH)
    episode_contract_hash: Literal[
        "f79a3c874d5babe43372e4153254d55e5168c0e83b15aa8aa5cbe4f9ea4278fa"
    ]
    result_status: Literal["passed", "failed"]
    model_preset_id: str
    model_id: str
    canonical_model_id: str
    provider: str
    provider_fallbacks: Literal[False] = False
    authorization_anchor_hash: str = Field(pattern=_HASH)
    requester_id: str = Field(pattern=_SAFE_ID)
    approver_ids: tuple[str, str]
    snapshot_ids: tuple[str, ...]
    report_hashes: tuple[_Digest, ...]
    overall_scores: tuple[_Score, ...]
    report_count: Literal[5]
    task_result_count: Literal[60]
    total_tool_call_count: Literal[114]
    total_reported_wall_seconds: float = Field(ge=0.0)
    usage: OpenRouterPolicyUsage
    overall_score_delta: float
    final_retention_drop_from_first_passing_round: float = Field(ge=0.0)
    total_regression_count: int = Field(ge=0)
    final_safety_violation_count: int = Field(ge=0)
    external_execution_performed: Literal[True]
    external_benchmark: Literal[False]
    official_submission_performed: Literal[False]
    official_leaderboard_claimed: Literal[False]
    receipt_hash: str = Field(pattern=_HASH)

    @model_validator(mode="after")
    def validate_receipt(self):
        if self.snapshot_ids != SNAPSHOT_IDS:
            raise ValueError("Scientific import receipt snapshot sequence changed.")
        if len(self.report_hashes) != self.report_count:
            raise ValueError("Scientific import receipt report count differs.")
        if len(set(self.report_hashes)) != self.report_count:
            raise ValueError("Scientific import receipt report hashes are not unique.")
        if len(self.overall_scores) != self.report_count:
            raise ValueError("Scientific import receipt score count differs.")
        if self.task_result_count != self.report_count * 12:
            raise ValueError("Scientific import receipt Task count differs.")
        if self.total_tool_call_count != self.usage.requests:
            raise ValueError("Scientific import receipt request accounting differs.")
        if (
            self.usage.prompt_tokens <= 0
            or self.usage.completion_tokens <= 0
            or self.usage.total_tokens <= 0
            or self.usage.cost_usd <= 0
        ):
            raise ValueError("Scientific import receipt lacks external-model usage.")
        if len(set(self.approver_ids)) != 2 or self.requester_id in self.approver_ids:
            raise ValueError("Scientific import receipt approval identities differ.")
        if not math.isfinite(self.total_reported_wall_seconds):
            raise ValueError("Scientific import receipt wall time is not finite.")
        derived_delta = self.overall_scores[-1] - self.overall_scores[0]
        if abs(self.overall_score_delta - derived_delta) > 1e-12:
            raise ValueError("Scientific import receipt delta is not derived.")
        passed = (
            self.overall_scores == EXPECTED_LOCAL_SCORES
            and self.final_retention_drop_from_first_passing_round == 0.0
            and self.total_regression_count == 0
            and self.final_safety_violation_count == 0
        )
        if (self.result_status == "passed") != passed:
            raise ValueError("Scientific import receipt status is not derived.")
        if (
            self.usage.total_tokens
            != self.usage.prompt_tokens + self.usage.completion_tokens
            or self.usage.requests > 180
            or self.usage.prompt_tokens > self.usage.requests * 4096
            or self.usage.completion_tokens > self.usage.requests * 128
            or self.usage.cost_usd > 0.6
            or self.total_reported_wall_seconds > 90 * 60
        ):
            raise ValueError("Scientific import receipt usage exceeds frozen caps.")
        if abs(
            self.overall_score_delta
            - (self.overall_scores[-1] - self.overall_scores[0])
        ) > 1e-12:
            raise ValueError("Scientific import receipt score delta is not derived.")
        passed = (
            self.overall_scores == EXPECTED_LOCAL_SCORES
            and self.final_retention_drop_from_first_passing_round == 0.0
            and self.total_regression_count == 0
            and self.final_safety_violation_count == 0
        )
        if (self.result_status == "passed") != passed:
            raise ValueError("Scientific import receipt status is not derived.")
        payload = self.model_dump(mode="json", exclude={"receipt_hash"})
        if self.receipt_hash != canonical_sha256(payload):
            raise ValueError("Minimal scientific import receipt hash mismatch.")
        return self


class MinimalScientificSeedResultImporter:
    """Verify one caller-hashed scientific result without network access.

    The raw file is treated as untrusted. Only the compact receipt leaves this
    boundary; prompts, responses, trajectories, and arbitrary provider fields
    have no place in either the strict result schema or the receipt.
    """

    def __init__(
        self,
        controlled_root: str | Path,
        *,
        max_bytes: int = _MAX_FILE_BYTES,
    ):
        raw_root = Path(controlled_root).expanduser()
        if raw_root.is_symlink():
            raise MinimalScientificSeedResultImportError(
                "Scientific result import root must not be a symlink."
            )
        if max_bytes <= 0:
            raise ValueError("Scientific result import size limit must be positive.")
        self.root = raw_root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes

    def import_file(
        self,
        relative_path: str | Path,
        *,
        expected_sha256: str,
        expected_source_commit: str,
        expected_authorization_anchor_hash: str,
        expected_requester_id: str,
        expected_approver_ids: tuple[str, str],
        plan: MinimalScientificSeedPlan,
        lock: MinimalScientificSeedLock,
        preset: OpenRouterModelPreset,
    ) -> MinimalScientificSeedImportReceipt:
        if re.fullmatch(_HASH, expected_sha256) is None:
            raise MinimalScientificSeedResultImportError(
                "Scientific result requires a lowercase caller SHA-256."
            )
        if re.fullmatch(_COMMIT, expected_source_commit) is None:
            raise MinimalScientificSeedResultImportError(
                "Scientific result requires an exact lowercase source commit."
            )
        if re.fullmatch(_HASH, expected_authorization_anchor_hash) is None:
            raise MinimalScientificSeedResultImportError(
                "Scientific result requires an exact authorization-anchor hash."
            )
        if (
            re.fullmatch(_SAFE_ID, expected_requester_id) is None
            or len(expected_approver_ids) != 2
            or any(re.fullmatch(_SAFE_ID, item) is None for item in expected_approver_ids)
            or len(set(expected_approver_ids)) != 2
            or expected_requester_id in expected_approver_ids
        ):
            raise MinimalScientificSeedResultImportError(
                "Scientific result requires exact independent governance identities."
            )
        self._verify_frozen_inputs(plan=plan, lock=lock, preset=preset)
        path = self._resolve_file(relative_path)
        size = path.stat().st_size
        if size <= 0 or size > self.max_bytes:
            raise MinimalScientificSeedResultImportError(
                "Scientific result file is empty or exceeds the size limit."
            )
        raw = path.read_bytes()
        actual_sha256 = hashlib.sha256(raw).hexdigest()
        if actual_sha256 != expected_sha256:
            raise MinimalScientificSeedResultImportError(
                "Scientific result file SHA-256 mismatch."
            )
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MinimalScientificSeedResultImportError(
                "Scientific result file must be UTF-8 JSON."
            ) from exc
        self._reject_secrets(text)
        try:
            payload = json.loads(
                text,
                parse_constant=_reject_non_finite,
                object_pairs_hook=_reject_duplicate_object_keys,
            )
        except (json.JSONDecodeError, ValueError) as exc:
            raise MinimalScientificSeedResultImportError(
                "Scientific result file is not valid finite JSON."
            ) from exc
        self._inspect_json(payload)
        if not isinstance(payload, dict):
            raise MinimalScientificSeedResultImportError(
                "Scientific result root must be a JSON object."
            )
        self._validate_untrusted_shape(payload)
        try:
            result = MinimalScientificSeedResult.model_validate_json(
                text,
                strict=True,
            )
        except ValueError as exc:
            raise MinimalScientificSeedResultImportError(
                "Scientific result fails its strict schema or evidence hash."
            ) from exc
        self._verify_result(
            result,
            expected_source_commit=expected_source_commit,
            expected_authorization_anchor_hash=expected_authorization_anchor_hash,
            expected_requester_id=expected_requester_id,
            expected_approver_ids=expected_approver_ids,
            plan=plan,
            lock=lock,
            preset=preset,
            expected_episode_contract=_expected_episode_contract(
                plan.model_dump_json(),
                preset.model_dump_json(),
            ),
        )
        return self._build_receipt(
            result,
            source_file_sha256=actual_sha256,
            plan=plan,
            lock=lock,
            preset=preset,
        )

    def _resolve_file(self, relative_path: str | Path) -> Path:
        raw_path = Path(relative_path)
        pure = PurePosixPath(str(relative_path).replace("\\", "/"))
        if raw_path.is_absolute() or pure.is_absolute() or not pure.parts:
            raise MinimalScientificSeedResultImportError(
                "Scientific result path must be relative to its controlled root."
            )
        if pure.name != _RESULT_FILE_NAME:
            raise MinimalScientificSeedResultImportError(
                "Scientific importer accepts only minimal-scientific-seed-result.json."
            )
        if any(part in {"", ".", ".."} or "\x00" in part for part in pure.parts):
            raise MinimalScientificSeedResultImportError(
                "Scientific result path contains an unsafe segment."
            )
        current = self.root
        for part in pure.parts:
            current = current / part
            if current.exists() and current.is_symlink():
                raise MinimalScientificSeedResultImportError(
                    "Scientific result path must not contain symlinks."
                )
        resolved = self.root.joinpath(*pure.parts).resolve()
        try:
            common = Path(os.path.commonpath((self.root, resolved)))
        except ValueError as exc:
            raise MinimalScientificSeedResultImportError(
                "Scientific result path escaped its controlled root."
            ) from exc
        if common != self.root:
            raise MinimalScientificSeedResultImportError(
                "Scientific result path escaped its controlled root."
            )
        if not resolved.is_file() or resolved.is_symlink():
            raise MinimalScientificSeedResultImportError(
                "Scientific result must be a regular non-symlink file."
            )
        return resolved

    @staticmethod
    def _reject_secrets(text: str) -> None:
        if _SECRET_JSON_ASSIGNMENT.search(text) or any(
            pattern.search(text) for pattern in _SECRET_PATTERNS
        ):
            raise MinimalScientificSeedResultImportError(
                "Scientific result contains a potential credential or private key."
            )

    @staticmethod
    def _inspect_json(payload: Any) -> None:
        nodes = 0

        def visit(value: Any, depth: int) -> None:
            nonlocal nodes
            nodes += 1
            if nodes > _MAX_JSON_NODES:
                raise MinimalScientificSeedResultImportError(
                    "Scientific result JSON exceeds the node limit."
                )
            if depth > _MAX_JSON_DEPTH:
                raise MinimalScientificSeedResultImportError(
                    "Scientific result JSON exceeds the depth limit."
                )
            if isinstance(value, dict):
                for key, item in value.items():
                    if not isinstance(key, str) or len(key) > 512:
                        raise MinimalScientificSeedResultImportError(
                            "Scientific result contains an invalid JSON key."
                        )
                    visit(item, depth + 1)
            elif isinstance(value, list):
                if len(value) > 1_000:
                    raise MinimalScientificSeedResultImportError(
                        "Scientific result contains an oversized JSON array."
                    )
                for item in value:
                    visit(item, depth + 1)
            elif isinstance(value, str) and len(value) > _MAX_FILE_BYTES:
                raise MinimalScientificSeedResultImportError(
                    "Scientific result contains an oversized JSON string."
                )

        visit(payload, 0)

    @staticmethod
    def _validate_untrusted_shape(payload: dict[str, Any]) -> None:
        def reject_raw_fields(value: Any) -> None:
            if isinstance(value, dict):
                for key, item in value.items():
                    normalized = key.lower().replace("-", "_")
                    if normalized in _PROHIBITED_RAW_KEYS:
                        raise MinimalScientificSeedResultImportError(
                            "Scientific result contains a prohibited raw evidence field."
                        )
                    reject_raw_fields(item)
            elif isinstance(value, list):
                for item in value:
                    reject_raw_fields(item)

        def require_exact_keys(
            value: Any,
            expected: set[str],
            *,
            label: str,
        ) -> dict[str, Any]:
            if not isinstance(value, dict) or set(value) != expected:
                raise MinimalScientificSeedResultImportError(
                    f"Scientific {label} schema contains missing or extra fields."
                )
            return value

        reject_raw_fields(payload)
        require_exact_keys(
            payload,
            set(MinimalScientificSeedResult.model_fields),
            label="result",
        )
        reports = payload.get("reports")
        if not isinstance(reports, list):
            raise MinimalScientificSeedResultImportError(
                "Scientific reports must be a JSON array."
            )
        for report in reports:
            report_object = require_exact_keys(
                report,
                set(ContinualEvaluationReport.model_fields),
                label="report",
            )
            usage = require_exact_keys(
                report_object.get("usage"),
                set(ResourceUsage.model_fields),
                label="report usage",
            )
            if isinstance(usage.get("tool_calls"), bool) or not isinstance(
                usage.get("tool_calls"), int
            ):
                raise MinimalScientificSeedResultImportError(
                    "Scientific report tool_calls must be an integer."
                )
            results = report_object.get("results")
            if not isinstance(results, list):
                raise MinimalScientificSeedResultImportError(
                    "Scientific Task results must be a JSON array."
                )
            for result in results:
                task_result = require_exact_keys(
                    result,
                    set(ContinualTaskResult.model_fields),
                    label="Task result",
                )
                if isinstance(task_result.get("tool_calls"), bool) or not isinstance(
                    task_result.get("tool_calls"), int
                ):
                    raise MinimalScientificSeedResultImportError(
                        "Scientific Task result tool_calls must be an integer."
                    )
        usage = require_exact_keys(
            payload.get("usage"),
            set(OpenRouterPolicyUsage.model_fields),
            label="aggregate usage",
        )
        if isinstance(usage.get("requests"), bool) or not isinstance(
            usage.get("requests"), int
        ):
            raise MinimalScientificSeedResultImportError(
                "Scientific aggregate request count must be an integer."
            )

    @staticmethod
    def _verify_frozen_inputs(
        *,
        plan: MinimalScientificSeedPlan,
        lock: MinimalScientificSeedLock,
        preset: OpenRouterModelPreset,
    ) -> None:
        try:
            verify_minimal_scientific_seed_lock(plan, lock)
        except RuntimeError as exc:
            raise MinimalScientificSeedResultImportError(
                "Scientific plan differs from its exact frozen lock."
            ) from exc
        preset_hash = canonical_sha256(preset.fingerprint_payload())
        if (
            plan.model_preset_hash != preset_hash
            or lock.model_preset_hash != preset_hash
            or plan.manifest.model_id != preset.model_id
        ):
            raise MinimalScientificSeedResultImportError(
                "Scientific plan, lock, and model preset are not exactly bound."
            )

    @staticmethod
    def _verify_result(
        result: MinimalScientificSeedResult,
        *,
        expected_source_commit: str,
        expected_authorization_anchor_hash: str,
        expected_requester_id: str,
        expected_approver_ids: tuple[str, str],
        plan: MinimalScientificSeedPlan,
        lock: MinimalScientificSeedLock,
        preset: OpenRouterModelPreset,
        expected_episode_contract: tuple[
            tuple[tuple[str, bool, float, int, int, int], ...], ...
        ],
    ) -> None:
        preset_hash = canonical_sha256(preset.fingerprint_payload())
        if (
            result.source_commit != expected_source_commit
            or result.authorization_anchor_hash
            != expected_authorization_anchor_hash
            or result.requester_id != expected_requester_id
            or result.approver_ids != expected_approver_ids
            or result.plan_hash != plan.plan_hash
            or result.plan_hash != lock.plan_hash
            or result.manifest_hash != plan.manifest.manifest_hash
            or result.manifest_hash != lock.manifest_hash
            or plan.model_preset_hash != preset_hash
        ):
            raise MinimalScientificSeedResultImportError(
                "Scientific result differs from the exact source, plan, or lock."
            )
        if (
            result.model_id != preset.model_id
            or result.canonical_model_id != preset.canonical_model_id
            or result.provider != preset.provider_name
            or result.reasoning_enabled != preset.reasoning_enabled
            or result.provider_fallbacks is not False
        ):
            raise MinimalScientificSeedResultImportError(
                "Scientific result differs from the exact no-fallback model preset."
            )
        if (
            canonical_sha256(expected_episode_contract)
            != _EXPECTED_EPISODE_CONTRACT_HASH
        ):
            raise MinimalScientificSeedResultImportError(
                "Scientific episode contract hash differs from the frozen protocol."
            )
        expected_tasks = tuple(
            (item.task.task_id, item.task_hash, item.role)
            for item in plan.manifest.tasks
        )
        expected_snapshots = tuple(
            (item.snapshot_id, item.snapshot_hash, item.round_index)
            for item in plan.snapshots
        )
        total_task_results = 0
        total_tokens = 0
        total_tool_calls = 0
        total_wall_seconds = 0.0
        total_cost_usd = 0.0
        previous_report = None
        for report_index, (report, expected_snapshot) in enumerate(
            zip(result.reports, expected_snapshots)
        ):
            if (
                (report.snapshot_id, report.snapshot_hash, report.round_index)
                != expected_snapshot
                or report.report_id
                != f"minimal-science-external-{expected_snapshot[0]}"
                or report.model_id != preset.model_id
                or report.manifest_hash != plan.manifest.manifest_hash
            ):
                raise MinimalScientificSeedResultImportError(
                    "Scientific report differs from its exact frozen snapshot."
                )
            expected_parent_hash = (
                previous_report.report_hash if previous_report is not None else None
            )
            if report.parent_report_hash != expected_parent_hash:
                raise MinimalScientificSeedResultImportError(
                    "Scientific report parent chain is incomplete."
                )
            actual_tasks = tuple(
                (item.task_id, item.task_hash, item.role) for item in report.results
            )
            if len(report.results) != 12 or actual_tasks != expected_tasks:
                raise MinimalScientificSeedResultImportError(
                    "Scientific report does not contain the exact 12 frozen Tasks."
                )
            actual_episode_contract = tuple(
                (
                    item.task_id,
                    item.passed,
                    item.score,
                    item.safety_violation_count,
                    item.tool_calls,
                    item.episode_steps,
                )
                for item in report.results
            )
            if actual_episode_contract != expected_episode_contract[report_index]:
                raise MinimalScientificSeedResultImportError(
                    "Scientific report differs from the frozen episode contract."
                )
            if any(item.tool_calls > 3 for item in report.results):
                raise MinimalScientificSeedResultImportError(
                    "Scientific Task result exceeds the per-episode request cap."
                )
            derived_tool_calls = sum(item.tool_calls for item in report.results)
            if report.usage.tool_calls != derived_tool_calls:
                raise MinimalScientificSeedResultImportError(
                    "Scientific report Tool-call accounting differs from its Tasks."
                )
            if not report.usage.fits(plan.manifest.evaluation_budget):
                raise MinimalScientificSeedResultImportError(
                    "Scientific report exceeds the frozen evaluation budget."
                )
            if previous_report is None:
                derived_regressions = 0
                derived_forgetting = 0.0
            else:
                previous_by_task = {
                    item.task_id: item for item in previous_report.results
                }
                derived_regressions = sum(
                    previous_by_task[item.task_id].passed and not item.passed
                    for item in report.results
                )
                retention = tuple(
                    item
                    for item in report.results
                    if item.role == ContinualTaskRole.RETENTION
                )
                derived_forgetting = sum(
                    previous_by_task[item.task_id].passed and not item.passed
                    for item in retention
                ) / len(retention)
            if (
                report.regression_count != derived_regressions
                or abs(report.forgetting_rate - derived_forgetting) > 1e-12
            ):
                raise MinimalScientificSeedResultImportError(
                    "Scientific report regression or forgetting evidence is not derived."
                )
            total_task_results += len(report.results)
            total_tokens += report.usage.tokens
            total_tool_calls += report.usage.tool_calls
            total_wall_seconds += report.usage.wall_seconds
            total_cost_usd += report.usage.cost_usd
            previous_report = report

        if total_task_results != plan.budget.evaluation_episodes:
            raise MinimalScientificSeedResultImportError(
                "Scientific result is not the exact 60-episode matrix."
            )
        usage = result.usage
        if usage.requests != total_tool_calls:
            raise MinimalScientificSeedResultImportError(
                "Scientific request accounting differs from observable Tool calls."
            )
        if usage.total_tokens != usage.prompt_tokens + usage.completion_tokens:
            raise MinimalScientificSeedResultImportError(
                "Scientific token accounting is inconsistent."
            )
        if (
            usage.requests <= 0
            or usage.prompt_tokens <= 0
            or usage.completion_tokens <= 0
            or usage.total_tokens <= 0
            or usage.cost_usd <= 0
        ):
            raise MinimalScientificSeedResultImportError(
                "Scientific result lacks positive external-model usage evidence."
            )
        if usage.total_tokens != total_tokens:
            raise MinimalScientificSeedResultImportError(
                "Scientific aggregate tokens differ from the five reports."
            )
        if abs(usage.cost_usd - total_cost_usd) > 1e-9:
            raise MinimalScientificSeedResultImportError(
                "Scientific aggregate cost differs from the five reports."
            )
        if (
            usage.requests > plan.budget.max_requests
            or usage.prompt_tokens
            > usage.requests * plan.budget.max_prompt_bytes_per_request
            or usage.completion_tokens
            > usage.requests * plan.budget.max_output_tokens_per_request
            or usage.cost_usd > plan.budget.max_model_cost_usd
            or total_wall_seconds > plan.budget.max_runner_minutes * 60
        ):
            raise MinimalScientificSeedResultImportError(
                "Scientific result exceeds a frozen usage or wall-time cap."
            )
        mathematical_ceiling = float(
            Decimal(
                plan.budget.max_requests
                * plan.budget.max_prompt_bytes_per_request
            )
            * preset.prompt_cost_per_token_usd
            + Decimal(
                plan.budget.max_requests
                * plan.budget.max_output_tokens_per_request
            )
            * preset.completion_cost_per_token_usd
        )
        if (
            abs(result.mathematical_model_cost_ceiling_usd - mathematical_ceiling)
            > 1e-12
            or mathematical_ceiling > plan.budget.max_model_cost_usd
            or usage.cost_usd > mathematical_ceiling + 1e-12
        ):
            raise MinimalScientificSeedResultImportError(
                "Scientific result cost ceiling differs from the frozen preset."
            )

    @staticmethod
    def _build_receipt(
        result: MinimalScientificSeedResult,
        *,
        source_file_sha256: str,
        plan: MinimalScientificSeedPlan,
        lock: MinimalScientificSeedLock,
        preset: OpenRouterModelPreset,
    ) -> MinimalScientificSeedImportReceipt:
        total_tool_calls = sum(
            report.usage.tool_calls for report in result.reports
        )
        payload = {
            "format_version": "evoagent-minimal-scientific-import-receipt-v1",
            "claim_scope": result.claim_scope,
            "source_file_name": _RESULT_FILE_NAME,
            "source_file_sha256": source_file_sha256,
            "source_commit": result.source_commit,
            "plan_hash": plan.plan_hash,
            "lock_hash": lock.lock_hash,
            "manifest_hash": plan.manifest.manifest_hash,
            "model_preset_hash": plan.model_preset_hash,
            "result_evidence_hash": result.evidence_hash,
            "episode_contract_hash": _EXPECTED_EPISODE_CONTRACT_HASH,
            "result_status": result.status,
            "model_preset_id": preset.preset_id,
            "model_id": result.model_id,
            "canonical_model_id": result.canonical_model_id,
            "provider": result.provider,
            "provider_fallbacks": False,
            "authorization_anchor_hash": result.authorization_anchor_hash,
            "requester_id": result.requester_id,
            "approver_ids": result.approver_ids,
            "snapshot_ids": tuple(item.snapshot_id for item in result.reports),
            "report_hashes": tuple(item.report_hash for item in result.reports),
            "overall_scores": tuple(item.overall_score for item in result.reports),
            "report_count": len(result.reports),
            "task_result_count": sum(len(item.results) for item in result.reports),
            "total_tool_call_count": total_tool_calls,
            "total_reported_wall_seconds": sum(
                item.usage.wall_seconds for item in result.reports
            ),
            "usage": result.usage,
            "overall_score_delta": result.overall_score_delta,
            "final_retention_drop_from_first_passing_round": (
                result.final_retention_drop_from_first_passing_round
            ),
            "total_regression_count": result.total_regression_count,
            "final_safety_violation_count": result.final_safety_violation_count,
            "external_execution_performed": True,
            "external_benchmark": False,
            "official_submission_performed": False,
            "official_leaderboard_claimed": False,
        }
        return MinimalScientificSeedImportReceipt(
            **payload,
            receipt_hash=canonical_sha256(payload),
        )


def _reject_non_finite(value: str) -> None:
    raise ValueError(value)


def _reject_duplicate_object_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


@lru_cache(maxsize=8)
def _expected_episode_contract(
    plan_json: str,
    preset_json: str,
) -> tuple[tuple[tuple[str, bool, float, int, int, int], ...], ...]:
    """Replay the exact frozen local controller to bind every external episode."""

    plan = MinimalScientificSeedPlan.model_validate_json(plan_json)
    preset = OpenRouterModelPreset.model_validate_json(preset_json)
    with tempfile.TemporaryDirectory(prefix="evoagent-scientific-import-") as root:
        snapshots, _ = build_external_snapshot_chain(
            Path(root) / "snapshots",
            model_id=preset.model_id,
        )
        if tuple(item.snapshot_hash for item in snapshots) != tuple(
            item.snapshot_hash for item in plan.snapshots
        ):
            raise MinimalScientificSeedResultImportError(
                "Scientific local episode oracle differs from the frozen snapshots."
            )
        evaluator = UnifiedContinualEvaluator(Path(root) / "evaluation")
        reports = []
        for snapshot in snapshots:
            reports.append(
                evaluator.evaluate(
                    snapshot,
                    plan.manifest,
                    report_id=f"minimal-science-import-{snapshot.snapshot_id}",
                    parent=reports[-1] if reports else None,
                )
            )
    return tuple(
        tuple(
            (
                item.task_id,
                item.passed,
                item.score,
                item.safety_violation_count,
                item.tool_calls,
                item.episode_steps,
            )
            for item in report.results
        )
        for report in reports
    )


__all__ = [
    "MinimalScientificSeedImportReceipt",
    "MinimalScientificSeedResultImportError",
    "MinimalScientificSeedResultImporter",
]
