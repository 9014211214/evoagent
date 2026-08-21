# Accepted local-policy promotion Lab

## Purpose

`AcceptedLocalPolicyPromotionLab` is the executable v2.2 integration path. It
starts from the exact three artifacts already accepted by the v2.1 trust
boundary:

```text
FullyAttestedProgramLocalRLBindingPackage
ProgramLocalRLTrustedAnchors
ProgramLocalRLAcceptanceReceipt
```

It does not invent optimizer metrics, training evidence or a checkpoint. The Lab
uses the accepted initial and selected checkpoint hashes and executes only the
separately governed local Registry pointer lifecycle.

## Import

```python
from evoagent.lab import AcceptedLocalPolicyPromotionLab
```

The root package exports the implementation from:

```text
evoagent.lab.local_policy_promotion_final
```

The exact-head and installed-Wheel gates pin this module identity so packaging
cannot silently fall back to an earlier internal layer.

## Execution

```python
lab = AcceptedLocalPolicyPromotionLab(
    "./local-policy-lab",
    accepted_program_package=fully_attested_package,
    trusted_anchors=anchors,
    acceptance_receipt=receipt,
    source_commit="<40-character v2.2 commit>",
    perform_rollback=True,
)

first = lab.run()
second = lab.run()

assert first.resumed is False
assert second.resumed is True
assert first.package_hash == second.package_hash
```

With `perform_rollback=True`, the first run executes:

```text
register P0
admit P1 from accepted v2.1 evidence
independent Promotion assessment and decision
Promotion Campaign + two approvals
Registry promotion authorization
P0 -> P1 pointer activation
independent Rollback request and assessment
Rollback Campaign + two approvals
Registry rollback authorization
P1 -> P0 pointer rollback
export reproducible v2.2 package
```

With `perform_rollback=False`, the Lab stops after P1 becomes active and exports
a valid Promotion-only package.

## Restart contract

If the final package already exists, `run()` does not re-enter Promotion or
Rollback. It:

1. loads and recursively verifies the package;
2. confirms the supplied accepted package, anchors and receipt are identical;
3. verifies the Registry audit chain and external checkpoint;
4. verifies the Campaign audit chain and external checkpoint;
5. verifies the persisted initial/candidate records and final active head;
6. returns `resumed=True` without appending any event.

The regression suite snapshots the semantic Registry and Campaign state before
the second invocation and requires exact equality afterwards:

```text
active Head
all immutable versions
Registry audit events and checkpoint
Campaign audit events and checkpoint
exported Package bytes and Package Hash
```

SQLite WAL/SHM bytes are deliberately not used as the read-only criterion.

## Interrupted lifecycle recovery

The public lifecycle can also resume before the final package exists:

```text
Registry evidence committed
Campaign only at OPEN
    -> attach exact persisted Candidate
    -> finish missing evaluation states
    -> keep Registry events unchanged

Registry pointer committed
Campaign still AUTHORIZED
    -> verify exact actor/revision/evidence
    -> write only Campaign COMPLETED
```

After either recovery, an exact second call is read-only. Changed evidence,
actor, family, candidate, direct parent or optimistic revision fails closed.

## Result

`LocalPolicyPromotionLabResult` reports:

- family and candidate IDs;
- final active policy ID and optimistic revision;
- whether the run resumed;
- Promotion and Rollback completion;
- Registry and Campaign event counts;
- exact package path and hash;
- authority-boundary flags.

The result and package always retain:

```text
local_policy_pointer_mutation_only = true
foundation_model_weights_updated = false
production_activation_performed = false
production_deployment_performed = false
```

## Verification gates

The v2.2 exact-head workflow performs:

- Python 3.11 and 3.12 compilation;
- base and final source invariant checks;
- Campaign API compatibility regression;
- complete Promotion and Rollback lifecycle tests;
- interrupted submission and pointer-completion recovery tests;
- runtime role-separation and argument-binding tests;
- semantic Campaign replay and coherent-rehash tests;
- full accepted-evidence Lab execution;
- Promotion-only Lab execution;
- read-only second invocation;
- complete repository regression;
- Wheel build and clean installation;
- installed root-package and final-module identity checks;
- `pip check`.

No passing claim is valid until those steps execute on the exact final Head. A
GitHub job rejected before runner allocation (`runner_id=0`, `steps=[]`) is an
infrastructure result, not a test result.

## Deliberately absent authority

The Lab does not:

- train or update foundation-model weights;
- activate a provider model checkpoint;
- alter a production Runtime configuration;
- deploy or route production traffic;
- perform an external rollout;
- run or upload an official Benchmark;
- publish a package, model, tag or release.

It mutates only the isolated local-policy Registry pointer under the v2.2
Promotion/Rollback governance contract.
