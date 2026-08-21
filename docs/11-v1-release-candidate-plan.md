# v1.0.0-rc1: Reproducible Runs and Release Hardening

## Purpose

A benchmark score, training result, or evolution curve is credible only when another party can determine exactly what ran and detect changes to the supporting artifacts.

v1.0.0-rc1 adds a reproducible run bundle without enabling external execution or claiming an official result.

## Run bundle structure

```text
<bundle>/
├── manifest.json
├── artifacts/
│   ├── 001-config.json
│   ├── 002-results.json
│   └── ...
└── external-signature.json   # optional reference only
```

The manifest records:

- framework version and source repository commit;
- whether the source worktree was dirty;
- system and initial model IDs;
- ordered snapshot IDs;
- benchmark dataset, revision, split, task IDs, and trials;
- evolution and evaluation budgets;
- exact command;
- Python, platform, package, tool, and container information;
- network-access declaration;
- random seeds and provenance;
- run status and any external validation reference;
- logical artifact names, paths, types, byte sizes, and SHA-256 digests.

The manifest hash is computed from canonical JSON containing the complete specification and artifact inventory. Artifact source paths and the bundle destination are excluded, so two bundles with identical declared inputs, fixed creation time, and identical bytes produce the same manifest hash.

## Atomic construction

A bundle is first built in a private temporary directory beside the destination. Every artifact is copied as bytes, flushed, synchronized, hashed, and scanned for common secret patterns. The manifest is written only after all artifacts are accepted. The temporary directory is then renamed into place.

The destination must not already exist. The builder rejects duplicate logical names, source symlinks, non-files, and potential secrets.

## Verification

Verification rejects:

- a missing or invalid manifest;
- a manifest whose canonical hash does not match;
- missing or extra files and directories;
- symlinks or unsupported filesystem entries;
- path traversal or an artifact resolving outside the bundle;
- artifact size or digest mismatch;
- secret-bearing manifest, artifact, or signature-reference content;
- a signature reference bound to a different manifest hash.

The verifier expects an exact file set. This prevents an unrecorded log, config, answer file, or alternate result from being smuggled into the evidence package.

## Why an external checkpoint is required

An internal manifest hash proves self-consistency. It cannot, by itself, prove that an attacker did not modify an artifact and recompute every internal hash.

For evidence used in a claim, save the manifest hash independently:

```bash
evoagent run checkpoint \
  --bundle ./run-bundle \
  --out /separate/location/run-checkpoint.json

evoagent run verify \
  --bundle ./run-bundle \
  --checkpoint /separate/location/run-checkpoint.json
```

The external checkpoint detects a completely rehashed replacement bundle.

## External signature references

`external-signature.json` may record:

- the signed manifest hash;
- algorithm name;
- signer identity;
- signature URI;
- external verification instructions.

Evoagent verifies only that the reference is bound to the internally verified manifest hash. It deliberately reports:

```text
external_signature_cryptographically_verified = false
```

Key management and cryptographic verification remain outside this framework. This avoids falsely claiming that metadata is a verified signature.

## CLI

```bash
evoagent run show --bundle ./run-bundle
evoagent run checkpoint --bundle ./run-bundle --out ./run-checkpoint.json
evoagent run verify --bundle ./run-bundle --checkpoint ./run-checkpoint.json
```

These commands are read-only with respect to the bundle, except that `checkpoint` writes the explicitly named external checkpoint file.

## Third-party lock

`THIRD_PARTY_LOCK.json` pins each integrated upstream project to:

- repository;
- reviewed commit;
- SPDX license identifier;
- license-file path and Git blob SHA;
- optional NOTICE path and blob SHA;
- integration method;
- source-copy and modification flags;
- required attribution and purpose.

`evoagent compliance verify` checks the lock hash, model constraints, unique components, and matching human-readable notices. It is an offline consistency check; reviewing a new upstream commit remains a deliberate maintainer action.

## Release boundary

The release candidate adds engineering evidence and process controls. It does not:

- choose or grant the final core-code license;
- make the repository public;
- run paid training;
- deploy a Skill or model;
- generate or verify cryptographic signatures;
- execute, upload, or claim an official Terminal-Bench result.
