# Governed composite Agent snapshot Registry

## Milestone position

This is the first v2.3 implementation slice. It is stacked on the v2.2
local-policy Promotion/Activation branch and therefore depends on the complete
Draft order:

```text
v2.0 Program control plane
    -> v2.1 actual local Agentic-RL optimizer
    -> v2.2 governed local-policy Promotion and Rollback
    -> v2.3 integrated multi-track Agent evolution
```

The current slice establishes the composite state boundary required by the
integrated loop. It does not yet claim that the full Supervisor, mixed-case
scheduler, composite evaluator or final v2.3 package is complete.

## Why a separate composite pointer is required

A Skill Registry and a local-policy Registry each own an independently governed
active pointer. Updating either component must not silently redefine the active
Agent. Otherwise a component promotion could change production-facing Agent
identity without a separately reviewable lineage event.

v2.3 therefore introduces one explicit composite lineage:

```text
A0 = active Skill S0 + active local policy P0
A1 = active Skill S1 + unchanged local policy P0
A2 = unchanged Skill S1 + active local policy P1
```

The component mutation happens first. A separate composite manifest is then
created from the two live Registries and committed with an optimistic expected
revision. Before that commit, the composite pointer remains unchanged.

## Immutable component bindings

`SkillComponentBinding` stores:

- Skill ID;
- active version;
- recomputed immutable content hash;
- active Skill revision.

`LocalPolicyComponentBinding` stores:

- local-policy family and policy IDs;
- active checkpoint hash;
- active local-policy revision.

Bindings are created from active Registry records. The service re-reads both
Registries before a composite commit and rejects a manifest when either live
pointer has moved.

## Snapshot manifest

Every `CompositeSnapshotManifest` binds:

- lineage, snapshot and direct-parent IDs;
- contiguous round index;
- exact Skill and local-policy bindings;
- triggering case IDs;
- attribution/decision and component-package hashes;
- frozen Runtime, Tool, verifier, Task-manifest and budget hashes;
- creator and timezone-aware creation time;
- a canonical SHA-256 manifest hash;
- explicit false authority flags for foundation-weight updates, production
  activation/deployment and external rollout.

Round zero has no parent and claims no evolution evidence. Every later snapshot
requires all three source-evidence sets.

## One-component transition rule

A child snapshot must change exactly one governed component:

```text
Skill transition:
    same Skill ID
    active Skill revision + 1
    changed Skill version and content hash
    exact unchanged local-policy binding

Local-policy transition:
    same local-policy family
    active policy revision + 1
    changed policy ID and checkpoint hash
    exact unchanged Skill binding
```

Runtime, Tool, verifier, Task and budget contracts remain frozen across the
lineage. Changing neither or both components fails closed.

## Explicit optimistic commit

The Registry stores immutable snapshots and one active head. A child commit
requires:

```text
manifest.parent_snapshot_id == current active snapshot
manifest.round_index == parent.round_index + 1
current head revision == expected_active_revision
manifest creator != pointer commit actor
manifest component bindings == current live component pointers
exactly one governed component changed
```

The transaction:

```text
parent ACTIVE -> SUPERSEDED
child         -> ACTIVE
head parent   -> child
revision n    -> n + 1
append COMMITTED audit event
```

An exact retry by the original commit actor and original expected revision is
read-only. A changed actor, stale revision, wrong parent, reused conflicting ID,
changed frozen contract or moved live component fails closed.

## Audit and persistence

Each lineage has its own SHA-256 chain. Events bind:

- sequence and event ID;
- REGISTERED or COMMITTED semantic type;
- from/to snapshot IDs;
- actor and reason;
- manifest and parent hashes;
- changed component;
- active revision before the commit;
- timestamp, previous hash and event hash.

`verify_state()` recomputes the contiguous parent lineage, component-transition
semantics, statuses, active pointer, optimistic revision, chronology, event hash
chain and event meanings. Event modification and audit-tail truncation are
rejected.

## Current controlled contract

The focused tests establish:

- A0 -> A1 -> A2 with revisions 0 -> 1 -> 2;
- component changes do not silently move the composite pointer;
- exact commit retries are read-only;
- actor-changing retry, stale revision and self-commit are rejected;
- wrong-parent and two-component changes are rejected;
- Skill content hashes are recomputed from immutable specifications;
- live component pointer drift invalidates a prepared manifest;
- the attributed track must equal the actually changed component;
- audit modification and tail truncation are detected.

## Deliberately absent authority

This slice does not:

- run the complete mixed-case Supervisor;
- train an LLM or modify foundation-model weights;
- call an external model provider;
- activate or deploy a production Runtime;
- route production traffic;
- execute Harbor, ml-intern or an official Benchmark;
- publish a package, release or tag;
- change repository visibility or license.

The only new mutable object is the isolated composite active-snapshot pointer,
and that pointer changes only through the explicit optimistic commit contract.
