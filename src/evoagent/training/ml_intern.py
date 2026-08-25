from __future__ import annotations

import json
import subprocess
from pathlib import Path

from evoagent.execution import (
    ExecutionAdapter,
    ExecutionAuthorization,
    ExecutionAuthorizationManager,
    ExecutionBudget,
    ExecutionInvocation,
    SQLiteExecutionUseStore,
)
from evoagent.execution.process import platform_executable_argv
from evoagent.execution.environment import build_authorized_environment
from evoagent.execution.redaction import redact_completed_process, redact_timeout
from evoagent.training.models import MLInternTaskSpec, ModelImprovementTicket, TrainingPlan


ML_INTERN_REVIEWED_COMMIT = "550a209701701e6a9ac7cac70b8dbd508822d467"
ML_INTERN_VERSION_PATTERN = r"Hugging Face Agent CLI"


class MLInternCLIAdapter:
    """Optional Apache-2.0 ml-intern CLI integration.

    Task-plan generation is always available. Execution additionally requires an
    exact-command external authorization, two distinct non-requester approvers,
    successful preflight, and a transactional one-use ledger claim.
    """

    def __init__(
        self,
        *,
        binary: str = "ml-intern",
        max_iterations: int = 100,
        agent_model: str | None = None,
        execution_enabled: bool = False,
    ):
        if max_iterations <= 0:
            raise ValueError("ml-intern max_iterations must be positive.")
        self.binary = binary
        self.max_iterations = max_iterations
        self.agent_model = agent_model
        self.execution_enabled = execution_enabled

    def build_task(
        self,
        ticket: ModelImprovementTicket,
        plan: TrainingPlan,
        *,
        workspace: str,
    ) -> MLInternTaskSpec:
        prompt = self._prompt(ticket, plan)
        command: list[str] = [
            self.binary,
            "--sandbox-tools",
            "--max-iterations",
            str(self.max_iterations),
            "--no-stream",
        ]
        if self.agent_model:
            command.extend(["--model", self.agent_model])
        command.append(prompt)
        return MLInternTaskSpec(
            command=tuple(command),
            prompt=prompt,
            workspace=str(Path(workspace).expanduser().resolve()),
            runtime_config={"tool_runtime": "sandbox", "share_traces": False},
            required_environment_variables=("HF_TOKEN",),
            execution_enabled=self.execution_enabled,
        )

    def to_execution_invocation(
        self,
        spec: MLInternTaskSpec,
        *,
        budget: ExecutionBudget,
    ) -> ExecutionInvocation:
        if budget.max_iterations < self.max_iterations:
            raise ValueError(
                "Execution budget max_iterations must cover the approved ml-intern command."
            )
        return ExecutionInvocation(
            adapter=ExecutionAdapter.ML_INTERN,
            command=spec.command,
            workspace=str(Path(spec.workspace).expanduser().resolve()),
            required_environment_variables=spec.required_environment_variables,
            network_access=True,
            upload=False,
            public=False,
            training=True,
            workspace_must_be_empty=True,
            budget=budget,
            version_arguments=("--help",),
            expected_version_pattern=ML_INTERN_VERSION_PATTERN,
        )

    def execute(
        self,
        spec: MLInternTaskSpec,
        *,
        budget: ExecutionBudget | None = None,
        authorization: ExecutionAuthorization | None = None,
        authorization_manager: ExecutionAuthorizationManager | None = None,
        use_store: SQLiteExecutionUseStore | None = None,
        environment: dict[str, str] | None = None,
        now=None,
    ) -> subprocess.CompletedProcess[str]:
        if not self.execution_enabled or not spec.execution_enabled:
            raise PermissionError("ml-intern execution is disabled; use the generated task spec.")
        if (
            budget is None
            or authorization is None
            or authorization_manager is None
            or use_store is None
        ):
            raise PermissionError(
                "ml-intern execution requires an approved budget, external authorization, "
                "preflight manager, and one-use ledger."
            )

        invocation = self.to_execution_invocation(spec, budget=budget)
        preflight = authorization_manager.preflight(
            authorization,
            invocation,
            environment=environment,
            now=now,
        )
        use_store.claim(authorization, preflight, now=now)

        workspace = Path(spec.workspace)
        config_dir = workspace / ".evoagent"
        config_path = config_dir / "ml-intern-config.json"
        env = build_authorized_environment(invocation, environment)
        secret_values = tuple(
            env.get(name, "")
            for name in spec.required_environment_variables
        )
        try:
            config_dir.mkdir(exist_ok=False)
            config_path.write_text(
                json.dumps(spec.runtime_config, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            env["ML_INTERN_CLI_CONFIG"] = str(config_path)
            completed = subprocess.run(
                platform_executable_argv(
                    preflight.executable_path,
                    spec.command[1:],
                ),
                cwd=workspace,
                env=env,
                text=True,
                capture_output=True,
                timeout=budget.max_wall_seconds,
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
        except Exception:
            use_store.complete(
                authorization.authorization_hash,
                return_code=1,
                now=now,
            )
            raise
        use_store.complete(
            authorization.authorization_hash,
            return_code=completed.returncode,
            now=now,
        )
        return redact_completed_process(completed, secret_values)

    @staticmethod
    def _prompt(ticket: ModelImprovementTicket, plan: TrainingPlan) -> str:
        metrics = ", ".join(metric.name for metric in ticket.target_metrics)
        constraints = "; ".join(ticket.safety_constraints) or "default safety policy"
        return (
            "You are preparing a candidate model experiment, not deploying to production.\n"
            f"Base model: {ticket.base_model_id}\n"
            f"Verified capability gap: {ticket.problem_cluster}\n"
            f"Training method: {plan.method.value}\n"
            f"Evidence traces: {len(ticket.evidence_trace_ids)} verified traces\n"
            f"Target metrics: {metrics}\n"
            f"Regression suite: {ticket.regression_suite}\n"
            f"Safety constraints: {constraints}\n"
            f"Budget: GPU hours={ticket.budget.max_gpu_hours}, "
            f"rollouts={ticket.budget.max_rollouts}, "
            f"training tokens={ticket.budget.max_training_tokens}, "
            f"cost USD={ticket.budget.max_cost_usd}.\n"
            "Research relevant primary sources, construct and validate the dataset pipeline, "
            "write reproducible training/evaluation code, run only within the stated budget, "
            "and return a candidate artifact plus an experiment report. Never deploy or publish "
            "data, traces, model weights, or repositories without a separate approval gate."
        )


__all__ = [
    "ML_INTERN_REVIEWED_COMMIT",
    "ML_INTERN_VERSION_PATTERN",
    "MLInternCLIAdapter",
]
