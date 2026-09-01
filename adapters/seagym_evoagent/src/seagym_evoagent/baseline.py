"""SEAGym baseline producing immutable, evaluation-only harness candidates."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any

from ._compat import BaseBaseline, BaselineState, Checkpoint, UpdateResult
from .canonical import atomic_write_json, contained_path, read_json, sha256_file, sha256_json
from .evidence import NO_USABLE_ATIF_SKIP_CODE, NoUsableHarborATIFEvidence, project_train_batch
from .models import HarnessComponents, HarnessSnapshot, UPDATE_MODEL_ID, default_a0
from .openrouter import OPENROUTER_ENDPOINT, OpenRouterStructuredClient, StructuredCompletion
from .routing import expected_route_contract, validate_route_contract


STATE_SCHEMA = "evoagent-seagym-state-v1"
CHECKPOINT_SCHEMA = "evoagent-seagym-checkpoint-v1"
ADAPTER_VERSION = "0.1.0"
ROUTE_CONTRACT_SHA256 = sha256_json(expected_route_contract())
ATTEMPT_SCHEMA = "evoagent-seagym-update-attempt-v1"


@dataclass
class EvoAgentSEAGymBaseline(BaseBaseline):
    """A SEAGym baseline with no implicit promotion or activation authority."""

    atif_root: Path | None = None
    seed: int = 43
    max_trajectories: int = 64
    timeout_seconds: float = 180.0
    fail_on_update_error: bool = False
    route_contract: dict[str, Any] = field(default_factory=expected_route_contract, repr=False)
    model_client: Any | None = field(default=None, repr=False)
    _a0: HarnessSnapshot | None = field(default=None, init=False, repr=False)
    _candidate: HarnessSnapshot | None = field(default=None, init=False, repr=False)
    _attempt_refs: list[str] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.atif_root is not None:
            self.atif_root = Path(self.atif_root).resolve(strict=False)
        if isinstance(self.seed, bool) or not isinstance(self.seed, int) or not 0 <= self.seed <= 2**63 - 1:
            raise ValueError("seed must be a non-negative signed 64-bit integer")
        if (
            isinstance(self.max_trajectories, bool)
            or not isinstance(self.max_trajectories, int)
            or not 1 <= self.max_trajectories <= 1024
        ):
            raise ValueError("max_trajectories must be in [1, 1024]")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, int | float)
            or not math.isfinite(float(self.timeout_seconds))
            or not 1 <= float(self.timeout_seconds) <= 600
        ):
            raise ValueError("timeout_seconds must be a finite number in [1, 600]")
        if not isinstance(self.fail_on_update_error, bool):
            raise ValueError("fail_on_update_error must be boolean")
        self.timeout_seconds = float(self.timeout_seconds)
        self.route_contract = validate_route_contract(self.route_contract)

    @classmethod
    def from_config(
        cls,
        *,
        name: str,
        config: dict[str, Any],
        models: dict[str, Any],
        state_dir: Path,
        run_dir: Path,
        base_dir: Path | None,
    ) -> "EvoAgentSEAGymBaseline":
        allowed = {
            "update_model_ref",
            "atif_root",
            "seed",
            "max_trajectories",
            "timeout_seconds",
            "fail_on_update_error",
            "route_contract",
            "automatic_promotion",
            "causal_attribution_claimed",
        }
        if not isinstance(config, dict) or set(config) - allowed:
            raise ValueError("EvoAgent baseline config contains unsupported fields")
        if not isinstance(models, dict):
            raise ValueError("baseline.models must be an object")
        model_ref = config.get("update_model_ref", "update_model")
        if not isinstance(model_ref, str) or not model_ref:
            raise ValueError("update_model_ref must be a string")
        _validate_model_binding(models.get(model_ref))
        route_contract = validate_route_contract(config.get("route_contract"))
        if config.get("automatic_promotion") is not False:
            raise ValueError("automatic_promotion must be explicitly false")
        if config.get("causal_attribution_claimed") is not False:
            raise ValueError("causal_attribution_claimed must be explicitly false")
        raw_root = config.get("atif_root")
        if raw_root is None:
            controlled_run_dir = run_dir.resolve(strict=False)
            root = (controlled_run_dir / "harbor" / "jobs").resolve(strict=False)
            if root != controlled_run_dir and controlled_run_dir not in root.parents:
                raise ValueError("derived atif_root escapes run_dir")
        else:
            if not isinstance(raw_root, str) or not raw_root:
                raise ValueError("atif_root must be a non-empty path string")
            root = Path(raw_root)
            if not root.is_absolute():
                root = (base_dir or run_dir) / root
        return cls(
            baseline_id=name,
            state_dir=state_dir,
            atif_root=root,
            seed=config.get("seed", 43),
            max_trajectories=config.get("max_trajectories", 64),
            timeout_seconds=config.get("timeout_seconds", 180.0),
            fail_on_update_error=config.get("fail_on_update_error", False),
            route_contract=route_contract,
        )

    @property
    def state_manifest_path(self) -> Path:
        return self.state_dir / "state.json"

    @property
    def snapshots_dir(self) -> Path:
        return self.state_dir / "snapshots"

    @property
    def prompts_dir(self) -> Path:
        return self.state_dir / "prompts"

    @property
    def attempts_dir(self) -> Path:
        return self.state_dir / "attempts"

    def initialize(self, run_dir: Path) -> BaselineState:
        del run_dir
        _prepare_controlled_state_dir(self.state_dir)
        if self.state_manifest_path.exists():
            self._load_state_from_disk()
        else:
            a0 = default_a0()
            self._persist_snapshot(a0)
            prompt_path = self._persist_prompt(a0)
            self._a0 = a0
            self._candidate = a0
            self._attempt_refs = []
            self._write_state_manifest(prompt_path)
        return self._baseline_state(loaded=False)

    def update(self, trajectories: Any, state: BaselineState) -> UpdateResult:
        self._require_initialized_state(state)
        assert self._candidate is not None
        if self.atif_root is None:
            raise ValueError("atif_root is required")
        attempt_index = self.update_index + 1
        before = self._candidate
        projection = None
        completion = None
        try:
            projection = project_train_batch(
                trajectories,
                atif_root=self.atif_root,
                expected_snapshot_sha256=before.snapshot_sha256,
                expected_component_sha256=dict(before.component_sha256),
                expected_route_contract_sha256=ROUTE_CONTRACT_SHA256,
                expected_seed=self.seed,
                max_trajectories=self.max_trajectories,
            )
        except NoUsableHarborATIFEvidence as exc:
            projection = exc.projection
            skipped = _skipped_attempt_record(
                attempt_index=attempt_index,
                before=before,
                projection_sha256=projection.evidence_sha256,
                seed=self.seed,
            )
            attempt_ref = self._persist_attempt(skipped)
            new_refs = [*self._attempt_refs, attempt_ref]
            prompt_path = self._prompt_path(before.snapshot_sha256)
            self._write_state_manifest(
                prompt_path,
                candidate=before,
                update_index=attempt_index,
                attempt_refs=new_refs,
            )
            self._candidate = before
            self.update_index = attempt_index
            self._attempt_refs = new_refs
            self._synchronize_live_state(state)
            return UpdateResult(
                update_index=attempt_index,
                changed=False,
                status="unchanged",
                metrics={
                    "num_trajectories": projection.summary["num_trajectories"],
                    "success_count": projection.summary["success_count"],
                    "failure_count": projection.summary["failure_count"],
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost_usd": 0.0,
                },
                logs={
                    "model_call_executed": False,
                    "skip_code": NO_USABLE_ATIF_SKIP_CODE,
                    "evidence_sha256": projection.evidence_sha256,
                    "candidate_sha256": before.snapshot_sha256,
                    "causal_attribution_claimed": False,
                    "promotion_claimed": False,
                },
                artifacts={
                    "candidate_path": str(self._snapshot_path(before.snapshot_sha256)),
                    "prompt_template_path": str(prompt_path),
                },
            )
        except Exception as exc:
            result = UpdateResult(
                update_index=attempt_index,
                changed=False,
                status="failed",
                metrics={"candidate_unchanged": True},
                logs={"error_code": _safe_error_code(exc), "model_call_executed": False},
            )
            if self.fail_on_update_error:
                raise
            return result

        model_call_executed = False
        try:
            client = self.model_client or OpenRouterStructuredClient(
                timeout_seconds=self.timeout_seconds,
                route_contract=self.route_contract,
            )
            model_call_executed = True
            completion: StructuredCompletion = client.complete(
                evidence=projection.summary,
                current_components=before.components.to_dict(),
                seed=self.seed,
            )
            components = HarnessComponents.from_dict(
                completion.candidate,
                forbidden_fragments=projection.forbidden_fragments,
            )
        except Exception as exc:
            metrics: dict[str, Any] = {"candidate_unchanged": True}
            logs: dict[str, Any] = {
                "error_code": _safe_error_code(exc),
                "model_call_executed": model_call_executed,
            }
            if projection is not None and completion is not None:
                rejected = _rejected_attempt_record(
                    attempt_index=attempt_index,
                    before=before,
                    projection_sha256=projection.evidence_sha256,
                    completion=completion,
                    seed=self.seed,
                    error_code=_safe_error_code(exc),
                )
                attempt_ref = self._persist_attempt(rejected)
                new_refs = [*self._attempt_refs, attempt_ref]
                self._write_state_manifest(
                    self._prompt_path(before.snapshot_sha256),
                    candidate=before,
                    update_index=attempt_index,
                    attempt_refs=new_refs,
                )
                self._candidate = before
                self.update_index = attempt_index
                self._attempt_refs = new_refs
                self._synchronize_live_state(state)
                metrics.update(
                    {
                        "input_tokens": completion.usage.prompt_tokens,
                        "output_tokens": completion.usage.completion_tokens,
                        "cost_usd": completion.usage.cost_usd,
                    }
                )
                logs.update(
                    {
                        "request_sha256": completion.request_sha256,
                        "response_sha256": completion.response_sha256,
                        "evidence_sha256": projection.evidence_sha256,
                    }
                )
            result = UpdateResult(
                update_index=attempt_index,
                changed=False,
                status="failed",
                metrics=metrics,
                logs=logs,
            )
            if self.fail_on_update_error:
                raise
            return result

        changed = components.to_dict() != before.components.to_dict()
        candidate = before
        if changed:
            candidate = HarnessSnapshot.create(
                generation=before.generation + 1,
                parent_snapshot_sha256=before.snapshot_sha256,
                evidence_sha256=projection.evidence_sha256,
                components=components,
            )
        attempt_record = _attempt_record(
            attempt_index=attempt_index,
            before=before,
            candidate=candidate,
            projection_sha256=projection.evidence_sha256,
            completion=completion,
            changed=changed,
            seed=self.seed,
        )

        # Every mutable pointer is committed only after the candidate and prompt
        # have been validated and persisted under content-addressed names.
        if changed:
            self._persist_snapshot(candidate)
            prompt_path = self._persist_prompt(candidate)
        else:
            prompt_path = self._prompt_path(before.snapshot_sha256)
        attempt_ref = self._persist_attempt(attempt_record)
        new_refs = [*self._attempt_refs, attempt_ref]
        self._write_state_manifest(
            prompt_path,
            candidate=candidate,
            update_index=attempt_index,
            attempt_refs=new_refs,
        )
        self._candidate = candidate
        self.update_index = attempt_index
        self._attempt_refs = new_refs
        self._synchronize_live_state(state)
        return UpdateResult(
            update_index=attempt_index,
            changed=changed,
            status="updated" if changed else "unchanged",
            metrics={
                "num_trajectories": projection.summary["num_trajectories"],
                "success_count": projection.summary["success_count"],
                "failure_count": projection.summary["failure_count"],
                "input_tokens": completion.usage.prompt_tokens,
                "output_tokens": completion.usage.completion_tokens,
                "cost_usd": completion.usage.cost_usd,
            },
            logs={
                "model_call_executed": True,
                "request_sha256": completion.request_sha256,
                "response_sha256": completion.response_sha256,
                "evidence_sha256": projection.evidence_sha256,
                "candidate_sha256": candidate.snapshot_sha256,
                "causal_attribution_claimed": False,
                "promotion_claimed": False,
            },
            artifacts={
                "candidate_path": str(self._snapshot_path(candidate.snapshot_sha256)),
                "prompt_template_path": str(prompt_path),
            },
        )

    def save_checkpoint(self, state: BaselineState, path: Path) -> Checkpoint:
        self._require_initialized_state(state)
        checkpoint_input = Path(path)
        if _is_linklike(checkpoint_input):
            raise ValueError("checkpoint destination cannot be a symlink or junction")
        checkpoint_dir = checkpoint_input.resolve(strict=False)
        if checkpoint_dir.exists() and any(checkpoint_dir.iterdir()):
            raise ValueError("checkpoint destination must be absent or empty")
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        if _is_linklike(checkpoint_dir):
            raise ValueError("checkpoint destination cannot be a symlink or junction")
        source_inventory = _state_inventory(self.state_dir)
        _read_validated_state(
            self.state_dir,
            baseline_id=self.baseline_id,
            seed=self.seed,
            route_contract_sha256=ROUTE_CONTRACT_SHA256,
        )
        destination = checkpoint_dir / "baseline_state"
        shutil.copytree(self.state_dir, destination, symlinks=False)
        inventory = _state_inventory(destination)
        if inventory != source_inventory:
            shutil.rmtree(destination, ignore_errors=True)
            raise ValueError("checkpoint copy verification failed")
        manifest = {
            "type": "evoagent_seagym_checkpoint",
            "schema_version": CHECKPOINT_SCHEMA,
            "baseline_id": self.baseline_id,
            "state_ref": destination.name,
            "update_index": self.update_index,
            "state_inventory": inventory,
            "state_inventory_sha256": sha256_json(inventory),
            "state_metadata": {
                "a0_sha256": self._a0.snapshot_sha256,
                "evaluation_candidate_sha256": self._candidate.snapshot_sha256,
                "prompt_template": f"baseline_state/prompts/{self._candidate.snapshot_sha256}.md",
                "route_contract_sha256": ROUTE_CONTRACT_SHA256,
                "evaluation_only": True,
                "causal_attribution_claimed": False,
                "promotion_claimed": False,
            },
        }
        atomic_write_json(checkpoint_dir / "checkpoint.json", manifest)
        return Checkpoint(checkpoint_dir=checkpoint_dir, state_ref=destination.name, metadata=manifest)

    def load_checkpoint(self, checkpoint: Checkpoint) -> BaselineState:
        checkpoint_input = Path(checkpoint.checkpoint_dir)
        if _is_linklike(checkpoint_input):
            raise ValueError("checkpoint directory cannot be a symlink or junction")
        checkpoint_dir = checkpoint_input.resolve(strict=True)
        manifest_path = contained_path(checkpoint_dir, checkpoint_dir / "checkpoint.json", must_exist=True)
        raw_manifest = read_json(manifest_path)
        if isinstance(raw_manifest, dict) and isinstance(raw_manifest.get("baseline"), dict):
            manifest = raw_manifest["baseline"]
        else:
            manifest = raw_manifest
        _validate_checkpoint_manifest(manifest, self.baseline_id)
        source = contained_path(checkpoint_dir, checkpoint_dir / manifest["state_ref"], must_exist=True)
        if source.name != "baseline_state" or not source.is_dir() or source.is_symlink():
            raise ValueError("checkpoint state_ref is not the controlled baseline_state directory")
        inventory = _state_inventory(source)
        if inventory != manifest["state_inventory"] or sha256_json(inventory) != manifest["state_inventory_sha256"]:
            raise ValueError("checkpoint state inventory does not match")

        parent = self.state_dir.parent.resolve(strict=True)
        temp_dir = Path(tempfile.mkdtemp(prefix=f".{self.state_dir.name}.load-", dir=parent))
        backup = parent / f".{self.state_dir.name}.backup-{os.getpid()}"
        try:
            shutil.rmtree(temp_dir)
            shutil.copytree(source, temp_dir, symlinks=False)
            if _state_inventory(temp_dir) != inventory:
                raise ValueError("checkpoint copy verification failed")
            validated = _read_validated_state(
                temp_dir,
                baseline_id=self.baseline_id,
                seed=self.seed,
                route_contract_sha256=ROUTE_CONTRACT_SHA256,
            )
            if validated[2] != manifest["update_index"]:
                raise ValueError("checkpoint update index mismatch")
            expected_metadata = {
                "a0_sha256": validated[0].snapshot_sha256,
                "evaluation_candidate_sha256": validated[1].snapshot_sha256,
                "prompt_template": f"baseline_state/prompts/{validated[1].snapshot_sha256}.md",
                "route_contract_sha256": ROUTE_CONTRACT_SHA256,
                "evaluation_only": True,
                "causal_attribution_claimed": False,
                "promotion_claimed": False,
            }
            if manifest["state_metadata"] != expected_metadata:
                raise ValueError("checkpoint state metadata does not match the validated state")
            if backup.exists():
                raise ValueError("checkpoint backup path unexpectedly exists")
            if self.state_dir.exists():
                os.replace(self.state_dir, backup)
            os.replace(temp_dir, self.state_dir)
            self._a0, self._candidate, self.update_index, self._attempt_refs = validated
            shutil.rmtree(backup, ignore_errors=True)
        except Exception:
            if not self.state_dir.exists() and backup.exists():
                os.replace(backup, self.state_dir)
            raise
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
        return self._baseline_state(loaded=True)

    def report(self, state: BaselineState) -> dict[str, Any]:
        self._require_initialized_state(state)
        assert self._a0 is not None and self._candidate is not None
        attempts = [read_json(self.state_dir / reference) for reference in self._attempt_refs]
        total_cost = sum(float(item["usage"]["cost_usd"]) for item in attempts)
        return {
            "baseline_id": self.baseline_id,
            "adapter_version": ADAPTER_VERSION,
            "model_id": UPDATE_MODEL_ID,
            "route_contract_sha256": ROUTE_CONTRACT_SHA256,
            "seed": self.seed,
            "update_index": self.update_index,
            "a0_sha256": self._a0.snapshot_sha256,
            "evaluation_candidate_sha256": self._candidate.snapshot_sha256,
            "evaluation_candidate_generation": self._candidate.generation,
            "attempts": len(attempts),
            "update_model_calls": sum(item.get("model_call_executed") is True for item in attempts),
            "skipped_updates": sum(item.get("status") == "skipped_no_usable_atif" for item in attempts),
            "update_cost_usd": round(total_cost, 12),
            "causal_attribution_claimed": False,
            "promotion_claimed": False,
        }

    def _require_initialized_state(self, state: BaselineState) -> None:
        if Path(state.state_dir).resolve(strict=True) != self.state_dir.resolve(strict=True):
            raise ValueError("BaselineState does not belong to this controlled state directory")
        if self._a0 is None or self._candidate is None:
            self._load_state_from_disk()
        loaded = state.metadata.get("loaded") is True
        if state.metadata != self._baseline_state(loaded=loaded).metadata:
            raise ValueError("BaselineState metadata does not match the current committed candidate")

    def _synchronize_live_state(self, state: BaselineState) -> None:
        """Publish a committed candidate to SEAGym's long-lived state object."""

        loaded = state.metadata.get("loaded") is True
        current = self._baseline_state(loaded=loaded)
        state.metadata.clear()
        state.metadata.update(current.metadata)

    def _snapshot_path(self, digest: str) -> Path:
        return self.snapshots_dir / f"{digest}.json"

    def _prompt_path(self, digest: str) -> Path:
        return self.prompts_dir / f"{digest}.md"

    def _persist_snapshot(self, snapshot: HarnessSnapshot) -> Path:
        path = self._snapshot_path(snapshot.snapshot_sha256)
        if path.exists():
            existing = HarnessSnapshot.from_dict(read_json(path))
            if existing != snapshot:
                raise ValueError("content-addressed snapshot collision")
            return path
        atomic_write_json(path, snapshot.to_dict())
        if HarnessSnapshot.from_dict(read_json(path)) != snapshot:
            path.unlink(missing_ok=True)
            raise ValueError("snapshot write verification failed")
        return path

    def _persist_prompt(self, snapshot: HarnessSnapshot) -> Path:
        path = self._prompt_path(snapshot.snapshot_sha256)
        rendered = render_prompt_projection(snapshot)
        if path.exists():
            if path.read_text(encoding="utf-8") != rendered:
                raise ValueError("content-addressed prompt collision")
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(rendered)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
        finally:
            temp_path.unlink(missing_ok=True)
        return path

    def _persist_attempt(self, record: dict[str, Any]) -> str:
        _validate_attempt_record(record)
        path = self.attempts_dir / f"{record['attempt_index']:06d}-{record['request_sha256']}.json"
        if path.exists():
            if read_json(path) != record:
                raise ValueError("attempt record collision")
            return str(path.relative_to(self.state_dir)).replace("\\", "/")
        atomic_write_json(path, record)
        return str(path.relative_to(self.state_dir)).replace("\\", "/")

    def _write_state_manifest(
        self,
        prompt_path: Path,
        *,
        candidate: HarnessSnapshot | None = None,
        update_index: int | None = None,
        attempt_refs: list[str] | None = None,
    ) -> None:
        assert self._a0 is not None and self._candidate is not None
        candidate = candidate or self._candidate
        update_index = self.update_index if update_index is None else update_index
        attempt_refs = list(self._attempt_refs if attempt_refs is None else attempt_refs)
        manifest = {
            "schema_version": STATE_SCHEMA,
            "baseline_id": self.baseline_id,
            "adapter_version": ADAPTER_VERSION,
            "model_id": UPDATE_MODEL_ID,
            "seed": self.seed,
            "route_contract_sha256": ROUTE_CONTRACT_SHA256,
            "update_index": update_index,
            "a0_sha256": self._a0.snapshot_sha256,
            "evaluation_candidate_sha256": candidate.snapshot_sha256,
            "prompt_template": str(prompt_path.relative_to(self.state_dir)).replace("\\", "/"),
            "attempt_refs": attempt_refs,
            "causal_attribution_claimed": False,
            "promotion_claimed": False,
        }
        atomic_write_json(self.state_manifest_path, manifest)

    def _load_state_from_disk(self) -> None:
        self._a0, self._candidate, self.update_index, self._attempt_refs = _read_validated_state(
            self.state_dir,
            baseline_id=self.baseline_id,
            seed=self.seed,
            route_contract_sha256=ROUTE_CONTRACT_SHA256,
        )

    def _baseline_state(self, *, loaded: bool) -> BaselineState:
        assert self._a0 is not None and self._candidate is not None
        return BaselineState(
            self.state_dir,
            {
                "baseline_id": self.baseline_id,
                "loaded": loaded,
                "a0_sha256": self._a0.snapshot_sha256,
                "evaluation_candidate_sha256": self._candidate.snapshot_sha256,
                "evaluation_candidate_path": str(self._snapshot_path(self._candidate.snapshot_sha256)),
                "prompt_template_path": str(self._prompt_path(self._candidate.snapshot_sha256)),
                "model_id": UPDATE_MODEL_ID,
                "route_contract_sha256": ROUTE_CONTRACT_SHA256,
                "seed": self.seed,
                "evaluation_only": True,
                "causal_attribution_claimed": False,
                "promotion_claimed": False,
            },
        )


def render_prompt_projection(snapshot: HarnessSnapshot) -> str:
    lines = [
        "# EvoAgent evaluation-only harness",
        "",
        "This candidate has not been promoted and makes no causal claim.",
        "",
        "## Skills",
    ]
    lines.extend(f"- {item.name}: {item.guidance}" for item in snapshot.components.skills)
    lines.extend(("", "## Memory"))
    lines.extend(f"- {item.topic}: {item.lesson}" for item in snapshot.components.memory)
    lines.extend(("", "## Router"))
    lines.extend(f"- When {item.condition} use {item.skill}." for item in snapshot.components.router)
    policy = snapshot.components.policy
    lines.extend(
        (
            "",
            "## Policy",
            f"- Planning: {policy.planning}",
            f"- Verification: {policy.verification}",
            f"- Recovery: {policy.recovery}",
            f"- Maximum iterations: {policy.max_iterations}",
            "",
            "## Task instruction",
            "",
            "{{ instruction }}",
            "",
        )
    )
    return "\n".join(lines)


def _validate_model_binding(raw: Any) -> None:
    if not isinstance(raw, dict):
        raise ValueError("the configured update model binding is required")
    allowed = {"provider", "model", "api_base", "api_key_env"}
    if set(raw) - allowed:
        raise ValueError("update model binding contains unsupported fields")
    if raw.get("provider") not in {"openrouter", "openai_compatible"}:
        raise ValueError("update model provider must be OpenRouter-compatible")
    if raw.get("model") != UPDATE_MODEL_ID:
        raise ValueError(f"update model must be exactly {UPDATE_MODEL_ID}")
    if raw.get("api_base") not in {"https://openrouter.ai/api/v1", OPENROUTER_ENDPOINT}:
        raise ValueError("update model api_base must be the official OpenRouter API")
    if raw.get("api_key_env") != "OPENROUTER_API_KEY":
        raise ValueError("update model key binding must be OPENROUTER_API_KEY")


def _prepare_controlled_state_dir(state_dir: Path) -> None:
    if _is_linklike(state_dir):
        raise ValueError("state_dir cannot be a symlink or junction")
    state_dir.mkdir(parents=True, exist_ok=True)
    if not state_dir.is_dir():
        raise ValueError("state_dir must be a directory")


def _attempt_record(
    *,
    attempt_index: int,
    before: HarnessSnapshot,
    candidate: HarnessSnapshot,
    projection_sha256: str,
    completion: StructuredCompletion,
    changed: bool,
    seed: int,
) -> dict[str, Any]:
    return {
        "schema_version": ATTEMPT_SCHEMA,
        "attempt_index": attempt_index,
        "status": "accepted" if changed else "accepted_unchanged",
        "model_call_executed": True,
        "model_id": UPDATE_MODEL_ID,
        "route_contract_sha256": ROUTE_CONTRACT_SHA256,
        "seed": seed,
        "before_snapshot_sha256": before.snapshot_sha256,
        "candidate_snapshot_sha256": candidate.snapshot_sha256,
        "evidence_sha256": projection_sha256,
        "request_sha256": completion.request_sha256,
        "response_sha256": completion.response_sha256,
        "served_model_id": completion.served_model_id,
        "provider": completion.provider,
        "usage": completion.usage.to_dict(),
        "causal_attribution_claimed": False,
        "promotion_claimed": False,
    }


def _rejected_attempt_record(
    *,
    attempt_index: int,
    before: HarnessSnapshot,
    projection_sha256: str,
    completion: StructuredCompletion,
    seed: int,
    error_code: str,
) -> dict[str, Any]:
    return {
        "schema_version": ATTEMPT_SCHEMA,
        "attempt_index": attempt_index,
        "status": "rejected",
        "model_call_executed": True,
        "error_code": error_code,
        "model_id": UPDATE_MODEL_ID,
        "route_contract_sha256": ROUTE_CONTRACT_SHA256,
        "seed": seed,
        "before_snapshot_sha256": before.snapshot_sha256,
        "candidate_snapshot_sha256": before.snapshot_sha256,
        "evidence_sha256": projection_sha256,
        "request_sha256": completion.request_sha256,
        "response_sha256": completion.response_sha256,
        "served_model_id": completion.served_model_id,
        "provider": completion.provider,
        "usage": completion.usage.to_dict(),
        "causal_attribution_claimed": False,
        "promotion_claimed": False,
    }


def _skipped_attempt_record(
    *,
    attempt_index: int,
    before: HarnessSnapshot,
    projection_sha256: str,
    seed: int,
) -> dict[str, Any]:
    request_sha256 = sha256_json(
        {
            "schema_version": "evoagent-seagym-no-model-call-v1",
            "attempt_index": attempt_index,
            "before_snapshot_sha256": before.snapshot_sha256,
            "evidence_sha256": projection_sha256,
            "skip_code": NO_USABLE_ATIF_SKIP_CODE,
        }
    )
    return {
        "schema_version": ATTEMPT_SCHEMA,
        "attempt_index": attempt_index,
        "status": "skipped_no_usable_atif",
        "model_call_executed": False,
        "skip_code": NO_USABLE_ATIF_SKIP_CODE,
        "model_id": UPDATE_MODEL_ID,
        "route_contract_sha256": ROUTE_CONTRACT_SHA256,
        "seed": seed,
        "before_snapshot_sha256": before.snapshot_sha256,
        "candidate_snapshot_sha256": before.snapshot_sha256,
        "evidence_sha256": projection_sha256,
        "request_sha256": request_sha256,
        "response_sha256": None,
        "served_model_id": None,
        "provider": None,
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
        },
        "causal_attribution_claimed": False,
        "promotion_claimed": False,
    }


def _validate_state_manifest(
    raw: Any,
    baseline_id: str,
    seed: int,
    route_contract_sha256: str,
) -> None:
    required = {
        "schema_version",
        "baseline_id",
        "adapter_version",
        "model_id",
        "route_contract_sha256",
        "seed",
        "update_index",
        "a0_sha256",
        "evaluation_candidate_sha256",
        "prompt_template",
        "attempt_refs",
        "causal_attribution_claimed",
        "promotion_claimed",
    }
    if not isinstance(raw, dict) or set(raw) != required:
        raise ValueError("state manifest has an invalid shape")
    if raw["schema_version"] != STATE_SCHEMA or raw["baseline_id"] != baseline_id:
        raise ValueError("state identity does not match baseline")
    if raw["adapter_version"] != ADAPTER_VERSION or raw["model_id"] != UPDATE_MODEL_ID or raw["seed"] != seed:
        raise ValueError("state protocol lock does not match")
    if raw["route_contract_sha256"] != route_contract_sha256:
        raise ValueError("state route contract lock does not match")
    if isinstance(raw["update_index"], bool) or not isinstance(raw["update_index"], int) or raw["update_index"] < 0:
        raise ValueError("state update_index is invalid")
    if raw["causal_attribution_claimed"] is not False or raw["promotion_claimed"] is not False:
        raise ValueError("state cannot claim causality or promotion")
    for key in ("a0_sha256", "evaluation_candidate_sha256"):
        if not isinstance(raw[key], str) or len(raw[key]) != 64 or any(char not in "0123456789abcdef" for char in raw[key]):
            raise ValueError("state contains an invalid snapshot hash")
    if not isinstance(raw["prompt_template"], str) or not raw["prompt_template"].startswith("prompts/"):
        raise ValueError("state prompt_template is invalid")
    attempt_refs = raw["attempt_refs"]
    if not isinstance(attempt_refs, list) or len(attempt_refs) != raw["update_index"]:
        raise ValueError("state attempt references do not match update_index")
    if len(set(attempt_refs)) != len(attempt_refs):
        raise ValueError("state attempt references must be unique")
    for index, reference in enumerate(attempt_refs, start=1):
        if not isinstance(reference, str) or not re.fullmatch(
            rf"attempts/{index:06d}-[0-9a-f]{{64}}\.json", reference
        ):
            raise ValueError("state attempt reference is invalid")


def _state_inventory(root: Path) -> dict[str, str]:
    allowed_top = {"state.json", "snapshots", "prompts", "attempts"}
    if not root.is_dir() or _is_linklike(root):
        raise ValueError("state root must be a regular controlled directory")
    inventory: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if _is_linklike(path):
            raise ValueError("state inventory cannot contain symlinks or junctions")
        relative = path.relative_to(root)
        if relative.parts[0] not in allowed_top:
            raise ValueError("state inventory contains an unexpected path")
        if path.is_dir():
            if len(relative.parts) != 1 or relative.name not in {"snapshots", "prompts", "attempts"}:
                raise ValueError("state inventory contains an unexpected directory")
            continue
        suffix = path.suffix
        if relative.parts == ("state.json",):
            pass
        elif len(relative.parts) == 2 and relative.parts[0] in {"snapshots", "attempts"} and suffix == ".json":
            pass
        elif len(relative.parts) == 2 and relative.parts[0] == "prompts" and suffix == ".md":
            pass
        else:
            raise ValueError("state inventory contains an unexpected file type")
        inventory[str(relative).replace("\\", "/")] = sha256_file(path, max_bytes=2 * 1024 * 1024)
    if "state.json" not in inventory:
        raise ValueError("state inventory is missing state.json")
    return inventory


def _read_validated_state(
    root: Path,
    *,
    baseline_id: str,
    seed: int,
    route_contract_sha256: str,
) -> tuple[HarnessSnapshot, HarnessSnapshot, int, list[str]]:
    inventory = _state_inventory(root)
    manifest_path = contained_path(root, root / "state.json", must_exist=True)
    manifest = read_json(manifest_path)
    _validate_state_manifest(manifest, baseline_id, seed, route_contract_sha256)

    snapshots: dict[str, HarnessSnapshot] = {}
    for reference in sorted(key for key in inventory if key.startswith("snapshots/")):
        if not re.fullmatch(r"snapshots/[0-9a-f]{64}\.json", reference):
            raise ValueError("snapshot filename is not content-addressed")
        path = contained_path(root, root / reference, must_exist=True)
        snapshot = HarnessSnapshot.from_dict(read_json(path))
        if reference != f"snapshots/{snapshot.snapshot_sha256}.json":
            raise ValueError("snapshot filename does not match its content hash")
        snapshots[snapshot.snapshot_sha256] = snapshot

    a0 = snapshots.get(manifest["a0_sha256"])
    candidate = snapshots.get(manifest["evaluation_candidate_sha256"])
    if a0 is None or candidate is None:
        raise ValueError("state references a missing snapshot")
    if a0.generation != 0 or a0.parent_snapshot_sha256 is not None:
        raise ValueError("state A0 snapshot is invalid")

    prompt_refs = sorted(key for key in inventory if key.startswith("prompts/"))
    for reference in prompt_refs:
        if not re.fullmatch(r"prompts/[0-9a-f]{64}\.md", reference):
            raise ValueError("prompt filename is not content-addressed")
        digest = Path(reference).stem
        snapshot = snapshots.get(digest)
        if snapshot is None:
            raise ValueError("prompt does not have a matching snapshot")
        path = contained_path(root, root / reference, must_exist=True)
        if path.read_text(encoding="utf-8") != render_prompt_projection(snapshot):
            raise ValueError("prompt projection does not match its snapshot")
    expected_prompt = f"prompts/{candidate.snapshot_sha256}.md"
    if manifest["prompt_template"] != expected_prompt or expected_prompt not in inventory:
        raise ValueError("state prompt does not match evaluation candidate")

    # Validate every attempt file for privacy/accounting shape, but treat the
    # manifest's ordered references as authoritative. A crash may leave a safe,
    # unreferenced orphan; it must never silently become part of the experiment.
    attempt_records: dict[str, dict[str, Any]] = {}
    for reference in sorted(key for key in inventory if key.startswith("attempts/")):
        if not re.fullmatch(r"attempts/\d{6}-[0-9a-f]{64}\.json", reference):
            raise ValueError("attempt filename is invalid")
        record = read_json(contained_path(root, root / reference, must_exist=True))
        _validate_attempt_record(record, seed=seed)
        expected_ref = f"attempts/{record['attempt_index']:06d}-{record['request_sha256']}.json"
        if reference != expected_ref:
            raise ValueError("attempt filename does not match its record")
        attempt_records[reference] = record

    current = a0
    for index, reference in enumerate(manifest["attempt_refs"], start=1):
        record = attempt_records.get(reference)
        if record is None or record["attempt_index"] != index:
            raise ValueError("state references a missing or out-of-order attempt")
        if record["before_snapshot_sha256"] != current.snapshot_sha256:
            raise ValueError("attempt lineage does not match the stable candidate")
        next_hash = record["candidate_snapshot_sha256"]
        if record["status"] == "accepted":
            next_snapshot = snapshots.get(next_hash)
            if (
                next_snapshot is None
                or next_snapshot.parent_snapshot_sha256 != current.snapshot_sha256
                or next_snapshot.generation != current.generation + 1
                or next_snapshot.evidence_sha256 != record["evidence_sha256"]
            ):
                raise ValueError("accepted attempt does not bind a valid child snapshot")
            current = next_snapshot
        elif next_hash != current.snapshot_sha256:
            raise ValueError("unchanged or rejected attempt changed the stable candidate")
    if current.snapshot_sha256 != candidate.snapshot_sha256:
        raise ValueError("evaluation candidate does not match the authoritative attempt chain")
    return a0, candidate, manifest["update_index"], list(manifest["attempt_refs"])


def _validate_attempt_record(raw: Any, *, seed: int | None = None) -> None:
    common = {
        "schema_version",
        "attempt_index",
        "status",
        "model_call_executed",
        "model_id",
        "route_contract_sha256",
        "seed",
        "before_snapshot_sha256",
        "candidate_snapshot_sha256",
        "evidence_sha256",
        "request_sha256",
        "response_sha256",
        "served_model_id",
        "provider",
        "usage",
        "causal_attribution_claimed",
        "promotion_claimed",
    }
    if not isinstance(raw, dict) or raw.get("status") not in {
        "accepted",
        "accepted_unchanged",
        "rejected",
        "skipped_no_usable_atif",
    }:
        raise ValueError("attempt record has an invalid status or shape")
    expected = common
    if raw["status"] == "rejected":
        expected |= {"error_code"}
    elif raw["status"] == "skipped_no_usable_atif":
        expected |= {"skip_code"}
    if set(raw) != expected:
        raise ValueError("attempt record has an invalid shape")
    if raw["schema_version"] != ATTEMPT_SCHEMA or raw["model_id"] != UPDATE_MODEL_ID:
        raise ValueError("attempt protocol lock does not match")
    if raw["route_contract_sha256"] != ROUTE_CONTRACT_SHA256:
        raise ValueError("attempt route contract lock does not match")
    if isinstance(raw["attempt_index"], bool) or not isinstance(raw["attempt_index"], int) or raw["attempt_index"] < 1:
        raise ValueError("attempt index is invalid")
    if isinstance(raw["seed"], bool) or not isinstance(raw["seed"], int) or not 0 <= raw["seed"] <= 2**63 - 1:
        raise ValueError("attempt seed is invalid")
    if seed is not None and raw["seed"] != seed:
        raise ValueError("attempt seed does not match state")
    for key in (
        "before_snapshot_sha256",
        "candidate_snapshot_sha256",
        "evidence_sha256",
        "request_sha256",
    ):
        if not _is_hash(raw[key]):
            raise ValueError("attempt contains an invalid hash")
    route = expected_route_contract()
    if raw["status"] == "skipped_no_usable_atif":
        if raw["model_call_executed"] is not False:
            raise ValueError("skipped attempt cannot claim a model call")
        if raw["skip_code"] != NO_USABLE_ATIF_SKIP_CODE:
            raise ValueError("skipped attempt code drifted")
        expected_request_sha256 = sha256_json(
            {
                "schema_version": "evoagent-seagym-no-model-call-v1",
                "attempt_index": raw["attempt_index"],
                "before_snapshot_sha256": raw["before_snapshot_sha256"],
                "evidence_sha256": raw["evidence_sha256"],
                "skip_code": NO_USABLE_ATIF_SKIP_CODE,
            }
        )
        if raw["request_sha256"] != expected_request_sha256:
            raise ValueError("skipped attempt no-call digest is invalid")
        if raw["response_sha256"] is not None or raw["served_model_id"] is not None or raw["provider"] is not None:
            raise ValueError("skipped attempt contains fabricated model evidence")
    else:
        if raw["model_call_executed"] is not True or not _is_hash(raw["response_sha256"]):
            raise ValueError("model attempt execution evidence is invalid")
        if raw["served_model_id"] not in route["accepted_response_models"] or raw["provider"] != route["response_provider"]:
            raise ValueError("attempt served model or provider drifted")
    usage = raw["usage"]
    if not isinstance(usage, dict) or set(usage) != {"prompt_tokens", "completion_tokens", "total_tokens", "cost_usd"}:
        raise ValueError("attempt usage has an invalid shape")
    prompt = _bounded_nonnegative_int(usage["prompt_tokens"], "attempt prompt tokens")
    completion = _bounded_nonnegative_int(usage["completion_tokens"], "attempt completion tokens")
    total = _bounded_nonnegative_int(usage["total_tokens"], "attempt total tokens")
    if total != prompt + completion:
        raise ValueError("attempt token accounting is inconsistent")
    cost = usage["cost_usd"]
    if isinstance(cost, bool) or not isinstance(cost, int | float) or not math.isfinite(float(cost)) or not 0 <= float(cost) <= 100_000:
        raise ValueError("attempt cost accounting is invalid")
    if raw["causal_attribution_claimed"] is not False or raw["promotion_claimed"] is not False:
        raise ValueError("attempt cannot claim causality or promotion")
    if raw["status"] == "skipped_no_usable_atif" and (prompt != 0 or completion != 0 or total != 0 or float(cost) != 0.0):
        raise ValueError("skipped attempt cannot contain model usage")
    if raw["status"] == "accepted" and raw["candidate_snapshot_sha256"] == raw["before_snapshot_sha256"]:
        raise ValueError("accepted attempt must change the candidate")
    if raw["status"] != "accepted" and raw["candidate_snapshot_sha256"] != raw["before_snapshot_sha256"]:
        raise ValueError("unchanged or rejected attempt cannot change the candidate")
    if raw["status"] == "rejected" and (
        not isinstance(raw["error_code"], str)
        or not raw["error_code"].isidentifier()
        or len(raw["error_code"]) > 80
    ):
        raise ValueError("rejected attempt error_code is invalid")


def _validate_checkpoint_manifest(raw: Any, baseline_id: str) -> None:
    required = {
        "type",
        "schema_version",
        "baseline_id",
        "state_ref",
        "update_index",
        "state_inventory",
        "state_inventory_sha256",
        "state_metadata",
    }
    if not isinstance(raw, dict) or set(raw) != required:
        raise ValueError("checkpoint manifest has an invalid shape")
    if raw["type"] != "evoagent_seagym_checkpoint" or raw["schema_version"] != CHECKPOINT_SCHEMA:
        raise ValueError("checkpoint schema does not match")
    if raw["baseline_id"] != baseline_id or raw["state_ref"] != "baseline_state":
        raise ValueError("checkpoint identity or state_ref does not match")
    if isinstance(raw["update_index"], bool) or not isinstance(raw["update_index"], int) or raw["update_index"] < 0:
        raise ValueError("checkpoint update index is invalid")
    inventory = raw["state_inventory"]
    if not isinstance(inventory, dict) or sha256_json(inventory) != raw["state_inventory_sha256"]:
        raise ValueError("checkpoint inventory hash is invalid")
    if not _is_hash(raw["state_inventory_sha256"]):
        raise ValueError("checkpoint inventory digest is invalid")
    for reference, digest in inventory.items():
        if (
            not isinstance(reference, str)
            or reference.startswith(("/", "\\"))
            or ".." in Path(reference).parts
            or not _is_hash(digest)
        ):
            raise ValueError("checkpoint inventory entry is invalid")
    metadata = raw["state_metadata"]
    metadata_keys = {
        "a0_sha256",
        "evaluation_candidate_sha256",
        "prompt_template",
        "route_contract_sha256",
        "evaluation_only",
        "causal_attribution_claimed",
        "promotion_claimed",
    }
    if not isinstance(metadata, dict) or set(metadata) != metadata_keys:
        raise ValueError("checkpoint state metadata has an invalid shape")
    if not _is_hash(metadata["a0_sha256"]) or not _is_hash(metadata["evaluation_candidate_sha256"]):
        raise ValueError("checkpoint state metadata contains an invalid snapshot hash")
    if metadata["prompt_template"] != f"baseline_state/prompts/{metadata['evaluation_candidate_sha256']}.md":
        raise ValueError("checkpoint state metadata prompt reference is invalid")
    if metadata["route_contract_sha256"] != ROUTE_CONTRACT_SHA256:
        raise ValueError("checkpoint route contract lock does not match")
    if (
        metadata["evaluation_only"] is not True
        or metadata["causal_attribution_claimed"] is not False
        or metadata["promotion_claimed"] is not False
    ):
        raise ValueError("checkpoint state metadata violates the claim boundary")


def _bounded_nonnegative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 10_000_000_000:
        raise ValueError(f"{label} is invalid")
    return value


def _is_hash(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{64}", value))


def _is_linklike(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


def _safe_error_code(exc: Exception) -> str:
    name = type(exc).__name__
    return name if name.isidentifier() and len(name) <= 80 else "UpdateError"
