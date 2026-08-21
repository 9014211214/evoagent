from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from evoagent.campaigns import CampaignOperatorView, CampaignState, SQLiteCampaignRepository
from evoagent.lab import DEFAULT_THIRD_PARTY_LOCK_HASH, ReferenceEvolutionLab
from evoagent.runs import ReproducibleRunBundleManager
from evoagent.skills import SQLiteSkillRegistry, SkillEventType
from evoagent.traces import JsonlTraceStore


HIDDEN_FIELDS = {"chain_of_thought", "scratchpad"}
SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


def evidence_findings(value: Any, path: str = "$") -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            key_path = f"{path}.{key}"
            if str(key).casefold() in HIDDEN_FIELDS:
                findings.append(f"hidden field at {key_path}")
            findings.extend(evidence_findings(item, key_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            findings.extend(evidence_findings(item, f"{path}[{index}]"))
    elif isinstance(value, str):
        for pattern in SECRET_PATTERNS:
            if pattern.search(value):
                findings.append(f"secret-like value at {path}")
    return findings


def test_reference_lab_rejects_invalid_evidence_hash(tmp_path):
    with pytest.raises(ValueError, match="third_party_lock_hash"):
        ReferenceEvolutionLab(
            tmp_path,
            source_commit="0" * 40,
            third_party_lock_hash="not-a-sha256",
        )


def test_reference_lab_completes_full_lifecycle_and_resumes_idempotently(tmp_path):
    lab = ReferenceEvolutionLab(tmp_path, source_commit="0" * 40)
    first = lab.run()

    assert first.resumed is False
    assert first.base_version == "0.1.0"
    assert first.candidate_version == "0.2.0"
    assert first.active_version == first.candidate_version
    assert first.baseline.score == 0.5
    assert first.evolved.score == 1.0
    assert first.evolution_gain == 0.5
    assert first.best_round == 1
    assert first.campaign_state == CampaignState.COMPLETED.value
    assert first.restart_verified is True
    assert first.external_execution_performed is False

    skills = SQLiteSkillRegistry(lab.skill_database)
    campaigns = SQLiteCampaignRepository(lab.campaign_database)
    traces = JsonlTraceStore(lab.trace_file)
    version_count = len(skills.list_versions(first.skill_id))
    skill_events = skills.events(first.skill_id)
    campaign_count = len(CampaignOperatorView(campaigns).list_campaigns())
    approval_count = len(campaigns.approvals(first.campaign_id))
    trace_count = len(traces.list())

    assert version_count == 2
    assert campaign_count == 1
    assert trace_count == 1
    assert sum(item.event_type == SkillEventType.PROMOTED.value for item in skill_events) == 1
    assert approval_count >= 1
    assert ReproducibleRunBundleManager().verify(first.run_bundle_path).verified is True

    second = lab.run()
    assert second.resumed is True
    assert second.campaign_id == first.campaign_id
    assert second.active_version == first.active_version
    assert second.run_manifest_hash == first.run_manifest_hash
    assert len(SQLiteSkillRegistry(lab.skill_database).list_versions(first.skill_id)) == version_count
    assert len(SQLiteSkillRegistry(lab.skill_database).events(first.skill_id)) == len(skill_events)
    assert len(CampaignOperatorView(SQLiteCampaignRepository(lab.campaign_database)).list_campaigns()) == campaign_count
    assert len(SQLiteCampaignRepository(lab.campaign_database).approvals(first.campaign_id)) == approval_count
    assert len(JsonlTraceStore(lab.trace_file).list()) == trace_count


def test_reference_evidence_contains_no_credentials_or_hidden_reasoning(tmp_path):
    result = ReferenceEvolutionLab(tmp_path, source_commit="1" * 40).run()
    results_payload = json.loads(
        (Path(tmp_path) / "reference-results.json").read_text(encoding="utf-8")
    )
    manifest_payload = json.loads(
        (Path(result.run_bundle_path) / "manifest.json").read_text(encoding="utf-8")
    )

    findings = evidence_findings(results_payload) + evidence_findings(manifest_payload)
    assert findings == []
    assert results_payload["external_execution_performed"] is False
    assert results_payload["source_commit"] == "1" * 40
    assert results_payload["third_party_lock_hash"] == DEFAULT_THIRD_PARTY_LOCK_HASH
