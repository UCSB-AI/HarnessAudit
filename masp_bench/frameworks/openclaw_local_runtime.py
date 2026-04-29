from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .harness_config_store import HarnessConfigStore
from .openclaw_bootstrap import OpenClawBootstrapManager
from .openclaw_model_registry import apply_openclaw_model_config

KEEP_OPENCLAW_ISOLATION_ENV = "MASP_KEEP_OPENCLAW_ISOLATION"
OPENCLAW_ISOLATION_ROOT_ENV = "MASP_OPENCLAW_ISOLATION_ROOT"
OPENCLAW_DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"


def _mk_isolation_root(*, prefix: str, root_env: str | None = None) -> Path:
    parent: Path | None = None
    if root_env:
        raw_parent = os.environ.get(root_env, "").strip()
        if raw_parent:
            parent = Path(raw_parent)
            parent.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix, dir=str(parent) if parent else None))


def _load_json_object(path: Path) -> dict[str, object]:
    try:
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw) if raw.strip() else {}
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


@dataclass(frozen=True)
class OpenClawLocalSession:
    root_dir: Path
    workspace: Path
    config_path: Path
    state_dir: Path
    session_path: Path
    agent_id: str
    session_id: str
    command: tuple[str, ...]
    env: dict[str, str]


class OpenClawLocalRuntime:
    """Build and clean up an isolated OpenClaw local-mode runtime."""

    def __init__(self, config_store: HarnessConfigStore | None = None) -> None:
        self._root_dir: Path | None = None
        self._config_store = config_store or HarnessConfigStore()
        self._bootstrap_manager = OpenClawBootstrapManager()

    def prepare_session(
        self,
        *,
        run_id: str,
        model: str,
        workspace: Path | None,
        task_domain: str,
        mcp_url: str,
        timeout_seconds: int,
    ) -> OpenClawLocalSession:
        # OpenClaw reads BOOTSTRAP.md on startup and can derail the task with
        # onboarding context, so hide it for the duration of this run.
        self._bootstrap_manager.suppress()
        root_dir = _mk_isolation_root(
            prefix="masp-openclaw-local-",
            root_env=OPENCLAW_ISOLATION_ROOT_ENV,
        )
        self._root_dir = root_dir

        workspace_path = workspace or (root_dir / "workspace")
        workspace_path.mkdir(parents=True, exist_ok=True)
        if workspace is None:
            self._mark_workspace_setup_completed(workspace_path)

        state_dir = root_dir / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        config_path = root_dir / "openclaw.json"

        agent_id = "sasp-bench"
        session_id = run_id
        session_path = state_dir / "agents" / agent_id / "sessions" / f"{session_id}.jsonl"

        config = _load_json_object(self._config_store.openclaw_config_path)
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
        defaults.update(
            {
                "workspace": str(workspace_path),
                "timeoutSeconds": timeout_seconds,
                "model": {"primary": model},
            }
        )
        agents_config["defaults"] = defaults
        agents_config["list"] = [
            {
                "id": agent_id,
                "name": f"SASP Bench ({task_domain})",
                "workspace": str(workspace_path),
                "model": {"primary": model},
            }
        ]
        config["agents"] = agents_config

        mcp_config = config.get("mcp")
        if not isinstance(mcp_config, dict):
            mcp_config = {}
        else:
            mcp_config = dict(mcp_config)
        servers = mcp_config.get("servers")
        if not isinstance(servers, dict):
            servers = {}
        else:
            servers = dict(servers)
        servers["masp_tools"] = {
            "url": mcp_url,
            "transport": "sse",
        }
        mcp_config["servers"] = servers
        config["mcp"] = mcp_config

        apply_openclaw_model_config(
            config,
            model,
            env=os.environ,
            openai_base_url_fallback=OPENCLAW_DEFAULT_OPENAI_BASE_URL,
            include_openai_base_url_env=True,
        )

        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

        env = dict(os.environ)
        env["OPENCLAW_CONFIG_PATH"] = str(config_path)
        env["OPENCLAW_STATE_DIR"] = str(state_dir)

        command = (
            "openclaw",
            "agent",
            "--local",
            "--agent",
            agent_id,
            "--session-id",
            session_id,
            "--json",
        )

        return OpenClawLocalSession(
            root_dir=root_dir,
            workspace=workspace_path,
            config_path=config_path,
            state_dir=state_dir,
            session_path=session_path,
            agent_id=agent_id,
            session_id=session_id,
            command=command,
            env=env,
        )

    @staticmethod
    def _mark_workspace_setup_completed(workspace_path: Path) -> None:
        state_path = workspace_path / ".openclaw" / "workspace-state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "version": 1,
            "setupCompletedAt": datetime.now(timezone.utc).isoformat(),
        }
        state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    def cleanup(self) -> None:
        try:
            if self._root_dir is not None:
                if os.environ.get(KEEP_OPENCLAW_ISOLATION_ENV) != "1":
                    shutil.rmtree(self._root_dir, ignore_errors=True)
                self._root_dir = None
        finally:
            self._bootstrap_manager.restore()
