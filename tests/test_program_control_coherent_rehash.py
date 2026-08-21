import json
import shutil
from datetime import datetime, timedelta

import pytest

from evoagent.lab import MultiGenerationEvolutionProgramLab
from evoagent.model_registry.models import canonical_sha256
from evoagent.program import (
    EvolutionProgramPackageManager,
    ProgramEventType,
    SQLiteEvolutionProgramRepository,
)


@pytest.fixture(scope="module")
def source_package(tmp_path_factory):
    root = tmp_path_factory.mktemp("program-control-rehash")
    result = MultiGenerationEvolutionProgramLab(
        root / "lab",
        source_commit="5" * 40,
    ).run()
    return result.package_path


def _parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _rehash_control(control):
    previous_hash = "0" * 64
    for event in control["events"]:
        event["previous_hash"] = previous_hash
        event["event_hash"] = SQLiteEvolutionProgramRepository._event_hash(
            sequence=event["sequence"],
            event_id=event["event_id"],
            program_id=event["program_id"],
            generation_id=event["generation_id"],
            event_type=ProgramEventType(event["event_type"]),
            actor_id=event["actor_id"],
            reason=event["reason"],
            payload=event["payload"],
            created_at=_parse_time(event["created_at"]),
            previous_hash=previous_hash,
        )
        previous_hash = event["event_hash"]
    control["checkpoint"] = {
        "event_count": len(control["events"]),
        "head_hash": previous_hash,
    }
    control["control_hash"] = canonical_sha256(
        {key: value for key, value in control.items() if key != "control_hash"}
    )


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


def test_package_rejects_rehashed_budget_feedback_ingestor(
    source_package,
    tmp_path,
):
    target = tmp_path / "budget-feedback-ingestor.json"

    def mutate(payload):
        control = payload["budget_control"]
        control["events"][2]["actor_id"] = control["signals"][0][
            "evidence_producer_id"
        ]
        _rehash_control(control)

    _copy_and_rewrite(source_package, target, mutate)
    with pytest.raises(ValueError, match="feedback ingestor equals"):
        EvolutionProgramPackageManager().load_file(target)


def test_package_rejects_rehashed_ambiguous_attribution_writer(
    source_package,
    tmp_path,
):
    target = tmp_path / "ambiguous-attribution-writer.json"

    def mutate(payload):
        control = payload["ambiguous_control"]
        control["events"][3]["actor_id"] = "proxy-attribution-writer"
        _rehash_control(control)

    _copy_and_rewrite(source_package, target, mutate)
    with pytest.raises(ValueError, match="Attribution actor differs"):
        EvolutionProgramPackageManager().load_file(target)


def test_package_rejects_rehashed_control_reason(
    source_package,
    tmp_path,
):
    target = tmp_path / "ambiguous-terminal-reason.json"

    def mutate(payload):
        control = payload["ambiguous_control"]
        control["events"][-1]["reason"] = "forged terminal rationale"
        _rehash_control(control)

    _copy_and_rewrite(source_package, target, mutate)
    with pytest.raises(ValueError, match="audit reason differs"):
        EvolutionProgramPackageManager().load_file(target)


def test_package_rejects_rehashed_control_attribution_time(
    source_package,
    tmp_path,
):
    target = tmp_path / "ambiguous-attribution-time.json"

    def mutate(payload):
        control = payload["ambiguous_control"]
        original = _parse_time(control["events"][3]["created_at"])
        control["events"][3]["created_at"] = (
            original + timedelta(seconds=1)
        ).isoformat().replace("+00:00", "Z")
        _rehash_control(control)

    _copy_and_rewrite(source_package, target, mutate)
    with pytest.raises(ValueError, match="event time differs"):
        EvolutionProgramPackageManager().load_file(target)
