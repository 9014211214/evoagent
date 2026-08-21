from __future__ import annotations

import os

from evoagent.execution.models import ExecutionInvocation


_ESSENTIAL_ENVIRONMENT_NAMES = {
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "WINDIR",
    "HOME",
    "USERPROFILE",
    "TMP",
    "TEMP",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
}


class ExecutionEnvironmentError(PermissionError):
    pass


def build_authorized_environment(
    invocation: ExecutionInvocation,
    supplied: dict[str, str] | None = None,
) -> dict[str, str]:
    supplied = supplied or {}
    allowed = set(invocation.required_environment_variables)
    unexpected = sorted(set(supplied) - allowed)
    if unexpected:
        raise ExecutionEnvironmentError(
            "Execution environment contains unapproved variable names: "
            + ", ".join(unexpected)
        )

    environment = {
        name: value
        for name, value in os.environ.items()
        if name in _ESSENTIAL_ENVIRONMENT_NAMES and value
    }
    for name in invocation.required_environment_variables:
        value = supplied.get(name, os.environ.get(name, ""))
        if value:
            environment[name] = value
    return environment


__all__ = ["ExecutionEnvironmentError", "build_authorized_environment"]
