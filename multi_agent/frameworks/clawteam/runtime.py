"""Native CLI harness runtime registry for ClawTeam execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .config_store import HarnessConfigStore


@dataclass(frozen=True)
class HarnessRunContext:
    """Shared registration context for native CLI harness runtimes."""

    run_id: str
    worktrees: dict[str, Path]
    mcp_port: int
    clawteam_command: str
    sde_mcp_only: bool = False
    catalog_names: frozenset[str] = field(default_factory=frozenset)
    model: str = ""



class HarnessRuntime(Protocol):
    name: str

    def register_mcp(
        self,
        *,
        context: HarnessRunContext,
    ) -> None:
        ...

    def unregister_mcp(self) -> None:
        ...

    def build_spawn_env(
        self,
        *,
        agent_role: str,
    ) -> dict[str, str]:
        ...

    def adjust_spawn_command(
        self,
        command: list[str],
        *,
        sde_mcp_only: bool = False,
    ) -> list[str]:
        ...

    def asset_stage_dir(self) -> Path | None:
        ...

    def keepalive_enabled(self) -> bool:
        ...

    def session_id(self, *, run_id: str, agent_role: str) -> str | None:
        ...

    def drain_targets(
        self,
        *,
        run_id: str,
        worktrees: dict[str, Path],
        hub_role: str,
    ) -> list[Path]:
        ...

    def collect_traces(
        self,
        *,
        run_id: str,
        worktrees: dict[str, Path],
        catalog_names: set[str],
        all_roles: list[str],
        action_sink: Any,
        hub_role: str,
        spawn_timestamp: float,
        sde_mcp_only: bool = False,
    ) -> str:
        ...

    def read_final_output(
        self,
        *,
        run_id: str,
        worktrees: dict[str, Path],
        hub_role: str,
        spawn_timestamp: float,
        quiet: bool = False,
    ) -> str:
        ...

    def cleanup(self) -> None:
        ...



def get_harness_runtime(
    name: str,
    *,
    config_store: HarnessConfigStore | None = None,
) -> HarnessRuntime:
    store = config_store or HarnessConfigStore()
    if name == "claude":
        from ..native.claude.runtime import ClaudeHarnessRuntime

        return ClaudeHarnessRuntime(config_store=store)
    if name == "codex":
        from ..native.codex.runtime import CodexHarnessRuntime

        return CodexHarnessRuntime(config_store=store)
    if name == "openclaw":
        from ..native.openclaw.runtime import OpenClawHarnessRuntime

        return OpenClawHarnessRuntime(config_store=store)
    raise ValueError(f"Unknown harness runtime: {name}")
