# Shadow/Canary Release Evidence, Drift Monitoring, and Governed Rollback

## Scope

v1.9 extends the benchmark-selected Champion lifecycle with a **local release control plane**. It evaluates shadow and Canary evidence, advances stages only through explicit operations, and records a governed rollback when frozen safety, quality, segment, latency, Token, or cost gates fail.

It does not call a serving platform, route real production traffic, invoke a model provider, download a checkpoint, train a model, execute Harbor, upload results, or deploy anything to production.

```text
v1.8 ChampionDecisionPackage
    -> immutable ReleasePlan
    -> high-risk CHAMPION_RELEASE Campaign
    -> two independent approvals
    -> local shadow stage
    -> caller-hashed observable evidence
    -> deterministic stage assessment
    -> explicit stage advance
    -> Canary evidence
    -> advance / hold / rollback / ready
    -> optional high-risk CHAMPION_ROLLBACK Campaign
    -> explicit local rollback
```

## Authority boundaries

The lifecycle keeps the following operations separate:

```text
ReleasePlan created
    != release Campaign authorized

release Campaign authorized
    != shadow stage activated

stage evidence admitted
    != stage decision stored

stage decision stored
    != stage advanced

rollback recommended
    != rollback authorized

rollback authorized
    != rollback completed

local READY
    != production deployment
```

`ReleaseHead.primary_snapshot_id` always remains the incumbent snapshot. Candidate allocation records only a local, synthetic control-plane stage. It is not evidence that an external traffic router changed.

## Frozen release plan

`ReleasePlan` binds:

- the complete v1.8 Champion package hash;
- the Champion family;
- the incumbent A0 snapshot and selected A1 Challenger;
- the Champion decision hash;
- runtime configuration and Tool-contract hashes;
- source commit;
- a complete segment manifest, including protected segments;
- a consecutive shadow/Canary stage schedule;
- candidate allocation percentages;
- minimum overall and per-segment pair counts;
- observation-window limits;
- immutable drift policy;
- synthetic or external evidence provenance;
- an explicit `production_deployment_authorized=false` attestation.

The stage schedule begins with zero-percent shadow and then increases strictly through bounded Canary allocations.

## Safe evidence admission

`ReleaseEvidenceImporter` accepts only a caller-attested `release-evidence.json` under a controlled root.

Admission requires:

- a lowercase SHA-256 matching the complete raw file;
- a relative path under the controlled root;
- no symlink component or traversal segment;
- a regular non-empty bounded file;
- UTF-8 JSON;
- bounded JSON depth, node count, and array size;
- an exact, allowlisted schema;
- the exact plan ID/hash, stage, snapshot pair, allocation, segment manifest, and observation window;
- unique event IDs;
- exactly one incumbent and one Challenger observation per pair;
- derived event and pair counts.

The importer persists only bounded observable fields:

```text
event and pair IDs
stage and segment IDs
snapshot ID
success / error / safety flags
latency
input/output Token counts
cost
timestamp
content hashes
```

It does not admit prompts, raw inputs/outputs, trajectories, environment values, credentials, hidden reasoning, scratchpads, or stack traces. Known secret patterns and forbidden hidden-reasoning fields are rejected before storage.

## Stage assessment

Every admitted pair produces:

```text
delta_i = challenger_success_i - incumbent_success_i
```

`ReleaseStageGate` computes:

- incumbent and Challenger success rate;
- error-rate delta;
- Challenger safety violations;
- p95 latency and growth;
- average input/output Tokens and growth;
- average cost and growth;
- per-segment success and error rates;
- regressed segment count and fraction;
- protected-segment regressions;
- deterministic paired bootstrap confidence evidence.

The bootstrap binds confidence level, resample count, round-specific seed, observed mean, lower/upper bounds, a SHA-256 of all sample means, and an evidence hash.

## Decision policy

A stage may produce:

```text
advance
hold
rollback
ready
```

Insufficient sample, Token, or cost evidence follows the immutable policy and normally produces `hold`.

Hard gates include:

- maximum safety violations;
- protected-segment zero-regression;
- maximum regressed segment count/fraction;
- minimum quality delta and confidence lower bound;
- maximum error-rate increase;
- maximum p95 latency growth;
- maximum input/output Token growth;
- maximum cost growth.

A protected-segment or safety failure cannot be overridden by a better aggregate score.

## Persistent Release Registry

`SQLiteReleaseRegistry` persists:

- immutable plans;
- immutable evidence batches;
- immutable assessments and decisions;
- one local release head per family;
- active local stage and candidate allocation;
- release and rollback Campaign IDs;
- optimistic revision;
- SHA-256 chained audit events;
- an external checkpoint.

Equivalent evidence is reused read-only. Conflicting content under the same ID fails closed.

The controlled drift path has this local revision sequence:

```text
0 plan registered
1 release Campaign bound
2 release authorization synchronized
3 shadow activated
4 advanced to 10% Canary
5 advanced to 25% Canary
6 rollback recommended
7 rollback Campaign bound
8 rollback completed
```

The passing control reaches `ready` at the final 25% local Canary stage while the incumbent remains the primary snapshot and `production_deployment_performed=false`.

## Governance

### CHAMPION_RELEASE

The high-risk release Campaign binds:

- complete Champion package hash;
- complete ReleasePlan hash;
- exact incumbent/Challenger snapshots;
- runtime and Tool-contract hashes.

It requires two distinct approvals. The plan creator cannot approve. Campaign authorization does not activate shadow or change candidate allocation. An explicit local `start_shadow` operation completes the Campaign.

### CHAMPION_ROLLBACK

The high-risk rollback Campaign binds:

- Champion package hash;
- ReleasePlan hash;
- raw evidence batch hash;
- stage-assessment hash;
- rollback-decision hash;
- exact stage and allocation.

The decision actor and evidence producer cannot approve. Campaign authorization does not change the local release head. Only explicit `execute_rollback` restores zero candidate allocation and completes the Campaign.

## Controlled lab

`ShadowCanaryReleaseLab` runs two independently persisted scenarios.

### Drift scenario

```text
shadow      -> pass -> advance
10% Canary  -> pass -> advance
25% Canary  -> protected segment regresses
              one safety violation appears
              aggregate success delta remains 0.0
              -> rollback
```

The rollback restores:

```text
primary snapshot: A0
candidate allocation: 0%
state: rolled_back
```

### Passing scenario

```text
shadow      -> pass -> advance
10% Canary  -> pass -> advance
25% Canary  -> pass -> ready
```

`ready` is a local evidence state only. No serving platform or production deployment is invoked.

Both scenarios run twice. The second run loads and verifies the same plans, evidence, decisions, Campaigns, approvals, audit events, checkpoints, heads, and package hashes without creating duplicates.

## Reproducible package

`ReleaseEvidencePackageManifest` embeds:

- framework/source/third-party provenance;
- the complete v1.8 Champion package;
- frozen ReleasePlan and policy;
- raw evidence SHA-256 values and safe evidence;
- every assessment and decision;
- release and optional rollback Campaigns;
- approvals;
- final local release head;
- release and Campaign audit chains/checkpoints;
- explicit no-model-call/no-training/no-external-rollout/no-production-traffic/no-deployment/no-upload flags.

Verification recomputes every assessment and decision, validates Campaign fingerprints/payloads/metadata, validates approval identities against Campaign audit events, checks final pointer state, and verifies both audit chains and checkpoints.

## Security and operational limitations

- Internal hashes provide tamper evidence inside the package; production identity and external execution claims still require independently anchored signatures and receipts.
- SQLite is a single-node research backend.
- Operator identities are strings, not authenticated organizational principals.
- Synthetic observations are lifecycle fixtures, not real online metrics.
- `ready` and `rolled_back` describe the local research control plane only.
- Real release automation must integrate an authenticated serving platform, independent observability, secret management, signed evidence, and an externally protected audit anchor.