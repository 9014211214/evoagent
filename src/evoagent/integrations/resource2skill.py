from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict

from evoagent.execution import (
    ExecutionAdapter,
    ExecutionAuthorization,
    ExecutionAuthorizationManager,
    ExecutionBudget,
    ExecutionInvocation,
    SQLiteExecutionUseStore,
    build_authorized_environment,
)


RESOURCE2SKILL_REPOSITORY = "https://github.com/microsoft/Resource2Skill"


class Resource2SkillCheckoutSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    repository_url: str
    checkout_path: str
    domain: str
    validation_command: tuple[str, ...]
    skills_wiki_path: str
    skills_library_path: str
    execution_enabled: bool = False


class Resource2SkillAdapter:
    """Optional local-checkout adapter for Microsoft's MIT-licensed Resource2Skill.

    The adapter neither clones nor copies upstream code. It only describes and,
    when explicitly enabled, validates an existing external checkout.
    """

    def __init__(self, *, python_binary: str = "python", execution_enabled: bool = False):
        self.python_binary = python_binary
        self.execution_enabled = execution_enabled

    def build_spec(self, *, checkout_path: str, domain: str) -> Resource2SkillCheckoutSpec:
        domain_path = PurePosixPath(domain)
        if (
            not domain.strip()
            or domain_path.is_absolute()
            or "\\" in domain
            or any(part in {"", ".", ".."} for part in domain_path.parts)
        ):
            raise ValueError("Resource2Skill domain must be a safe relative POSIX path.")
        checkout = Path(checkout_path).expanduser().resolve()
        wiki = checkout / "skills_wiki" / domain
        library = checkout / "skills_library" / domain
        command = (
            self.python_binary,
            "cli.py",
            "validate-domain",
            "--domain",
            domain,
        )
        return Resource2SkillCheckoutSpec(
            repository_url=RESOURCE2SKILL_REPOSITORY,
            checkout_path=str(checkout),
            domain=domain,
            validation_command=command,
            skills_wiki_path=str(wiki),
            skills_library_path=str(library),
            execution_enabled=self.execution_enabled,
        )

    def execute_validation(
        self,
        spec: Resource2SkillCheckoutSpec,
        *,
        timeout_seconds: int = 600,
        authorization: ExecutionAuthorization | None = None,
        authorization_manager: ExecutionAuthorizationManager | None = None,
        use_store: SQLiteExecutionUseStore | None = None,
        now=None,
    ) -> subprocess.CompletedProcess[str]:
        if not self.execution_enabled or not spec.execution_enabled:
            raise PermissionError(
                "Resource2Skill execution is disabled; use the generated checkout spec."
            )
        if authorization is None or authorization_manager is None or use_store is None:
            raise PermissionError(
                "Resource2Skill execution requires an external authorization, "
                "preflight manager, and one-use ledger."
            )
        checkout = Path(spec.checkout_path)
        cli = checkout / "cli.py"
        if (
            checkout.is_symlink()
            or not checkout.is_dir()
            or cli.is_symlink()
            or not cli.is_file()
        ):
            raise FileNotFoundError(
                "Resource2Skill checkout must be a regular external directory containing "
                "a regular non-symlink cli.py."
            )
        invocation = self.to_execution_invocation(
            spec,
            timeout_seconds=timeout_seconds,
        )
        preflight = authorization_manager.preflight(
            authorization,
            invocation,
            environment={},
            now=now,
        )
        use_store.claim(authorization, preflight, now=now)
        environment = build_authorized_environment(invocation, {})
        try:
            completed = subprocess.run(
                [preflight.executable_path, *spec.validation_command[1:]],
                cwd=checkout,
                env=environment,
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            use_store.complete(
                authorization.authorization_hash,
                return_code=124,
                now=now,
            )
            raise
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
        return completed

    @staticmethod
    def to_execution_invocation(
        spec: Resource2SkillCheckoutSpec,
        *,
        timeout_seconds: int = 600,
    ) -> ExecutionInvocation:
        return ExecutionInvocation(
            adapter=ExecutionAdapter.RESOURCE2SKILL,
            command=spec.validation_command,
            workspace=spec.checkout_path,
            network_access=False,
            upload=False,
            public=False,
            training=False,
            workspace_must_be_empty=False,
            budget=ExecutionBudget(max_wall_seconds=timeout_seconds),
            version_arguments=("--version",),
            expected_version_pattern=r"^Python \d+\.\d+(?:\.\d+)?",
        )

    @staticmethod
    def discover_output_files(spec: Resource2SkillCheckoutSpec) -> tuple[str, ...]:
        paths: list[str] = []
        for root in (Path(spec.skills_wiki_path), Path(spec.skills_library_path)):
            if root.is_dir():
                paths.extend(str(path) for path in sorted(root.rglob("*.md")) if path.is_file())
        return tuple(paths)
