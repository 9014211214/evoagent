# Release Checklist

This checklist governs the public Research Preview and any later release. Completing technical checks does not authorize additional paid execution, model training, traffic routing, deployment, benchmark upload, tag, GitHub Release, or package publication.

## Source and legal boundary

- [ ] Repository contains only public, synthetic, licensed, or independently authored material.
- [ ] No employer-confidential code, data, workflow, prompt, Skill, metric, screenshot, or API is present.
- [ ] No secret, credential, private user data, hidden reasoning, scratchpad, raw output, trajectory, or stack trace is present.
- [ ] `THIRD_PARTY_LOCK.json` is current and hash-valid.
- [ ] `THIRD_PARTY_NOTICES.md` matches every lock component and required attribution.
- [ ] `evoagent compliance verify` passes.
- [ ] Repository owner explicitly approves a core-code license before public distribution.
- [ ] A root `LICENSE` exists before public distribution.
- [ ] Employment, invention-assignment, confidentiality, and organizational open-source obligations are independently reviewed where applicable.

## Build and package

- [ ] Package, test, README, changelog, source validator, examples, and CI agree on `2.0.0`.
- [ ] Python 3.11 full suite passes on the exact PR head.
- [ ] Python 3.12 full suite passes on the exact PR head.
- [ ] GitHub Hosted Runner jobs actually acquire runners and execute steps; a billing/spending-limit pre-run failure is not a passing gate.
- [ ] Every offline example passes without external credentials.
- [ ] `python scripts/validate_v1_source.py` passes.
- [ ] No write-enabled or one-time release workflow remains.
- [ ] External execution, training, upload, traffic routing, deployment, publication, and benchmark submission remain disabled by default.
- [ ] Wheel builds from a clean checkout.
- [ ] Wheel installs into a fresh virtual environment.
- [ ] Installed core, Runtime, Campaign, Skill, Model, Supervisor, benchmark-evidence, Champion, release, Program, integration, execution, compliance, run, and lab modules import successfully.
- [ ] Installed `evoagent --version`, `evoagent --help`, and `pip check` pass.
- [ ] Installed historical governed labs continue to pass.
- [ ] Installed Benchmark Evidence, Champion, Release, and multi-generation Program labs each execute twice and prove read-only second runs.

## Unified continual Agent

- [ ] One immutable Agent snapshot binds the frozen Model, Skill library, Router, bounded verified Memory, numeric action Policy, Runtime, Tool contract, and Verifier.
- [ ] Skill, Router, Memory, and Policy execute in the same Tool-Agent Runtime rather than separate scoring fixtures.
- [ ] Every successor changes exactly one eligible component while Model, Runtime, Tool contract, and Verifier remain frozen.
- [ ] Failed observations do not directly mutate the active Agent; fresh-Environment counterfactuals support exactly one component or fail closed as insufficient/conflicting evidence.
- [ ] Memory stores only verified, bounded records and excludes raw prompts, raw Task input/output, hidden reasoning, credentials, and stack traces.
- [ ] Numeric Policy optimization uses observable Runtime rollouts, bounded exploration/steps, explicit rewards, and no external execution by default.
- [ ] Frozen evaluation includes retention, transfer, adversarial, and composition roles, with separate regression, forgetting, safety, and resource gates.
- [ ] Candidate registration, evaluation, decision, and explicit activation remain separate; restart is read-only and does not retrain or duplicate events.
- [ ] Full-Agent benchmark evidence binds the exact Agent snapshot and all component hashes for every Task result.
- [ ] SkillEvolBench bridge artifacts declare `agent_scope=skill_component`, `evaluated_components=["skill"]`, and `full_agent_evidence=false`.
- [ ] Local A0→A4 results are described only as deterministic mechanism evidence, never as an external benchmark or general continual-learning claim.

## v2.0 persistent multi-generation Evolution Program

### Feedback admission

- [ ] Complete v1.9 `ReleaseEvidencePackage` is reverified before Program ingestion.
- [ ] Only terminal rollback or hold evidence creates a repair learning signal.
- [ ] Signal binds exact release package, plan, batch, assessment, decision, stage, snapshots, Runtime, Tool contract, reasons, segments, safety count, and producer identity.
- [ ] Signal stores `causal_attribution_claimed=false`.
- [ ] Release metrics, safety count, and protected-segment regression are not represented as a root cause.
- [ ] Signal contains no prompt, raw input/output, trajectory, private payload, credential, hidden reasoning, scratchpad, or stack trace.
- [ ] Exact signal retry is read-only; conflicting same-ID signal fails closed.

### Attribution boundary

- [ ] Automatic continuation requires an exact AttributionReceipt bound to signal ID and hash.
- [ ] Attributor differs from release evidence producer.
- [ ] Layer and action match the fixed intervention contract.
- [ ] Confidence meets immutable Program policy.
- [ ] Strict policy requires exactly one supported experiment hash.
- [ ] Zero supported experiments pauses/escalates without a Campaign.
- [ ] Multiple supported experiments escalate without a Campaign.
- [ ] Non-allowlisted, Environment, Model, untrusted, or ambiguous attribution cannot silently create a successor.
- [ ] Exact attribution retry is read-only; conflicting same-ID receipt fails closed.

### Program policy and budgets

- [ ] Program policy hash binds maximum generations, rollbacks, holds, Campaigns, cumulative pairs, Tokens, cost, attribution rules, approvals, stop-on-ready, and non-improvement limits.
- [ ] Pydantic-backed Program hashes normalize UTC time, enums, tuples, nested models, and default attestations.
- [ ] GenerationPlan has a narrower pair/Token/cost/child-package budget.
- [ ] GenerationPlan budget fits the remaining cumulative Program budget before Campaign creation.
- [ ] `max_generations=1` produces `stop_budget` after Generation 0 and opens no Campaign.
- [ ] Rollback, hold, Campaign, resource, and consecutive non-improvement caps are enforced.
- [ ] `stop_on_ready=true` produces `stop_success`.
- [ ] `stop_on_ready=false` pauses rather than silently optimizing past readiness.
- [ ] High-risk Program policy cannot disable independent approvals.

### Generation identity and lineage

- [ ] Generations are contiguous from index zero.
- [ ] One Program generation runs at a time.
- [ ] Successor parent equals the current active generation.
- [ ] GenerationPlan binds exact signal, attribution, parent identity, target identity, Runtime hash, Tool hash, expected child release package, expected child ReleasePlan, and budget.
- [ ] Parent and target Agent identity hashes differ.
- [ ] Child completion recomputes target Agent identity from Champion package, snapshot, Runtime, and Tool contract.
- [ ] Child package cannot widen or substitute target Runtime, Tool, Champion, package, plan, or budget.
- [ ] Same Champion snapshot with different Runtime/config identity is described as configuration evolution, not trained model weights.

### Generation Campaign and approvals

- [ ] Campaign type is `EVOLUTION_GENERATION` and risk is `HIGH`.
- [ ] Fingerprint binds Program policy, signal, attribution, plan, expected child package, and budget.
- [ ] Payload embeds complete immutable policy, signal, attribution, and plan.
- [ ] Exactly two distinct approvals are required.
- [ ] Release evidence producer cannot approve.
- [ ] Attributor cannot approve.
- [ ] CONTINUE decision / planning actor cannot approve.
- [ ] Persistent approvals are revalidated before Program authorization and start.
- [ ] Generic Campaign API approval cannot bypass Program-specific identity checks.
- [ ] Approval identity, decision, reason, and time match Campaign audit events.

### Authorization, start, completion, and decisions

- [ ] Campaign authorization does not start a generation.
- [ ] Program authorization leaves the parent generation active.
- [ ] Explicit start requires exact expected revision and changes local state to `generation_running`.
- [ ] Stale start is rejected unless it is an exact read-only retry of the already-started generation.
- [ ] Completion requires exact authorized plan and child package.
- [ ] Stale completion is rejected unless it is an exact read-only retry of the identical outcome.
- [ ] Conflicting completion under the same generation ID fails closed.
- [ ] Program decisions are exactly `continue`, `stop_success`, `stop_budget`, `pause`, `escalate`, or `fail`.
- [ ] Exact decision retry is read-only; a genuinely new stale decision fails.

### Controlled two-generation result

- [ ] Generation 0 consumes the v1.9 drift package and is `rolled_back`.
- [ ] Signal records the protected-segment regression and one safety violation without causal claim.
- [ ] Independent Context attribution contains one supported replacement-context-policy experiment.
- [ ] Generation 1 Campaign receives two independent approvals.
- [ ] Authorization alone leaves Generation 0 active.
- [ ] Explicit Generation 1 start occurs once.
- [ ] Generation 1 consumes the exact passing v1.9 package and reaches local `ready`.
- [ ] Decisions are `continue`, then `stop_success`.
- [ ] Final Program state is `completed`.
- [ ] Exactly two generations, one Generation Campaign, two approvals, twelve Program events, and seven Campaign events are present.
- [ ] Final Program revision is six.
- [ ] Second lab invocation creates no duplicate signal, attribution, plan, Campaign, approval, authorization, start, completion, decision, or event.
- [ ] Second lab invocation returns the same package hash.

### Negative controls

- [ ] Budget control ends `budget_exhausted` with no Generation Campaign.
- [ ] Ambiguous-attribution control ends `escalated` with no Generation Campaign.
- [ ] Missing-attribution path pauses with no automatic successor.
- [ ] Low confidence, non-independent attributor, wrong layer/action, wrong signal hash, wrong parent, and wrong target identity fail closed.
- [ ] Over-budget plan is rejected before Campaign creation.
- [ ] Over-budget child outcome is rejected before completion.

### Persistent Registry and audit

- [ ] Program, head, generations, signals, attributions, decisions, and events survive restart.
- [ ] Program record and head state agree.
- [ ] Head counters equal immutable generation outcomes.
- [ ] Active generation and current index agree.
- [ ] Audit content modification is detected.
- [ ] Audit sequence insertion/deletion/reordering is detected.
- [ ] External checkpoint detects tail truncation.
- [ ] Reanchoring a truncated tail to a rewritten checkpoint still fails package lifecycle-event validation.
- [ ] Event payloads and actors match immutable Signal, Attribution, Plan, Outcome, Decision, and Campaign evidence.

### Reproducible Program package

- [ ] Package embeds full drift and passing v1.9 release packages.
- [ ] Package embeds policy, signal, attribution, generations, decisions, completed Campaign, approvals, final head, both event chains/checkpoints, and negative controls.
- [ ] Package recomputes signals and generation outcomes from release packages.
- [ ] Package recomputes Program decisions with the same hardened runtime gate.
- [ ] Package verifies target Agent identity and child package/plan binding.
- [ ] Package verifies Campaign fingerprint, target, metadata, payload, and approval identities.
- [ ] Rehashed feedback reason/segment/safety changes are rejected.
- [ ] Rehashed attribution layer/action/experiment changes are rejected.
- [ ] Rehashed CONTINUE decision/planning-actor changes are rejected.
- [ ] Rehashed target identity, Runtime, Tool, budget, parent, or child package changes are rejected.
- [ ] Approval identity/reason/time substitution is rejected.
- [ ] Program head rewriting is rejected.
- [ ] Program/Campaign audit modification and reanchored tail truncation are rejected.
- [ ] Secret-bearing and hidden-reasoning content is rejected.

## Existing governed lifecycle regression

- [ ] Skill candidate creation, frozen evaluation, approval, authorization, promotion, and rollback remain separate.
- [ ] Model evidence requires repeated distinct executable Tasks and external-layer exclusion.
- [ ] Agentic RL planning remains non-executing.
- [ ] External Model Candidate admission verifies the exact governed package and loads no checkpoint bytes.
- [ ] Model held-out/replay/retention/safety evaluation remains independent.
- [ ] Model Campaign authorization, Registry authorization, activation, and rollback remain separate.
- [ ] Supervisor routing cannot bypass any child lifecycle gate.
- [ ] Harbor evidence import and exact same-model comparisons remain unchanged.
- [ ] Champion selection still rejects aggregate-score-only promotion.
- [ ] Shadow/Canary release still enforces protected-segment and safety gates.
- [ ] Historical restart, duplicate, stale-revision, audit-tamper, tail-truncation, and package-rehash tests pass.

## Runtime, security, and external execution

- [ ] Tool-Agent Runtime limits steps, Tool calls, and wall time before another side effect.
- [ ] Resettable Environments isolate effects and expose deterministic state fingerprints.
- [ ] Filesystem Tools reject traversal, symlinks, unsafe segments, oversized documents, and protected writes.
- [ ] Verifiers inspect final state and prohibited effects rather than trusting final Agent text.
- [ ] `execution_enabled=True` alone cannot execute Harbor or ml-intern.
- [ ] External execution requires exact request binding, independent approvals, unexpired authorization, offline preflight, minimal Environment, one-use claim, redaction, and receipt finalization.
- [ ] No CI job or example performs real Harbor, model-provider, serving-platform, GPU, paid, upload, public, traffic-routing, deployment, rollback, or training work.

## Reproducibility and claims

- [ ] Source commit, framework version, policy/model identifiers, frozen Task manifests, seeds, budgets, checkpoints, and artifact hashes are recorded where applicable.
- [ ] Same-start comparisons preserve every non-studied variable.
- [ ] Internal hashes are described as self-consistency evidence, not external authenticity.
- [ ] Synthetic scores, release observations, and Program generations are described only as lifecycle and causal-control results.
- [ ] Same Champion snapshot plus changed Runtime/config is not described as model training.
- [ ] No trained-model, checkpoint, production-readiness, official-score, leaderboard, or autonomous-deployment claim is made without separate evidence and authorization.
- [ ] No Git tag, GitHub Release, PyPI publication, repository visibility change, or license grant occurs without explicit owner approval.
