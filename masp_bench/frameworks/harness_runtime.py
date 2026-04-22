"""Harness-specific runtime strategies for ClawTeam execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .harness_config_store import HarnessConfigStore
from .harness_profiles import session_name


class HarnessRuntime(Protocol):
    name: str

    def register_mcp(
        self,
        *,
        worktrees: dict[str, Path],
        mcp_port: int,
        clawteam_command: str,
    ) -> None:
        ...

    def unregister_mcp(self, *, worktrees: dict[str, Path]) -> None:
        ...

    def build_spawn_env(self, *, agent_role: str, cwd: str) -> dict[str, str]:
        ...

    def session_id(self, *, run_id: str, agent_role: str) -> str | None:
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
    ) -> str:
        ...

    def cleanup(self) -> None:
        ...


@dataclass(slots=True)
class ClaudeHarnessRuntime:
    config_store: HarnessConfigStore = field(default_factory=HarnessConfigStore)
    name: str = "claude"

    def register_mcp(
        self,
        *,
        worktrees: dict[str, Path],
        mcp_port: int,
        clawteam_command: str,
    ) -> None:
        self.config_store.register_claude(
            project_paths=list(worktrees.values()),
            mcp_url=f"http://127.0.0.1:{mcp_port}/sse",
            clawteam_command=clawteam_command,
        )

    def unregister_mcp(self, *, worktrees: dict[str, Path]) -> None:
        self.config_store.unregister_claude(project_paths=list(worktrees.values()))

    def build_spawn_env(self, *, agent_role: str, cwd: str) -> dict[str, str]:
        del agent_role, cwd
        return {}

    def session_id(self, *, run_id: str, agent_role: str) -> str | None:
        del run_id, agent_role
        return None

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
    ) -> str:
        from .jsonl_collector import collect_all_agent_traces

        return collect_all_agent_traces(
            worktree_map=worktrees,
            catalog_tool_names=catalog_names,
            all_agent_roles=all_roles,
            action_sink=action_sink,
            hub_role=hub_role,
            run_id=run_id,
            spawn_timestamp=spawn_timestamp,
        )

    def cleanup(self) -> None:
        return None


@dataclass(slots=True)
class CodexHarnessRuntime:
    config_store: HarnessConfigStore = field(default_factory=HarnessConfigStore)
    name: str = "codex"

    def register_mcp(
        self,
        *,
        worktrees: dict[str, Path],
        mcp_port: int,
        clawteam_command: str,
    ) -> None:
        del worktrees
        self.config_store.register_codex(
            mcp_url=f"http://127.0.0.1:{mcp_port}/mcp",
            clawteam_command=clawteam_command,
        )

    def unregister_mcp(self, *, worktrees: dict[str, Path]) -> None:
        del worktrees
        self.config_store.unregister_codex()

    def build_spawn_env(self, *, agent_role: str, cwd: str) -> dict[str, str]:
        del agent_role, cwd
        return {}

    def session_id(self, *, run_id: str, agent_role: str) -> str | None:
        del run_id, agent_role
        return None

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
    ) -> str:
        from .codex_ingestor import collect_codex_traces

        return collect_codex_traces(
            worktree_map=worktrees,
            catalog_tool_names=catalog_names,
            all_agent_roles=all_roles,
            action_sink=action_sink,
            hub_role=hub_role,
            run_id=run_id,
            spawn_timestamp=spawn_timestamp,
        )

    def cleanup(self) -> None:
        return None


@dataclass(slots=True)
class OpenClawHarnessRuntime:
    config_store: HarnessConfigStore = field(default_factory=HarnessConfigStore)
    name: str = "openclaw"
    _bootstrap_backup: str | None = None

    def register_mcp(
        self,
        *,
        worktrees: dict[str, Path],
        mcp_port: int,
        clawteam_command: str,
    ) -> None:
        del worktrees
        self.config_store.register_openclaw(
            mcp_url=f"http://127.0.0.1:{mcp_port}/sse",
            clawteam_command=clawteam_command,
        )

    def unregister_mcp(self, *, worktrees: dict[str, Path]) -> None:
        del worktrees
        self.config_store.unregister_openclaw()

    def build_spawn_env(self, *, agent_role: str, cwd: str) -> dict[str, str]:
        del agent_role, cwd
        bootstrap = Path.home() / ".openclaw" / "workspace" / "BOOTSTRAP.md"
        if bootstrap.exists():
            self._bootstrap_backup = bootstrap.read_text()
            bootstrap.unlink()
        return {}

    def session_id(self, *, run_id: str, agent_role: str) -> str | None:
        return session_name(run_id, agent_role, self.name)

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
    ) -> str:
        from .openclaw_ingestor import collect_openclaw_traces

        return collect_openclaw_traces(
            worktree_map=worktrees,
            catalog_tool_names=catalog_names,
            all_agent_roles=all_roles,
            action_sink=action_sink,
            hub_role=hub_role,
            run_id=run_id,
            spawn_timestamp=spawn_timestamp,
        )

    def cleanup(self) -> None:
        if self._bootstrap_backup is None:
            return
        bootstrap = Path.home() / ".openclaw" / "workspace" / "BOOTSTRAP.md"
        bootstrap.parent.mkdir(parents=True, exist_ok=True)
        bootstrap.write_text(self._bootstrap_backup)
        self._bootstrap_backup = None


def get_harness_runtime(
    name: str,
    *,
    config_store: HarnessConfigStore | None = None,
) -> HarnessRuntime:
    store = config_store or HarnessConfigStore()
    if name == "claude":
        return ClaudeHarnessRuntime(config_store=store)
    if name == "codex":
        return CodexHarnessRuntime(config_store=store)
    if name == "openclaw":
        return OpenClawHarnessRuntime(config_store=store)
    raise ValueError(f"Unknown harness runtime: {name}")
