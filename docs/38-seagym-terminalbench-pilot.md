# SEAGym + Terminal-Bench 2.0 pilot

## Status and claim boundary

This is a pre-registered, real external pilot for the EvoAgent learning loop.
It is not a synthetic wiring test, a full SEAGym paper reproduction, a
Terminal-Bench leaderboard submission, or evidence that any upstream project
endorses EvoAgent. No result exists at freeze time.

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
- reasoning.enabled: false
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

This is protocol v2, a score-blind amendment to v1. Four controller
infrastructure/preflight attempts occurred after v1; all stopped before
provider inference, official Task trials, or any benchmark score. The final
diagnostic run `33237155533` recorded zero observed usage delta and exposed the
safe `openrouter_http_404` category. The endpoint capability record showed that
Xiaomi supports the required Tool parameters but not the API `seed` parameter.
The amendment therefore changes only that transport field and the corresponding
determinism claim; it does not change the 12 Tasks, split, order, model,
provider, Tool contract, budget, metrics, or interpretation rule. GitHub
Actions artifact ZIP `9710284320` is bound by SHA-256
`caa249dea1ff5ac015780b673c4fc4f2ab81ed55ec221b6ba13143213eb568eb`.

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
concurrency is two, with a 1,800-second Agent timeout, 600-second verifier
timeout, and 355-minute workflow ceiling. Concurrency changes wall-clock
scheduling only; it must not change task membership, batch boundaries, seed,
model route, or comparison identity.

The one-seed authorization has a USD 1.20 maximum observed key-usage delta.
An independent guard polls OpenRouter's authenticated, cumulative key usage
every 10 seconds and stops the process group at a USD 0.90 delta, leaving a
buffer for the two in-flight trials. Three consecutive usage-check failures
also stop execution fail-closed. MiMoCode additionally binds each trial to the
validated harness policy's bounded agent-step limit. The frozen baseline sets
`fail_on_update_error=true`, so a failed update request stops the paid pilot
instead of spending on later rollouts that cannot produce a verifiable update
chain. Reaching the budget guard, an update failure, or a timeout produces an
incomplete-run blocker, never a partial score. Because the usage endpoint is
key-wide, unrelated calls made with the same key during the window would count
against this pilot and should be avoided.

Raw MiMoCode JSONL is transient and deleted after privacy projection. The
sanitizer accepts at most 64 MiB per trial, 16 MiB per JSONL record, and 16 MiB
per raw string; it persists only bounded structural ATIF evidence. Exceeding a
bound fails closed and makes the pilot incomplete rather than truncating or
inventing evidence.

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
- Controlled SEAGym patch:
  experiments/seagym_terminalbench/patches/seagym-token-count-redaction.patch
- Source and license pins: THIRD_PARTY_LOCK.json (Git-LF SHA-256
  0fae2820ba1056f4812a25a085162fdb7b3c75a9351f2a03e1886a06887ce849)
  and THIRD_PARTY_NOTICES.md

The protocol binds SHA-256 values for the config, task index, split, controlled
patch, and the Git-LF-normalized repository third-party lock. The lock's
separate internal canonical-content hash remains the compliance/runtime
identity. A changed byte requires an explicit new protocol version rather than
a silent rerun.

## Controlled SEAGym redaction patch

Pinned SEAGym redacts any key containing TOKEN. That also redacts numeric fields
such as total_tokens, preventing complete token/cost evidence without improving
credential safety. The workflow must:

1. verify the SEAGym commit and original redaction.py Git blob;
2. verify the committed patch SHA-256;
3. run a patch applicability check;
4. apply only that patch in the transient checkout; and
5. test that numeric token counts persist while string credentials remain
   redacted.

The third-party source is not edited or vendored in EvoAgent. The patch does
not alter task instructions, solutions, verifiers, model routing, or scores.

## Reporting

Only real reports may be imported. The final evidence bundle should include
the resolved commits and model route, frozen batch plan, normalized task and
verifier results, A_0/A_T summaries, validation/replay metrics, token and cost
records, controlled-patch receipt, SHA-256 manifest, and an explicit list of
errors or incomplete trials. Do not fabricate missing scores, infer success
from workflow completion alone, or describe this pilot as a leaderboard result.

Harbor's `input_tokens` value includes cache reads, while `cached_tokens`
identifies the cached subset. The final bundle therefore reports both the
attested total (`input_tokens + output_tokens`) and SEAGym's upstream total
(`input_tokens + cached_tokens + output_tokens`) instead of silently treating
the latter as a second independent token count.

Upstream references:

- https://github.com/antropy-research/SEAGym
- https://github.com/harbor-framework/harbor
- https://github.com/harbor-framework/terminal-bench-2
- https://github.com/XiaomiMiMo/MiMo-Code
