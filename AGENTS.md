# AGENTS.md

## Architectural rules

### Core independence

- Core code must not import concrete third-party self-evolution, benchmark, training, or serving-platform implementations.
- Third-party systems belong behind adapters or independently authored data contracts and must update the pinned lock and notices.
- Use only public, synthetic, licensed, or independently authored resources.
- Never add employer-confidential code, private workflows, proprietary prompts or Skills, private data, credentials, or non-public examples.

### Evolution authority boundaries

- A failure must never mutate a stable artifact directly.
- Every proposed change creates an immutable candidate, bounded repair request, or immutable generation plan.
- Observation, attribution, planning, candidate generation, evaluation, comparison, selection, approval, authorization, execution, promotion, activation, release planning, stage advancement, readiness, Program continuation, deployment, rollback, publication, upload, and official submission are separate stages.
- `AUTHORIZED` is never equivalent to `COMPLETED`, running, promoted, activated, released, deployed, published, uploaded, officially submitted, or externally validated.
- `READY` and `COMPLETED` are local evidence/control states and never equivalent to production deployment.
- A Campaign reaches `COMPLETED` only after the exact authorized local operation succeeds.
- Equivalent open work reuses one deterministic Campaign.
- Persistent transitions use legal states and optimistic revisions.
- A generator, plan creator, decision actor, attributor, or evidence producer cannot approve its own high-risk operation where prohibited.

### Observable evidence

- Persist only observable Tasks, actions, Tool calls/results, state transitions, Verifier outputs, deterministic costs, fingerprints, and bounded metadata.
- Never persist hidden chain-of-thought, scratchpads, raw stack traces, secret values, credentials, prompts, raw inputs/outputs, trajectories, or private payloads.
- Quarantined or untrusted evidence cannot mutate Skills, enter model-training evidence, or create an automatic Program generation.
- An identical ID/payload may be reused read-only; a conflicting payload under the same ID is an error.
- Internal hashes prove self-consistency only. External claims require separately anchored evidence.
- Hashes for Pydantic-backed Program artifacts use normalized JSON semantics, including UTC time, enums, tuples, defaults, and nested models.

### Tool-Agent Runtime

- A policy receives only the Task, frozen snapshot, current observation, prior observable Tool results, and bounded step metadata.
- A policy emits exactly one typed Tool call or typed finish result.
- Step, Tool-call, and wall-time limits fail closed before another side effect.
- Unexpected policy, Environment, or Verifier errors record only a structured error type.
- Resettable Environments isolate effects and expose deterministic state fingerprints.
- Filesystem Tools reject absolute paths, traversal, unsafe segments, NUL bytes, backslashes, symlinks, oversized documents, and protected writes.
- Independent Verifiers inspect final state and prohibited attempted side effects; final Agent text alone is insufficient.

### Executable counterfactual attribution

- Every counterfactual reruns the actual failed Task in a fresh resettable Environment.
- A replay changes exactly one declared component: Skill, Router, Tool, Context, Verifier, Environment, or Model policy.
- Frozen Task ID/input, expected outcome, seed, Tool contract, Runtime limits, Trace schema, and non-intervened components remain fixed.
- Exactly one successful intervention supports a single-layer attribution.
- Zero successful interventions means insufficient evidence and escalation.
- Multiple successful interventions mean causal conflict and escalation.
- Model is selected only after Skill, Router, Tool, Context, Verifier, and Environment interventions remain unsuccessful.
- Counterfactual and evaluation Traces do not silently become training experience.

### Intervention dispatch

- Skill -> `UPDATE_SKILL`.
- Router -> `UPDATE_ROUTER`.
- Tool -> `REPAIR_TOOL`.
- Context -> `UPDATE_CONTEXT`.
- Verifier -> `REPAIR_VERIFIER`.
- Environment or unknown -> `ESCALATE`.
- Model -> `TRAIN_MODEL` improvement request only.
- Safety or untrusted evidence -> `QUARANTINE`.
- An action/layer mismatch fails closed.
- Environment, conflicting, and unknown attributions do not create automatic mutation Tickets.
- A `TRAIN_MODEL` request is not a trained model, checkpoint, GPU job, or deployment authorization.

### Skill evolution

- A Skill counterfactual applies only bounded structured evidence emitted by an independent Verifier.
- Missing or malformed evidence makes automatic evolution non-actionable.
- A bad-case candidate is minimal and preserves unrelated semantic sections.
- Evolution Task IDs are disjoint from frozen held-out Task IDs.
- Candidate generation leaves the active parent unchanged until evaluation, authorization, and explicit promotion succeed.
- Promotion validates exact Campaign, parent, candidate, evaluation IDs, zero regression, and active revision.

### Model evidence and post-training lifecycle

- One Model bad case never creates a Campaign or training plan.
- Failed and reference trajectories share the exact frozen Task and expected outcome.
- `reference_model` is the only successful intervention for admitted Model evidence.
- Hidden-reasoning keys, stack traces, credentials, duplicate/conflicting Tasks, Task mismatch, and held-out leakage are rejected.
- Persistent Trace and distinct-Task thresholds are both required before a Model Campaign.
- Non-executing Agentic RL plans keep execution, publication, deployment, GPU, cost, and training-token budgets disabled or zero.
- Candidate manifests and receipts are metadata; they do not prove remote bytes exist, match a digest, execute, or were trained.
- Admission never downloads, deserializes, or executes checkpoint bytes.
- Candidate evaluation requires an explicit Adapter and complete held-out, replay, retention, and safety Tasks.
- Trainer, Evaluator, decision actor, and approvers are independent as required.
- Campaign `AUTHORIZED` and Registry `AUTHORIZED` leave the parent active.
- Only a separate optimistic operation activates a model; rollback is another separate operation.

### Persistent closed-loop Supervisor

- The Supervisor routes causal evidence; it is not a mutation engine or approval authority.
- It cannot directly edit a Skill, train a model, approve a Campaign, promote a Skill, activate a model, select a Champion, advance a release stage, start a Program generation, or deploy an artifact.
- A Supervisor run policy is immutable after creation.
- Case IDs bind the complete case payload and evidence hashes.
- Identical completed cases are reused without executor re-entry; conflicting duplicates fail.
- Budgets are checked before executor invocation.
- A completed Skill outcome attests governed promotion.
- A completed Model outcome attests independent evaluation, explicit activation, and rollback verification.
- Untrusted or safety-flagged evidence is quarantined before automatic execution.
- Environment and ambiguous failures remain explicit escalations with no mutation artifact.
- The second completed run is read-only across cases, child lifecycles, events, checkpoints, and package.

### Authoritative Harbor benchmark evidence

- Harbor `result.json` is external input and is parsed through independently authored contracts without importing Harbor implementation code.
- Import requires a controlled root, regular non-symlink file, caller SHA-256, bounded UTF-8 JSON, complete Job state, and declared/actual reconciliation.
- Whole-file secret scanning occurs before unsafe fields are discarded.
- Persisted evidence excludes exception messages, tracebacks, prompts, trajectories, logs, Environment values, credentials, hidden reasoning, and scratchpads.
- Errored, cancelled, or unverified Trials count as zero.
- Longitudinal comparison requires the same Agent family, exact Model, benchmark Suite, Task/checksum manifest, reasoning/inference settings, trials, budgets, timeouts, and resources.
- Cross-Agent comparison requires the exact same Model and frozen contract; Model mismatch invalidates the comparison.
- Local prerequisite assessment never implies official submission or acceptance.

### Benchmark-gated Champion promotion

- Champion selection consumes a fully verified v1.7 `BenchmarkComparisonPackage`.
- Recompute every round’s Task deltas against A0; never promote from aggregate score alone.
- The immutable policy binds gain, deterministic bootstrap, Task regressions, error growth, Token/cost growth, incomplete-evidence handling, non-final-round selection, patience, and optional comparator gates.
- Every evolved round is exactly one of `eligible`, `rejected`, or `insufficient_evidence`.
- Select the highest-scoring eligible round with deterministic tie-breaking; the final or highest aggregate round has no special privilege.
- No eligible round means hold or reject; the active Champion remains unchanged.
- A `CHAMPION_PROMOTION` Campaign binds the complete benchmark package, policy, decision, selected run/evidence, and exact snapshot.
- High-risk Champion promotion requires exactly two independent approvals; the decision actor cannot approve.
- Candidate admission, evaluation, Campaign authorization, Registry authorization, and pointer activation are separate operations.
- Campaign and Registry authorization must leave the current Champion pointer unchanged.
- Only explicit activation changes the active pointer and completes the Campaign.
- Rollback is a separate explicit optimistic operation and preserves snapshot records and event history.
- Package verification recomputes the decision and checks Campaign fingerprint/payload, approvals against audit identities, records, pointer, both event chains, and checkpoints.

### Shadow and Canary release control

- A ReleasePlan consumes a fully verified v1.8 `ChampionDecisionPackage` and binds its complete package hash, decision hash, incumbent/Challenger snapshots, source commit, runtime/config/Tool hashes, segment manifest, stage schedule, allocations, observation windows, minimum samples, and immutable policy.
- Release evidence is external input and requires a controlled root, regular non-symlink `release-evidence.json`, caller SHA-256, bounded UTF-8 JSON, exact schema, complete pair counts, and exact plan/stage/snapshot/allocation/window binding.
- Persist only event/pair/stage/segment/snapshot IDs, success/error/safety flags, latency, Token counts, cost, timestamps, and hashes.
- Errors and safety violations stay in the denominator.
- Every pair contains exactly one incumbent and one Challenger observation in the same segment.
- Stage assessment recomputes quality/error deltas, safety count, p95 latency, Token/cost growth, per-segment regressions, protected-segment regressions, and deterministic paired bootstrap evidence.
- Stage actions are exactly `advance`, `hold`, `rollback`, or `ready`.
- Aggregate improvement cannot override safety, protected-segment, or configured hard regression gates.
- Insufficient evidence follows the immutable policy and never silently advances.
- `CHAMPION_RELEASE` binds the complete Champion package and ReleasePlan. Plan creator cannot approve.
- Release Campaign authorization leaves the local stage inactive and candidate allocation at zero.
- Explicit `start_shadow` is a separate local operation and completes the release Campaign without calling a serving platform.
- Stage advance uses optimistic revisions, cannot skip stages, and records local allocation metadata only.
- `CHAMPION_ROLLBACK` binds the exact evidence batch, assessment, decision, stage, and allocation. Decision actor and evidence producer cannot approve.
- Rollback Campaign authorization does not change local state; only explicit rollback restores zero candidate allocation.
- `READY` keeps the incumbent as `primary_snapshot_id` and is not production deployment.
- Release package verification recomputes assessments/decisions and verifies Campaign fingerprints/payloads, approval identities, final head, both audit chains, and checkpoints.

### Persistent multi-generation Evolution Program

- The Program consumes only fully verified v1.9 `ReleaseEvidencePackage` objects.
- Release rollback/hold reasons are observable feedback and must be stored with `causal_attribution_claimed=false`.
- A release metric, protected-segment regression, or safety violation is never itself a causal attribution.
- Automatic continuation requires one exact `AttributionReceipt` bound to the signal hash.
- The attributor must differ from the release evidence producer when policy requires independence.
- Confidence must meet policy; the layer must be allowlisted; strict automation requires exactly one supported experiment.
- Zero supported experiments pauses or escalates. Multiple supported experiments escalate. Neither opens a Generation Campaign.
- Default automatic layers are bounded external layers: Skill, Router, Tool, Context, and Verifier.
- Environment attribution escalates. Model attribution remains in the separately governed training/candidate lifecycle and is not executed by the Program.
- `EvolutionProgramPolicy` binds generation, rollback, hold, Campaign, pair, Token, cost, non-improvement, attribution, approval, and stop-on-ready rules.
- A `GenerationPlan` binds contiguous parent/child lineage, exact signal and attribution hashes, intervention layer/action, parent/target Agent identities, runtime and Tool-contract hashes, exact expected child release package/plan hashes, and a narrower budget.
- A GenerationPlan must fit the remaining cumulative Program budget before Campaign creation.
- `EVOLUTION_GENERATION` is a high-risk Campaign with exactly two independent approvals.
- Release evidence producer, causal attributor, and CONTINUE decision/planning actor cannot approve the Generation Campaign.
- Generic Campaign approval cannot bypass Program-specific approval revalidation.
- Campaign authorization and local Program authorization leave the parent generation active.
- Only explicit optimistic `start_generation` changes local state to `generation_running`.
- Completion requires the exact child package and target Agent identity declared in the authorized plan.
- Exact retries for signal, attribution, plan, Campaign binding, authorization, start, completion, and decision are read-only. Conflicting retries fail closed.
- Program generations are contiguous from zero and only one generation runs at a time.
- Program actions are exactly `continue`, `stop_success`, `stop_budget`, `pause`, `escalate`, or `fail`.
- `stop_on_ready=false` pauses at readiness rather than silently optimizing beyond it.
- Maximum consecutive non-improving outcomes and rollback/hold/Campaign/resource limits are enforced before another generation.
- The controlled main path must record Generation 0 rollback, one independently attributed Context/runtime-policy successor, Generation 1 ready, then `stop_success`.
- The budget control must stop without a Campaign; the ambiguous-attribution control must escalate without a Campaign.
- The second completed Program run is read-only with the same signals, attribution, generations, Campaign, approvals, decisions, events, checkpoints, controls, and package hash.
- Program package verification recomputes child release evidence, signals, outcomes, decisions, target identities, Campaign evidence, approval identity/reason/time, head counters, event payloads/actors, chains, and checkpoints.

### External execution

- `execution_enabled=True` is configuration only and never authorization.
- External execution requires exact invocation binding, hashed request, independent approvals, externally anchored authorization, unexpired preflight, and transactional one-use claim.
- Networked, paid, public, upload, GPU, training, traffic-routing, deployment, or external Program-generation execution requires distinct non-requester approvers.
- External processes receive only a minimal approved Environment.
- Credential values are absent from requests, hashes, preflight output, and receipts and are redacted from captured output.

### Evaluation and claims

- Frozen comparison uses the same model/checkpoint, Tasks, Environment, Verifier, seed, and budgets unless one variable is explicitly studied.
- Longitudinal evaluation retains intermediate snapshots and exposes regression and best round.
- Synthetic scores, release observations, and Program generations prove lifecycle wiring or controlled causal attribution only.
- Caller-supplied URI/hash metadata is not external verification of remote bytes.
- `submission_prerequisites_met` is not an official submission or leaderboard acceptance.
- `READY`, `ROLLED_BACK`, `COMPLETED`, and candidate allocation describe the local research control plane only.
- Retaining the same Champion snapshot while changing Runtime/config identity is not newly trained model weights.
- Repository publication and any later tag, release, or package publication require explicit owner approval. Public snapshots must not inherit private Git history, private review records, credentials, or raw execution artifacts.

## Testing rules

- Every automatic intervention has negative tests for incomplete, conflicting, unsafe, stale, unauthorized, and over-budget evidence.
- Every candidate path proves the active artifact remains unchanged before explicit promotion, activation, release advance, or generation start.
- Persistent workflows include restart, exact retry, conflicting duplicate, illegal transition, stale revision, tamper, and tail-truncation tests.
- Every single-layer fault has exactly one successful counterfactual and the matching action; conflicts escalate.
- Skill tests prove Task disjointness, minimal patch, provenance, authorization, zero regression, and idempotent resume.
- Model tests cover exact Task equality, external-layer exclusion, leakage, secrets, thresholds, admission, independent evaluation, approval, explicit activation, rollback, and no execution.
- Benchmark tests cover safe import, errored-Trial accounting, exact same-model comparison, contract mismatch, persistence, package rehash attacks, and official-claim boundaries.
- Champion tests cover best-admissible selection, deterministic bootstrap, hard regression gates, missing usage evidence, comparator requirements, authorization without activation, stale revisions, rollback, audit tamper, package tamper, and read-only resume.
- Release tests cover caller-hashed import, exact schema and pair binding, sample completeness, deterministic bootstrap, safety/protected-segment gates, resource drift, authorization without activation, stage continuity, stale revisions, readiness, rollback, audit tamper, package tamper, and read-only resume.
- Program tests cover non-causal feedback, normalized hashing, independent single-experiment attribution, decision/planning identity, approval separation, remaining-budget reservation, stop-on-ready, non-improvement caps, authorization without start, exact child identity, exact retries, stale writes, audit binding, package rehash attacks, negative controls, and read-only resume.
- Stable CI runs Python 3.11 and 3.12, all offline examples, source invariants, compliance verification, Wheel build, clean installation, installed imports, CLI checks, every installed lab, and `pip check`.
- The clean Wheel runs Benchmark, Champion, Release, and multi-generation Program labs twice, not only imports their classes.
- No write-enabled or one-time release workflow may remain on a mergeable branch.
