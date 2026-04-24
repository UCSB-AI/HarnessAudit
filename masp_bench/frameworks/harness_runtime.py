"""Harness-specific runtime strategies for ClawTeam execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .harness_config_store import HarnessConfigStore
from .harness_profiles import session_name
from .openclaw_bootstrap import OpenClawBootstrapManager

CLAUDE_CODE_AGENT_TOOL = "Agent"
CLAUDE_CODE_DISALLOWED_TOOLS_FLAG = "--disallowedTools"


def _append_disallowed_tool(command: list[str], tool_name: str) -> list[str]:
    adjusted = list(command)
    for idx, value in enumerate(adjusted[:-1]):
        if value != CLAUDE_CODE_DISALLOWED_TOOLS_FLAG:
            continue

        existing = [
            item.strip()
            for item in adjusted[idx + 1].split(",")
            if item.strip()
        ]
        if tool_name not in existing:
            existing.append(tool_name)
            adjusted[idx + 1] = ",".join(existing)
        return adjusted

    adjusted.extend([CLAUDE_CODE_DISALLOWED_TOOLS_FLAG, tool_name])
    return adjusted


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

    def adjust_spawn_command(self, command: list[str]) -> list[str]:
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
    ) -> str:
        ...

    def read_final_output(
        self,
        *,
        run_id: str,
        worktrees: dict[str, Path],
        hub_role: str,
        spawn_timestamp: float,
    ) -> str:
        ...

    def cleanup(self) -> None:
        ...


@dataclass
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

    def adjust_spawn_command(self, command: list[str]) -> list[str]:
        return _append_disallowed_tool(command, CLAUDE_CODE_AGENT_TOOL)

    def keepalive_enabled(self) -> bool:
        return False

    def session_id(self, *, run_id: str, agent_role: str) -> str | None:
        del run_id, agent_role
        return None

    def drain_targets(
        self,
        *,
        run_id: str,
        worktrees: dict[str, Path],
        hub_role: str,
    ) -> list[Path]:
        del run_id, worktrees, hub_role
        return []

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

    def read_final_output(
        self,
        *,
        run_id: str,
        worktrees: dict[str, Path],
        hub_role: str,
        spawn_timestamp: float,
    ) -> str:
        del run_id, worktrees, hub_role, spawn_timestamp
        return ""

    def cleanup(self) -> None:
        return None


@dataclass
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

    def adjust_spawn_command(self, command: list[str]) -> list[str]:
        return list(command)

    def keepalive_enabled(self) -> bool:
        return False

    def session_id(self, *, run_id: str, agent_role: str) -> str | None:
        del run_id, agent_role
        return None

    def drain_targets(
        self,
        *,
        run_id: str,
        worktrees: dict[str, Path],
        hub_role: str,
    ) -> list[Path]:
        del run_id, worktrees, hub_role
        return []

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

    def read_final_output(
        self,
        *,
        run_id: str,
        worktrees: dict[str, Path],
        hub_role: str,
        spawn_timestamp: float,
    ) -> str:
        del run_id, worktrees, hub_role, spawn_timestamp
        return ""

    def cleanup(self) -> None:
        return None


@dataclass
class OpenClawHarnessRuntime:
    config_store: HarnessConfigStore = field(default_factory=HarnessConfigStore)
    _bootstrap_manager: OpenClawBootstrapManager = field(
        default_factory=OpenClawBootstrapManager,
    )
    name: str = "openclaw"

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
        self._bootstrap_manager.suppress()
        return {}

    def adjust_spawn_command(self, command: list[str]) -> list[str]:
        return list(command)

    def keepalive_enabled(self) -> bool:
        return True

    def session_id(self, *, run_id: str, agent_role: str) -> str | None:
        return session_name(run_id, agent_role, self.name)

    def drain_targets(
        self,
        *,
        run_id: str,
        worktrees: dict[str, Path],
        hub_role: str,
    ) -> list[Path]:
        del worktrees
        from .openclaw_ingestor import _OPENCLAW_SESSIONS_DIR

        sid = session_name(run_id, hub_role, self.name)
        return [_OPENCLAW_SESSIONS_DIR / f"{sid}.jsonl"]

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

    def read_final_output(
        self,
        *,
        run_id: str,
        worktrees: dict[str, Path],
        hub_role: str,
        spawn_timestamp: float,
    ) -> str:
        del worktrees
        from .openclaw_ingestor import read_openclaw_final_output

        return read_openclaw_final_output(
            run_id=run_id,
            hub_role=hub_role,
            spawn_timestamp=spawn_timestamp,
        )

    def cleanup(self) -> None:
        self._bootstrap_manager.restore()
        return None


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
