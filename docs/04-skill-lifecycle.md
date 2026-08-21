# Immutable Skill Lifecycle

## Invariants

1. A registered `SkillSpec` is immutable.
2. Evolution creates a new semantic version; it never edits the active version in place.
3. Every candidate has an existing parent version.
4. Promotion requires an explicit frozen-evaluation decision.
5. Rejected candidates remain available for audit.
6. Rollback moves the active pointer; it does not delete later versions.
7. Every lifecycle transition emits a sequenced event.

## Lifecycle

```text
ACTIVE 1.0.0
   |
   +-- immutable patch --> CANDIDATE 1.1.0
                              |
                  frozen evaluation
                       /              \
                    pass              fail
                     |                  |
                 ACTIVE             REJECTED
                     |
               canary regression
                     |
                 ROLLBACK
                     |
                 ACTIVE 1.0.0
```

## Candidate evaluation

A candidate is evaluated on the same task IDs as its base version. The policy records:

- base and candidate score;
- score delta;
- regressions where the base passed and the candidate failed;
- promotion or rejection reason.

The default policy requires a strictly positive score delta and zero regression.

## Version graph and provenance

Each version records its parent, content hash, evidence references, generator and evaluation decision. The in-memory registry retains all active, superseded and rejected versions.

## Concurrency limitation

The v0.3 registry is in-memory and is not thread-safe. A persistent implementation must use a transaction or optimistic compare-and-swap on the active version to prevent two candidates from being promoted against a stale parent.
