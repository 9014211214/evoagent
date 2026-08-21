# Benchmark-Gated Champion Promotion

## Purpose

v1.7 produces authoritative, frozen comparison evidence. v1.8 turns that evidence into a governed Agent-snapshot release decision.

A higher aggregate score is not sufficient for promotion. A Challenger must also satisfy Task-regression, error, confidence, Token, cost, comparability, approval, and active-pointer gates.

## Lifecycle

```text
v1.7 BenchmarkComparisonPackage
    -> deterministic paired-Task bootstrap
    -> per-round hard gates
    -> best eligible Challenger
    -> stop / continue recommendation
    -> immutable ChampionSelectionDecision
    -> persistent Champion Registry
    -> CHAMPION_PROMOTION Campaign
    -> two independent approvals
    -> Campaign AUTHORIZED
    -> Registry AUTHORIZED
    -> explicit pointer activation
    -> Campaign COMPLETED
    -> optional explicit rollback
```

Every arrow is a separate operation. In particular:

```text
best benchmark round != admitted Challenger
admitted Challenger != evaluated
Campaign AUTHORIZED != Registry AUTHORIZED
Registry AUTHORIZED != active Champion
active Champion != production deployment
```

## Promotion policy

`ChampionPromotionPolicy` is immutable and hash-bound. It controls:

- minimum aggregate gain over A0;
- paired-Task bootstrap confidence level, deterministic seed, and resample count;
- minimum confidence lower bound;
- maximum regressed Task count and fraction;
- maximum error-rate increase;
- maximum input/output Token growth;
- maximum cost growth;
- required versus optional usage evidence;
- whether a non-final round may be selected;
- patience and stop behavior;
- optional exact same-model comparator requirements.

The gate recomputes Task deltas from the immutable v1.7 run evidence. It does not trust only the precomputed aggregate report.

## Deterministic paired bootstrap

For each evolved round, the gate builds one score delta per frozen Task:

```text
delta_i = candidate_task_score_i - baseline_task_score_i
```

A seeded bootstrap repeatedly samples the Task deltas with replacement and records the mean. The output binds:

```text
confidence level
resample count
round-specific seed
observed mean
lower and upper bounds
hash of the complete bootstrap mean sequence
```

This is deterministic evidence for release gating. It does not convert the synthetic controlled lab into a statistically representative real-world benchmark.

## Round outcomes

Each evolved round is classified as:

```text
eligible
rejected
insufficient_evidence
```

Hard violations such as Task regression, excessive error growth, or excessive resource growth produce `rejected`. Missing evidence required by policy produces `insufficient_evidence`.

The selected Challenger is the highest-scoring eligible round. Tie-breaking is deterministic and considers confidence, regressions, cost, and earlier round number. The final round receives no automatic privilege.

If no round is eligible, the decision is `hold` or `reject`; the active Champion remains unchanged.

## Controlled result

The offline lab reuses the v1.7 synthetic evidence:

```text
A0 = 0.25
A1 = 0.50, zero Task regressions
A2 = 0.75, one Task regression
```

Under the zero-regression policy:

```text
A1 -> eligible and selected
A2 -> rejected: maximum_regressed_tasks_exceeded
stop recommendation -> stop
```

This demonstrates why the framework must not promote the latest or highest aggregate score blindly.

```bash
python examples/benchmark_gated_champion.py
```

The result remains synthetic lifecycle evidence. It is not an official Terminal-Bench score or a foundation-model intelligence claim.

## Persistent Champion Registry

`SQLiteChampionRegistry` stores:

```text
immutable decisions
immutable snapshot records
one active Champion pointer
optimistic pointer revision
SHA-256 chained lifecycle events
external audit checkpoint
```

Lifecycle states are:

```text
champion
challenger
evaluated
authorized
retired
rejected
rolled_back
```

The controlled activation changes revision `0 -> 1`. Dedicated tests execute rollback `1 -> 2` and restore the parent Champion while preserving both records and the full event history.

## Governance

A separate high-risk `CHAMPION_PROMOTION` Campaign binds:

```text
full v1.7 package hash
policy hash
decision hash
selected run ID and evidence hash
selected snapshot ID
```

It requires exactly two distinct approving actors. The decision actor cannot approve. Authorization is revalidated from persistent approval records before Registry authorization and again before activation.

Only explicit activation changes the Champion pointer and completes the Campaign.

## Reproducible package

`ChampionDecisionPackageManifest` includes:

```text
framework/source/third-party provenance
full v1.7 BenchmarkComparisonPackage
immutable policy and selection decision
all per-round assessments and bootstrap evidence
completed Promotion Campaign and approvals
Champion records, audit events, and checkpoint
Campaign events and checkpoint
active snapshot and revision
explicit no-execution/no-upload/no-official-claim flags
```

Verification recomputes the decision from v1.7 evidence, checks the Campaign fingerprint and payload, binds approvals to Campaign audit identities, verifies both audit chains, and validates the active pointer.

Coherently rehashed changes to policy thresholds, selected round, confidence interval, approvals, active pointer, Campaign evidence, event history, or checkpoint are rejected.

## Boundaries

v1.8 does not:

- execute Harbor or Terminal-Bench;
- call a model provider;
- run SFT, DPO, GRPO, or Agentic RL;
- create, download, or load checkpoints;
- deploy an Agent to production;
- upload results or submit to a leaderboard;
- claim an official benchmark score;
- change repository visibility or grant a software license.
