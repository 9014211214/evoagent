# Demonstration Acquisition and Tamper-Evident Trace Store

## Acquisition boundary

A recording, tutorial, repository or previously generated Skill is evidence, not an approved production capability. The acquisition path is:

```text
public/synthetic source artifacts
    -> provenance, consent and secret validation
    -> structured semantic demonstration
    -> candidate Skill compilation
    -> generated acceptance cases
    -> isolated sandbox evaluation
    -> initial immutable Skill registration
```

There is no direct demonstration-to-production operation.

## Source requirements

Every `SourceArtifact` declares:

- source identifier and resource type;
- URI;
- SHA-256 checksum;
- license identifier;
- consent to process;
- trust level;
- non-secret metadata.

Unlicensed, unconsented or secret-bearing sources are rejected. `UNTRUSTED` sources produce a warning; initial registration then requires explicit warning approval in addition to sandbox success.

## Semantic steps

The core package does not decode video or perform OCR. An upstream recorder or extractor must provide structured `DemonstrationStep` objects. UI actions require a semantic target; raw coordinates without a semantic target are rejected because they cannot safely generalize across layout changes.

Tool steps require an explicit tool name. Parameter values are validated for secrets but are not copied into the compiled procedure. The generated procedure contains semantic actions and expected observable outcomes.

## Extended Skill specification

v0.6 adds:

- preconditions;
- allowed tools;
- ordered procedure;
- success criteria;
- failure handling;
- source references and generator provenance.

The compiler emits an immutable `SkillSpec` candidate and generated success/failure acceptance cases.

## Sandbox gate

Initial registration requires:

1. no blocking static finding;
2. explicit approval when warnings remain;
3. a sandbox result that belongs to the exact candidate;
4. results for the exact generated acceptance-case IDs;
5. every acceptance case to pass.

Only then does the gate call the immutable `SkillRegistry.register_initial` operation.

## Trace store

`JsonlTraceStore` stores observable `ExecutionTrace` envelopes as append-only JSONL. Each line includes:

- sequence number;
- previous record hash;
- current record hash;
- UTC timestamp;
- source and trust level;
- safety flags;
- observable task, tool and verifier data.

The internal SHA-256 chain detects line edits, insertion, reordering and deletion within the retained chain. Duplicate trace IDs are rejected. Query filters include task type, model, Skill ID/version, verifier outcome and trust level.

A valid prefix cannot reveal that later records were truncated. `TraceCheckpoint` therefore records the expected record count and head hash; storing that checkpoint outside the JSONL file allows `verify(checkpoint)` to detect tail truncation.

The store is tamper-evident, not tamper-proof. The v0.6 implementation uses an in-process lock; multi-process production use requires transactional storage or external locking.

## Hidden reasoning policy

The trace schema does not add a chain-of-thought field. Storage rejects exact hidden-reasoning keys such as `chain_of_thought`, `hidden_reasoning`, `raw_reasoning`, `private_reasoning` and `scratchpad`. Observable actions, tool calls, state changes, verifier outputs and concise explicit summaries remain allowed.

## Resource2Skill integration

The optional adapter references an external local checkout of Microsoft Resource2Skill. It generates the documented `validate-domain` command and the `skills_wiki/<domain>` / `skills_library/<domain>` paths. Domain paths must remain relative and cannot escape the external checkout. The adapter does not clone, vendor or copy upstream source. Execution is disabled by default; enabling the adapter is still insufficient to run it. Validation additionally requires an exact external authorization, a successful fixed Python version probe, and a transactional one-use ledger. The child process receives only the minimal allowlisted operating-system environment and never inherits unrelated ambient secrets.
