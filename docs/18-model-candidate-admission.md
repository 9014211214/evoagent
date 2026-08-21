# External Model Candidate Admission, Activation, and Rollback

`v1.5.0` closes the lifecycle after the governed training-intent package introduced in `v1.4.0`.

It does **not** train a model. It accepts metadata describing a candidate produced elsewhere, verifies that the metadata and receipt remain inside the previously governed intent, evaluates an explicit candidate adapter independently, and keeps authorization, activation, and rollback as separate audited operations.

## Lifecycle

```text
v1.4 ModelEvolutionPackage
    -> externally supplied Candidate Manifest
    -> externally supplied Training Receipt
    -> exact admission checks
    -> immutable CANDIDATE record
    -> independent frozen evaluation
       -> held-out
       -> replay
       -> retention
       -> safety
       -> deterministic resource budget
    -> evaluation-bound MODEL_ACTIVATION Campaign
    -> two independent approvals
    -> AUTHORIZED Registry state
    -> explicit active-pointer operation
    -> ACTIVE candidate / SUPERSEDED parent
    -> explicit rollback
    -> ROLLED_BACK candidate / ACTIVE parent
    -> reproducible admission package
```

Each arrow is a distinct operation. Reaching `AUTHORIZED` cannot alter the active model pointer.

## Candidate admission

`ExternalModelCandidateManifest` binds:

- Model family, candidate ID, version, and base model ID;
- artifact URI and caller-supplied artifact SHA-256;
- config and tokenizer hashes;
- training method;
- evidence manifest hash;
- complete held-out Task ID tuple;
- original Model Campaign ID;
- external authorization reference;
- source and training commits;
- generator identity, license, and creation time;
- immutable manifest hash;
- `training_executed_by_evoagent = false`.

`ExternalTrainingReceipt` separately binds:

- candidate and trainer identities;
- the same governed Campaign, base model, method, evidence manifest, and held-out Tasks;
- authorization-reference hash;
- declared used budget;
- artifact SHA-256;
- start and completion times;
- immutable receipt hash.

A real receipt must explicitly attest that external training occurred. The included laboratory uses `synthetic_lifecycle_fixture`, which is required to set `external_training_attested = false`. The fixture demonstrates lifecycle wiring only.

Admission rejects a candidate when any governed field widens or changes, including:

```text
base model
training method
evidence manifest
held-out Task IDs
training-intent Campaign
source commit
artifact hash
authorization reference
trainer identity
GPU / rollout / token / cost budget
```

The default authorization verifier rejects every reference. A caller must supply an externally anchored allowlist or another implementation of `TrainingAuthorizationVerifier`.

## Artifact boundary

Admission never downloads, imports, or executes candidate bytes.

Allowed URI schemes are:

```text
https
s3
gs
hf
synthetic
```

The schema rejects local-file URIs, credentials embedded in URIs, query strings, fragments, traversal segments, secret patterns, hidden-reasoning fields, stack traces, and unsupported formats.

An accepted URI and SHA-256 are metadata claims supplied by the caller. Internal hashing proves package self-consistency; it is not external proof that remote bytes exist or match the declared digest.

## Independent evaluation adapter

The evaluator requires an explicit `ModelCandidateAdapter`. It will not infer that a manifest can be executed and will not load checkpoint bytes.

The adapter binds:

```text
adapter ID
candidate ID
candidate manifest hash
generator identity
synthetic/non-synthetic declaration
adapter hash
```

The trainer and evaluator identities must differ. The adapter must match the exact candidate manifest.

The repository includes `SyntheticModelCandidateAdapter` only for deterministic lifecycle tests. Its profiles are:

```text
passing
regressing
unsafe
over_budget
```

These profiles are synthetic policies, not trained checkpoints and not evidence of model intelligence.

## Frozen suites and metrics

`ModelEvaluationSuite` contains complete immutable Task objects for four disjoint categories:

- `held_out`: the exact v1.4 held-out Tasks;
- `replay`: selected evidence Tasks replayed outside training;
- `retention`: a baseline capability that the parent already passes;
- `safety`: a protected-document adversarial Task.

Every run freezes the Task hash, model binding, Runtime limits, Tool contract, Verifier, seed, and resource budget. A mutated Task or mismatched held-out tuple is rejected.

The report records per-Task base and candidate results plus:

```text
held-out base score
held-out candidate score
held-out improvement
replay candidate score
retention candidate score
safety candidate score
regression count
forgetting rate
safety violation count
base and candidate usage
Tool-call / token / cost deltas
candidate budget compliance
```

The report schema recomputes these aggregates from per-Task results. Rehashing a modified aggregate cannot make it valid.

## Activation gate

`ModelActivationPolicy` rejects a candidate when any configured gate fails:

- held-out improvement below threshold;
- replay, retention, or safety score below threshold;
- too many regressions;
- excessive forgetting rate;
- any disallowed safety violation;
- frozen evaluation budget exceeded.

A passing report creates a separate high-risk `MODEL_ACTIVATION` Campaign. Its fingerprint binds the candidate manifest, receipt, v1.4 package, evaluation report, and activation decision hashes.

The Trainer, Evaluator, and decision actor cannot approve this Campaign. Two other distinct approvers are required.

## Registry state and active pointer

`SQLiteModelRegistry` persists immutable version records and one optimistic active pointer.

```text
initial ACTIVE
candidate CANDIDATE
candidate EVALUATED
candidate AUTHORIZED
candidate ACTIVE / initial SUPERSEDED
candidate ROLLED_BACK / initial ACTIVE
```

Candidate admission, evaluation, Campaign approval, and synchronization to `AUTHORIZED` all leave the initial model active.

Activation requires:

- exact `AUTHORIZED` candidate record;
- exact authorized Campaign payload;
- passing report and decision;
- candidate parent equal to the current active model;
- expected active revision.

The first activation advances revision `0 -> 1`. The controlled rollback advances revision `1 -> 2`. Stale revisions fail closed.

## Audit and package

Registry events are hash chained:

```text
REGISTERED
CANDIDATE_ADMITTED
EVALUATED
AUTHORIZED
ACTIVATED
ROLLED_BACK
```

The reproducible package contains:

- initial and candidate manifests;
- training receipt;
- complete frozen suite and per-Task report;
- activation decision;
- completed activation Campaign and approvals;
- Campaign events and checkpoint;
- final Model Registry records, events, and checkpoint;
- active model and revision after activation and rollback;
- framework, source, and third-party lock metadata;
- package SHA-256;
- explicit no-download/no-load/no-training/no-external-execution flags.

Verification recomputes the Campaign fingerprint, report aggregates, decision, adapter binding, audit chains, event transitions, exact model pointer transitions, and final Registry statuses.

## Idempotency

The laboratory's second run only loads and verifies the existing training-intent package, Model Registry, Campaign repository, approvals, audit events, and final package.

It must add no duplicate:

```text
candidate
receipt binding
evaluation
Campaign
approval
activation event
rollback event
```

The Model and Campaign checkpoints and the package hash remain unchanged.

## Laboratory claims

The included laboratory proves:

- metadata admission controls are wired;
- frozen deterministic evaluation is wired;
- independent approval boundaries are enforced;
- `AUTHORIZED` remains separate from activation;
- optimistic activation and rollback are persisted and audited;
- restart and second-run verification are idempotent.

It does not prove:

- that external training occurred;
- that a real checkpoint exists;
- that remote bytes match a declared hash;
- that a model improved on a public benchmark;
- that deployment is safe;
- that any production system was modified.

## Non-goals

- executing SFT, DPO, GRPO, or another training algorithm;
- running Agentic RL rollouts;
- downloading or loading model weights;
- invoking Harbor, ml-intern, or a model provider;
- GPU or paid work;
- production deployment;
- official benchmark submission;
- public release, package publication, Git tag, or license selection.
