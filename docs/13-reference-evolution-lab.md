# End-to-End Reference Evolution Lab

## Purpose

The reference lab is a deterministic public/synthetic integration test for the complete framework lifecycle. It does not measure foundation-model intelligence and is not an official benchmark result.

```text
synthetic Skill Recorder BuiltSkill
    -> consent + checksum + import validation
    -> acquisition sandbox
    -> persistent active Skill 0.1.0
    -> observable safe/unsafe task stream
    -> one verified Skill-layer bad case
    -> counterfactual attribution
    -> deduplicated Skill Campaign + immutable candidate 0.2.0
    -> frozen held-out + regression evaluation
    -> Campaign evaluation and independent approval
    -> AUTHORIZED
    -> separate explicit Skill promotion
    -> Campaign COMPLETED
    -> A0/A1 frozen snapshot evaluation
    -> Skill/Campaign/Trace checkpoints
    -> reproducible run bundle
    -> process restart and audit verification
```

## Deliberate separation of authority

The lab keeps three gates separate:

1. Candidate generation does not evaluate or authorize the candidate.
2. Passing frozen evaluation does not promote the candidate.
3. Campaign authorization does not itself modify the active Skill pointer.

Only the final explicit promotion operation receives both a passing `SkillEvaluationDecision` and a matching `AUTHORIZED` Campaign candidate. The Campaign is marked `COMPLETED` only after that promotion succeeds.

## Deterministic reference runtime

The initial imported plan can complete the stable `reference:safe` case. The injected held-out `reference:unsafe` case requires the `reject_unsafe` rule. The reference runtime stores only observable events and has zero model tokens, tool calls, external cost, or network access.

Expected frozen scores:

```text
A0 / Skill 0.1.0: 0.5
A1 / Skill 0.2.0: 1.0
Evolution gain:   0.5
Regression count: 0
```

These values prove the lifecycle wiring only. They must not be presented as a general Agent score.

## Persistence and resume

State is derived from the transactional Skill and Campaign databases plus the append-only Trace store. Running the lab again after completion:

- reuses the same Campaign;
- keeps exactly two Skill versions;
- adds no second failure Trace;
- adds no second approval or promotion event;
- verifies the same active version and audit checkpoints;
- verifies the existing run bundle instead of rebuilding it.

If a process stops after candidate creation but before authorization, the next run resumes from the persisted Campaign and candidate.

## Evidence package

The reproducible run bundle records:

- framework version;
- source repository and commit;
- frozen task manifest and budgets;
- A0/A1 snapshots;
- baseline and evolved case results;
- Skill, Campaign, and Trace checkpoints;
- third-party lock hash;
- external-execution flag set to `false`.

The stable package embeds the SHA-256 of the reviewed `THIRD_PARTY_LOCK.json` as the default evidence identifier, so the installed Wheel does not depend on a source checkout. Stable-source validation proves that this packaged default equals the repository lock hash. A caller may provide another reviewed lock hash explicitly, and that value is then included in both the result document and reproducible command.

The bundle contains no credentials, hidden reasoning, recordings, screenshots, or private business data.

## Local command

```bash
python -m evoagent.lab \
  --root ./.evoagent/reference-lab \
  --source-commit <40-hex-commit>
```

An explicit reviewed evidence hash may be supplied with:

```bash
python -m evoagent.lab \
  --root ./.evoagent/reference-lab \
  --source-commit <40-hex-commit> \
  --third-party-lock-hash <64-hex-sha256>
```

A fully isolated example is also available:

```bash
python examples/reference_evolution_lab.py
```

CI additionally builds the Wheel, installs it into a fresh virtual environment, and executes the reference lab from that installed package.

## Boundaries

The reference lab does not:

- capture the desktop or call Copilot;
- execute Harbor or ml-intern;
- train or deploy a model;
- publish an artifact;
- submit an official benchmark;
- change repository visibility;
- resolve the owner’s final public-license decision.
