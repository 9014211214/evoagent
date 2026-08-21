# Persistent Evolution Campaigns and Approval Governance

## Why Campaigns

A bad case, an attribution report, and a generated candidate are not independent one-off events. They belong to a longer-lived evolution Campaign that must survive process restarts, collect additional evidence, avoid duplicate work, and separate candidate generation from evaluation, approval, execution, and deployment.

v0.8 introduces a SQLite-backed Campaign repository using only the Python standard library.

## Campaign lifecycle

```text
OPEN / EVIDENCE_ACCUMULATING
    -> CANDIDATE_READY
    -> EVALUATION_PENDING
    -> APPROVAL_PENDING
    -> AUTHORIZED
    -> COMPLETED

Any pre-authorization stage may become REJECTED or CANCELLED through a legal transition.
```

`AUTHORIZED` means that the configured approval policy has been satisfied. It does **not** promote a Skill, execute training, deploy a model, or publish an artifact. Those actions remain separate operations.

## Transactional persistence

The repository uses SQLite WAL mode and `BEGIN IMMEDIATE` transactions. It persists:

- Campaign metadata and state;
- immutable candidate payload references;
- approval decisions;
- verified model-evidence traces and task IDs;
- a global append-only audit event chain.

Every state-changing operation includes an expected revision. A stale revision is rejected, preventing an older worker from overwriting a newer decision.

## Deduplication

A deterministic target key identifies the capability under work, for example:

```text
skill:<skill_id>@<active_parent_version>
model:<base_model_id>:<capability_cluster>
tool:<tool_or_task_target>
```

Only one open Campaign may own a target. A semantic candidate fingerprint excludes incidental evidence identifiers such as the latest trace ID. Equivalent new evidence therefore reuses the existing Campaign and candidate instead of creating another version or training run.

If the same target receives a materially different fingerprint while a Campaign is open, the repository raises a conflict and requires explicit resolution.

## Persistent model evidence

`PersistentModelEvidenceAccumulator` stores verified evidence by base model and capability cluster. Trace and distinct-task thresholds survive process restart. Several failures from one task cannot satisfy the distinct-task requirement.

After a model candidate Campaign exists, later matching traces append evidence and reuse the open Campaign; they do not trigger another candidate.

## Rejection cooldown

Rejected or cancelled Campaigns may set a cooldown deadline. A new Campaign for the same target is blocked until that deadline, preventing repeated regeneration loops after a known regression or governance decision.

## Approval separation

Approval policy is risk-based. The default policy requires two distinct approvals for a high-risk model Campaign and at least one approval for lower-risk Campaigns.

Governance rules:

- the candidate generator cannot approve its own Campaign;
- one actor may submit only one decision per Campaign;
- a rejection immediately rejects the Campaign;
- approval only moves the Campaign to `AUTHORIZED` after the threshold is met;
- authorization still does not execute or deploy anything.

Identity strings are placeholders in v0.8. A production deployment must bind them to authenticated principals and an organizational directory.

## Audit integrity

Every Campaign creation, reuse, candidate attachment, transition, approval, and model-evidence addition emits a globally sequenced SHA-256 chained audit event.

The internal chain detects modification, insertion, reordering, and deletion inside the retained history. An externally stored `CampaignCheckpoint` records event count and head hash so tail truncation can also be detected.

The chain is tamper-evident, not tamper-proof. The database file and checkpoint must be protected by independent operational controls.

## Governed Evolution Cycle

`GovernedEvolutionCycleService` is an optional wrapper around the v0.7 service. Without it, the existing in-memory flow remains available. With it:

- Skill candidates are Campaign-deduplicated;
- model evidence persists across restarts;
- model candidates are reused after threshold crossing;
- Router, Tool, Context, and Verifier tickets are deduplicated;
- every candidate remains unevaluated and undeployed until later lifecycle stages.

## Current limitations

- SQLite is a single-node persistence backend; distributed deployments require a database with equivalent transactions, uniqueness constraints, and revision checks.
- Skill lifecycle state remains in the existing in-memory registry; persistent Skill registry work remains future scope.
- Campaign authorization is modeled, but authenticated identity, signatures, and execution workers are not yet implemented.
