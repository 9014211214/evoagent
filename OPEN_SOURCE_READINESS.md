# Open-Source Readiness

Updated: 2026-08-28

This file records the publication boundary for the public v2.0 Research Preview source snapshot. It does not authorize a Git tag, GitHub Release, package publication, paid model execution, benchmark submission, or performance claim.

## Current gate table

| Gate | State | Evidence / exact boundary |
|---|---|---|
| Repository visibility | PUBLIC SOURCE SNAPSHOT | `9014211214/evoagent` is a new public repository. The original development repository remains private. |
| History isolation | PASSED | The candidate was exported from the reviewed source tree and contains one new root commit. Private commits, branches, PRs, Actions logs, artifacts, and repository Secrets are not copied. |
| Governed self-evolution core | UNIFIED LOCAL PATH IMPLEMENTED | One immutable Agent snapshot now binds Skill, Router, bounded Memory and numeric Agent Policy. All four act in the same Tool-Agent runtime; candidates, frozen evaluation, decision and explicit Registry activation remain separate. |
| Unified continual reference evidence | LOCAL SYNTHETIC PASSED | The zero-cost A0→A4 lab changes Skill, Memory, Router and Policy one at a time and reaches all held-out retention/transfer/adversarial/composition Tasks with zero final regression, forgetting and safety violations. This is mechanism evidence only. |
| Python regression | LOCAL AND HOSTED EXACT-HEAD MATRICES PASSED | Local Python 3.12 collected the complete 709-test matrix in two isolated file shards: 702 passed, 7 skipped, 0 failed. The 42 directly affected unified-runtime, Full-Agent, atomic-path, release-scanner and SkillEvolBench contract tests also pass together. Public code head `415e17f54d373267895b73804228eca74427e18e` completed all five required GitHub Actions workflows, including the full Python 3.11/3.12 jobs. |
| Third-party and license review | READY | The independently authored core is Apache-2.0. The lock, notices, and source-origin boundary avoid vendoring unresolved upstream benchmark source or assets. |
| Secret and privacy review | PASSED ON PUBLICATION CANDIDATE | The deterministic tracked-source scan, Gitleaks 8.30.1 directory scan, identifying-email/local-path scan, suspicious-file inventory, staged-path check, and GitHub no-reply author-metadata check all passed. |
| SkillEvolBench integration | PARTIAL SKILL-COMPONENT SMOKE VERIFIED | A bounded two-task same-model smoke completed both conditions and strict import. It is integration evidence only, evaluates the Skill bridge only, and records `publishable_full_benchmark=false`. New artifacts also bind `agent_scope=skill_component` and `full_agent_evidence=false`. |
| Full same-seed comparison | NOT RUN | No complete 180-trial no-skill / 270-trial EvoAgent comparison exists. No score is inferred or fabricated. |
| Full-Agent external adapter | HOSTED DRY-RUN PASSED | Exact-head run `32970101477` passed the credential-free deterministic double-build and strict adapter tests. Artifact `9607245269` has digest `sha256:375114fb2e3790b6e45327f3ea1b021ce77e0ee3c01500e489c11a6caa61020b`. This proves the binding/import path only. |
| Full-Agent external evaluation | CALIBRATION ONLY; EFFECTIVENESS NOT RUN | One owner-approved MiMo run bound Skill, Router, Memory and numeric Policy in the same real Tool loop and passed its verifier for USD 0.00024206. Sanitized evidence is tracked under `evidence/full-agent/`; it explicitly claims no benchmark score or generalization result. |
| Minimal scientific seed | ONE-REQUEST ROUTE REACHED; REQUIRED-TOOL CONTRACT FAILED; SEED BLOCKED | The lock still binds three retention, three transfer, three adversarial and three composition Tasks, all five A0→A4 snapshot hashes, the exact model/provider policy, seed 43 and a USD 1.20 total ceiling. Private run `33179418764` used one fresh exact-head authorization and exactly one inference request. Fresh price/key preflights passed; OpenRouter returned HTTP 200 from Xiaomi for `xiaomi/mimo-v2.5` (canonical response metadata `xiaomi/mimo-v2.5-20260422`) with 277 prompt plus 32 completion Tokens and complete observed cost USD 0.00004774. The response did not contain the required frozen Tool call, so the verifier recorded `required_tool_call_verified=false`, `status=blocked` and `successful_response_failed_closed_verification`. Artifact `9689044579` has digest `sha256:a38fb4dd9e818ab8cfc26883fa151654ea79d200bb8e0e4586788cdaa1a0bb1b`; its evidence JSON has SHA-256 `7dd79cab6df62ffa38209ee237100fd424651f0dac48025e8650e9150374664e`. No seed episode or score was produced, and the 60-episode seed remains ineligible under the frozen protocol. |
| External model execution | DISABLED BY DEFAULT | Pull requests remain credential-free. The completed calibration used a private one-use manually dispatched workflow, two approval identities, a USD 2 hard stop and a repository Secret that was neither printed nor copied into the public repository. |
| Employment / invention / confidentiality | OWNER CONFIRMED | On 2026-08-20 the owner confirmed Apache-2.0 publication authority and no applicable employment, invention-assignment, confidentiality, or organizational open-source conflict. |

## Publication boundary

The public repository contains source, tests, documentation, pinned integration metadata, and read-only or explicitly dispatched workflows. It intentionally excludes the private Git graph, review discussions, retained Actions logs and artifacts, benchmark outputs, credentials, and local audit working files.

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

The frozen 12-Task set is a separate controlled Full-Agent mechanism test. A
passing external seed may support only the narrow A0→A4 causal-chain claim in
`docs/MINIMAL_SCIENTIFIC_VALIDATION.md`; it is not a SkillEvolBench result,
leaderboard claim, broad generalization result or multi-seed significance test.

The local matrix also verifies read-only restart of the unified lab: the
second invocation does not rerun optimization or append Registry events, and
returns the same result, snapshot and decision hashes.

## Actions not authorized by this snapshot

- Git tag or GitHub Release creation;
- PyPI or other package publication;
- paid model execution or a full benchmark run;
- official benchmark submission or leaderboard claim;
- deployment, traffic routing, checkpoint activation, or model training.
