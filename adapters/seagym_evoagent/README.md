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
  the evaluation candidate. After a candidate transition is fully persisted,
  it also refreshes the same long-lived `BaselineState` object SEAGym uses for
  later rollout Agent specifications; stale live state is rejected fail-closed.
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

The generated MiMoCode config disables the default title Agent, next-prompt
prediction, automatic dream/distill, checkpoint writer, cron scheduling, MCP
sampling, actor sub-sessions, orchestration, and workflow/exec surfaces. The
build Agent receives only `bash`, `read`, `write`, `edit`, `glob`, and `grep`.
The CLI also supplies the task-independent fixed title
`evoagent-seagym-trial`. These controls prevent model calls that can pass
through the proxy without appearing as root-session CLI `step_finish` events.

Automatic compaction remains enabled because its calls stay in the root event
stream and are therefore ATIF-accountable. Each trial binds `HOME`,
`USERPROFILE`, and `MIMOCODE_HOME` to the disposable runtime directory and
runs MiMoCode in pure mode. It also sets `MIMOCODE_CONFIG_CONTENT={}` so a
host-inherited inline config cannot be merged after the locked config file. The
controller guard proxy is required to enforce the root `x-session-affinity`
value, reject `x-parent-session-id`, and bind the same value to the request's
`prompt_cache_key`. Route/lifecycle checks permit one root session; the full
pilot permits and requires exactly 24. Only task-scoped root-session calls are
authorized; proxy logical-request counts must still equal the ATIF model-call
count.

The MiMoCode deadline reserves 120 seconds inside Harbor's outer Agent timeout.
Its bounded 15-second forced-kill grace is part of that reserve, leaving at
least 105 seconds at the command layer for the sanitizer to write valid ATIF or
emit a content-free, identity-bound failure receipt.

The Xiaomi endpoint does not advertise the API `seed` parameter, so the
host-side update request omits it instead of weakening `require_parameters`.
Seed 42 still binds the Task split/order and host-side attempt, checkpoint, and
trial evidence. Neither update nor rollout provider sampling is claimed to be
bit-for-bit deterministic.

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
- Classified runtime failure: the same directory contains
  `evoagent-runtime-failure.json`, schema `evoagent-runtime-failure-v1`. This
  content-free, self-hashed receipt binds a bounded failure class/stage, the
  actual ATIF-presence bit, snapshot/components, route, model, seed, and runtime.

## Privacy and claim boundary

Only structural, observable train metrics are sent to the update model:
success/failure counts, score/reward aggregates, runtime/cost aggregates, and
bounded tool-name/status counts from privacy-preserving ATIF. Task IDs,
instructions, model text, reasoning, tool arguments, tool output, canaries,
secrets, and raw trajectories are neither sent nor persisted.

Before any update-model call, every ATIF-v1.7 document must bind the exact
current snapshot, all four component hashes, route contract, seed, API model,
and MiMoCode runtime identity in both its root and Agent `extra` blocks. The two
blocks must be identical. A stale or missing identity is rejected rather than
being treated as evidence for the current candidate.

Pinned Harbor represents one task in two forms. The child `result.json`
`task_name` must equal the frozen canonical task ID exactly (for example,
`terminal-bench/fix-git`), while its `LocalTaskId` path, one-task dataset filter,
and patched task directory must use the derived leaf (`fix-git`). These forms
are checked separately. A namespace alias, wrong leaf, cross-task dataset, or
substituted directory is rejected before the update model can be called.

Pinned Harbor also serializes a local `TaskConfig` with exactly eight fields:
`path`, `git_url`, `git_commit_id`, `name`, `ref`, `overwrite`, `download_dir`,
and `source`. The adapter requires `path` to equal the serialized `LocalTaskId`,
`source` to equal the child result source and unique job-directory name,
`overwrite` to be false, and every remote/package field to be null. Missing or
extra fields and any drift are rejected before the update model can be called.
The unattested-error path and final pilot verifier apply the same rule.

Pinned Harbor writes job-aggregate and exception times as local wall-clock
timestamps without offsets. Protocol v14 accepts those native values without
inventing a timezone. Aggregate start, update, and finish must use one
consistent naive/aware basis and stay in order. Trial, phase, ATIF, and runtime
receipt timestamps keep their existing timezone requirements; malformed values
and mixed aggregate bases fail before learning.

An errored Harbor trial may lack ATIF only when its own contained, regular
failure receipt is present, self-hashed, identity-bound, and declares
`atif_present=false`. It contributes a real zero to the experiment and bounded
failure-class counts, but never receives a fabricated ATIF digest, step, Tool
summary, or model usage. If every trajectory in a train batch meets that narrow
condition, the baseline persists an immutable `no_usable_harbor_atif_evidence`
skip, advances the update index with the candidate unchanged, and records
`model_call_executed=false`. This is not a successful learning update.

Pinned Harbor can also produce a real errored child `result.json` without a
MiMo/sanitizer receipt; the missing receipt alone does not identify which stage
failed. Whether or not a separately valid ATIF exists, the entire train batch
is ineligible for learning: the adapter persists
`incomplete_unattested_harbor_evidence`, makes no update-model call, advances
the frozen update slot, and keeps the candidate unchanged. Valid ATIF elsewhere
in that batch is not used for a partial update. No receipt is synthesized.

A non-errored trial without ATIF, an explicitly declared but missing receipt,
a malformed/tampered receipt, a
receipt whose ATIF bit disagrees with disk, malformed ATIF already on disk,
path escape, symlink, or junction fails before the update-model call. When a
classified MiMoCode failure still produced valid sanitized ATIF, the real ATIF
remains usable and the accompanying `atif_present=true` receipt is independently
validated. The pilot verifier requires every errored row's original score to
already be zero, keeps it in the denominator, and labels any receipt-bearing result
`completed_with_incomplete_training_evidence`. An unattested Harbor errored
result is never assigned an unproved failure stage or treated as usable
learning evidence; it additionally makes the pilot classification
inconclusive even when raw A0/AT values can be recomputed.

Any explicit `atif_path` or `trajectory_path` is mandatory and must resolve to
the unique ATIF under that same Harbor result's Agent directory. Missing,
cross-trial, linked, conflicting, or duplicate evidence is rejected before an
update-model call; a derived ATIF cannot silently replace a bad declaration.

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
