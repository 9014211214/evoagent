from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def require(path: str, needle: str) -> None:
    text = (ROOT / path).read_text(encoding="utf-8")
    if needle not in text:
        raise SystemExit(f"required rc2 marker missing from {path}: {needle}")


def forbid(path: str, needle: str) -> None:
    text = (ROOT / path).read_text(encoding="utf-8")
    if needle in text:
        raise SystemExit(f"forbidden rc2 marker remains in {path}: {needle}")


require("pyproject.toml", 'version = "1.0.0rc2"')
require("README.md", "## Current status: v1.0.0-rc2")
require("CHANGELOG.md", "## 1.0.0rc2")
require(".github/workflows/ci.yml", "evoagent 1.0.0rc2")
require(".github/workflows/ci.yml", "examples/skill_recorder_import.py")
require(".github/workflows/ci.yml", "examples/execution_authorization.py")
forbid(".github/workflows/ci.yml", "1.0.0rc1")

require("src/evoagent/skills/models.py", "procedure_kinds")
require("src/evoagent/skills/models.py", "@model_serializer")
require("src/evoagent/integrations/skill_recorder.py", "PureWindowsPath")
require("src/evoagent/integrations/skill_recorder.py", "top-level and plan values conflict")
require("src/evoagent/integrations/skill_recorder.py", "after rendering Skill Recorder values")
require("src/evoagent/execution/authorization.py", "build_authorized_environment")
require("src/evoagent/execution/authorization.py", "_reject_secret_text")
require("src/evoagent/execution/models.py", "approved_probes")
require("src/evoagent/integrations/harbor.py", "redact_completed_process")
require("src/evoagent/integrations/harbor.py", "preflight.executable_path")
require("src/evoagent/training/ml_intern.py", "redact_completed_process")
require("src/evoagent/training/ml_intern.py", "preflight.executable_path")
forbid("src/evoagent/integrations/harbor.py", '"--jobs-dir"')

lock = json.loads((ROOT / "THIRD_PARTY_LOCK.json").read_text(encoding="utf-8"))
components = {item["name"]: item for item in lock["components"]}
recorder = components.get("Skill Recorder")
if recorder is None:
    raise SystemExit("Skill Recorder is missing from THIRD_PARTY_LOCK.json")
if recorder["reviewed_commit"] != "93b3ccf887a46d3e3b91ed856d888d399b02c6e4":
    raise SystemExit("Skill Recorder lock commit is not the reviewed 0.4.2 commit")
if recorder["source_copied"] or recorder["modified"]:
    raise SystemExit("Skill Recorder integration must remain non-vendored and unmodified")

workflow_directory = ROOT / ".github" / "workflows"
offenders = []
for path in workflow_directory.glob("*.yml"):
    text = path.read_text(encoding="utf-8")
    if "contents: write" in text or path.name.startswith("rc2-"):
        offenders.append(path.name)
if offenders:
    raise SystemExit(f"write-enabled or one-time workflows remain: {sorted(offenders)}")

print("rc2 source invariants verified")
