# Agentic RL and frozen evaluation

Updated: 2026-08-25

## Implemented optimizer boundary

EvoAgent now includes two local numeric policy paths:

- the historical four-state safe-document MDP, retained as a governance and
  package-compatibility fixture;
- `BoundedObservablePolicyOptimizer`, which collects real rollouts from the
  same unified Tool-Agent runtime used by Skill, Router and Memory.

The unified optimizer performs deterministic, bounded group-relative policy
updates over observable actions. It emits an immutable Agent-policy candidate;
it cannot promote the candidate, change foundation-model weights, call a model
provider or deploy anything. Training Task IDs are disjoint from the frozen
held-out Task manifest.

This is real numeric Agent-policy optimization, but it is still a small local
reference policy. It is not PPO/GRPO equivalence, Transformer training, or
evidence of general Agent performance.

## Frozen continual evaluation

Every candidate uses the exact same:

- foundation model identity;
- retention, transfer, adversarial and composition Task manifest;
- seed and trial count;
- Runtime, Tool and Verifier contracts;
- Token, Tool-call, wall-time and cost budgets.

The gate derives per-Task and per-role scores, safety violations, regressions,
retention drop, forgetting rate and resource growth. Aggregate gain cannot
hide a configured retention, regression or safety gate. Safety violations may
never grow relative to the active parent, including in an intermediate round
whose final zero-violation gate has not yet been reached.

## Same-start external comparison

External comparisons additionally freeze benchmark revision, Task/checksum
manifest, inference settings and resources. Every external Task result must
bind the complete Skill, Router, Memory and Agent-policy component hashes to
qualify as **Full-Agent evidence**.

The current pinned SkillEvolBench bridge changes only the Skill-evolution
strategy. Its reports are therefore Skill-component evidence, even when a full
SkillEvolBench schedule completes. They cannot validate the unified Agent or
its Agentic-RL path.

## Benchmark boundary

Benchmarking evaluates external validity; it does not implement the Agent.
Transfer, adversarial, composition and forgetting protocols can be implemented
and tested locally. Claims that those capabilities generalize to real external
tasks require a separately obtained benchmark run through the Full-Agent
adapter contract.
