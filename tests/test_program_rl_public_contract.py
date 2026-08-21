from evoagent.program_rl import (
    AttestedProgramLocalRLPackageManager,
    EvoagentLocalRLPackageAttestationError,
    EvoagentLocalRLPackageAttestor,
    EvoagentLocalRLPackageProjector,
    FullyAttestedProgramLocalRLPackageManager,
    NativeLocalRLProjectionSpec,
    NativeLocalRLRuntimeContractBuilder,
    ProgramLocalRLAcceptanceManager,
    ProgramLocalRLAdapter,
    ProgramLocalRLPackageManager,
    RunningAttestedProgramLocalRLPackageManager,
    RunningGenerationIntentBindingManager,
    RuntimeAttestedProgramLocalRLPackageManager,
    RuntimeBoundNativeLocalRLAttestor,
    SchemaAttestedProgramLocalRLPackageManager,
    build_trusted_anchors,
)


def test_program_local_rl_public_api_exposes_final_runtime_contracts():
    assert ProgramLocalRLAdapter.__module__ == (
        "evoagent.program_rl.adapter_attested_final"
    )
    assert hasattr(ProgramLocalRLAdapter, "build_intent_from_attestation")
    assert NativeLocalRLRuntimeContractBuilder.__module__ == (
        "evoagent.program_rl.native_contract"
    )
    assert EvoagentLocalRLPackageProjector.__module__ == (
        "evoagent.program_rl.native_contract"
    )
    assert EvoagentLocalRLPackageAttestor.__module__ == (
        "evoagent.program_rl.evoagent_native"
    )
    assert EvoagentLocalRLPackageAttestationError.__module__ == (
        "evoagent.program_rl.evoagent_native"
    )
    assert RuntimeBoundNativeLocalRLAttestor.__module__ == (
        "evoagent.program_rl.native_contract"
    )
    assert NativeLocalRLProjectionSpec.__module__ == (
        "evoagent.program_rl.schema_attestation"
    )


def test_public_managers_use_recursive_final_verifiers():
    assert ProgramLocalRLPackageManager.__module__ == (
        "evoagent.program_rl.package_verified_public_final"
    )
    assert RunningGenerationIntentBindingManager.__module__ == (
        "evoagent.program_rl.intent_binding_verified_final"
    )
    assert RunningAttestedProgramLocalRLPackageManager.__module__ == (
        "evoagent.program_rl.intent_binding_verified_final"
    )
    for manager in (
        AttestedProgramLocalRLPackageManager,
        SchemaAttestedProgramLocalRLPackageManager,
        RuntimeAttestedProgramLocalRLPackageManager,
    ):
        assert manager.__module__ == "evoagent.program_rl.stage_managers_final"
    assert FullyAttestedProgramLocalRLPackageManager.__module__ == (
        "evoagent.program_rl.evidence_verified_public_final"
    )
    assert ProgramLocalRLAcceptanceManager.__module__ == (
        "evoagent.program_rl.trusted_acceptance"
    )
    assert build_trusted_anchors.__module__ == (
        "evoagent.program_rl.trusted_acceptance"
    )


def test_public_managers_do_not_fall_back_to_base_modules():
    assert RuntimeAttestedProgramLocalRLPackageManager.__module__ != (
        "evoagent.program_rl.runtime_attested_package"
    )
    assert ProgramLocalRLPackageManager.__module__ != (
        "evoagent.program_rl.package"
    )
    assert AttestedProgramLocalRLPackageManager.__module__ != (
        "evoagent.program_rl.attested_package"
    )
    assert SchemaAttestedProgramLocalRLPackageManager.__module__ != (
        "evoagent.program_rl.schema_attested_package"
    )
