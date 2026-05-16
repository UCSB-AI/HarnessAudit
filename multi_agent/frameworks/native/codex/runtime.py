"""Codex CLI runtime integration for ClawTeam execution."""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from ...clawteam.config_store import MASP_OWNED_KEY, HarnessConfigStore, _parse_toml_document
from ...clawteam.runtime import HarnessRunContext
from ..common import (
    _load_text_or_empty,
    _mk_isolation_root,
    _requested_auth_mode,
    _safe_temp_fragment,
)

log = logging.getLogger(__name__)

CODEX_AUTH_MODE_ENV = "CODEX_AUTH_MODE"
CODEX_AUTH_STATE_NAMES = ("auth.json", "accounts")
KEEP_CODEX_ISOLATION_ENV = "MASP_KEEP_CODEX_ISOLATION"
CODEX_ISOLATION_ROOT_ENV = "MASP_CODEX_ISOLATION_ROOT"


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
    _auth_mode: str | None = field(default=None, init=False)
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
        context: HarnessRunContext,
    ) -> None:
        self.cleanup()

        root_dir = _mk_isolation_root(
            prefix=f"multi-agent-codex-harness-{_safe_temp_fragment(context.run_id)}-",
            root_env=CODEX_ISOLATION_ROOT_ENV,
        )
        codex_home = root_dir / "codex_homes"
        sessions_dir = root_dir / "sessions"
        agent_log_dir = root_dir / "logs" / "agents"
        codex_home.mkdir(parents=True, exist_ok=True)
        sessions_dir.mkdir(parents=True, exist_ok=True)
        agent_log_dir.mkdir(parents=True, exist_ok=True)
        config_path = root_dir / "config.toml"

        auth_mode = self._detect_auth_mode()
        raw_config = _load_text_or_empty(self.config_store.codex_config_path)
        doc = _parse_toml_document(raw_config)
        openai_base_url = os.environ.get("OPENAI_BASE_URL")
        if openai_base_url and auth_mode == "api_key":
            doc.upsert_preamble_value("openai_base_url", openai_base_url)
        if context.sde_mcp_only:
            doc.upsert_preamble_value("include_apply_patch_tool", False)
            doc.upsert_preamble_value("approval_policy", "never")
            doc.upsert_preamble_value("sandbox_mode", "read-only")
        mcp_approval_mode = "approve" if context.sde_mcp_only else "auto"
        doc.replace_section(
            "mcp_servers.multi_agent_tools",
            {
                "url": f"http://127.0.0.1:{context.mcp_port}/mcp",
                "default_tools_approval_mode": mcp_approval_mode,
                MASP_OWNED_KEY: True,
            },
        )
        clawteam_server: dict[str, Any] = {
            "command": context.clawteam_command,
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
        self._run_id = context.run_id
        self._auth_mode = auth_mode
        if self._auth_mode == "missing":
            log.warning(
                "Codex harness has neither OPENAI_API_KEY nor "
                "local Codex login state under %s; Codex may prompt or fail.",
                self.config_store.codex_config_path.parent,
            )

    def unregister_mcp(self) -> None:
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
        self._provision_auth_state(agent_home)
        self._agent_codex_homes[agent_role] = agent_home
        self._agent_sessions_dirs[agent_role] = agent_sessions_dir
        return agent_home

    @staticmethod
    def _api_key_auth_available(env: Mapping[str, str] | None = None) -> bool:
        env_map = env or os.environ
        return bool(str(env_map.get("OPENAI_API_KEY", "")).strip())

    def _local_auth_state_available(self) -> bool:
        source_home = self.config_store.codex_config_path.parent
        return any((source_home / name).exists() for name in CODEX_AUTH_STATE_NAMES)

    def _detect_auth_mode(self) -> str:
        requested = _requested_auth_mode(CODEX_AUTH_MODE_ENV)
        if requested == "api_key":
            return "api_key" if self._api_key_auth_available() else "missing"
        if requested == "cli_login":
            return "cli_login" if self._local_auth_state_available() else "missing"
        if self._api_key_auth_available():
            return "api_key"
        if self._local_auth_state_available():
            return "cli_login"
        return "missing"

    def _provision_auth_state(self, agent_home: Path) -> None:
        if self._auth_mode == "api_key":
            api_key = os.environ.get("OPENAI_API_KEY", "").strip()
            if not api_key:
                return
            auth_path = agent_home / "auth.json"
            auth_path.write_text(
                json.dumps(
                    {
                        "OPENAI_API_KEY": api_key,
                        "auth_mode": "apikey",
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            try:
                auth_path.chmod(0o600)
            except OSError:
                pass
            return

        if self._auth_mode != "cli_login":
            return
        source_home = self.config_store.codex_config_path.parent
        for name in CODEX_AUTH_STATE_NAMES:
            source = source_home / name
            if not source.exists():
                continue
            target = agent_home / name
            if target.exists() or target.is_symlink():
                continue
            try:
                target.symlink_to(source, target_is_directory=source.is_dir())
            except OSError:
                if source.is_dir():
                    shutil.copytree(source, target, dirs_exist_ok=True)
                else:
                    shutil.copy2(source, target)

    def build_spawn_env(
        self,
        *,
        agent_role: str,
    ) -> dict[str, str]:
        agent_home = self._ensure_agent_codex_home(agent_role)
        env = dict(os.environ)
        env["CODEX_HOME"] = str(agent_home)
        if self._auth_mode == "cli_login":
            # ClawTeam's SubprocessBackend starts from os.environ.copy() and
            # then overlays this mapping. Removing the key here is not enough:
            # the parent-process API key would be reintroduced by the backend.
            env["OPENAI_API_KEY"] = ""
        if self._auth_mode:
            env[CODEX_AUTH_MODE_ENV] = self._auth_mode
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
        del sde_mcp_only
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
        from .ingestor import collect_codex_traces

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
        self._auth_mode = None
        return None
