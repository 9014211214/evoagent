import json
import shutil

import pytest

from evoagent.lab import MultiGenerationEvolutionProgramLab
from evoagent.model_registry.models import canonical_sha256
from evoagent.program import EvolutionProgramPackageManager


@pytest.fixture(scope="module")
def source_package(tmp_path_factory):
    root = tmp_path_factory.mktemp("program-provenance")
    result = MultiGenerationEvolutionProgramLab(
        root / "lab",
        source_commit="c" * 40,
    ).run()
    package = EvolutionProgramPackageManager().load_file(result.package_path)
    return result.package_path, package


def _copy_and_rewrite(source, destination, mutate):
    shutil.copyfile(source, destination)
    payload = json.loads(destination.read_text(encoding="utf-8"))
    mutate(payload)
    payload["package_hash"] = canonical_sha256(
        {key: value for key, value in payload.items() if key != "package_hash"}
    )
    destination.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def test_program_starts_after_both_release_packages(source_package):
    _, package = source_package
    g0 = package.generations[0]
    assert g0.outcome is not None
    release_ready_at = max(
        package.drift_release_package.created_at,
        package.passing_release_package.created_at,
    )
    assert release_ready_at < g0.outcome.completed_at
    assert g0.outcome.completed_at <= package.signal.created_at
    assert package.signal.created_at <= package.attribution.created_at
    assert package.attribution.created_at <= package.decisions[0].decided_at
    assert package.decisions[-1].decided_at <= package.created_at


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("framework_version", "9.9.9-forged"),
        ("source_repository", "https://example.invalid/forged/repository"),
        ("source_commit", "0" * 40),
        ("third_party_lock_hash", "0" * 64),
    ),
)
def test_package_rejects_rehashed_top_level_provenance_substitution(
    source_package,
    tmp_path,
    field,
    value,
):
    source, _ = source_package
    target = tmp_path / f"forged-{field}.json"

    def mutate(payload):
        payload[field] = value

    _copy_and_rewrite(source, target, mutate)
    with pytest.raises(ValueError, match=field.replace("_", ".*") + ".*release provenance"):
        EvolutionProgramPackageManager().load_file(target)


def test_package_rejects_rehashed_creation_time_before_evidence(
    source_package,
    tmp_path,
):
    source, package = source_package
    target = tmp_path / "forged-created-at.json"

    def mutate(payload):
        payload["created_at"] = (
            package.drift_release_package.created_at.isoformat()
            .replace("+00:00", "Z")
        )

    _copy_and_rewrite(source, target, mutate)
    with pytest.raises(ValueError, match="causal chronology"):
        EvolutionProgramPackageManager().load_file(target)


def test_package_rejects_embedded_release_plan_commit_substitution(source_package):
    _, package = source_package
    forged_plan = package.drift_release_package.plan.model_copy(
        update={"source_commit": "0" * 40}
    )
    forged_release = package.drift_release_package.model_copy(
        update={"plan": forged_plan}
    )
    forged_package = package.model_copy(
        update={"drift_release_package": forged_release}
    )

    with pytest.raises(ValueError, match="embedded ReleasePlan"):
        EvolutionProgramPackageManager._verify_source_identity(forged_package)
