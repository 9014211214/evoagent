# SEAGym + Terminal-Bench 2.0 pilot

## Status and claim boundary

This is a pre-registered, real external pilot for the EvoAgent learning loop.
It is not a synthetic wiring test, a full SEAGym paper reproduction, a
Terminal-Bench leaderboard submission, or evidence that any upstream project
endorses EvoAgent. No complete result exists at the protocol-v15 freeze time.

The pilot asks whether an EvoAgent-managed MiMoCode Agent can improve on three
held-out Terminal-Bench 2.0 tasks after two bounded updates, while avoiding a
drop on a frozen three-task validation set. It does not authorize automatic
promotion and does not support a causal claim. With only three final tasks, a
positive result is pilot-level evidence that justifies a larger replication;
it is not a statistically powered benchmark claim.

## Frozen upstream and runtime identity

| Component | Exact identity | Role |
| --- | --- | --- |
| SEAGym | 9e61e14db1f1355de944cd7c5b10c244fc74e82d | outer update/validation/replay/final lifecycle |
| Harbor SEAGym gitlink | f7110f1a240c6a50589b90c4d69714763946d088 | Docker task execution and verifier boundary |
| Terminal-Bench 2.0 | 2fd12b88aafdd04a52c298e3940bcb189f9766d6 | task environments and verifiers |
| MiMoCode | v0.1.13, commit 67c9cf1e26288d03c65fb844be71f39581ffc1de | terminal Agent runtime |
| MiMoCode Linux x64 asset | SHA-256 0997a43647a99969d0194fad71af1fd6112aa8220e24a4562aea63953b1e1ada | transient executable |

The MiMoCode tag and asset digest were resolved from the official GitHub tag
and release API. The workflow must verify the digest before extraction.

The same model route is frozen for update and rollout:

- OpenRouter request model: xiaomi/mimo-v2.5
- accepted response model identities: xiaomi/mimo-v2.5 and
  xiaomi/mimo-v2.5-20260422
- Harbor/MiMoCode model string: openrouter/xiaomi/mimo-v2.5
- request provider allowlist: xiaomi/fp8 only
- required response provider identity: Xiaomi
- provider fallbacks: disabled
- required parameter support: enabled
- request setting `reasoning.enabled`: false; this does not claim that the
  provider performed no internal reasoning
- provider-reported reasoning-token counts: accepted only as bounded numeric
  usage telemetry; reasoning text, events, details, and content remain forbidden
  and are never persisted
- response cache: disabled
- router metadata: required; one alias/direct Xiaomi attempt and no material
  plugin, guardrail, compression, healing, or server-tool pipeline stage
- host credential source: OPENROUTER_API_KEY environment variable only

The host starts a route-locked guard proxy. Task containers receive only a
short-lived capability for that local proxy; the real OpenRouter account key
is not injected into task containers. The credential value is never part of
the config, protocol, hashes, command evidence, or committed artifacts.

The workflow downloads and SHA-256-verifies the MiMoCode release archive once
on the host. Harbor uploads that verified local archive into each task
container before extraction, so tasks do not depend on outbound GitHub access
or a preinstalled `curl`. A missing `python3` runtime is installed from the
task's Debian/Ubuntu package source before the sanitized converter runs.

## Score-blind task selection

All 12 tasks are Terminal-Bench 2.0 tasks and retain their membership in
SEAGym's official paper-reproduction train, validation, and test pools. The
selection was frozen using only:

1. official split membership;
2. declared CPU, memory, disk, and timeout requirements; and
3. domain coverage and pre-registered transfer clusters.

No task score or solution was consulted. The committed task index contains
only task identifiers, public attributes, scoring metadata, and data://
references. It does not copy instructions, images, Docker environments,
solutions, verifiers, or run artifacts.

| Split | Frozen order | Role |
| --- | --- | --- |
| train | fix-git; log-summary-date-ranges; vulnerable-secret; pytorch-model-recovery; polyglot-c-py; configure-git-webserver | two ordered batches of three |
| validation | cancel-async-tasks; openssl-selfsigned-cert; multi-source-data-merger | frozen initial/post-epoch check |
| test | regex-log; sanitize-git-repo; build-cython-ext | held-out final A_T versus A_0 |

The pre-registered transfer clusters are:

- Git and secret hygiene: fix-git, vulnerable-secret, and
  configure-git-webserver to held-out sanitize-git-repo.
- Log parsing: log-summary-date-ranges to held-out regex-log.
- Cross-language build: polyglot-c-py to held-out build-cython-ext.

These clusters are hypotheses based on names and public metadata, not claims
that transfer will occur. The PyTorch training task and the three validation
tasks broaden the observed domains without being assigned a held-out transfer
claim.

## Frozen schedule and trial accounting

The top-level seed and dataloader seed are both 42. Train shuffling is disabled,
so the manifest order is the execution order. There is one epoch, batch size
three, two train batches, one update per batch, and therefore two updates.
Validation uses one fixed three-task view. Replay uses one fixed three-task
view after each batch. Final evaluation loads both A_T and the saved A_0
checkpoint on the same three held-out tasks.

Seed 42 freezes the task split and batch order and binds every update-attempt,
checkpoint, and trial attestation. The Xiaomi endpoint does not advertise the
OpenRouter `seed` parameter, so the update client does not send it while
`require_parameters=true`; otherwise OpenRouter filters the only allowed
endpoint before inference. We claim bit-for-bit determinism for neither update
nor rollout sampling. The exact model/provider route is frozen, and every
observed output remains part of the evidence.

Protocol v15 separates upstream raw scoring from the pilot's existing rule
that errored trials count as zero. Controller run 121 (`33942199178`) passed
the exact route, official oracle, and real lifecycle gates, then completed
six of 24 singleton jobs. Five children recorded an exception. Before the
first update-model call, v14 rejected a child with both an exception and a
nonzero raw verifier reward. No update completed, and no final A0/A_T result,
comparison, delta, or project score was produced.

Pinned Harbor can retain a verifier reward of one alongside exception metadata;
pinned SEAGym consequently retains raw score one while setting success false.
This is a valid source shape, not permission to count an errored task as a
success. The v15 projector and independent final verifier validate the original
child, aggregate, normalized row, and source metrics without rewriting them.
Only the effective learning and scientific scores apply the error-to-zero rule.
Raw `mean_score` and score-derived upstream diagnostics are checked separately
from success-based metrics. Published gains and the pilot classification use
effective scores. Unknown usage is not imputed as zero, and unattested train
errors still skip the entire update batch rather than become learning evidence.

The same pre-merge review found a separate accounting defect: a classified
runtime failure with no ATIF used zero token/cost placeholders, which could
enter a mixed learning batch or final usage total as if measured. V15 preserves
those missing measurements as null and labels incomplete usage explicitly.
Known subtotals are separate from complete totals; source-reported SEAGym
accounting is independently reconciled but is not an exact total when any
trial's telemetry is unknown. OpenRouter's before/after usage remains a separate
whole-key observation, not per-trial attribution. A valid measured zero remains
zero. An ATIF file alone does not prove complete usage: at least one complete
usage event is required, every usage event must contain the four core fields,
and child context, ATIF attestation, normalized row, and aggregate must agree.
Missing fields or a stream with no usage events must not become a zero-use
attestation. This does not relax the separate, unsupported early-failure contract
where the entire Harbor agent or verifier result is absent.

The specific stage/type behind run 121's Harbor exceptions was not retained
and remains unknown. Accepting the valid reward/exception combination does not
repair or explain those underlying exceptions. The run's observed whole-key
usage delta was USD 0.016065648, below the stop threshold. Its artifact ZIP
SHA-256 is `82c8ba36c13e7ffa979aa33e0ff61f4e398f93fb3776b45ef37043ac09d66aac`;
all 15 manifest entries were independently verified. These are diagnostic
observations, not task-success scores or an exact per-request invoice.

The v15 ledger includes 21 controller attempts, zero complete comparisons, and
USD 1.420900401 cumulative measured whole-key deltas. Model/provider, tasks,
splits, order, seed, retries, update schedule, declared metrics, budget, and
timeouts remain frozen. No failed slot is replaced or silently retried.

Protocol v14 corrected the timestamp contract after an offline source review.
Pinned Harbor creates job-aggregate and exception timestamps with local
`datetime.now()`, without an offset, while trial and runtime evidence use
timezone-aware timestamps. The v13 update projector incorrectly required an
offset on every aggregate and exception. The v14 checks preserve those distinct
representations: aggregate times must share a consistent naive/aware basis and
remain ordered, and no timezone is inferred or attached. Trial, phase, ATIF,
and runtime-receipt timezone requirements remain in force.

Run `33938703704` (job `101231659264`) was cancelled during installation after
this source mismatch was found, before any model-call step, lifecycle check,
or pilot trial executed. Its controller was
`2967e437421e2e38e7a8357098f087993e44cfe6` and public source was
`8bc85f91d80496b2b02e66862eb1cec2513a62bc`. There is no before/after usage delta:
only the final read-only key-usage snapshot exists. Artifact `9961079285` has
ZIP SHA-256 `ee8f8b9471f9b11be0437fc934908c0b6e1d0d4d6a47c3f2d872c6068885e83a`;
the job log SHA-256 is
`aa612e2df86f92212eddcd6f7803181493784609cea3885908ed727cc904ecac`.
The ledger now contains 20 controller attempts, zero complete comparisons,
and USD 1.404834753 in previously observed deltas. An unavailable delta is not
recorded as a measured zero. The correction changes no experiment config,
model, task, split, seed, budget, or update schedule.

An early Harbor error with no AgentContext or VerifierResult is still outside
the admissible evidence contract and stops validation. The neutral incomplete
training classification described below applies only after child identity,
measurement, and verifier contracts validate. Missing usage or rewards are not
converted into measured zeros; supporting that early-null case requires a
separate explicit unmeasured-evidence contract.

The preceding protocol v13 was a final-comparison-blind evidence-schema correction. Nineteen
controller attempts reached progressively deeper parts of the pinned external
pipeline, but none completed the A_0/A_T comparison and none produced a score.
Their cumulative observed key-usage delta was USD 1.404834753 across separately
bounded observation windows. This is observed key-wide telemetry, not an exact
invoice attribution.

The preceding incomplete run `33928934542`, against controller commit
`eb8a7fd20b5a09521852096e700c9fefdaac9c8f` and public EvoAgent commit
`f2672b3399ef3b687984faf314a49c3f7410de94`, passed every source, route,
official Harbor oracle, and real lifecycle gate. It started the full pilot and
completed the first six of 24 unique singleton Harbor jobs: the three initial
validation tasks and the first three-task training batch. Before update 1 could
call the learning model, the evidence projector stopped with `Harbor train
result has an invalid config binding`. It produced no A_0, A_T, comparison,
delta, full report, or reportable score, and completed zero updates.

Pinned Harbor's local `TaskConfig` serializer emits eight fields: `path`,
`git_url`, `git_commit_id`, `name`, `ref`, `overwrite`, `download_dir`, and
`source`. For the patched local dataset, `path` binds the serialized
`LocalTaskId`, `source` is the unique Harbor job name, `overwrite` is false, and
the five remote/package fields are null. The v12 projector's artificial test
fixture emitted only `path`, and its validator required that incomplete shape,
so it rejected every faithful pinned-Harbor result. Protocol v13 fixes the
fixture and requires the exact canonical eight-field shape in the normal
projection, unattested-error path, and final verifier. Missing or extra fields,
remote/package values, source or path drift, and overwrite still fail closed.

The guard proxy forwarded and completed 52 requests with zero rejection,
retry, upstream error, or final HTTP 4xx/5xx response across six root sessions.
The run observed a USD 0.336810424 key-wide usage delta; its budget guard stopped
because the child failed, not because the USD 0.90 stop line was reached. The
diagnostic log exposed zero successes and `mean_score=0.333333` for the first
training batch. This intermediate value is not an A_0/A_T comparison, was not
used to select the model, tasks, split, order, or amendment, and supports no
effect claim.

Artifact `9958092092` is bound by GitHub Actions artifact-ZIP SHA-256
`e4b74311c6ccbe75234328d3c2d8891975c8dd0947204f1fe724efa8c03ef81b`;
the downloaded job-log bytes have SHA-256
`3df8c0a5241139f078e660d96fc6e9c014ea6b44172fa951e4f058a553e499b0`.
The artifact explicitly records `score_produced=false`, contains no result or
comparison directory, and passed independent checksum and credential scans.

The preceding v12 diagnostic run `33893733107`, against controller commit
`a18e061644d4a71873784fd3322a08d22561ea19` and public EvoAgent commit
`d9e4ebe298b623816cb6cf459716b8cded518b32`, passed every source, route,
official Harbor oracle, and real lifecycle gate. It started the full pilot and
completed the first six of 24 unique singleton Harbor jobs: the three initial
validation tasks and the first three-task training batch. Before update 1 could
call the learning model, the evidence projector stopped with `Harbor train
result has an invalid task binding`. It produced no A_0, A_T, comparison,
delta, full report, or reportable score, and completed zero updates.

Pinned Harbor serializes each child result's canonical `task_name`, such as
`terminal-bench/fix-git`, while its `LocalTaskId` path, single-task dataset
filter, and patched task directory use the leaf `fix-git`. The v11 projector
incorrectly required those distinct representations to be the same string.
This source-confirmed mismatch, rather than a task substitution, caused the
direct controller exception. The guard proxy forwarded and completed 57
requests with zero rejection or retry. It recorded one upstream identity error
and no final HTTP 4xx or 5xx response; that separate provider event is not
claimed as the cause of the controller exception. The run observed a USD
0.699431712 key-wide usage delta. The diagnostic log exposed one success and
`mean_score=1.0` for the first training batch. This intermediate value is not an
A_0/A_T comparison, was not used to select the model, tasks, split, order, or
amendment, and supports no effect claim.

Artifact `9945806075` is bound by GitHub Actions artifact-ZIP SHA-256
`e6efff42a960ca049a971c55898501a42d67e4128f4f179023f1145ab561ddc5`;
the downloaded job-log bytes have SHA-256
`836a020a8e59aead31db59c9515c77b86b97fc4c5b1bc65d5dbee7cce2ddface`.
The artifact explicitly records `score_produced=false`, contains no result or
comparison directory, and passed an independent checksum and credential scan.

The preceding v11 diagnostic run `33553086805`, against controller commit
`5117afafc0aa8735d9666641aa0ec170faad5f2a` and public EvoAgent commit
`28a733b973dd691af6acab60f81da37915a5e07a`, passed every source, route,
oracle, and real lifecycle gate. It completed the first 12 of 24 unique
singleton Harbor jobs in the frozen order. Update 1 completed with
`status=updated` and `changed=true`; update 2 stopped at
`Harbor failure receipt is missing`. It produced no A_0, A_T, comparison,
delta, full report, or reportable score. The guard proxy completed 128 of 128
forwarded requests with zero rejection, upstream error, retry, HTTP 4xx, or
HTTP 5xx result, so this was not a provider, route, budget, runner, or
concurrency failure. The run observed a USD 0.027092801 key-wide delta.
The diagnostic log did expose train-batch aggregates of 1 success with
`mean_score=0.333333` for batch 1 and 0 successes with `mean_score=0.000000`
for batch 2. Those intermediate values were not an A_0/A_T comparison, were
not used to select the model, tasks, split, order, or amendment, and support no
effect claim.

Artifact `9819530153` is bound by GitHub Actions artifact-ZIP SHA-256
`a463bf5599b7619f0dd6fa973d690621b4ff3ecd79d4bde6ea0d3dce19cccce4`;
the downloaded job-log bytes have SHA-256
`6c796dca51a2df538610e1b39c2d1862f357894ec651431ddb41553d727ccb81`.
The preserved evidence identifies an errored second-batch trajectory with a
real Harbor child result but neither ATIF nor a classified runtime-failure
receipt. It does not preserve the raw exception content, so the exact outer
Harbor stage and exception type are unknown and are not claimed.

The preceding v10 diagnostic run `33537027914`, against controller commit
`b3375d7e02860d5fb6e391238f67a907f2f360d2` and public EvoAgent commit
`0217429e776e60c005396e26c9903c815c711ce0`, passed every source, route,
oracle, and real lifecycle gate. It then completed the first 12 of 24 unique
singleton Harbor jobs in the frozen order. Update 1 completed with
`status=updated` and `changed=true`; update 2 stopped fail-closed with
`Harbor failure receipt snapshot drifted`. It produced no A_0, A_T,
comparison, delta, full report, or reportable score. The guard proxy completed
124 of 124 forwarded requests with zero rejection, upstream error, retry,
HTTP 4xx, or HTTP 5xx result, so this was not a provider, route, budget, runner,
or concurrency failure. The run observed a USD 0.035118395 key-wide delta.

Artifact `9813372700` is bound by GitHub Actions artifact-ZIP SHA-256
`d2e5a12ddd704f350b3894a2b0483c29515a41db9ba4e01045d21fe982269c60`;
the downloaded job-log bytes have SHA-256
`fd9d1ba7fb281c764aa4be17ab2504649e6c046c1641f88ddc70f679e9e85215`.
The raw failure receipt was not published, so the direction of the mismatched
receipt hash is not claimed from artifact contents alone. Source inspection and
a dependency-free reproduction establish the deterministic integration bug:
after update 1 committed a new internal and on-disk candidate, the adapter did
not refresh the long-lived `BaselineState.metadata` that SEAGym passes to the
next Harbor rollout.

The preserved v9 ledger includes both hosted protocol-v8 runs after the v8
amendment. Run `33508167549` completed the real lifecycle execution but stopped
fail-closed when its evidence contract was rejected; it did not start the
24-trial pilot and observed a USD 0.005162355 key-wide delta. Run `33517129366`
then observed a USD 0.028482794 key-wide delta before the partial-job blocker
below. Neither run produced A_0, A_T, a comparison, a delta, or a score.

An earlier v8 diagnostic run `33304816856`, against controller commit
`cc54328af922aed15093687f654383c2cf88f5e5` and public EvoAgent commit
`25ee0721f7d206b6168a6d7d642bebb1700d9b41`, passed the strict route canary and
official Harbor oracle. Its lifecycle Harbor child exited zero; the guard proxy
forwarded and completed 12 requests with 12 upstream attempts, zero retries,
zero rejections, and zero upstream errors. The safe request profile contained
one request with absent `tool_choice` and 11 with `tool_choice: "auto"`. The
strict lifecycle verifier returned `invalid_evidence`, so the 24-trial pilot did
not start and no score was produced. Raw trial jobs were deleted at the privacy
boundary, so the exact ATIF model-call count is unavailable. MiMoCode v0.1.13's
source contract and the one-plus-eleven request profile provide high-confidence,
not definitive, evidence that the unmatched request was its default first-turn
title call. Artifact `9730177730` is bound by GitHub Actions artifact-ZIP
SHA-256 `819d535de948bc3a8d2ecda62af647901d4fa4f82b309ac130a250930126b0bb`;
the downloaded job-log bytes have SHA-256
`217028db03c83c93e90577aceea539825babeea93a18deb5c0ea28def13f7051`.
The lifecycle guard observed USD 0.004855739 and the entire key-usage window
observed USD 0.005334952.

The immediately preceding incomplete run `33295415122`, against controller commit
`2a44abedde490fc3d6d602a372284db030357eb4` and public EvoAgent commit
`9889fee8888baca681311a3c10880a7144f5736d`, forwarded and completed all 111
logical requests with 111 upstream attempts, zero retries, zero proxy
rejections, and zero upstream errors. It made seven bounded final-text
normalizations. The first train batch retained one usable ATIF, but later
Harbor exceptions left the second train batch with no usable ATIF. SEAGym then
stopped at the existing fail-closed evidence boundary. The safe fixed phrase
`train batch contains no usable Harbor ATIF evidence` occurred twice in
downloaded job-log bytes whose SHA-256 is
`b02d75dfe3e7591af55577c917185b55823eedca6590f52de7eda9911310f181`.
Artifact `9727486245` is bound by the GitHub Actions artifact-ZIP SHA-256
`84dcd2eb4a08a24144e50290afc5aacb373ce4f1adb30703d5cd7e3ea79a53c9`.
That run used USD 0.050295529. The artifact manifest verified 11 of 11 files;
it contains no raw prompt, response, reasoning content, complete comparison, or
reportable effect score.

Protocol v4 had already added two bounded failure-handling corrections before
any score was available. First, an errored zero-score trajectory may contribute
to aggregate failure evidence without a fabricated ATIF, provided the batch
contains at least one real valid ATIF; completed unsuccessful and successful
trajectories still require ATIF. Second, HTTP 404 joined 408, 409, 425, 429,
500, 502, 503, 504, 524, and 529 in the exact same-route retry set.

Protocol v5 responded only to the observed persistent 404 bursts: it serialized
Harbor trials from two to one, limited the proxy to at most two simultaneous
requests (one main and one auxiliary request), and expanded retry delays to 5,
10, 20, and 40 seconds, for at most four retries. Every retry preserves the
normalized outbound request bytes, route, model, and provider. Ambiguous
transport failures are not retried because the upstream may already have
accepted the POST.

After v5 failed and before any score existed, a bounded local capture of the
frozen MiMoCode v0.1.13 runtime showed that its final text step sends non-empty
local function `tools` together with `tool_choice: "none"`. The live OpenRouter
parameter metadata for the only allowed `xiaomi/fp8` endpoint reported that
`tool_choice: "none"` is unsupported. Together with the five logical requests
that each exhausted all five same-route attempts, this is high-confidence
diagnostic evidence for the 404 cause. It is not a benchmark result, and the
compatibility correction has not yet been proven by a completed benchmark.

Protocol v6 therefore applies one narrow action-authorization normalization: only an
inbound request with non-empty validated local function tools and
`tool_choice: "none"` loses both `tools` and `tool_choice` before forwarding.
The inbound request disables Tool calls; the normalized outbound request has no
Tool definitions. The proxy rejects an unexpected returned Tool call and makes
the pilot incomplete instead of allowing it to execute. Retries remain
byte-identical after this normalization. All other Tool choices remain
unchanged. Removing the schemas can alter input Tokens, cache behavior, model
conditioning, and the final-text distribution, so v6 claims preservation of
the no-Tool action boundary rather than request-semantic equivalence. Its
benchmark effect remains unproven. The model, Xiaomi endpoint, no-fallback policy, 12 Tasks, split,
order, seed, budget, metrics, interpretation rule, and config bytes remain
unchanged; the config SHA-256 remains
`28f4c9078b36c78abdb72e31014629f47943f1bee1c2f94168004d62d8b0b195`.
No Task score or partial effect was inspected to select this amendment, and
`benchmark_effect_claimed` remains false until a complete frozen run verifies it.

Protocol v7 addresses a different, two-layer lifecycle failure without changing
the frozen model, Xiaomi endpoint, tasks, split, order, seed, budget, metrics, or
config bytes. MiMoCode v0.1.13 maps a provider-reported numeric reasoning-token
usage count into its terminal step. The old sanitizer rejected any non-zero
count as though it were reasoning content. This is the highest-probability leaf
cause for the observed missing ATIF files, but it is not proven for run
`33295415122`: raw response and event content was correctly deleted, and the
safe proxy telemetry did not record that count. v7 therefore treats only a
bounded non-negative numeric count as usage telemetry while continuing to reject
all reasoning text, events, details, and content.

The second layer was deterministic: the custom Agent raised a generic runtime
error, then Harbor's post-run recovery attempted to load the missing ATIF and
raised again, masking the inner stage and cancelling pending trials. v7 assigns
fixed classified exit codes and writes a content-free, canonical-hash-bound
failure receipt. Harbor may contain only those classified failures. A missing,
malformed, unbound, or unsafe receipt remains a hard failure. A train update may
be skipped unchanged only when every missing ATIF belongs to an explicit errored
trial with a valid receipt; it performs no update-model call and remains visible
in the denominator. Any such skip or incomplete trial prevents a positive pilot
classification even if the outer run reaches final reporting.

MiMoCode's command deadline reserves 120 seconds inside Harbor's outer Agent
timeout. Its bounded 15-second forced-kill grace is part of that reserve,
leaving at least 105 seconds at the command layer in which ATIF or the
classified failure receipt can be persisted before the outer process ends.

Protocol v8 closes an evidence-accounting bypass exposed by the v7 lifecycle
run without weakening the verifier. MiMoCode v0.1.13 starts its default title
Agent on the first user turn and can independently predict a next prompt. Its
build Agent can also spawn `actor` sub-sessions; checkpoint, dream/distill,
cron, workflow/orchestrator, and MCP sampling paths can likewise initiate work
outside the completed root turn. Those requests use the frozen route but are
not guaranteed to be emitted as root-session CLI `step_finish` events, so the
sanitizer cannot bind them to the task's ATIF model calls.

The generated, per-task MiMoCode config now sets `agent.title.disable=true`,
`experimental.predict_next_prompt=false`, `memory.disable_write=true`, and
`dream.auto=false`/`distill.auto=false`; it disables the checkpoint writer,
max/orchestrator and other detached system Agents. The build Agent has an exact
Tool allowlist of `bash`, `read`, `write`, `edit`, `glob`, and `grep`, plus an
explicit `actor` deny. Top-level permissions deny actor, cron, MCP sampling,
and MCP Tool search, with no MCP servers configured. Runtime flags independently
disable cron at registration and at fire time, checkpoint creation, the
experimental umbrella, orchestrator, workflow, MCP Tool search, and exec. The
CLI supplies the fixed task-independent title `evoagent-seagym-trial`, so even
a title-config merge regression leaves the session non-default.

Automatic compaction and pruning remain enabled because compaction stays in the
root event flow. Each trial binds `HOME`, `USERPROFILE`, and `MIMOCODE_HOME` to
its disposable runtime directory, enables MiMoCode pure mode, and overrides the
late-merged `MIMOCODE_CONFIG_CONTENT` value with `{}`. The controller guard
proxy must enforce the root `x-session-affinity` value; the private proxy
implementation rejects `x-parent-session-id`, requires the affinity to equal
the request `prompt_cache_key`, and remains bound by its reviewed source hash.
Route and lifecycle checks permit exactly one root session; the full pilot
permits and requires exactly 24, with zero root-session rejections. The
small-model route remains identical to the frozen rollout route, but no
unattested auxiliary or actor-session request is authorized. A complete scored
comparison still requires exact proxy-to-ATIF logical-request equality. A
classified trial whose ATIF is missing may produce only the already-defined,
explicitly incomplete diagnostic bundle; it cannot support a positive pilot
classification.

This changes only generated runtime request scoping. It does not change the
model or provider, task set or split, order, seed, budgets, metrics, or
interpretation rule. The frozen SEAGym experiment config bytes remain unchanged
at SHA-256
`28f4c9078b36c78abdb72e31014629f47943f1bee1c2f94168004d62d8b0b195`.
No task score or partial effect was inspected, and no benchmark effect is
claimed.

Protocol v9 responds to a source-supported Harbor orchestration failure pattern observed
after v8 passed the real lifecycle gate and entered the frozen 24-trial pilot.
Run `33517129366` completed 87/87 guarded Xiaomi requests with zero proxy
rejection, upstream error, retry, provider fallback, or route drift. A replay
Harbor job then exited with two completed child results and one pending task;
the following train job exited with one completed child result and two pending
tasks. Both returned code 1. No A0/AT comparison or score was produced. The
safe artifact and job-log SHA-256 values are frozen in `protocol.json`.

Pinned Harbor executes all trials in one job with an `asyncio.TaskGroup`. An
unhandled per-trial creation, finalization, or hook exception can therefore
cancel siblings that are still pending. The safe artifact did not retain raw
Harbor stderr or job directories, so the exact triggering exception type and
stage are unknown and are not claimed. SEAGym then represented each missing
child result as an errored placeholder. EvoAgent correctly rejected those
placeholders because a task that never produced a real Harbor child result
cannot possess a real task-scoped ATIF or runtime failure receipt.

The v9 patch adds the explicit backend option
`one_task_per_harbor_job=true`. Each of the 24 already planned slots is launched
once, in the frozen order, in its own Harbor subprocess. The logical three-task
train, validation, and replay batches and both update points are unchanged.
There are no added Harbor retries, replacement tasks, synthetic receipts, or
new learning evidence. A nonzero exit returned normally by one Harbor
subprocess cannot cancel a later planned slot, but it still makes the
experiment incomplete. An exception that escapes the SEAGym host loop still
stops the run fail-closed.

The result verifier now requires exactly 24 unique Harbor job directories,
job configs, trial configs, canonical job/trial UUIDs, and the required
pinned-Harbor child `TrialResult` shape. It rejects any unreferenced complete or partial job
or trial directory. Every job must report one completed trial, no running,
pending, cancelled, or retried trial, return code zero, and aggregate token,
cost, reward, exception, and eval evidence that reconciles with its child. A
normal contained Agent failure may still be counted as a real zero-score trial
only when Harbor produced its real child result and the existing
ATIF/failure-receipt contract is satisfied. A missing result, reused job,
partial aggregate, or malformed, unbound, or synthetic placeholder receipt
remains non-scoreable and cannot update EvoAgent.

This score-blind execution-resilience amendment does not change the model or
provider, tasks or split, frozen order, seed, attempts, metrics, budgets,
timeouts, update schedule, or interpretation rule. Adding the backend isolation
flag changes the config bytes only; the v9 config SHA-256 is
`d59f0f40f0d6d7f41606be77dba7cf10c91fde7cdd13683a8b3047cc7871ae87`.

Protocol v10 responds only to the cross-batch state bug exposed after v9 made
all 12 attempted task slots independently complete. SEAGym keeps the same
`BaselineState` object for the full training loop and derives each Harbor Agent
specification from its `prompt_template_path`. EvoAgent had committed the new
candidate, prompt, attempt record, and manifest after update 1, but had updated
only its private `_candidate` pointer. The still-live state therefore continued
to identify the prior prompt for later rollouts while update 2 correctly
expected evidence bound to the committed candidate.

The adapter now refreshes that same live state object only after a state
transition has fully committed. It also verifies on entry that the live state,
internal candidate, and content-addressed disk state agree. A failed or
partially persisted transition cannot publish a new rollout state. The strict
failure-receipt snapshot and component checks are unchanged and are not
relaxed, rewritten, or inferred from the incoming trajectory.

The update evidence projection now independently requires every ATIF-v1.7
document to carry the exact current snapshot, four component hashes, route
contract, seed, API model, and MiMoCode runtime identity in both the root and
Agent identity blocks. The two blocks must agree. Missing identity, a stale
snapshot, or any drift is rejected before an update-model call. This closes the
case where a stale but otherwise structurally valid ATIF without a failure
receipt could previously have reached the updater.

These changes do not alter the model or provider, tasks or split, order, seed,
attempts, metrics, budgets, timeouts, update schedule, or interpretation rule.
The experiment config bytes remain unchanged at SHA-256
`d59f0f40f0d6d7f41606be77dba7cf10c91fde7cdd13683a8b3047cc7871ae87`.
No partial task outcome was used to choose the amendment, and no benchmark
effect is claimed.

Protocol v11 closes the distinct exception-semantics gap exposed only after
v10 crossed the stale-state blocker. Pinned Harbor writes setup, Agent,
log-sync, post-hook, verifier, and teardown exceptions into the same child
`exception_info` field. Pinned SEAGym normalizes all of them to a non-empty
trajectory error. A MiMo or sanitizer failure can create EvoAgent's strictly
identity-bound runtime receipt. A missing receipt alone does not prove which
Harbor stage failed, and the run did not preserve the raw exception type or
content.

The adapter now distinguishes three cases. A normal zero-score task with no
exception still requires a valid ATIF. A classified MiMo/sanitizer exception
still requires its existing receipt, and a declared, malformed, missing,
stale, escaped, linked, or partially present receipt/ATIF remains fatal. Any
explicit errored trajectory backed by a real contained `result.json` but no
valid receipt is classified neutrally as incomplete, unattested Harbor error
evidence, whether or not a separately valid ATIF exists. No failure stage is
inferred and no receipt is synthesized.

If any train trajectory has that third shape, the entire three-task update is
ineligible for learning. The update model is not called, zero update tokens
and cost are persisted, the candidate remains unchanged, and the frozen update
slot advances so later validation and A_0/A_T evaluation can complete. Valid
ATIF from other tasks in the same batch is not used for a partial update. A
completed bundle separately counts and hashes these failures, marks the result
`completed_with_incomplete_training_evidence`, and classifies it as
`inconclusive_incomplete_training_evidence`; raw A_0/A_T values may remain
inspectable, but no positive effect claim is permitted from such a run.

The final verifier independently recomputes each three-task training projection
from the exact trial-bound ATIF and receipt files. It binds that digest through
the update record, immutable attempt, child snapshot, E_1/final checkpoint
prefix, and aggregate update-token metrics. An explicit ATIF reference is a
mandatory constraint, not a hint: a missing path, a path to another trial, two
competing derived ATIF files, or a rewritten historical request fails closed.
The request digest is independently reconstructed from the frozen request body.
The response digest, provider, and usage are internally cross-bound across the
producer record, update record, and checkpoints; the raw provider response is
intentionally not retained, so its digest is not independently re-derived.

The update-time projector now closes the same boundary before any paid learning
call. It recomputes every normalized trajectory field from the bound Harbor
`result.json`, enforces unique task and one-task-per-job identities, and requires
each ATIF to have the production attestation and AgentContext metadata bound to
its result, snapshot, components, route, model, runtime, usage, and optional
failure receipt. Both normal and incomplete projections hash the verified
result/ATIF/attestation/receipt bundles. Incomplete no-call evidence additionally
hashes the verified ATIF set, receipt set, and batch task/job identity, so a
self-consistent rewrite of any retained evidence changes the durable attempt
digest rather than being hidden behind document counts.

The same pre-call boundary now requires binary task reward (`0` or `1`) and
zero reward for every errored child. It validates the exact production child,
TrialConfig, AgentContext, ExceptionInfo, phase timing, single-task job config,
child config, and completed aggregate; cross-checks UUIDs, usage, cost, reward,
and error counts; and binds the job-config, child-config, and aggregate hashes
into the projection. The normal ATIF, ATIF-plus-receipt, receipt-only, and empty
unattested agent-directory inventories are exact. Every bound result, config,
aggregate, ATIF, attestation, and receipt is credential-scanned before its data
can enter a projection or update request. Runtime limits match the final
verifier (16 MiB Harbor JSON, 8 MiB ATIF, and 64 KiB attestation/receipt), and
mandatory job-directory/task-checksum references plus any explicitly present
ATIF timestamp are revalidated rather than defaulted.

Checkpoint verification now also requires each durable attempt reference to
match its ordered content-addressed record, snapshot lineage, model-call
decision, skip code, request digest, zero-use no-call evidence, and the
corresponding SEAGym update record. This is a verification hardening, not new
learning evidence. Protocol v11 does not change the model or provider, tasks
or split, order, seed, attempts, metrics, budgets, timeouts, update schedule,
or interpretation rule. The experiment config SHA-256 remains
`d59f0f40f0d6d7f41606be77dba7cf10c91fde7cdd13683a8b3047cc7871ae87`.

Protocol v12 closes the task-representation mismatch exposed only after v11
allowed the first training batch to reach update projection. A frozen task has
two deliberately different representations in pinned Harbor: the child result
uses the full canonical Terminal-Bench name, while its local path and
single-task execution scaffolding use only that name's leaf. The adapter now
requires the full child `task_name` to equal the frozen task ID exactly, then
separately binds the `LocalTaskId` path, dataset filter, and patched task
directory to the derived leaf. The unattested-error path and final verifier use
the same distinction. Namespace aliases, a wrong path leaf, a cross-task
dataset, or a substituted patched directory still fail closed before any
learning-model call.

This is an identity correction, not weaker evidence validation and not new
learning evidence. It adds no retry or replacement task and does not change the
model or provider, task set or split, order, seed, attempts, metrics, budgets,
timeouts, update schedule, interpretation rule, or experiment config bytes.
The config SHA-256 remains
`d59f0f40f0d6d7f41606be77dba7cf10c91fde7cdd13683a8b3047cc7871ae87`.
No intermediate task outcome was used to choose this correction, and no
benchmark effect is claimed.

Protocol v13 closes the local `TaskConfig` serialization mismatch exposed only
after v12 allowed the first train batch to reach update projection. Pinned
Harbor writes all eight model fields, including null remote/package fields and
the false overwrite default. The adapter and final verifier now require that
exact key set and bind the local path, job source, defaults, and nulls before an
update-model call or final comparison can be accepted. This is stricter than
the v12 one-field fixture: arbitrary extra fields and every non-local field
remain rejected.

This schema correction adds no retry or replacement task and does not change
the model or provider, task set or split, order, seed, attempts, metrics,
budgets, timeouts, update schedule, interpretation rule, or experiment config
bytes. The config SHA-256 remains
`d59f0f40f0d6d7f41606be77dba7cf10c91fde7cdd13683a8b3047cc7871ae87`.
No intermediate task outcome was used to choose this correction, and no
benchmark effect is claimed.

The public protocol also pins guard-proxy source SHA-256
`e2cea221758f09c8658a65e120be3056d4dc5948eccb93668c3e3561d363fe29`,
health schema v5, and all request, response, concurrency, token and timeout
limits. Schema v5 preserves the fixed aggregate Tool-choice and normalization
counters from v4 and adds only whether root-session binding is enabled, its
fixed limit, the number of distinct accepted root sessions, and the number of
root-session rejections. It records no request body, prompt, Tool definition,
session value or hash, dynamic identifier, or request hash. A completed result
is accepted only if its safe runtime health evidence matches that identity and
proves the logical-request, attempt, retry, profile, normalization, and
root-session counts.
The controller repository remains private, so the digest binds the reviewed
implementation used by this pilot but does not make that implementation
publicly inspectable. This is an explicit controller trust boundary, not a
claim of independent end-to-end reproduction from the public repository alone.
The proxy counters bind only MiMoCode rollout requests from the 24 Harbor
trajectories. The two host-side EvoAgent update requests are verified
separately from the update records and usage evidence and are not added to the
proxy count.

| Stage | Task trials |
| --- | ---: |
| initial frozen validation | 3 |
| train rollouts | 6 |
| post-epoch frozen validation | 3 |
| replay after two batches | 6 |
| final A_T | 3 |
| final A_0 | 3 |
| total | 24 |

The expected host is a standard GitHub-hosted Ubuntu runner with four logical
CPUs and 16 GiB memory. Each selected task declares one CPU, 2,048 MB memory,
10,240 MB storage, and no GPU at the pinned Terminal-Bench commit. Harbor
concurrency is one, with a 1,800-second Agent timeout, 600-second verifier
timeout, and 355-minute workflow ceiling. The proxy permits at most two
simultaneous requests as a transport ceiling, but v8 forbids untracked title
and next-prompt calls and requires every rollout request in a complete scored
comparison to be accounted for in ATIF. Concurrency changes wall-clock
scheduling only; it must not change task
membership, batch boundaries, seed, model route, or comparison identity.
Protocol v9 through v14 isolate every planned slot into a unique Harbor
job; this changes process containment, not the frozen logical concurrency or
batch schedule.

The full pilot command is bounded to 13,200 seconds and the lifecycle gate to
2,400 seconds. Together with the 600-second MiMo route canary, this reserves
5,100 seconds of the GitHub job ceiling for checkout, installation, the Harbor
oracle, usage capture, verification, cleanup, blocker generation, and artifact
upload, so the job-level timeout should not pre-empt fail-closed reporting.

The one-seed authorization has a USD 1.20 maximum observed key-usage delta.
An independent guard polls OpenRouter's authenticated, cumulative key usage
every 10 seconds and stops the process group at a USD 0.90 delta, leaving a
buffer for the single in-flight Harbor trial and its task-scoped request. Three
consecutive usage-check failures also stop execution fail-closed. MiMoCode
additionally binds each trial to the
validated harness policy's bounded agent-step limit. The frozen baseline sets
`fail_on_update_error=true`, so a failed update request stops the paid pilot
instead of spending on later rollouts that cannot produce a verifiable update
chain. Reaching the budget guard, an update failure, or a timeout produces an
incomplete-run blocker, never a partial score. Because the usage endpoint is
key-wide, unrelated calls made with the same key during the window would count
against this pilot and should be avoided. Before the 24-trial run, compare mode
must also pass one official `terminal-bench/fix-git` lifecycle gate through the
exact custom Agent, Harbor synchronization, sanitizer, ATIF, attestation, and
verifier boundary. It is integration evidence only and produces no benchmark
claim. The gate has a USD 0.15 stop threshold measured from the same pre-run
usage snapshot; a failure stops before the full pilot.

Raw MiMoCode JSONL is transient and deleted after privacy projection. The
sanitizer accepts at most 64 MiB per trial, 16 MiB per JSONL record, and 16 MiB
per raw string; it persists only bounded structural ATIF evidence. A bounded
numeric reasoning-token usage count may be retained in ATIF metrics, but no
reasoning content is retained. Exceeding a bound fails closed and makes the
pilot incomplete rather than truncating or inventing evidence.

## Pre-registered interpretation

The primary endpoint is SEAGym final.id_test.gain_vs_A_0.

A positive pilot signal requires all of the following:

- final gain versus A_0 is greater than zero;
- the final frozen-validation score is not below its initial score;
- all 24 task trials and required reports are complete; and
- no credential exposure or safety-boundary violation is observed.

Equal final performance with no harm is no detectable signal. Negative final
gain, frozen-validation degradation, a credential exposure, or another
safety-boundary violation is a negative signal. These labels summarize
evidence only: none promotes, activates, releases, deploys, publishes, or
submits an Agent automatically.

Replay and forgetting metrics are secondary diagnostics. Aggregate gain cannot
hide a validation or safety failure. Any errored, cancelled, missing, or
unverified task remains in the denominator according to the frozen EvoAgent
benchmark-evidence rules.

## Frozen repository assets

- Config:
  experiments/seagym_terminalbench/configs/evoagent_mimo_v2_5_seed42.json
- Task references: experiments/seagym_terminalbench/tasks/task_index.json
- Split: experiments/seagym_terminalbench/splits/seed42.json
- Machine-readable protocol and all local SHA-256 values:
  experiments/seagym_terminalbench/protocol.json
- Controlled SEAGym redaction patch:
  experiments/seagym_terminalbench/patches/seagym-token-count-redaction.patch
- Controlled SEAGym job-isolation patch:
  experiments/seagym_terminalbench/patches/seagym-one-task-per-harbor-job.patch
- Source and license pins: THIRD_PARTY_LOCK.json (Git-LF SHA-256
  0fae2820ba1056f4812a25a085162fdb7b3c75a9351f2a03e1886a06887ce849)
  and THIRD_PARTY_NOTICES.md

The protocol binds SHA-256 values for the config, task index, split, controlled
patches, and the Git-LF-normalized repository third-party lock. The lock's
separate internal canonical-content hash remains the compliance/runtime
identity. A changed byte requires an explicit new protocol version rather than
a silent rerun.

## Controlled SEAGym patches

Pinned SEAGym redacts any key containing TOKEN. That also redacts numeric fields
such as total_tokens, preventing complete token/cost evidence without improving
credential safety. For both controlled patches, the workflow must:

1. verify the SEAGym commit and every original target Git blob;
2. verify both committed patch SHA-256 values;
3. run a patch applicability check;
4. apply only those patches in the transient checkout; and
5. run focused tests for numeric telemetry redaction, backend flag binding,
   ordered task isolation, non-retry behavior, and continued execution after
   one isolated job fails.

The third-party source is not edited or vendored in EvoAgent. The patches do
not alter task instructions, solutions, verifiers, model routing, or scores.

## Reporting

Only real reports may be imported. The final evidence bundle should include
the resolved commits and model route, frozen batch plan, normalized task and
verifier results, A_0/A_T summaries, validation/replay metrics, token and cost
records, controlled-patch receipt, SHA-256 manifest, and an explicit list of
errors or incomplete trials. Do not fabricate missing scores, infer success
from workflow completion alone, or describe this pilot as a leaderboard result.

Harbor's `input_tokens` value includes cache reads, while `cached_tokens`
identifies the cached subset. Visible output tokens and provider-reported
reasoning-token usage are reported separately. The attested total is therefore
`input_tokens + visible_output_tokens + reasoning_tokens`; SEAGym's existing
upstream total remains separately labeled instead of being treated as a second
independent token count.

Upstream references:

- https://github.com/antropy-research/SEAGym
- https://github.com/harbor-framework/harbor
- https://github.com/harbor-framework/terminal-bench-2
- https://github.com/XiaomiMiMo/MiMo-Code
