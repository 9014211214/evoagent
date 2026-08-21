# v2.3 Integrated Multi-Track Evolution

## Scope

v2.3 closes one controlled, persistent, restart-safe loop across two independently governed component tracks:

```text
Frozen A0 evaluation
    -> observable failed Tasks
    -> bounded counterfactual attribution
    -> persistent mixed-track Case queue
    -> one governed Skill intervention
    -> explicit composite commit A1
    -> one governed local-policy optimization and Promotion
    -> explicit composite commit A2
    -> frozen evaluation
    -> deterministic STOP
```

The target controlled score sequence is:

```text
A0 = Skill S0 + local policy P0 = 0.25
A1 = Skill S1 + local policy P0 = 0.50
A2 = Skill S1 + local policy P1 = 1.00
```

The stop sequence is:

```text
A0: CONTINUE
A1: CONTINUE
A2: STOP
```

## What actually changes

### Skill track

The Skill executor runs the existing local Tool evolution lifecycle:

1. reproduce one verified protected-document bad case;
2. perform bounded causal attribution;
3. generate the minimal `inspect_before_write` Skill patch;
4. evaluate S0 and S1 on a frozen held-out Task manifest;
5. require strict improvement and zero regression;
6. require an authorized Skill Campaign;
7. promote S1 in the persistent Skill Registry.

The integrated result is derived from the active Registry record, the immutable evaluation decision, the Skill audit checkpoint, the Campaign audit checkpoint and the Trace checkpoint. The Supervisor cannot manufacture a successful Skill result.

### Local-policy track

The local-policy executor runs the real bounded local Agentic-RL implementation from v2.1 and the Promotion lifecycle from v2.2:

1. replay the governed Evolution Program to one externally anchored running Generation;
2. construct an optimizer intent from that exact running attestation;
3. run the real tiny tabular local-policy rollout optimizer;
4. independently evaluate retained checkpoints on the frozen held-out Tasks;
5. select the deterministic safe, improving, non-regressing checkpoint;
6. recursively bind native package, schema, runtime identity and Program lineage;
7. verify independent external trust anchors;
8. accept the evidence without Promotion authority;
9. enter the separate v2.2 Promotion Campaign;
10. explicitly activate P1 in the local-policy Registry;
11. preserve rollback evidence and authority separation.

This is numeric local Agent-policy optimization. It is not Transformer, LLM or foundation-model training.

## Composite Agent state

Component Registries and the composite Agent pointer are separate.

```text
Skill Registry:        S0 -> S1
Local-policy Registry: P0 -> P1
Composite Registry:    A0 -> A1 -> A2
```

A component mutation never silently changes the composite Agent. Each child composite manifest:

- has one direct parent;
- increments the round by exactly one;
- changes exactly one component;
- binds the source Case IDs, decision hashes and package hashes;
- preserves frozen Runtime, Tool, verifier, Task-manifest and budget hashes;
- requires a distinct creator and commit actor;
- uses optimistic pointer revision locking.

## Runtime evaluation

The controlled evaluator does not accept caller-supplied scores.

The Skill Tasks execute through:

```text
ToolAgentRuntime
DocumentSkillPolicy
LocalDocumentEnvironment
DocumentTaskVerifier
```

The local-policy Tasks execute through:

```text
LocalSafeDocumentMDP
IndependentLocalPolicyEvaluator
P0 or the selected P1 checkpoint
```

Every Task outcome binds a deterministic Trace or evaluation-result hash, verifier hash, score, safety count, steps, Tool calls and deterministic cost.

## Mixed-track routing

Only a unique successful bounded counterfactual may enter an automatic track.

```text
unique REPLACE_SKILL success
    + Skill root cause
    + UPDATE_SKILL
    -> Skill track

unique reference-policy success
    + local-policy/model root cause
    + TRAIN_MODEL action in the current routing contract
    -> local-policy track
```

Low confidence, multiple successful counterfactuals, non-actionable reports, trust failures and safety flags do not mutate components. They enter escalation or quarantine.

## Persistent Supervisor

The Supervisor owns only dispatch and state transitions. It does not self-certify component success.

```text
OPEN
    -> exact Case claim
RUNNING
    -> independently produced track result
OPEN
    -> next exact claim
...
STOPPED / ESCALATED / FAILED
```

Recovery rules:

- exact claim retry is read-only;
- a changed batch, Track or executor is rejected;
- result retry is read-only;
- stale Run revision is rejected;
- a local-policy execution claims the complete pending evidence batch;
- STOP requires no pending automatic Cases;
- terminal decision round must equal both the integrated execution round and active composite round.

## Self-contained evidence package

The final package contains:

- integrated Run, Cases, results, audit chain and checkpoint;
- A0/A1/A2 composite snapshots, head, audit chain and checkpoint;
- frozen stop policy, three evaluations, three decisions, audit chain and checkpoint;
- complete Skill Registry bundle and child Skill result;
- complete accepted Program/local-RL evidence chain;
- complete v2.2 local-policy Promotion package.

Verification recursively recomputes child package hashes, optimizer evidence, held-out evaluations, checkpoint selection, audit chains, one-component transitions, score sequence, stop decisions and cross-bindings.

Export is immutable:

- absent path: write;
- identical existing bytes: read-only reuse;
- different existing bytes: reject without overwrite;
- symlink path: reject.

## Deliberately absent authority

v2.3 does not perform or authorize:

- foundation-model or LLM training;
- Transformer checkpoint creation or activation;
- production Runtime activation or deployment;
- production traffic routing;
- external rollout;
- paid model-provider execution;
- official Benchmark submission;
- package publication, release or Tag creation.

Those claims require separate milestones and separate evidence.

## Verification

The v2.3 exact-Head gate performs:

1. exact commit and clean-worktree verification;
2. predecessor v2.2 source validation;
3. composite and integrated source-invariant validation;
4. focused Program, local-RL, composite, Supervisor and full Lab regressions;
5. full historical pytest regression;
6. Python 3.11 and 3.12 execution;
7. Wheel and source-archive build;
8. clean Wheel installation and `pip check`;
9. installed public-contract checks.

The workflow builds artifacts only. It does not publish them.
