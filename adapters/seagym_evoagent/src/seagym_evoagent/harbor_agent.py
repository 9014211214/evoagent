"""Harbor custom agent for the frozen MiMoCode/OpenRouter experiment route."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import shlex
import tempfile
from typing import Any

from ._compat import BaseAgent, NonZeroAgentExitCodeError
from .canonical import atomic_write_json, canonical_bytes, contained_path, read_json, sha256_file, sha256_json
from .mimocode import (
    HARBOR_RUNTIME_COMMIT,
    MIMOCODE_ARCHIVE_ENV,
    MIMOCODE_ARCHIVE_URL,
    MIMOCODE_ARCHIVE_SHA256,
    MIMOCODE_VERSION,
    SEAGYM_COMMIT,
    install_command,
    locked_mimocode_config,
    runtime_env,
)
from .models import HARBOR_MODEL_ID, UPDATE_MODEL_ID, HarnessSnapshot, SECRET_PATTERNS
from .routing import expected_route_contract, validate_route_contract
from .runtime_sanitizer import FAILURE_RECEIPT_FILENAME, FAILURE_RECEIPT_SCHEMA


ATTESTATION_SCHEMA = "evoagent-harbor-attestation-v1"
ATTESTATION_FILENAME = "evoagent-attestation.json"
ATIF_FILENAME = "trajectory.json"
REMOTE_RUNTIME_DIR = "/tmp/evoagent-mimo-runtime"
REMOTE_ATIF_PATH = f"/logs/agent/{ATIF_FILENAME}"
REMOTE_FAILURE_RECEIPT_PATH = f"/logs/agent/{FAILURE_RECEIPT_FILENAME}"
ADAPTER_VERSION = "0.1.0"
MIMOCODE_PROCESS_EXIT = 80
SANITIZER_REJECT_EXIT = 81
MIMOCODE_AND_SANITIZER_EXIT = 82
PROXY_TOKEN_PATTERN = re.compile(r"evoagent-local-proxy-v1-[0-9a-f]{64}")
SAFE_TOOL_NAMES = {
    "apply_patch",
    "bash",
    "browser",
    "edit",
    "edit_file",
    "exec",
    "exec_command",
    "execute",
    "execute_command",
    "glob",
    "grep",
    "python",
    "read",
    "read_file",
    "search",
    "shell",
    "web_search",
    "websearch",
    "webfetch",
    "codesearch",
    "actor",
    "skill",
    "write",
    "write_file",
}
SAFE_OBSERVATIONS = {
    "status:pending",
    "status:running",
    "status:success",
    "status:error",
    "status:timeout",
    "status:cancelled",
    "status:unknown",
}
FAILURE_RECEIPT_CLASSES = {
    "mimocode_process_failed",
    "runtime_sanitization_failed",
    "mimocode_and_sanitization_failed",
}
FAILURE_RECEIPT_STAGES = {"mimocode", "sanitize"}
MIMOCODE_EXIT_CLASSES = {
    "nonzero",
    "signal",
    "timeout",
    "spawn_failed",
    "success",
    "unknown",
}


class EvoAgentMiMo(BaseAgent):
    """A privacy-preserving Harbor agent; it has no candidate promotion authority."""

    SUPPORTS_ATIF = True
    SUPPORTS_WINDOWS = False

    def __init__(
        self,
        logs_dir: Path,
        model_name: str | None = None,
        logger: Any | None = None,
        mcp_servers: list[Any] | None = None,
        skills_dir: str | None = None,
        *,
        extra_env: dict[str, str] | None = None,
        prompt_template_path: str | Path | None = None,
        seed: int = 43,
        timeout_seconds: int = 1800,
        route_contract: dict[str, Any] | None = None,
        mimocode_asset_sha256: str = MIMOCODE_ARCHIVE_SHA256,
        mimocode_asset_url: str = MIMOCODE_ARCHIVE_URL,
        mimocode_version: str = MIMOCODE_VERSION,
        **kwargs: Any,
    ) -> None:
        if kwargs:
            raise ValueError("EvoAgentMiMo received unsupported constructor fields")
        locked_model = model_name or HARBOR_MODEL_ID
        if locked_model != HARBOR_MODEL_ID:
            raise ValueError(f"EvoAgentMiMo model must be exactly {HARBOR_MODEL_ID}")
        if prompt_template_path is None:
            raise ValueError("prompt_template_path is required")
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2**63 - 1:
            raise ValueError("seed must be a non-negative signed 64-bit integer")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or not 60 <= timeout_seconds <= 7200:
            raise ValueError("timeout_seconds must be an integer in [60, 7200]")
        self.route_contract = validate_route_contract(route_contract or expected_route_contract())
        if mimocode_asset_sha256 != MIMOCODE_ARCHIVE_SHA256:
            raise ValueError("mimocode_asset_sha256 does not match the frozen artifact")
        if mimocode_asset_url != MIMOCODE_ARCHIVE_URL:
            raise ValueError("mimocode_asset_url does not match the frozen official release asset")
        if mimocode_version != MIMOCODE_VERSION:
            raise ValueError("mimocode_version does not match the frozen artifact")
        if extra_env is not None and (
            not isinstance(extra_env, dict)
            or not all(isinstance(key, str) and isinstance(value, str) for key, value in extra_env.items())
        ):
            raise ValueError("extra_env must be a string mapping")
        super().__init__(
            logs_dir,
            locked_model,
            logger=logger,
            mcp_servers=mcp_servers,
            skills_dir=skills_dir,
            extra_env=extra_env,
        )
        self.prompt_template_path = Path(prompt_template_path).resolve(strict=True)
        if self.prompt_template_path.is_symlink():
            raise ValueError("prompt_template_path cannot be a symlink")
        self.seed = seed
        self.timeout_seconds = timeout_seconds
        self.snapshot = _snapshot_for_prompt(self.prompt_template_path)

    @staticmethod
    def name() -> str:
        return "evoagent-mimo"

    def version(self) -> str | None:
        return ADAPTER_VERSION

    async def setup(self, environment: Any) -> None:
        raw_archive_path = os.environ.get(MIMOCODE_ARCHIVE_ENV)
        if not raw_archive_path:
            raise RuntimeError(f"{MIMOCODE_ARCHIVE_ENV} is required")
        archive_input = Path(raw_archive_path)
        if archive_input.is_symlink():
            raise RuntimeError("pinned MiMoCode archive cannot be a symlink")
        archive_path = archive_input.resolve(strict=True)
        if (
            not archive_path.is_file()
            or sha256_file(archive_path, max_bytes=50 * 1024 * 1024)
            != MIMOCODE_ARCHIVE_SHA256
        ):
            raise RuntimeError("pinned MiMoCode host archive failed SHA-256 verification")
        prepare = await environment.exec(
            command="rm -rf /tmp/evoagent-mimocode-install && mkdir -p /tmp/evoagent-mimocode-install",
            user="root",
            timeout_sec=30,
        )
        if prepare.return_code != 0:
            raise RuntimeError("MiMoCode runtime directory preparation failed")
        await environment.upload_file(
            archive_path,
            "/tmp/evoagent-mimocode-install/archive.tar.gz",
        )
        result = await environment.exec(command=install_command(), user="root", timeout_sec=600)
        if result.return_code != 0:
            raise RuntimeError("pinned MiMoCode installation or SHA-256 verification failed")

    async def run(self, instruction: str, environment: Any, context: Any) -> None:
        if not isinstance(instruction, str) or not instruction:
            raise ValueError("Harbor instruction must be non-empty text")
        template = self.prompt_template_path.read_text(encoding="utf-8")
        if template.count("{{ instruction }}") != 1:
            raise ValueError("prompt template must contain exactly one instruction slot")
        projection = template.replace("{{ instruction }}", instruction)
        metadata = {
            "snapshot_hash": self.snapshot.snapshot_sha256,
            "component_hashes": dict(self.snapshot.component_sha256),
            "runtime_identity": {"name": "mimocode", "version": MIMOCODE_VERSION},
            "route_contract_sha256": sha256_json(self.route_contract),
        }
        config = locked_mimocode_config(
            self.route_contract,
            max_iterations=self.snapshot.components.policy.max_iterations,
        )
        if _contains_secret(config):
            raise ValueError("locked MiMoCode config unexpectedly contains secret material")
        proxy_token = self.extra_env.get("OPENROUTER_API_KEY")
        if not isinstance(proxy_token, str) or not PROXY_TOKEN_PATTERN.fullmatch(proxy_token):
            raise RuntimeError("Harbor rollout is missing its run-scoped local proxy capability")

        with tempfile.TemporaryDirectory(prefix="evoagent-harbor-upload-") as temporary:
            temp_root = Path(temporary)
            prompt_local = temp_root / "projected-task.md"
            config_local = temp_root / "mimocode.json"
            sanitizer_local = Path(__file__).with_name("runtime_sanitizer.py")
            try:
                prompt_local.write_text(projection, encoding="utf-8")
                config_local.write_text(json.dumps(config, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
                await environment.exec(command=f"rm -rf {REMOTE_RUNTIME_DIR} && mkdir -p {REMOTE_RUNTIME_DIR}/home", timeout_sec=30)
                await environment.upload_file(prompt_local, f"{REMOTE_RUNTIME_DIR}/projected-task.md")
                await environment.upload_file(config_local, f"{REMOTE_RUNTIME_DIR}/mimocode.json")
                await environment.upload_file(sanitizer_local, f"{REMOTE_RUNTIME_DIR}/runtime_sanitizer.py")
            finally:
                _scrub_local_file(prompt_local)

        env = runtime_env(
            f"{REMOTE_RUNTIME_DIR}/mimocode.json",
            f"{REMOTE_RUNTIME_DIR}/home",
            proxy_token=proxy_token,
        )
        command = _run_command(metadata, self.seed)
        result = await environment.exec(command=command, env=env, timeout_sec=self.timeout_seconds)
        classified_failure = {
            MIMOCODE_PROCESS_EXIT: "mimocode_process_failed",
            SANITIZER_REJECT_EXIT: "runtime_sanitization_failed",
            MIMOCODE_AND_SANITIZER_EXIT: "mimocode_and_sanitization_failed",
        }.get(result.return_code)
        if classified_failure is not None:
            raise NonZeroAgentExitCodeError(
                f"EvoAgentMiMo classified runtime failure: {classified_failure}"
            )
        if result.return_code != 0:
            raise RuntimeError("EvoAgentMiMo runtime exited without a classified failure receipt")
        # Harbor calls populate_context_post_run only when this object is still
        # completely empty after it has synchronized the safe ATIF file.
        # Writing even provisional metadata here would suppress that hook.
        del context

    def populate_context_post_run(self, context: Any) -> None:
        atif_path = self.logs_dir / ATIF_FILENAME
        receipt_path = self.logs_dir / FAILURE_RECEIPT_FILENAME
        if atif_path.exists() and (not atif_path.is_file() or atif_path.is_symlink()):
            raise RuntimeError("privacy-preserving ATIF output is invalid")
        atif_present = atif_path.is_file() and not atif_path.is_symlink()
        receipt = None
        if receipt_path.exists() or receipt_path.is_symlink():
            if not receipt_path.is_file() or receipt_path.is_symlink():
                raise RuntimeError("runtime failure receipt is invalid")
            receipt = _validate_failure_receipt(
                read_json(receipt_path, max_bytes=64 * 1024),
                snapshot=self.snapshot,
                seed=self.seed,
                route_contract_sha256=sha256_json(self.route_contract),
                atif_present=atif_present,
            )
        if not atif_present:
            if receipt is None:
                raise RuntimeError("privacy-preserving ATIF output and runtime failure receipt are missing")
            _populate_failure_context(context, receipt)
            return
        atif = read_json(atif_path, max_bytes=8 * 1024 * 1024)
        usage = _validate_sanitized_atif(atif, self.snapshot, self.seed)
        atif_hash = sha256_file(atif_path, max_bytes=8 * 1024 * 1024)
        unsigned = {
            "schema_version": ATTESTATION_SCHEMA,
            "snapshot_sha256": self.snapshot.snapshot_sha256,
            "component_sha256": dict(self.snapshot.component_sha256),
            "atif_sha256": atif_hash,
            "route_contract_sha256": sha256_json(self.route_contract),
            "model": {
                "api_id": UPDATE_MODEL_ID,
                "harbor_id": HARBOR_MODEL_ID,
                "openrouter_provider": self.route_contract["provider"]["only"][0],
                "fallbacks_allowed": self.route_contract["provider"]["allow_fallbacks"],
                "reasoning_enabled": self.route_contract["reasoning"]["enabled"],
                "credential_transport": "local_guard_proxy_v1",
            },
            "seed": self.seed,
            "runtime": {
                "adapter_version": ADAPTER_VERSION,
                "mimocode_version": MIMOCODE_VERSION,
                "mimocode_archive_sha256": MIMOCODE_ARCHIVE_SHA256,
                "seagym_commit": SEAGYM_COMMIT,
                "harbor_commit": HARBOR_RUNTIME_COMMIT,
            },
            "usage": usage,
            "runtime_failure_receipt_sha256": None,
            "raw_prompt_persisted": False,
            "raw_response_persisted": False,
            "reasoning_persisted": False,
            "causal_attribution_claimed": False,
            "promotion_claimed": False,
            "activation_claimed": False,
        }
        if receipt is not None:
            unsigned["runtime_failure_receipt_sha256"] = receipt["receipt_sha256"]
        attestation = {**unsigned, "attestation_sha256": sha256_json(unsigned)}
        atomic_write_json(self.logs_dir / ATTESTATION_FILENAME, attestation)
        context.n_input_tokens = usage["prompt_tokens"]
        context.n_cache_tokens = usage["cached_tokens"]
        context.n_output_tokens = usage["completion_tokens"]
        context.cost_usd = usage["cost_usd"]
        context.rollout_details = None
        context_metadata = {
            "attestation_sha256": attestation["attestation_sha256"],
            "atif_sha256": atif_hash,
            "snapshot_sha256": self.snapshot.snapshot_sha256,
            "model_id": UPDATE_MODEL_ID,
            "seed": self.seed,
            "route_contract_sha256": sha256_json(self.route_contract),
            "privacy_projection": True,
        }
        if receipt is not None:
            context_metadata["runtime_failure_receipt_sha256"] = receipt["receipt_sha256"]
        context.metadata = context_metadata


def _validate_failure_receipt(
    raw: Any,
    *,
    snapshot: HarnessSnapshot,
    seed: int,
    route_contract_sha256: str,
    atif_present: bool,
) -> dict[str, Any]:
    expected_keys = {
        "schema_version",
        "failure_class",
        "failure_stage",
        "mimocode_exit_class",
        "snapshot_sha256",
        "component_sha256",
        "route_contract_sha256",
        "model",
        "seed",
        "runtime",
        "atif_present",
        "raw_prompt_persisted",
        "raw_response_persisted",
        "reasoning_content_persisted",
        "receipt_sha256",
    }
    if not isinstance(raw, dict) or set(raw) != expected_keys:
        raise RuntimeError("runtime failure receipt shape is invalid")
    if raw.get("schema_version") != FAILURE_RECEIPT_SCHEMA:
        raise RuntimeError("runtime failure receipt schema is invalid")
    failure_class = raw.get("failure_class")
    failure_stage = raw.get("failure_stage")
    exit_class = raw.get("mimocode_exit_class")
    if failure_class not in FAILURE_RECEIPT_CLASSES:
        raise RuntimeError("runtime failure receipt class is invalid")
    if failure_stage not in FAILURE_RECEIPT_STAGES:
        raise RuntimeError("runtime failure receipt stage is invalid")
    if exit_class not in MIMOCODE_EXIT_CLASSES:
        raise RuntimeError("runtime failure receipt exit class is invalid")
    expected_pair = {
        "mimocode_process_failed": ("mimocode", False),
        "runtime_sanitization_failed": ("sanitize", True),
        "mimocode_and_sanitization_failed": ("sanitize", False),
    }[failure_class]
    if failure_stage != expected_pair[0] or (exit_class == "success") != expected_pair[1]:
        raise RuntimeError("runtime failure receipt classification is inconsistent")
    if raw.get("snapshot_sha256") != snapshot.snapshot_sha256:
        raise RuntimeError("runtime failure receipt snapshot is invalid")
    if raw.get("component_sha256") != dict(snapshot.component_sha256):
        raise RuntimeError("runtime failure receipt components are invalid")
    if raw.get("route_contract_sha256") != route_contract_sha256:
        raise RuntimeError("runtime failure receipt route is invalid")
    if raw.get("model") != {"api_id": UPDATE_MODEL_ID, "harbor_id": HARBOR_MODEL_ID}:
        raise RuntimeError("runtime failure receipt model is invalid")
    if raw.get("seed") != seed:
        raise RuntimeError("runtime failure receipt seed is invalid")
    if raw.get("runtime") != {"name": "mimocode", "version": MIMOCODE_VERSION}:
        raise RuntimeError("runtime failure receipt runtime is invalid")
    if raw.get("atif_present") is not atif_present:
        raise RuntimeError("runtime failure receipt ATIF state is invalid")
    for key in (
        "raw_prompt_persisted",
        "raw_response_persisted",
        "reasoning_content_persisted",
    ):
        if raw.get(key) is not False:
            raise RuntimeError("runtime failure receipt privacy flags are invalid")
    digest = raw.get("receipt_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise RuntimeError("runtime failure receipt digest is invalid")
    unsigned = dict(raw)
    unsigned.pop("receipt_sha256")
    if sha256_json(unsigned) != digest or _contains_secret(raw):
        raise RuntimeError("runtime failure receipt self-attestation is invalid")
    return dict(raw)


def _populate_failure_context(context: Any, receipt: dict[str, Any]) -> None:
    context.n_input_tokens = 0
    context.n_cache_tokens = 0
    context.n_output_tokens = 0
    context.cost_usd = 0.0
    context.rollout_details = None
    context.metadata = {
        "runtime_failure_receipt_sha256": receipt["receipt_sha256"],
        "runtime_failure_class": receipt["failure_class"],
        "runtime_failure_stage": receipt["failure_stage"],
        "mimocode_exit_class": receipt["mimocode_exit_class"],
        "snapshot_sha256": receipt["snapshot_sha256"],
        "model_id": receipt["model"]["api_id"],
        "seed": receipt["seed"],
        "route_contract_sha256": receipt["route_contract_sha256"],
        "privacy_projection": True,
    }


def _snapshot_for_prompt(prompt_path: Path) -> HarnessSnapshot:
    if prompt_path.suffix != ".md" or len(prompt_path.stem) != 64:
        raise ValueError("prompt template must be content-addressed by snapshot hash")
    state_root = prompt_path.parent.parent.resolve(strict=True)
    contained_path(state_root, prompt_path, must_exist=True)
    snapshot_path = contained_path(
        state_root,
        state_root / "snapshots" / f"{prompt_path.stem}.json",
        must_exist=True,
    )
    snapshot = HarnessSnapshot.from_dict(read_json(snapshot_path))
    if snapshot.snapshot_sha256 != prompt_path.stem:
        raise ValueError("prompt and snapshot hashes do not match")
    return snapshot


def _run_command(metadata: dict[str, Any], seed: int) -> str:
    metadata_json = canonical_bytes(metadata).decode("utf-8")
    sanitizer_args = " ".join(
        (
            "python3",
            f"{REMOTE_RUNTIME_DIR}/runtime_sanitizer.py",
            "--input",
            f"{REMOTE_RUNTIME_DIR}/events.jsonl",
            "--output",
            REMOTE_ATIF_PATH,
            "--model",
            shlex.quote(HARBOR_MODEL_ID),
            "--seed",
            str(seed),
            "--snapshot-metadata-json",
            shlex.quote(metadata_json),
            "--prompt-file",
            f"{REMOTE_RUNTIME_DIR}/projected-task.md",
            "--session-dir",
            REMOTE_RUNTIME_DIR,
        )
    )
    sanitizer_args += (
        ' --mimocode-exit-code "$mimo_status"'
        f" --failure-receipt {REMOTE_FAILURE_RECEIPT_PATH}"
    )
    # The raw task never appears in the command. stdout/stderr and MiMoCode state
    # remain under the disposable runtime directory until the sanitizer removes it.
    return (
        f"trap 'rm -rf {REMOTE_RUNTIME_DIR}' EXIT; "
        f"rm -f {REMOTE_ATIF_PATH} {REMOTE_FAILURE_RECEIPT_PATH}; set +e; "
        f"/usr/local/bin/mimo run --model {shlex.quote(HARBOR_MODEL_ID)} --agent build --format json "
        f"--file {REMOTE_RUNTIME_DIR}/projected-task.md --dangerously-skip-permissions "
        "'Complete the attached task under its stated constraints.' "
        f"> {REMOTE_RUNTIME_DIR}/events.jsonl 2> {REMOTE_RUNTIME_DIR}/stderr.log; "
        "mimo_status=$?; "
        f"{sanitizer_args}; "
        "sanitize_status=$?; "
        "if [ \"$mimo_status\" -eq 0 ] && [ \"$sanitize_status\" -eq 0 ]; then exit 0; fi; "
        f"if [ \"$mimo_status\" -ne 0 ] && [ \"$sanitize_status\" -eq 0 ]; then exit {MIMOCODE_PROCESS_EXIT}; fi; "
        f"if [ \"$mimo_status\" -eq 0 ] && [ \"$sanitize_status\" -ne 0 ]; then exit {SANITIZER_REJECT_EXIT}; fi; "
        f"exit {MIMOCODE_AND_SANITIZER_EXIT}"
    )


def _validate_sanitized_atif(raw: Any, snapshot: HarnessSnapshot, seed: int) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != {"schema_version", "agent", "steps", "final_metrics", "extra"}:
        raise ValueError("ATIF root shape is invalid")
    if raw.get("schema_version") != "ATIF-v1.7":
        raise ValueError("ATIF schema is invalid")
    rendered = canonical_bytes(raw).decode("utf-8")
    if "reasoning_content" in rendered or '"reasoning"' in rendered or _contains_secret(raw):
        raise ValueError("ATIF contains forbidden reasoning or secret material")
    extra = raw.get("extra")
    if not isinstance(extra, dict):
        raise ValueError("ATIF privacy metadata is missing")
    if extra.get("snapshot_hash") != snapshot.snapshot_sha256:
        raise ValueError("ATIF snapshot hash mismatch")
    if extra.get("component_hashes") != dict(snapshot.component_sha256):
        raise ValueError("ATIF component hash mismatch")
    if extra.get("api_model_id") != UPDATE_MODEL_ID or extra.get("seed") != seed:
        raise ValueError("ATIF model or seed mismatch")
    if set(extra) != {
        "api_model_id",
        "seed",
        "snapshot_hash",
        "component_hashes",
        "runtime_identity",
        "route_contract_sha256",
    }:
        raise ValueError("ATIF privacy metadata shape is invalid")
    if extra.get("runtime_identity") != {"name": "mimocode", "version": MIMOCODE_VERSION}:
        raise ValueError("ATIF runtime identity mismatch")
    if extra.get("route_contract_sha256") != sha256_json(expected_route_contract()):
        raise ValueError("ATIF route contract lock mismatch")
    agent = raw.get("agent")
    if not isinstance(agent, dict) or set(agent) != {"name", "version", "model_name", "extra"}:
        raise ValueError("ATIF agent shape is invalid")
    if agent != {
        "name": "seagym-evoagent-mimocode",
        "version": ADAPTER_VERSION,
        "model_name": HARBOR_MODEL_ID,
        "extra": extra,
    }:
        raise ValueError("ATIF agent identity mismatch")
    steps = raw.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("ATIF steps are missing")
    aggregate_metrics: dict[str, int | float] = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cached_tokens": 0,
        "cost_usd": 0.0,
        "reasoning_tokens": 0,
    }
    seen_metrics: set[str] = set()
    saw_reasoning_telemetry = False
    for expected, step in enumerate(steps, start=1):
        if not isinstance(step, dict) or step.get("step_id") != expected:
            raise ValueError("ATIF step sequence is invalid")
        if step.get("message") != "":
            raise ValueError("ATIF step contains raw model text")
        if expected == 1:
            if step != {"step_id": 1, "source": "system", "message": "", "extra": {"status": "sanitized"}}:
                raise ValueError("ATIF privacy-boundary system step is invalid")
            continue
        allowed_step_keys = {
            "step_id",
            "source",
            "message",
            "model_name",
            "timestamp",
            "metrics",
            "llm_call_count",
            "tool_calls",
            "observation",
            "extra",
        }
        if set(step) - allowed_step_keys or step.get("source") != "agent" or step.get("model_name") != HARBOR_MODEL_ID:
            raise ValueError("ATIF agent step shape or model is invalid")
        if "timestamp" in step and (
            not isinstance(step["timestamp"], str)
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", step["timestamp"])
        ):
            raise ValueError("ATIF timestamp is invalid")
        step_metrics = step.get("metrics")
        if step_metrics is not None:
            if not isinstance(step_metrics, dict) or set(step_metrics) - {
                "prompt_tokens",
                "completion_tokens",
                "cached_tokens",
                "cost_usd",
                "extra",
            }:
                raise ValueError("ATIF step metrics shape is invalid")
            _validated_metric_dict(step_metrics)
            for key, value in step_metrics.items():
                if key == "extra":
                    aggregate_metrics["reasoning_tokens"] += value["reasoning_tokens"]
                    saw_reasoning_telemetry = True
                    continue
                aggregate_metrics[key] += value
                seen_metrics.add(key)
            if step.get("llm_call_count") != 1:
                raise ValueError("ATIF metric step must represent exactly one model call")
        elif "llm_call_count" in step:
            raise ValueError("ATIF llm_call_count requires metrics")
        calls = step.get("tool_calls")
        if calls is not None:
            if not isinstance(calls, list) or len(calls) != 1:
                raise ValueError("ATIF tool step must contain one sanitized call")
            call = calls[0]
            if not isinstance(call, dict) or set(call) != {"tool_call_id", "function_name", "arguments"}:
                raise ValueError("ATIF tool call shape is invalid")
            if not isinstance(call.get("tool_call_id"), str) or not re.fullmatch(r"tool-\d{6}", call["tool_call_id"]):
                raise ValueError("ATIF sanitized tool call id is invalid")
            if call.get("function_name") not in SAFE_TOOL_NAMES or call.get("arguments") != {}:
                raise ValueError("ATIF contains a non-allowlisted tool name or raw arguments")
        elif any(key in step for key in ("observation", "extra")):
            raise ValueError("ATIF observation metadata requires a tool call")
        if step_metrics is None and calls is None:
            raise ValueError("ATIF agent step has no sanitized structural event")
        observation = step.get("observation")
        if calls is not None:
            if not isinstance(observation, dict) or set(observation) != {"results"}:
                raise ValueError("ATIF sanitized observation shape is invalid")
            results = observation["results"]
            if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict):
                raise ValueError("ATIF sanitized observation result is invalid")
            result = results[0]
            if set(result) != {"source_call_id", "content"} or result.get("source_call_id") != calls[0]["tool_call_id"]:
                raise ValueError("ATIF observation reference is invalid")
            if result.get("content") not in SAFE_OBSERVATIONS:
                raise ValueError("ATIF contains raw tool output")
            expected_status = result["content"].split(":", 1)[1]
            if step.get("extra") != {"status": expected_status}:
                raise ValueError("ATIF tool status metadata is inconsistent")
    metrics = raw.get("final_metrics")
    expected_metric_keys = {"total_steps"} | {
        ("total_cost_usd" if name == "cost_usd" else f"total_{name}")
        for name in seen_metrics
    }
    if saw_reasoning_telemetry:
        expected_metric_keys.add("extra")
    if not isinstance(metrics, dict) or set(metrics) != expected_metric_keys:
        raise ValueError("ATIF final_metrics are missing")
    if metrics.get("total_steps") != len(steps):
        raise ValueError("ATIF total_steps is inconsistent")
    usage = {
        "prompt_tokens": _metric_int(metrics.get("total_prompt_tokens", 0), "prompt tokens"),
        "completion_tokens": _metric_int(metrics.get("total_completion_tokens", 0), "completion tokens"),
        "cached_tokens": _metric_int(metrics.get("total_cached_tokens", 0), "cached tokens"),
        "reasoning_tokens": 0,
        "cost_usd": _metric_cost(metrics.get("total_cost_usd", 0.0)),
    }
    if saw_reasoning_telemetry:
        final_extra = metrics.get("extra")
        if not isinstance(final_extra, dict) or set(final_extra) != {"total_reasoning_tokens"}:
            raise ValueError("ATIF final reasoning telemetry is invalid")
        usage["reasoning_tokens"] = _metric_int(
            final_extra["total_reasoning_tokens"],
            "reasoning tokens",
        )
    if usage["cached_tokens"] > usage["prompt_tokens"]:
        raise ValueError("ATIF cached tokens exceed prompt tokens")
    expected_usage = {
        "prompt_tokens": int(aggregate_metrics["prompt_tokens"]),
        "completion_tokens": int(aggregate_metrics["completion_tokens"]),
        "cached_tokens": int(aggregate_metrics["cached_tokens"]),
        "reasoning_tokens": int(aggregate_metrics["reasoning_tokens"]),
        "cost_usd": float(aggregate_metrics["cost_usd"]),
    }
    if usage != expected_usage:
        raise ValueError("ATIF final usage does not match sanitized model-call metrics")
    return usage


def _validated_metric_dict(metrics: dict[str, Any]) -> None:
    if "prompt_tokens" in metrics:
        _metric_int(metrics["prompt_tokens"], "prompt tokens")
    if "completion_tokens" in metrics:
        _metric_int(metrics["completion_tokens"], "completion tokens")
    if "cached_tokens" in metrics:
        _metric_int(metrics["cached_tokens"], "cached tokens")
    if "cost_usd" in metrics:
        _metric_cost(metrics["cost_usd"])
    if "extra" in metrics:
        if set(metrics) != {
            "prompt_tokens",
            "completion_tokens",
            "cached_tokens",
            "cost_usd",
            "extra",
        }:
            raise ValueError("ATIF MiMo step usage is incomplete")
        extra = metrics["extra"]
        if not isinstance(extra, dict) or set(extra) != {"reasoning_tokens"}:
            raise ValueError("ATIF step reasoning telemetry is invalid")
        _metric_int(extra["reasoning_tokens"], "reasoning tokens")
    if metrics.get("cached_tokens", 0) > metrics.get("prompt_tokens", 0):
        raise ValueError("ATIF step cached tokens exceed prompt tokens")


def _metric_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 10**12:
        raise ValueError(f"ATIF {label} are invalid")
    return value


def _metric_cost(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("ATIF cost is invalid")
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 10**9:
        raise ValueError("ATIF cost is invalid")
    return number


def _contains_secret(value: Any) -> bool:
    if isinstance(value, str):
        return any(pattern.search(value) for pattern in SECRET_PATTERNS)
    if isinstance(value, list):
        return any(_contains_secret(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_secret(key) or _contains_secret(item) for key, item in value.items())
    return False


def _scrub_local_file(path: Path) -> None:
    try:
        size = path.stat().st_size
        with path.open("r+b", buffering=0) as handle:
            remaining = size
            block = b"\x00" * min(65_536, max(1, size))
            while remaining:
                amount = min(remaining, len(block))
                handle.write(block[:amount])
                remaining -= amount
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        pass
    path.unlink(missing_ok=True)
