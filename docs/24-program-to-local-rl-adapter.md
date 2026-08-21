# Program-to-local-RL adapter

## Purpose

The v2.0 Evolution Program authorizes a bounded successor generation. It does
not authorize an optimizer process, checkpoint promotion, model activation, or
production deployment. The v2.1 adapter adds a separate offline optimization
boundary without weakening the Program contract.

This document describes an independently authored local policy experiment. It
is not a claim of foundation-model training, canonical GRPO equivalence, an
official Benchmark result, or production readiness.

## Lifecycle

```text
verified release evidence
    -> ProgramLearningSignal
    -> independent AttributionReceipt
    -> CONTINUE decision and GenerationPlan
    -> Generation Campaign evaluation and two approvals
    -> Program authorization
    -> explicit Generation start

RUNNING Generation
    + Program head and audit checkpoint
    + exact plan/signal/Attribution lineage
    -> ProgramLocalRLIntent
       optimizer_execution_authorized = false
       checkpoint_promotion_authorized = false
       production_activation_authorized = false

independent optimizer authorizer
    -> ProgramLocalRLAuthorization
       bounded iterations / rollouts / Tokens / cost
       optimizer_execution_authorized = true
       checkpoint_promotion_authorized = false
       production_activation_authorized = false

local optimizer executor
    -> LocalRLPackageManifest

exact LocalRLPackageManager
    -> recompute package hash
    -> replay optimization and numeric parameter updates
    -> recompute every frozen held-out evaluation
    -> recompute deterministic checkpoint selection
    -> verify audit chain and checkpoint

EvoagentLocalRLPackageProjector
    -> derive governed evidence only from verified native records

independent EvoagentLocalRLPackageAttestor
    -> regenerate the concrete projection
    -> NativeLocalRLPackageAttestation

independent Program result binder
    -> cross-bind native attestation and Program result
    -> AttestedProgramLocalRLBindingPackage
       checkpoint_promotion_performed = false
       production_activation_performed = false
```

## Input bindings

`ProgramLocalRLIntent` records the exact inputs available before optimizer
execution:

- Program ID and running generation ID/index;
- Program head revision and external audit checkpoint;
- Generation Campaign ID;
- GenerationPlan ID/hash;
- source learning-signal ID/hash;
- source Attribution receipt ID/hash;
- intervention layer/action;
- parent and target Agent identity hashes;
- expected child release package and ReleasePlan hashes;
- local optimizer run ID and configuration hash;
- frozen training-task-set hash;
- disjoint frozen held-out-task-set hash;
- the governed Program actor set;
- a separate intent-author identity and timestamp.

The adapter rejects a Generation that is merely planned or authorized. The
Registry must show `GENERATION_RUNNING`, and the active Program head must name
the same generation index and ID.

## Separate optimizer authorization

Program authorization cannot be reused as optimizer authorization. A separate
actor creates `ProgramLocalRLAuthorization` after the immutable intent exists.
The authorizer must differ from:

- the release evidence producer;
- causal attributor;
- CONTINUE decision/planning actor;
- Generation evaluator;
- both Generation approvers;
- the intent author.

The authorization binds a maximum number of iterations and rollouts plus Token
and cost ceilings. Token and cost ceilings cannot exceed the GenerationPlan
budget. The authorization still grants no checkpoint-promotion or activation
right.

## Result acceptance

A projected result is admissible only when all of these conditions hold:

```text
actual usage <= authorization budget
selected checkpoint != initial checkpoint
held-out reward delta > 0
held-out success delta > 0
unsafe action count = 0
held-out regression count = 0
execution starts after optimizer authorization
execution completes before authorization expiry
```

The optimizer executor must differ from all Program actors, the intent author,
and the optimizer authorizer.

## Concrete native package projection

`EvoagentLocalRLPackageProjector` accepts only the exact native classes:

```text
LocalRLPackageManifest
LocalRLPackageManager
```

It rejects subclasses and alternative managers. It calls the exact native
manager first and continues only when `manager.verify(package) is True`.

The native manager deterministically verifies or recomputes:

- the complete package hash;
- the frozen run manifest;
- optimizer rollouts, metrics, checkpoints, and numeric parameters;
- baseline evaluation;
- every retained-checkpoint held-out evaluation;
- deterministic safe checkpoint selection;
- audit-event content, ordering, hash chain, and external checkpoint.

Only after that verification does the Projector derive:

- native package ID/hash and run ID;
- optimizer configuration hash from the environment, hyperparameter, and
  training-budget hashes;
- frozen training and held-out Task-set hashes;
- initial and selected checkpoint hashes;
- optimizer result hash;
- exact selected held-out report hash;
- actual iteration and rollout usage;
- held-out mean-reward and success deltas;
- unsafe-action count;
- an independently recomputed per-Task regression count.

No caller supplies these projected metrics. A regression test changes a selected
evaluation, recomputes the outer package hash, and still requires rejection
because the native evaluations are no longer reproducible.

## Concrete native attestation

`EvoagentLocalRLPackageAttestor` regenerates the concrete projection from the
same exact package and manager. The verifier must be independent from:

- the Trainer;
- baseline and candidate Evaluators;
- checkpoint-selection actor;
- every native audit-event actor.

Verification must occur after all native evidence exists. The resulting
`NativeLocalRLPackageAttestation` is verification evidence only:

```text
checkpoint_promotion_authorized = false
production_activation_authorized = false
```

`AttestedProgramLocalRLPackageManager` then compares every projected field with
the Program binding: run/config/task hashes, package identity, checkpoints,
optimizer/evaluation evidence, resource usage, held-out deltas, safety count and
regression count.

The native verifier and final Program result binder are separate actors.

## Canonical API boundaries

Two public namespaces have distinct responsibilities and must not be mixed.

### `evoagent.local_rl`

The canonical Program-bound execution entry is:

```python
from evoagent.local_rl import ProgramLocalRLBindingManager
```

This manager owns the native execution evidence lifecycle:

```text
RUNNING Program generation
    -> ProgramLocalRLExecutionTicket
    -> exact LocalRLPackageManifest
    -> ProgramLocalRLCompletionReceipt
    -> ProgramBoundLocalRLPackageManifest
    -> atomic export / verified load
```

It binds the full Program, Campaign, approval, budget, Task, initial-checkpoint,
training, evaluation, selection, audit and provenance records. It explicitly
states that the resulting policy evidence does not satisfy the Generation
Outcome and still requires release evaluation.

`evoagent.local_rl.program_adapter` is an earlier generic compatibility module.
Its builder functions are deliberately not exported from `evoagent.local_rl`.
New callers must not treat that module as the canonical governance boundary.

### `evoagent.program_rl`

The canonical portable attestation and trust boundary is:

```python
from evoagent.program_rl import (
    ProgramLocalRLAdapter,
    EvoagentLocalRLPackageProjector,
    EvoagentLocalRLPackageAttestor,
    FullyAttestedProgramLocalRLPackageManager,
    ProgramLocalRLAcceptanceManager,
)
```

This namespace owns:

- the pre-execution optimizer intent and separate authorization;
- intervention-scope enforcement;
- exact native package verification and projection;
- independent native, schema and runtime attestations;
- recursive intermediate-stage verification;
- running-Generation evidence binding;
- fully attested evidence acceptance against independent external anchors.

A completed native package must never be converted retroactively into a
pre-execution intent. The intent must exist before optimizer execution. Native
completion evidence can only be cross-bound to that already-authorized lineage.

Neither namespace grants checkpoint promotion or production activation. A future
promotion layer must consume the accepted evidence as an input to a separate
Campaign, independent evaluation, approval, activation and rollback lifecycle.

## Deliberately absent authority

Neither the base nor attested package can:

- promote the selected checkpoint;
- replace a Champion or active policy;
- activate a model or Runtime configuration;
- deploy to production;
- start an external rollout;
- upload an official Benchmark submission;
- claim that foundation-model weights changed.

A future promotion layer must consume the attested package as evidence, perform
independent evaluation and approvals, and create a different authorization
record. Held-out improvement alone is not activation authority.

## Remaining release work

The concrete native Projector and verification-only Attestor are implemented.
The remaining release work is operational rather than another caller-metric
projection layer:

- execute exact-head Python 3.11 and 3.12 regression gates;
- execute all historical offline examples;
- build and install the Wheel in a clean environment;
- run installed local-RL and Program-bound labs twice;
- prove the second invocation is lifecycle read-only;
- normalize the stacked branch onto the verified v2.0 `main` lineage;
- keep checkpoint promotion and activation in a separate future governance
  milestone.
