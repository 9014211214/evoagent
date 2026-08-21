# End-to-End Evolution Cycle Service

## Purpose

`EvolutionCycleService` connects the previously independent lifecycle components without removing their safety boundaries:

```text
observable execution
    -> append to tamper-evident Trace store
    -> success / quarantine / failure detection
    -> counterfactual attribution
    -> intervention decision
       -> immutable Skill candidate
       -> external repair ticket
       -> model-evidence cluster
       -> bounded model ticket and dry-run candidate
```

The service creates evidence, tickets and candidates. It never promotes a Skill, executes an unapproved training backend, or deploys a model.

## Ingestion first

Every accepted observable trace is written before diagnosis. Duplicate trace IDs and hidden-reasoning fields are rejected by the Trace store. This provides a stable evidence reference for every downstream ticket or candidate.

## Early quarantine

The cycle stops before attribution when:

- the trace trust level is `UNTRUSTED`; or
- a configured blocking safety flag is present, such as prompt injection, secret leakage, policy violation or suspected training-data poisoning.

Quarantined traces remain auditable but do not modify Skills or contribute to model-training clusters.

## Skill path

A Skill failure must pass counterfactual attribution. The executed Skill ID/version must match the active immutable registry version. Stale traces are escalated rather than patched.

The v0.7 `StructuredVerifierSkillBackend` accepts only machine-readable evidence of the form:

```text
missing_skill_rule: <safe_rule_identifier>
```

It emits a `SkillPatch`, creates the next unused semantic version as `CANDIDATE`, and records the parent version. The active pointer is unchanged until a separate frozen evaluation and promotion step.

## Model path

A verified model failure is first grouped by base model and capability cluster. Planning is blocked until both thresholds are met:

- minimum verified trace count;
- minimum distinct task count.

Repeated attempts on one task therefore cannot masquerade as a general capability gap. Untrusted traces never enter the accumulator.

When the cluster is ready, explicit model-evolution settings provide target metrics, dataset signals, permitted methods, budgets, replay environment and safety constraints. The existing v0.4 ticket factory rechecks that Skill, Router, Tool, Context, Verifier and Environment explanations were ruled out. An authorized backend can then create a candidate; the default example uses a non-executing ml-intern task package.

## Other layers

Verified Router, Tool, Context and Verifier failures create auditable intervention tickets. v0.7 does not pretend to repair these layers automatically.

## Status model

A cycle returns one of:

- `NO_ACTION`
- `QUARANTINED`
- `ESCALATED`
- `TICKET_CREATED`
- `SKILL_CANDIDATE`
- `MODEL_EVIDENCE_ACCUMULATED`
- `MODEL_CANDIDATE`

The result includes the stored trace hash plus the relevant attribution, decision, ticket, patch, evidence cluster or candidate.

## Current limitations

- Skill patch generation is deterministic and deliberately narrow; it is not a general Skill-writing model.
- Model evidence is in-memory in v0.7.
- A later trace after a candidate may create another candidate; candidate deduplication and campaign scheduling remain future work.
- Promotion/deployment and real paid training remain outside the service.
