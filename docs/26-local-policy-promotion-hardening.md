# Local-policy promotion hardening notes

This note records the final v2.2 controls that sit above the basic promotion and
rollback state machine. They are part of the reviewed contract rather than
implementation details.

## Final public implementations

The public package deliberately points at three final layers:

```text
SQLiteLocalPolicyRegistry
    -> evoagent.local_policy.repository_chronology_final

LocalPolicyPromotionLifecycleService
    -> evoagent.local_policy.lifecycle_recovery_final

LocalPolicyPromotionPackageManager
    -> evoagent.local_policy.package_semantic_final
```

The earlier modules remain internal implementation layers. Public API tests and
the installed-Wheel gate reject a fallback to them.

## Stage-aware exact retries

A complete Promotion or Rollback submission retry first reconstructs the exact
immutable report, decision/request and assessment. The retry must match the
persisted Registry evidence byte-for-byte under normalized model semantics.

The persisted Campaign state then determines the only permitted recovery step:

```text
OPEN
    -> attach the exact candidate artifact

CANDIDATE_READY / EVALUATION_PENDING
    -> finish only the missing evaluation transition

APPROVAL_PENDING / AUTHORIZED / COMPLETED
    -> validate and return without lifecycle writes
```

The same actor, approval decision and reason may retry a previously stored
approval read-only. A changed reason or decision fails closed.

## Two restart windows

### Evidence persisted before Campaign attachment

The first process can stop after:

```text
Campaign reserved as OPEN
Registry promotion or rollback evidence stored
candidate artifact not yet attached to Campaign
```

An exact retry reloads the persisted Registry evidence, attaches exactly that
artifact, and advances the Campaign to its evaluation/approval state. It does
not write another Registry event. A retry with changed evidence fails closed.

### Pointer mutation persisted before Campaign completion

The second process can stop after:

```text
Registry pointer transaction committed
Campaign still AUTHORIZED
```

An exact retry verifies:

- the same actor;
- the same direct parent;
- the original expected optimistic revision;
- the already-applied resulting revision;
- the exact Campaign evidence.

It then performs only the missing `AUTHORIZED -> COMPLETED` Campaign transition.
Later retries are read-only. A Campaign that is `COMPLETED` while the matching
pointer mutation is absent is rejected as inconsistent.

## Time safety

Lifecycle evidence must be timezone-aware and must not be in the future.
Registry write timestamps are validated before any SQLite transaction begins,
so a naive timestamp cannot leave a partially committed row or audit event.

The final package verifies:

```text
promotion decision
    <= promotion approvals
    <= Registry promotion authorization
    <= pointer activation
    <= Promotion Campaign completion

pointer activation
    <= rollback request / assessment
    <= rollback approvals
    <= Registry rollback authorization
    <= pointer rollback
    <= Rollback Campaign completion
```

Both the local Registry audit chain and global Campaign audit chain must also be
internally monotonic. The final package timestamp must be timezone-aware, must
not be in the future and must not predate any embedded evidence.

## Role separation after generic Campaign API use

The shared Campaign repository intentionally remains generic. Therefore a caller
could try to approve a Promotion or Rollback Campaign directly and bypass the
domain service. Before Registry authorization and again before pointer mutation,
v2.2 reloads all persisted approvals and revalidates:

- Campaign type;
- `HIGH` risk;
- exactly two distinct approvals;
- approval decisions;
- complete accepted-evidence and local lifecycle roles.

The Promotion Registry authorizer cannot execute activation. The Activation
executor cannot become Rollback requester, evaluator, approver, authorizer or
executor. Rollback authorization and pointer execution are also separate roles.
The final package repeats the same cross-stage checks, so a generic API or direct
database bypass followed by coherent rehashing still fails.

## Semantic audit replay

Hash validity is necessary but insufficient. The final package recomputes and
compares the exact local Registry semantics and each seven-event Campaign
lifecycle:

```text
campaign_created
candidate_attached
candidate_ready -> evaluation_pending
evaluation_pending -> approval_pending
approval A
approval B -> authorized
authorized -> completed
```

Verification binds:

- event type ordering;
- actor ordering;
- governed reason strings;
- manifest, package, receipt, report and decision hashes;
- Campaign type, risk, generator, target, fingerprint and metadata;
- Candidate artifact and reference;
- approval identity, decision, reason, time and resulting state;
- Campaign IDs and revisions;
- `active_revision_before` values;
- request and rollback report hashes;
- terminal activation/rollback executor;
- external audit checkpoints.

Regression tests change reason, metadata, chronology, terminal actor, candidate
checkpoint, provenance and approval identity, rebuild every affected hash, and
still require rejection.

## Provenance binding

The v2.2 package cross-binds these fields to the independently accepted v2.1
base package:

```text
framework_version
source_repository
third_party_lock_hash
```

The v2.2 source commit remains a separate top-level provenance value because it
identifies the successor implementation rather than the v2.1 optimizer evidence.
Candidate admission separately binds the parent checkpoint, optimizer
configuration and accepted source commit.

## Authority boundary

None of the hardening layers grants authority to:

- update foundation-model weights;
- activate a provider model checkpoint;
- deploy or route production traffic;
- perform an external rollout;
- publish a package or model artifact;
- run or upload an official Benchmark;
- create a release or tag.

The only mutable object authorized by v2.2 is the local Registry active-policy
pointer, and that mutation is separately governed, audited and rollbackable.
