# Third-Party Notices

The independently authored core does not include copied third-party source code. The machine-readable source of truth is `THIRD_PARTY_LOCK.json`; this document provides the corresponding human-readable attribution and integration boundaries.

License metadata below was reviewed against the pinned upstream commits on 2026-08-09. A later upstream commit is not automatically covered by this review.

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

## External model service used only by optional calibration

The optional Full-Agent integration calibration can call Xiaomi MiMo-V2.5
through OpenRouter. Neither OpenRouter source nor Xiaomi model weights are
copied, vendored, downloaded, redistributed or included as a package
dependency. The independently authored adapter uses OpenRouter's public HTTPS
API contract and an owner-supplied credential. Model access, pricing, data
handling and use remain subject to the providers' current terms:

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
