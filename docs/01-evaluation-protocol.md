# Evaluation Protocol

## Longitudinal
Freeze and evaluate A0, A1, ... AN on the same held-out suite.

EvolutionGain_N = Score(A_N) - Score(A_0)

## Same-start comparison
All systems receive the same:
- initial model checkpoint
- evolution-task stream
- verifier feedback
- rollout budget
- token budget
- compute budget
- held-out evaluation suite

Final model weights may differ because model evolution is part of the system under test.

## Report costs separately
- LLM tokens
- environment rollouts
- tool calls
- GPU time
- training tokens
- wall-clock runtime
