# SkillEvolBench result template

Use this only for exact workflow artifacts. Never hand-enter or infer scores.

## Workflow identity

- mode: <preflight | smoke | compare>
- publishable full benchmark: <true only for a complete compare>
- workflow run URL / attempt: <required>
- job IDs: <required>
- GitHub runner image: <required>
- artifact ID / name / digest: <required>
- EvoAgent commit: <required>
- SkillEvolBench commit: 9e3daa339987c3cfa624121e1be442593a53d43c
- benchmark Harbor version: 0.7.0
- benchmark Harbor commit: 5a22a1dd4cc42fff8418bcb1e796ceb3624df931
- provider: OpenRouter
- model ID: qwen/qwen3-coder-plus
- model preset: configs/skillevolbench/openrouter-qwen3-coder-plus.yaml
- Harbor agent: claude-code
- agent wire API: Anthropic Messages through https://openrouter.ai/api
- inference config hash / exact settings: <required>
- order seed: <A | B | C>
- authenticated probe status: <required, never include the Secret>

## Baseline evidence

- condition: no_skill
- run ID: <required>
- config SHA-256: <required>
- full_report.json SHA-256: <required>
- imported evidence hash: <required>
- learning_sr: <from importer>
- evaluation_sr: <from importer>
- overall_sr: <from importer>
- context_shift / T4: <from importer>
- adversarial / T5: <from importer>
- composition / T6: <from importer>
- active skill count: <from importer>

## EvoAgent evidence

- condition: evoagent_unique_attribution
- run ID: <required>
- evolution rounds: <required>
- config SHA-256: <required>
- full_report.json SHA-256: <required>
- imported evidence hash: <required>
- learning_sr: <from importer>
- evaluation_sr: <from importer>
- overall_sr: <from importer>
- context_shift / T4: <from importer>
- adversarial / T5: <from importer>
- composition / T6: <from importer>
- final retention / forgetting: <if reported>
- negative transfer / revision hurt: <if reported>
- active skill count: <from importer>

## Derived comparison

- same model/provider/settings: <pass required>
- same upstream commit: <pass required>
- same seed and assets: <pass required>
- delta overall_sr: <derived by importer>
- delta context shift: <derived>
- delta adversarial: <derived>
- delta composition: <derived>
- partial smoke: <true makes result non-publishable>
- official submission: false unless separately evidenced
- leaderboard accepted: false unless separately evidenced

## Blocked-run record

When execution fails, record:

- last completed step;
- failed job and attempt;
- whether steps was null;
- whether a log blob exists;
- whether the Secret check, provider probe, Docker build, task execution, report generation, import, and artifact upload actually occurred;
- exact quota/model/disk/timeout error only when the log states it.

Current status: no publishable full result exists. Exact-head run 32245387979 passed the authenticated real-edit probe for `qwen/qwen3-coder-plus` and completed two ordered no_skill tasks. Artifact 9363084982 has digest sha256:d24b2189b34d28f2c822b355a75bc2cfbf347a7c5f581b11433022c3fbeede32. The no_skill report SHA-256 is aab1c2b7a9576504fd4537f11d0a35affd326e0adfd1f32cdcec462e0303f4cf; normalized rewards are 1.0 and 0.9, and upstream pass-threshold `overall_sr` is 0.5. The report recorded USD 6.494378 of agent cost. EvoAgent stopped during Skill induction because OpenRouter returned HTTP 402 `Insufficient credits`, so no EvoAgent report, comparison import, or delta exists. Label all of these values `partial smoke only; not a benchmark score`. Full compare was not launched because credits are exhausted, projected duration exceeds the current hosted-job envelope, and no full-experiment spend ceiling has been approved.
