# Persistent Closed-Loop Evolution Supervisor

## Purpose

The v1.6 Supervisor connects causal bad-case diagnosis to the already governed Skill and Model lifecycles. It is an orchestration and evidence layer, not a new mutation engine.

The core contract is:

```text
verified observable failure
    -> executable counterfactual attribution
    -> immutable Supervisor case
    -> deterministic track routing
    -> bounded idempotent executor
    -> existing governed lifecycle
    -> terminal outcome
    -> persistent audit and package
```

The Supervisor cannot bypass any child lifecycle gate.

## Separation of authority

The following remain separate operations:

```text
case admission
case routing
executor claim
candidate generation
evaluation
approval
authorization
Skill promotion
Model activation
Model rollback
production deployment
publication
benchmark upload
```

A completed Supervisor case is evidence that a child lifecycle reached its required terminal state. It is not an alternative authorization mechanism.

### Skill track

A completed Skill outcome must attest:

```text
actual Skill attribution
immutable candidate
frozen disjoint evaluation
independent approval
explicit promotion
active-version verification
```

The v1.6 controlled executor delegates to `AutomaticLocalToolEvolutionLab`; it does not directly call a backend patch or Registry promotion method.

### Model track

A completed Model outcome must attest:

```text
actual Model attribution
v1.4 governed evidence package
external Candidate admission
independent frozen evaluation
activation-bound Campaign
two independent approvals
Registry AUTHORIZED
separate explicit activation
separate explicit rollback
rollback pointer and revision verification
```

The outcome also remains explicit that evoagent did not execute model training and did not load checkpoint bytes.

## Case routing

Routing is derived only from the persisted `EvolutionAction`, causal `FailureLayer`, trust level, and safety flags.

| Failure layer | Action | Track |
|---|---|---|
| None | `NO_ACTION` | none |
| Skill | `CREATE_SKILL`, `UPDATE_SKILL` | Skill |
| Model | `TRAIN_MODEL` | Model |
| Router | `UPDATE_ROUTER` | external repair |
| Tool | `REPAIR_TOOL` | external repair |
| Context | `UPDATE_CONTEXT` | external repair |
| Verifier | `REPAIR_VERIFIER` | external repair |
| Environment, Unknown, Safety | `ESCALATE` | escalation |
| Any untrusted or safety-flagged evidence | any | quarantine |

An action/layer mismatch fails closed. The Supervisor does not infer a different root cause in order to make a case executable.

## Persistent repository

`SQLiteSupervisorRepository` stores one run policy and deterministic case records.

### Run state

```text
OPEN
    -> RUNNING
    -> COMPLETED
    -> COMPLETED_WITH_ESCALATIONS
    -> BLOCKED
    -> QUARANTINED
    -> BUDGET_EXHAUSTED
    -> FAILED
```

Terminal states cannot return to `RUNNING` automatically.

### Case state

```text
PENDING
    -> RUNNING
    -> COMPLETED
    -> BLOCKED
    -> ESCALATED
    -> QUARANTINED
    -> FAILED
```

A terminal case has exactly one immutable `SupervisorOutcome`. A `RUNNING` case left by an interrupted process requires explicit operator recovery; it is not silently rerun.

### Idempotency

Case IDs are deterministic and bind a complete `case_hash` containing:

```text
Trace ID
Task ID
Failure layer
Evolution action
Attribution hash
Evidence hash
Source
Trust level
Safety flags
Creation time
```

An identical repeated case is reused. A duplicate ID with different content is rejected.

A terminal run can be resumed only with the exact same case set and case payloads. The second controlled lab invocation creates no additional case, claim, outcome, or run-transition event.

## Budgets

The immutable policy contains:

```text
maximum total cases
maximum processing rounds
maximum Skill executions
maximum Model executions
maximum external-repair Tickets
```

Track execution budgets count only cases that actually reached an executor identity. A second Skill case can be recorded as `BLOCKED` when the Skill execution budget has already been consumed; that blocked case does not retroactively violate the first execution budget.

Budgets are checked before executor invocation. Budget exhaustion becomes a durable terminal outcome and a precise `BUDGET_EXHAUSTED` run status.

## Quarantine and escalation

### Quarantine

Any untrusted case or case with safety flags enters `QUARANTINE`. With the default policy, the first quarantined case stops all later automatic work in the batch.

Quarantine is not a repair strategy. It preserves evidence for investigation while preventing automatic mutation.

### Escalation

Environment and ambiguous failures enter the escalation track. Escalation records a terminal outcome and creates no Skill, Model, Router, Tool, Context, or Verifier mutation artifact.

The controlled mixed run therefore ends in:

```text
COMPLETED_WITH_ESCALATIONS
```

because the Skill and Model tracks complete while the Environment case remains an explicit human task.

## Audit chain

Every persistent transition creates a SHA-256 chained event:

```text
RUN_CREATED
RUN_STATUS_CHANGED
CASE_ADMITTED
CASE_ROUTED
CASE_CLAIMED
CASE_COMPLETED / CASE_BLOCKED / CASE_ESCALATED /
CASE_QUARANTINED / CASE_FAILED
```

Each event binds:

```text
sequence
event ID
run ID
optional case ID
event type
actor ID
from/to status
payload
creation time
previous event hash
```

An external `SupervisorCheckpoint` records the final event count and head hash. This detects event edits and tail truncation.

## Closed-loop package

`ClosedLoopEvolutionPackageManifest` contains:

```text
framework version
source repository and commit
third-party lock hash
immutable Supervisor policy
terminal run record
all cases and outcomes
child lifecycle references and SHA-256 values
Supervisor events
Supervisor checkpoint
frozen score summary
explicit no-training/no-external-execution/no-deployment flags
```

Validation recomputes:

```text
action-to-track routing
case and outcome hashes
run status from terminal cases
per-track budgets
Skill and Model score bindings
composite initial/final score and gain
audit event hashes and checkpoint
```

A package cannot be repaired merely by recomputing its outer hash after changing an inner outcome, run status, score, budget, or audit tail.

## Controlled experiment

The lab selects three actual results from `ExecutableCrossLayerAttributionLab`:

```text
Skill       -> UPDATE_SKILL -> Skill track
Model       -> TRAIN_MODEL  -> Model track
Environment -> ESCALATE     -> escalation track
```

Expected result:

```text
Skill A0/A1:       0.5 -> 1.0
Model held-out:    0.0 -> 1.0
Composite:         0.25 -> 1.0
Composite gain:    0.75
Escalations:       1
Cases:             3
Supervisor events: 15
Run status:        completed_with_escalations
```

These scores validate deterministic lifecycle composition. They do not claim that a foundation model was trained or improved.

```bash
python examples/closed_loop_supervisor.py
```

## Restart contract

The second run must verify and reuse:

```text
same Supervisor policy
same three case hashes
same terminal outcomes
same Skill checkpoint hashes
same Model training-intent package hash
same Model admission package hash
same score summary
same audit checkpoint
same closed-loop package hash
```

Child presentation fields such as `resumed` and phase lists are not artifact identity. Restart verification uses stable Registry checkpoints, child package hashes, identifiers, and score values.

## Security and publication boundaries

The controlled Supervisor performs no:

```text
real SFT, DPO, GRPO, or Agentic RL rollout
checkpoint creation, download, deserialization, or execution
model-provider call
Harbor or ml-intern subprocess
GPU or paid task
production deployment
result upload
official benchmark submission
repository visibility change
Git tag or GitHub Release
package publication
license selection
```

Only public, synthetic, licensed, or independently authored resources may enter this lab.
