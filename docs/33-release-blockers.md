# Current release blockers

Updated: 2026-08-29

## Public source snapshot

The new `9014211214/evoagent` repository is public. The history-free candidate
passed the final regression, third-party, secret, privacy, file-inventory, and
author-metadata checks and contains one GitHub no-reply-authored root commit.
No source-publication blocker remains. The current evidence-hardening changes
remain in Draft PR #7 until its exact-head checks and review are complete.

The original development repository remains private. Its commits, PRs, Actions logs, artifacts, and repository Secrets are not publication inputs.

## Performance-claim blocker: no complete comparison

The current hosted workflow cannot complete the 180-trial no-skill and 270-trial EvoAgent schedules within its existing sequential job envelope. No approved full-run spend ceiling or validated checkpoint/resume protocol exists.

The completed two-task Qwen smoke is partial integration evidence. It produced both reports and an all-zero partial delta, but the retained comparison explicitly records `publishable_full_benchmark=false`. No public benchmark score or leaderboard claim is permitted from that run.

The separate frozen 12-Task Full-Agent seed completed and passed its narrow
controlled-mechanism gate. It does not remove this authoritative benchmark
blocker and is not substituted for the missing SkillEvolBench comparison.

## Separate future actions

A Git tag, GitHub Release, package publication, repeat paid model run, full
benchmark, official submission, or deployment requires its own authorization
and evidence. None is authorized by the source-snapshot publication or PR #7.
