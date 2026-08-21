# Authoritative Harbor Benchmark Evidence

## Purpose

The v1.7 evidence layer answers two questions under a pinned, auditable benchmark contract:

1. Did the same Agent improve from `A0` to `A1…AN` while using the same Model and execution settings?
2. How does the evolved Agent compare with other Agents when every run uses the exact same Model and benchmark contract?

It does not execute Harbor, call a model provider, download a checkpoint, upload a result, or make an official leaderboard claim.

## Reviewed upstream scope

The implementation is independently authored and bound to reviewed upstream commits already present in `THIRD_PARTY_LOCK.json`:

```text
Harbor:
0348989adffbb43bf0b410fd36197333239633f1

Terminal-Bench 2.1:
ffccbe05ee73a9d59518217f294ad711bda39304

Dataset reference:
terminal-bench/terminal-bench-2-1
```

Harbor writes a job-level `result.json`. Evoagent parses only the observable fields required for evaluation and independently reconstructs run and Task metrics.

## Evidence lifecycle

```text
external result.json
    -> caller-supplied SHA-256
    -> regular-file and path validation
    -> raw secret scan
    -> bounded JSON parsing
    -> safe observable Trial evidence
    -> immutable BenchmarkRunEvidence
    -> SQLiteBenchmarkEvidenceRepository
    -> longitudinal and same-model comparisons
    -> eligibility assessment
    -> BenchmarkComparisonPackage
    -> restart and read-only second import
```

The raw file is never presented as authenticated merely because its internal hash is self-consistent. The caller-supplied file SHA-256 binds the exact imported bytes; external provenance requires a separately anchored source.

## Safe Harbor result import

`HarborResultImporter` requires:

```text
controlled import root
relative path ending in result.json
regular non-symlink file
caller-supplied SHA-256
bounded file size
bounded JSON depth, nodes, arrays, strings, and Trial count
UTF-8 JSON
fully completed job
consistent declared and observed counts
```

The importer rejects path escape, absolute paths, unsafe segments, symlinks, malformed JSON, duplicate Trial names, Task/checksum drift, Agent/Model identity drift, malformed rewards, missing primary reward, non-finite values, and inconsistent job totals.

### Raw secret gate

The complete raw file is scanned before any fields are dropped. A credential or private key inside a field that would otherwise be ignored still rejects the file.

Detected patterns include common API-key, GitHub-token, Hugging Face-token, AWS-key, private-key, password, token, and secret assignments.

### Persisted evidence

The safe Trial record contains:

```text
Trial name
Task name / ID / checksum / source
Agent name / version
Model provider / name
numeric reward map
primary reward
Verifier-evidence presence
error type only
input/cache/output Tokens
cost
safe duration
```

It excludes:

```text
exception messages
tracebacks
prompts
trajectories
Agent logs
Environment values
raw configuration
credentials
hidden reasoning
scratchpads
```

Errored or unverified Trials receive primary reward zero. They remain in the denominator.

## Immutable identities

### Benchmark suite

`BenchmarkSuiteIdentity` binds:

```text
suite ID
pinned dataset reference
reviewed Harbor commit
reviewed Terminal-Bench commit
primary reward key
complete Task name / ID / checksum manifest
canonical-manifest attestation
suite hash
```

### Agent snapshot

`BenchmarkAgentIdentity` binds:

```text
Agent family
Agent name/version
source commit
configuration SHA-256
snapshot ID
evolution round
parent snapshot
identity hash
```

Round zero cannot have a parent. Every evolved round must name its parent.

### Model identity

`BenchmarkModelIdentity` binds:

```text
provider
name
revision
configuration SHA-256
inference-settings SHA-256
identity hash
```

These are metadata bindings. Evoagent does not verify or load remote Model bytes.

### Frozen run contract

`BenchmarkRunContract` binds:

```text
suite
Agent
Model
reasoning effort
Trials per Task
timeout multiplier
timeout overrides
resource overrides
default-settings attestation
source type
execution budget
upload/public/Hub references
trajectory availability
```

The execution budget Trial count must exactly equal `Task count × Trials per Task`.

## Longitudinal `A0…AN` comparison

A valid evolution curve requires:

```text
rounds are consecutive from zero
A0 role is baseline
A1…AN roles are evolved
same Agent family
exact snapshot parent chain
exact same Model identity
exact same suite
exact same frozen run contract
```

Only Agent snapshot/version/source/configuration/evolution round may differ.

The report contains:

```text
score and gain for every round
best and final round
baseline and final score
final gain
per-Task delta for every evolved round
final improved/regressed/tied Task counts
monotonicity and downward-round count
error-rate delta
Token deltas when complete
cost delta when complete
```

A total score may increase while an individual Task regresses. Both facts are preserved.

## Exact same-model cross-Agent comparison

Cross-Agent comparison requires:

```text
exact Model identity equality
exact suite equality
exact frozen execution contract equality
distinct Agent identities
```

A Model mismatch invalidates the comparison. The framework does not provide a fuzzy or approximate same-model mode.

The report contains:

```text
score and deterministic rank by Agent
error rate
Token/cost totals when complete
pairwise per-Task wins/losses/ties
score, error, Token, and cost deltas
same_model_verified = true
```

## Submission-prerequisite assessment

The evidence layer can assess whether externally supplied evidence appears to meet local prerequisites:

```text
exact pinned suite
canonical Task manifest attested
default timeout/resource settings attested
complete Task coverage
at least five Trials per Task
public uploaded Harbor Hub job
reviewable trajectories
non-synthetic source
```

A positive assessment means only:

```text
submission_prerequisites_met = true
```

It does not mean:

```text
official_submission_performed = true
official_submission_accepted = true
```

Those fields remain false until a later separately authorized submission and externally verifiable acceptance receipt are implemented.

## Persistent Evidence Registry

`SQLiteBenchmarkEvidenceRepository` stores:

```text
immutable run evidence by evidence ID
raw-file and evidence hashes
immutable comparison reports
deterministic identical-import reuse
conflicting-import rejection
SHA-256 chained audit events
external checkpoint
```

The second import of identical evidence creates no new event. A conflicting object under the same ID is rejected.

Audit events are:

```text
RUN_IMPORTED
LONGITUDINAL_COMPARISON_STORED
SAME_MODEL_COMPARISON_STORED
```

Each event binds its subject, exact evidence/report hash, actor, timestamp, and previous event hash.

## Reproducible comparison package

`BenchmarkComparisonPackageManifest` contains:

```text
framework and source provenance
third-party lock hash
pinned suite
all safe run evidence and raw-file hashes
longitudinal comparison
same-model comparison
one eligibility assessment per run
Registry audit events and checkpoint
explicit no-execution/no-upload/no-official-claim flags
```

Validation independently recomputes both comparisons and every eligibility assessment from the packaged run evidence. It also verifies the event chain and exact import/comparison event payloads.

Recomputing the outer package hash does not authorize:

```text
run-score rewriting
Model substitution
raw-result hash substitution
comparison-mode substitution
eligibility rewriting
audit modification or tail truncation
```

## Controlled offline lab

The lab creates five independently authored Harbor-shaped fixtures:

```text
A0:         0.25
A1:         0.50
A2:         0.75
Comparator: 0.75, exact same Model
Mismatch:   different Model, comparison rejected
```

A0 contains an errored Trial whose raw input includes a synthetic exception message and traceback. The imported evidence retains only the error type and zero reward.

Expected longitudinal result:

```text
A0 -> A1 -> A2
0.25 -> 0.50 -> 0.75
final gain: 0.50
best round: 2
monotonic total score: true
final Task changes: 3 improved, 1 regressed, 0 tied
```

Expected same-model result:

```text
A2 score: 0.75
Comparator score: 0.75
A2 Task wins/losses/ties: 1 / 1 / 2
mismatched Model: rejected
```

Persistent shape:

```text
5 run-evidence records
2 comparison records
7 audit events
same package hash after restart
synthetic submission prerequisites met: 0
```

```bash
python examples/authoritative_benchmark_evidence.py
```

## Security and claim boundaries

The v1.7 lab performs no:

```text
Harbor or Terminal-Bench execution
model-provider call
real SFT, DPO, GRPO, or Agentic RL rollout
checkpoint creation, download, deserialization, or loading
public Harbor upload
leaderboard submission or submission PR
GPU or paid task
production deployment
result publication
repository visibility change
Git tag or GitHub Release
package publication
license selection
```

Only public, synthetic, licensed, or independently authored resources may enter the controlled lab.
