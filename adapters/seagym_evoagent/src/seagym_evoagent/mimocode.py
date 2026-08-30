"""Pinned MiMoCode artifact and exact OpenRouter runtime configuration."""

from __future__ import annotations

import re
from typing import Any

from .models import HARBOR_MODEL_ID, UPDATE_MODEL_ID
from .routing import expected_route_contract, validate_route_contract


MIMOCODE_VERSION = "0.1.13"
MIMOCODE_ARCHIVE_URL = (
    "https://github.com/XiaomiMiMo/MiMo-Code/releases/download/"
    "v0.1.13/mimocode-linux-x64.tar.gz"
)
MIMOCODE_ARCHIVE_SHA256 = "0997a43647a99969d0194fad71af1fd6112aa8220e24a4562aea63953b1e1ada"
MIMOCODE_ARCHIVE_ENV = "EVOAGENT_MIMOCODE_ARCHIVE_PATH"
MIMOCODE_PROXY_BASE_URL = "http://evoagent-openrouter-proxy:18765/api/v1"
MIMOCODE_SESSION_TITLE = "evoagent-seagym-trial"
SEAGYM_COMMIT = "9e61e14db1f1355de944cd7c5b10c244fc74e82d"
HARBOR_RUNTIME_COMMIT = "f7110f1a240c6a50589b90c4d69714763946d088"


def locked_mimocode_config(
    route_contract: dict[str, Any] | None = None,
    *,
    max_iterations: int,
) -> dict[str, Any]:
    """Return a secret-free config; MiMoCode expands the env placeholder in memory."""

    contract = validate_route_contract(route_contract or expected_route_contract())
    if isinstance(max_iterations, bool) or not isinstance(max_iterations, int) or not 1 <= max_iterations <= 32:
        raise ValueError("max_iterations must be an integer in [1, 32]")
    model = {
        "id": UPDATE_MODEL_ID,
        "name": "Xiaomi MiMo V2.5 (frozen OpenRouter route)",
        "reasoning": False,
        "tool_call": True,
        "provider": {
            "npm": "@openrouter/ai-sdk-provider",
            "api": UPDATE_MODEL_ID,
        },
        "options": {
            "provider": contract["provider"],
            "reasoning": contract["reasoning"],
        },
    }
    return {
        "$schema": "https://mimo.xiaomi.com/mimocode/config.json",
        "enabled_providers": ["openrouter"],
        "model": HARBOR_MODEL_ID,
        "small_model": HARBOR_MODEL_ID,
        "provider": {
            "openrouter": {
                "npm": "@openrouter/ai-sdk-provider",
                "env": ["OPENROUTER_API_KEY"],
                "only_configured_models": True,
                "options": {
                    "apiKey": "{env:OPENROUTER_API_KEY}",
                    "baseURL": MIMOCODE_PROXY_BASE_URL,
                },
                "models": {UPDATE_MODEL_ID: model},
            }
        },
        "compaction": {"auto": True, "prune": True},
        "memory": {"disable_write": True},
        "dream": {"auto": False},
        "distill": {"auto": False},
        "mcp": {},
        "permission": {
            "actor": "deny",
            "cron": "deny",
            "mcp_sampling": "deny",
            "mcp_tool_search": "deny",
        },
        # Only root-session calls may reach the proxy. Compaction remains in the
        # root event stream; detached/system model paths are disabled instead of
        # being excluded from strict proxy-to-ATIF request accounting.
        "experimental": {"predict_next_prompt": False},
        "agent": {
            "build": {
                "permission": {"actor": "deny"},
                "steps": max_iterations,
                "tool_allowlist": ["bash", "read", "write", "edit", "glob", "grep"],
            },
            "checkpoint-writer": {"disable": True},
            "distill": {"disable": True},
            "dream": {"disable": True},
            "max": {"disable": True},
            "orchestrator": {"disable": True},
            "summary": {"disable": True},
            "title": {"disable": True},
        },
    }


def install_command() -> str:
    """Install an already-uploaded frozen archive inside the Harbor sandbox."""

    return " && ".join(
        (
            "command -v tar >/dev/null",
            "command -v sha256sum >/dev/null",
            "command -v grep >/dev/null",
            "command -v install >/dev/null",
            "command -v timeout >/dev/null",
            "if ! command -v python3 >/dev/null; then command -v apt-get >/dev/null && apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends python3-minimal ca-certificates && rm -rf /var/lib/apt/lists/*; fi",
            "command -v python3 >/dev/null",
            "grep -qwi avx2 /proc/cpuinfo",
            "python3 -c \"import socket,struct; rows=[line.split() for line in open('/proc/net/route', encoding='ascii') if line.strip()]; gateways=[row[2] for row in rows[1:] if len(row)>2 and row[1]=='00000000']; assert len(gateways)==1; address=socket.inet_ntoa(struct.pack('<I', int(gateways[0],16))); open('/etc/hosts','a',encoding='ascii').write(address+' evoagent-openrouter-proxy\\n')\"",
            f"test \"$(sha256sum /tmp/evoagent-mimocode-install/archive.tar.gz | cut -d' ' -f1)\" = '{MIMOCODE_ARCHIVE_SHA256}'",
            "tar -xzf /tmp/evoagent-mimocode-install/archive.tar.gz -C /tmp/evoagent-mimocode-install",
            "test -f /tmp/evoagent-mimocode-install/mimo",
            "install -m 0755 /tmp/evoagent-mimocode-install/mimo /usr/local/bin/mimo",
            "rm -rf /tmp/evoagent-mimocode-install",
            f"test \"$(/usr/local/bin/mimo --version)\" = '{MIMOCODE_VERSION}'",
        )
    )


def runtime_env(config_path: str, home_path: str, *, proxy_token: str) -> dict[str, str]:
    if not re.fullmatch(r"evoagent-local-proxy-v1-[0-9a-f]{64}", proxy_token):
        raise ValueError("invalid run-scoped local proxy capability")
    return {
        "HOME": home_path,
        "OPENROUTER_API_KEY": proxy_token,
        "USERPROFILE": home_path,
        "MIMOCODE_CONFIG": config_path,
        "MIMOCODE_CONFIG_CONTENT": "{}",
        "MIMOCODE_HOME": home_path,
        "MIMOCODE_PURE": "1",
        "MIMOCODE_EXPERIMENTAL": "0",
        "MIMOCODE_EXPERIMENTAL_CRON": "0",
        "MIMOCODE_DISABLE_CRON": "1",
        "MIMOCODE_DISABLE_CHECKPOINT": "1",
        "MIMOCODE_EXPERIMENTAL_ORCHESTRATOR": "0",
        "MIMOCODE_EXPERIMENTAL_WORKFLOW_TOOL": "0",
        "MIMOCODE_EXPERIMENTAL_MCP_TOOL_SEARCH": "0",
        "MIMOCODE_ENABLE_EXEC_TOOL": "0",
        "MIMOCODE_DISABLE_PROVIDER_ENV": "1",
        "MIMOCODE_DISABLE_EXTERNAL_SKILLS": "1",
        "MIMOCODE_DISABLE_BUILTIN_SKILLS": "1",
        "MIMOCODE_DISABLE_CLAUDE_CODE": "1",
        "MIMOCODE_DISABLE_CLAUDE_CODE_COMMANDS": "1",
        "MIMOCODE_DISABLE_CLAUDE_CODE_ENV": "1",
        "MIMOCODE_DISABLE_CLAUDE_IMPORT": "1",
        "MIMOCODE_DISABLE_CLAUDE_CODE_MCP": "1",
        "MIMOCODE_DISABLE_CLAUDE_CODE_PROMPT": "1",
        "MIMOCODE_DISABLE_CLAUDE_CODE_SKILLS": "1",
        "MIMOCODE_DISABLE_PROJECT_CONFIG": "1",
        "MIMOCODE_AUTO_SHARE": "0",
        "MIMOCODE_DISABLE_AUTOUPDATE": "1",
        "NO_COLOR": "1",
    }
