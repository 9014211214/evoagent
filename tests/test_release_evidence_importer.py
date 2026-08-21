import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from evoagent.model_registry.models import canonical_sha256
from evoagent.release import (
    ReleaseEvidenceError,
    ReleaseEvidenceImporter,
    ReleaseEvidenceSource,
    ReleaseStageKind,
    build_release_plan,
    build_release_policy,
    build_release_segment,
    build_release_stage,
)


START = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)


def _plan():
    return build_release_plan(
        plan_id="release-plan:importer",
        champion_package_hash="1" * 64,
        family_id="family",
        incumbent_snapshot_id="a0",
        challenger_snapshot_id="a1",
        champion_decision_hash="2" * 64,
        runtime_config_sha256="3" * 64,
        tool_contract_sha256="4" * 64,
        segments=(build_release_segment("general"),),
        stages=(
            build_release_stage(
                stage_id="shadow",
                stage_index=0,
                kind=ReleaseStageKind.SHADOW,
                candidate_traffic_percent=0.0,
                minimum_pairs=2,
                minimum_pairs_per_segment=2,
                observation_window_seconds=3600,
            ),
            build_release_stage(
                stage_id="canary-10",
                stage_index=1,
                kind=ReleaseStageKind.CANARY,
                candidate_traffic_percent=10.0,
                minimum_pairs=2,
                minimum_pairs_per_segment=2,
                observation_window_seconds=3600,
            ),
        ),
        policy=build_release_policy(
            bootstrap_resamples=256,
            require_token_evidence=True,
            require_cost_evidence=True,
        ),
        evidence_source=ReleaseEvidenceSource.SYNTHETIC_FIXTURE,
        created_by="planner",
        created_at=START,
        source_commit="a" * 40,
    )


def _payload(plan):
    events = []
    for index in range(2):
        pair_id = f"pair-{index}"
        observed = START + timedelta(minutes=index + 1)
        for snapshot_id in ("a0", "a1"):
            events.append(
                {
                    "event_id": f"event-{index}-{snapshot_id}",
                    "pair_id": pair_id,
                    "stage_id": "shadow",
                    "segment_id": "general",
                    "snapshot_id": snapshot_id,
                    "success": True,
                    "error": False,
                    "safety_violation": False,
                    "latency_ms": 100.0,
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cost_usd": 0.001,
                    "observed_at": observed.isoformat(),
                }
            )
    return {
        "batch_id": "release-batch:importer:shadow",
        "plan_id": plan.plan_id,
        "plan_hash": plan.plan_hash,
        "stage_id": "shadow",
        "incumbent_snapshot_id": "a0",
        "challenger_snapshot_id": "a1",
        "candidate_traffic_percent": 0.0,
        "window_start": START.isoformat(),
        "window_end": (START + timedelta(minutes=30)).isoformat(),
        "producer_id": "observer",
        "declared_event_count": len(events),
        "declared_pair_count": 2,
        "events": events,
    }


def _write(root, payload):
    path = root / "shadow" / "release-evidence.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode()
    path.write_bytes(data)
    return path, hashlib.sha256(data).hexdigest()


def test_valid_release_evidence_import(tmp_path):
    plan = _plan()
    path, digest = _write(tmp_path, _payload(plan))
    batch = ReleaseEvidenceImporter(tmp_path).import_file(
        str(path.relative_to(tmp_path)), expected_sha256=digest, plan=plan
    )
    assert batch.pair_count == 2
    assert batch.segment_pair_counts == {"general": 2}
    assert batch.plan_hash == plan.plan_hash
    assert batch.external_execution_performed_by_evoagent is False
    assert batch.production_traffic_observed_by_evoagent is False


def test_import_rejects_hash_path_and_schema_drift(tmp_path):
    plan = _plan()
    payload = _payload(plan)
    path, digest = _write(tmp_path, payload)
    importer = ReleaseEvidenceImporter(tmp_path)

    with pytest.raises(ReleaseEvidenceError, match="SHA-256"):
        importer.import_file(
            str(path.relative_to(tmp_path)), expected_sha256="0" * 64, plan=plan
        )
    with pytest.raises(ReleaseEvidenceError, match="unsafe"):
        importer.import_file(
            "../release-evidence.json", expected_sha256=digest, plan=plan
        )

    payload["unexpected"] = "value"
    path, digest = _write(tmp_path, payload)
    with pytest.raises(ReleaseEvidenceError, match="schema"):
        importer.import_file(
            str(path.relative_to(tmp_path)), expected_sha256=digest, plan=plan
        )


def test_import_rejects_duplicate_events_and_plan_drift(tmp_path):
    plan = _plan()
    payload = _payload(plan)
    payload["events"][1]["event_id"] = payload["events"][0]["event_id"]
    path, digest = _write(tmp_path, payload)
    with pytest.raises(ReleaseEvidenceError, match="integrity"):
        ReleaseEvidenceImporter(tmp_path).import_file(
            str(path.relative_to(tmp_path)), expected_sha256=digest, plan=plan
        )

    payload = _payload(plan)
    payload["plan_hash"] = canonical_sha256("different-plan")
    path, digest = _write(tmp_path, payload)
    with pytest.raises(ReleaseEvidenceError, match="frozen plan"):
        ReleaseEvidenceImporter(tmp_path).import_file(
            str(path.relative_to(tmp_path)), expected_sha256=digest, plan=plan
        )