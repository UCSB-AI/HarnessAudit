"""Claude Code runtime integration for ClawTeam execution."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...clawteam.config_store import HarnessConfigStore
from ...clawteam.runtime import HarnessRunContext
from ...core.sde_policy import SDE_CLAUDE_NATIVE_TOOLS, SDE_MCP_ALLOWED_TOOLS
from ..common import (
    _append_comma_flag_values,
    _load_text_or_empty,
    _mk_isolation_root,
    _requested_auth_mode,
    _safe_temp_fragment,
)

log = logging.getLogger(__name__)

CLAUDE_CODE_AGENT_TOOL = "Agent"
CLAUDE_CODE_ALLOWED_TOOLS_FLAG = "--allowedTools"
CLAUDE_CODE_DISALLOWED_TOOLS_FLAG = "--disallowedTools"
CLAUDE_CODE_TOOLS_FLAG = "--tools"
CLAUDE_API_KEY_ENV = "ANTHROPIC_API_KEY"
CLAUDE_AUTH_TOKEN_ENV = "ANTHROPIC_AUTH_TOKEN"
CLAUDE_AUTH_MODE_ENV = "CLAUDE_CODE_AUTH_MODE"
KEEP_CLAUDE_ISOLATION_ENV = "MASP_KEEP_CLAUDE_ISOLATION"
CLAUDE_ISOLATION_ROOT_ENV = "MASP_CLAUDE_ISOLATION_ROOT"
CLAUDE_CONFIG_FILE_NAME = ".claude.json"


def _append_disallowed_tool(command: list[str], tool_name: str) -> list[str]:
    return _append_comma_flag_values(
        command,
        CLAUDE_CODE_DISALLOWED_TOOLS_FLAG,
        [tool_name],
    )


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
    _auth_mode: str | None = field(default=None, init=False)
    name: str = "claude"

    @property
    def config_dir(self) -> Path | None:
        return self._config_dir

    def register_mcp(
        self,
        *,
        context: HarnessRunContext,
    ) -> None:
        self.cleanup()

        root_dir = _mk_isolation_root(
            prefix=f"multi-agent-claude-harness-{_safe_temp_fragment(context.run_id)}-",
            root_env=CLAUDE_ISOLATION_ROOT_ENV,
        )
        config_dir = root_dir / ".claude"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "projects").mkdir(parents=True, exist_ok=True)
        agent_log_dir = root_dir / "logs" / "agents"
        agent_log_dir.mkdir(parents=True, exist_ok=True)

        config_path = root_dir / CLAUDE_CONFIG_FILE_NAME
        alternate_config_path = config_dir / CLAUDE_CONFIG_FILE_NAME
        requested_auth_mode = _requested_auth_mode(CLAUDE_AUTH_MODE_ENV)
        self._inherit_claude_config(
            config_dir,
            config_path,
            alternate_config_path,
            include_local_auth=requested_auth_mode != "api_key",
        )
        auth_mode = self._detect_auth_mode(
            config_dir,
            config_path,
            requested=requested_auth_mode,
        )
        if auth_mode == "missing":
            log.warning(
                "Claude harness has neither %s nor copied local Claude login state; "
                "Claude Code may prompt or fail during spawn.",
                CLAUDE_API_KEY_ENV,
            )

        local_store = HarnessConfigStore(
            claude_config_path=config_path,
            codex_config_path=self.config_store.codex_config_path,
            openclaw_config_path=self.config_store.openclaw_config_path,
        )
        # Claude Code 2.1.x may resolve hidden ClawTeam role worktrees back to
        # the user's home directory as the project root. Register both scopes
        # inside this per-run isolated config so MCP tools are visible either way.
        claude_project_paths = [Path.home(), *context.worktrees.values()]
        local_store.register_claude(
            project_paths=list(dict.fromkeys(claude_project_paths)),
            mcp_url=f"http://127.0.0.1:{context.mcp_port}/sse",
            clawteam_command=context.clawteam_command,
        )
        self._mirror_claude_config(config_path, alternate_config_path)

        self._root_dir = root_dir
        self._config_dir = config_dir
        self._config_path = config_path
        self._alternate_config_path = alternate_config_path
        self._agent_log_dir = agent_log_dir
        self._run_id = context.run_id
        self._auth_mode = auth_mode

    def unregister_mcp(self) -> None:
        return None

    def _inherit_claude_config(
        self,
        config_dir: Path,
        config_path: Path,
        alternate_config_path: Path,
        *,
        include_local_auth: bool,
    ) -> None:
        if include_local_auth:
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
        else:
            config_path.write_text("{}", encoding="utf-8")
        self._mirror_claude_config(config_path, alternate_config_path)

    @staticmethod
    def _mirror_claude_config(config_path: Path, alternate_config_path: Path) -> None:
        alternate_config_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(config_path, alternate_config_path)

    @staticmethod
    def _detect_auth_mode(config_dir: Path, config_path: Path, *, requested: str) -> str:
        api_key_available = bool(
            os.environ.get(CLAUDE_API_KEY_ENV, "").strip()
            or os.environ.get(CLAUDE_AUTH_TOKEN_ENV, "").strip()
        )
        cli_login_available = (config_dir / ".credentials.json").is_file()
        config_text = _load_text_or_empty(config_path).strip()
        cli_login_available = cli_login_available or bool(config_text and config_text != "{}")

        if requested == "api_key":
            return "api_key" if api_key_available else "missing"
        if requested == "cli_login":
            return "cli_login" if cli_login_available else "missing"
        if api_key_available:
            return "api_key"
        if cli_login_available:
            return "cli_login"
        return "missing"

    def build_spawn_env(
        self,
        *,
        agent_role: str,
    ) -> dict[str, str]:
        if self._config_dir is None:
            raise RuntimeError("Claude runtime must register_mcp before spawning agents")
        env = dict(os.environ)
        env["CLAUDE_CONFIG_DIR"] = str(self._config_dir)
        if self._auth_mode == "cli_login":
            # ClawTeam's SubprocessBackend starts from os.environ.copy() and
            # then overlays this mapping.  Removing the key here is not enough:
            # the parent-process API key would be reintroduced by the backend.
            # Use an empty value to force Claude Code to use copied local login
            # state instead of API-key billing in cli_login mode.
            env[CLAUDE_API_KEY_ENV] = ""
            env[CLAUDE_AUTH_TOKEN_ENV] = ""
        if self._auth_mode:
            env[CLAUDE_AUTH_MODE_ENV] = self._auth_mode
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
    ) -> list[str]:
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
        from .ingestor import collect_all_agent_traces

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
        self._auth_mode = None
