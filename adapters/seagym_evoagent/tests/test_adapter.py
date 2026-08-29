from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from seagym_evoagent.baseline import EvoAgentSEAGymBaseline
from seagym_evoagent.canonical import atomic_write_json, read_json, sha256_file, sha256_json
from seagym_evoagent.harbor_agent import ATTESTATION_FILENAME, EvoAgentMiMo
from seagym_evoagent.mimocode import (
    MIMOCODE_ARCHIVE_ENV,
    MIMOCODE_ARCHIVE_SHA256,
    MIMOCODE_ARCHIVE_URL,
    MIMOCODE_VERSION,
    install_command,
    locked_mimocode_config,
)
from seagym_evoagent.models import (
    CANONICAL_MODEL_ID,
    HARBOR_MODEL_ID,
    UPDATE_MODEL_ID,
    HarnessComponents,
    HarnessSnapshot,
    default_a0,
)
from seagym_evoagent.openrouter import (
    ModelUsage,
    OpenRouterStructuredClient,
    StructuredCompletion,
    safe_probe_failure_code,
)
from seagym_evoagent.routing import expected_route_contract
from seagym_evoagent.runtime_sanitizer import sanitize_runtime_jsonl

try:
    from harbor.models.agent.context import AgentContext as HarborAgentContext
except ImportError:  # Optional dependency is installed in the real workflow.
    HarborAgentContext = None


CANARY = "private-task-CANARY-77"
SECRET = "sk-or-v1-this-must-never-persist-123456"
PROXY_TOKEN = "evoagent-local-proxy-v1-" + "a" * 64


def candidate_payload(*, max_iterations: int = 11) -> dict[str, object]:
    payload = copy.deepcopy(default_a0().components.to_dict())
    payload["policy"]["max_iterations"] = max_iterations
    return payload


class FakeClient:
    def __init__(self, candidate: dict[str, object]) -> None:
        self.candidate = candidate
        self.calls: list[dict[str, object]] = []

    def complete(self, **kwargs: object) -> StructuredCompletion:
        self.calls.append(kwargs)
        return StructuredCompletion(
            candidate=self.candidate,
            usage=ModelUsage(100, 20, 120, 0.0025),
            request_sha256="1" * 64,
            response_sha256="2" * 64,
            served_model_id=CANONICAL_MODEL_ID,
            provider="Xiaomi",
        )


def write_raw_atif(root: Path) -> Path:
    trial = root / "trial-a"
    atif = trial / "agent" / "trajectory.json"
    atif.parent.mkdir(parents=True)
    atif.write_text(
        json.dumps(
            {
                "schema_version": "ATIF-v1.7",
                "session_id": CANARY,
                "agent": {"name": "mimo", "version": "0.1.13"},
                "steps": [
                    {"step_id": 1, "source": "user", "message": f"raw prompt {CANARY} {SECRET}"},
                    {
                        "step_id": 2,
                        "source": "agent",
                        "message": f"raw response {SECRET}",
                        "reasoning_content": f"hidden reasoning {CANARY}",
                        "tool_calls": [
                            {
                                "tool_call_id": "raw-call-id",
                                "function_name": "bash",
                                "arguments": {"command": f"echo {SECRET}"},
                            }
                        ],
                        "observation": {
                            "results": [{"source_call_id": "raw-call-id", "content": f"raw output {CANARY}"}]
                        },
                        "extra": {"status": "completed"},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return trial / "result.json"


def train_batch(root: Path) -> SimpleNamespace:
    result_path = write_raw_atif(root)
    trajectory = SimpleNamespace(
        task_id=CANARY,
        attempt_id=f"attempt-{CANARY}",
        view_name="train",
        mode="train",
        success=False,
        reward=0.25,
        score=0.5,
        rewards={"main": 0.25},
        cost={"n_input_tokens": 25, "n_output_tokens": 5, "cost_usd": 0.0005},
        runtime_seconds=4.5,
        error=f"raw error {SECRET}",
        refs={"result_path": str(result_path), "harbor_stdout": f"raw {SECRET}"},
    )
    return SimpleNamespace(
        trajectories=[trajectory],
        task_ids=[CANARY],
        view_name="train",
        mode="train",
        batch_index=0,
        epoch=0,
        refs={"raw": SECRET},
    )


class BaselineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_update_projects_only_structural_train_evidence_and_persists_immutable_candidate(self) -> None:
        client = FakeClient(candidate_payload())
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=client,
        )
        state = baseline.initialize(self.root / "run")
        a0_hash = state.metadata["a0_sha256"]

        result = baseline.update(train_batch(self.root / "harbor"), state)

        self.assertTrue(result.changed)
        self.assertEqual(result.status, "updated")
        self.assertEqual(len(client.calls), 1)
        request_text = json.dumps(client.calls[0], sort_keys=True)
        self.assertNotIn(CANARY, request_text)
        self.assertNotIn(SECRET, request_text)
        self.assertNotIn("raw prompt", request_text)
        projected = client.calls[0]["evidence"]
        self.assertEqual(projected["atif"]["tool_categories"], {"shell": 1})
        self.assertEqual(projected["atif"]["tool_statuses"], {"success": 1})
        report = baseline.report(state)
        self.assertEqual(report["a0_sha256"], a0_hash)
        self.assertNotEqual(report["evaluation_candidate_sha256"], a0_hash)
        self.assertFalse(report["causal_attribution_claimed"])
        self.assertFalse(report["promotion_claimed"])
        all_state = "".join(
            path.read_text(encoding="utf-8")
            for path in (self.root / "state").rglob("*")
            if path.is_file()
        )
        for forbidden in (CANARY, SECRET, "raw prompt", "raw response", "hidden reasoning"):
            self.assertNotIn(forbidden, all_state)
        snapshots = list((self.root / "state" / "snapshots").glob("*.json"))
        self.assertEqual(len(snapshots), 2)
        for path in snapshots:
            snapshot = HarnessSnapshot.from_dict(read_json(path))
            self.assertEqual(path.stem, snapshot.snapshot_sha256)

    def test_invalid_candidate_records_cost_and_hash_but_does_not_change_snapshot(self) -> None:
        payload = candidate_payload()
        payload["skills"][0]["guidance"] = f"memorize {CANARY}"
        client = FakeClient(payload)
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=client,
        )
        state = baseline.initialize(self.root / "run")
        before = state.metadata["evaluation_candidate_sha256"]

        result = baseline.update(train_batch(self.root / "harbor"), state)

        self.assertFalse(result.changed)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.metrics["cost_usd"], 0.0025)
        self.assertEqual(baseline.report(state)["evaluation_candidate_sha256"], before)
        attempt = read_json(next((self.root / "state" / "attempts").glob("*.json")))
        self.assertEqual(attempt["status"], "rejected")
        self.assertEqual(attempt["candidate_snapshot_sha256"], before)
        self.assertEqual(attempt["request_sha256"], "1" * 64)
        self.assertNotIn(CANARY, json.dumps(attempt))

    def test_eval_or_mixed_batch_is_rejected_before_model_call(self) -> None:
        client = FakeClient(candidate_payload())
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=client,
        )
        state = baseline.initialize(self.root / "run")
        batch = train_batch(self.root / "harbor")
        batch.mode = "checkpoint_eval"
        result = baseline.update(batch, state)
        self.assertEqual(result.status, "failed")
        self.assertEqual(client.calls, [])
        self.assertEqual(baseline.update_index, 0)

    def test_fail_on_update_error_stops_before_later_paid_rollouts(self) -> None:
        class FailingClient:
            def complete(self, **kwargs: object) -> StructuredCompletion:
                del kwargs
                raise RuntimeError("simulated update route failure")

        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=FailingClient(),
            fail_on_update_error=True,
        )
        state = baseline.initialize(self.root / "run")

        with self.assertRaisesRegex(RuntimeError, "simulated update route failure"):
            baseline.update(train_batch(self.root / "harbor"), state)

        self.assertEqual(baseline.update_index, 0)
        self.assertEqual(list((self.root / "state" / "attempts").glob("*.json")), [])

    def test_checkpoint_round_trip_reproduces_a0_and_candidate_and_rejects_tamper(self) -> None:
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
            model_client=FakeClient(candidate_payload()),
        )
        state = baseline.initialize(self.root / "run")
        baseline.update(train_batch(self.root / "harbor"), state)
        expected = baseline.report(state)
        checkpoint = baseline.save_checkpoint(state, self.root / "checkpoint")

        restored = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "restored",
            atif_root=self.root / "harbor",
            model_client=FakeClient(candidate_payload()),
        )
        restored_state = restored.load_checkpoint(checkpoint)
        actual = restored.report(restored_state)
        self.assertEqual(actual["a0_sha256"], expected["a0_sha256"])
        self.assertEqual(actual["evaluation_candidate_sha256"], expected["evaluation_candidate_sha256"])
        self.assertEqual(actual["update_cost_usd"], expected["update_cost_usd"])

        snapshot = next((self.root / "checkpoint" / "baseline_state" / "snapshots").glob("*.json"))
        snapshot.write_text("{}", encoding="utf-8")
        other = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "other",
            atif_root=self.root / "harbor",
        )
        with self.assertRaisesRegex(ValueError, "inventory"):
            other.load_checkpoint(checkpoint)

    def test_semantically_invalid_rehashed_checkpoint_cannot_replace_stable_state(self) -> None:
        source = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "source",
            atif_root=self.root / "harbor",
            model_client=FakeClient(candidate_payload()),
        )
        source_state = source.initialize(self.root / "run")
        source.update(train_batch(self.root / "harbor"), source_state)
        checkpoint = source.save_checkpoint(source_state, self.root / "checkpoint")

        restored = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "restored",
            atif_root=self.root / "harbor",
        )
        restored_state = restored.load_checkpoint(checkpoint)
        stable_report = restored.report(restored_state)
        stable_manifest_hash = sha256_file(restored.state_manifest_path)

        checkpoint_state = self.root / "checkpoint" / "baseline_state"
        state_manifest = read_json(checkpoint_state / "state.json")
        state_manifest["evaluation_candidate_sha256"] = state_manifest["a0_sha256"]
        state_manifest["prompt_template"] = f"prompts/{state_manifest['a0_sha256']}.md"
        atomic_write_json(checkpoint_state / "state.json", state_manifest)
        checkpoint_manifest = read_json(self.root / "checkpoint" / "checkpoint.json")
        checkpoint_manifest["state_inventory"]["state.json"] = sha256_file(checkpoint_state / "state.json")
        checkpoint_manifest["state_inventory_sha256"] = sha256_json(checkpoint_manifest["state_inventory"])
        checkpoint_manifest["state_metadata"]["evaluation_candidate_sha256"] = state_manifest["a0_sha256"]
        checkpoint_manifest["state_metadata"]["prompt_template"] = (
            f"baseline_state/prompts/{state_manifest['a0_sha256']}.md"
        )
        atomic_write_json(self.root / "checkpoint" / "checkpoint.json", checkpoint_manifest)

        with self.assertRaisesRegex(ValueError, "candidate"):
            restored.load_checkpoint(checkpoint)
        self.assertEqual(sha256_file(restored.state_manifest_path), stable_manifest_hash)
        self.assertEqual(restored.report(restored_state), stable_report)

    def test_from_config_freezes_route_governance_and_derives_contained_atif_root(self) -> None:
        run_dir = self.root / "run"
        run_dir.mkdir()
        base = EvoAgentSEAGymBaseline.from_config(
            name="evo",
            config={
                "route_contract": expected_route_contract(),
                "automatic_promotion": False,
                "causal_attribution_claimed": False,
                "fail_on_update_error": True,
            },
            models={
                "update_model": {
                    "provider": "openrouter",
                    "model": UPDATE_MODEL_ID,
                    "api_base": "https://openrouter.ai/api/v1",
                    "api_key_env": "OPENROUTER_API_KEY",
                }
            },
            state_dir=self.root / "state",
            run_dir=run_dir,
            base_dir=self.root,
        )
        self.assertEqual(base.atif_root, run_dir / "harbor" / "jobs")
        self.assertTrue(base.fail_on_update_error)
        bad_contract = expected_route_contract()
        bad_contract["provider"]["allow_fallbacks"] = True
        with self.assertRaisesRegex(ValueError, "route_contract"):
            EvoAgentSEAGymBaseline.from_config(
                name="evo",
                config={
                    "route_contract": bad_contract,
                    "automatic_promotion": False,
                    "causal_attribution_claimed": False,
                },
                models={
                    "update_model": {
                        "provider": "openrouter",
                        "model": UPDATE_MODEL_ID,
                        "api_base": "https://openrouter.ai/api/v1",
                        "api_key_env": "OPENROUTER_API_KEY",
                    }
                },
                state_dir=self.root / "bad",
                run_dir=run_dir,
                base_dir=self.root,
            )


class OpenRouterTests(unittest.TestCase):
    def test_probe_failure_codes_are_bounded_and_secret_free(self) -> None:
        self.assertEqual(
            safe_probe_failure_code(RuntimeError("OpenRouter returned HTTP 429")),
            "openrouter_http_429",
        )
        self.assertEqual(
            safe_probe_failure_code(RuntimeError("OpenRouter request failed (TimeoutError)")),
            "openrouter_transport_failure",
        )
        self.assertEqual(
            safe_probe_failure_code(RuntimeError("provider body " + SECRET)),
            "openrouter_runtime_failure",
        )
        self.assertEqual(
            safe_probe_failure_code(ValueError("response body " + SECRET)),
            "openrouter_response_validation_failed",
        )

    def test_request_sends_exact_route_and_accepts_only_empty_reasoning(self) -> None:
        captured: dict[str, object] = {}

        def transport(endpoint: str, headers: dict[str, str], body: bytes, timeout: float) -> bytes:
            captured.update(endpoint=endpoint, headers=headers, body=body, timeout=timeout)
            return json.dumps(
                {
                    "model": CANONICAL_MODEL_ID,
                    "provider": "Xiaomi",
                    "openrouter_metadata": {
                        "requested": "xiaomi/mimo-v2.5",
                        "strategy": "alias",
                        "attempt": 1,
                        "endpoints": {
                            "available": [
                                {
                                    "provider": "Xiaomi",
                                    "model": CANONICAL_MODEL_ID,
                                    "selected": True,
                                }
                            ]
                        },
                        "attempts": [
                            {"provider": "Xiaomi", "model": CANONICAL_MODEL_ID, "status": 200}
                        ],
                        "pipeline": [],
                    },
                    "choices": [
                        {
                            "finish_reason": "tool_calls",
                            "message": {
                                "role": "assistant",
                                "content": "",
                                "reasoning": None,
                                "reasoning_content": "",
                                "reasoning_details": [],
                                "tool_calls": [
                                    {
                                        "id": "call_verified",
                                        "type": "function",
                                        "function": {
                                            "name": "evoagent_harness_components",
                                            "arguments": json.dumps(candidate_payload()),
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14, "cost": 0.001},
                }
            ).encode()

        previous = os.environ.get("OPENROUTER_API_KEY")
        os.environ["OPENROUTER_API_KEY"] = SECRET
        try:
            completion = OpenRouterStructuredClient(transport=transport).complete(
                evidence={"safe": 1},
                current_components=default_a0().components.to_dict(),
                seed=43,
            )
        finally:
            if previous is None:
                os.environ.pop("OPENROUTER_API_KEY", None)
            else:
                os.environ["OPENROUTER_API_KEY"] = previous
        body = json.loads(captured["body"])
        self.assertEqual(body["provider"], expected_route_contract()["provider"])
        self.assertEqual(body["reasoning"], {"enabled": False})
        self.assertEqual(body["tool_choice"], "required")
        self.assertEqual(len(body["tools"]), 1)
        self.assertEqual(body["tools"][0]["function"]["name"], "evoagent_harness_components")
        self.assertNotIn("response_format", body)
        self.assertEqual(captured["headers"]["X-OpenRouter-Cache"], "false")
        self.assertEqual(captured["headers"]["X-OpenRouter-Metadata"], "enabled")
        self.assertNotIn(SECRET, captured["body"].decode())
        self.assertEqual(completion.served_model_id, CANONICAL_MODEL_ID)
        self.assertEqual(completion.provider, "Xiaomi")

    def test_response_model_provider_and_nonempty_reasoning_drift_fail_closed(self) -> None:
        base = {
            "model": CANONICAL_MODEL_ID,
            "provider": "Xiaomi",
            "openrouter_metadata": {
                "requested": "xiaomi/mimo-v2.5",
                "strategy": "alias",
                "attempt": 1,
                "endpoints": {
                    "available": [
                        {"provider": "Xiaomi", "model": CANONICAL_MODEL_ID, "selected": True}
                    ]
                },
                "attempts": [
                    {"provider": "Xiaomi", "model": CANONICAL_MODEL_ID, "status": 200}
                ],
                "pipeline": [],
            },
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_verified",
                                "type": "function",
                                "function": {
                                    "name": "evoagent_harness_components",
                                    "arguments": json.dumps(candidate_payload()),
                                },
                            }
                        ],
                    },
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "cost": 0.0},
        }
        previous = os.environ.get("OPENROUTER_API_KEY")
        os.environ["OPENROUTER_API_KEY"] = "safe-test-key"
        try:
            for mutation in (
                {"model": "other/model"},
                {"provider": "Other"},
                {"reasoning": "nonempty"},
                {"reasoning_content": ["nonempty"]},
            ):
                with self.subTest(mutation=mutation):
                    response = {**base, **mutation}
                    client = OpenRouterStructuredClient(
                        transport=lambda *_args, response=response: json.dumps(response).encode()
                    )
                    with self.assertRaises(ValueError):
                        client.complete(evidence={"safe": 1}, current_components=default_a0().components.to_dict(), seed=1)

            wrong_tool = json.loads(json.dumps(base))
            wrong_tool["choices"][0]["message"]["tool_calls"][0]["function"]["name"] = "wrong_tool"
            client = OpenRouterStructuredClient(
                transport=lambda *_args: json.dumps(wrong_tool).encode()
            )
            with self.assertRaisesRegex(ValueError, "wrong candidate Tool"):
                client.complete(evidence={"safe": 1}, current_components=default_a0().components.to_dict(), seed=1)

            for metadata in (
                None,
                {**base["openrouter_metadata"], "attempt": 2},
                {**base["openrouter_metadata"], "pipeline": [{"type": "plugin", "name": "web-search"}]},
                {
                    **base["openrouter_metadata"],
                    "endpoints": {
                        "available": [
                            {"provider": "Other", "model": CANONICAL_MODEL_ID, "selected": True}
                        ]
                    },
                },
            ):
                with self.subTest(router_metadata=metadata):
                    response = {**base, "openrouter_metadata": metadata}
                    client = OpenRouterStructuredClient(
                        transport=lambda *_args, response=response: json.dumps(response).encode()
                    )
                    with self.assertRaisesRegex(ValueError, "router metadata|materially altered|selected endpoint"):
                        client.complete(
                            evidence={"safe": 1},
                            current_components=default_a0().components.to_dict(),
                            seed=1,
                        )
        finally:
            if previous is None:
                os.environ.pop("OPENROUTER_API_KEY", None)
            else:
                os.environ["OPENROUTER_API_KEY"] = previous


class FakeExecResult:
    def __init__(self, return_code: int = 0) -> None:
        self.return_code = return_code
        self.stdout = ""
        self.stderr = ""


class FakeEnvironment:
    def __init__(self) -> None:
        self.commands: list[dict[str, object]] = []
        self.uploads: dict[str, bytes] = {}

    async def exec(self, **kwargs: object) -> FakeExecResult:
        self.commands.append(kwargs)
        return FakeExecResult()

    async def upload_file(self, source: Path, target: str) -> None:
        self.uploads[target] = Path(source).read_bytes()


class HarborAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        baseline = EvoAgentSEAGymBaseline(
            baseline_id="evo",
            state_dir=self.root / "state",
            atif_root=self.root / "harbor",
        )
        self.state = baseline.initialize(self.root / "run")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _agent(self, **kwargs: object) -> EvoAgentMiMo:
        parameters: dict[str, object] = {
            "logs_dir": self.root / "logs",
            "model_name": HARBOR_MODEL_ID,
            "prompt_template_path": self.state.metadata["prompt_template_path"],
            "seed": 43,
            "route_contract": expected_route_contract(),
            "mimocode_asset_sha256": MIMOCODE_ARCHIVE_SHA256,
            "mimocode_asset_url": MIMOCODE_ARCHIVE_URL,
            "mimocode_version": MIMOCODE_VERSION,
        }
        parameters.update(kwargs)
        return EvoAgentMiMo(**parameters)

    def test_setup_and_run_use_pinned_asset_secret_free_command_and_exact_config(self) -> None:
        agent = self._agent(extra_env={"OPENROUTER_API_KEY": PROXY_TOKEN})
        environment = FakeEnvironment()
        context = SimpleNamespace(metadata=None)
        archive = self.root / "mimocode-linux-x64.tar.gz"
        archive.write_bytes(b"locked-test-archive")
        previous_archive = os.environ.get(MIMOCODE_ARCHIVE_ENV)
        os.environ[MIMOCODE_ARCHIVE_ENV] = str(archive)
        try:
            with patch(
                "seagym_evoagent.harbor_agent.sha256_file",
                return_value=MIMOCODE_ARCHIVE_SHA256,
            ):
                asyncio.run(agent.setup(environment))
        finally:
            if previous_archive is None:
                os.environ.pop(MIMOCODE_ARCHIVE_ENV, None)
            else:
                os.environ[MIMOCODE_ARCHIVE_ENV] = previous_archive
        asyncio.run(agent.run(f"do private work {CANARY} {SECRET}", environment, context))

        setup_command = environment.commands[1]["command"]
        self.assertIn(MIMOCODE_ARCHIVE_SHA256, setup_command)
        self.assertNotIn(MIMOCODE_ARCHIVE_URL, setup_command)
        self.assertEqual(
            environment.uploads["/tmp/evoagent-mimocode-install/archive.tar.gz"],
            b"locked-test-archive",
        )
        run_call = environment.commands[-1]
        command = run_call["command"]
        self.assertNotIn(CANARY, command)
        self.assertNotIn(SECRET, command)
        self.assertNotIn("--thinking", command)
        self.assertIn(f"--model {HARBOR_MODEL_ID}", command)
        config = json.loads(environment.uploads["/tmp/evoagent-mimo-runtime/mimocode.json"])
        self.assertEqual(
            config,
            locked_mimocode_config(
                expected_route_contract(),
                max_iterations=agent.snapshot.components.policy.max_iterations,
            ),
        )
        self.assertTrue(config["provider"]["openrouter"]["only_configured_models"])
        model_options = config["provider"]["openrouter"]["models"][UPDATE_MODEL_ID]["options"]
        self.assertEqual(model_options["provider"], expected_route_contract()["provider"])
        self.assertEqual(model_options["reasoning"], {"enabled": False})
        self.assertEqual(config["agent"]["build"]["steps"], agent.snapshot.components.policy.max_iterations)
        with self.assertRaises(TypeError):
            locked_mimocode_config(expected_route_contract())
        for invalid in (True, 0, 33):
            with self.subTest(max_iterations=invalid):
                with self.assertRaises(ValueError):
                    locked_mimocode_config(expected_route_contract(), max_iterations=invalid)
        self.assertNotIn(SECRET, json.dumps(config))
        self.assertIn(CANARY.encode(), environment.uploads["/tmp/evoagent-mimo-runtime/projected-task.md"])
        self.assertEqual(run_call["env"]["MIMOCODE_DISABLE_PROVIDER_ENV"], "1")
        self.assertEqual(run_call["env"]["MIMOCODE_DISABLE_PROJECT_CONFIG"], "1")
        self.assertEqual(run_call["env"]["MIMOCODE_DISABLE_CLAUDE_CODE_COMMANDS"], "1")
        self.assertEqual(run_call["env"]["MIMOCODE_DISABLE_CLAUDE_IMPORT"], "1")
        self.assertEqual(run_call["env"]["OPENROUTER_API_KEY"], PROXY_TOKEN)

    @unittest.skipIf(HarborAgentContext is None, "optional pinned Harbor is not installed")
    def test_run_leaves_real_harbor_context_empty_for_post_run_hook(self) -> None:
        agent = self._agent(extra_env={"OPENROUTER_API_KEY": PROXY_TOKEN})
        environment = FakeEnvironment()
        context = HarborAgentContext()

        self.assertTrue(context.is_empty())
        asyncio.run(agent.run("complete the official task", environment, context))
        self.assertTrue(context.is_empty())

    def test_run_refuses_a_real_account_credential_in_the_task_container(self) -> None:
        agent = self._agent(extra_env={"OPENROUTER_API_KEY": SECRET})
        with self.assertRaisesRegex(RuntimeError, "local proxy capability"):
            asyncio.run(agent.run("complete the official task", FakeEnvironment(), SimpleNamespace()))

    def test_post_run_attestation_binds_snapshot_atif_model_seed_runtime_and_usage(self) -> None:
        agent = self._agent()
        logs = self.root / "logs"
        logs.mkdir()
        raw = self.root / "raw.jsonl"
        raw.write_text(
            json.dumps(
                {
                    "type": "tool_call",
                    "tool_name": "bash",
                    "status": "completed",
                    "content": f"raw {CANARY} {SECRET}",
                    "usage": {"input_tokens": 12, "output_tokens": 3, "cached_tokens": 2, "cost_usd": 0.001},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        sanitize_runtime_jsonl(
            raw,
            logs / "trajectory.json",
            model=HARBOR_MODEL_ID,
            seed=43,
            snapshot_metadata_json=json.dumps(
                {
                    "snapshot_hash": agent.snapshot.snapshot_sha256,
                    "component_hashes": dict(agent.snapshot.component_sha256),
                    "runtime_identity": {"name": "mimocode", "version": MIMOCODE_VERSION},
                    "route_contract_sha256": sha256_json(expected_route_contract()),
                }
            ),
        )
        context = SimpleNamespace(
            n_input_tokens=None,
            n_cache_tokens=None,
            n_output_tokens=None,
            cost_usd=None,
            rollout_details=[],
            metadata={"raw": CANARY},
        )
        agent.populate_context_post_run(context)
        attestation = read_json(logs / ATTESTATION_FILENAME)
        self.assertEqual(attestation["schema_version"], "evoagent-harbor-attestation-v1")
        self.assertEqual(attestation["snapshot_sha256"], agent.snapshot.snapshot_sha256)
        self.assertEqual(attestation["component_sha256"], dict(agent.snapshot.component_sha256))
        self.assertEqual(attestation["seed"], 43)
        self.assertEqual(attestation["model"]["api_id"], UPDATE_MODEL_ID)
        self.assertEqual(attestation["route_contract_sha256"], sha256_json(expected_route_contract()))
        self.assertEqual(attestation["runtime"]["mimocode_archive_sha256"], MIMOCODE_ARCHIVE_SHA256)
        self.assertEqual(attestation["usage"]["cost_usd"], 0.001)
        unsigned = dict(attestation)
        digest = unsigned.pop("attestation_sha256")
        self.assertEqual(digest, sha256_json(unsigned))
        persisted = (logs / "trajectory.json").read_text() + (logs / ATTESTATION_FILENAME).read_text()
        self.assertNotIn(CANARY, persisted)
        self.assertNotIn(SECRET, persisted)
        self.assertEqual(context.metadata["attestation_sha256"], digest)
        self.assertNotIn("raw", context.metadata)

    def test_post_run_rejects_prose_tool_identity_usage_and_route_tampering(self) -> None:
        agent = self._agent()
        raw = self.root / "raw-template.jsonl"
        raw.write_text(
            json.dumps(
                {
                    "type": "tool_call",
                    "tool_name": "bash",
                    "status": "completed",
                    "usage": {"input_tokens": 12, "output_tokens": 3, "cached_tokens": 2, "cost_usd": 0.001},
                }
            ) + "\n",
            encoding="utf-8",
        )
        template_path = self.root / "safe-template.json"
        safe = sanitize_runtime_jsonl(
            raw,
            template_path,
            model=HARBOR_MODEL_ID,
            seed=43,
            snapshot_metadata_json=json.dumps(
                {
                    "snapshot_hash": agent.snapshot.snapshot_sha256,
                    "component_hashes": dict(agent.snapshot.component_sha256),
                    "runtime_identity": {"name": "mimocode", "version": MIMOCODE_VERSION},
                    "route_contract_sha256": sha256_json(expected_route_contract()),
                }
            ),
        )
        tampered: list[dict[str, object]] = []
        prose = copy.deepcopy(safe)
        prose["steps"][1]["message"] = "raw response"
        tampered.append(prose)
        tool = copy.deepcopy(safe)
        tool["steps"][1]["tool_calls"][0]["function_name"] = "private_CANARY_tool"
        tampered.append(tool)
        usage = copy.deepcopy(safe)
        usage["final_metrics"]["total_cost_usd"] = 999.0
        tampered.append(usage)
        route = copy.deepcopy(safe)
        route["extra"]["route_contract_sha256"] = "0" * 64
        route["agent"]["extra"]["route_contract_sha256"] = "0" * 64
        tampered.append(route)

        for index, payload in enumerate(tampered):
            with self.subTest(index=index):
                logs = self.root / f"tampered-{index}"
                logs.mkdir()
                atomic_write_json(logs / "trajectory.json", payload)
                guarded = self._agent(logs_dir=logs)
                with self.assertRaises(ValueError):
                    guarded.populate_context_post_run(SimpleNamespace())
                self.assertFalse((logs / ATTESTATION_FILENAME).exists())

    def test_asset_and_route_drift_are_rejected_before_base_agent_passthrough(self) -> None:
        with self.assertRaisesRegex(ValueError, "asset_sha256"):
            self._agent(mimocode_asset_sha256="0" * 64)
        with self.assertRaisesRegex(ValueError, "asset_url"):
            self._agent(mimocode_asset_url="https://example.invalid/mimo.tar.gz")
        with self.assertRaisesRegex(ValueError, "mimocode_version"):
            self._agent(mimocode_version="0.1.14")
        drift = expected_route_contract()
        drift["provider"]["allow_fallbacks"] = True
        with self.assertRaisesRegex(ValueError, "route_contract"):
            self._agent(route_contract=drift)
        with self.assertRaisesRegex(ValueError, "unsupported constructor fields"):
            self._agent(unrecognized_runtime_switch=True)

    def test_pinned_harbor_base_constructor_fields_are_explicitly_preserved(self) -> None:
        logger = logging.getLogger("seagym-evoagent-test")
        mcp_servers = [SimpleNamespace(name="test-mcp")]
        agent = self._agent(
            logger=logger,
            mcp_servers=mcp_servers,
            skills_dir="/skills",
            extra_env={"OPENROUTER_API_KEY": PROXY_TOKEN},
        )
        self.assertEqual(agent.extra_env, {"OPENROUTER_API_KEY": PROXY_TOKEN})
        self.assertEqual(agent.mcp_servers, mcp_servers)
        self.assertEqual(agent.skills_dir, "/skills")


if __name__ == "__main__":
    unittest.main()
