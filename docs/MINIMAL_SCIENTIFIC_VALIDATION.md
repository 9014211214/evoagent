# Minimal scientific validation

This protocol is a low-cost, controlled mechanism test for EvoAgent. It is not
an authoritative external benchmark and it must not be presented as a public
leaderboard result.

## Frozen design

- One model endpoint and provider per frozen lock. The current execution
  candidate is `xiaomi/mimo-v2.5`, Xiaomi only, with provider fallbacks
  disabled, exact endpoint slug `xiaomi/fp8` pinned after zero-cost preflight,
  and verified `tool_choice="required"` with reasoning disabled. Every request
  exposes exactly one Tool schema, so `required` can select only the
  controller's frozen action;
  the response must still match its exact Tool name and arguments. Historical
  MiMo named-function and Qwen locks remain reproducible and are not silently
  substituted for this run.
- One seed: label `A`, numeric seed `43`.
- Five immutable Agent snapshots: A0, then one-component changes to Skill,
  Memory, Router and numeric Policy (A1 through A4).
- Twelve public synthetic held-out Tasks: three each for retention, transfer,
  adversarial safety and multi-component composition.
- Every snapshot is evaluated on every Task: 5 × 12 = 60 external episodes.
- The model, Task hashes, Environment, verifier, seed and per-episode limits are
  identical across snapshots. Evaluation never changes a snapshot.

The capability-aware MiMo execution lock at
`configs/full_agent/minimal-scientific-seed-A-mimo-v2.5-required.lock.json`
binds the generated plan hash, manifest hash, all 12 Task hashes, all five
snapshot hashes, the exact required-single-Tool model-preset hash and every
budget limit. The historical MiMo and Qwen protocols remain frozen at
`configs/full_agent/minimal-scientific-seed-A.lock.json` and
`configs/full_agent/minimal-scientific-seed-A-qwen3.8-flash.lock.json`.
A different model, provider or Tool-choice mode changes the plan and cannot run
under the new lock. Source identity is not established by the lock alone; an
external run additionally requires exact-head authorization bound outside this
public helper.

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
treated as negative performance evidence. A separately controlled Qwen3.8
Flash route probe then returned HTTP 404 before any provider attempt because
no route accepted the stricter named-function request; this is consistent with,
but does not by itself prove, the endpoint capability-table mismatch. It used
zero Tokens, cost USD 0, and also produced no scientific score. The new MiMo
preset changed the compatibility field to `required` and now explicitly
disables reasoning. It never falls back to `auto`, another provider or another
model.

The public endpoint catalogue exposes generic `tool_choice` support but not a
machine-verifiable guarantee for every Tool-choice subtype. Chat-completion
responses identify the model and provider, not the endpoint tag. Private
workflow run `33179418764` therefore executed one separately authorized probe
from exact source head `920e972b268b13f5f5cb6e333fefa1058b1edaac`.
The fresh metadata and regular-key preflights passed, and the only inference
request returned HTTP 200 from Xiaomi for `xiaomi/mimo-v2.5`, with canonical
response metadata `xiaomi/mimo-v2.5-20260422`, 277 prompt Tokens, 32 completion
Tokens and complete observed cost USD 0.00004774. It did not return the required
frozen Tool call. The verifier recorded `required_tool_call_verified=false`,
`status=blocked` and `successful_response_failed_closed_verification`.

Sanitized artifact `9689044579` has GitHub digest
`sha256:a38fb4dd9e818ab8cfc26883fa151654ea79d200bb8e0e4586788cdaa1a0bb1b`;
the evidence JSON has SHA-256
`7dd79cab6df62ffa38209ee237100fd424651f0dac48025e8650e9150374664e`.
It persists no credential, raw prompt, raw response or raw router summary.
That response hit its 32-Token completion cap and did not contain the required
call, so it was insufficient to decide whether the exact route could comply
under a non-truncated contract. It is not a benchmark score or Task failure.

A corrected, independently authorized probe disabled reasoning, allowed up to
256 output Tokens for the single probe request, and required a complete
`finish_reason=tool_calls` response with one typed call ID, exact function and
arguments, no prose, and no reasoning fields. Private workflow run
`33183563382` at exact source head
`252344023c75152bf67be33aa0c9d51fa997f094` passed that contract. It used 279
prompt plus 22 completion Tokens and USD 0.00004522. Sanitized artifact
`9690717555` has GitHub digest
`sha256:1a219cb3f8fd035f25ee09ccb4f0852d7e5ed703aa4591633fc75c36a7fffabd`;
the evidence JSON has SHA-256
`b8957ad45199457a0501cf4d92ec753ba14bcd4765c7253bac56ef1600e4946f`.
It persists no credential, raw prompt, raw response or raw router summary.
This makes the frozen seed transport-eligible, but supplies no scientific score
and does not itself support an EvoAgent-effectiveness claim.

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
  capability-aware MiMo preset is USD 0.1096704 and remains below this cap.
- Private-runner timeout: 90 minutes, budgeted at USD 0.54.
- Reserve: USD 0.06. Total authorization cannot exceed USD 1.20.

The shared ledger reserves a request before network I/O and aggregates usage
across all 60 episodes. Evidence contains hashes, derived results and usage
only; API credentials, raw prompts, raw responses and full trajectories are
never persisted.

The offline result importer accepts only one caller-hashed regular JSON file
inside a controlled root. It rejects raw prompt/response/trajectory/reasoning
fields and verifies the exact public source commit, plan, lock, preset, five
snapshot reports, all 60 Task IDs and hashes, report chain, derived metrics,
usage and cost ceilings before emitting a compact self-hashed receipt.

The public workflow performs only credential-free tests and deterministic plan
generation. A real seed must run from a one-use private workflow pinned to the
exact reviewed public commit, after separate action-time authorization, and
the temporary remote branch must be deleted after the run.

Hosted implementation run `33024979960` regenerated and verified both model locks.
Its Qwen no-reasoning artifact is `9628057696`, with GitHub artifact digest
`sha256:942924093fe1f680d92f816d3e430d27891db90c1087c3d66c439bf884a3da22`.
This remains zero-cost contract evidence; it is not the external seed result.
Exact public-head dry-run `33176159018` then passed the required-single-Tool,
historical MiMo and Qwen profiles. Its required-single-Tool artifact is
`9687672829`, with GitHub digest
`sha256:2a5fa40928947755aaf1d38c8fd46bc22c87ecdc119e10f5bf049bd60ce64126`.
This is also zero-cost contract evidence, not a Task result or score.
The newly versioned MiMo lock's public dry-run and expiring exact-head,
transactional one-use authorization gates were exercised. The corrected
one-request route probe passed the required-Tool response contract as recorded
above, so the 60-episode external seed is transport-eligible but not yet
executed. The public execution helper now rejects direct paid execution; the
private controller must supply the fresh preflight, expiring exact-head
authorization and transactional one-use claim. A changed model, protocol or
repeat probe would require a new lock rationale and a new explicit
authorization.
