import evoagent.local_rl as local_rl
from evoagent.local_rl import ProgramLocalRLBindingManager


def test_local_rl_public_api_uses_persistent_program_binding_manager():
    assert ProgramLocalRLBindingManager.__module__ == (
        "evoagent.local_rl.program_binding_persistent"
    )
    assert hasattr(ProgramLocalRLBindingManager, "build_ticket")
    assert hasattr(ProgramLocalRLBindingManager, "export_file")
    assert hasattr(ProgramLocalRLBindingManager, "load_file")


def test_generic_legacy_program_adapter_is_not_exported():
    for name in (
        "build_program_local_rl_binding_package",
        "build_program_local_rl_evidence",
        "build_program_local_rl_execution_authorization",
        "build_program_local_rl_intent",
    ):
        assert not hasattr(local_rl, name)
