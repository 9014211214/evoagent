# Open-Source Readiness

Updated: 2026-08-30

This file records the publication boundary for the public v2.0 Research Preview source snapshot. It does not authorize a Git tag, GitHub Release, package publication, paid model execution, benchmark submission, or performance claim.

## Current gate table

| Gate | State | Evidence / exact boundary |
|---|---|---|
| Repository visibility | PUBLIC SOURCE SNAPSHOT | `9014211214/evoagent` is a new public repository. The original development repository remains private. |
| History isolation | PASSED | The candidate was exported from the reviewed source tree and contains one new root commit. Private commits, branches, PRs, Actions logs, artifacts, and repository Secrets are not copied. |
| Governed self-evolution core | UNIFIED LOCAL PATH IMPLEMENTED | One immutable Agent snapshot now binds Skill, Router, bounded Memory and numeric Agent Policy. All four act in the same Tool-Agent runtime; candidates, frozen evaluation, decision and explicit Registry activation remain separate. |
| Unified continual reference evidence | LOCAL SYNTHETIC PASSED | The zero-cost A0→A4 lab changes Skill, Memory, Router and Policy one at a time and reaches all held-out retention/transfer/adversarial/composition Tasks with zero final regression, forgetting and safety violations. This is mechanism evidence only. |
| Python regression | LOCAL AND HOSTED EXACT-HEAD MATRICES PASSED | Public experimental source head `3f3e85b188ac6ecbb4734053ed5615da89d2e889` completed all five required GitHub Actions workflows, including the full Python 3.11/3.12 CI jobs. The 89 directly affected OpenRouter, minimal-seed and strict-import tests also pass locally: 88 passed and 1 skipped. |
| Third-party and license review | READY | The independently authored core is Apache-2.0. The lock, notices, and source-origin boundary avoid vendoring unresolved upstream benchmark source or assets. |
| Secret and privacy review | PASSED ON PUBLICATION CANDIDATE | The deterministic tracked-source scan, Gitleaks 8.30.1 directory scan, identifying-email/local-path scan, suspicious-file inventory, staged-path check, and GitHub no-reply author-metadata check all passed. |
| SkillEvolBench integration | PARTIAL SKILL-COMPONENT SMOKE VERIFIED | A bounded two-task same-model smoke completed both conditions and strict import. It is integration evidence only, evaluates the Skill bridge only, and records `publishable_full_benchmark=false`. New artifacts also bind `agent_scope=skill_component` and `full_agent_evidence=false`. |
| Full same-seed comparison | NOT RUN | No complete 180-trial no-skill / 270-trial EvoAgent comparison exists. No score is inferred or fabricated. |
| Full-Agent external adapter | HOSTED DRY-RUN PASSED | Exact-head run `32970101477` passed the credential-free deterministic double-build and strict adapter tests. Artifact `9607245269` has digest `sha256:375114fb2e3790b6e45327f3ea1b021ce77e0ee3c01500e489c11a6caa61020b`. This proves the binding/import path only. |
| Full-Agent external calibration | CALIBRATION PASSED | One owner-approved MiMo run bound Skill, Router, Memory and numeric Policy in the same real Tool loop and passed its verifier for USD 0.00024206. Sanitized evidence is tracked under `evidence/full-agent/`; it explicitly claims no benchmark score or generalization result. The separate multi-snapshot seed is recorded in the next row. |
| Minimal scientific seed | ONE FROZEN SEED PASSED; NOT A BENCHMARK | Private run `33197785751` executed all 12 public synthetic Tasks against A0→A4 from exact public source `3f3e85b`. The strict importer accepted 5 reports and 60 Task results. Scores were `0, 0.5, 2/3, 0.75, 1.0`; final retention/transfer/adversarial/composition were all `1.0`, with zero regression, zero retention drop and zero final safety violations. All 114 requests were accounted and validated with no retry: 58,014 Tokens and USD 0.0086178344 model cost. Artifact `9696558786` has digest `sha256:f5b5435b977b9c82a194d8cbcf75b773c11686b85b5963d0a760841112df8905`; result SHA-256 is `3fb8ea9aab7de4f64fe1810362aa1c7dbf3a9fb2e835e76c437933455cbe8cc1` and strict receipt hash is `8ad68e19543f9e86682a93294ad8a412e67a5b00e12d73003457ef755231d6e9`. This supports only the preregistered controlled mechanism claim, not an authoritative benchmark or broad generalization claim. |
| SEAGym + Terminal-Bench 2.0 pilot | PROTOCOL V5 FROZEN; INCOMPLETE, NO SCORE | Ten score-blind controller attempts reached parts of the pinned external lifecycle but none completed the 24-task-trial A0/AT comparison. Cumulative observed key usage across their bounded windows was USD 0.174437779. Latest run `33285475794`, bound to controller `9c25f473e8054bac76d7128f0ac025dd3e154080` and public EvoAgent `09018d7b4bdfcdc11f61f8c302c857d7f5dfd7f7`, produced artifact `9724445260` with ZIP SHA-256 `9b4d9465991ed5f9ef0bb5db5a3d56253751289917878b0a39a18f8e2359caee`. It completed 100 logical requests over 110 upstream attempts, observed 15 HTTP 404 attempts, made 10 byte-identical same-route retries, ended with five final 404 errors and zero proxy rejections, and used USD 0.042464641. Protocol v5 keeps the frozen model, Xiaomi endpoint, requests, Tasks, seed, budget and interpretation, while serializing Harbor trials and extending same-route retry backoff to 5/10/20/40 seconds. No partial score is reported. |
| External model execution | DISABLED BY DEFAULT | Pull requests remain credential-free. Calibration and seed execution used separate private one-use manual workflows, independent approval identities, exact-head binding, cumulative hard caps and a repository Secret that was neither printed nor copied into the public repository. The successful seed used USD 0.0086178344 of model credit under its USD 0.60 model / USD 1.20 total authorization. |
| Employment / invention / confidentiality | OWNER CONFIRMED | On 2026-08-20 the owner confirmed Apache-2.0 publication authority and no applicable employment, invention-assignment, confidentiality, or organizational open-source conflict. |

## Publication boundary

The public repository contains source, tests, documentation, pinned integration metadata, read-only or explicitly dispatched workflows, and a compact sanitized aggregate seed summary. It intentionally excludes the private Git graph, review discussions, retained Actions logs and artifacts, raw execution outputs, credentials, and local audit working files.

The initial public commit uses a GitHub-provided no-reply author identity. Repository URLs and default provenance values point to `https://github.com/9014211214/evoagent`.

## Benchmark claim boundary

The successful bounded smoke demonstrates that the pinned runtime, no-skill
control, Skill-evolution bridge, report importer, SHA-256 binding, and delta
calculation can execute end to end. It is not a benchmark score and does not
validate the complete EvoAgent architecture.

A performance claim requires both complete schedules under the same model, seed, inference settings, benchmark assets, budgets, and resources. Both exact `full_report.json` files must be imported and accepted as complete. Until then, upstream paper numbers, synthetic fixtures, failed runs, dry-runs, and partial smoke metrics are not project results.

A whole-EvoAgent claim additionally requires Full-Agent evidence in which every
Task result binds the complete Skill, Router, Memory and Agent-policy hashes.
The current SkillEvolBench strategy bridge cannot produce that evidence by
design.

The frozen 12-Task set is a separate controlled Full-Agent mechanism test. Its
first complete external seed passed the preregistered local gates and therefore
supports only the narrow A0→A4 causal-chain claim in
`docs/MINIMAL_SCIENTIFIC_VALIDATION.md`. It is not a SkillEvolBench result,
leaderboard claim, broad generalization result or multi-seed significance test.
The public aggregate evidence is under `evidence/minimal-scientific-seed/`.

The local matrix also verifies read-only restart of the unified lab: the
second invocation does not rerun optimization or append Registry events, and
returns the same result, snapshot and decision hashes.

## Actions not authorized by this snapshot

- Git tag or GitHub Release creation;
- PyPI or other package publication;
- repeat paid model execution or a full benchmark run;
- official benchmark submission or leaderboard claim;
- deployment, traffic routing, checkpoint activation, or model training.
