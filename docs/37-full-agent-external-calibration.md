# Full-Agent external adapter and low-cost calibration

Updated: 2026-08-28

## Claim boundary

This work separates three different kinds of evidence:

1. a credential-free adapter dry-run proves schema, component binding,
   deterministic packaging and fail-closed import behavior;
2. a real MiMo Tool-call calibration may prove that the exact frozen complete
   Agent controller can drive a pinned external model through a bounded local
   Tool loop;
3. only a preregistered frozen external Task set can estimate continual-learning
   effectiveness.

Neither the dry-run nor the one-Task calibration is a benchmark score, an
official submission, a leaderboard result or evidence of generalization.

## Implemented external boundary

`FullAgentExternalEvidenceAdapter` imports an independently operated runner's
`full-agent-result.json`. It accepts only a strict observable subset and
requires:

- a controlled relative path, regular non-symlink file and caller SHA-256;
- bounded UTF-8 JSON with no credential pattern or unknown field;
- a completed, non-synthetic external run;
- the frozen manifest and complete snapshot hashes;
- one exact Task hash and role per result;
- Skill, Router, Memory and numeric Agent-policy hashes per result;
- observable Trace hashes and complete resource usage;
- no official-submission or leaderboard claim.

The protocol independently checks the frozen model, inference configuration,
Runtime, Tool contract, Verifier, seed, Task set, trials and resource budget.
Component-only SkillEvolBench output cannot pass this contract.

The credential-free run plan contains the complete immutable Agent snapshot,
four-role manifest and budget while keeping execution, network, payment,
upload and public submission disabled. Two locally independent builds produced
the same plan hash
`4effc432a51c00f286353b1ad7ce9cbdfd8c23d4eddcf066274c00c00a08e1cb`
and the same file SHA-256
`45c20b7313adcc7e6dffdd8d101c68914b2edf930044a312a3a59733b3a57827`.
GitHub-hosted run
https://github.com/9014211214/evoagent/actions/runs/32970101477 then passed
the same tests and deterministic double-build on exact source commit
`02aa17e04f7cb11ad63b02bd8727547fd6e21eec`. Artifact `9607245269` has
archive digest
`sha256:375114fb2e3790b6e45327f3ea1b021ce77e0ee3c01500e489c11a6caa61020b`.
This is hosted adapter evidence, not external Task evidence.

## Pinned MiMo calibration preset

The preset is `configs/full_agent/openrouter-mimo-v2.5-xiaomi.json`.
OpenRouter's live catalogue was checked on 2026-08-26 and returned:

- model ID `xiaomi/mimo-v2.5`;
- canonical model ID `xiaomi/mimo-v2.5-20260422`;
- Tool-call support;
- 1,050,000-Token context and 131,072 maximum completion Tokens;
- Xiaomi endpoint price of USD 0.14/M input and USD 0.28/M output.

The request pins provider slug `xiaomi`, disables provider fallbacks, accepts
only the exact or canonical model ID, and requires response-side provider,
Token and cost accounting. Sources:

- https://openrouter.ai/api/v1/models
- https://openrouter.ai/xiaomi/mimo-v2.5
- https://openrouter.ai/docs/guides/routing/provider-selection
- https://openrouter.ai/docs/cookbook/administration/usage-accounting

The calibration allows at most three model requests, 32,768 encoded request
bytes per request and 256 output Tokens per request. Treating every request
byte as an input Token gives this conservative preflight ceiling:

```text
3 * 32,768 * $0.00000014 + 3 * 256 * $0.00000028
= $0.01397760
```

This is below the owner-approved USD 2 hard cap. The runner stops on provider
drift, model drift, argument drift, missing usage, inconsistent Token counts,
request-count exhaustion or observed cumulative cost above the cap. It makes
no retry after a provider error.

The complete-snapshot controller still decides the action. Router and verified
Memory select the Skill, Skill rules require post-write verification, and the
numeric Policy selects the initial inspection. MiMo must reproduce each exact
typed Tool call; the independent Verifier, rather than model text, checks the
final state. Persisted calibration evidence contains only hashes, bounded
component selections, Tool sequence hash, verifier outcome and usage totals.
It excludes prompts, raw responses, credentials and document payloads.

## Budget recalculation protocol

After one successful real calibration, record its exact request, input Token,
output Token, cost and wall-time totals. Estimate a frozen experiment from
observable units rather than the previous Qwen smoke:

```text
model cost = measured cost per completed model-mediated episode
             * planned model-mediated episodes

hosted time = measured wall time per completed episode
              * planned episodes / safe concurrency
```

The estimate must separately reserve evolution, evaluation, replay and failed
episode overhead. A complete seed remains unauthorized until the measured
estimate fits the available balance and a preregistered Task/seed/budget
contract. Calibration cost must never be presented as that estimate before a
real result exists.

## Executed calibration result

The owner approved one run with a USD 2 hard stop. Private GitHub Actions run
`32971345039` executed exact private workflow commit
`c82704889f3a62be003c42e8f29c5a103b4c77df` and exact public implementation
commit `02aa17e04f7cb11ad63b02bd8727547fd6e21eec`. All gates passed on the first
attempt. The provider returned the exact canonical model and the frozen
controller's three exact Tool calls; the independent verifier passed.

Observable usage was:

| Quantity | Result |
|---|---:|
| Model requests | 3 |
| Input Tokens | 1,377 |
| Output Tokens | 176 |
| Total Tokens | 1,553 |
| OpenRouter model cost | USD 0.00024206 |
| Paid execution step | 16 seconds |
| Complete hosted job | 36 seconds |

Artifact `9607726279` has archive digest
`sha256:6d67e509c62c5c2b5f2ae780c87358bc7994cba88ed94326934659b167e9f0e0`.
Its three content hashes were independently recomputed after download. The
sanitized evidence is retained at
`evidence/full-agent/mimo-v2.5-calibration-seed43.json`; its SHA-256 is
`cfdfe3fff7be2d03371d5770b42cf920fb2dd2a9a5dfcfe1c8516cb472efc01f`.
It contains no prompt, response, credential or document payload.

The claim remains `integration_calibration_only_not_benchmark_evidence`.
This result proves the exact-model/provider route, complete-snapshot controller
binding, bounded Tool-call loop, usage accounting and verifier path. It does
not measure continual-learning effectiveness or generalization.

## Capability-aware scientific preset

Later scientific execution exposed a narrower routing constraint. The
historical MiMo calibration used the named-function Tool-choice object and a
first complete-seed attempt stopped with `model_tool_call_noncompliance`. A
separate one-request Qwen3.8 Flash route probe reached OpenRouter but returned
HTTP 404 before provider selection: provider attempt count 0, model requests 1,
Token usage 0 and observed cost USD 0. Neither failure is a Task score.

Current endpoint metadata and OpenRouter's per-provider feature table checked
on 2026-08-28 identify the active Xiaomi route as exact endpoint tag
`xiaomi/fp8`, with a 1,048,576-Token endpoint context, and indicate that
`required` is the compatibility candidate rather than the stricter
named-function form. This directory signal is not route-execution proof. The
versioned preset
`configs/full_agent/openrouter-mimo-v2.5-xiaomi-required.json` therefore sends
`tool_choice="required"` while exposing exactly one Tool schema. This preserves
the controller boundary: the model has no alternative Tool to choose, and the
existing response verifier still requires one call with the exact frozen name
and arguments. Provider/model fallback remains disabled, `auto` is not an
allowed mode, and malformed, zero-call, multi-call or drifted responses fail
closed.

The historical preset and lock are unchanged. The new mode has a distinct
preset ID, endpoint tag, capability-verification timestamp, disabled-reasoning
setting, fingerprint and
scientific lock at
`configs/full_agent/minimal-scientific-seed-A-mimo-v2.5-required.lock.json`.
Its Task, manifest and snapshot hashes remain identical to the historical MiMo
protocol; only the explicitly studied transport-compatibility contract changes.
This implementation and its dry-run were zero-cost evidence. A real request
required a separate one-use authorization. The request pinned the full
`xiaomi/fp8` endpoint slug rather than the Xiaomi base slug, with fallbacks
disabled. The generic catalogue still did not prove each strict `tool_choice`
subtype.

An initial gate exercise in private workflow run `33179418764` at source
head `920e972b268b13f5f5cb6e333fefa1058b1edaac`. Fresh route-price and regular-key
preflights passed. The sole inference request reached Xiaomi and returned HTTP
200 for `xiaomi/mimo-v2.5`; canonical response metadata identified
`xiaomi/mimo-v2.5-20260422`. It used 277 prompt plus 32 completion Tokens,
reached its 32-Token completion cap, and reported complete cost USD 0.00004774.
It did not contain the exact required frozen Tool call, so the strict verifier recorded
`required_tool_call_verified=false` and failed closed with
`successful_response_failed_closed_verification`. Sanitized artifact
`9689044579` has digest
`sha256:a38fb4dd9e818ab8cfc26883fa151654ea79d200bb8e0e4586788cdaa1a0bb1b`;
its JSON has SHA-256
`7dd79cab6df62ffa38209ee237100fd424651f0dac48025e8650e9150374664e`.
No seed episode or score was produced by that run.

The corrected, separately authorized probe disabled reasoning, raised only the
probe output ceiling to 256 Tokens, and strengthened the success check. Private
workflow run `33183563382` at exact source head
`252344023c75152bf67be33aa0c9d51fa997f094` then completed the one allowed
inference request. Xiaomi returned HTTP 200, exact model/provider routing,
`finish_reason=tool_calls`, one typed Tool call with an ID, the exact frozen
function name and arguments, no prose or reasoning fields, and complete usage
of 279 prompt plus 22 completion Tokens. Observed cost was USD 0.00004522.
Sanitized artifact `9690717555` has GitHub digest
`sha256:1a219cb3f8fd035f25ee09ccb4f0852d7e5ed703aa4591633fc75c36a7fffabd`;
its JSON has SHA-256
`b8957ad45199457a0501cf4d92ec753ba14bcd4765c7253bac56ef1600e4946f`.
This proves the exact required-Tool transport contract needed to make the
frozen 60-episode seed eligible. That probe is still not a Task score or
benchmark result.

Subsequent full-seed attempts exposed two response-normalization bugs rather
than model-performance failures. Public fixes first accepted an exact empty
`content` placeholder, then accepted absent/null/exact-empty `reasoning` and
`reasoning_content` plus absent/null/empty-list `reasoning_details`. Non-empty
reasoning, prose, wrong types and any Tool/provider drift remain rejected.
Governed run `33197785751` from exact public source
`3f3e85b188ac6ecbb4734053ed5615da89d2e889` then completed all 60 frozen
episodes and strict import. The authoritative attempt ledger and narrow claim
boundary are in `docs/MINIMAL_SCIENTIFIC_VALIDATION.md`.

## Historical pre-execution planning envelope

The proposed minimal scientific seed is one complete A0 to A4 evolution path:

- four one-component evolution rounds;
- 12 frozen held-out Tasks, three each for retention, transfer, adversarial and
  composition;
- all 12 Tasks evaluated on A0 through A4, giving 60 evaluation episodes;
- 20 to 40 additional failure, counterfactual, candidate-verification, replay
  and optimizer episodes;
- 80 to 100 model-mediated episodes in total, with three requests per episode.

The three-request value is a proposed hard planning limit derived from the
verified controller loop, not an observed property of unseen benchmark Tasks.
A Task that cannot finish inside it must be recorded as budget-blocked rather
than silently receiving more calls.

At the measured calibration size, 80 to 100 episodes would use 124,240 to
155,300 Tokens and cost USD 0.0193648 to USD 0.024206. Reserving 25 percent
for failed episodes and control overhead gives a calibration-equivalent floor
of USD 0.024206 to USD 0.0302575. This is a lower bound because real Tasks may
have larger contexts.

For execution planning, cap every request at 8,000 input and 1,000 output
Tokens. At the pinned Xiaomi prices, 240 to 300 requests cost USD 0.336 to USD
0.420; the same 25 percent reserve gives a model envelope of USD 0.420 to USD
0.525. Serial model time extrapolates to roughly 27 to 33 minutes including
that reserve. A conservative private-runner envelope of 45 to 90 minutes adds
USD 0.27 to USD 0.54 when included Actions minutes are exhausted, using the
current USD 0.006/minute standard Linux rate. Standard runners in a public
repository are free. Pricing source:
https://docs.github.com/en/billing/reference/actions-runner-pricing.

The recommended authorization cap for one minimal seed was therefore USD 1.20
total: USD 0.60 model plus USD 0.60 hosted-runner reserve. A separate extreme
32,000-input/4,000-output-token envelope would raise model cost to USD 1.68 to
USD 2.10 after reserve and is not recommended for the minimum set. The later
frozen execution used 114 requests, 58,014 Tokens and USD 0.0086178344 model
cost over 150.394 controller seconds. Those observed values replace this
planning estimate for the exact successful seed, without turning the result
into an authoritative benchmark.
