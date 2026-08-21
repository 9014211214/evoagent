# Open-Source Readiness

Updated: 2026-08-21

This file records the publication boundary for the public v2.0 Research Preview source snapshot. It does not authorize a Git tag, GitHub Release, package publication, paid model execution, benchmark submission, or performance claim.

## Current gate table

| Gate | State | Evidence / exact boundary |
|---|---|---|
| Repository visibility | PUBLIC SOURCE SNAPSHOT | `9014211214/evoagent` is a new public repository. The original development repository remains private. |
| History isolation | PASSED | The candidate was exported from the reviewed source tree and contains one new root commit. Private commits, branches, PRs, Actions logs, artifacts, and repository Secrets are not copied. |
| Governed self-evolution core | READY | The Skill, local-policy, composite-Agent, evaluation, promotion, rollback, and persistent Program paths are implemented. |
| Python regression | PUBLICATION TARGETED PASSED; BASE FULL CI PASSED | The reviewed source tree passed 694 tests on Python 3.11 and 694 on Python 3.12 on Ubuntu. On the history-free candidate, 62 release, provenance, lifecycle, and SkillEvolBench tests passed and 2 platform-dependent tests skipped on Python 3.12. |
| Third-party and license review | READY | The independently authored core is Apache-2.0. The lock, notices, and source-origin boundary avoid vendoring unresolved upstream benchmark source or assets. |
| Secret and privacy review | PASSED ON PUBLICATION CANDIDATE | The deterministic tracked-source scan, Gitleaks 8.30.1 directory scan, identifying-email/local-path scan, suspicious-file inventory, staged-path check, and GitHub no-reply author-metadata check all passed. |
| SkillEvolBench integration | PARTIAL SMOKE VERIFIED | A bounded two-task same-model smoke completed both conditions and strict import. It is integration evidence only and explicitly records `publishable_full_benchmark=false`. |
| Full same-seed comparison | NOT RUN | No complete 180-trial no-skill / 270-trial EvoAgent comparison exists. No score is inferred or fabricated. |
| External model execution | DISABLED BY DEFAULT | Pull requests run credential-free preflight only. Smoke or compare needs an owner-supplied repository Secret and explicit manual dispatch; no Secret is copied into the public repository. |
| Employment / invention / confidentiality | OWNER CONFIRMED | On 2026-08-20 the owner confirmed Apache-2.0 publication authority and no applicable employment, invention-assignment, confidentiality, or organizational open-source conflict. |

## Publication boundary

The public repository contains source, tests, documentation, pinned integration metadata, and read-only or explicitly dispatched workflows. It intentionally excludes the private Git graph, review discussions, retained Actions logs and artifacts, benchmark outputs, credentials, and local audit working files.

The initial public commit uses a GitHub-provided no-reply author identity. Repository URLs and default provenance values point to `https://github.com/9014211214/evoagent`.

## Benchmark claim boundary

The successful bounded smoke demonstrates that the pinned runtime, no-skill control, EvoAgent bridge, report importer, SHA-256 binding, and delta calculation can execute end to end. It is not a benchmark score.

A performance claim requires both complete schedules under the same model, seed, inference settings, benchmark assets, budgets, and resources. Both exact `full_report.json` files must be imported and accepted as complete. Until then, upstream paper numbers, synthetic fixtures, failed runs, dry-runs, and partial smoke metrics are not project results.

## Actions not authorized by this snapshot

- Git tag or GitHub Release creation;
- PyPI or other package publication;
- paid model execution or a full benchmark run;
- official benchmark submission or leaderboard claim;
- deployment, traffic routing, checkpoint activation, or model training.
