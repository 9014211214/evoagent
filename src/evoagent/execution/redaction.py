from __future__ import annotations

import subprocess
from collections.abc import Iterable


_REDACTED = "[REDACTED]"


def redact_text(value: str | bytes | None, secrets: Iterable[str]) -> str | bytes | None:
    if value is None:
        return None
    secret_values = sorted(
        {item for item in secrets if item},
        key=len,
        reverse=True,
    )
    if isinstance(value, bytes):
        result = value
        for secret in secret_values:
            result = result.replace(secret.encode("utf-8"), _REDACTED.encode("utf-8"))
        return result
    result = value
    for secret in secret_values:
        result = result.replace(secret, _REDACTED)
    return result


def redact_completed_process(
    completed: subprocess.CompletedProcess[str],
    secrets: Iterable[str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=completed.args,
        returncode=completed.returncode,
        stdout=redact_text(completed.stdout, secrets),
        stderr=redact_text(completed.stderr, secrets),
    )


def redact_timeout(
    error: subprocess.TimeoutExpired,
    secrets: Iterable[str],
) -> subprocess.TimeoutExpired:
    return subprocess.TimeoutExpired(
        cmd=error.cmd,
        timeout=error.timeout,
        output=redact_text(error.output, secrets),
        stderr=redact_text(error.stderr, secrets),
    )


__all__ = ["redact_completed_process", "redact_text", "redact_timeout"]
