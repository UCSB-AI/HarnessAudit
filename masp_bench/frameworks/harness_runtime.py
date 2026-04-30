"""Harness-specific runtime strategies for ClawTeam execution."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .harness_config_store import MASP_OWNED_KEY, HarnessConfigStore, _parse_toml_document
from .harness_profiles import session_name
from .openclaw_model_registry import apply_openclaw_model_config
from .sde_policy import (
    SDE_CLAUDE_NATIVE_TOOLS,
    SDE_MCP_ALLOWED_TOOLS,
    SDE_OPENCLAW_NATIVE_TOOLS,
)

log = logging.getLogger(__name__)

CLAUDE_CODE_AGENT_TOOL = "Agent"
CLAUDE_CODE_ALLOWED_TOOLS_FLAG = "--allowedTools"
CLAUDE_CODE_DISALLOWED_TOOLS_FLAG = "--disallowedTools"
CLAUDE_CODE_TOOLS_FLAG = "--tools"
KEEP_CLAUDE_ISOLATION_ENV = "MASP_KEEP_CLAUDE_ISOLATION"
KEEP_CODEX_ISOLATION_ENV = "MASP_KEEP_CODEX_ISOLATION"
KEEP_OPENCLAW_ISOLATION_ENV = "MASP_KEEP_OPENCLAW_ISOLATION"
CLAUDE_ISOLATION_ROOT_ENV = "MASP_CLAUDE_ISOLATION_ROOT"
CODEX_ISOLATION_ROOT_ENV = "MASP_CODEX_ISOLATION_ROOT"
OPENCLAW_ISOLATION_ROOT_ENV = "MASP_OPENCLAW_ISOLATION_ROOT"
OPENCLAW_DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
CLAUDE_CONFIG_FILE_NAME = ".claude.json"
KIMI_OPENCLAW_NATIVE_SCHEMA_DENY_TOOLS = (
    "group:fs",
    "group:runtime",
    "group:web",
    "group:memory",
    "group:sessions",
    "group:ui",
    "group:messaging",
    "group:automation",
    "group:nodes",
    "group:agents",
    "group:media",
)


def _merge_openclaw_tool_deny(
    config: dict[str, Any],
    tool_names: tuple[str, ...] | list[str],
) -> None:
    tools_config = config.get("tools")
    if not isinstance(tools_config, dict):
        tools_config = {}
    else:
        tools_config = dict(tools_config)
    existing_deny = tools_config.get("deny")
    if isinstance(existing_deny, list):
        deny = [str(item) for item in existing_deny]
    else:
        deny = []
    for tool_name in tool_names:
        if tool_name not in deny:
            deny.append(tool_name)
    tools_config["deny"] = deny
    config["tools"] = tools_config


def _is_openclaw_kimi_model(resolved_model: Any) -> bool:
    return (
        getattr(resolved_model, "provider_id", None) == "openrouter"
        and getattr(resolved_model, "model_id", None) == "moonshotai/kimi-k2.6"
    )


def _append_comma_flag_values(
    command: list[str],
    flag: str,
    values: list[str] | tuple[str, ...],
) -> list[str]:
    adjusted = list(command)
    for idx, value in enumerate(adjusted[:-1]):
        if value != flag:
            continue

        existing = [
            item.strip()
            for item in adjusted[idx + 1].split(",")
            if item.strip()
        ]
        for item in values:
            if item not in existing:
                existing.append(item)
        adjusted[idx + 1] = ",".join(existing)
        return adjusted

    adjusted.extend([flag, ",".join(values)])
    return adjusted


def _append_disallowed_tool(command: list[str], tool_name: str) -> list[str]:
    return _append_comma_flag_values(
        command,
        CLAUDE_CODE_DISALLOWED_TOOLS_FLAG,
        [tool_name],
    )


def _safe_temp_fragment(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in value)
    return safe[:80] or "run"


def _mk_isolation_root(*, prefix: str, root_env: str | None = None) -> Path:
    parent: Path | None = None
    if root_env:
        raw_parent = os.environ.get(root_env, "").strip()
        if raw_parent:
            parent = Path(raw_parent)
            parent.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix, dir=str(parent) if parent else None))


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
        sde_mcp_only: bool = False,
        catalog_names: set[str] | None = None,
        model: str = "",
    ) -> None:
        ...

    def unregister_mcp(self, *, worktrees: dict[str, Path]) -> None:
        ...

    def build_spawn_env(
        self,
        *,
        agent_role: str,
        cwd: str,
        sde_mcp_only: bool = False,
    ) -> dict[str, str]:
        ...

    def adjust_spawn_command(
        self,
        command: list[str],
        *,
        sde_mcp_only: bool = False,
        catalog_names: set[str] | None = None,
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


@dataclass
class ClaudeHarnessRuntime:
    config_store: HarnessConfigStore = field(default_factory=HarnessConfigStore)
    _supports_tools_flag: bool | None = field(default=None, init=False)
    _root_dir: Path | None = field(default=None, init=False)
    _config_dir: Path | None = field(default=None, init=False)
    _config_path: Path | None = field(default=None, init=False)
    _alternate_config_path: Path | None = field(default=None, init=False)
    _agent_log_dir: Path | None = field(default=None, init=False)
    _run_id: str | None = field(default=None, init=False)
    name: str = "claude"

    @property
    def config_dir(self) -> Path | None:
        return self._config_dir

    def register_mcp(
        self,
        *,
        run_id: str,
        worktrees: dict[str, Path],
        mcp_port: int,
        clawteam_command: str,
        sde_mcp_only: bool = False,
        catalog_names: set[str] | None = None,
        model: str = "",
    ) -> None:
        del sde_mcp_only, catalog_names, model
        self.cleanup()

        root_dir = _mk_isolation_root(
            prefix=f"masp-claude-harness-{_safe_temp_fragment(run_id)}-",
            root_env=CLAUDE_ISOLATION_ROOT_ENV,
        )
        config_dir = root_dir / ".claude"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "projects").mkdir(parents=True, exist_ok=True)
        agent_log_dir = root_dir / "logs" / "agents"
        agent_log_dir.mkdir(parents=True, exist_ok=True)

        config_path = root_dir / CLAUDE_CONFIG_FILE_NAME
        alternate_config_path = config_dir / CLAUDE_CONFIG_FILE_NAME
        self._inherit_claude_config(config_dir, config_path, alternate_config_path)

        local_store = HarnessConfigStore(
            claude_config_path=config_path,
            codex_config_path=self.config_store.codex_config_path,
            openclaw_config_path=self.config_store.openclaw_config_path,
        )
        local_store.register_claude(
            project_paths=list(worktrees.values()),
            mcp_url=f"http://127.0.0.1:{mcp_port}/sse",
            clawteam_command=clawteam_command,
        )
        self._mirror_claude_config(config_path, alternate_config_path)

        self._root_dir = root_dir
        self._config_dir = config_dir
        self._config_path = config_path
        self._alternate_config_path = alternate_config_path
        self._agent_log_dir = agent_log_dir
        self._run_id = run_id

    def unregister_mcp(self, *, worktrees: dict[str, Path]) -> None:
        del worktrees
        return None

    def _inherit_claude_config(
        self,
        config_dir: Path,
        config_path: Path,
        alternate_config_path: Path,
    ) -> None:
        source_dir = Path.home() / ".claude"
        for name in (
            ".credentials.json",
            "settings.json",
            "settings.local.json",
            "mcp-needs-auth-cache.json",
        ):
            source = source_dir / name
            if source.is_file():
                shutil.copy2(source, config_dir / name)

        source_config = self.config_store.claude_config_path
        if source_config.is_file():
            shutil.copy2(source_config, config_path)
        else:
            config_path.write_text("{}", encoding="utf-8")
        self._mirror_claude_config(config_path, alternate_config_path)

    @staticmethod
    def _mirror_claude_config(config_path: Path, alternate_config_path: Path) -> None:
        alternate_config_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(config_path, alternate_config_path)

    def build_spawn_env(
        self,
        *,
        agent_role: str,
        cwd: str,
        sde_mcp_only: bool = False,
    ) -> dict[str, str]:
        del cwd, sde_mcp_only
        if self._config_dir is None:
            raise RuntimeError("Claude runtime must register_mcp before spawning agents")
        env = dict(os.environ)
        env["CLAUDE_CONFIG_DIR"] = str(self._config_dir)
        if self._agent_log_dir is not None and self._run_id:
            log_name = (
                f"{_safe_temp_fragment(self._run_id)}-"
                f"{_safe_temp_fragment(agent_role)}.log"
            )
            env["CLAWTEAM_SPAWN_LOG_FILE"] = str(self._agent_log_dir / log_name)
        return env

    def adjust_spawn_command(
        self,
        command: list[str],
        *,
        sde_mcp_only: bool = False,
        catalog_names: set[str] | None = None,
    ) -> list[str]:
        del catalog_names
        adjusted = _append_disallowed_tool(command, CLAUDE_CODE_AGENT_TOOL)
        if not sde_mcp_only:
            return adjusted
        adjusted = _append_comma_flag_values(
            adjusted,
            CLAUDE_CODE_ALLOWED_TOOLS_FLAG,
            SDE_MCP_ALLOWED_TOOLS,
        )
        if self._command_supports_tools_flag(command):
            adjusted = _append_comma_flag_values(
                adjusted,
                CLAUDE_CODE_TOOLS_FLAG,
                SDE_MCP_ALLOWED_TOOLS,
            )
        return _append_comma_flag_values(
            adjusted,
            CLAUDE_CODE_DISALLOWED_TOOLS_FLAG,
            SDE_CLAUDE_NATIVE_TOOLS,
        )

    def asset_stage_dir(self) -> Path | None:
        return None

    def _command_supports_tools_flag(self, command: list[str]) -> bool:
        if self._supports_tools_flag is not None:
            return self._supports_tools_flag
        exe = command[0] if command else "claude"
        try:
            result = subprocess.run(
                [exe, "--help"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            help_text = f"{result.stdout}\n{result.stderr}"
            self._supports_tools_flag = CLAUDE_CODE_TOOLS_FLAG in help_text
        except Exception:
            self._supports_tools_flag = False
        return self._supports_tools_flag

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
        sde_mcp_only: bool = False,
    ) -> str:
        from .jsonl_collector import collect_all_agent_traces

        if self._config_dir is None:
            raise RuntimeError("Claude runtime must register_mcp before collecting traces")

        return collect_all_agent_traces(
            worktree_map=worktrees,
            catalog_tool_names=catalog_names,
            all_agent_roles=all_roles,
            action_sink=action_sink,
            hub_role=hub_role,
            run_id=run_id,
            spawn_timestamp=spawn_timestamp,
            sde_mcp_only=sde_mcp_only,
            claude_config_dir=self._config_dir,
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
        if self._root_dir and os.environ.get(KEEP_CLAUDE_ISOLATION_ENV, "").strip():
            log.warning("Keeping Claude isolation dir: %s", self._root_dir)
        elif self._root_dir:
            shutil.rmtree(self._root_dir, ignore_errors=True)
        self._root_dir = None
        self._config_dir = None
        self._config_path = None
        self._alternate_config_path = None
        self._agent_log_dir = None
        self._run_id = None


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
        sde_mcp_only: bool = False,
        catalog_names: set[str] | None = None,
        model: str = "",
    ) -> None:
        del worktrees, catalog_names, model
        self.cleanup()

        root_dir = _mk_isolation_root(
            prefix=f"masp-codex-harness-{_safe_temp_fragment(run_id)}-",
            root_env=CODEX_ISOLATION_ROOT_ENV,
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
        openai_base_url = os.environ.get("OPENAI_BASE_URL")
        if openai_base_url:
            doc.upsert_preamble_value("openai_base_url", openai_base_url)
        if sde_mcp_only:
            doc.upsert_preamble_value("include_apply_patch_tool", False)
            doc.upsert_preamble_value("approval_policy", "never")
            doc.upsert_preamble_value("sandbox_mode", "read-only")
        mcp_approval_mode = "approve" if sde_mcp_only else "auto"
        doc.replace_section(
            "mcp_servers.masp_tools",
            {
                "url": f"http://127.0.0.1:{mcp_port}/mcp",
                "default_tools_approval_mode": mcp_approval_mode,
                MASP_OWNED_KEY: True,
            },
        )
        clawteam_server: dict[str, Any] = {
            "command": clawteam_command,
            "default_tools_approval_mode": mcp_approval_mode,
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

    def build_spawn_env(
        self,
        *,
        agent_role: str,
        cwd: str,
        sde_mcp_only: bool = False,
    ) -> dict[str, str]:
        del cwd, sde_mcp_only
        agent_home = self._ensure_agent_codex_home(agent_role)
        env = dict(os.environ)
        env["CODEX_HOME"] = str(agent_home)
        if not env.get("CODEX_API_KEY"):
            for source_key in ("OPENAI_API_KEY",):
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

    def adjust_spawn_command(
        self,
        command: list[str],
        *,
        sde_mcp_only: bool = False,
        catalog_names: set[str] | None = None,
    ) -> list[str]:
        del sde_mcp_only, catalog_names
        return list(command)

    def asset_stage_dir(self) -> Path | None:
        return None

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
        sde_mcp_only: bool = False,
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
            sde_mcp_only=sde_mcp_only,
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
    _agent_workspace_dir: Path | None = field(default=None, init=False)
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

    @property
    def agent_workspace_dir(self) -> Path | None:
        return self._agent_workspace_dir

    def register_mcp(
        self,
        *,
        run_id: str,
        worktrees: dict[str, Path],
        mcp_port: int,
        clawteam_command: str,
        sde_mcp_only: bool = False,
        catalog_names: set[str] | None = None,
        model: str = "",
    ) -> None:
        del worktrees, catalog_names
        self.cleanup()

        root_dir = _mk_isolation_root(
            prefix=f"masp-openclaw-harness-{_safe_temp_fragment(run_id)}-",
            root_env=OPENCLAW_ISOLATION_ROOT_ENV,
        )
        state_dir = root_dir / "state"
        sessions_dir = state_dir / "agents" / "main" / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        agent_workspace_dir = root_dir / "agent-workspace"
        agent_workspace_dir.mkdir(parents=True, exist_ok=True)
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

        agents_config = config.get("agents")
        if not isinstance(agents_config, dict):
            agents_config = {}
        else:
            agents_config = dict(agents_config)
        defaults = agents_config.get("defaults")
        if not isinstance(defaults, dict):
            defaults = {}
        else:
            defaults = dict(defaults)
        defaults["workspace"] = str(agent_workspace_dir)
        agents_config["defaults"] = defaults

        agent_list = agents_config.get("list")
        if isinstance(agent_list, list):
            rewritten_list: list[Any] = []
            for item in agent_list:
                if isinstance(item, dict):
                    rewritten_item = dict(item)
                    rewritten_item["workspace"] = str(agent_workspace_dir)
                    rewritten_list.append(rewritten_item)
                else:
                    rewritten_list.append(item)
            agents_config["list"] = rewritten_list
        config["agents"] = agents_config

        # Override the OpenClaw model when the benchmark requests a specific one.
        resolved_model = None
        if model:
            resolved_model = apply_openclaw_model_config(
                config,
                model,
                env=os.environ,
                openai_base_url_fallback=OPENCLAW_DEFAULT_OPENAI_BASE_URL,
                include_openai_base_url_env=False,
            )
        if _is_openclaw_kimi_model(resolved_model):
            # Kimi-compatible OpenAI endpoints can reject some OpenClaw native
            # tool schemas before the first model turn. Keep MASP MCP tools
            # available, but remove the native OpenClaw tool surface.
            _merge_openclaw_tool_deny(
                config,
                KIMI_OPENCLAW_NATIVE_SCHEMA_DENY_TOOLS,
            )
        if sde_mcp_only:
            _merge_openclaw_tool_deny(config, SDE_OPENCLAW_NATIVE_TOOLS)
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        self._validate_config(config_path, state_dir)

        self._root_dir = root_dir
        self._config_path = config_path
        self._state_dir = state_dir
        self._sessions_dir = sessions_dir
        self._agent_workspace_dir = agent_workspace_dir
        self._agent_log_dir = agent_log_dir
        self._run_id = run_id

    @staticmethod
    def _validate_config(config_path: Path, state_dir: Path) -> None:
        openclaw_bin = shutil.which("openclaw")
        if not openclaw_bin:
            return
        env = dict(os.environ)
        env["OPENCLAW_CONFIG_PATH"] = str(config_path)
        env["OPENCLAW_STATE_DIR"] = str(state_dir)
        result = subprocess.run(
            [openclaw_bin, "config", "validate"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env=env,
        )
        if result.returncode != 0:
            detail = "\n".join(
                part.strip()
                for part in (result.stdout, result.stderr)
                if part.strip()
            )
            raise RuntimeError(f"Generated OpenClaw config is invalid: {detail}")

    def unregister_mcp(self, *, worktrees: dict[str, Path]) -> None:
        del worktrees
        return None

    def build_spawn_env(
        self,
        *,
        agent_role: str,
        cwd: str,
        sde_mcp_only: bool = False,
    ) -> dict[str, str]:
        del cwd, sde_mcp_only
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

    def adjust_spawn_command(
        self,
        command: list[str],
        *,
        sde_mcp_only: bool = False,
        catalog_names: set[str] | None = None,
    ) -> list[str]:
        del sde_mcp_only, catalog_names
        return list(command)

    def asset_stage_dir(self) -> Path | None:
        return self._agent_workspace_dir

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
        sde_mcp_only: bool = False,
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
            sde_mcp_only=sde_mcp_only,
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
        self._agent_workspace_dir = None
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
