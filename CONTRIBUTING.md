# Contributing

## Project boundary

Contributions must be independently authored and based only on public, synthetic, or properly licensed resources.

Do not contribute:

- employer-confidential code, workflows, prompts, Skills, APIs, screenshots, metrics, or data;
- real customer or employee records;
- credentials or private keys;
- hidden chain-of-thought;
- copied third-party source without an explicit license review and repository-owner approval;
- benchmark answers, hidden test data, or misleading result claims.

## Development workflow

1. Open or reference a narrowly scoped issue.
2. Create a feature branch from `main`.
3. Keep third-party integrations behind adapters.
4. Add positive and negative tests.
5. Run the full suite on Python 3.11 and 3.12.
6. Run all affected examples.
7. Open a pull request describing safety boundaries and non-goals.
8. Merge only after CI passes.

## Required design properties

- Stable artifacts are immutable.
- Every mutation creates a candidate.
- Counterfactual evidence determines the intervention layer.
- Model training is a high-cost, gated intervention.
- Untrusted evidence is quarantined.
- Candidate generation does not imply evaluation, approval, execution, promotion, or deployment.
- Persistent writes use legal state transitions and stale-revision protection.
- Read-only commands do not mutate state.
- External execution and upload remain disabled by default.

## Tests

Every new automatic action requires at least one negative test proving that unsafe, stale, conflicting, incomplete, duplicated, or tampered input is blocked.

Persistent or exported state additionally requires:

- restart tests;
- duplicate and stale-revision tests;
- content-tamper tests;
- external-checkpoint or signature-reference tests where applicable;
- secret and path-safety tests;
- clean round-trip tests.

## Third-party integrations

Before adding or changing an integration:

1. Review the upstream license file at a pinned commit.
2. Check whether an upstream NOTICE file exists at that commit.
3. Update `THIRD_PARTY_LOCK.json`.
4. Update `THIRD_PARTY_NOTICES.md`.
5. Run `evoagent compliance verify`.
6. Prefer API/CLI or external-checkout integration over vendoring, forking, or copying source.

A public release also requires an owner-approved core-code license. The release candidate does not make that decision automatically.

## Pull request checklist

- [ ] Public, synthetic, or independently authored inputs only
- [ ] No secrets or hidden reasoning
- [ ] Tests cover blocked as well as successful paths
- [ ] Stable artifacts remain unchanged until explicit promotion
- [ ] Third-party metadata updated when applicable
- [ ] Documentation states limitations and non-goals
- [ ] No unverified benchmark, training, or deployment claim
