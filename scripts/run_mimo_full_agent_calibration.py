from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from evoagent.continual.builders import to_runtime_snapshot
from evoagent.continual.runtime import ContinualDocumentVerifier, UnifiedDocumentPolicy
from evoagent.integrations.full_agent_calibration import (
    build_calibration_snapshot,
    build_calibration_task,
)
from evoagent.integrations.openrouter import (
    OpenRouterControlledToolPolicy,
    OpenRouterModelPreset,
)
from evoagent.model_registry.models import canonical_sha256
from evoagent.runtime import LocalDocumentEnvironment, RuntimeLimits, ToolAgentRuntime


def _authorization(args: argparse.Namespace) -> dict[str, object]:
    approvers = tuple(args.approver_id)
    if len(approvers) != 2 or len(set(approvers)) != 2:
        raise PermissionError("Real calibration requires exactly two distinct approvers.")
    if args.requester_id in approvers:
        raise PermissionError("Calibration requester cannot self-approve.")
    if not args.authorization_anchor.startswith("github-actions://"):
        raise PermissionError("Calibration authorization must be externally anchored.")
    return {
        "authorization_anchor_hash": canonical_sha256(args.authorization_anchor),
        "requester_id": args.requester_id,
        "approver_ids": approvers,
    }


def _stable_trace_hash(trace) -> str:
    payload = trace.model_dump(mode="json")
    payload["cost"] = {
        key: value for key, value in payload["cost"].items() if key != "wall_seconds"
    }
    return canonical_sha256(payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--preset", type=Path, required=True)
    parser.add_argument("--requester-id", required=True)
    parser.add_argument("--approver-id", action="append", default=[])
    parser.add_argument("--authorization-anchor", required=True)
    parser.add_argument("--max-cost-usd", type=float, default=2.0)
    args = parser.parse_args()

    authorization = _authorization(args)
    preset = OpenRouterModelPreset.model_validate_json(
        args.preset.read_text(encoding="utf-8")
    )
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is unavailable to calibration.")

    snapshot = build_calibration_snapshot(args.workspace, model_id=preset.model_id)
    task = build_calibration_task()
    controller = UnifiedDocumentPolicy(snapshot)
    policy = OpenRouterControlledToolPolicy(
        controller=controller,
        preset=preset,
        api_key=api_key,
        max_requests=3,
        max_output_tokens=256,
        max_prompt_bytes_per_request=32_768,
        max_cost_usd=args.max_cost_usd,
    )
    runtime = ToolAgentRuntime(
        environment_factory=lambda: LocalDocumentEnvironment(
            args.workspace / "calibration-environment"
        ),
        policy=policy,
        verifier=ContinualDocumentVerifier(),
        limits=RuntimeLimits(max_steps=6, max_tool_calls=4, max_wall_seconds=360.0),
        seed=43,
    )
    trace = runtime.run(task, to_runtime_snapshot(snapshot))
    first_metadata = next(
        (
            event["metadata"]
            for event in trace.observable_events
            if event.get("event") == "policy_observation"
            and event.get("step_index") == 0
        ),
        {},
    )
    tool_sequence = tuple(
        event["result"]["tool_name"]
        for event in trace.observable_events
        if event.get("event") == "tool_result"
    )
    components = {
        component.value: digest for component, digest in snapshot.component_hashes.items()
    }
    payload = {
        "format_version": "evoagent-mimo-full-agent-calibration-v1",
        "claim_scope": "integration_calibration_only_not_benchmark_evidence",
        "status": "passed" if trace.verifier_passed else "failed",
        "model_id": preset.model_id,
        "canonical_model_id": preset.canonical_model_id,
        "provider": preset.provider_name,
        "provider_fallbacks": False,
        "snapshot_hash": snapshot.snapshot_hash,
        "component_hashes": components,
        "runtime_hash": snapshot.runtime_hash,
        "tool_contract_hash": snapshot.tool_contract_hash,
        "verifier_hash": snapshot.verifier_hash,
        "task_hash": canonical_sha256(task.model_dump(mode="json")),
        "seed": 43,
        "controller_binding": {
            "selected_skill_ids": first_metadata.get("selected_skill_ids", ()),
            "router_source": first_metadata.get("router_source"),
            "memory_record_ids": first_metadata.get("memory_record_ids", ()),
            "policy_state": first_metadata.get("policy_state"),
            "initial_policy_action": first_metadata.get("initial_policy_action"),
        },
        "verifier_passed": trace.verifier_passed,
        "tool_call_count": len(tool_sequence),
        "tool_sequence_hash": canonical_sha256(tool_sequence),
        "observable_trace_hash": _stable_trace_hash(trace),
        "usage": policy.usage.model_dump(mode="json"),
        "approved_cost_cap_usd": args.max_cost_usd,
        "mathematical_cost_ceiling_usd": policy.mathematical_cost_ceiling_usd,
        **authorization,
        "raw_prompts_persisted": False,
        "raw_responses_persisted": False,
        "credentials_persisted": False,
        "external_execution_performed": True,
        "benchmark_score_claimed": False,
        "official_submission_performed": False,
        "official_leaderboard_claimed": False,
    }
    payload["evidence_hash"] = canonical_sha256(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        "MiMo Full-Agent integration calibration "
        f"{payload['status']}; cost_usd={policy.usage.cost_usd:.8f}"
    )
    return 0 if trace.verifier_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
