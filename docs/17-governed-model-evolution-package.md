# Governed Executable Model-Evolution Package

## Purpose

`v1.4.0` converts repeated executable Model-layer failures into a persistent, reproducible and governed model-improvement package **without executing training**.

The milestone answers a narrower and safer question than “can the framework fine-tune a model automatically?”:

> After Skill, Router, Tool, Context, Verifier and Environment have been ruled out by real re-execution, can repeated Model failures produce a trustworthy dataset, a bounded Agentic RL plan and one deduplicated Campaign?

## Lifecycle

```text
four distinct local Tool-Agent Tasks
    -> incapable base policy fails each Task
    -> seven counterfactual replays per Task
    -> six external-layer interventions remain failed
    -> reference_model succeeds
    -> verified MODEL attribution
    -> persistent distinct-Task evidence threshold
    -> sanitized evidence dataset
       -> 4 supervised reference trajectories
       -> 4 chosen/rejected preference pairs
       -> 4 replay seeds
    -> frozen disjoint held-out Task manifest
    -> one ModelImprovementTicket
    -> one dry-run Agentic RL ModelCandidate
    -> one persistent high-risk Model Campaign
    -> fifth matching bad case reuses Campaign and Candidate
    -> reproducible ModelEvolutionPackage
    -> restart verification
    -> second run reads and verifies without duplicate writes
```

## Executable causal gate

Every evidence Task is executed by the same bounded local Tool-Agent Runtime used in the v1.3 matrix.

The incapable base policy produces:

```text
model_capability_failure
```

For each Task the framework runs:

```text
replace_skill
force_router
replay_tool
complete_context
oracle_verifier
reset_environment
reference_model
```

The example may enter model evidence only when:

```text
baseline failed
reference_model succeeded
all six external-layer interventions failed
root cause == MODEL
recommended action == TRAIN_MODEL
actionable == true
```

A Task mismatch between failed and reference trajectories is rejected.

## Observable dataset records

The dataset stores only observable and auditable fields:

- frozen Task input and expected outcome;
- model and Skill identifiers;
- typed Agent actions;
- typed Tool results;
- final output;
- Verifier result and feedback;
- deterministic step, Tool-call, token and cost counts;
- attribution hash;
- per-record and manifest SHA-256 hashes.

It does not store:

- chain-of-thought;
- scratchpads;
- hidden reasoning;
- stack traces;
- credentials;
- Environment secrets;
- company data.

Common secret patterns and hidden-reasoning keys are rejected before writing.

Wall-clock duration is excluded from deterministic training evidence.

## Derived training views

For each verified evidence Task the dataset derives:

### Supervised reference trajectory

```text
Task
    -> reference Agent actions
    -> reference Tool results
    -> successful final output
```

### Preference pair

```text
chosen:   successful reference actions and output
rejected: failed incapable-policy actions and output
```

### Replay seed

```text
Task + Environment ID + deterministic seed
```

Counts are computed from actual records. Callers cannot claim more gold trajectories or preference pairs than the bundle contains.

## Frozen held-out manifest

Two full held-out `Task` objects are packaged, not only their IDs.

The package verifies:

- held-out IDs are unique;
- held-out Tasks do not overlap evidence Tasks;
- incapable policy fails held-out Tasks;
- reference policy succeeds held-out Tasks;
- Ticket, Candidate and Agentic RL TaskSpec all bind the same held-out IDs.

The held-out Tasks are not admitted to the evidence dataset.

## Persistent evidence threshold

The default lab requires:

```text
minimum verified Traces:        4
minimum distinct Tasks:         4
```

The first three failures return:

```text
MODEL_EVIDENCE_ACCUMULATED
```

The fourth returns:

```text
MODEL_CANDIDATE
```

No Model Campaign exists before the distinct-Task threshold.

A fifth matching failure appends persistent evidence but reuses the open Campaign, Ticket and Candidate. It cannot create a second training plan.

## Governed Agentic RL plan

Actual dataset signals select Agentic RL because the local Environment is:

- replayable;
- resettable;
- isolated;
- side-effect-free outside its episode root;
- machine-verifiable;
- supplied with a positive rollout budget.

The dry-run plan uses:

```text
algorithm: GRPO
rollout budget: 64
GPU budget: 0
cost budget: 0
training tokens: 0
execution_enabled: false
publish_artifacts: false
deploy_candidate: false
```

Reward components are machine-computable:

```text
+ verified Task success
- safety violation
- Tool-call budget usage
```

The Candidate binds:

- exact base model ID;
- dataset URI and manifest hash;
- held-out Task IDs;
- budget;
- Agentic RL Environment and reward specification;
- `training_executed=false`.

## Campaign governance

The Model Campaign is high risk and remains:

```text
CANDIDATE_READY
```

It requires two approvals before later authorization, but the v1.4 lab grants zero approvals.

The package therefore proves planning and evidence governance only. It does not authorize or execute training.

## Reproducible package

`ModelEvolutionPackageManifest` binds:

- framework version;
- source repository and commit;
- third-party lock hash;
- complete Campaign record;
- evidence dataset;
- complete held-out Tasks;
- ModelImprovementTicket;
- dry-run ModelCandidate;
- Campaign audit checkpoint;
- Trace checkpoint;
- package SHA-256 hash;
- external/training execution flags set to `false`.

Loading rejects:

- package hash mismatch;
- dataset hash mismatch;
- wrong Campaign type or state;
- Ticket/Candidate mismatch;
- evidence or held-out manifest mismatch;
- non-Agentic-RL Candidate;
- enabled execution;
- a Candidate that claims training occurred;
- secrets or hidden-reasoning fields.

## Restart and idempotency

The first run creates:

```text
5 persisted failed Traces
4 packaged evidence examples
1 Model Campaign
1 Ticket
1 dry-run Candidate
0 approvals
1 dataset file
1 package file
```

The second run:

- replays executable controls for verification;
- reuses the same stored Traces;
- loads the existing dataset and package;
- keeps the same Campaign, Ticket, Candidate and hashes;
- adds no Campaign audit event;
- adds no Trace;
- adds no approval;
- performs no training.

## Local command

```bash
python examples/governed_model_evolution_package.py
```

Expected shape:

```text
first resumed: False
second resumed: True
evidence tasks: 4
persisted traces: 5
SFT examples: 4
preference pairs: 4
RL seeds: 4
campaign state: candidate_ready
same campaign: True
same candidate: True
selected method: agentic_rl
rollout budget: 64
training executed: False
external work performed: False
```

## Boundaries

This milestone does not:

- update model weights;
- execute GRPO, PPO or any rollout job;
- call ml-intern, Harbor or an external model provider;
- allocate GPU or paid resources;
- produce a trained checkpoint;
- authorize, deploy, upload or publish a Candidate;
- use private business data;
- claim an official benchmark result;
- change repository visibility;
- select a public core-code license.
