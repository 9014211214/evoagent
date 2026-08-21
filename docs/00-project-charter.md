# Project Charter

Research question:

Given an observed agent failure, can the system identify the earliest actionable root cause and choose the lowest-cost intervention that improves future held-out performance without unacceptable regression?

Agent state:

A_t = (M_t, S_t, H_t, R_t, T_t)

- M: model
- S: skills
- H: harness/runtime
- R: memory/experience
- T: tools

v0.1 keeps the model fixed and validates failure-attributed skill evolution.
