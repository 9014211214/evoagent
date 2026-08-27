# Minimal scientific validation

This protocol is a low-cost, controlled mechanism test for EvoAgent. It is not
an authoritative external benchmark and it must not be presented as a public
leaderboard result.

## Frozen design

- One model endpoint and provider per frozen lock. The current execution
  candidate is `qwen/qwen3.8-flash`, Alibaba only, with provider fallbacks
  disabled, required-parameter routing enabled, and reasoning explicitly
  disabled. The earlier MiMo lock remains reproducible but is not silently
  substituted for this run.
- One seed: label `A`, numeric seed `43`.
- Five immutable Agent snapshots: A0, then one-component changes to Skill,
  Memory, Router and numeric Policy (A1 through A4).
- Twelve public synthetic held-out Tasks: three each for retention, transfer,
  adversarial safety and multi-component composition.
- Every snapshot is evaluated on every Task: 5 × 12 = 60 external episodes.
- The model, Task hashes, Environment, verifier, seed and per-episode limits are
  identical across snapshots. Evaluation never changes a snapshot.

The Qwen execution lock at
`configs/full_agent/minimal-scientific-seed-A-qwen3.8-flash.lock.json` binds
the generated plan hash, manifest hash, all 12 Task hashes, all five snapshot
hashes, the exact reasoning-disabled model-preset hash and every budget limit.
The earlier MiMo protocol remains frozen at
`configs/full_agent/minimal-scientific-seed-A.lock.json`. A different source
tree, model, provider or reasoning setting cannot run under either lock.

## What the four groups test

| Group | Frozen cases | Intended causal signal |
| --- | ---: | --- |
| Retention | 3 | The Skill change adds post-write verification and later changes do not regress it. |
| Transfer | 3 | Verified Memory handles two unseen contexts; the Router change handles a disjoint route shift. |
| Adversarial | 3 | The learned numeric Policy inspects protected targets instead of attempting writes. |
| Composition | 3 | Skill, Router and Policy observations operate in one Tool-Agent episode. |

The zero-cost fixture has the frozen A0→A4 score sequence
`0, 0.5, 2/3, 0.75, 1.0`. A real run does not copy those numbers: it derives
each binary Task result from the external Tool call, local Environment and
verifier. Any model mismatch, provider fallback, malformed Tool call, missing
usage accounting, budget violation or source/lock mismatch fails closed.

The first full MiMo attempt stopped on the first external episode with
`model_tool_call_noncompliance`; it produced no scientific score and is not
treated as negative performance evidence. Qwen3.8 Flash is a separately frozen
retry because its endpoint currently supports `tools`, `tool_choice` and an
explicit no-reasoning setting.

## Success gate and claim boundary

A seed passes only if the external run reproduces the complete frozen causal
sequence, reaches all 12 final passes, introduces no task regression, preserves
all first-passing retention Tasks, and ends with zero safety violations.

Passing supports a narrow claim: the pinned external model can execute the
controlled A0→A4 EvoAgent mechanism and the four one-component changes produce
the expected verifiable gains on this synthetic set. It does not establish
broad task generalization, superiority to another Agent, statistical
significance across seeds, or an official benchmark ranking.

## Hard budget and privacy

- At most 180 OpenRouter requests (three per episode).
- At most 4,096 prompt bytes and 128 output tokens per request.
- Model hard cap: USD 0.60. The pricing-derived mathematical ceiling at the
  Qwen preset is USD 0.1287936 and remains below this cap.
- Private-runner timeout: 90 minutes, budgeted at USD 0.54.
- Reserve: USD 0.06. Total authorization cannot exceed USD 1.20.

The shared ledger reserves a request before network I/O and aggregates usage
across all 60 episodes. Evidence contains hashes, derived results and usage
only; API credentials, raw prompts, raw responses and full trajectories are
never persisted.

The public workflow performs only credential-free tests and deterministic plan
generation. A real seed must run from a one-use private workflow pinned to the
exact reviewed public commit, after separate action-time authorization, and
the temporary remote branch must be deleted after the run.

Exact-head hosted run `33024979960` regenerated and verified both model locks.
Its Qwen no-reasoning artifact is `9628057696`, with GitHub artifact digest
`sha256:942924093fe1f680d92f816d3e430d27891db90c1087c3d66c439bf884a3da22`.
This remains zero-cost contract evidence; it is not the external seed result.
