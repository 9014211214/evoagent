# Auto-Evolving Agent

A research framework for **failure-attributed, governed Agent self-evolution**.

> When an Agent fails, can it identify *what* should change, evolve only that capability, and prove the resulting Agent is better without silently degrading safety, retention, or unrelated tasks?

The framework separates observation, causal attribution, capability evolution, frozen evaluation, promotion, rollback, and multi-round stopping. It does not treat every bad case as a prompt problem or a model-training problem.

## Status

**v2.0 Research Preview source snapshot.** The release label matches package version `2.0.0`. This public repository starts from a privacy-reviewed, history-free snapshot; no tag, GitHub Release, or package publication is implied.

Canonical implemented path:

```text
failed task / trace
    -> bounded counterfactual attribution
    -> governed intervention routing
       -> Skill / Router / bounded Memory / Agent-policy candidate
       -> escalation / quarantine
    -> immutable component evidence
    -> frozen retention / transfer / adversarial / composition evaluation
    -> explicit complete-Agent activation
    -> CONTINUE / STOP / ESCALATE
    -> persistent audit + restart-safe state
```

The zero-cost unified reference lab evolves one complete Agent:

```text
A0 baseline
A1 Skill candidate
A2 verified bounded Memory candidate
A3 Router candidate
A4 numeric Agent-policy candidate
```

All four components act inside the same Tool-Agent runtime. The controlled
`0.0 -> 0.4 -> 0.6 -> 0.8 -> 1.0` sequence ends with zero regression, zero
forgetting and zero safety violations. These scores are **synthetic mechanism
evidence, not external benchmark results**.

## What is implemented

### Observable execution and causal attribution

The Runtime records bounded observable evidence. Counterfactual experiments isolate failures across:

```text
Skill
Router
Tool
Context
Verifier
Environment
Model
```

Automatic mutation requires bounded, uniquely supported evidence. Ambiguous, unsafe, or untrusted evidence escalates or quarantines.

### Skill evolution

```text
verified bad case
    -> Skill attribution
    -> minimal candidate
    -> frozen held-out evaluation
    -> independent approval
    -> explicit promotion
    -> immutable Skill Registry lineage
```

### Local Agent-policy evolution

The repository includes bounded numeric policy optimization from actual
rollouts of the unified Tool-Agent runtime, plus separate evaluation,
registration and explicit activation. This demonstrates governed Agentic-RL
mechanics without claiming foundation-model training or general performance.

**Current boundary:** the integrated v2.3 path does not modify Transformer/LLM weights.

### Unified Agent evolution

One immutable snapshot binds the frozen model, every Skill, Router, bounded
Memory, numeric Agent policy and Runtime contracts. Component candidates are
independent from the active complete-Agent pointer:

```text
candidate creation -> frozen evaluation -> decision -> explicit activation
```

A component candidate does not silently activate a new Agent, and each child
must change exactly one declared component.

### Independent evaluation and stopping

The Supervisor cannot self-report success. Frozen outcomes are independently converted into quality, safety, regression, and resource evidence before deterministic:

```text
CONTINUE
STOP
ESCALATE
```

### Persistence, governance, and rollback

The project includes persistent Registries, high-risk Campaign approvals, optimistic revisions, exact read-only retries, hash-chained audit events, checkpoints, semantic replay, immutable evidence packages, and rollback evidence.

## External benchmark: SkillEvolBench

The release line targets **SkillEvolBench** at pinned upstream commit:

```text
9e3daa339987c3cfa624121e1be442593a53d43c
```

SkillEvolBench contains 180 tasks across six Agent environments, with learning tasks followed by frozen deployment tasks testing context shift, adversarial behavior, and skill composition.

This repository contains two independently authored integration layers:

1. a strict importer for external `reports/full_report.json` evidence;
2. an EvoAgent Skill-evolution strategy bridge for the external `LifelongRunner` runtime.

The bridge deliberately changes only the evolution strategy. It keeps the external benchmark tasks, Harbor execution, model harness, verifier, and report generator outside this repository.

**Scope warning:** this bridge is `skill_component` evidence, not Full-Agent
evidence. It does not execute EvoAgent's Router, bounded Memory, numeric Agent
Policy or unified Registry. Workflow identities and comparison artifacts set
`full_agent_evidence=false`, including for a complete SkillEvolBench schedule.
The benchmark-neutral `FullAgentBenchmarkProtocol` separately requires every
external Task result to bind all four component hashes.

The concrete benchmark-neutral external evidence adapter and credential-free
hosted dry-run are documented in
[`docs/37-full-agent-external-calibration.md`](docs/37-full-agent-external-calibration.md).
Its strict importer also binds each frozen Task payload hash, caller SHA-256 and
resource usage. The historical MiMo preset is only for a bounded Tool-call
integration calibration; the separately versioned capability-aware preset is
used by the minimal scientific protocol. Neither is a benchmark result.

### Frozen 12-Task minimal scientific seed

The next low-cost Full-Agent gate is a controlled 12-Task set: three retention,
three transfer, three adversarial and three composition cases. The current
execution lock pins MiMo-V2.5 to the Xiaomi `xiaomi/fp8` provider endpoint slug
after live route verification and uses `tool_choice="required"`, disabled
reasoning and exactly one available Tool. Exact Tool-name and argument
verification, model/provider pinning and no-fallback routing remain unchanged.
A separately authorized one-request probe reached Xiaomi with HTTP 200 and
returned the exact typed Tool call, complete usage/cost accounting, no prose
and no reasoning fields. This makes the frozen seed transport-eligible, but it
is neither a Task score nor an effectiveness claim. The same seed 43,
Environment and verifier run
across all five A0→A4 snapshots for exactly 60 episodes. Historical MiMo named-function and
Qwen locks remain reproducible but are not interchangeable. A compact lock
binds every Task, snapshot, component, model-preset, inference setting and
budget hash. The public workflow is credential-free; real execution is
private, one-use and capped at USD 1.20 including runner reserve. See
[`docs/MINIMAL_SCIENTIFIC_VALIDATION.md`](docs/MINIMAL_SCIENTIFIC_VALIDATION.md).

This is designed to test the causal mechanism and retention boundary at low
cost. Even a passing seed is not an authoritative benchmark, a comparison with
another Agent, or evidence of statistical significance across seeds.

The repository includes a one-click GitHub Actions workflow at .github/workflows/skillevolbench-benchmark.yml. It pins SkillEvolBench, the benchmark-compatible Harbor v0.7.0 commit, and Claude Code 2.1.235 as Harbor's code-editing tool shell; the OpenRouter Qwen or GLM endpoint remains the inference model. Pull requests are hardwired to credential-free preflight. Authenticated smoke or compare execution requires an owner-supplied `OPENROUTER_API_KEY` repository Secret and an explicit manual dispatch; the public repository does not contain or inherit that Secret. The workflow reclaims hosted-runner disk, installs Python and the external runtime, validates the live OpenRouter catalogue, dry-runs both conditions, and supports three modes: preflight, bounded smoke, and explicitly acknowledged full compare. Real modes build from a temporary copy of the upstream runtime directory, retain Ubuntu base-image mirrors, apply bounded apt retries, verify the installed tool-shell version, and preserve before/after SHA-256 plus the exact preparation patch without modifying the pinned checkout. The release preset is configs/skillevolbench/openrouter-qwen3-coder-plus.yaml and uses the verified OpenRouter model ID qwen/qwen3-coder-plus; qwen/qwen3.7-plus and z-ai/glm-5.2:free remain optional smoke-only presets.

Hosted Ubuntu allocation, preflight, both dry-runs, the runtime build, and the complete bounded two-condition smoke are verified. Exact-head run 32324346605 used smoke-only model `qwen/qwen3.7-plus`, seed A, and identical 64-turn / 8,192-output / 100,000-context / 50%-compaction controls for both conditions. Both two-task reports passed strict import and produced an all-zero partial delta; the artifact explicitly records `publishable_full_benchmark=false`. Reported agent cost was USD 1.860921 for no_skill and USD 1.333647 for EvoAgent (USD 3.194568 combined), excluding unpriced host/probe overhead. This is integration evidence, not a benchmark score. A full same-seed run is not currently viable on the standard workflow: no_skill schedules 180 trials, EvoAgent schedules 270 including replay, and even the shorter side projects beyond the 355-minute job cap. The importer rejects incomplete schedules rather than labeling them publishable.

For the EvoAgent condition:

```text
T4-T6 evaluation roles
    -> always frozen / no mutation

learning pass
    -> no revision

learning failure
    -> require exactly one same-family target
    -> minimal-edit revision

ambiguous same-family evidence
    -> NoOp / fail closed
```

Run from a separately obtained, clean SkillEvolBench checkout:

```bash
python scripts/run_skillevolbench_evoagent.py \
  --checkout /path/to/SkillEvolBench \
  --model-yaml /path/to/SkillEvolBench/configs/models/<model>.yaml \
  --baseline-name selfgen_experience_always \
  --order-seed A \
  --run-id evoagent_seedA
```

The launcher refuses a non-pinned or dirty benchmark checkout. A real run additionally requires Docker/Harbor and a supported provider credential supplied outside this repository.

### Release comparison contract

```text
same model
same pinned SkillEvolBench commit
same order seed
same inference settings
same benchmark assets

No-Skill baseline
vs
EvoAgent governed Skill-evolution condition
```

If multi-round project snapshots are evaluated, every round must preserve the same non-studied controls.

The importer records learning/evaluation/overall success, context shift, adversarial and composition results, retention/forgetting/negative transfer when present, revision hurt rate, and library size. Exact report bytes are bound by caller-supplied SHA-256.

**No real EvoAgent SkillEvolBench score is published until the external run actually executes and the exact report is imported.** Upstream paper numbers, upstream canonical-baseline numbers, or local synthetic fixtures are not substituted for a project score.

See `docs/31-skillevolbench-release-protocol.md` and `OPEN_SOURCE_READINESS.md`.

## Quick start

```bash
python -m pip install -e ".[dev]"
evoagent --version
pytest -q
```

Useful controlled labs:

```bash
python examples/automatic_local_tool_evolution.py
python examples/executable_cross_layer_matrix.py
python examples/closed_loop_supervisor.py
python examples/authoritative_benchmark_evidence.py
python examples/benchmark_gated_champion.py
python examples/shadow_canary_release.py
python examples/multi_generation_evolution_program.py
python examples/unified_continual_agent.py
```

The canonical architecture, implemented gaps and benchmark boundary are in
[`docs/36-unified-continual-agent-architecture.md`](docs/36-unified-continual-agent-architecture.md).

## Architecture

```text
 Frozen model + Skill + Router + Memory + Agent Policy
                         |
                         v
              +------------------------+
              | Unified Tool Runtime   |
              +-----------+------------+
                          |
                          v
              Observable Trace + Verifier
                          |
                          v
            one-component counterfactuals
                          |
          +---------------+----------------+
          | Skill | Router | Memory | Policy|
          +---------------+----------------+
                          |
                          v
       frozen retention / transfer / adversarial /
                 composition evaluation
                          |
                          v
       reject / explicit activation / escalation
```

## Research principles

- **A bad case is not automatically a model problem.**
- **Improvement must be measured on frozen evidence.**
- **Aggregate score cannot hide protected-task regression or safety failure.**
- **Authorization is not execution.**
- **Component promotion is not composite Agent activation.**
- **Internal hashes prove self-consistency, not external authenticity.**
- **Synthetic lifecycle fixtures are not external benchmark scores.**

## Safety and claim boundaries

The default repository path performs no automatic:

```text
foundation-model training
checkpoint download or activation
production traffic routing
production deployment
external rollout
paid provider execution
official benchmark submission
package publication
repository visibility change
```

External execution requires separate credentials, runtime, budget, and authorization.

The Full-Agent external adapter and its credential-free hosted dry-run now
pass. One separately authorized MiMo calibration also completed the exact
Skill/Router/Memory/numeric-Policy Tool loop: 3 requests, 1,553 Tokens, USD
0.00024206, exact Xiaomi provider, no fallback, verifier passed. This is
integration calibration only, not benchmark or generalization evidence. See
`docs/37-full-agent-external-calibration.md` for the public sanitized evidence
hash and the measured one-seed planning envelope.

## Publication and performance-claim gates

The public source snapshot was authorized by the owner after Python 3.11/3.12 validation, license and third-party review, and secret/privacy scanning. Its Git history starts with one privacy-safe commit and does not inherit the private development history, pull requests, Actions logs, or artifacts.

Publishing source code does not create a benchmark claim. A complete
same-model, same-seed SkillEvolBench result may be reported only as a
Skill-component result after strict import. A whole-EvoAgent claim additionally
requires Full-Agent evidence binding Skill, Router, Memory and Agent Policy on
every external Task. Neither result currently exists.

The 12-Task Full-Agent mechanism set is frozen and its zero-cost local and
hosted gates pass. The first complete MiMo attempt stopped on model Tool-call
noncompliance and produced no score. A Qwen3.8 Flash route probe then stopped
before provider selection with HTTP 404, zero Tokens and USD 0. The current
versioned MiMo preset uses the verified required-single-Tool form with
reasoning disabled. Corrected one-use hosted run `33183563382` reached the
pinned Xiaomi/model route with HTTP 200 and returned the exact typed Tool call
in 301 Tokens at observed cost USD 0.00004522. This makes the 60-episode seed
transport-eligible, but the seed has not yet produced a result or scientific
score. External seed evidence remains independent from the authoritative
SkillEvolBench claim.

`OPEN_SOURCE_READINESS.md` records the current evidence and exact claim boundary.

## License

The independently authored core is licensed under the **Apache License, Version 2.0**. See `LICENSE`.

Third-party projects, benchmarks, datasets, model weights, and optional integrations remain subject to their own terms. See `THIRD_PARTY_LOCK.json`, `THIRD_PARTY_NOTICES.md`, and `LICENSE_DECISION.md`.
