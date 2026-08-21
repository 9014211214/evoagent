from __future__ import annotations

import subprocess
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evoagent.execution import (
    ExecutionAdapter,
    ExecutionAuthorization,
    ExecutionAuthorizationManager,
    ExecutionBudget,
    ExecutionInvocation,
    SQLiteExecutionUseStore,
)
from evoagent.execution.environment import build_authorized_environment
from evoagent.execution.redaction import redact_completed_process, redact_timeout


TERMINAL_BENCH_2_1 = "terminal-bench/terminal-bench-2-1"
HARBOR_REVIEWED_COMMIT = "0348989adffbb43bf0b410fd36197333239633f1"
HARBOR_VERSION_PATTERN = r"^0\.16(?:\.\d+)?(?:\b|[-+])"


class HarborRunSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    dataset_ref: str
    agent: str
    model: str
    command: tuple[str, ...]
    workspace: str
    trials_per_task: int = Field(gt=0)
    concurrency: int = Field(gt=0)
    upload: bool = False
    public: bool = False
    required_environment_variables: tuple[str, ...] = ()
    execution_budget: ExecutionBudget
    execution_enabled: bool = False

    @field_validator("workspace")
    @classmethod
    def require_absolute_workspace(cls, value: str) -> str:
        path = Path(value)
        if "\x00" in value or not path.is_absolute() or ".." in path.parts:
            raise ValueError("Harbor workspace must be a safe absolute path.")
        return str(path)

    @field_validator("required_environment_variables")
    @classmethod
    def canonicalize_environment_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("Harbor environment-variable names must be unique.")
        return tuple(sorted(value))

    @model_validator(mode="after")
    def validate_visibility(self):
        if self.public and not self.upload:
            raise ValueError("Public Harbor results require upload.")
        if self.upload and "--upload" not in self.command:
            raise ValueError("Harbor upload intent must be present in the approved command.")
        if self.public and "--public" not in self.command:
            raise ValueError("Harbor public intent must be present in the approved command.")
        return self


class HarborCLIAdapter:
    """Optional Harbor CLI integration for Terminal-Bench 2.1.

    Run-plan generation remains available without execution. Any subprocess call
    additionally requires a verified, unexpired, exact-command authorization and
    a one-use ledger claim.
    """

    def __init__(self, *, binary: str = "harbor", execution_enabled: bool = False):
        self.binary = binary
        self.execution_enabled = execution_enabled

    def build_run(
        self,
        *,
        agent: str,
        model: str,
        workspace: str,
        dataset_ref: str = TERMINAL_BENCH_2_1,
        trials_per_task: int = 1,
        concurrency: int = 1,
        required_environment_variables: tuple[str, ...] = (),
        leaderboard_mode: bool = False,
        max_cost_usd: float = 0.0,
        max_wall_seconds: int = 7200,
    ) -> HarborRunSpec:
        if not agent.strip() or not model.strip():
            raise ValueError("Harbor agent and model must be explicit.")
        if leaderboard_mode and trials_per_task < 5:
            raise ValueError("Terminal-Bench leaderboard mode requires at least five trials per task.")
        resolved_workspace = str(Path(workspace).expanduser().resolve())
        command: list[str] = [
            self.binary,
            "run",
            "-d",
            dataset_ref,
            "-a",
            agent,
            "-m",
            model,
            "-k",
            str(trials_per_task),
            "-n",
            str(concurrency),
            "--jobs-dir",
            resolved_workspace,
        ]
        if leaderboard_mode:
            command.extend(["--upload", "--public"])
        return HarborRunSpec(
            dataset_ref=dataset_ref,
            agent=agent,
            model=model,
            command=tuple(command),
            workspace=resolved_workspace,
            trials_per_task=trials_per_task,
            concurrency=concurrency,
            upload=leaderboard_mode,
            public=leaderboard_mode,
            required_environment_variables=required_environment_variables,
            execution_budget=ExecutionBudget(
                max_cost_usd=max_cost_usd,
                max_wall_seconds=max_wall_seconds,
                max_trials=trials_per_task,
            ),
            execution_enabled=self.execution_enabled,
        )

    def build_leaderboard_run(self, **kwargs) -> HarborRunSpec:
        kwargs["leaderboard_mode"] = True
        return self.build_run(**kwargs)

    @staticmethod
    def to_execution_invocation(spec: HarborRunSpec) -> ExecutionInvocation:
        return ExecutionInvocation(
            adapter=ExecutionAdapter.HARBOR,
            command=spec.command,
            workspace=spec.workspace,
            required_environment_variables=spec.required_environment_variables,
            network_access=True,
            upload=spec.upload,
            public=spec.public,
            training=False,
            workspace_must_be_empty=True,
            budget=spec.execution_budget,
            version_arguments=("--version",),
            expected_version_pattern=HARBOR_VERSION_PATTERN,
        )

    def execute(
        self,
        spec: HarborRunSpec,
        *,
        authorization: ExecutionAuthorization | None = None,
        authorization_manager: ExecutionAuthorizationManager | None = None,
        use_store: SQLiteExecutionUseStore | None = None,
        environment: dict[str, str] | None = None,
        now=None,
    ) -> subprocess.CompletedProcess[str]:
        if not self.execution_enabled or not spec.execution_enabled:
            raise PermissionError("Harbor execution is disabled; use the generated run spec.")
        if authorization is None or authorization_manager is None or use_store is None:
            raise PermissionError(
                "Harbor execution requires an external authorization, preflight manager, "
                "and one-use ledger."
            )

        invocation = self.to_execution_invocation(spec)
        preflight = authorization_manager.preflight(
            authorization,
            invocation,
            environment=environment,
            now=now,
        )
        use_store.claim(authorization, preflight, now=now)

        env = build_authorized_environment(invocation, environment)
        secret_values = tuple(
            env.get(name, "")
            for name in spec.required_environment_variables
        )
        workspace = Path(spec.workspace)
        try:
            completed = subprocess.run(
                [preflight.executable_path, *spec.command[1:]],
                cwd=workspace,
                env=env,
                text=True,
                capture_output=True,
                timeout=spec.execution_budget.max_wall_seconds,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            use_store.complete(
                authorization.authorization_hash,
                return_code=124,
                now=now,
            )
            raise redact_timeout(exc, secret_values) from None
        except OSError:
            use_store.complete(
                authorization.authorization_hash,
                return_code=127,
                now=now,
            )
            raise
        use_store.complete(
            authorization.authorization_hash,
            return_code=completed.returncode,
            now=now,
        )
        return redact_completed_process(completed, secret_values)


__all__ = [
    "HARBOR_REVIEWED_COMMIT",
    "HARBOR_VERSION_PATTERN",
    "HarborCLIAdapter",
    "HarborRunSpec",
    "TERMINAL_BENCH_2_1",
]
