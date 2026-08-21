# Persistent Multi-Generation Evolution Program

## Purpose

Versions through v1.9 implement governed child lifecycles:

```text
observable bad case
    -> executable attribution
    -> Skill / Model / repair intervention
    -> independent evaluation
    -> Campaign authorization
    -> explicit promotion or activation
    -> frozen benchmark comparison
    -> Champion selection
    -> shadow / Canary release evidence
    -> ready or governed rollback
```

v2.0 closes the **outer learning loop** across those already-governed lifecycles. A verified release rollback can become bounded input to a successor generation, but it cannot directly mutate a Skill, Runtime, Tool contract, model, or Agent snapshot.

The central rule is:

> Release metrics identify where an operational regression was observed; they do not by themselves identify the causal component that must change.

## Outer-loop lifecycle

```text
v1.9 ReleaseEvidencePackage
    -> verify complete release package
    -> extract non-causal ProgramLearningSignal
    -> independent AttributionReceipt
    -> immutable GenerationPlan
    -> high-risk EVOLUTION_GENERATION Campaign
    -> two independent approvals
    -> Campaign AUTHORIZED
    -> local Program authorization synchronization
    -> separate explicit generation start
    -> verified child release package
    -> immutable GenerationOutcome
    -> deterministic ProgramDecision
       -> continue
       -> stop_success
       -> stop_budget
       -> pause
       -> escalate
       -> fail
```

Candidate generation, causal attribution, planning, evaluation, approval, authorization, start, completion, stop, deployment, and publication remain separate operations.

## Non-causal learning signal

`ReleaseFeedbackExtractor` first verifies the complete v1.9 `ReleaseEvidencePackage`. It then binds a signal to:

```text
source release package hash
source ReleasePlan hash
terminal evidence batch hash
stage assessment hash
stage decision hash
stage ID
Champion family
incumbent and Challenger snapshots
runtime configuration hash
Tool-contract hash
observable release reasons
affected and protected segments
safety violation count
evidence producer identity
```

The signal contains:

```python
causal_attribution_claimed = False
```

Only terminal `rollback` or `hold` evidence creates an automatic learning signal. `ready` is already a successful terminal outcome and does not create another repair signal.

The extractor persists no prompt, raw request, raw output, trajectory, Agent log, private payload, credential, hidden reasoning, scratchpad, or stack trace.

## Independent attribution receipt

Automatic continuation requires an independently authored `AttributionReceipt` bound to the exact signal ID and signal hash.

The receipt records:

```text
one failure layer
its matching bounded intervention action
confidence
one or more supported counterfactual experiment hashes
attributor identity
creation time
receipt hash
```

The layer/action contract is fixed:

```text
Skill     -> UPDATE_SKILL
Router    -> UPDATE_ROUTER
Tool      -> REPAIR_TOOL
Context   -> UPDATE_CONTEXT
Verifier  -> REPAIR_VERIFIER
Environment -> ESCALATE
Model       -> TRAIN_MODEL request
```

The default v2.0 automatic allowlist contains external bounded layers only:

```text
Skill
Router
Tool
Context
Verifier
```

Environment evidence escalates. Model evidence remains in the separately governed model-training and candidate-admission lifecycles; the Program does not silently execute training.

Under the default strict policy:

- attributor and release evidence producer must differ;
- confidence must meet the immutable threshold;
- exactly one supported experiment is required;
- multiple supported experiments mean causal ambiguity and escalation;
- an unsupported or non-allowlisted layer cannot open a successor Campaign.

## Immutable Program policy and budgets

`EvolutionProgramPolicy` binds:

```text
maximum generations
maximum rollbacks
maximum holds
maximum generation Campaigns
maximum cumulative evidence pairs
maximum cumulative Tokens
maximum cumulative cost
minimum attribution confidence
automatic intervention allowlist
single-supported-experiment requirement
independent-attributor requirement
independent-approval requirement
stop-on-ready behavior
safety-feedback attribution requirement
maximum consecutive non-improving generations
```

A `GenerationPlan` has its own narrower budget:

```text
maximum child packages
maximum evidence pairs
maximum Tokens
maximum cost
```

Planning reserves no external resource and performs no execution. Before a plan is accepted, its declared budget must fit inside the remaining cumulative Program budget.

Zero cost is a valid bounded zero-cost generation. No paid work is implied or performed.

## Generation identity

A generation binds both lineage and target identity:

```text
program ID
generation ID and contiguous index
parent generation ID
source signal and attribution hashes
intervention layer and action
parent Agent identity hash
target Agent identity hash
target runtime configuration hash
target Tool-contract hash
expected child release package hash
expected child ReleasePlan hash
```

The target identity is recomputed from:

```text
Champion package hash
Champion snapshot ID
runtime configuration hash
Tool-contract hash
```

A successor may therefore improve Runtime, Context, Tool contract, Router, Verifier, or Skill configuration while retaining the same underlying Champion snapshot. That must not be described as newly trained model weights.

## High-risk Generation Campaign

Every automatic successor uses a distinct:

```text
EVOLUTION_GENERATION
```

Campaign with high risk and two required approvals.

The fingerprint binds:

```text
Program policy hash
learning signal hash
attribution receipt hash
GenerationPlan hash
expected child release package hash
per-generation budget
```

The Campaign payload embeds the complete policy, signal, attribution, and plan.

The following identities cannot approve:

```text
release evidence producer
causal attributor
CONTINUE decision / generation planning actor
```

Persistent approvals are reloaded and revalidated before authorization synchronization and before explicit generation start. Direct use of the generic Campaign API cannot bypass these checks.

## Authorization is not execution

The Program head and Generation record make the separation explicit:

```text
Generation PLANNED
    -> Campaign AUTHORIZED
    -> Generation AUTHORIZED
```

At this point:

```text
active generation remains the parent
no generation has started
no child package has been consumed
no external command has run
```

Only an explicit optimistic operation changes the local state to:

```text
GENERATION_RUNNING
```

Completion is another separate operation and requires the exact child package declared in the authorized plan.

## Persistent Program Registry

`SQLiteEvolutionProgramRepository` stores:

```text
immutable Program policy
one revisioned Program head
contiguous Generation records
learning signals
attribution receipts
Program decisions
Campaign bindings
hash-chained audit events
external audit checkpoint
```

Program states:

```text
planned
running
generation_authorized
generation_running
completed
paused
budget_exhausted
escalated
failed
```

Generation states:

```text
observed
planned
authorized
running
completed
rolled_back
held
escalated
```

Exactly one generation is active in the local Program head. New generations must be contiguous and reference the current active parent.

Optimistic revisions protect:

```text
Campaign binding
authorization synchronization
explicit generation start
generation completion
Program decision storage
```

Exact retries are read-only. A conflicting payload under an existing ID fails closed.

## Deterministic decisions

`EvolutionProgramGate` returns exactly one action:

### `continue`

Requires all of the following:

- terminal generation is rollback or hold;
- remaining Program budget exists;
- consecutive non-improvement limit is not exceeded;
- an exact independent attribution receipt exists;
- the attributed layer is allowlisted;
- confidence passes;
- strict policy has exactly one supported experiment.

### `stop_success`

A verified child release package reaches local `ready` and `stop_on_ready=true`.

### `stop_budget`

No successor is allowed because a generation, rollback, hold, Campaign, cumulative resource, or consecutive non-improvement limit is exhausted.

### `pause`

Evidence exists but causal attribution or explicit owner direction is missing. A ready outcome with `stop_on_ready=false` also pauses instead of silently optimizing past readiness.

### `escalate`

Attribution is ambiguous, non-independent, low-confidence, outside the allowlist, or otherwise unsafe for automatic continuation.

### `fail`

Reserved for an explicit terminal failure recorded by the control plane.

## Controlled two-generation result

The v2.0 lab composes the independently authored v1.9 synthetic release packages.

### Generation 0

```text
shadow      -> advance
10% Canary  -> advance
25% Canary  -> rollback
```

Observable feedback contains:

```text
protected-segment regression
one safety violation
```

This is stored without a causal claim.

An independent synthetic attribution receipt identifies:

```text
layer: Context
intervention: UPDATE_CONTEXT
one supported replacement-context-policy experiment
```

### Generation 1

Two independent approvals authorize the exact plan. Authorization leaves Generation 0 active. An explicit local start then consumes the predeclared passing v1.9 package:

```text
shadow      -> advance
10% Canary  -> advance
25% Canary  -> ready
```

The Program ends with:

```text
decisions: continue, stop_success
generations: rolled_back, completed
final state: completed
one Generation Campaign
two independent approvals
```

Generation 0 and Generation 1 use the same Champion snapshot but different governed Runtime/config identity hashes. No model training or checkpoint work occurs.

## Negative controls

### Generation budget exhausted

A policy with `max_generations=1` consumes Generation 0 and returns:

```text
stop_budget
budget_exhausted
```

It creates no Generation Campaign.

### Ambiguous attribution

A receipt with two supported experiments returns:

```text
escalate
escalated
```

It creates no Generation Campaign.

### Missing attribution

Rollback or hold evidence without an independent receipt returns:

```text
pause
```

It cannot open an automatic successor Campaign.

## Reproducible package

`EvolutionProgramPackageManifest` binds:

```text
framework/source/third-party provenance
complete drift and passing v1.9 release packages
Program policy
learning signal
attribution receipt
Generation 0 and Generation 1 records
Program decisions
completed Generation Campaign
approvals
final Program head
Program audit events and checkpoint
Campaign audit events and checkpoint
budget-exhaustion control
ambiguous-attribution control
```

The package fixes all external-work claims to false:

```text
external_model_call_performed_by_evoagent
training_executed_by_evoagent
external_rollout_performed_by_evoagent
production_traffic_observed_by_evoagent
production_deployment_performed
external_rollback_performed
upload_performed
official_benchmark_claimed
```

Verification recomputes release-package evidence, signals, outcomes, decisions, target identity, Campaign fingerprint and payload, approval identities/reasons/times, Program head counters, event payloads, event actors, both hash chains, and both checkpoints.

Coherently rehashed changes to the following are rejected:

```text
feedback reasons or protected segment
safety count
attribution layer/action/experiment
CONTINUE decision actor
GenerationPlan parent or target identity
runtime/Tool hashes
budget
child package binding
Program action or head
approval identity/reason/time
Campaign fingerprint/payload
Program or Campaign event content
audit-tail truncation with a rewritten checkpoint
```

## Current boundary

The lab and Program are local, synthetic control-plane experiments. They perform no:

```text
real model training or Agentic RL rollout
model-provider call
checkpoint creation, download, load, or execution
serving-platform call
production traffic routing
external rollout or rollback
production deployment
Harbor or Terminal-Bench execution/upload
GPU or paid work
public package or repository release
```

The Program creates governed plans and verifies declared child evidence; it is not autonomous production authority.
