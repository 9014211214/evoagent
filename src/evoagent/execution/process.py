from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterable


def platform_executable_argv(
    executable_path: str,
    arguments: Iterable[str],
) -> list[str]:
    """Build a shell-free argv, including Python shebang support on Windows."""

    argv = [executable_path, *arguments]
    if os.name != "nt":
        return argv

    path = Path(executable_path)
    if path.suffix.casefold() in {".exe", ".com"} or not path.is_file():
        return argv
    try:
        first_line = path.open("rb").readline(256).decode("utf-8-sig", errors="strict")
    except (OSError, UnicodeError):
        return argv
    if first_line.startswith("#!") and "python" in first_line.casefold():
        return [sys.executable, executable_path, *arguments]
    return argv


__all__ = ["platform_executable_argv"]
