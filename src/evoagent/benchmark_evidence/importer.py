from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from evoagent.benchmark_evidence.models import (
    BenchmarkRunContract,
    BenchmarkRunEvidence,
    BenchmarkTaskAggregate,
    SafeHarborTrialEvidence,
)
from evoagent.model_registry.models import canonical_sha256


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


class HarborResultImportError(ValueError):
    pass


class HarborResultImporter:
    """Import Harbor's observable `result.json` without importing Harbor.

    Raw exception messages, tracebacks, Agent logs, trajectories, prompts,
    Environment data, and arbitrary config fields are intentionally omitted.
    The complete caller-attested raw-file SHA-256 remains bound to the safe
    evidence record.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        max_bytes: int = 64 * 1024 * 1024,
        max_trials: int = 20_000,
        max_depth: int = 64,
        max_nodes: int = 1_000_000,
    ):
        raw_root = Path(root).expanduser()
        if raw_root.is_symlink():
            raise HarborResultImportError("Harbor import root must not be a symlink.")
        self.root = raw_root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        if max_bytes <= 0 or max_trials <= 0 or max_depth <= 0 or max_nodes <= 0:
            raise ValueError("Harbor import bounds must be positive.")
        self.max_bytes = max_bytes
        self.max_trials = max_trials
        self.max_depth = max_depth
        self.max_nodes = max_nodes

    def import_file(
        self,
        relative_path: str | Path,
        *,
        expected_sha256: str,
        evidence_id: str,
        contract: BenchmarkRunContract,
    ) -> BenchmarkRunEvidence:
        path = self._resolve_file(relative_path)
        size = path.stat().st_size
        if size <= 0 or size > self.max_bytes:
            raise HarborResultImportError(
                "Harbor result file is empty or exceeds the configured size limit."
            )
        raw = path.read_bytes()
        actual_sha256 = hashlib.sha256(raw).hexdigest()
        if actual_sha256 != expected_sha256:
            raise HarborResultImportError("Harbor result file SHA-256 mismatch.")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HarborResultImportError("Harbor result file must be UTF-8 JSON.") from exc
        self._reject_secrets(text)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise HarborResultImportError("Harbor result file is not valid JSON.") from exc
        self._validate_shape_bounds(payload)
        if not isinstance(payload, dict):
            raise HarborResultImportError("Harbor result root must be a JSON object.")
        return self._parse_job(
            payload,
            evidence_id=evidence_id,
            source_file_sha256=actual_sha256,
            contract=contract,
        )

    def _resolve_file(self, relative_path: str | Path) -> Path:
        candidate = Path(relative_path)
        if candidate.is_absolute():
            raise HarborResultImportError("Harbor result path must be relative to its root.")
        if candidate.name != "result.json":
            raise HarborResultImportError("Harbor importer accepts only result.json.")
        unresolved = self.root / candidate
        current = self.root
        for part in candidate.parts:
            if part in {"", ".", ".."} or "\x00" in part:
                raise HarborResultImportError("Harbor result path contains an unsafe segment.")
            current = current / part
            if current.exists() and current.is_symlink():
                raise HarborResultImportError("Harbor result path must not contain symlinks.")
        path = unresolved.resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise HarborResultImportError("Harbor result path escapes its import root.") from exc
        if not path.is_file() or path.is_symlink():
            raise HarborResultImportError(
                "Harbor result must be a regular non-symlink file."
            )
        return path

    @staticmethod
    def _reject_secrets(text: str) -> None:
        if _SECRET_JSON_ASSIGNMENT.search(text) or any(
            pattern.search(text) for pattern in _SECRET_PATTERNS
        ):
            raise HarborResultImportError(
                "Harbor result file contains a potential credential or private key."
            )

    def _validate_shape_bounds(self, payload: Any) -> None:
        nodes = 0

        def visit(value: Any, depth: int) -> None:
            nonlocal nodes
            nodes += 1
            if nodes > self.max_nodes:
                raise HarborResultImportError("Harbor result JSON exceeds the node limit.")
            if depth > self.max_depth:
                raise HarborResultImportError("Harbor result JSON exceeds the depth limit.")
            if isinstance(value, dict):
                for key, item in value.items():
                    if not isinstance(key, str) or len(key) > 512:
                        raise HarborResultImportError(
                            "Harbor result contains an invalid or oversized JSON key."
                        )
                    visit(item, depth + 1)
            elif isinstance(value, list):
                if len(value) > self.max_trials * 32:
                    raise HarborResultImportError(
                        "Harbor result contains an oversized JSON array."
                    )
                for item in value:
                    visit(item, depth + 1)
            elif isinstance(value, str) and len(value) > self.max_bytes:
                raise HarborResultImportError(
                    "Harbor result contains an oversized JSON string."
                )

        visit(payload, 0)

    def _parse_job(
        self,
        payload: dict[str, Any],
        *,
        evidence_id: str,
        source_file_sha256: str,
        contract: BenchmarkRunContract,
    ) -> BenchmarkRunEvidence:
        job_id = _bounded_string(payload.get("id"), label="Harbor job ID", max_length=256)
        started_at = _parse_datetime(payload.get("started_at"), label="Harbor started_at")
        finished_at = _parse_datetime(payload.get("finished_at"), label="Harbor finished_at")
        n_total_trials = _integer(
            payload.get("n_total_trials"),
            label="Harbor n_total_trials",
            minimum=1,
        )
        if n_total_trials > self.max_trials:
            raise HarborResultImportError("Harbor result exceeds the trial-count limit.")
        stats = payload.get("stats")
        if not isinstance(stats, dict):
            raise HarborResultImportError("Harbor result stats must be an object.")
        n_completed = _integer(
            stats.get("n_completed_trials"),
            label="Harbor completed-trial count",
            minimum=0,
        )
        n_errored = _integer(
            stats.get("n_errored_trials"),
            label="Harbor errored-trial count",
            minimum=0,
        )
        n_cancelled = _integer(
            stats.get("n_cancelled_trials", 0),
            label="Harbor cancelled-trial count",
            minimum=0,
        )
        n_running = _integer(
            stats.get("n_running_trials", 0),
            label="Harbor running-trial count",
            minimum=0,
        )
        n_pending = _integer(
            stats.get("n_pending_trials", 0),
            label="Harbor pending-trial count",
            minimum=0,
        )
        if n_completed != n_total_trials or n_running != 0 or n_pending != 0:
            raise HarborResultImportError(
                "Harbor evidence import requires a fully completed job."
            )
        trial_payloads = payload.get("trial_results")
        if not isinstance(trial_payloads, list):
            raise HarborResultImportError("Harbor trial_results must be an array.")
        if len(trial_payloads) != n_total_trials:
            raise HarborResultImportError(
                "Harbor declared total differs from the number of trial results."
            )
        trials = tuple(
            self._parse_trial(item, contract=contract)
            for item in trial_payloads
        )
        task_aggregates = _build_task_aggregates(contract, trials)
        score = sum(item.primary_reward for item in trials) / len(trials)
        error_rate = sum(item.error_type is not None for item in trials) / len(trials)
        token_usage_complete = all(
            item.input_tokens is not None
            and item.cache_tokens is not None
            and item.output_tokens is not None
            for item in trials
        )
        cost_usage_complete = all(item.cost_usd is not None for item in trials)
        total_input_tokens = (
            sum(item.input_tokens or 0 for item in trials)
            if token_usage_complete
            else None
        )
        total_cache_tokens = (
            sum(item.cache_tokens or 0 for item in trials)
            if token_usage_complete
            else None
        )
        total_output_tokens = (
            sum(item.output_tokens or 0 for item in trials)
            if token_usage_complete
            else None
        )
        total_cost_usd = (
            sum(item.cost_usd or 0.0 for item in trials)
            if cost_usage_complete
            else None
        )
        self._verify_job_totals(
            stats,
            token_usage_complete=token_usage_complete,
            cost_usage_complete=cost_usage_complete,
            total_input_tokens=total_input_tokens,
            total_cache_tokens=total_cache_tokens,
            total_output_tokens=total_output_tokens,
            total_cost_usd=total_cost_usd,
        )
        run_payload = {
            "evidence_id": evidence_id,
            "harbor_job_id": job_id,
            "source_file_name": "result.json",
            "source_file_sha256": source_file_sha256,
            "contract": contract,
            "started_at": started_at,
            "finished_at": finished_at,
            "n_total_trials": n_total_trials,
            "n_errored_trials": n_errored,
            "n_cancelled_trials": n_cancelled,
            "trials": trials,
            "task_aggregates": task_aggregates,
            "score": score,
            "error_rate": error_rate,
            "token_usage_complete": token_usage_complete,
            "cost_usage_complete": cost_usage_complete,
            "total_input_tokens": total_input_tokens,
            "total_cache_tokens": total_cache_tokens,
            "total_output_tokens": total_output_tokens,
            "total_cost_usd": total_cost_usd,
            "harbor_execution_performed_by_evoagent": False,
            "external_model_call_performed_by_evoagent": False,
            "upload_performed_by_evoagent": False,
            "official_submission_performed": False,
            "official_submission_accepted": False,
        }
        return BenchmarkRunEvidence(
            **run_payload,
            evidence_hash=canonical_sha256(run_payload),
        )

    @staticmethod
    def _parse_trial(
        payload: Any,
        *,
        contract: BenchmarkRunContract,
    ) -> SafeHarborTrialEvidence:
        if not isinstance(payload, dict):
            raise HarborResultImportError("Each Harbor trial result must be an object.")
        task_name = _bounded_string(
            payload.get("task_name"),
            label="Harbor task_name",
            max_length=256,
        )
        trial_name = _bounded_string(
            payload.get("trial_name"),
            label="Harbor trial_name",
            max_length=256,
        )
        task_id = _canonical_task_id(payload.get("task_id"))
        task_checksum = _sha256_string(
            payload.get("task_checksum"),
            label="Harbor task_checksum",
        )
        source = _bounded_string(
            payload.get("source"),
            label="Harbor trial source",
            max_length=4096,
        )
        agent_info = payload.get("agent_info")
        if not isinstance(agent_info, dict):
            raise HarborResultImportError("Harbor trial agent_info must be an object.")
        agent_name = _bounded_string(
            agent_info.get("name"),
            label="Harbor Agent name",
            max_length=256,
        )
        agent_version = _bounded_string(
            agent_info.get("version"),
            label="Harbor Agent version",
            max_length=256,
        )
        model_info = agent_info.get("model_info")
        if not isinstance(model_info, dict):
            raise HarborResultImportError(
                "Harbor trial requires explicit model_info for same-model comparison."
            )
        model_name = _bounded_string(
            model_info.get("name"),
            label="Harbor Model name",
            max_length=256,
        )
        model_provider = _bounded_string(
            model_info.get("provider"),
            label="Harbor Model provider",
            max_length=256,
        )
        exception_info = payload.get("exception_info")
        error_type: str | None = None
        if exception_info is not None:
            if not isinstance(exception_info, dict):
                raise HarborResultImportError(
                    "Harbor trial exception_info must be an object when present."
                )
            error_type = _bounded_string(
                exception_info.get("exception_type"),
                label="Harbor exception type",
                max_length=256,
            )
        verifier_result = payload.get("verifier_result")
        rewards: dict[str, float] = {}
        verifier_evidence_present = False
        if verifier_result is not None:
            if not isinstance(verifier_result, dict):
                raise HarborResultImportError(
                    "Harbor verifier_result must be an object when present."
                )
            raw_rewards = verifier_result.get("rewards")
            if raw_rewards is not None:
                if not isinstance(raw_rewards, dict) or not raw_rewards:
                    raise HarborResultImportError(
                        "Harbor verifier rewards must be a non-empty object."
                    )
                rewards = {
                    _bounded_string(key, label="Harbor reward key", max_length=256): (
                        _finite_number(value, label=f"Harbor reward {key}")
                    )
                    for key, value in raw_rewards.items()
                }
                if contract.suite.primary_reward_key not in rewards:
                    raise HarborResultImportError(
                        "Harbor verifier rewards omit the frozen primary reward key."
                    )
                verifier_evidence_present = True
        primary_reward = (
            0.0
            if error_type is not None or not verifier_evidence_present
            else rewards[contract.suite.primary_reward_key]
        )
        if primary_reward < 0.0 or primary_reward > 1.0:
            raise HarborResultImportError(
                "Terminal-Bench primary reward must be between zero and one."
            )
        input_tokens, cache_tokens, output_tokens, cost_usd = _trial_usage(payload)
        duration_seconds = _trial_duration(payload)
        trial_payload = {
            "trial_name": trial_name,
            "task_name": task_name,
            "task_id": task_id,
            "task_checksum": task_checksum,
            "source": source,
            "agent_name": agent_name,
            "agent_version": agent_version,
            "model_provider": model_provider,
            "model_name": model_name,
            "rewards": rewards,
            "verifier_evidence_present": verifier_evidence_present,
            "primary_reward": primary_reward,
            "error_type": error_type,
            "input_tokens": input_tokens,
            "cache_tokens": cache_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost_usd,
            "duration_seconds": duration_seconds,
        }
        return SafeHarborTrialEvidence(
            **trial_payload,
            evidence_hash=canonical_sha256(trial_payload),
        )

    @staticmethod
    def _verify_job_totals(
        stats: dict[str, Any],
        *,
        token_usage_complete: bool,
        cost_usage_complete: bool,
        total_input_tokens: int | None,
        total_cache_tokens: int | None,
        total_output_tokens: int | None,
        total_cost_usd: float | None,
    ) -> None:
        if token_usage_complete:
            for key, expected in (
                ("n_input_tokens", total_input_tokens),
                ("n_cache_tokens", total_cache_tokens),
                ("n_output_tokens", total_output_tokens),
            ):
                declared = stats.get(key)
                if declared is not None and _integer(
                    declared,
                    label=f"Harbor {key}",
                    minimum=0,
                ) != expected:
                    raise HarborResultImportError(
                        f"Harbor job {key} differs from per-trial evidence."
                    )
        if cost_usage_complete and stats.get("cost_usd") is not None:
            declared_cost = _finite_number(
                stats["cost_usd"],
                label="Harbor job cost_usd",
            )
            if total_cost_usd is None or abs(declared_cost - total_cost_usd) > 1e-9:
                raise HarborResultImportError(
                    "Harbor job cost differs from per-trial evidence."
                )


def _build_task_aggregates(
    contract: BenchmarkRunContract,
    trials: tuple[SafeHarborTrialEvidence, ...],
) -> tuple[BenchmarkTaskAggregate, ...]:
    by_task: dict[str, list[SafeHarborTrialEvidence]] = {
        item.task_name: [] for item in contract.suite.tasks
    }
    identities = {item.task_name: item for item in contract.suite.tasks}
    for trial in trials:
        if trial.task_name in by_task:
            by_task[trial.task_name].append(trial)
    aggregates: list[BenchmarkTaskAggregate] = []
    for task_name in sorted(identities):
        identity = identities[task_name]
        items = by_task[task_name]
        if not items:
            raise HarborResultImportError(
                f"Harbor result is missing frozen Task {task_name}."
            )
        payload = {
            "task_name": identity.task_name,
            "task_id": identity.task_id,
            "task_checksum": identity.task_checksum,
            "trial_count": len(items),
            "score": sum(item.primary_reward for item in items) / len(items),
            "error_count": sum(item.error_type is not None for item in items),
        }
        aggregates.append(
            BenchmarkTaskAggregate(
                **payload,
                aggregate_hash=canonical_sha256(payload),
            )
        )
    return tuple(aggregates)


def _trial_usage(
    payload: dict[str, Any],
) -> tuple[int | None, int | None, int | None, float | None]:
    contexts: list[dict[str, Any]] = []
    direct = payload.get("agent_result")
    if isinstance(direct, dict):
        contexts.append(direct)
    elif direct is not None:
        raise HarborResultImportError(
            "Harbor trial agent_result must be an object when present."
        )
    steps = payload.get("step_results")
    if steps is not None:
        if not isinstance(steps, list):
            raise HarborResultImportError(
                "Harbor trial step_results must be an array when present."
            )
        for step in steps:
            if not isinstance(step, dict):
                raise HarborResultImportError("Harbor step result must be an object.")
            context = step.get("agent_result")
            if isinstance(context, dict):
                contexts.append(context)
            elif context is not None:
                raise HarborResultImportError(
                    "Harbor step agent_result must be an object when present."
                )
    if not contexts:
        return None, None, None, None

    def summed_int(name: str) -> int | None:
        values = [context.get(name) for context in contexts]
        if any(value is None for value in values):
            return None
        return sum(
            _integer(value, label=f"Harbor Agent {name}", minimum=0)
            for value in values
        )

    def summed_cost() -> float | None:
        values = [context.get("cost_usd") for context in contexts]
        if any(value is None for value in values):
            return None
        total = sum(
            _finite_number(value, label="Harbor Agent cost_usd")
            for value in values
        )
        if total < 0.0:
            raise HarborResultImportError("Harbor Agent cost must be non-negative.")
        return total

    return (
        summed_int("n_input_tokens"),
        summed_int("n_cache_tokens"),
        summed_int("n_output_tokens"),
        summed_cost(),
    )


def _trial_duration(payload: dict[str, Any]) -> float | None:
    started = payload.get("started_at")
    finished = payload.get("finished_at")
    if started is None and finished is None:
        return None
    if started is None or finished is None:
        raise HarborResultImportError(
            "Harbor trial timing requires both start and finish times."
        )
    start_time = _parse_datetime(started, label="Harbor trial started_at")
    finish_time = _parse_datetime(finished, label="Harbor trial finished_at")
    duration = (finish_time - start_time).total_seconds()
    if duration < 0.0:
        raise HarborResultImportError("Harbor trial finish time predates start time.")
    return duration


def _canonical_task_id(value: Any) -> str:
    if isinstance(value, str):
        return _bounded_string(value, label="Harbor task_id", max_length=4096)
    if value is None or not isinstance(value, (dict, list)):
        raise HarborResultImportError(
            "Harbor task_id must be a string, object, or array."
        )
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
    except (TypeError, ValueError) as exc:
        raise HarborResultImportError("Harbor task_id is not canonical JSON.") from exc
    return _bounded_string(encoded, label="Harbor task_id", max_length=4096)


def _parse_datetime(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise HarborResultImportError(f"{label} must be an ISO-8601 string.")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise HarborResultImportError(f"{label} is not valid ISO-8601.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HarborResultImportError(f"{label} must include a timezone.")
    return parsed


def _bounded_string(value: Any, *, label: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise HarborResultImportError(f"{label} must be a string.")
    if not value.strip() or len(value) > max_length or "\x00" in value:
        raise HarborResultImportError(f"{label} must be bounded and non-empty.")
    return value


def _sha256_string(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise HarborResultImportError(f"{label} must be lowercase SHA-256 hex.")
    return value


def _integer(value: Any, *, label: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise HarborResultImportError(f"{label} must be an integer >= {minimum}.")
    return value


def _finite_number(value: Any, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HarborResultImportError(f"{label} must be numeric.")
    result = float(value)
    if not math.isfinite(result):
        raise HarborResultImportError(f"{label} must be finite.")
    return result


__all__ = [
    "HarborResultImportError",
    "HarborResultImporter",
]
