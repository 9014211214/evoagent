from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from evoagent.benchmark_evidence import (
    BenchmarkEvidenceSource,
    BenchmarkRunRole,
    BenchmarkTaskIdentity,
    HarborResultImporter,
    assess_submission_eligibility,
    build_agent_identity,
    build_benchmark_suite,
    build_model_identity,
    build_run_contract,
)
from evoagent.model_registry.models import canonical_sha256


def test_external_evidence_can_meet_prerequisites_without_claiming_acceptance(
    tmp_path,
):
    tasks = tuple(
        BenchmarkTaskIdentity(
            task_name=f"official-prerequisite-task-{index}",
            task_id=f"official-prerequisite-id-{index}",
            task_checksum=canonical_sha256(
                {"official-prerequisite-task": index}
            ),
        )
        for index in range(2)
    )
    suite = build_benchmark_suite(
        suite_id="terminal-bench-2.1-attested-control",
        tasks=tasks,
        canonical_task_manifest_attested=True,
    )
    agent = build_agent_identity(
        family_id="external-agent-family",
        name="external-agent",
        version="1.0.0",
        source_commit="a" * 40,
        config_sha256=canonical_sha256("external-agent-config"),
        snapshot_id="external-agent-snapshot",
        evolution_round=0,
        parent_snapshot_id=None,
    )
    model = build_model_identity(
        provider="external-provider",
        name="external-model",
        revision="external-revision",
        config_sha256=canonical_sha256("external-model-config"),
        inference_settings_sha256=canonical_sha256(
            {"temperature": 0.0, "seed": 123}
        ),
    )
    contract = build_run_contract(
        contract_id="external-official-prerequisite-contract",
        role=BenchmarkRunRole.COMPARATOR,
        suite=suite,
        agent=agent,
        model=model,
        reasoning_effort="medium",
        trials_per_task=5,
        max_wall_seconds=7200,
        max_cost_usd=20.0,
        source=BenchmarkEvidenceSource.EXTERNAL_HARBOR,
        upload=True,
        public=True,
        harbor_hub_job_uri=(
            "https://harborframework.com/jobs/external-attested-control"
        ),
        trajectories_available=True,
        default_execution_settings_attested=True,
    )

    started = datetime(2026, 8, 11, 10, 0, tzinfo=timezone.utc)
    trials = []
    for task_index, task in enumerate(tasks):
        for trial_index in range(5):
            trial_started = started + timedelta(
                seconds=(task_index * 5 + trial_index) * 10
            )
            trials.append(
                {
                    "task_name": task.task_name,
                    "trial_name": (
                        f"{task.task_name}-trial-{trial_index}"
                    ),
                    "task_id": task.task_id,
                    "source": suite.dataset_ref,
                    "task_checksum": task.task_checksum,
                    "agent_info": {
                        "name": agent.name,
                        "version": agent.version,
                        "model_info": {
                            "name": model.name,
                            "provider": model.provider,
                        },
                    },
                    "verifier_result": {"rewards": {"reward": 1.0}},
                    "exception_info": None,
                    "agent_result": {
                        "n_input_tokens": 100,
                        "n_cache_tokens": 10,
                        "n_output_tokens": 50,
                        "cost_usd": 0.01,
                    },
                    "started_at": trial_started.isoformat(),
                    "finished_at": (
                        trial_started + timedelta(seconds=5)
                    ).isoformat(),
                }
            )
    payload = {
        "id": "external-attested-control-job",
        "started_at": started.isoformat(),
        "updated_at": (started + timedelta(minutes=5)).isoformat(),
        "finished_at": (started + timedelta(minutes=5)).isoformat(),
        "n_total_trials": len(trials),
        "stats": {
            "n_completed_trials": len(trials),
            "n_errored_trials": 0,
            "n_running_trials": 0,
            "n_pending_trials": 0,
            "n_cancelled_trials": 0,
            "n_retries": 0,
            "n_input_tokens": 100 * len(trials),
            "n_cache_tokens": 10 * len(trials),
            "n_output_tokens": 50 * len(trials),
            "cost_usd": 0.01 * len(trials),
            "evals": {},
        },
        "trial_results": trials,
    }
    result_path = tmp_path / "external" / "result.json"
    result_path.parent.mkdir(parents=True)
    encoded = (
        json.dumps(payload, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    result_path.write_bytes(encoded)
    run = HarborResultImporter(tmp_path).import_file(
        "external/result.json",
        expected_sha256=hashlib.sha256(encoded).hexdigest(),
        evidence_id="benchmark-run:external-attested-control",
        contract=contract,
    )

    assessment = assess_submission_eligibility(run)
    assert assessment.submission_prerequisites_met is True
    assert assessment.reasons == ()
    assert assessment.synthetic_fixture is False
    assert assessment.official_submission_performed is False
    assert assessment.official_submission_accepted is False
    assert run.harbor_execution_performed_by_evoagent is False
    assert run.external_model_call_performed_by_evoagent is False
    assert run.upload_performed_by_evoagent is False
    assert run.official_submission_performed is False
    assert run.official_submission_accepted is False
