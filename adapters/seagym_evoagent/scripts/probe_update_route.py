#!/usr/bin/env python3
"""Make one exact, privacy-bounded probe of the frozen update transport."""

from __future__ import annotations

import argparse
from pathlib import Path

from seagym_evoagent.canonical import atomic_write_json, sha256_json
from seagym_evoagent.models import HarnessComponents, default_a0
from seagym_evoagent.openrouter import OpenRouterStructuredClient, safe_probe_failure_code
from seagym_evoagent.routing import expected_route_contract


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        completion = OpenRouterStructuredClient(timeout_seconds=180).complete(
            evidence={
                "num_trajectories": 0,
                "success_count": 0,
                "failure_count": 0,
                "purpose": "authenticated_transport_preflight_only",
            },
            current_components=default_a0().components.to_dict(),
            seed=42,
        )
        components = HarnessComponents.from_dict(completion.candidate)
    except Exception as exc:
        failure_code = safe_probe_failure_code(exc)
        atomic_write_json(
            args.output,
            {
                "schema_version": "evoagent-seagym-update-route-probe-v1",
                "status": "failed",
                "failure_code": failure_code,
                "requested_model": "xiaomi/mimo-v2.5",
                "candidate_persisted": False,
                "raw_response_persisted": False,
                "reasoning_persisted": False,
            },
        )
        raise SystemExit(f"exact update-route probe failed closed: {failure_code}") from None

    atomic_write_json(
        args.output,
        {
            "schema_version": "evoagent-seagym-update-route-probe-v1",
            "status": "verified",
            "transport": "required_single_tool",
            "requested_model": "xiaomi/mimo-v2.5",
            "served_model": completion.served_model_id,
            "provider": completion.provider,
            "route_contract_sha256": sha256_json(expected_route_contract()),
            "candidate_sha256": sha256_json(components.to_dict()),
            "request_sha256": completion.request_sha256,
            "response_sha256": completion.response_sha256,
            "usage": completion.usage.to_dict(),
            "candidate_persisted": False,
            "raw_response_persisted": False,
            "reasoning_persisted": False,
        },
    )
    print("Exact Xiaomi required-Tool update route verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
