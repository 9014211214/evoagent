"""Small import shims so the adapter's offline tests stay dependency-free."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # pragma: no cover - exercised only with the optional dependency installed
    from seagym.baselines.base import (  # type: ignore
        BaseBaseline,
        BaselineState,
        Checkpoint,
        UpdateResult,
    )
except ImportError:  # pragma: no cover - concrete fallback is covered indirectly
    @dataclass
    class BaselineState:
        state_dir: Path
        metadata: dict[str, Any] = field(default_factory=dict)

    @dataclass(frozen=True)
    class Checkpoint:
        checkpoint_dir: Path
        state_ref: str | None = None
        metadata: dict[str, Any] = field(default_factory=dict)

    @dataclass(frozen=True)
    class UpdateResult:
        update_index: int
        changed: bool
        status: str = "updated"
        metrics: dict[str, Any] = field(default_factory=dict)
        logs: dict[str, Any] = field(default_factory=dict)
        artifacts: dict[str, Any] = field(default_factory=dict)

    @dataclass
    class BaseBaseline:
        baseline_id: str
        state_dir: Path
        update_index: int = 0

        def __post_init__(self) -> None:
            self.state_dir = self.state_dir.resolve()


try:  # pragma: no cover - exercised only with the optional dependency installed
    from harbor.agents.base import BaseAgent  # type: ignore
except ImportError:  # pragma: no cover - concrete fallback is covered indirectly
    class BaseAgent:
        SUPPORTS_ATIF = False
        SUPPORTS_WINDOWS = False

        def __init__(
            self,
            logs_dir: Path,
            model_name: str | None = None,
            logger: Any | None = None,
            mcp_servers: list[Any] | None = None,
            skills_dir: str | None = None,
            *args: Any,
            extra_env: dict[str, str] | None = None,
            **kwargs: Any,
        ) -> None:
            del args, kwargs
            self.logs_dir = Path(logs_dir)
            self.model_name = model_name
            self.logger = logger
            self.mcp_servers = list(mcp_servers or [])
            self.skills_dir = skills_dir
            self._extra_env = dict(extra_env or {})

        @property
        def extra_env(self) -> dict[str, str]:
            return dict(self._extra_env)
