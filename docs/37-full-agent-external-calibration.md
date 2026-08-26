# Full-Agent external adapter and low-cost calibration

Updated: 2026-08-26

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
This remains local mechanism evidence until the hosted workflow artifact is
available.

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
