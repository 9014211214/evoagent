# Unified continual Agent architecture

Updated: 2026-08-26

## 1. Goal and claim boundary

EvoAgent's target is:

> A frozen-model Tool Agent that can attribute an observable failure, propose a
> bounded change to the right Agent component, verify the candidate against
> old and new Tasks, and explicitly activate a better complete Agent snapshot
> without silently degrading safety or retention.

The default object of continual learning is the **Agent configuration**, not
foundation-model weights. The components in scope are Skills, routing,
bounded observable Memory and Agent policy. Optional model training remains a
separate governed lifecycle.

The project does not claim autonomous production deployment, hidden-reasoning
learning, general continual-learning performance, an official benchmark score
or state of the art.

## 2. Complete Agent identity

`UnifiedAgentSnapshot` binds:

- exact frozen model ID;
- immutable Skill specifications;
- immutable Router rules and default route;
- bounded Memory records derived only from verified observable evidence;
- a numeric observable Agent action policy;
- Runtime, Tool-contract and Verifier hashes;
- exact parent ID and parent hash, round, creator, changed component and
  evidence hashes;
- explicit false authority flags for model-weight updates, production
  activation and external execution.

A child is valid only when it changes exactly one of:

```text
Skill | Router | Memory | Policy
```

The model and every non-studied component remain byte-bound. A component
candidate is not active until the persistent Registry records an eligible
evaluation decision and a distinct actor explicitly advances the pointer.

## 3. One shared runtime

The reference runtime executes all adaptive components inside one actual
Tool-Agent loop:

```text
Task + frozen snapshot
    -> Router selects one or more Skills
    -> Memory may supply a verified route when no explicit rule matches
    -> Skill rules shape observable action preferences
    -> numeric Agent policy chooses the initial action
    -> Tool results feed the next decision
    -> independent Verifier checks final state and attempted side effects
```

The Trace records selected Skill IDs, route source, Memory record IDs, policy
state/action, typed Tool results and verification. It does not store raw task
inputs, prompts, hidden reasoning, credentials or full trajectories in Memory.

This closes the previous split-track gap: Skill and policy no longer act in
different synthetic Environments when the canonical unified lab is run.

## 4. Failure attribution and candidates

`UnifiedCounterfactualRunner` first executes an actually failed baseline Task.
It then reruns the exact Task, seed, Environment limits and frozen contracts in
fresh Environments. Every counterfactual candidate changes exactly one declared
component.

- one successful intervention: actionable attribution;
- zero successful interventions: insufficient evidence;
- multiple successful interventions: causal conflict and escalation.

Memory acquisition is intentionally different. A Memory record is created
only from a verified successful Trace and then evaluated as an immutable
candidate. A failure alone cannot write Memory.

## 5. Agentic RL path

`BoundedObservablePolicyOptimizer` runs actual unified-runtime episodes with a
bounded exploration probability. It derives reward from independent verifier
success, safety violations and Tool usage, computes group-relative advantages,
clips numeric updates and emits a new immutable policy.

The optimizer:

- has fixed rollout and episode-step budgets;
- cannot change Skill, Router, Memory or model identity;
- cannot promote or activate its output;
- uses no external model and no network;
- changes numeric Agent-policy parameters only.

It is a reference implementation of the learning boundary. Larger neural or
LLM-backed policies must implement the same input, evidence, budget and
candidate contracts behind an adapter; a benchmark does not supply that
optimizer.

## 6. Continual evaluation protocol

Every frozen manifest covers four roles:

| Role | What it tests |
|---|---|
| Retention | previously working Tasks remain working |
| Transfer | capability applies to a disjoint context or route |
| Adversarial | unsafe shortcuts are rejected without prohibited side effects |
| Composition | multiple learned capabilities work together in one episode |

The evaluator derives overall and per-role scores, per-Task regression count,
retention forgetting rate, safety-violation count and exact resource usage.
The promotion gate independently enforces target gain, retention drop,
regressions, zero safety-violation growth (even when an intermediate round is
not yet safety-clean), the final safety policy and Tool-growth policy.

## 7. Zero-cost reference evidence

`examples/unified_continual_agent.py` executes this synthetic sequence:

```text
A0  complete Agent baseline                         0.0
A1  Skill adds verified post-write checking         0.4
A2  verified bounded Memory transfers the route     0.6
A3  Router selects the known correct Skill          0.8
A4  numeric Agent policy learns safe inspection     1.0
```

At A4, every held-out role passes with zero regression, zero forgetting and
zero safety violations. Skill, Router and Policy changes have executable
counterfactual evidence; Memory comes from a disjoint verified source Task.
The Registry revision advances exactly four times.

The Lab persists a hash-bound result beside the Registry. A second invocation
verifies the active snapshot, revision and event count, returns the same
snapshot/decision/result hashes, and does not rerun the optimizer or append
events.

A separate hash-bound loop policy produces `continue` for A0 through A3 and
`stop_success` for A4. Its negative controls produce `stop_budget` at the
round cap and `escalate` at the consecutive non-improvement cap; completing a
Python script is not treated as an implicit stopping rule.

These numbers prove wiring and controlled causal behavior in the local
reference Environment only. They are not an external performance result.

## 8. What requires a benchmark

| Work item | Implement in EvoAgent | Requires benchmark evidence |
|---|---:|---:|
| Unified snapshot and component boundaries | yes | no |
| Same-runtime Skill/Router/Memory/Policy execution | yes | no |
| Candidate isolation, Registry and activation | yes | no |
| Numeric Agent-policy optimizer | yes | no |
| Retention/transfer/adversarial/composition metrics | yes | no |
| Local executable counterfactual engine | yes | no |
| Generalization to unseen real external Tasks | no | yes |
| Real external adversarial/composition effect | no | yes |
| Real forgetting/negative transfer rate | no | yes |
| Token, cost and latency at useful scale | no | yes |
| Official leaderboard/ranking claim | no | yes |
| Foundation-model weight training | separate implementation | benchmark only evaluates it |

The core rule is: **a benchmark can validate an implemented capability, but it
cannot fill an implementation gap.**

## 9. External adapter boundary

`FullAgentBenchmarkProtocol` accepts only an adapter whose scope is
`full_agent`. Every Task result must bind:

- complete snapshot hash;
- Skill hash;
- Router hash;
- Memory hash;
- Agent-policy hash;
- frozen model, inference, Runtime, Tool and Verifier contract;
- observable Trace hash and resource usage.

The existing pinned SkillEvolBench strategy bridge declares
`skill_component`, evaluates only Skill evolution and sets
`full_agent_evidence=false` in workflow identities/comparison output.
`FullAgentExternalEvidenceAdapter` now provides the benchmark-neutral strict
result boundary for a future SEAGym/Terminal-Bench runner. It does not itself
execute those suites or turn a dry-run into external evidence; the runner must
drive the complete snapshot and return one fully bound result per frozen Task.

## 10. Next evidence plan

1. Keep the zero-cost unified lab in Python 3.11/3.12 CI and clean-Wheel tests.
2. Keep the implemented credential-free Full-Agent adapter dry-run in hosted CI.
3. Run the bounded same-snapshot MiMo Tool-call calibration; report it
   only as integration evidence.
4. If the smoke proves every component binding and budget control, run a
   preregistered minimal scientific validation set covering all four roles.
5. Only after complete imported evidence exists, make an external performance
   claim or consider a leaderboard run.

Until step 4, the honest project description is:

> EvoAgent implements a governed, failure-attributed architecture for
> continual adaptation of Tool Agents and demonstrates the complete mechanism
> in a controlled local Environment; external effectiveness remains to be
> validated.
