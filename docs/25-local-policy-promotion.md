# Governed local-policy promotion and rollback

## Scope

v2.2 consumes an independently accepted v2.1 Program/local-RL evidence chain and
turns the selected tiny local Agent-policy checkpoint into a Registry candidate.
It does not train a language model, promote foundation-model weights, deploy to
production, route production traffic, or authorize an external rollout.

The new boundary is deliberately narrower than model activation:

```text
accepted v2.1 local-RL evidence
    -> immutable local-policy candidate
    -> independent promotion assessment
    -> promotion decision
    -> high-risk Promotion Campaign
    -> two independent approvals
    -> separate Registry authorization
    -> explicit optimistic-pointer activation
    -> optional independent Rollback Campaign
    -> two independent rollback approvals
    -> separate rollback authorization
    -> explicit optimistic-pointer rollback
```

`AUTHORIZED` never means active. Only `activate()` or `rollback()` changes the
local Registry pointer.

## Accepted input evidence

A candidate is admissible only after
`ProgramLocalRLAcceptanceManager.verify(...)` accepts all three exact objects:

- `FullyAttestedProgramLocalRLBindingPackage`;
- `ProgramLocalRLTrustedAnchors`;
- `ProgramLocalRLAcceptanceReceipt`.

The candidate manifest binds:

- the fully attested package ID and hash;
- trusted-anchor ID and hash;
- acceptance-receipt ID and hash;
- native local-RL package hash;
- optimizer and held-out evaluation hashes;
- initial and selected checkpoint hashes;
- optimizer-configuration hash;
- disjoint training and held-out Task-set hashes;
- source commit;
- the complete accepted-evidence actor set.

The candidate creator must be independent from every actor in the accepted v2.1
evidence chain.

## Registry states

```text
P0 ACTIVE
    |
    | admit accepted candidate
    v
P1 CANDIDATE
    |
    | independent assessment and decision
    v
P1 EVALUATED or REJECTED
    |
    | exact Promotion Campaign authorized
    v
P1 AUTHORIZED
    |
    | activate(expected_active_revision=0)
    v
P1 ACTIVE, P0 SUPERSEDED, head revision=1
    |
    | exact Rollback Campaign authorized
    v
rollback(expected_active_revision=1)
    |
    v
P0 ACTIVE, P1 ROLLED_BACK, head revision=2
```

The SQLite Registry stores immutable manifests and evidence. The only mutable
business pointer is `local_policy_heads.active_policy_id`, guarded by an
optimistic revision.

## Promotion governance

Promotion uses `CampaignType.LOCAL_POLICY_PROMOTION` with high risk and exactly
two approvals. Its fingerprint binds the candidate manifest, accepted package,
acceptance receipt, promotion report, promotion decision, and selected
checkpoint.

The following roles must remain distinct:

- accepted v2.1 evidence roles;
- candidate creator;
- promotion evaluator;
- promotion decision actor;
- two promotion approvers;
- Registry promotion authorizer;
- activation executor.

Generic Campaign approval is not sufficient. Before Registry authorization and
again before activation, the domain service reloads persisted approvals and
revalidates the full role set.

## Activation

Activation requires all of the following:

```text
candidate status == AUTHORIZED
promotion Campaign state == AUTHORIZED
exact Campaign artifact == Registry candidate evidence
exactly two independent approvals
current active policy == candidate parent
current head revision == expected_active_revision
activation actor independent from every prior role
```

A successful activation performs one transaction:

```text
P0 ACTIVE      -> SUPERSEDED
P1 AUTHORIZED  -> ACTIVE
head P0        -> P1
revision 0     -> 1
append ACTIVATED audit event
```

Campaign completion occurs only after this Registry transaction succeeds.

## Crash recovery

The service explicitly handles the narrow recovery window in which the Registry
pointer mutation committed but the process stopped before the Campaign was
marked `COMPLETED`.

On retry:

1. the Registry detects that the exact policy is already active;
2. it verifies that the retry uses the original activation actor;
3. it writes no second Registry event;
4. the service changes the still-`AUTHORIZED` Campaign to `COMPLETED` once;
5. later retries are completely read-only.

The rollback path implements the same recovery contract.

A Campaign that is already `COMPLETED` while its expected pointer mutation is
absent is rejected as inconsistent evidence.

## Rollback governance

Rollback uses the separate
`CampaignType.LOCAL_POLICY_ROLLBACK`. It is not an inverse flag on the Promotion
Campaign.

A rollback request binds:

- exact source and direct-parent target policy IDs;
- Promotion Campaign ID;
- promotion-decision hash;
- independent rollback evidence hash;
- requester identity and timestamp.

An independent rollback evaluator attests that:

- the source is active;
- the target is its direct parent;
- the target is superseded;
- the local pointer rollback is admissible.

Promotion authorization and activation actors are prohibited from becoming
rollback requester, evaluator, approver, authorizer, or executor. This rule is
checked both by the runtime service and by the final package verifier.

## Rollback transaction

A successful rollback performs one transaction:

```text
P1 ACTIVE      -> ROLLED_BACK
P0 SUPERSEDED  -> ACTIVE
head P1        -> P0
revision 1     -> 2
append ROLLED_BACK audit event
```

The rollback Campaign is completed only after the Registry transaction commits.

## Audit evidence

The local Registry audit chain contains these exact semantic stages:

```text
REGISTERED
CANDIDATE_ADMITTED
EVALUATED
AUTHORIZED
ACTIVATED
ROLLBACK_SUBMITTED       # only when rollback exists
ROLLBACK_AUTHORIZED      # only when rollback exists
ROLLED_BACK              # only when rollback exists
```

Every event binds actor, reason, metadata, timestamp, previous hash, and event
hash. The final package recomputes the chain and also verifies actor and reason
semantics, so changing a claim and coherently recomputing all outer hashes still
fails.

The package separately includes the Campaign audit chain and its external
checkpoint.

## Reproducible package

`LocalPolicyPromotionPackageManifest` includes:

- accepted v2.1 package, anchors, and receipt;
- initial and candidate Registry records;
- final active head;
- Promotion Campaign and approvals;
- optional Rollback Campaign and approvals;
- local Registry audit events and checkpoint;
- Campaign audit events and checkpoint;
- source and dependency provenance.

`LocalPolicyPromotionPackageManager` recursively verifies every nested evidence
boundary before accepting the package.

The package always states:

```text
local_policy_pointer_mutation_only = true
foundation_model_weights_updated = false
production_activation_performed = false
production_deployment_performed = false
external_rollout_performed_by_evoagent = false
upload_performed = false
official_benchmark_claimed = false
```

## Regression gates

The focused suite covers:

- complete promotion and rollback;
- authorization without pointer mutation;
- generic Campaign approval bypass;
- stale optimistic revision;
- activation crash recovery and read-only retry;
- rollback crash recovery and read-only retry;
- actor-changing retries;
- promotion-control actors attempting rollback roles;
- coherent audit rehash;
- selected-checkpoint substitution;
- approval-role substitution;
- fabricated foundation-weight or production-activation claims;
- public API identity and source invariants.

## Deliberately absent authority

v2.2 does not:

- modify foundation-model weights;
- activate a model-provider checkpoint;
- replace a production Runtime configuration;
- deploy or route production traffic;
- perform an external rollout;
- run or upload an official Benchmark;
- publish a package, tag, release, or model artifact.

A future production-deployment layer must consume the accepted v2.2 package,
perform separate environment-specific evaluation and approvals, and issue a new
authorization record. Local pointer promotion is not production deployment.
