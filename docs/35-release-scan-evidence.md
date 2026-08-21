# Release scan evidence

Updated: 2026-08-21

## Publication candidate

- public repository: `9014211214/evoagent`
- visibility: public
- target branch: `main`
- history policy: one new initial commit; no inherited private Git history
- license: Apache-2.0
- tag or GitHub Release: not created
- package publication: not performed
- full benchmark or model spend: not performed

The candidate was exported from the reviewed `main` source tree of the private development repository. Only tracked source bytes were exported. Private branches, commits, PR conversations, Actions logs, artifacts, repository Secrets, and local audit directories were not copied.

## Final candidate checks

All publication-candidate checks passed before push:

- deterministic tracked-source scanner: passed;
- official Gitleaks 8.30.1 directory scan: zero findings; the final rescan examined approximately 10.70 MB including the one-commit Git metadata and local generated test metadata;
- identifying-email and local-path scan: passed;
- suspicious archive/database/model-weight inventory: none found;
- tracked file count: 575; largest tracked file: 55,639 bytes;
- third-party lock/notices verification: 5 components verified;
- staged-path and GitHub no-reply author-metadata checks: passed;
- publication-targeted Python 3.12 regression: 57 passed, 2 skipped;
- closed-loop package-tamper regression: 5 passed.

The reviewed pre-publication source tree had already passed the complete 694-test suite on both Python 3.11 and Python 3.12 on GitHub-hosted Ubuntu. A local Windows attempt additionally confirmed that the broader suite reaches a Linux-specific fake-executable test which Windows rejects with `WinError 193`; that platform-fixture mismatch is not reported as a source pass or product failure. The bounded candidate suites above avoid that invalid cross-platform assertion and completed successfully.

## Benchmark and privacy boundary

No raw prompt, tool argument, observation, trajectory, benchmark output, credential, or private execution log is part of the public snapshot. The repository retains only independently authored integration code and sanitized documentation of the bounded smoke. The smoke is partial and non-publishable; no complete SkillEvolBench comparison exists.
