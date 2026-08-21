# Public Research Preview release checklist

This is the short release-facing checklist. `RELEASE_CHECKLIST.md` remains the exhaustive historical control list.

## Source-publication gates

- [ ] Exact release head has a real Python 3.11 GitHub Actions run with executed steps and PASS.
- [ ] Exact release head has a real Python 3.12 GitHub Actions run with executed steps and PASS.
- [ ] Wheel builds and installs cleanly from the same exact head.
- [ ] `pytest -q` passes from the same exact head.
- [ ] `evoagent compliance verify --lock THIRD_PARTY_LOCK.json --notices THIRD_PARTY_NOTICES.md` passes.
- [ ] `python scripts/verify_release_readiness.py` passes.
- [ ] Root `LICENSE` exists and matches the owner's explicit license decision.
- [ ] No third-party source/tasks/assets with unresolved redistribution rights are vendored.
- [ ] Exact-head tracked-file and git-history secret scans pass.
- [ ] GitHub Actions use pinned immutable action revisions, least-privilege permissions, and cannot select a secret-bearing benchmark mode from pull-request-controlled files.
- [ ] External execution remains fail-closed, explicitly authorized, single-use, bounded, and isolated from the ambient environment.
- [x] Owner confirmed on 2026-08-20 that Apache-2.0 publication is authorized and that no employment, invention-assignment, confidentiality, or organizational open-source conflict applies.

Publishing source code does not require buying a full benchmark run. Until the separate performance-claim gates below pass, the release must state that no publishable full benchmark result exists and must not present smoke results as a project score.

## Must pass only before a performance claim or benchmark submission

- [ ] One complete external SkillEvolBench baseline report has been imported with exact SHA-256.
- [ ] One complete external evolved-Agent SkillEvolBench report has been imported with exact SHA-256.
- [ ] Same-model, same-seed and same-settings comparison evidence is recorded.
- [ ] The reports cover the complete pinned schedules and the importer marks the comparison publishable.
- [ ] README reports aggregate, context-shift, adversarial, composition and retention/forgetting metrics together.

## Must remain false by default in the Research Preview

```text
production deployment
production traffic routing
external rollout
paid provider execution from CI
foundation-model checkpoint activation
official benchmark submission
leaderboard acceptance claim
package publication
repository visibility mutation by CI
```

## Evidence package to retain

```text
release commit SHA
CI run URLs and artifacts
wheel SHA-256
third-party lock hash
secret-scan report
SkillEvolBench upstream commit
model/provider identity
inference settings hash
order seed
license decision
```

Add the two report SHA-256 values, imported evidence hashes, and comparison deltas only when making a performance claim or submitting a benchmark.
