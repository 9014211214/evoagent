# Agentic RL and Frozen Snapshot Evaluation

## Agentic RL task boundary

The framework does not implement GRPO, PPO or other optimizers. It validates whether a verified model-improvement ticket has a suitable environment and emits a backend-neutral task specification.

Required environment properties:

- replayable;
- resettable;
- machine-verifiable;
- isolated;
- side-effect-free;
- bounded episode length;
- positive rollout budget.

The reward must include at least one positive machine-computable signal. The generated task disables deployment and artifact publishing and remains a dry-run candidate.

## Frozen longitudinal evaluation

Each evolution checkpoint is frozen before evaluation:

```text
A0 -> evolve -> A1 -> evolve -> A2 -> ... -> AN
 |              |              |            |
 +-- same held-out task manifest and evaluation budget --+
```

The protocol rejects:

- snapshot mutation during evaluation;
- missing or additional task IDs;
- evaluation budget overflow;
- duplicate or non-zero-starting rounds;
- an A0 model that differs from the declared initial checkpoint.

It reports initial score, final score, evolution gain, best score and best round. This preserves evidence of over-evolution when an intermediate checkpoint is stronger than the final one.

## Same-start comparison

Two systems can be compared only when the complete protocol is identical:

- initial model checkpoint;
- frozen dataset revision and split;
- task IDs and trials per task;
- evolution budget;
- evaluation budget.

Final weights may differ because model evolution is part of the system under test.

## Harbor and Terminal-Bench

The optional Harbor CLI adapter defaults to `terminal-bench/terminal-bench-2-1`, one trial per task, no upload, no public result and execution disabled. Leaderboard mode must be explicitly requested; it requires at least five trials per task and adds `--upload --public`.

The adapter creates a run specification only. It does not claim a Terminal-Bench result until the job is actually executed and externally validated.
