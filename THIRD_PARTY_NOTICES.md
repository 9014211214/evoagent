# Third-Party Notices

The independently authored core does not include copied third-party source code. The machine-readable source of truth is `THIRD_PARTY_LOCK.json`; this document provides the corresponding human-readable attribution and integration boundaries.

License metadata below was reviewed against the pinned upstream commits on 2026-08-29. A later upstream commit is not automatically covered by this review.

## Harbor

- Repository: https://github.com/harbor-framework/harbor
- Reviewed commit: 0348989adffbb43bf0b410fd36197333239633f1
- License: Apache-2.0
- License path: LICENSE
- License Git blob SHA: 261eeb9e9f8b2b4b0d119366dda99c6fd7d35c64
- Upstream NOTICE at reviewed commit: none found
- Integration method: cli_adapter
- Source copied: false
- Modified: false
- Purpose: Optional containerized evaluation and rollout backend.
- Required attribution: Record Harbor, its repository, reviewed commit, Apache-2.0 license, and CLI-only integration boundary.

Integration boundary:

- Harbor is not a required Python dependency of the core package.
- No Harbor source file is copied into this repository.
- The adapter emits an explicit command plan.
- Execution, upload, and public visibility remain disabled by default.

## ml-intern

- Repository: https://github.com/huggingface/ml-intern
- Reviewed commit: 550a209701701e6a9ac7cac70b8dbd508822d467
- License: Apache-2.0
- License path: LICENSE
- License Git blob SHA: 261eeb9e9f8b2b4b0d119366dda99c6fd7d35c64
- Upstream NOTICE at reviewed commit: none found
- Integration method: cli_adapter
- Source copied: false
- Modified: false
- Purpose: Optional ML research, training-code, and experiment-orchestration backend.
- Required attribution: Record ml-intern, its repository, reviewed commit, Apache-2.0 license, and headless CLI integration boundary.

Integration boundary:

- `ml-intern` is not a required Python dependency of the core package.
- No ml-intern source file is copied into this repository.
- The adapter only generates an argv tuple and runtime configuration.
- Execution is disabled by default.
- Credentials are supplied through environment variables, not prompts or committed files.
- The generated configuration disables trace sharing and requests sandbox tools.
- Training results remain candidates and cannot be deployed by the adapter.

## Resource2Skill

- Repository: https://github.com/microsoft/Resource2Skill
- Reviewed commit: 7f101b4cfe214cc496d085a34efac528a17cc375
- License: MIT
- License path: LICENSE
- License Git blob SHA: 22aed37e650bbf933b6983cda9c2c5db65dcdd04
- Upstream NOTICE at reviewed commit: none found
- Integration method: external_checkout
- Source copied: false
- Modified: false
- Purpose: Optional public-resource-to-Skill extraction and validation backend.
- Required attribution: Record Resource2Skill, Microsoft copyright, its repository, reviewed commit, MIT license, and external-checkout boundary.

Integration boundary:

- Resource2Skill is not a required Python dependency of the core package.
- No Resource2Skill source, generated wiki, or generated Skill is copied into this repository.
- The adapter references an external local checkout and generates the documented `validate-domain` command.
- Any imported output must still pass this project's license, consent, secret, semantic, and sandbox acquisition gates.

## Skill Recorder

- Repository: https://github.com/microsoft/skill-recorder
- Reviewed commit: 93b3ccf887a46d3e3b91ed856d888d399b02c6e4
- Reviewed release: 0.4.2
- License: MIT
- License path: LICENSE
- License Git blob SHA: 22aed37e650bbf933b6983cda9c2c5db65dcdd04
- Upstream NOTICE at reviewed commit: none found
- Integration method: external_checkout
- Source copied: false
- Modified: false
- Purpose: Optional desktop demonstration recorder whose persisted BuiltSkill output can seed a controlled candidate Skill.
- Required attribution: Record Microsoft Skill Recorder, Microsoft copyright, its repository, reviewed commit, MIT license, and persisted skill.json import boundary.

Integration boundary:

- Skill Recorder is not a required package dependency of the core framework.
- No Microsoft Skill Recorder source, recording, screenshot, or generated artifact is copied into this repository.
- Evoagent accepts only an explicitly selected, checksummed, consented version-1 `skill.json` output.
- Import validates architecture, names, values, paths, typed calculation/action steps, unresolved tokens, and secrets before creating an immutable candidate.
- A parsed output is never registered, promoted, deployed, or published without the existing acquisition, evaluation, governance, and promotion gates.
- Evoagent does not capture the screen, invoke GitHub Copilot, or analyze a real recording in CI.

## Terminal-Bench 2.1

- Repository: https://github.com/harbor-framework/terminal-bench-2-1
- Reviewed commit: ffccbe05ee73a9d59518217f294ad711bda39304
- License: Apache-2.0
- License path: LICENSE
- License Git blob SHA: 261eeb9e9f8b2b4b0d119366dda99c6fd7d35c64
- Upstream NOTICE at reviewed commit: none found
- Integration method: dataset_reference
- Source copied: false
- Modified: false
- Purpose: Optional external terminal-agent benchmark dataset.
- Required attribution: Record Terminal-Bench 2.1, its repository, reviewed commit, Apache-2.0 license, and dataset-reference boundary.

Integration boundary:

- No Terminal-Bench task or source file is copied into this repository.
- The Harbor adapter references `terminal-bench/terminal-bench-2-1` explicitly.
- Development mode is private and upload-disabled.
- Leaderboard mode requires explicit opt-in and at least five trials per task.
- This repository does not claim a benchmark result until a real external run is completed and independently validated.

## SEAGym

- Repository: https://github.com/antropy-research/SEAGym
- Reviewed commit: 9e61e14db1f1355de944cd7c5b10c244fc74e82d
- License: Apache-2.0
- License path: LICENSE
- License Git blob SHA: 261eeb9e9f8b2b4b0d119366dda99c6fd7d35c64
- Upstream NOTICE at reviewed commit: none found
- Integration method: external_checkout
- Source copied: false
- Modified: true, only in the transient workflow checkout
- Purpose: Optional outer training and frozen-evaluation lifecycle for the Terminal-Bench 2.0 scientific pilot.
- Required attribution: Record SEAGym, its repository, reviewed commit, Apache-2.0 license, external-checkout boundary, and the controlled token-redaction patch hash.

Integration boundary:

- Modifications summary: The external checkout receives one hash-pinned workflow patch that preserves numeric token-count telemetry while retaining credential redaction; no SEAGym source is vendored in this repository.
- No SEAGym source file is copied into the EvoAgent source tree.
- The workflow must verify the exact checkout commit and the original
  seagym/logging/redaction.py Git blob
  daa4fe84a28c63b68aaaffa6318e82a54b7be2df before patching.
- The only allowed modification is
  experiments/seagym_terminalbench/patches/seagym-token-count-redaction.patch,
  SHA-256
  0c5302339bdcbeec076796b38f6ffd81803ce7f40cec1922c410294e8472018c.
  It preserves numeric token-count telemetry while retaining redaction for
  credential-shaped values.
- Applying that patch to a transient checkout is not an upstream contribution,
  fork, or claim that upstream accepted the change.

## Harbor (SEAGym runtime)

- Repository: https://github.com/harbor-framework/harbor
- Reviewed commit: f7110f1a240c6a50589b90c4d69714763946d088
- License: Apache-2.0
- License path: LICENSE
- License Git blob SHA: 261eeb9e9f8b2b4b0d119366dda99c6fd7d35c64
- Upstream NOTICE at reviewed commit: none found
- Integration method: external_checkout
- Source copied: false
- Modified: false
- Purpose: Exact Harbor runtime selected by the pinned SEAGym gitlink for the scientific pilot.
- Required attribution: Record the Harbor SEAGym runtime, its repository, reviewed gitlink commit, Apache-2.0 license, and external-checkout boundary.

Integration boundary:

- This is a second, purpose-specific pin of the same official Harbor
  repository; it does not replace the existing stable CLI-adapter review pin.
- SEAGym commit 9e61e14db1f1355de944cd7c5b10c244fc74e82d
  records this exact commit as the reference/harbor gitlink.
- No Harbor source file is copied or modified in this repository.

## Terminal-Bench 2.0

- Repository: https://github.com/harbor-framework/terminal-bench-2
- Reviewed commit: 2fd12b88aafdd04a52c298e3940bcb189f9766d6
- License: Apache-2.0
- License path: LICENSE
- License Git blob SHA: 261eeb9e9f8b2b4b0d119366dda99c6fd7d35c64
- Upstream NOTICE at reviewed commit: none found
- Integration method: dataset_reference
- Source copied: false
- Modified: false
- Purpose: Pinned external task environments and verifiers for the score-blind 6/3/3 SEAGym pilot.
- Required attribution: Record Terminal-Bench 2.0, its repository, reviewed commit, Apache-2.0 license, and data-reference-only boundary.

Integration boundary:

- The committed task index contains identifiers, public attributes, scoring
  metadata, and data://terminal-bench-2 references only.
- No task instruction, image, environment, solution, verifier, or raw run
  artifact is copied into this repository.
- The pilot is not a Terminal-Bench leaderboard submission.

## MiMoCode

- Repository: https://github.com/XiaomiMiMo/MiMo-Code
- Reviewed tag: v0.1.13
- Reviewed commit: 67c9cf1e26288d03c65fb844be71f39581ffc1de
- License: MIT
- Copyright: 2026 MiMo Code, Xiaomi Corporation; 2025 opencode
- License path: LICENSE
- License Git blob SHA: 83621ff267c81af7d7ac26254c4ec81d917f4a82
- Upstream NOTICE at reviewed commit: none found
- Integration method: subprocess_adapter
- Source copied: false
- Modified: false
- Purpose: Hash-pinned terminal Agent runtime used by the optional SEAGym scientific pilot.
- Required attribution: Record MiMoCode, Xiaomi Corporation and opencode copyright, repository, v0.1.13 commit, MIT license, and subprocess-only boundary.

Integration boundary:

- The workflow may download only mimocode-linux-x64.tar.gz from the official
  v0.1.13 GitHub release.
- The expected archive SHA-256 is
  0997a43647a99969d0194fad71af1fd6112aa8220e24a4562aea63953b1e1ada;
  execution must fail closed before extraction if it differs.
- No MiMoCode source or binary is committed, redistributed, or installed as a
  core dependency.

## External model service used only by optional calibration and pilot

The optional Full-Agent integration calibration and SEAGym pilot can call
Xiaomi MiMo-V2.5 through OpenRouter. Neither OpenRouter source nor Xiaomi model
weights are copied, vendored, downloaded, redistributed or included as a
package dependency. The independently authored adapter uses OpenRouter's
public HTTPS API contract and an owner-supplied credential. The pilot freezes
the request model xiaomi/mimo-v2.5, accepted response model identities
xiaomi/mimo-v2.5 and xiaomi/mimo-v2.5-20260422, Xiaomi FP8-only routing,
required Xiaomi response-provider identity, no fallback, required parameter
support, and reasoning disabled. Model access, pricing, data handling and use
remain subject to the providers' current terms:

- OpenRouter: https://openrouter.ai/
- Xiaomi MiMo-V2.5 model page: https://openrouter.ai/xiaomi/mimo-v2.5

The machine-readable source lock covers source-code, dataset and checkout
integrations with pinned Git commits. It does not misrepresent a hosted API or
remote model endpoint as a vendored Git component.

## Policy for future integrations

Every integration must update both `THIRD_PARTY_LOCK.json` and this file with:

- project name and repository;
- reviewed commit;
- SPDX license identifier;
- license and NOTICE file paths plus pinned Git blob SHAs;
- integration method;
- whether source was copied or modified;
- required attribution and purpose.

Preferred order: API/CLI -> dependency -> subprocess adapter -> submodule -> fork -> source-code copy.
