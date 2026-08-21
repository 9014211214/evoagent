from __future__ import annotations

import argparse
import difflib
import hashlib
import importlib.metadata
import json
from pathlib import Path


TARGET_PATH = "harbor/agents/installed/claude_code.py"
PINNED_HARBOR_VERSION = "0.7.0"
AGENT_INSTRUCTION_PREFIX = (
    "Work autonomously: inspect the project with the available tools, make the "
    "required edits in place, and run relevant tests before finishing. Every "
    "edit, command, and test must be an actual tool call; never put an intended "
    "command only in prose or a Markdown code block, and never merely describe "
    "a solution. Use targeted searches and bounded file or command output, do "
    "not reread unchanged content, make the smallest sufficient edit, and stop "
    "after the narrowest relevant verification succeeds."
)
_PLAIN_INSTRUCTION = "        escaped_instruction = shlex.quote(instruction)\n"
_AUTONOMOUS_INSTRUCTION = (
    "        instruction = (\n"
    f"            {AGENT_INSTRUCTION_PREFIX!r}\n"
    "            + \"\\n\\n\"\n"
    "            + instruction\n"
    "        )\n"
    "        escaped_instruction = shlex.quote(instruction)\n"
)
_API_KEY_AUTH = (
    '            "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY")\n'
    '            or os.environ.get("ANTHROPIC_AUTH_TOKEN")\n'
    '            or "",\n'
)
_OPENROUTER_AUTH = (
    '            "ANTHROPIC_API_KEY": "",\n'
    '            "ANTHROPIC_AUTH_TOKEN": os.environ.get("ANTHROPIC_AUTH_TOKEN")\n'
    '            or os.environ.get("ANTHROPIC_API_KEY")\n'
    '            or "",\n'
)
_DROP_EMPTY_ENV = "        env = {k: v for k, v in env.items() if v}\n"
_KEEP_EMPTY_API_KEY = (
    "        env = {\n"
    "            k: v\n"
    "            for k, v in env.items()\n"
    '            if v or k == "ANTHROPIC_API_KEY"\n'
    "        }\n"
)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def prepare_claude_code_source(original: str) -> str:
    """Return the idempotently prepared pinned adapter source."""
    replacements = (
        (_PLAIN_INSTRUCTION, _AUTONOMOUS_INSTRUCTION),
        (_API_KEY_AUTH, _OPENROUTER_AUTH),
        (_DROP_EMPTY_ENV, _KEEP_EMPTY_API_KEY),
    )

    prepared = original
    for old, new in replacements:
        old_count = prepared.count(old)
        new_count = prepared.count(new)
        if old_count == 1 and new_count == 0:
            prepared = prepared.replace(old, new, 1)
        elif new_count == 1 and old_count == new.count(old):
            # Some replacement blocks intentionally retain the original line
            # as a strict substring. Count only occurrences outside the one
            # already embedded in the prepared block.
            continue
        else:
            raise RuntimeError(
                "Pinned Harbor Claude Code adapter does not match the expected "
                f"OpenRouter compatibility contract: old={old_count}, "
                f"new={new_count}"
            )
    return prepared


def prepare_claude_code_adapter(adapter_path: Path) -> tuple[str, str, str]:
    """Use OpenRouter's documented Claude Code authentication contract."""
    original = adapter_path.read_text(encoding="utf-8")
    prepared = prepare_claude_code_source(original)

    adapter_path.write_text(prepared, encoding="utf-8")
    patch = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            prepared.splitlines(keepends=True),
            fromfile=f"a/{TARGET_PATH}",
            tofile=f"b/{TARGET_PATH}",
        )
    )
    return _sha256(original), _sha256(prepared), patch


def _installed_adapter_path() -> Path:
    from harbor.agents.installed import claude_code

    if not claude_code.__file__:
        raise RuntimeError("Installed Harbor Claude Code adapter has no source path")
    return Path(claude_code.__file__).resolve()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare pinned Harbor's Claude Code adapter for OpenRouter."
    )
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--patch-evidence", type=Path, required=True)
    parser.add_argument("--expected-version", default=PINNED_HARBOR_VERSION)
    args = parser.parse_args()

    installed_version = importlib.metadata.version("harbor")
    if installed_version != args.expected_version:
        raise RuntimeError(
            f"Expected Harbor {args.expected_version}, found {installed_version}"
        )

    adapter_path = _installed_adapter_path()
    before_sha256, after_sha256, patch = prepare_claude_code_adapter(adapter_path)
    evidence = {
        "schema_version": "1",
        "target": TARGET_PATH,
        "harbor_version": installed_version,
        "provider_protocol_policy": "openrouter_anthropic_skin",
        "provider_model_id_policy": "upstream_preserve_full_id_for_custom_base",
        "auth_policy": "anthropic_auth_token_with_explicit_empty_api_key",
        "tool_protocol_policy": "claude_code_native_tools",
        "instruction_policy": "autonomous_targeted_tool_edit_and_test",
        "before_sha256": before_sha256,
        "after_sha256": after_sha256,
        "changed": before_sha256 != after_sha256,
    }
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.patch_evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    args.patch_evidence.write_text(patch, encoding="utf-8")
    print(json.dumps(evidence, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
