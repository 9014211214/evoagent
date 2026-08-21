# Model Evolution Orchestration

## Entry condition

A `ModelImprovementTicket` can be created only from an actionable attribution whose root cause is `MODEL` and whose action is `TRAIN_MODEL`. The ticket factory rechecks that Skill, Router, Tool, Context, Verifier and Environment experiments were executed and did not explain the failure.

## Ticket contents

- base model and capability-gap cluster;
- verified evidence traces;
- ruled-out external layers;
- available dataset signals;
- permitted training methods;
- target metrics and regression suite;
- GPU, rollout, token and cost budget;
- replay environment and safety constraints.

## Strategy selection

```text
resettable + replayable + machine verifier + rollout budget
    -> Agentic RL
else verified preference pairs
    -> DPO
else verified gold trajectories
    -> SFT
else
    -> escalate: no supported training strategy
```

## ml-intern adapter

The optional adapter invokes the public `ml-intern` CLI rather than copying its source code. It generates argv as a tuple, never a shell string. Credentials stay in environment variables and are not embedded in the prompt. The generated runtime config uses sandbox tools and disables trace sharing.

Execution is disabled by default. v0.4 CI produces a dry-run task package only; it does not consume paid compute, publish traces, train weights or deploy a model.

## Output boundary

The orchestrator returns a `ModelCandidate` with status `candidate`. Independent functional, regression, safety and cost evaluation is required before any future promotion.
