# EvoAgent SEAGym adapter

This is an optional integration package. It is intentionally outside the
EvoAgent core dependency graph and pins the exact SEAGym source revision and
the separate Harbor revision required by that SEAGym runtime in its optional
dependency groups. This adapter-specific Harbor runtime pin is intentionally
distinct from the older CLI-only integration reviewed in the root third-party
lock; it does not silently replace that root integration boundary.

The adapter has two public entry points:

- `seagym_evoagent.baseline:EvoAgentSEAGymBaseline` implements the SEAGym
  `BaseBaseline` lifecycle. It learns an **evaluation-only** four-component
  harness snapshot from train batches. A failed or invalid update never changes
  the evaluation candidate.
- `seagym_evoagent.harbor_agent:EvoAgentMiMo` is a Harbor custom agent. It
  requires the host workflow to prefetch the official MiMoCode 0.1.13 Linux
  x64 archive, verifies its pinned SHA-256 before uploading it into each
  transient task container, projects the four components into the task input,
  and emits a privacy-preserving ATIF file and attestation. The host archive
  path is supplied through `EVOAGENT_MIMOCODE_ARCHIVE_PATH`; task containers do
  not download the executable themselves.

The OpenRouter update model is locked to `xiaomi/mimo-v2.5`; the Harbor CLI
model string is locked to `openrouter/xiaomi/mimo-v2.5`. The host-side update
call reads the account key only from `OPENROUTER_API_KEY`. Rollout containers
receive only a run-scoped local-proxy capability and call the route-locked host
guard proxy; the account key never enters a task container. Neither credential
is placed in persisted state, reports, exceptions, or hash inputs.

The frozen route sends `provider.only=["xiaomi/fp8"]`, disables fallbacks,
requires route parameters, and sends `reasoning.enabled=false`. Responses from
the update-model call are accepted only when OpenRouter reports the alias
`xiaomi/mimo-v2.5` or canonical ID `xiaomi/mimo-v2.5-20260422` and provider
`Xiaomi`. Empty reasoning values (`null`, empty string/list/object) are allowed;
non-empty reasoning fails closed. MiMoCode's `agent.build.steps` is bound to the
validated snapshot policy (`max_iterations`, 1--32) so a task has a real
agentic-step limit in addition to the wall-clock timeout.

## Verifier-facing files

- Snapshot: `baseline_state/snapshots/<snapshot_sha256>.json`, schema
  `evoagent-seagym-harness-v1`.
- Checkpoint manifest: `checkpoint.json`, schema
  `evoagent-seagym-checkpoint-v1`; its inventory covers the copied
  `baseline_state/` directory.
- Per-trial sanitized trajectory: given a SEAGym task row `result_path`, use
  `<parent(result_path)>/agent/trajectory.json` (ATIF-v1.7).
- Per-trial attestation: the same agent directory contains
  `evoagent-attestation.json`, schema `evoagent-harbor-attestation-v1`. It binds
  the ATIF hash, snapshot and four component hashes, route-contract hash,
  model/seed, MiMoCode archive hash, and SEAGym/Harbor runtime commits.

## Privacy and claim boundary

Only structural, observable train metrics are sent to the update model:
success/failure counts, score/reward aggregates, runtime/cost aggregates, and
bounded tool-name/status counts from privacy-preserving ATIF. Task IDs,
instructions, model text, reasoning, tool arguments, tool output, canaries,
secrets, and raw trajectories are neither sent nor persisted.

The generated snapshot is not an activation or promotion. The adapter records
`causal_attribution_claimed=false` and `promotion_claimed=false`; a later frozen
evaluation and governance decision must establish any improvement claim.

## Offline tests

From this directory:

```text
python -m pytest -q
```

The tests use mock model clients and mock Harbor environments. They do not call
OpenRouter, download MiMoCode, execute a benchmark, or require secrets.
