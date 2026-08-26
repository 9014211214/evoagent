# SkillEvolBench release protocol

Updated: 2026-08-20

## Scope boundary

The pinned bridge is **Skill-component only**. It replaces Skill-evolution
strategy inside the external runtime; it does not execute EvoAgent's Router,
bounded Memory, numeric Agent Policy, unified snapshot Registry or full
continual loop. Workflow identity and imported comparison artifacts therefore
record:

```text
agent_scope=skill_component
evaluated_components=[skill]
full_agent_evidence=false
```

Even a complete schedule can support a Skill-module claim only. Whole-EvoAgent
evidence must use the Full-Agent adapter contract described in document 36.

## Pinned identities

- repository: AIoT-MLSys-Lab/SkillEvolBench
- commit: 9e3daa339987c3cfa624121e1be442593a53d43c
- benchmark Harbor version: 0.7.0
- benchmark Harbor commit: 5a22a1dd4cc42fff8418bcb1e796ceb3624df931
- provider: OpenRouter
- verified model ID: qwen/qwen3-coder-plus
- preset: configs/skillevolbench/openrouter-qwen3-coder-plus.yaml
- Harbor agent: claude-code
- agent wire API: Anthropic Messages through https://openrouter.ai/api
- repository Secret: OPENROUTER_API_KEY
- default order seed: A
- agent execution policy: maximum 64 turns; maximum 8,192 response/file-read output tokens; 100,000-token assumed context; auto-compaction at 50%

The model ID is not guessed. scripts/verify_openrouter_model.py checks OpenRouter's live /api/v1/models catalogue and stores sanitized evidence. The Secret value is never committed or printed.

Manual runs also expose two independent smoke-only presets: `qwen3.7-plus` (`qwen/qwen3.7-plus`) and `glm-5.2-free` (`z-ai/glm-5.2:free`). Qwen3.7 Plus was live-catalogue and hosted-smoke verified on 2026-08-20. The GLM live catalogue record showed zero prompt/completion prices, 256K context, and support for `tools` and `tool_choice` on 2026-08-19, but its free shared pool remained intermittently rate-limited. Neither preset silently replaces the pinned Qwen Coder Plus release identity. Any no_skill/EvoAgent comparison must use the same selected preset for both conditions and record that model identity in its artifacts.

## One-click workflow

Use GitHub Actions → SkillEvolBench benchmark → Run workflow.

Modes:

| Mode | Work performed | Claim status |
|---|---|---|
| preflight | hosted-runner disk cleanup; pinned checkouts; Python and Harbor installation; live model lookup; upstream config/asset/preflight validation; no_skill and EvoAgent dry-run; bridge tests; artifact upload | no provider call and no score |
| smoke | all preflight work, an authenticated real-edit probe, one Harbor runtime build, two ordered no_skill tasks and two ordered EvoAgent tasks on the already allocated runner, partial report import, SHA-256, and artifact upload | partial and non-publishable |
| compare | preflight plus isolated full no_skill and EvoAgent conditions, exact report import, control verification, SHA-256, delta, and artifacts | publishable only if every gate passes |

Pull-request execution is hardwired to read-only `preflight` mode and the pinned Qwen preset; pull-request-controlled files cannot select a secret-bearing smoke or compare path. Only `workflow_dispatch` inputs can select preflight, smoke, or compare; seed A, B, or C; and the pinned Qwen preset or either smoke-only preset. Full compare additionally requires an explicit acknowledgement and rejects both smoke-only presets before installation or any provider request.

Smoke intentionally reuses the preflight runner. This avoids an otherwise observed downstream runner-allocation failure and builds the Docker image only once. Full compare retains separate condition jobs because each condition can approach the hosted-runner time limit.

Before either real mode builds `agent-runtime`, the workflow copies the pinned checkout's `docker/agent-build` directory to runner temporary storage. A deterministic, fail-closed preparation step removes the copied installer's forced AWS us-east-1 Ubuntu mirror call, preserves the Ubuntu base image's default mirrors, and adds five bounded apt retries with a 30-second HTTP timeout. The pinned external checkout remains clean. Before/after SHA-256 and the exact preparation patch are uploaded with runtime evidence.

SkillEvolBench's pinned runtime patch calls Harbor `Trial._execute_agent`. Harbor v0.7.0 is the last tagged release exposing that API; later releases refactor it away. The workflow therefore uses a benchmark-only exact Harbor commit and verifies both package version and method presence before any real task. This compatibility pin is separate from the core Harbor CLI adapter's third-party review pin.

## EvoAgent condition

The external SkillEvolBench runtime continues to own task order, Harbor execution, model harness, verifier, stores, and report generation. EvoAgent replaces only the Skill-evolution strategy:

- T4-T6 evaluation: frozen NoOp;
- T1 with no family seed: induce an initial Skill;
- learning pass: NoOp;
- learning failure: revise only one uniquely supported same-family target;
- missing or ambiguous target: NoOp and fail closed.

The no_skill control and EvoAgent condition must keep the model, provider, inference settings, upstream commit, order seed, task assets, and evaluation protocol fixed.

## Bounded agent context policy

Harbor's `claude-code` adapter is the code-editing agent shell; the selected OpenRouter model remains the inference model. Claude Code's agent loop sends tool results into subsequent model decisions, so an unbounded task can repeatedly carry a growing single-task context even when SkillEvolBench history, Skill-library, trajectory-RAG, and feedback-memory channels are all disabled.

All OpenRouter presets therefore set `CLAUDE_CODE_MAX_TURNS=64`, `CLAUDE_CODE_MAX_OUTPUT_TOKENS=8192`, `CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS=8192`, `CLAUDE_CODE_MAX_CONTEXT_TOKENS=100000`, and `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=50`. The 64-turn guard covers the 55 and 41 tool calls observed in the historical successful tasks; the context window and 50% threshold are the actual compaction controls. The launcher persists all five non-secret values into Harbor `agent_kwargs`, making them part of the importer's same-control comparison identity. Workflow identity also records the exact model-preset SHA-256 without exposing credentials. The standalone tool-effect probe loads and records the same allowlisted policy. The adapter instruction asks for targeted searches, bounded command/file output, no unchanged rereads, the smallest sufficient edit, and the narrowest relevant verification.

The runtime also pins Claude Code 2.1.235, the exact tool shell recorded by the successful Qwen probe, and fails the image build if `claude --version` does not match. This is a runtime reproducibility control only: the selected OpenRouter Qwen or GLM endpoint remains the inference model.

Every retained trajectory summary includes only Harbor final usage totals (prompt, completion, cache, steps, optional reported USD cost, and cache creation/read totals), model-request count, per-request prompt/output maxima, and structural counts. Duplicate mounted/artifact copies are SHA-256-deduplicated. Raw messages, tool arguments, and observations remain excluded, and real conditions run without verbose prompt logging. These controls bound work; they do not guarantee a fixed token count or task success. Results produced under the bounded policy cannot be treated as directly comparable to the earlier unbounded smoke.

## Evidence import and publication rule

A successful condition must produce reports/full_report.json and config.json. The workflow copies exact bytes, computes SHA-256, records upstream/Harbor/EvoAgent/model/seed identity, and calls scripts/import_skillevolbench_comparison.py. Smoke passes --partial-smoke and can never be published as a full result. The importer rejects a smoke whose attempted-task count differs from its positive `max_tasks` cap and rejects a full comparison unless no_skill attempted exactly 180 trials and EvoAgent attempted exactly 270 trials.

A project number may appear only when:

- both full conditions execute;
- the EvoAgent bridge participates in the evolved condition;
- both exact report hashes and artifact digests are retained;
- the importer accepts both reports;
- non-studied controls match;
- aggregate, transfer/adversarial/composition, and retention/forgetting fields are reported as available;
- no official submission or leaderboard acceptance is claimed without separate evidence.

## Recorded execution evidence

Latest successful preflight:

- historical private validation run: 32244987047 (logs and artifacts were not copied into the public repository)
- commit: ebe556fb1703312336f17b93786c86bca716dc90
- artifact ID: 9362352109
- artifact digest: sha256:a289847a5524337750b1e167d829a55b39e4efceb459adae75f032e61a473fae
- runner: GitHub-hosted Ubuntu 24.04
- pinned Claude Code adapter patch: installed and contract-checked against exact Harbor v0.7.0 source
- upstream assets: 30 families and 180 tasks validated
- focused bridge/import tests: 26 passed
- dry-run: no_skill and EvoAgent launch plans completed

Observed smoke blockers:

- historical private validation run: 32171202279 (logs and artifacts were not copied into the public repository)
- the no_skill authenticated probe received OpenRouter HTTP 403 `Key limit exceeded (total limit)`; artifact 9347037736 has digest sha256:3a95cc977312db45546beb8159eb6c9bff0cf38d311c13a97eac438bd105c876;
- the owner reports raising that key total limit, but a later probe has not verified it;
- the evoagent runtime build failed after about 2 hours 33 minutes when `node-supports-color` returned HTTP 503 from the upstream-forced AWS us-east-1 Ubuntu mirror; artifact 9350183087 has digest sha256:0dc32e52d8a3cdc7dd6b9611ed83a867cc2f277dd4c88e7d23fd3d46ed45005f;
- no benchmark task, report, score, or delta occurred.

Earlier attempts also recorded runner-allocation failures. Run 32196903424 temporarily confirmed allocation after the Actions budget increase. The runtime mirror adaptation is implemented locally and must still pass on a hosted smoke run.

After the mirror fix was pushed as commit 703b22566da0bcda091a72e1c03e6906a6cad238, benchmark run 32211209892 again failed before preflight acquired a runner. Its required job has `steps=null` and no log URL. The companion release-readiness run 32211209903 and CI run 32211210007 have the same zero-step state. Therefore neither the mirror adaptation nor the reported OpenRouter limit increase has hosted verification yet.

After payment/budget state was updated, exact-head preflight rerun 32211305279 passed. Artifact 9351532301 has digest sha256:15dccc55b703a8973d5a4c386865d0ab921de1115e84a4e6bba6d16c276baccc. Smoke run 32213647962 then passed the authenticated OpenRouter probe and verified the mirror adaptation: 32.2 MB of indexes downloaded at 26.4 MB/s and 67.8 MB of packages at 15.9 MB/s. The build failed after about 81 seconds because floating OpenClaw `latest` resolved to 2026.7.1-2 and its current CLI routed legacy `setup --workspace` into interactive onboarding. Smoke artifact 9351661486 has digest sha256:e5e97d3fecfaea1ea0f2a0b7950c58b2780d35b328d4145e975d6f7802beca37. No task, report, score, or delta occurred.

The temporary-copy preparation now pins OpenClaw 2026.7.1-2 and replaces that one copied command with the documented non-interactive baseline form, `setup --baseline --workspace`. The pinned checkout remains unchanged. This compatibility adaptation must pass another hosted smoke before either result condition can be claimed.

Run 32216993122 hosted-verified that OpenClaw adaptation and completed the runtime build. It then failed both bounded conditions before agent execution because Harbor commit 0348989adffbb43bf0b410fd36197333239633f1 no longer exposes `Trial._execute_agent`. Preflight artifact 9352715261 has digest sha256:40cd3944f08f8454f5543a456578f06f2348a172958055f89de2304b214b3645; smoke artifact 9352769035 has digest sha256:393f5fbe48ef3b4d4c706c9012b6f00f80b399f395294abda824022c855f8acf. The benchmark-only Harbor pin is now v0.7.0 commit 5a22a1dd4cc42fff8418bcb1e796ceb3624df931 and awaits hosted smoke verification. No task report, score, or delta occurred.

Run 32218315302 then passed with that exact Harbor pin. It completed no_skill and EvoAgent one-task conditions, imported both reports, checked controls, computed SHA-256, and uploaded artifact 9353272657 with digest sha256:5bebc643823c806fb3de9eae334bda44555689b68cb7ab1412556f9e9cf66712. Report hashes are 1e2e325b16a4796cacd3c176e8872186ced38524330285acbff59f864e2673d6 (no_skill) and 637d395e1eb0dc9a64c1cf568d3ec606474c688269077c46738e5374f50a5f23 (EvoAgent); comparison hash is 1dae33ec5251b239d94e8892e3bd6b4ac9b463eb53754206c2a31217b47f8915. Both tasks failed, so the partial delta is 0.0. The comparison records `partial smoke only; not a benchmark score` and `publishable_full_benchmark=false`.

The no-skill task took about 4 minutes 13 seconds. Linear projection for its 180 sequential trials is approximately 12.7 hours, beyond the workflow's 355-minute condition limit and GitHub's standard hosted-job envelope. The EvoAgent baseline schedules 270 trials because it includes 90 within-environment replay trials, so its expected duration is longer still. The full comparison is therefore blocked pending a scientifically valid checkpoint/resume or longer-runner plan. In addition, LiteLLM did not map the Qwen OpenRouter slug for cost calculation; token counts are retained, but zero USD totals in this partial upstream report must not be read as free execution.

Run 32245387979 is the current Qwen Coder Plus smoke evidence. It used exact commit ebe556fb1703312336f17b93786c86bca716dc90 and exact model `qwen/qwen3-coder-plus`. Before the benchmark conditions, Claude Code 2.1.235 changed the controlled probe file, changed its SHA-256 from 71bdccb8f575f1049fa00f1fc2ab6ba89d655be178c869ac7c190839e4fe2330 to 63098273a7aaa630762d6f145dfcf9c61c8c9b290c6ebeb2a05fea5854ccb476, and passed the synthetic unit test. This is the gated proof that the model can invoke editing tools through OpenRouter; a successful text-only response is not accepted.

The no_skill condition completed two ordered tasks. The normalized rewards were 1.0 and 0.9, while upstream pass-threshold aggregation reported `overall_sr=0.5`. Its exact report SHA-256 is aab1c2b7a9576504fd4537f11d0a35affd326e0adfd1f32cdcec462e0303f4cf. The report recorded USD 6.494378 of agent cost, 3,747,185 input tokens, 31,808 output tokens, and 324,992 cache tokens. These are partial-smoke measurements only.

That run preceded the bounded agent context policy. Its no_skill config disabled cross-task history, Skill-library, trajectory-RAG, and feedback-memory use; sanitized trajectories contained 105 and 81 steps with 55 and 41 tool calls. The high prompt-token count is therefore attributed primarily to the unbounded within-task agent loop. It remains valid historical evidence but is not a cost projection for the new policy.

The EvoAgent condition then received OpenRouter HTTP 402 `Insufficient credits` during host-side Skill induction (`limit_source=openrouter_credits`). It produced no report. Therefore comparison import, SHA-256 binding for EvoAgent, and delta calculation did not occur. Artifact 9363084982 has digest sha256:d24b2189b34d28f2c822b355a75bc2cfbf347a7c5f581b11433022c3fbeede32. The runner still had about 30 GB available and the runtime image was about 2.03 GB, excluding disk exhaustion as the cause.

That Qwen run established a credit-availability gate: require both conditions and the comparison-import job to pass before considering a full experiment. The workflow emits a sanitized `openrouter_insufficient_credits` blocker code for the exact HTTP 402 error without copying the raw run log or any secret into the artifact.

After the owner restored OpenRouter credits, the optional GLM free preset was tested separately in run 32253544320. Live catalogue verification, the isolated oracle, both same-model dry-runs, and 27 focused tests passed. The authenticated probe then received HTTP 429 from the Decart free upstream on all six bounded attempts. OpenRouter classified it as `upstream_provider_shared_pool`, not an account-credit failure. Artifact 9365438857 records the sanitized blocker `openrouter_free_shared_pool_rate_limited` with digest sha256:8b9a95f77322bf1dca316c06e2918a0d217df849bb235a642211a0c15ff680aa. No runtime build or task execution occurred, so this attempt has no report, score, delta, or model cost.

Retry run 32256534585 at exact head f65350b5e71afc292319b75a713bab62a0fc9936 passed that authenticated GLM request and built the runtime, leaving about 30 GB free. The stronger tool-effect gate then received HTTP 429 `rate_limit` on all ten Claude Code retries over roughly 195 seconds. Claude Code recorded zero input/output tokens and USD 0 cost, and neither smoke condition started. Artifact 9366901882 has digest sha256:64e6c6ab2c46395d443044e3ff907e0c72e10779b102c9ba51310834327e749a. This demonstrates intermittent free-provider capacity: a successful one-turn probe does not establish enough availability for agent execution.

The first bounded Qwen3.7 Plus attempt, run 32323366701 at f9ab22c9dabf3c8ab683b4696ec3505a2953e82e, verified exact model `qwen/qwen3.7-plus`, the runtime image, and a real tool edit. Both conditions then failed before agent creation because `extra_env` appeared in Harbor `AgentConfig.kwargs` while Harbor 0.7.0 also supplied it from `AgentConfig.env`. Artifact 9390598629 has digest sha256:d937881a8b45a88663a1d5dc5ad59481c1ebc82f092a77f0b75f94066032d37d and contains no task report or score.

Commit 0d54af18c8826b2a5ecf42faf512772ff966c6fd moves the four non-CLI policy values to bridge-owned Harbor `EnvVar` descriptors, leaving `extra_env` solely owned by Harbor. Exact-head smoke run 32324346605 completed both two-task conditions and strict partial import. Report hashes are ca1ccb2713b698a2015073d0c528db37a04ac40639ac1877f39f5f36f53ebdce (no_skill) and dd2aaf5063f76f39690c5fead3de34cf82de53332e7a551f0a84c8b72ad0600b (EvoAgent); comparison hash is f25344b35e0776b20c9443a4c863eff039070691a727a70d9ffb3ad32262b091. Top-level SHA256SUMS independently verified, and local re-import matched every semantic field after excluding import timestamps and their derived evidence hashes.

Both partial conditions recorded `overall_sr=1.0` and the partial delta is 0.0. EvoAgent proposed/applied one unique-attribution patch and recorded two retrieval events. Reported agent costs were USD 1.860921 for no_skill and USD 1.333647 for EvoAgent; their USD 3.194568 sum excludes unpriced host/probe overhead. Artifact 9391310159 has digest sha256:d41b6beb3b0fcdf697d334554659a0eaf50afd57b7328ab91d539247ed20ef1b. These are bounded smoke diagnostics with `publishable_full_benchmark=false`, never a leaderboard or release score.

The probe retries only HTTP 429 and 503, respects `Retry-After`, caps each delay at 30 seconds, and stops after six attempts. HTTP 402 and other non-transient failures are not retried. The workflow never falls back to `openrouter/free` or another random model because doing so would invalidate exact same-model comparison identity.

The separate Claude Code tool-effect gate uses the client's own bounded retry policy. On failure, the workflow retains only a structured, sanitized stage blocker such as `openrouter_tool_probe_rate_limited`; raw agent output is not placed in the artifact.

Missing runner allocation, Secret visibility, provider quota, model access, Docker/Harbor failure, disk exhaustion, timeout, or an unexecuted job must be recorded exactly as NOT RUN or BLOCKED. None may be replaced with a mock score.
