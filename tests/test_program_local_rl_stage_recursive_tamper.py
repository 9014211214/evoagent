import pytest

from evoagent.program.hashing import program_payload_hash
from evoagent.program_rl import (
    AttestedProgramLocalRLPackageManager,
    RuntimeAttestedProgramLocalRLPackageManager,
    SchemaAttestedProgramLocalRLPackageManager,
)
from tests.test_program_local_rl_full_lineage import _full_lineage


def _replace_and_hash(model, updates):
    payload = model.model_dump(mode="json", exclude={"package_hash"})
    for key, value in updates.items():
        payload[key] = (
            value.model_dump(mode="json")
            if hasattr(value, "model_dump")
            else value
        )
    return model.model_copy(
        update={
            **updates,
            "package_hash": program_payload_hash(payload),
        }
    )


def test_public_stages_recompute_nested_result_hash(tmp_path, monkeypatch):
    _, _, full, _, _ = _full_lineage(tmp_path, monkeypatch)
    runtime = full.runtime_attested_package
    schema = runtime.schema_attested_package
    attested = schema.attested_package
    base = attested.base_package

    changed_result = base.result.model_copy(update={"result_hash": "f" * 64})
    changed_base = _replace_and_hash(base, {"result": changed_result})

    binding_payload = attested.attested_result.model_dump(
        mode="json",
        exclude={"binding_hash"},
    )
    binding_payload["result"] = changed_result.model_dump(mode="json")
    changed_binding = attested.attested_result.model_copy(
        update={
            "result": changed_result,
            "binding_hash": program_payload_hash(binding_payload),
        }
    )
    changed_attested = _replace_and_hash(
        attested,
        {
            "base_package": changed_base,
            "attested_result": changed_binding,
        },
    )
    changed_schema = _replace_and_hash(
        schema,
        {"attested_package": changed_attested},
    )
    changed_runtime = _replace_and_hash(
        runtime,
        {"schema_attested_package": changed_schema},
    )

    for manager, package in (
        (AttestedProgramLocalRLPackageManager(), changed_attested),
        (SchemaAttestedProgramLocalRLPackageManager(), changed_schema),
        (RuntimeAttestedProgramLocalRLPackageManager(), changed_runtime),
    ):
        with pytest.raises(ValueError, match="result hash mismatch"):
            manager.verify(package)
