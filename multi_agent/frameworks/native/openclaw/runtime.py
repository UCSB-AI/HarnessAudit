"""OpenClaw runtime integration for ClawTeam execution."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...clawteam.config_store import MASP_OWNED_KEY, HarnessConfigStore
from ...clawteam.profiles import session_name
from ...clawteam.runtime import HarnessRunContext
from ...core.sde_policy import SDE_OPENCLAW_NATIVE_TOOLS
from .model_registry import apply_openclaw_model_config
from ..common import (
    _load_json_object,
    _mk_isolation_root,
    _process_output_text,
    _safe_temp_fragment,
)

log = logging.getLogger(__name__)

KEEP_OPENCLAW_ISOLATION_ENV = "MASP_KEEP_OPENCLAW_ISOLATION"
OPENCLAW_ISOLATION_ROOT_ENV = "MASP_OPENCLAW_ISOLATION_ROOT"
OPENCLAW_VALIDATE_TIMEOUT_ENV = "MASP_OPENCLAW_VALIDATE_TIMEOUT"


def _mark_openclaw_workspace_setup_completed(workspace_path: Path) -> None:
    """Prevent OpenClaw from seeding its interactive BOOTSTRAP.md workflow."""

    state_path = workspace_path / ".openclaw" / "workspace-state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "version": 1,
        "setupCompletedAt": datetime.now(timezone.utc).isoformat(),
    }
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


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
        context: HarnessRunContext,
    ) -> None:
        self.cleanup()

        root_dir = _mk_isolation_root(
            prefix=f"multi-agent-openclaw-harness-{_safe_temp_fragment(context.run_id)}-",
            root_env=OPENCLAW_ISOLATION_ROOT_ENV,
        )
        state_dir = root_dir / "state"
        sessions_dir = state_dir / "agents" / "main" / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        agent_workspace_dir = root_dir / "agent-workspace"
        agent_workspace_dir.mkdir(parents=True, exist_ok=True)
        _mark_openclaw_workspace_setup_completed(agent_workspace_dir)
        agent_log_dir = root_dir / "logs" / "agents"
        agent_log_dir.mkdir(parents=True, exist_ok=True)
        config_path = root_dir / "openclaw.json"

        managed_servers = {
            "multi_agent_tools": {
                "url": f"http://127.0.0.1:{context.mcp_port}/sse",
                "transport": "sse",
                MASP_OWNED_KEY: True,
            },
            "clawteam": {
                "command": context.clawteam_command,
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
        defaults["skipBootstrap"] = True
        agents_config["defaults"] = defaults

        agent_list = agents_config.get("list")
        if isinstance(agent_list, list):
            rewritten_list: list[Any] = []
            for item in agent_list:
                if isinstance(item, dict):
                    rewritten_item = dict(item)
                    rewritten_item["workspace"] = str(agent_workspace_dir)
                    rewritten_item["skipBootstrap"] = True
                    rewritten_list.append(rewritten_item)
                else:
                    rewritten_list.append(item)
            agents_config["list"] = rewritten_list
        config["agents"] = agents_config

        # Override the OpenClaw model when the benchmark requests a specific one.
        if context.model:
            apply_openclaw_model_config(
                config,
                context.model,
                env=os.environ,
            )
        if context.sde_mcp_only:
            _merge_openclaw_tool_deny(config, SDE_OPENCLAW_NATIVE_TOOLS)
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        self._validate_config(config_path, state_dir)

        self._root_dir = root_dir
        self._config_path = config_path
        self._state_dir = state_dir
        self._sessions_dir = sessions_dir
        self._agent_workspace_dir = agent_workspace_dir
        self._agent_log_dir = agent_log_dir
        self._run_id = context.run_id

    @staticmethod
    def _validate_config(config_path: Path, state_dir: Path) -> None:
        openclaw_bin = shutil.which("openclaw")
        if not openclaw_bin:
            return
        env = dict(os.environ)
        env["OPENCLAW_CONFIG_PATH"] = str(config_path)
        env["OPENCLAW_STATE_DIR"] = str(state_dir)
        timeout_s = float(os.environ.get(OPENCLAW_VALIDATE_TIMEOUT_ENV, "60"))
        try:
            result = subprocess.run(
                [openclaw_bin, "config", "validate"],
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            detail = _process_output_text(exc.stdout, exc.stderr)
            suffix = f"config={config_path} state_dir={state_dir}"
            if detail:
                suffix = f"{suffix}\n{detail}"
            raise RuntimeError(
                "Generated OpenClaw config validation timed out "
                f"after {timeout_s:.1f}s: {suffix}"
            ) from exc
        if result.returncode != 0:
            detail = _process_output_text(result.stdout, result.stderr)
            suffix = f"config={config_path} state_dir={state_dir}"
            if detail:
                suffix = f"{suffix}\n{detail}"
            raise RuntimeError(f"Generated OpenClaw config is invalid: {suffix}")

    def unregister_mcp(self) -> None:
        return None

    def build_spawn_env(
        self,
        *,
        agent_role: str,
    ) -> dict[str, str]:
        if (
            self._config_path is None
            or self._state_dir is None
            or self._sessions_dir is None
        ):
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
    ) -> list[str]:
        del sde_mcp_only
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
        from .ingestor import openclaw_session_path

        sid = session_name(run_id, hub_role, self.name)
        return [openclaw_session_path(sid, sessions_dir=self._sessions_dir)]

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
        from .ingestor import collect_openclaw_traces

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
        from .ingestor import read_openclaw_final_output

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
