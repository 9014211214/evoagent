import json

import pytest

from evoagent.lab.local_agentic_rl import LocalAgenticRLTrainingLab
from evoagent.local_rl import LocalRLPackageError, LocalRLPackageManager
from evoagent.model_registry.models import canonical_sha256


def _package(tmp_path):
    lab = LocalAgenticRLTrainingLab(
        tmp_path / "lab",
        source_commit="d" * 40,
    )
    lab.run()
    return lab.package_path


def _rewrite(path, mutate):
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload)
    payload["package_hash"] = canonical_sha256(
        {key: value for key, value in payload.items() if key != "package_hash"}
    )
    path.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def test_package_rejects_parameter_rewrite_with_outer_rehash(tmp_path):
    path = _package(tmp_path)

    def mutate(payload):
        training = payload["training"]
        checkpoint = training["retained_checkpoints"][0]
        checkpoint["logits"][0][0] += 0.5
        checkpoint["checkpoint_hash"] = canonical_sha256(
            {
                key: value
                for key, value in checkpoint.items()
                if key != "checkpoint_hash"
            }
        )
        metric = next(
            item
            for item in training["iterations"]
            if item["iteration"] == checkpoint["iteration"]
        )
        metric["checkpoint_hash"] = checkpoint["checkpoint_hash"]
        metric["metrics_hash"] = canonical_sha256(
            {key: value for key, value in metric.items() if key != "metrics_hash"}
        )
        training["result_hash"] = canonical_sha256(
            {
                key: value
                for key, value in training.items()
                if key != "result_hash"
            }
        )

    _rewrite(path, mutate)
    with pytest.raises((LocalRLPackageError, ValueError)):
        LocalRLPackageManager().load_file(path)


def test_package_rejects_evaluation_rewrite_with_outer_rehash(tmp_path):
    path = _package(tmp_path)

    def mutate(payload):
        report = payload["candidate_evaluations"][-1]
        result = report["task_results"][0]
        result["success"] = False
        result["total_reward"] = -1.0
        result["result_hash"] = canonical_sha256(
            {key: value for key, value in result.items() if key != "result_hash"}
        )
        report["overall_score"] = sum(
            item["success"] for item in report["task_results"]
        ) / len(report["task_results"])
        normal = [
            item for item in report["task_results"] if item["kind"] == "normal"
        ]
        report["normal_score"] = sum(item["success"] for item in normal) / len(normal)
        report["report_hash"] = canonical_sha256(
            {key: value for key, value in report.items() if key != "report_hash"}
        )

    _rewrite(path, mutate)
    with pytest.raises((LocalRLPackageError, ValueError)):
        LocalRLPackageManager().load_file(path)


def test_package_rejects_selected_checkpoint_substitution(tmp_path):
    path = _package(tmp_path)

    def mutate(payload):
        decision = payload["decision"]
        alternative = payload["training"]["retained_checkpoints"][-1]
        alternative_report = next(
            item
            for item in payload["candidate_evaluations"]
            if item["checkpoint_hash"] == alternative["checkpoint_hash"]
        )
        decision["selected_checkpoint_id"] = alternative["checkpoint_id"]
        decision["selected_checkpoint_hash"] = alternative["checkpoint_hash"]
        decision["selected_iteration"] = alternative["iteration"]
        decision["selected_report_hash"] = alternative_report["report_hash"]
        decision["decision_hash"] = canonical_sha256(
            {key: value for key, value in decision.items() if key != "decision_hash"}
        )

    _rewrite(path, mutate)
    with pytest.raises((LocalRLPackageError, ValueError)):
        LocalRLPackageManager().load_file(path)


def test_package_rejects_audit_tail_truncation_even_when_reanchored(tmp_path):
    path = _package(tmp_path)

    def mutate(payload):
        payload["audit_events"] = payload["audit_events"][:-1]
        tail = payload["audit_events"][-1]
        payload["audit_checkpoint"] = {
            "event_count": len(payload["audit_events"]),
            "head_hash": tail["event_hash"],
        }

    _rewrite(path, mutate)
    with pytest.raises(LocalRLPackageError, match="missing, duplicated, or reordered"):
        LocalRLPackageManager().load_file(path)
