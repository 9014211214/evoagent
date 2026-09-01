# SEAGym + Terminal-Bench 2.0 pilot

## Status and claim boundary

This is a pre-registered, real external pilot for the EvoAgent learning loop.
It is not a synthetic wiring test, a full SEAGym paper reproduction, a
Terminal-Bench leaderboard submission, or evidence that any upstream project
endorses EvoAgent. No complete result exists at the protocol-v10 freeze time.

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

This is protocol v10, a score-blind state-propagation and evidence-binding
amendment. Sixteen controller attempts reached progressively deeper parts of
the pinned external pipeline, but none completed the A_0/A_T comparison and
none produced a score. Their cumulative observed key-usage delta was USD
0.341499816 across separately bounded observation windows. This is observed
key-wide telemetry, not an exact invoice attribution.

The latest incomplete run `33537027914`, against controller commit
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
Protocol v9 and v10 isolate every planned slot into a unique Harbor job;
this changes process containment, not the frozen logical concurrency or batch
schedule.

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
