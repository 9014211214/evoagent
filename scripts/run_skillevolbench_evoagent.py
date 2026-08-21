from __future__ import annotations

import argparse
import importlib.metadata
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from evoagent.benchmarks import install_skillevolbench_strategy_patch


PINNED_SKILLEVOLBENCH_COMMIT = "9e3daa339987c3cfa624121e1be442593a53d43c"
PINNED_SKILLEVOLBENCH_HARBOR_VERSION = "0.7.0"
SUPPORTED_PROVIDER_MARKERS = (
    "AZURE_OPENAI_API_KEY",
    "AWS_BEARER_TOKEN_BEDROCK",
    "GEMINI_API_KEY",
    "KIMI_BEDROCK_API_KEY",
    "OPENROUTER_API_KEY",
)
_CONDITION_BASELINES = {
    "no_skill": "no_skill",
    "evoagent": "selfgen_experience_always",
}
_CLAUDE_CODE_BOUNDED_ENV = (
    "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
    "CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS",
    "CLAUDE_CODE_MAX_CONTEXT_TOKENS",
    "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE",
)
_CLAUDE_CODE_POLICY_KWARGS = {
    "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "evoagent_max_output_tokens",
    "CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS": (
        "evoagent_file_read_max_output_tokens"
    ),
    "CLAUDE_CODE_MAX_CONTEXT_TOKENS": "evoagent_max_context_tokens",
    "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "evoagent_autocompact_pct_override",
}


def _git_output(checkout: Path, *args: str) -> str:
    process = subprocess.run(
        ["git", "-C", str(checkout), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if process.returncode != 0:
        raise RuntimeError(process.stderr.strip() or "git command failed")
    return process.stdout.strip()


def _assert_harbor_runtime_contract(
    *,
    installed_version: str,
    trial_type: type[Any],
) -> None:
    """Fail before execution when the pinned benchmark/Harbor API has drifted."""
    if installed_version != PINNED_SKILLEVOLBENCH_HARBOR_VERSION:
        raise RuntimeError(
            "SkillEvolBench requires its benchmark-specific Harbor pin: "
            f"expected {PINNED_SKILLEVOLBENCH_HARBOR_VERSION}, "
            f"found {installed_version}."
        )
    if not callable(getattr(trial_type, "_execute_agent", None)):
        raise RuntimeError(
            "The installed Harbor Trial API is incompatible with the pinned "
            "SkillEvolBench runtime patch: Trial._execute_agent is absent."
        )


def _verify_harbor_runtime_contract() -> None:
    from harbor.trial.trial import Trial

    _assert_harbor_runtime_contract(
        installed_version=importlib.metadata.version("harbor"),
        trial_type=Trial,
    )


def _preflight(checkout: Path, *, require_external_runtime: bool) -> None:
    if not checkout.is_dir():
        raise RuntimeError(f"SkillEvolBench checkout does not exist: {checkout}")
    head = _git_output(checkout, "rev-parse", "HEAD")
    if head != PINNED_SKILLEVOLBENCH_COMMIT:
        raise RuntimeError(
            "SkillEvolBench checkout is not at the pinned release commit: "
            f"expected {PINNED_SKILLEVOLBENCH_COMMIT}, found {head}."
        )
    status = _git_output(checkout, "status", "--porcelain")
    if status:
        raise RuntimeError(
            "SkillEvolBench checkout must be clean so benchmark assets and "
            "runtime code cannot drift during the release experiment."
        )

    if not require_external_runtime:
        return
    if shutil.which("docker") is None:
        raise RuntimeError("Docker is required for a real SkillEvolBench run.")
    if shutil.which("git") is None:
        raise RuntimeError("git is required for pinned benchmark verification.")
    _verify_harbor_runtime_contract()
    if not any(os.getenv(name) for name in SUPPORTED_PROVIDER_MARKERS):
        raise RuntimeError(
            "No supported SkillEvolBench model-provider credential is present. "
            "Provide credentials outside this repository; never commit them."
        )


def _install_run_config_overrides(
    upstream_run: Any,
    *,
    max_tasks: int | None,
) -> Callable[[argparse.Namespace], Any]:
    """Apply bounded execution controls without editing the pinned checkout.

    The model preset is applied inside the wrapped upstream function. Reading
    the turn limit afterward lets us persist the same non-secret value into
    Harbor AgentConfig kwargs as well as its process environment. This makes
    no_skill/EvoAgent control equality independently auditable in config.json.

    Harbor 0.7.0 always supplies ``extra_env`` from ``AgentConfig.env``. It
    therefore cannot also appear in ``AgentConfig.kwargs``: the factory would
    pass the keyword twice before an agent is created. The bounded policy uses
    dedicated, bridge-owned kwargs which are registered as Harbor ``EnvVar``
    descriptors by :func:`_install_claude_code_policy_descriptors`.
    """
    original = upstream_run._build_run_config

    def build_run_config(args: argparse.Namespace):
        config = original(args)
        updates: dict[str, Any] = {}
        if max_tasks is not None:
            updates["max_tasks"] = max_tasks

        if config.baseline.harbor_agent_name == "claude-code":
            agent_kwargs = dict(config.baseline.agent_kwargs)
            policy_changed = False

            if "extra_env" in agent_kwargs:
                raise RuntimeError(
                    "Harbor 0.7.0 reserves extra_env for AgentConfig.env; "
                    "putting it in agent_kwargs would pass the keyword twice"
                )

            raw_max_turns = os.environ.get("CLAUDE_CODE_MAX_TURNS")
            if raw_max_turns:
                try:
                    max_turns = int(raw_max_turns)
                except ValueError as exc:
                    raise RuntimeError(
                        "CLAUDE_CODE_MAX_TURNS must be a positive integer"
                    ) from exc
                if max_turns < 1:
                    raise RuntimeError(
                        "CLAUDE_CODE_MAX_TURNS must be a positive integer"
                    )
                agent_kwargs["max_turns"] = max_turns
                policy_changed = True

            for env_name in _CLAUDE_CODE_BOUNDED_ENV:
                raw_value = os.environ.get(env_name)
                if not raw_value:
                    continue
                try:
                    value = int(raw_value)
                except ValueError as exc:
                    raise RuntimeError(
                        f"{env_name} must be a positive integer"
                    ) from exc
                if value < 1:
                    raise RuntimeError(
                        f"{env_name} must be a positive integer"
                    )
                if env_name == "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE" and value > 100:
                    raise RuntimeError(
                        "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE must be between 1 and 100"
                    )
                agent_kwargs[_CLAUDE_CODE_POLICY_KWARGS[env_name]] = value
                policy_changed = True

            if policy_changed:
                updates["baseline"] = config.baseline.model_copy(
                    update={"agent_kwargs": agent_kwargs}
                )

        if not updates:
            return config
        return config.model_copy(update=updates)

    upstream_run._build_run_config = build_run_config
    return original


def _install_claude_code_policy_descriptors(
    agent_type: type[Any],
    env_var_type: type[Any],
) -> list[Any]:
    """Teach the pinned Harbor adapter how bridge policy kwargs map to env.

    The returned list is the exact original class attribute and must be
    restored after the upstream run. Re-installation is idempotent so tests
    and defensive callers cannot append duplicate descriptors.
    """

    original = list(agent_type.ENV_VARS)
    by_kwarg = {
        getattr(descriptor, "kwarg", None): descriptor for descriptor in original
    }
    patched = list(original)
    for env_name, kwarg_name in _CLAUDE_CODE_POLICY_KWARGS.items():
        existing = by_kwarg.get(kwarg_name)
        if existing is not None:
            if getattr(existing, "env", None) != env_name:
                raise RuntimeError(
                    f"Harbor descriptor collision for {kwarg_name}: "
                    f"expected {env_name}, found {getattr(existing, 'env', None)}"
                )
            continue
        patched.append(env_var_type(kwarg_name, env=env_name, type="int"))
    agent_type.ENV_VARS = patched
    return original


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run a separately obtained pinned SkillEvolBench checkout as "
            "either the no-skill control or the EvoAgent unique-attribution "
            "Skill-evolution condition."
        )
    )
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument(
        "--condition",
        choices=tuple(_CONDITION_BASELINES),
        default="evoagent",
    )
    parser.add_argument(
        "--baseline-name",
        default=None,
        help="Compatibility override; it must match the selected condition.",
    )
    parser.add_argument("--model-yaml", type=Path, required=True)
    parser.add_argument("--order-seed", default="A", choices=("A", "B", "C"))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--workspace-root", default="workspace/runs")
    parser.add_argument(
        "--max-tasks",
        type=int,
        default=None,
        help=(
            "Partial smoke-only task cap. Omit for a publishable full run; "
            "partial results must never be reported as benchmark scores."
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    if args.max_tasks is not None and args.max_tasks < 1:
        parser.error("--max-tasks must be at least 1")
    expected_baseline = _CONDITION_BASELINES[args.condition]
    baseline_name = args.baseline_name or expected_baseline
    if baseline_name != expected_baseline:
        parser.error(
            f"condition {args.condition!r} requires baseline "
            f"{expected_baseline!r}, not {baseline_name!r}"
        )

    checkout = args.checkout.expanduser().resolve()
    _preflight(checkout, require_external_runtime=not args.dry_run)

    # Upstream source and assets remain in the separately obtained checkout.
    # The model preset may live in this repository so the upstream tree stays
    # byte-for-byte clean and pinned.
    model_yaml = args.model_yaml.expanduser().resolve()
    if not model_yaml.is_file():
        raise RuntimeError(f"model preset does not exist: {model_yaml}")

    sys.path.insert(0, str(checkout))
    old_cwd = Path.cwd()
    upstream_run = None
    original_build_run_config = None
    claude_code_agent_type = None
    original_claude_code_env_vars = None
    try:
        os.chdir(checkout)
        if args.condition == "evoagent":
            install_skillevolbench_strategy_patch()
        from scripts import run as upstream_run

        if not args.dry_run:
            from agents_port.preinstalled import ClaudeCodePreinstalled
            from harbor.agents.installed.base import EnvVar

            claude_code_agent_type = ClaudeCodePreinstalled
            original_claude_code_env_vars = _install_claude_code_policy_descriptors(
                ClaudeCodePreinstalled,
                EnvVar,
            )

        original_build_run_config = _install_run_config_overrides(
            upstream_run,
            max_tasks=args.max_tasks,
        )
        upstream_args = [
            "--baseline-name",
            baseline_name,
            "--model-yaml",
            str(model_yaml),
            "--order-seed",
            args.order_seed,
            "--run-id",
            args.run_id,
            "--workspace-root",
            args.workspace_root,
        ]
        if args.dry_run:
            upstream_args.append("--dry-run")
        if args.verbose:
            upstream_args.append("--verbose")
        return int(upstream_run.main(upstream_args))
    finally:
        if upstream_run is not None and original_build_run_config is not None:
            upstream_run._build_run_config = original_build_run_config
        if (
            claude_code_agent_type is not None
            and original_claude_code_env_vars is not None
        ):
            claude_code_agent_type.ENV_VARS = original_claude_code_env_vars
        os.chdir(old_cwd)


if __name__ == "__main__":
    raise SystemExit(main())
