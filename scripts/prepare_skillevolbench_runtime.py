from __future__ import annotations

import argparse
import difflib
import hashlib
import json
from pathlib import Path


TARGET_PATH = "docker/agent-build/install-agent-runtime.sh"
_AWS_MIRROR_CALL = "  configure_apt_mirror\n  apt-get update\n"
_DEFAULT_MIRROR_UPDATE = (
    "  # GitHub-hosted runners are outside AWS; keep the Ubuntu base-image mirrors.\n"
    "  apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=30 update\n"
)
_APT_INSTALL = "  apt-get install -y --no-install-recommends \\\n"
_RETRYING_APT_INSTALL = (
    "  apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=30 "
    "install -y --no-install-recommends \\\n"
)
_OPENCLAW_INTERACTIVE_SETUP = (
    '    openclaw --no-color setup --workspace "$workspace_dir"\n'
)
_OPENCLAW_BASELINE_SETUP = (
    '    openclaw --no-color setup --baseline --workspace "$workspace_dir"\n'
)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def prepare_runtime_script(script_path: Path) -> tuple[str, str, str]:
    original = script_path.read_text(encoding="utf-8")
    replacements = (
        (_AWS_MIRROR_CALL, _DEFAULT_MIRROR_UPDATE),
        (_APT_INSTALL, _RETRYING_APT_INSTALL),
        (_OPENCLAW_INTERACTIVE_SETUP, _OPENCLAW_BASELINE_SETUP),
    )

    prepared = original
    for old, new in replacements:
        old_count = prepared.count(old)
        new_count = prepared.count(new)
        if old_count == 1 and new_count == 0:
            prepared = prepared.replace(old, new, 1)
        elif old_count == 0 and new_count == 1:
            continue
        else:
            raise RuntimeError(
                "Pinned SkillEvolBench runtime script does not match the "
                f"expected hosted-runtime contract: old={old_count}, new={new_count}"
            )

    script_path.write_text(prepared, encoding="utf-8")
    patch = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            prepared.splitlines(keepends=True),
            fromfile=f"a/{TARGET_PATH}",
            tofile=f"b/{TARGET_PATH}",
        )
    )
    return _sha256(original), _sha256(prepared), patch


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare a copied SkillEvolBench runtime build for hosted runners."
    )
    parser.add_argument("--script", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--patch-evidence", type=Path, required=True)
    parser.add_argument("--claude-code-version", required=True)
    parser.add_argument("--openclaw-version", required=True)
    args = parser.parse_args()

    before_sha256, after_sha256, patch = prepare_runtime_script(args.script)
    evidence = {
        "schema_version": "1",
        "target": TARGET_PATH,
        "mirror_policy": "ubuntu_base_image_defaults",
        "apt_retries": 5,
        "apt_http_timeout_seconds": 30,
        "openclaw_setup_policy": "baseline_noninteractive",
        "claude_code_version": args.claude_code_version,
        "openclaw_version": args.openclaw_version,
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
