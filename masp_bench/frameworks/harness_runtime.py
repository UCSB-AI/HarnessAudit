"""Harness-specific runtime strategies for ClawTeam execution."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .harness_config_store import MASP_OWNED_KEY, HarnessConfigStore, _parse_toml_document
from .harness_profiles import session_name

CLAUDE_CODE_AGENT_TOOL = "Agent"
CLAUDE_CODE_DISALLOWED_TOOLS_FLAG = "--disallowedTools"
KEEP_CODEX_ISOLATION_ENV = "MASP_KEEP_CODEX_ISOLATION"
KEEP_OPENCLAW_ISOLATION_ENV = "MASP_KEEP_OPENCLAW_ISOLATION"


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


def _safe_temp_fragment(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in value)
    return safe[:80] or "run"


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _load_text_or_empty(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


class HarnessRuntime(Protocol):
    name: str

    def register_mcp(
        self,
        *,
        run_id: str,
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
        quiet: bool = False,
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
        run_id: str,
        worktrees: dict[str, Path],
        mcp_port: int,
        clawteam_command: str,
    ) -> None:
        del run_id
        self.config_store.register_claude(
            project_paths=list(worktrees.values()),
            mcp_url=f"http://127.0.0.1:{mcp_port}/sse",
            clawteam_command=clawteam_command,
        )

    def unregister_mcp(self, *, worktrees: dict[str, Path]) -> None:
        self.config_store.unregister_claude(project_paths=list(worktrees.values()))

    def build_spawn_env(self, *, agent_role: str, cwd: str) -> dict[str, str]:
        del cwd
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
        quiet: bool = False,
    ) -> str:
        del run_id, worktrees, hub_role, spawn_timestamp, quiet
        return ""

    def cleanup(self) -> None:
        return None


@dataclass
class CodexHarnessRuntime:
    config_store: HarnessConfigStore = field(default_factory=HarnessConfigStore)
    _root_dir: Path | None = field(default=None, init=False)
    _codex_home: Path | None = field(default=None, init=False)
    _config_path: Path | None = field(default=None, init=False)
    _sessions_dir: Path | None = field(default=None, init=False)
    _config_text: str | None = field(default=None, init=False)
    _agent_codex_homes: dict[str, Path] = field(default_factory=dict, init=False)
    _agent_sessions_dirs: dict[str, Path] = field(default_factory=dict, init=False)
    _agent_log_dir: Path | None = field(default=None, init=False)
    _run_id: str | None = field(default=None, init=False)
    name: str = "codex"

    @property
    def codex_home(self) -> Path | None:
        return self._codex_home

    @property
    def config_path(self) -> Path | None:
        return self._config_path

    @property
    def sessions_dir(self) -> Path | None:
        return self._sessions_dir

    @property
    def agent_codex_homes(self) -> dict[str, Path]:
        return dict(self._agent_codex_homes)

    @property
    def agent_sessions_dirs(self) -> dict[str, Path]:
        return dict(self._agent_sessions_dirs)

    def register_mcp(
        self,
        *,
        run_id: str,
        worktrees: dict[str, Path],
        mcp_port: int,
        clawteam_command: str,
    ) -> None:
        del worktrees
        self.cleanup()

        root_dir = Path(
            tempfile.mkdtemp(
                prefix=f"masp-codex-harness-{_safe_temp_fragment(run_id)}-",
            )
        )
        codex_home = root_dir / "codex_homes"
        sessions_dir = root_dir / "sessions"
        agent_log_dir = root_dir / "logs" / "agents"
        codex_home.mkdir(parents=True, exist_ok=True)
        sessions_dir.mkdir(parents=True, exist_ok=True)
        agent_log_dir.mkdir(parents=True, exist_ok=True)
        config_path = root_dir / "config.toml"

        raw_config = _load_text_or_empty(self.config_store.codex_config_path)
        doc = _parse_toml_document(raw_config)
        openai_base_url = os.environ.get("OPENAI_BASE_URL") or os.environ.get("SUXI_BASE_URL")
        if openai_base_url:
            doc.upsert_preamble_value("openai_base_url", openai_base_url)
        doc.replace_section(
            "mcp_servers.masp_tools",
            {
                "url": f"http://127.0.0.1:{mcp_port}/mcp",
                "default_tools_approval_mode": "auto",
                MASP_OWNED_KEY: True,
            },
        )
        clawteam_server: dict[str, Any] = {
            "command": clawteam_command,
            "default_tools_approval_mode": "auto",
            MASP_OWNED_KEY: True,
        }
        clawteam_env = {
            key: value
            for key in ("CLAWTEAM_DATA_DIR",)
            if (value := os.environ.get(key))
        }
        if clawteam_env:
            clawteam_server["env"] = clawteam_env
        doc.replace_section("mcp_servers.clawteam", clawteam_server)
        config_text = doc.render()
        config_path.write_text(config_text, encoding="utf-8")

        self._root_dir = root_dir
        self._codex_home = codex_home
        self._config_path = config_path
        self._sessions_dir = sessions_dir
        self._config_text = config_text
        self._agent_codex_homes = {}
        self._agent_sessions_dirs = {}
        self._agent_log_dir = agent_log_dir
        self._run_id = run_id

    def unregister_mcp(self, *, worktrees: dict[str, Path]) -> None:
        del worktrees
        return None

    def _ensure_agent_codex_home(self, agent_role: str) -> Path:
        if self._root_dir is None or self._config_text is None:
            raise RuntimeError("Codex runtime must register_mcp before spawning agents")
        if agent_role in self._agent_codex_homes:
            return self._agent_codex_homes[agent_role]

        agent_home = self._root_dir / "codex_homes" / _safe_temp_fragment(agent_role)
        agent_sessions_dir = agent_home / "sessions"
        agent_sessions_dir.mkdir(parents=True, exist_ok=True)
        (agent_home / "config.toml").write_text(self._config_text, encoding="utf-8")
        self._agent_codex_homes[agent_role] = agent_home
        self._agent_sessions_dirs[agent_role] = agent_sessions_dir
        return agent_home

    def build_spawn_env(self, *, agent_role: str, cwd: str) -> dict[str, str]:
        del cwd
        agent_home = self._ensure_agent_codex_home(agent_role)
        env = dict(os.environ)
        env["CODEX_HOME"] = str(agent_home)
        if not env.get("CODEX_API_KEY"):
            for source_key in ("OPENAI_API_KEY", "SUXI_API_KEY"):
                source_value = env.get(source_key, "").strip()
                if source_value:
                    env["CODEX_API_KEY"] = source_value
                    break
        if self._agent_log_dir is not None and self._run_id:
            log_name = (
                f"{_safe_temp_fragment(self._run_id)}-"
                f"{_safe_temp_fragment(agent_role)}.log"
            )
            env["CLAWTEAM_SPAWN_LOG_FILE"] = str(self._agent_log_dir / log_name)
        return env

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

        if self._sessions_dir is None:
            raise RuntimeError("Codex runtime must register_mcp before collecting traces")

        return collect_codex_traces(
            worktree_map=worktrees,
            catalog_tool_names=catalog_names,
            all_agent_roles=all_roles,
            action_sink=action_sink,
            hub_role=hub_role,
            run_id=run_id,
            spawn_timestamp=spawn_timestamp,
            sessions_dirs_by_role=self._agent_sessions_dirs,
        )

    def read_final_output(
        self,
        *,
        run_id: str,
        worktrees: dict[str, Path],
        hub_role: str,
        spawn_timestamp: float,
        quiet: bool = False,
    ) -> str:
        del run_id, worktrees, hub_role, spawn_timestamp, quiet
        return ""

    def cleanup(self) -> None:
        root_dir = self._root_dir
        if root_dir and os.environ.get(KEEP_CODEX_ISOLATION_ENV) != "1":
            shutil.rmtree(root_dir, ignore_errors=True)
        self._root_dir = None
        self._codex_home = None
        self._config_path = None
        self._sessions_dir = None
        self._config_text = None
        self._agent_codex_homes = {}
        self._agent_sessions_dirs = {}
        self._agent_log_dir = None
        self._run_id = None
        return None


@dataclass
class OpenClawHarnessRuntime:
    config_store: HarnessConfigStore = field(default_factory=HarnessConfigStore)
    _root_dir: Path | None = field(default=None, init=False)
    _config_path: Path | None = field(default=None, init=False)
    _state_dir: Path | None = field(default=None, init=False)
    _sessions_dir: Path | None = field(default=None, init=False)
    _agent_log_dir: Path | None = field(default=None, init=False)
    _run_id: str | None = field(default=None, init=False)
    name: str = "openclaw"

    @property
    def config_path(self) -> Path | None:
        return self._config_path

    @property
    def state_dir(self) -> Path | None:
        return self._state_dir

    @property
    def sessions_dir(self) -> Path | None:
        return self._sessions_dir

    def register_mcp(
        self,
        *,
        run_id: str,
        worktrees: dict[str, Path],
        mcp_port: int,
        clawteam_command: str,
    ) -> None:
        del worktrees
        self.cleanup()

        root_dir = Path(
            tempfile.mkdtemp(
                prefix=f"masp-openclaw-harness-{_safe_temp_fragment(run_id)}-",
            )
        )
        state_dir = root_dir / "state"
        sessions_dir = state_dir / "agents" / "main" / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        agent_log_dir = root_dir / "logs" / "agents"
        agent_log_dir.mkdir(parents=True, exist_ok=True)
        config_path = root_dir / "openclaw.json"

        managed_servers = {
            "masp_tools": {
                "url": f"http://127.0.0.1:{mcp_port}/sse",
                "transport": "sse",
                MASP_OWNED_KEY: True,
            },
            "clawteam": {
                "command": clawteam_command,
                MASP_OWNED_KEY: True,
            },
        }
        config = _load_json_object(self.config_store.openclaw_config_path)
        mcp_config = config.get("mcp")
        if not isinstance(mcp_config, dict):
            mcp_config = {}
        else:
            mcp_config = dict(mcp_config)
        mcp_config["servers"] = managed_servers
        config["mcp"] = mcp_config
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

        self._root_dir = root_dir
        self._config_path = config_path
        self._state_dir = state_dir
        self._sessions_dir = sessions_dir
        self._agent_log_dir = agent_log_dir
        self._run_id = run_id

    def unregister_mcp(self, *, worktrees: dict[str, Path]) -> None:
        del worktrees
        return None

    def build_spawn_env(self, *, agent_role: str, cwd: str) -> dict[str, str]:
        del cwd
        if self._config_path is None or self._state_dir is None:
            raise RuntimeError("OpenClaw runtime must register_mcp before spawning agents")
        env = dict(os.environ)
        env["OPENCLAW_CONFIG_PATH"] = str(self._config_path)
        env["OPENCLAW_STATE_DIR"] = str(self._state_dir)
        if self._agent_log_dir is not None and self._run_id:
            log_name = (
                f"{_safe_temp_fragment(self._run_id)}-"
                f"{_safe_temp_fragment(agent_role)}.log"
            )
            env["CLAWTEAM_SPAWN_LOG_FILE"] = str(self._agent_log_dir / log_name)
        return env

    def adjust_spawn_command(self, command: list[str]) -> list[str]:
        return list(command)

    def keepalive_enabled(self) -> bool:
        return False

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
        if self._sessions_dir is None:
            return []

        sid = session_name(run_id, hub_role, self.name)
        return [self._sessions_dir / f"{sid}.jsonl"]

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
            sessions_dir=self._sessions_dir,
        )

    def read_final_output(
        self,
        *,
        run_id: str,
        worktrees: dict[str, Path],
        hub_role: str,
        spawn_timestamp: float,
        quiet: bool = False,
    ) -> str:
        del worktrees
        from .openclaw_ingestor import read_openclaw_final_output

        return read_openclaw_final_output(
            run_id=run_id,
            hub_role=hub_role,
            spawn_timestamp=spawn_timestamp,
            sessions_dir=self._sessions_dir,
            quiet=quiet,
        )

    def cleanup(self) -> None:
        root_dir = self._root_dir
        if root_dir and os.environ.get(KEEP_OPENCLAW_ISOLATION_ENV) != "1":
            shutil.rmtree(root_dir, ignore_errors=True)
        self._root_dir = None
        self._config_path = None
        self._state_dir = None
        self._sessions_dir = None
        self._agent_log_dir = None
        self._run_id = None
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
