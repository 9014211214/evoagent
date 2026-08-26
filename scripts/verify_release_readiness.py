from __future__ import annotations

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {".py", ".md", ".json", ".yaml", ".yml", ".toml", ".txt", ".ini", ".cfg", ".sh"}
SECRET_PATTERNS = {
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "github_pat": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    "huggingface_token": re.compile(r"\bhf_[A-Za-z0-9]{20,}\b"),
    "generic_secret_assignment": re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\s*[:=]\s*['\"]?[A-Za-z0-9_\-/.+=]{16,}"
    ),
}
IGNORED_PARTS = {
    ".git",
    ".venv",
    "venv",
    ".wheel-venv",
    ".release-wheel-venv",
    "dist",
    "build",
    ".pytest_cache",
    "__pycache__",
}
SELF = Path(__file__).resolve()


def _candidate_paths():
    git_marker = ROOT / ".git"
    if git_marker.exists():
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(ROOT),
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        for relative in completed.stdout.split("\0"):
            if relative:
                yield ROOT / relative
        return
    yield from ROOT.rglob("*")


def iter_text_files():
    for path in _candidate_paths():
        if not path.is_file() or path.is_symlink() or path.resolve() == SELF:
            continue
        if any(part in IGNORED_PARTS for part in path.parts):
            continue
        if path.suffix.lower() in TEXT_SUFFIXES or path.name in {"Dockerfile", "LICENSE"}:
            yield path


def main() -> int:
    failures: list[str] = []
    if not (ROOT / "OPEN_SOURCE_READINESS.md").is_file():
        failures.append("OPEN_SOURCE_READINESS.md is missing")
    if not (ROOT / "LICENSE").is_file():
        failures.append("root LICENSE is missing (owner decision required before public release)")

    for path in iter_text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for label, pattern in SECRET_PATTERNS.items():
            for match in pattern.finditer(text):
                line_start = text.rfind("\n", 0, match.start()) + 1
                line_end = text.find("\n", match.end())
                if line_end == -1:
                    line_end = len(text)
                matched_line = text[line_start:line_end]
                # Synthetic credential-shaped test data is exempt only when the
                # exact matched line carries the explicit marker. A marker
                # elsewhere in the file must never suppress a real finding.
                if "synthetic-secret-fixture" in matched_line:
                    continue
                relative = path.relative_to(ROOT)
                failures.append(f"possible {label}: {relative}")
                break

    if failures:
        print("release readiness: BLOCKED")
        for item in sorted(set(failures)):
            print(f"- {item}")
        return 1

    print("release readiness: source scan passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
