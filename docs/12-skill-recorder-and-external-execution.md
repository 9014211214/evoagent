# Skill Recorder Import and External Execution Authorization

## Microsoft Skill Recorder boundary

The framework integrates the persisted output contract of Microsoft Skill Recorder release 0.4.2, reviewed at commit:

```text
93b3ccf887a46d3e3b91ed856d888d399b02c6e4
```

The upstream desktop application records a session, analyzes it, lets the user review the generated plan, and persists a version-1 `BuiltSkill` as `skill.json`. Evoagent does not decode video, perform OCR, control the desktop application, call GitHub Copilot, or approve a recording automatically.

The import path is:

```text
caller-selected skill.json
    -> explicit consent
    -> caller-supplied SHA-256 checksum
    -> BuiltSkill v1 schema validation
    -> architecture, path, token, consistency, and secret checks
    -> calculation/action-aware immutable Skill candidate
    -> existing static and sandbox acquisition gate
    -> separate evaluation and promotion lifecycle
```

A successful parse is not a stable Skill and is not production approval.

### Retained provenance

The candidate records:

- Skill Recorder release and reviewed commit;
- upstream session ID;
- source checksum and source URI;
- rendered body SHA-256;
- MIT license identifier;
- generator identity;
- allowed tools;
- ordered plan steps;
- explicit `calculation` and `action` procedure kinds.

Fixed values are rendered into the semantic procedure only after all tokens resolve. The importer rejects unsupported architectures, unsafe names or paths, symlink inputs, oversized inputs, potential secrets, unresolved tokens, empty bodies/plans, and inconsistent plan metadata.

The complete raw recording and screenshots are deliberately not copied into the core repository.

## Why `execution_enabled=True` is insufficient

A boolean configuration flag is not authorization. Harbor and ml-intern execution now require all of the following:

```text
exact ExecutionInvocation
    -> hashed ExecutionRequest
    -> independent approvals
    -> hashed ExecutionAuthorization
    -> offline preflight
    -> transactional one-use claim
    -> subprocess execution
    -> finalized receipt
```

The approved invocation binds:

- adapter kind;
- exact argv and command hash;
- absolute workspace;
- required environment-variable names;
- network, upload, public, and training flags;
- wall-clock, cost, GPU, trial, and iteration budget;
- executable version arguments and expected version pattern;
- workspace-empty policy.

Changing one bound field invalidates the authorization.

## Approval policy

Two distinct non-requester approvers are required whenever an invocation is networked, uploads, is public, trains a model, has nonzero cost, or requests GPU time. A lower-risk offline action requires at least one approval.

Approvals are bound to the request hash and must occur between request issue and expiry. The requester cannot self-approve and one identity cannot approve twice.

The current research implementation stores actor identifiers as strings. They are not authenticated identities or cryptographic signatures. A production control plane must bind them to an identity provider and externally signed authorization records.

## Preflight

Preflight is read-only with respect to external execution. It verifies:

- request and authorization hashes;
- approval threshold and identities;
- issue/expiry window;
- exact invocation equality;
- existing, non-symlink, optionally empty workspace;
- executable presence;
- approved executable version pattern;
- required credential presence by environment-variable name only.

Credential values are not serialized or returned. Preflight output contains names and boolean presence indicators only.

## One-use execution ledger

A SQLite ledger atomically claims an authorization hash before the subprocess starts. A second claim is rejected, including after a previous execution failed. The receipt records only:

- authorization and command hashes;
- request ID;
- claimed/completed/failed status;
- timestamps;
- return code.

It does not store credentials, stdout, stderr, prompts, or model artifacts.

## Harbor boundary

The Harbor adapter generates an explicit Terminal-Bench command and binds the dataset, agent, model, trials, concurrency, jobs directory, upload/public flags, environment-variable names, and budget into the authorization.

Leaderboard mode additionally requires:

- at least five trials per task;
- network access;
- explicit upload and public flags;
- two distinct non-requester approvals.

This repository still does not run or claim an official Terminal-Bench result without an externally executed and validated run bundle.

## ml-intern boundary

The ml-intern adapter binds the sandbox-tools command, iteration limit, optional agent model, prompt, workspace, HF token name, network/training flags, and budget into the authorization.

The runtime configuration disables trace sharing and selects sandbox tools. `HF_TOKEN` remains in the environment. The adapter produces or executes candidate experiments only; it cannot deploy or publish the resulting model.

## CLI boundary

The CLI can:

```bash
evoagent execution request ...
evoagent execution show-request ...
evoagent execution show-authorization ...
evoagent execution preflight ...
```

The CLI deliberately cannot:

- create approvals on behalf of reviewers;
- execute Harbor or ml-intern;
- reveal environment values;
- deploy or publish artifacts;
- upload benchmark results.

Approval creation is available through the Python API so a future authenticated control plane can own that action instead of a local convenience command.

## Minimal subprocess environment

External commands receive only a small set of operating-system variables plus the credential/configuration names explicitly listed in the approved invocation. Unapproved supplied variable names are rejected, and unrelated ambient secrets are not inherited. Authorized credential values are redacted from captured stdout, stderr, and timeout output before control returns to the caller.

Version preflight uses fixed side-effect-free arguments (`harbor --version` or `ml-intern --help`). The actual subprocess then uses the resolved executable path returned by preflight rather than resolving the command name again through `PATH`.
