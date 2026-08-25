# Changelog

Historical entries below describe research-framework milestones. They do not imply production deployment, trained-model availability, real traffic, or official benchmark results.

## Unreleased

- Added one immutable `UnifiedAgentSnapshot` binding the frozen model, Skills,
  Router, bounded observable Memory, numeric Agent Policy and Runtime contracts.
- Added a shared Tool-Agent runtime in which Skill, Router, Memory and Policy
  jointly affect the same Task execution and emit bounded observable metadata.
- Added executable one-component counterfactual attribution with insufficient-
  evidence and causal-conflict escalation.
- Added frozen retention, transfer, adversarial and composition evaluation,
  forgetting/regression/safety/resource metrics and an independent promotion
  gate.
- Added bounded group-relative numeric Agent-policy optimization from actual
  unified-runtime rollouts; no external model or foundation weights are used.
- Added a persistent complete-Agent candidate Registry with explicit activation
  and actor separation.
- Added a zero-cost A0→A4 reference lab evolving Skill, Memory, Router and
  Policy one at a time to a final controlled score of 1.0 with zero regression,
  forgetting and safety violations. This remains synthetic mechanism evidence.
- Added a benchmark-neutral Full-Agent evidence contract binding every
  component hash, and marked the pinned SkillEvolBench bridge and artifacts as
  Skill-component-only evidence.
- Centralized short same-directory atomic temporary paths for document,
  evidence, package and Registry writes to avoid Windows long-path failures.
- Added shell-free Windows execution for explicitly selected Python-shebang
  CLIs while retaining exact authorization, environment, budget and one-use
  controls.
- Added restart-safe unified Lab results: a verified second invocation is
  read-only and performs no duplicate optimization or Registry events.

## 2.0.0 release candidate

- Added a persistent outer-loop `EvolutionProgramPolicy` spanning already-governed release generations rather than adding another direct mutation path.
- Added verified extraction of rollback/hold `ProgramLearningSignal` objects from complete v1.9 release packages, explicitly retaining `causal_attribution_claimed=false`.
- Added independently authored `AttributionReceipt` objects bound to exact signal hashes, fixed layer/action contracts, confidence, attributor identity, and supported counterfactual experiment hashes.
- Required independent single-layer attribution before automatic continuation; missing attribution pauses, ambiguous attribution escalates, and neither path opens a Generation Campaign.
- Added immutable `GenerationPlan` records binding contiguous lineage, exact parent and target Agent identities, runtime/config and Tool-contract hashes, expected child release package/plan hashes, intervention layer/action, and bounded budgets.
- Added `EVOLUTION_GENERATION` high-risk Campaigns whose fingerprints bind the Program policy, signal, attribution, GenerationPlan, expected child package, and per-generation budget.
- Required exactly two independent approvals and prohibited the release evidence producer, causal attributor, and CONTINUE decision/planning actor from approving.
- Kept Campaign authorization, local Program authorization, explicit generation start, child evidence completion, and Program stop decisions separate.
- Added `SQLiteEvolutionProgramRepository` with immutable Programs, contiguous generations, learning signals, attributions, decisions, one optimistic Program head, cumulative counters, idempotent exact retries, conflict rejection, hash-chained events, and external checkpoints.
- Enforced maximum generations, rollbacks, holds, Campaigns, cumulative pairs/Tokens/cost, and consecutive non-improving outcomes; GenerationPlan budgets must fit the remaining cumulative Program budget.
- Added normalized Pydantic-JSON hashing for Program payloads so UTC datetimes, enums, tuples, nested models, and default attestations hash identically before and after persistence.
- Added target Agent identity verification during child completion; a declared child package cannot change the Champion/runtime/Tool identity outside the authorized plan.
- Added deterministic Program actions: `continue`, `stop_success`, `stop_budget`, `pause`, `escalate`, and `fail`.
- Added a controlled two-generation path: Generation 0 consumes the v1.9 protected-segment/safety rollback, independent Context attribution authorizes one runtime-policy successor, and Generation 1 consumes the passing v1.9 package and ends in `stop_success`.
- Demonstrated that Generations 0 and 1 may retain the same Champion snapshot while using different governed Agent/runtime identity hashes; no model weights are trained or loaded.
- Added budget-exhaustion and ambiguous-attribution controls that terminate without creating a Generation Campaign.
- Added `EvolutionProgramPackageManifest` embedding both v1.9 release packages, Program policy, signal, attribution, generations, decisions, completed Campaign, approvals, Program head, both audit chains/checkpoints, and negative controls.
- Cross-bound every Program event to immutable Signal/Plan/Outcome/Decision/Campaign evidence and bound approval identity, reason, decision, and time to Campaign audit events.
- Added coherent-rehash controls for feedback, segment/safety evidence, attribution, decision/planning actor, target identity, budget, child package, approvals, head state, Campaign evidence, audit content, and reanchored tail truncation.
- Added read-only second-run behavior with identical generations, decisions, Campaigns, approvals, events, checkpoints, controls, and package hash.
- Added local AST/compile validation and isolated dynamic checks for normalized hashing, policy decisions, budget stops, ambiguity escalation, and no-external-work attestations.
- Prepared Python 3.11/3.12, historical-example, Wheel, clean-install, installed-import, CLI, and installed multi-generation-lab gates. Exact-head GitHub Hosted Runner execution remains pending because the repository account currently reports failed billing or an exceeded spending limit before runner allocation.
- Performed no model training, Agentic RL rollout, model-provider or serving-platform call, checkpoint creation/download/load, production traffic, external rollout/rollback, deployment, Harbor/Terminal-Bench execution/upload, GPU/paid task, public release, package publication, Git tag, license selection, or repository visibility change.

## 1.9.0

- Added immutable `ReleasePlan` contracts binding the complete v1.8 Champion package, exact incumbent/Challenger snapshots, runtime and Tool-contract hashes, source commit, segment manifest, shadow/Canary schedule, allocations, observation windows, sample requirements, and release policy.
- Added caller-hashed, controlled-root `release-evidence.json` import with regular-file, symlink, traversal, size, UTF-8, JSON-bound, exact-schema, count, pair, stage, snapshot, allocation, segment, window, secret, and hidden-reasoning controls.
- Persisted bounded observable serving evidence only: event/pair/stage/segment/snapshot IDs, success/error/safety flags, latency, Token counts, cost, timestamps, and hashes.
- Added deterministic paired bootstrap and release-stage assessments for success/error deltas, safety violations, p95 latency, Token and cost growth, per-segment regressions, protected-segment regressions, evidence completeness, and confidence bounds.
- Added explicit `advance`, `hold`, `rollback`, and `ready` decisions; aggregate improvement cannot override a protected-segment or safety hard gate.
- Added `SQLiteReleaseRegistry` with immutable plans, batches, assessments, decisions, one local revisioned stage pointer per family, stage advancement, readiness, rollback, SHA-256 chained events, external checkpoints, duplicate reuse, and conflict rejection.
- Added distinct high-risk `CHAMPION_RELEASE` and `CHAMPION_ROLLBACK` Campaigns whose fingerprints bind exact Champion, plan, evidence, assessment, decision, stage, allocation, and runtime evidence.
- Required two independent approvals; prohibited the release plan creator from approving release and prohibited the decision actor/evidence producer from approving rollback.
- Kept Campaign authorization separate from local stage activation and kept local `ready` separate from production deployment.
- Added a controlled drift scenario: shadow pass, 10% Canary pass, 25% Canary protected-segment regression plus one safety violation, then governed rollback to zero candidate allocation and incumbent A0.
- Added a separate passing scenario reaching local `ready` at 25% Canary while retaining A0 as the primary snapshot and keeping `production_deployment_performed=false`.
- Added `ReleaseEvidencePackageManifest` embedding the complete v1.8 Champion package, plan/policy, raw evidence hashes, safe evidence, assessments, decisions, release/rollback Campaigns, approvals, final head, both audit chains, checkpoints, and explicit no-execution/no-traffic/no-deployment flags.
- Recomputed stage assessments and decisions during package verification and rejected coherently rehashed policy, plan, evidence, confidence, approval, pointer, Campaign, audit-event, and audit-tail modifications.
- Added restart-safe controlled labs whose second run is read-only with identical plans, evidence, decisions, Campaigns, approvals, audit events, checkpoints, heads, and package hashes.
- Added Python 3.11/3.12 tests, all offline examples, v1.9 source/compliance gates, Wheel build, clean installation, installed imports, CLI checks, historical labs, and installed Release Lab execution twice.
- Performed no production traffic, serving-platform call, real model-provider call, Harbor/Terminal-Bench execution, training, checkpoint creation/download/load, GPU/paid task, external rollout, automatic external rollback, production deployment, upload, public release, package publication, Git tag, license selection, or repository visibility change.

## 1.8.0

- Added an immutable `ChampionPromotionPolicy` binding score gain, deterministic paired-Task bootstrap confidence, Task-regression count/fraction, error-rate growth, Token/cost growth, incomplete-evidence behavior, non-final-round selection, patience, and optional exact same-model comparator requirements.
- Added `ChampionPromotionGate` to recompute every evolved round against A0 from immutable v1.7 Task aggregates rather than trusting only total scores.
- Classified rounds as `eligible`, `rejected`, or `insufficient_evidence`, selected the highest-scoring eligible round with deterministic tie-breaking, and produced an explicit stop/continue/hold recommendation.
- Demonstrated a strict zero-regression policy that selects A1 at `0.50` and rejects higher-scoring A2 at `0.75` because A2 regresses one frozen Task.
- Added deterministic paired-Task bootstrap evidence with confidence level, round-specific seed, resample count, observed mean, interval, complete sample-means hash, and evidence hash.
- Added persistent `SQLiteChampionRegistry` decisions, immutable snapshot records, one active Champion pointer, optimistic revisions, activation, rollback, SHA-256 chained lifecycle events, external checkpoints, restart verification, duplicate reuse, and conflict rejection.
- Added a distinct high-risk `CHAMPION_PROMOTION` Campaign whose fingerprint binds the complete v1.7 package, policy, decision, selected run/evidence, and exact snapshot.
- Required exactly two independent approvals and prohibited the decision actor from approving.
- Kept Campaign authorization, Registry authorization, and explicit Champion activation separate; authorization leaves A0 active and only activation changes revision `0 -> 1`.
- Added explicit rollback tests restoring A0 at revision `1 -> 2` while preserving A1 as `rolled_back` and retaining immutable history.
- Added `ChampionDecisionPackageManifest` embedding the full v1.7 package, policy, assessments, bootstrap evidence, decision, completed Campaign, approvals, Registry records, both audit chains, checkpoints, and active pointer.
- Recomputed the selection decision during package verification and rejected coherently rehashed policy, selected-round, confidence-interval, approval, pointer, Campaign, audit-event, and audit-tail changes.
- Bound packaged approval identities and reasons to the Campaign audit events rather than trusting an independent approval list.
- Added a read-only second controlled lab run with the same decision, Campaign, Registry state, checkpoints, and package hash.
- Added Python 3.11/3.12 tests, all offline examples, v1.8 source/compliance gates, Wheel build, clean installation, installed imports, CLI checks, historical labs, and installed Champion lab execution twice.
- Performed no Harbor or Terminal-Bench execution, model-provider call, checkpoint creation/download/load, SFT/DPO/GRPO/Agentic RL execution, GPU/paid task, upload, leaderboard submission, production deployment, public release, package publication, Git tag, license selection, or repository visibility change.

## 1.7.0

- Added safe metadata-only Harbor `result.json` import pinned to reviewed Harbor and Terminal-Bench 2.1 identities.
- Added immutable benchmark, Task/checksum, Agent, Model, inference, resource, timeout, and budget contracts.
- Added strict A0…AN longitudinal comparison and exact same-model cross-Agent comparison.
- Added persistent benchmark evidence, audit checkpoints, reproducible packages, submission-prerequisite assessment, and official-claim boundaries.
- Added an offline A0/A1/A2/comparator lab with `0.25 -> 0.50 -> 0.75`, one detected final-round Task regression, and read-only resume.

## 1.6.0

- Added a persistent closed-loop Supervisor routing verified causal attribution into bounded Skill, Model, external-repair, escalation, quarantine, and no-action tracks.
- Added immutable cases, budgets, optimistic revisions, idempotent executors, audit chains, mixed-track lab composition, reproducible package, and read-only resume.

## 1.5.0

- Added metadata-only external Model candidate admission, external Training Receipt validation, independent held-out/replay/retention/safety evaluation, two-approver activation Campaign, explicit activation, rollback, persistent Model Registry, and reproducible admission package.

## 1.4.0

- Added four distinct executable Model-layer evidence Tasks, sanitized SFT/preference/replay data views, disjoint held-out Tasks, persistent thresholds, and a non-executing Agentic RL plan.

## 1.3.0

- Added executable seven-layer fault injection and counterfactual attribution for Skill, Router, Tool, Context, Verifier, Environment, and Model, plus conflict escalation.

## 1.2.0

- Replaced a manually prepared A1 with evidence-gated automatic Skill evolution, independent frozen evaluation, approval, explicit promotion, and idempotent restart.

## 1.1.0

- Added the bounded Tool-Agent Runtime, resettable local document Environment, typed Tool actions/results, independent Verifier, filesystem controls, and local frozen evaluation.

## 1.0.0

- Added the stable private governed reference lifecycle from checksummed Skill acquisition through Trace, attribution, Campaign, evaluation, approval, promotion, audit, reproducible bundle, and restart.

## 1.0.0rc2

- Added Microsoft Skill Recorder persisted `skill.json` import plus external-execution authorization, minimal environments, one-use claims, and output redaction.

## 1.0.0rc1

- Added reproducible run bundles, pinned third-party compliance metadata, security/release documents, Wheel build, and clean-install validation.

## 0.9.0

- Added persistent immutable Skill versions, active pointers, state bundles, audit checkpoints, and operator CLI.

## 0.8.0

- Added persistent evolution Campaigns, candidate deduplication, model-evidence persistence, cooldowns, approvals, and audit governance.

## 0.7.0

- Connected Trace storage, bad-case detection, counterfactual attribution, candidate/Ticket creation, repeated model evidence, and dry-run model candidates in one bounded service.

## 0.6.0

- Added controlled demonstration-to-Skill acquisition, tamper-evident observable Trace storage, and Resource2Skill integration boundaries.

## 0.5.0

- Added guarded Agentic RL task specifications, frozen snapshot evaluation, same-start comparison, and Harbor/Terminal-Bench planning.

## 0.4.0

- Added verified model-improvement Tickets, strategy selection, and ml-intern-compatible dry-run orchestration.

## 0.3.0

- Added immutable Skill versions, diffs, promotion, rejection, rollback, and audit events.

## 0.2.0

- Added counterfactual cross-layer failure attribution and model-training gates.

## 0.1.0

- Added the minimal Trace → diagnosis → Skill candidate → frozen evaluation loop.
