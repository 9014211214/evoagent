import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from evoagent.campaigns import SQLiteCampaignRepository
from evoagent.lab import MultiGenerationEvolutionProgramLab
from evoagent.model_registry.models import canonical_sha256
from evoagent.program import EvolutionProgramPackageManager


_RECOVERY_REASON = (
    "Recovered exact completed generation after partial cross-registry commit."
)


def _parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _json_time(value):
    return value.isoformat().replace("+00:00", "Z")


def _source_payload(tmp_path, name, source_commit):
    result = MultiGenerationEvolutionProgramLab(
        tmp_path / name,
        source_commit=source_commit,
    ).run()
    return json.loads(Path(result.package_path).read_text(encoding="utf-8"))


def _rehash_campaign_chain(payload):
    previous_hash = "0" * 64
    for event in payload["campaign_events"]:
        event["previous_hash"] = previous_hash
        event["event_hash"] = SQLiteCampaignRepository._event_hash(
            sequence=event["sequence"],
            event_id=event["event_id"],
            campaign_id=event["campaign_id"],
            event_type=event["event_type"],
            actor_id=event["actor_id"],
            payload=event["payload"],
            created_at=_parse_time(event["created_at"]),
            previous_hash=previous_hash,
        )
        previous_hash = event["event_hash"]
    payload["campaign_checkpoint"] = {
        "event_count": len(payload["campaign_events"]),
        "head_hash": previous_hash,
    }


def _rewrite_recovery(payload, *, actor_id, recovery_time):
    recovery_time_json = _json_time(recovery_time)
    completion = payload["campaign_events"][6]
    completion["actor_id"] = actor_id
    completion["created_at"] = recovery_time_json
    completion["payload"]["reason"] = _RECOVERY_REASON
    payload["generation_campaign"]["updated_at"] = recovery_time_json
    _rehash_campaign_chain(payload)
    payload["package_hash"] = canonical_sha256(
        {key: value for key, value in payload.items() if key != "package_hash"}
    )


def _write_payload(tmp_path, name, payload):
    path = tmp_path / name
    path.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def test_full_package_accepts_independent_campaign_completion_recovery(tmp_path):
    payload = _source_payload(tmp_path, "program-lab", "4" * 40)
    generation_completed_at = _parse_time(
        payload["program_events"][9]["created_at"]
    )
    recovery_time = generation_completed_at + timedelta(seconds=1)
    _rewrite_recovery(
        payload,
        actor_id="independent-campaign-recovery",
        recovery_time=recovery_time,
    )

    recovered = EvolutionProgramPackageManager().load_file(
        _write_payload(tmp_path, "recovered-program-package.json", payload)
    )

    assert recovered.campaign_events[6].actor_id == (
        "independent-campaign-recovery"
    )
    assert recovered.campaign_events[6].payload["reason"] == _RECOVERY_REASON
    assert recovered.campaign_events[6].created_at == recovery_time
    assert recovered.generation_campaign.updated_at == recovery_time


def test_full_package_rejects_recovery_after_final_decision(tmp_path):
    payload = _source_payload(tmp_path, "late-recovery-lab", "5" * 40)
    recovery_time = _parse_time(
        payload["decisions"][1]["decided_at"]
    ) + timedelta(seconds=1)
    _rewrite_recovery(
        payload,
        actor_id="independent-late-recovery",
        recovery_time=recovery_time,
    )

    with pytest.raises(ValueError, match="decision predates recovered"):
        EvolutionProgramPackageManager().load_file(
            _write_payload(tmp_path, "late-recovery.json", payload)
        )


def test_full_package_rejects_approver_as_recovery_actor(tmp_path):
    payload = _source_payload(tmp_path, "role-recovery-lab", "6" * 40)
    generation_completed_at = _parse_time(
        payload["program_events"][9]["created_at"]
    )
    _rewrite_recovery(
        payload,
        actor_id=payload["generation_approvals"][0]["actor_id"],
        recovery_time=generation_completed_at + timedelta(seconds=1),
    )

    with pytest.raises(ValueError, match="approver|role separation"):
        EvolutionProgramPackageManager().load_file(
            _write_payload(tmp_path, "approver-recovery.json", payload)
        )
