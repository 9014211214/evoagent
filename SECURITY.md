# Security Policy

## Supported development line

This repository is a Research Preview. Security fixes are applied to `main` and the active release-candidate branch. Historical research milestones are not maintained as independently supported products.

## Reporting a vulnerability

Do not disclose a suspected vulnerability, secret, exploit trace, poisoned Skill, malicious demonstration, or unsafe model artifact in a public issue.

Use GitHub private vulnerability reporting when it is enabled for the repository. If it is unavailable, contact the repository owner through a private channel and include only the minimum information needed to reproduce the issue safely.

A useful report contains:

- affected commit and component;
- observable impact;
- minimal synthetic reproduction;
- whether credentials, private data, tool authority, training data, or benchmark integrity may be affected;
- suggested containment, if known.

Do not include employer-confidential data, real customer records, production credentials, hidden chain-of-thought, or third-party private data.

## Security invariants

The project treats the following as mandatory boundaries:

- untrusted or safety-flagged traces cannot update Skills or enter model-training evidence;
- one bad case cannot directly trigger model training;
- candidate generation, evaluation, approval, authorization, execution, promotion, deployment, publication, and benchmark upload are distinct stages;
- `AUTHORIZED` does not mean deployed;
- stable Skill artifacts are immutable;
- hidden chain-of-thought is not stored;
- external execution, upload, and public visibility are disabled by default;
- secrets must remain in environment variables or external secret managers;
- read-only CLI commands must not create or mutate state;
- state bundles and run bundles require hash, graph, path, artifact, event, and secret validation;
- internal hash chains are tamper-evident, not tamper-proof, and should be anchored externally for high-assurance use.

## Threats in scope

- direct and indirect prompt injection;
- poisoned demonstrations, traces, Skills, memory, retrieval content, or training data;
- verifier manipulation and reward hacking;
- unauthorized Tool use or privilege escalation;
- duplicate or conflicting evolution work;
- stale concurrent updates;
- artifact substitution, path traversal, symlink attacks, and bundle tampering;
- secret leakage;
- third-party dependency or license drift;
- false benchmark or deployment claims.

## Operational limitations

SQLite and JSONL implementations are single-node research backends. They are not substitutes for authenticated identity, hardened key management, remote append-only storage, distributed transactions, or a production deployment control plane.

The framework does not cryptographically verify external signatures. It records and checks that an external signature reference is bound to the verified manifest hash; signature verification remains the responsibility of an external trusted verifier.

Authorized environment values are redacted from captured stdout, stderr, and timeout output before adapters return control to callers.
