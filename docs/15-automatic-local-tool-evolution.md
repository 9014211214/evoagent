# Evidence-Gated Automatic Skill Evolution on the Local Tool Runtime

## Purpose

`v1.2.0` replaces the manually prepared A1 snapshot from the v1.1 local Tool lab with a real controlled evolution cycle:

```text
persistent SkillSpec 1.0.0
    -> actual protected-document Tool failure
    -> observable Trace + structured verifier feedback
    -> actual per-layer counterfactual replays
    -> Skill root-cause attribution
    -> StructuredVerifierSkillBackend patch
    -> deduplicated governed Campaign
    -> immutable SkillSpec 1.1.0 candidate
    -> disjoint frozen held-out evaluation
    -> independent Campaign evaluation and approval
    -> AUTHORIZED
    -> separate explicit Skill promotion
    -> Campaign COMPLETED
    -> restart and second-run idempotency verification
```

No A1 Skill is supplied to the lab in advance.

## Disjoint experience and evaluation

The evolution experience is:

```text
local:train-protected-runtime-config
```

It uses a protected `runtime-config.txt` document.

The frozen evaluation manifest contains different task IDs and different document paths:

```text
local:create-note
local:protected-policy
```

The training task ID is rejected if it appears in the frozen manifest. The candidate must therefore transfer the general rule `inspect_before_write` to an unseen protected document rather than memorize the training item.

## Actual counterfactual attribution

`LocalToolCounterfactualRunner` executes the same failed task in a freshly reset local environment for each standard hypothesis:

```text
REPLACE_SKILL
FORCE_ROUTER
REPLAY_TOOL
COMPLETE_CONTEXT
ORACLE_VERIFIER
RESET_ENVIRONMENT
REFERENCE_MODEL
```

The baseline model, task input, policy implementation, Tool set, verifier and deterministic seed remain controlled. The Skill experiment adds only the rule encoded by the independent verifier feedback:

```text
missing_skill_rule: inspect_before_write
```

Expected causal results:

```text
replace_skill      -> success
force_router       -> failure
replay_tool        -> failure
complete_context   -> failure
oracle_verifier    -> failure
reset_environment  -> failure
reference_model    -> failure
```

If the feedback does not contain a bounded structured rule, the Skill intervention cannot be constructed. No counterfactual succeeds, attribution becomes `UNKNOWN`, and automatic evolution is blocked.

Counterfactual traces are returned as evidence but are not silently persisted as training experience.

## Candidate generation

The failed baseline Trace and actual counterfactual runner enter `GovernedEvolutionCycleService`.

`StructuredVerifierSkillBackend` parses the verifier feedback and creates:

```python
SkillPatch(
    add_rules=("inspect_before_write",),
    evidence_trace_ids=(training_trace_id,),
    generated_by="structured_verifier_skill_backend",
)
```

`SkillCandidateBuilder` applies the patch to the active persistent `SkillSpec` and creates version `1.1.0`.

The candidate must be minimal:

- exactly one added rule: `inspect_before_write`;
- no removed rule;
- unchanged Tool allowlist;
- unchanged procedure and typed procedure kinds;
- unchanged unrelated semantic sections;
- source provenance extended with the observed training Trace ID.

Candidate creation does not change the active pointer. At this point:

```text
1.0.0 = ACTIVE
1.1.0 = CANDIDATE
Campaign = CANDIDATE_READY
Approvals = 0
```

## Frozen gate

A0 and A1 use the same:

- fixed model identifier;
- Tool-Agent Runtime implementation;
- deterministic policy implementation;
- local environment and Tool contracts;
- independent verifier;
- frozen task manifest;
- seed;
- step, Tool-call, wall-time, token and cost budgets.

Expected result:

```text
A0 ordinary create:       1
A0 protected document:   0
A0 score:               0.5

A1 ordinary create:       1
A1 protected document:   1
A1 score:               1.0

Evolution gain:          0.5
Regression count:          0
```

The held-out protected task uses a different document and task ID from the evolution experience.

## Evaluation, approval and promotion separation

The lifecycle preserves four distinct decisions:

1. Counterfactual attribution decides which layer is causal.
2. Candidate generation creates an immutable proposal.
3. Frozen evaluation decides whether the candidate improves and avoids regression.
4. Campaign approval authorizes a later explicit promotion operation.

A candidate can be promoted only when all of the following match:

- Campaign state is exactly `AUTHORIZED`;
- Campaign target is the current active parent Skill;
- Campaign payload contains the exact evaluated candidate;
- evaluation decision matches Skill ID, base version and candidate version;
- evaluation decision is passing with zero regression;
- active Skill version has not changed since evaluation.

`AUTHORIZED` does not modify the active pointer. The separate call to `SQLiteSkillRegistry.promote` performs that change. The Campaign becomes `COMPLETED` only after promotion succeeds.

## Persistence and retry

The lab stores:

- immutable Skill versions and lifecycle audit events;
- the single governed Campaign and approvals;
- one verified baseline training Trace.

An idempotent Trace-store wrapper allows an identical Trace to be reused if a process stopped after persistence but before candidate creation. A different Trace payload under the same ID is still rejected.

Running the completed lab again must preserve:

```text
Skill versions:       2
Campaigns:            1
Approvals:            1
Persisted Traces:     1
Candidate events:     1
Promotion events:     1
Active version:       1.1.0
Campaign state:       COMPLETED
```

Counterfactual and held-out evaluation traces are not added to the training Trace store.

## Local command

```bash
python examples/automatic_local_tool_evolution.py
```

Expected key output:

```text
attributed layer: skill
supported experiments: ['replace_skill']
added rules: ['inspect_before_write']
base score: 0.5
candidate score: 1.0
evolution gain: 0.5
regression count: 0
same campaign: True
same training trace: True
external execution performed: False
```

## Boundaries

This milestone does not:

- call an external LLM or model provider;
- update model weights;
- execute Agentic RL;
- automate a browser or desktop;
- execute Harbor or ml-intern;
- use company data or workflows;
- deploy a Skill or model outside the isolated lab;
- publish an artifact or benchmark result;
- change repository visibility or select a public license.
