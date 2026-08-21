# Persistent Skill Registry, Verified State Bundles, and Operator CLI

## SQLite Skill lifecycle

`SQLiteSkillRegistry` is a restart-safe implementation of the immutable Skill lifecycle. It preserves the existing registry surface used by acquisition and evolution services:

```text
register_initial
add_candidate
promote / reject
rollback
active / get / list_versions / events
```

The database stores immutable Skill specifications, parent versions, lifecycle status, content hashes, evaluation decisions, active pointers, active-pointer revisions, and append-only audit events.

All writes use SQLite WAL mode and `BEGIN IMMEDIATE` transactions. Promotion and rollback may include an expected active revision; a stale worker is rejected instead of overwriting a newer active pointer.

## Lifecycle invariants

- A Skill ID has one active version.
- A candidate version is immutable and unique.
- A candidate parent must already exist.
- Promotion requires a passing evaluation decision whose base version and candidate version match the current state.
- Rejection cannot change the active pointer.
- Rollback can target only a previously active stable version.
- History is never deleted by promotion, rejection, or rollback.

## Skill audit chain

Each lifecycle event records:

- global sequence;
- event and actor IDs;
- Skill and version references;
- from/to versions;
- reason and metadata;
- UTC time;
- previous event hash and current SHA-256 hash.

The internal chain detects content modification, insertion, deletion, and reordering inside retained history. An externally stored `SkillRegistryCheckpoint` detects tail truncation.

## Verified state bundles

`SkillStateBundleManager` exports a versioned JSON bundle containing:

- all immutable version records;
- active versions and revisions;
- all lifecycle audit events;
- a manifest SHA-256 hash.

Export is atomic: the bundle is written to a temporary file, flushed, synchronized, and then moved into place.

Before export or import, the manager validates:

- manifest hash;
- every Skill content hash;
- unique Skill versions;
- parent existence;
- exactly one active record per Skill;
- active-pointer and revision maps;
- event references and hash chain;
- absence of common secret patterns.

Import is allowed only into an empty registry. The imported audit chain is reverified after the transaction commits.

## Operator CLI

The package exposes both:

```bash
python -m evoagent ...
evoagent ...
```

All output is single-line JSON for scripting.

### Skill inspection and transfer

```bash
evoagent skill list --db skills.db
evoagent skill show --db skills.db --skill-id decision_skill
evoagent skill events --db skills.db --skill-id decision_skill
evoagent skill export --db skills.db --out skills-state.json
evoagent skill import --db restored.db --input skills-state.json
evoagent skill checkpoint --db skills.db --out skill-checkpoint.json
evoagent skill audit-verify --db skills.db --checkpoint skill-checkpoint.json
```

The list, show, events, checkpoint, export, and audit commands do not modify lifecycle state. Import modifies only an empty target registry.

### Campaign inspection and approval

```bash
evoagent campaign list --db campaigns.db --state approval_pending
evoagent campaign show --db campaigns.db --campaign-id <id>
evoagent campaign approvals --db campaigns.db --campaign-id <id>
evoagent campaign checkpoint --db campaigns.db --out campaign-checkpoint.json
evoagent campaign audit-verify --db campaigns.db --checkpoint campaign-checkpoint.json
```

Explicit approval or rejection requires the actor, reason, and expected revision:

```bash
evoagent campaign approve \
  --db campaigns.db \
  --campaign-id <id> \
  --actor reviewer-a \
  --reason "risk review passed" \
  --expected-revision 3
```

The CLI delegates to v0.8 governance rules. It cannot bypass self-approval prevention, distinct-approver requirements, legal states, or optimistic revisions.

## Authorization boundary

The CLI intentionally contains no command to:

- deploy a model;
- promote an authorized Skill automatically;
- run paid training;
- upload benchmark results;
- publish an artifact.

An `AUTHORIZED` Campaign remains separate from execution and deployment.

## Current limitations

- SQLite remains a single-node backend.
- Operator identities are strings, not authenticated principals.
- The state bundle currently covers persistent Skill lifecycle state; Campaign storage is backed up at the database layer and inspected separately.
- Cryptographic signatures and remote checkpoint anchoring remain future work.
