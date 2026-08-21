# Frozen composite evaluation and bounded stop policy

## Purpose

The composite snapshot Registry identifies the exact Agent configuration, but it
does not determine whether that Agent is good enough to stop evolving. v2.3
therefore adds a separate immutable evaluation and decision lifecycle:

```text
active composite snapshot
    -> frozen Task outcomes
    -> independently derived track scores
    -> persisted evaluation
    -> deterministic stop policy
    -> CONTINUE / STOP / ESCALATE
```

The Supervisor cannot self-report success. Scores, safety counts, regressions,
resource usage and the stop action are recomputed from immutable evidence.

## Frozen Task outcomes

Every `CompositeTaskOutcome` binds:

- Task ID and governed track (`skill` or `local_policy`);
- binary pass result and matching 0/1 score;
- unsafe-action count;
- Tool calls and episode steps;
- deterministic local cost;
- trace and verifier hashes.

A composite evaluation requires at least one Task from both tracks. Task IDs are
unique and normalized before hashing.

## Snapshot evaluation

`CompositeSnapshotEvaluation` binds:

- lineage, snapshot and round IDs;
- exact composite snapshot manifest hash;
- frozen Task-manifest hash;
- direct parent evaluation hash;
- normalized Task outcomes;
- derived Skill, local-policy and composite scores;
- derived safety, regression and resource totals;
- independent evaluator and timezone-aware chronology;
- canonical evaluation hash;
- explicit false flags for external execution, production traffic/deployment and
  official Benchmark claims.

The composite score is always:

```text
(skill_score + local_policy_score) / 2
```

A child evaluation must contain the exact same Task IDs and tracks as its parent.
A Task that passed in the parent and fails in the child increments the derived
regression count.

## Controlled A0 / A1 / A2 evidence

The controlled four-Task manifest contains two Skill Tasks and two local-policy
Tasks:

```text
A0: Skill 0.5, local policy 0.0, composite 0.25
A1: Skill 1.0, local policy 0.0, composite 0.50
A2: Skill 1.0, local policy 1.0, composite 1.00
```

A0 and A1 retain the unsafe initial policy evidence. A2 has zero safety
violations and zero regressions.

## Bounded stop policy

`CompositeStopPolicy` freezes:

- maximum rounds;
- target composite score;
- zero-safety requirement;
- zero-regression requirement;
- no-actionable-case requirement.

The deterministic decision is:

```text
STOP
    when target score is reached,
    safety violations == 0,
    regressions == 0,
    actionable cases == 0

ESCALATE
    when the target is not met and
    the final round or explicit budget exhaustion is reached

CONTINUE
    otherwise
```

A perfect score with a safety violation cannot stop. A terminal decision cannot
be followed by another evaluation round.

## Persistence

`SQLiteCompositeEvaluationRepository` persists one frozen policy and one
Evaluation/Decision pair per round. It enforces:

- contiguous rounds beginning at zero;
- exact parent evaluation hashes;
- parent decision must exist and be `CONTINUE` before a child evaluation;
- exact retries are read-only;
- conflicting evaluations and decisions fail closed;
- decisions are regenerated from the persisted evaluation and policy before
  acceptance;
- one SHA-256 audit chain per lineage;
- semantic replay of Policy, Evaluation and Decision events;
- audit modification and tail truncation detection.

## Role separation and snapshot binding

`CompositeEvaluationService` evaluates only the active composite pointer.

- The evaluator cannot be the snapshot creator or pointer committer.
- The stop-policy registrar cannot overlap snapshot control roles.
- The stop decider cannot be the evaluator, policy registrar, snapshot creator
  or pointer committer.
- Every persisted evaluation is cross-checked against the immutable snapshot
  manifest hash, round and Task-manifest hash.

## Restart contract

An exact Evaluation or Decision retry returns the persisted record and writes no
second event. A changed payload, actor, action, parent hash or Task set fails
closed.

## Deliberately absent authority

This evaluation layer does not:

- mutate a Skill or local policy;
- execute local-RL optimization;
- promote or activate a component;
- train an LLM or modify foundation-model weights;
- activate or deploy a production Runtime;
- perform external execution or an official Benchmark;
- publish a package, release or tag.

It records only frozen local evidence and a bounded control decision for the
next v2.3 orchestration stage.
