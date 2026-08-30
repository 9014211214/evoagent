from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).parents[1] / "src" / "seagym_evoagent" / "runtime_sanitizer.py"
SPEC = importlib.util.spec_from_file_location("runtime_sanitizer_under_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
sanitizer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sanitizer)


HASH_A = "a" * 64
HASH_B = "b" * 64
METADATA = json.dumps(
    {
        "snapshot_hash": HASH_A,
        "component_hashes": {
            "skills": HASH_A,
            "memory": HASH_B,
            "router": HASH_A,
            "policy": HASH_B,
        },
        "runtime_identity": {"name": "mimocode", "version": "1.2.3"},
        "route_contract_sha256": "c" * 64,
    }
)


class RuntimeSanitizerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_input(self, records: list[object] | None = None, raw: str | None = None) -> Path:
        path = self.root / "raw.jsonl"
        if raw is not None:
            path.write_text(raw, encoding="utf-8")
        else:
            path.write_text(
                "".join(json.dumps(record) + "\n" for record in (records or [])),
                encoding="utf-8",
            )
        return path

    def _sanitize(self, source: Path, **overrides: object) -> tuple[dict[str, object], Path]:
        destination = self.root / "trajectory.json"
        kwargs = {
            "model": sanitizer.MODEL_NAME,
            "seed": 43,
            "snapshot_metadata_json": METADATA,
        }
        kwargs.update(overrides)
        result = sanitizer.sanitize_runtime_jsonl(source, destination, **kwargs)
        return result, destination

    def test_emits_valid_shaped_atif_without_any_raw_text_or_ids(self) -> None:
        canary = "CANARY_TASK_9f8c"
        secret = "sk-secret-do-not-persist"
        source = self._write_input(
            [
                {
                    "type": "message",
                    "task_id": canary,
                    "session_id": "private-session-id",
                    "prompt": f"raw prompt {canary}",
                    "private_note": f"hidden reasoning {secret}",
                    "content": f"raw assistant text {secret}",
                },
                {
                    "type": "tool_call",
                    "timestamp": "2026-08-29T01:02:03Z",
                    "tool": {"name": "bash", "input": {"command": f"echo {secret}"}},
                    "status": "completed",
                    "output": f"raw output {canary}",
                    "usage": {
                        "input_tokens": 12,
                        "output_tokens": 3,
                        "cached_tokens": 2,
                        "cost_usd": 0.001,
                    },
                },
                {
                    "tool_name": f"not_allowlisted_{canary}",
                    "arguments": {"password": secret},
                    "result": f"{secret}:{canary}",
                },
            ]
        )

        trajectory, destination = self._sanitize(source)

        self.assertFalse(source.exists())
        rendered = destination.read_text(encoding="utf-8")
        for forbidden in (canary, secret, "private-session-id", "raw prompt", "hidden reasoning", "echo"):
            self.assertNotIn(forbidden, rendered)
        self.assertEqual(trajectory["schema_version"], "ATIF-v1.7")
        self.assertNotIn("session_id", trajectory)
        self.assertNotIn("trajectory_id", trajectory)
        self.assertEqual(trajectory["agent"]["model_name"], sanitizer.MODEL_NAME)
        self.assertEqual(trajectory["extra"]["api_model_id"], sanitizer.API_MODEL_ID)
        self.assertEqual(trajectory["steps"][0]["source"], "system")
        self.assertEqual(trajectory["steps"][0]["message"], "")
        tool_step = trajectory["steps"][1]
        self.assertEqual(tool_step["step_id"], 2)
        self.assertEqual(tool_step["timestamp"], "2026-08-29T01:02:03Z")
        self.assertEqual(tool_step["tool_calls"][0]["function_name"], "bash")
        self.assertEqual(tool_step["tool_calls"][0]["arguments"], {})
        call_id = tool_step["tool_calls"][0]["tool_call_id"]
        self.assertEqual(tool_step["observation"]["results"][0], {"source_call_id": call_id, "content": "status:success"})
        self.assertEqual(trajectory["final_metrics"]["total_prompt_tokens"], 12)
        self.assertEqual(trajectory["final_metrics"]["total_completion_tokens"], 3)
        self.assertEqual(trajectory["final_metrics"]["total_cached_tokens"], 2)
        self.assertEqual(trajectory["final_metrics"]["total_cost_usd"], 0.001)

    def test_malformed_json_is_rejected_scrubbed_and_does_not_replace_output(self) -> None:
        source = self._write_input(raw='{"content":"SECRET"\n')
        destination = self.root / "trajectory.json"
        destination.write_text("existing-safe-output", encoding="utf-8")
        with self.assertRaises(sanitizer.SanitizationError):
            sanitizer.sanitize_runtime_jsonl(
                source,
                destination,
                model=sanitizer.MODEL_NAME,
                seed=1,
                snapshot_metadata_json=METADATA,
            )
        self.assertFalse(source.exists())
        self.assertEqual(destination.read_text(encoding="utf-8"), "existing-safe-output")

    def test_oversize_and_nonfinite_inputs_are_rejected(self) -> None:
        source = self._write_input(raw='{"content":"too long"}\n')
        with mock.patch.object(sanitizer, "MAX_LINE_BYTES", 8):
            with self.assertRaises(sanitizer.SanitizationError):
                self._sanitize(source)
        self.assertFalse(source.exists())

        source = self._write_input(raw='{"usage":{"cost_usd":NaN}}\n')
        with self.assertRaises(sanitizer.SanitizationError):
            self._sanitize(source)
        self.assertFalse(source.exists())

    def test_large_raw_tool_output_is_sanitized_without_being_persisted(self) -> None:
        self.assertEqual(sanitizer.MAX_INPUT_BYTES, 64 * 1024 * 1024)
        self.assertEqual(sanitizer.MAX_LINE_BYTES, 16 * 1024 * 1024)
        self.assertEqual(sanitizer.MAX_STRING_CHARS, 16 * 1024 * 1024)
        canary = "RAW-LARGE-TOOL-OUTPUT-MUST-NOT-PERSIST"
        raw_output = canary + ("x" * (2 * 1024 * 1024))
        source = self._write_input(
            [
                {
                    "type": "tool_use",
                    "part": {
                        "type": "tool",
                        "tool": "bash",
                        "state": {
                            "status": "completed",
                            "input": {"command": "produce a large build log"},
                            "output": raw_output,
                        },
                    },
                }
            ]
        )

        trajectory, destination = self._sanitize(source)

        self.assertFalse(source.exists())
        self.assertEqual(trajectory["steps"][1]["tool_calls"][0]["function_name"], "bash")
        self.assertLess(destination.stat().st_size, 16 * 1024)
        self.assertNotIn(canary, destination.read_text(encoding="utf-8"))

    def test_wrong_metric_timestamp_and_status_types_are_rejected(self) -> None:
        bad_records = [
            {"usage": {"input_tokens": "12"}},
            {"timestamp": [], "tool_name": "bash"},
            {"tool_name": "bash", "status": {"secret": "x"}},
            {"usage": {"prompt_tokens": 1, "cached_tokens": 2}},
            {"tool_name": 7},
            {"tool_name": "bash", "success": "yes"},
        ]
        for index, record in enumerate(bad_records):
            with self.subTest(index=index):
                source = self.root / f"bad-{index}.jsonl"
                source.write_text(json.dumps(record) + "\n", encoding="utf-8")
                with self.assertRaises(sanitizer.SanitizationError):
                    self._sanitize(source)
                self.assertFalse(source.exists())

    def test_exact_model_seed_and_bounded_metadata_are_enforced(self) -> None:
        cases = [
            {"model": "xiaomi/mimo-v2.5"},
            {"seed": True},
            {"snapshot_metadata_json": '{"snapshot_hash":"secret"}'},
            {
                "snapshot_metadata_json": json.dumps(
                    {
                        "snapshot_hash": HASH_A,
                        "component_hashes": {
                            "skills": HASH_A,
                            "memory": HASH_B,
                            "router": HASH_A,
                            "task_id": HASH_B,
                        },
                        "runtime_identity": {"name": "mimocode", "version": "1.0"},
                    }
                )
            },
            {
                "snapshot_metadata_json": json.dumps(
                    {
                        "snapshot_hash": HASH_A,
                        "component_hashes": {
                            "skills": HASH_A,
                            "memory": HASH_B,
                            "router": HASH_A,
                            "policy": HASH_B,
                        },
                        "runtime_identity": {"name": "CANARY", "version": "secret"},
                    }
                )
            },
        ]
        for index, overrides in enumerate(cases):
            with self.subTest(index=index):
                source = self.root / f"metadata-{index}.jsonl"
                source.write_text("{}\n", encoding="utf-8")
                with self.assertRaises(sanitizer.SanitizationError):
                    self._sanitize(source, **overrides)
                self.assertFalse(source.exists())

    def test_prompt_and_session_are_removed_even_when_conversion_fails(self) -> None:
        source = self._write_input(raw="not-json\n")
        prompt = self.root / "prompt.txt"
        prompt.write_text("PROMPT-CANARY", encoding="utf-8")
        session = self.root / "session"
        session.mkdir()
        (session / "nested-secret.log").write_text("SESSION-SECRET", encoding="utf-8")
        with self.assertRaises(sanitizer.SanitizationError):
            self._sanitize(source, prompt_path=prompt, session_dir=session)
        self.assertFalse(source.exists())
        self.assertFalse(prompt.exists())
        self.assertFalse(session.exists())

    def test_reasoning_hard_lock_rejects_recursive_content_and_reasoning_events(self) -> None:
        records = [
            {"type": "reasoning", "content": "HIDDEN-CANARY"},
            {"event": {"payload": {"reasoning_content": "HIDDEN-CANARY"}}},
            {"reasoning": "HIDDEN-CANARY"},
        ]
        for index, record in enumerate(records):
            with self.subTest(index=index):
                source = self.root / f"reasoning-{index}.jsonl"
                source.write_text(json.dumps(record) + "\n", encoding="utf-8")
                prompt = self.root / f"prompt-{index}.txt"
                prompt.write_text("PROMPT-SECRET", encoding="utf-8")
                session = self.root / f"session-{index}"
                session.mkdir()
                (session / "raw.log").write_text("SESSION-SECRET", encoding="utf-8")
                destination = self.root / "trajectory.json"
                destination.unlink(missing_ok=True)
                with self.assertRaises(sanitizer.SanitizationError):
                    self._sanitize(source, prompt_path=prompt, session_dir=session)
                self.assertFalse(source.exists())
                self.assertFalse(prompt.exists())
                self.assertFalse(session.exists())
                self.assertFalse(destination.exists())

        source = self._write_input(
            [
                {"reasoning_content": None},
                {"reasoning_content": ""},
                {"reasoning_content": []},
                {"reasoning_details": {}},
                {"reasoning": False},
                {"reasoning": 0},
            ]
        )
        trajectory, _ = self._sanitize(source)
        self.assertEqual(len(trajectory["steps"]), 1)

    def test_mimocode_reasoning_token_telemetry_accepts_zero_to_one_without_content(self) -> None:
        trajectories: list[dict[str, object]] = []
        for reasoning_tokens in (0, 1):
            source = self._write_input(
                [
                    {
                        "type": "step_finish",
                        "part": {
                            "type": "step-finish",
                            "reason": "stop",
                            "cost": 0.001,
                            "tokens": {
                                "input": 4,
                                "output": 3,
                                "reasoning": reasoning_tokens,
                                "cache": {"read": 2, "write": 0},
                            },
                        },
                    }
                ]
            )
            trajectory, destination = self._sanitize(source)
            trajectories.append(trajectory)
            rendered = destination.read_text(encoding="utf-8")
            self.assertNotIn('"reasoning"', rendered)
            self.assertNotIn("reasoning_content", rendered)
            self.assertEqual(
                trajectory["steps"][1]["metrics"]["extra"],
                {"reasoning_tokens": reasoning_tokens},
            )
            self.assertEqual(
                trajectory["final_metrics"]["extra"],
                {"total_reasoning_tokens": reasoning_tokens},
            )
            # MiMo/Harbor count output as completion; reasoning is separate
            # telemetry and is not subtracted from output.
            self.assertEqual(trajectory["final_metrics"]["total_completion_tokens"], 3)
        zero = json.loads(json.dumps(trajectories[0]))
        one = json.loads(json.dumps(trajectories[1]))
        for trajectory in (zero, one):
            trajectory["steps"][1]["metrics"].pop("extra")
            trajectory["final_metrics"].pop("extra")
        self.assertEqual(zero, one)

    def test_mimocode_reasoning_token_telemetry_rejects_non_integer_or_unbounded_values(self) -> None:
        for index, value in enumerate((True, -1, 1.5, sanitizer.MAX_TOKENS + 1)):
            with self.subTest(value=value):
                source = self.root / f"bad-reasoning-token-{index}.jsonl"
                source.write_text(
                    json.dumps(
                        {
                            "type": "step_finish",
                            "part": {
                                "type": "step-finish",
                                "cost": 0.0,
                                "tokens": {
                                    "input": 1,
                                    "output": 1,
                                    "reasoning": value,
                                    "cache": {"read": 0, "write": 0},
                                },
                            },
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                with self.assertRaises(sanitizer.SanitizationError):
                    self._sanitize(source)
                self.assertFalse(source.exists())

    def test_unrecognized_raw_event_still_produces_one_safe_system_step(self) -> None:
        source = self._write_input([{"task_id": "CANARY", "text": "SECRET"}])
        trajectory, _ = self._sanitize(source)
        self.assertEqual(len(trajectory["steps"]), 1)
        self.assertEqual(trajectory["final_metrics"]["total_steps"], 1)

    def test_real_mimocode_json_events_preserve_only_safe_usage_and_tool_facts(self) -> None:
        source = self._write_input(
            [
                {
                    "type": "tool_use",
                    "timestamp": 1_788_000_000_000,
                    "sessionID": "SECRET-SESSION",
                    "part": {
                        "type": "tool",
                        "id": "SECRET-PART",
                        "tool": "bash",
                        "state": {
                            "status": "completed",
                            "input": {"command": "echo SECRET"},
                            "output": "SECRET-OUTPUT",
                        },
                    },
                },
                {
                    "type": "step_finish",
                    "timestamp": 1_788_000_000_100,
                    "sessionID": "SECRET-SESSION",
                    "part": {
                        "type": "step-finish",
                        "reason": "stop",
                        "cost": 0.002,
                        "tokens": {
                            "total": 18,
                            "input": 12,
                            "output": 4,
                            "reasoning": 0,
                            "cache": {"read": 2, "write": 0},
                        },
                    },
                },
            ]
        )
        trajectory, destination = self._sanitize(source)
        rendered = destination.read_text(encoding="utf-8")
        for forbidden in ("SECRET-SESSION", "SECRET-PART", "echo SECRET", "SECRET-OUTPUT"):
            self.assertNotIn(forbidden, rendered)
        self.assertEqual(trajectory["steps"][1]["tool_calls"][0]["function_name"], "bash")
        self.assertEqual(trajectory["steps"][2]["metrics"], {
            "prompt_tokens": 14,
            "completion_tokens": 4,
            "cached_tokens": 2,
            "cost_usd": 0.002,
            "extra": {"reasoning_tokens": 0},
        })
        self.assertEqual(trajectory["final_metrics"]["total_prompt_tokens"], 14)
        self.assertEqual(trajectory["final_metrics"]["total_completion_tokens"], 4)
        self.assertEqual(trajectory["final_metrics"]["extra"], {"total_reasoning_tokens": 0})

    def test_mimocode_cache_read_may_exceed_non_cached_increment(self) -> None:
        source = self._write_input(
            records=[
                {
                    "type": "step_finish",
                    "part": {
                        "type": "step-finish",
                        "cost": 0.001,
                        "tokens": {
                            "input": 1,
                            "output": 2,
                            "reasoning": 0,
                            "cache": {"read": 5, "write": 0},
                        },
                    },
                }
            ]
        )
        trajectory, _destination = self._sanitize(source)
        self.assertEqual(trajectory["steps"][1]["metrics"]["prompt_tokens"], 6)
        self.assertEqual(trajectory["steps"][1]["metrics"]["cached_tokens"], 5)

    def test_mimocode_websearch_alias_and_success_status_are_preserved(self) -> None:
        source = self._write_input(
            records=[
                {
                    "type": "tool_use",
                    "part": {
                        "type": "tool",
                        "id": "RAW-ID-MUST-BE-REMOVED",
                        "tool": "websearch",
                        "state": {
                            "status": "completed",
                            "input": {"query": "RAW-QUERY-MUST-BE-REMOVED"},
                            "output": "RAW-OUTPUT-MUST-BE-REMOVED",
                        },
                    },
                }
            ]
        )

        trajectory, destination = self._sanitize(source)

        tool_step = trajectory["steps"][1]
        self.assertEqual(tool_step["tool_calls"][0]["function_name"], "web_search")
        self.assertEqual(tool_step["extra"], {"status": "success"})
        rendered = destination.read_text(encoding="utf-8")
        for forbidden in ("RAW-ID", "RAW-QUERY", "RAW-OUTPUT"):
            self.assertNotIn(forbidden, rendered)

    def test_failure_receipts_are_exact_content_free_and_self_verifying(self) -> None:
        metadata = sanitizer._load_metadata(METADATA)
        cases = (
            (1, False, True, "mimocode_process_failed", "mimocode", "nonzero"),
            (0, True, False, "runtime_sanitization_failed", "sanitize", "success"),
            (137, True, False, "mimocode_and_sanitization_failed", "sanitize", "signal"),
        )
        expected_keys = {
            "schema_version",
            "failure_class",
            "failure_stage",
            "mimocode_exit_class",
            "snapshot_sha256",
            "component_sha256",
            "route_contract_sha256",
            "model",
            "seed",
            "runtime",
            "atif_present",
            "raw_prompt_persisted",
            "raw_response_persisted",
            "reasoning_content_persisted",
            "receipt_sha256",
        }
        for index, (exit_code, sanitize_failed, atif_present, failure_class, stage, exit_class) in enumerate(cases):
            with self.subTest(failure_class=failure_class):
                path = self.root / f"receipt-{index}.json"
                receipt = sanitizer.write_runtime_failure_receipt(
                    path,
                    mimocode_exit_code=exit_code,
                    sanitization_failed=sanitize_failed,
                    atif_present=atif_present,
                    metadata=metadata,
                    model=sanitizer.MODEL_NAME,
                    seed=43,
                )
                self.assertEqual(set(receipt), expected_keys)
                self.assertEqual(receipt["failure_class"], failure_class)
                self.assertEqual(receipt["failure_stage"], stage)
                self.assertEqual(receipt["mimocode_exit_class"], exit_class)
                self.assertIs(receipt["atif_present"], atif_present)
                self.assertIs(receipt["raw_prompt_persisted"], False)
                self.assertIs(receipt["raw_response_persisted"], False)
                self.assertIs(receipt["reasoning_content_persisted"], False)
                unsigned = dict(receipt)
                digest = unsigned.pop("receipt_sha256")
                expected = hashlib.sha256(
                    json.dumps(
                        unsigned,
                        ensure_ascii=False,
                        allow_nan=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                self.assertEqual(digest, expected)
                persisted = path.read_text(encoding="utf-8")
                for forbidden in ("message", "stderr", "command", "task", "raw_hash"):
                    self.assertNotIn(forbidden, persisted)

    def test_cli_emits_receipt_for_sanitizer_reject_and_mimocode_only_failure(self) -> None:
        malformed = self._write_input(raw="not-json\n")
        rejected_output = self.root / "rejected-trajectory.json"
        rejected_receipt = self.root / "rejected-receipt.json"
        with self.assertRaises(SystemExit):
            sanitizer.main(
                [
                    "--input",
                    str(malformed),
                    "--output",
                    str(rejected_output),
                    "--model",
                    sanitizer.MODEL_NAME,
                    "--seed",
                    "43",
                    "--snapshot-metadata-json",
                    METADATA,
                    "--mimocode-exit-code",
                    "0",
                    "--failure-receipt",
                    str(rejected_receipt),
                ]
            )
        rejected = json.loads(rejected_receipt.read_text(encoding="utf-8"))
        self.assertEqual(rejected["failure_class"], "runtime_sanitization_failed")
        self.assertEqual(rejected["mimocode_exit_class"], "success")
        self.assertIs(rejected["atif_present"], False)

        valid = self._write_input([{"type": "message", "content": "RAW-MUST-DROP"}])
        accepted_output = self.root / "accepted-trajectory.json"
        process_receipt = self.root / "process-receipt.json"
        self.assertEqual(
            sanitizer.main(
                [
                    "--input",
                    str(valid),
                    "--output",
                    str(accepted_output),
                    "--model",
                    sanitizer.MODEL_NAME,
                    "--seed",
                    "43",
                    "--snapshot-metadata-json",
                    METADATA,
                    "--mimocode-exit-code",
                    "1",
                    "--failure-receipt",
                    str(process_receipt),
                ]
            ),
            0,
        )
        process = json.loads(process_receipt.read_text(encoding="utf-8"))
        self.assertEqual(process["failure_class"], "mimocode_process_failed")
        self.assertEqual(process["mimocode_exit_class"], "nonzero")
        self.assertIs(process["atif_present"], True)
        self.assertTrue(accepted_output.is_file())

    def test_failure_receipt_builder_rejects_success_and_metadata_shape_drift(self) -> None:
        metadata = sanitizer._load_metadata(METADATA)
        with self.assertRaises(sanitizer.SanitizationError):
            sanitizer.build_runtime_failure_receipt(
                mimocode_exit_code=0,
                sanitization_failed=False,
                atif_present=False,
                metadata=metadata,
                model=sanitizer.MODEL_NAME,
                seed=43,
            )
        drifted = dict(metadata)
        drifted["private_message"] = "MUST-NOT-PERSIST"
        with self.assertRaises(sanitizer.SanitizationError):
            sanitizer.build_runtime_failure_receipt(
                mimocode_exit_code=1,
                sanitization_failed=False,
                atif_present=True,
                metadata=drifted,
                model=sanitizer.MODEL_NAME,
                seed=43,
            )


if __name__ == "__main__":
    unittest.main()
