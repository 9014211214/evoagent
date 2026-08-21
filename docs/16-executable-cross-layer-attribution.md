# Executable Cross-Layer Fault Attribution and Intervention Dispatch

## Purpose

`v1.3.0` moves every supported failure layer from prefilled synthetic experiment outcomes to **actual local Tool-Agent executions**.

Every baseline and counterfactual is an `ExecutionTrace` produced by the bounded Runtime. For each scenario the framework:

```text
inject one failure layer
    -> run an actual failed baseline Trace
    -> replay the same frozen Task seven times
    -> change one declared component per replay
    -> aggregate causal evidence
    -> dispatch a bounded intervention
```

The standard experiments are:

```text
REPLACE_SKILL
FORCE_ROUTER
REPLAY_TOOL
COMPLETE_CONTEXT
ORACLE_VERIFIER
RESET_ENVIRONMENT
REFERENCE_MODEL
```

Every replay receives a freshly reset local environment. Task ID, Task input, expected outcome, deterministic seed, Tool contract, Runtime limits, Trace schema, and all non-intervened components remain fixed.

A Context intervention changes only the policy-visible context view; it does not rewrite the frozen Task or verifier expectation.

## Executable scenarios

### Skill

The selected Skill lacks `inspect_before_write` for a protected document.

```text
baseline:
write first -> protected write rejected -> failure

replace_skill:
add inspect_before_write -> inspect -> block safely -> success
```

Only `replace_skill` succeeds.

```text
root cause: SKILL
action: UPDATE_SKILL
```

### Router

Two Skills exist:

```text
safe_document_writer:
- inspect_before_write
- verify_after_write

unsafe_document_writer:
- verify_after_write
```

The correct Skill already exists, but the Router selects the unsafe one. The Skill replay does not patch the wrongly routed artifact as a workaround. Only `force_router` selects the existing correct Skill and succeeds.

```text
root cause: ROUTER
action: UPDATE_ROUTER
```

This distinguishes missing Skill content from incorrect Skill selection.

### Tool

Skill, route, Context, policy, Environment, and Verifier are valid. The injected `write_document` backend returns:

```text
tool_backend_failure
```

Only `replay_tool` with the reference Tool implementation succeeds.

```text
root cause: TOOL
action: REPAIR_TOOL
```

### Context

The frozen Task and independent Verifier retain the required document `content` in every run. An injected Context Provider withholds that field only from the policy-visible view, so the policy returns:

```text
configuration_error
```

No Tool is called. `complete_context` restores the policy-visible field while preserving the exact same Task input and expected outcome.

```text
root cause: CONTEXT
action: UPDATE_CONTEXT
```

### Verifier

The policy executes correctly and final document state is correct, but an injected Verifier returns:

```text
verifier_fault: false_negative
```

Only `oracle_verifier` succeeds.

```text
root cause: VERIFIER
action: REPAIR_VERIFIER
```

A correct final answer or final state alone therefore does not prove the Verifier is correct.

### Environment

The Task specifies an empty initial state. The injected reset Environment creates a protected conflicting target document. A correct safety-aware policy blocks rather than overwriting it, causing the requested create task to fail.

Only `reset_environment` succeeds.

```text
root cause: ENVIRONMENT
controller action: ESCALATE
automatic mutation Ticket: none
```

Environment repair is deliberately not automated in this milestone. The Controller action and absence of a Ticket are the binding controls.

### Model

Skill, Router, Tool, Context, Environment, and Verifier are valid. The injected policy cannot produce the required action and returns:

```text
model_capability_failure
```

All external-layer interventions remain failed. Only `reference_model` succeeds.

```text
root cause: MODEL
action: TRAIN_MODEL
```

The result is only an improvement Ticket. No training backend, Agentic RL run, GPU job, checkpoint, model candidate, or deployment is created.

## Controlled component matrix

| Fault | Only successful replay | Controller action |
|---|---|---|
| Skill | `replace_skill` | `UPDATE_SKILL` |
| Router | `force_router` | `UPDATE_ROUTER` |
| Tool | `replay_tool` | `REPAIR_TOOL` |
| Context | `complete_context` | `UPDATE_CONTEXT` |
| Verifier | `oracle_verifier` | `REPAIR_VERIFIER` |
| Environment | `reset_environment` | `ESCALATE` |
| Model | `reference_model` | `TRAIN_MODEL` |

A single-layer scenario must produce exactly one successful controlled intervention.

## Conflicting explanations

The ambiguity scenario combines Skill and Router faults:

- the selected Skill lacks `inspect_before_write`;
- a correct safe Skill also exists but is not selected.

Both interventions succeed:

```text
replace_skill -> success
force_router  -> success
```

The framework must not choose one mutation arbitrarily:

```text
root cause: UNKNOWN
actionable: false
conflict action: escalate
mutation Ticket: none
```

## Intervention dispatch

`EvolutionController` maps the attribution result to:

```text
Skill       -> UPDATE_SKILL
Router      -> UPDATE_ROUTER
Tool        -> REPAIR_TOOL
Context     -> UPDATE_CONTEXT
Verifier    -> REPAIR_VERIFIER
Environment -> ESCALATE
Model       -> TRAIN_MODEL
```

Each non-environment actionable result creates an `EvolutionTicket` containing:

- exact target layer;
- exact target artifact or capability;
- baseline evidence Trace ID;
- proposed action;
- expected benefit;
- required held-out, regression, and safety evaluation.

Environment and ambiguous results create no automatic mutation Ticket.

## Repeatability

The complete seven-layer matrix plus the conflict scenario runs twice. The following must match:

- scenario IDs and injected layers;
- baseline Trace IDs;
- root-cause layers;
- recommended and dispatched actions;
- successful experiments;
- counterfactual Trace IDs;
- serialized Ticket contents.

Wall-clock duration is excluded from the deterministic signature.

## Observable evidence boundary

The matrix records:

- structured Agent actions;
- typed Tool results;
- final output;
- independent Verifier evidence;
- model and Skill identifiers;
- step and Tool-call counts;
- state fingerprints.

It does not persist:

- chain-of-thought;
- scratchpads;
- stack traces;
- credentials;
- company data;
- external model output.

The matrix uses zero LLM tokens, zero external cost, and no external execution.

## Local command

```bash
python examples/executable_cross_layer_matrix.py
```

Expected shape:

```text
fault=skill       attributed=skill       action=update_skill
fault=router      attributed=router      action=update_router
fault=tool        attributed=tool        action=repair_tool
fault=context     attributed=context     action=update_context
fault=verifier    attributed=verifier    action=repair_verifier
fault=environment attributed=environment action=escalate
fault=model       attributed=model       action=train_model
conflict action: escalate
repeatable: True
external execution performed: False
```

## Boundaries

This milestone does not:

- train or deploy a model;
- execute Agentic RL;
- claim that the reference policy is a stronger foundation model;
- repair an Environment automatically;
- execute Harbor or ml-intern;
- call an external model provider;
- automate a browser or desktop;
- use private business data;
- publish an artifact;
- submit an official benchmark;
- change repository visibility;
- select a public core-code license.
