# Architecture

Updated: 2026-08-25

The canonical architecture is a **unified continual Agent**, not a collection
of independent Skill and policy demonstrations:

```text
frozen foundation model
    + Skill library
    + Router policy
    + bounded observable Memory
    + learnable Agent action policy
    + Tool runtime / Environment / Verifier
    = one immutable UnifiedAgentSnapshot
```

Every Task executes through the same runtime and produces only observable
evidence. A failure never edits the active Agent directly:

```text
Task -> route/retrieve -> act with Tools -> independent verification
     -> failed observable Trace
     -> fresh-Environment, one-component counterfactuals
     -> unique attribution or escalation
     -> immutable component candidate
     -> frozen retention / transfer / adversarial / composition evaluation
     -> independent decision
     -> explicit Registry activation or rejection
     -> next complete Agent snapshot
```

The default research scope freezes foundation-model weights. EvoAgent evolves
Skills, bounded Memory, routing and Agent policy. Model training remains a
separately governed optional lifecycle and is never implied by a benchmark or
by a changed Agent runtime configuration.

See `docs/36-unified-continual-agent-architecture.md` for the implemented
contracts, evidence boundaries and the exact benchmark/non-benchmark split.
