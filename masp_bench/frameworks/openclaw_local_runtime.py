from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

OPENAI_HARDCODED_BASE_URL = "https://new.suxi.ai/v1"

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

    def __init__(self) -> None:
        self._root_dir: Path | None = None

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
        root_dir = Path(tempfile.mkdtemp(prefix="masp-openclaw-local-"))
        self._root_dir = root_dir

        workspace_path = workspace or (root_dir / "workspace")
        workspace_path.mkdir(parents=True, exist_ok=True)

        state_dir = root_dir / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        config_path = root_dir / "openclaw.json"

        agent_id = "sasp-bench"
        session_id = run_id
        session_path = state_dir / "agents" / agent_id / "sessions" / f"{session_id}.jsonl"

        config = {
            "agents": {
                "defaults": {
                    "workspace": str(workspace_path),
                    "timeoutSeconds": timeout_seconds,
                    "model": {"primary": model},
                },
                "list": [
                    {
                        "id": agent_id,
                        "name": f"SASP Bench ({task_domain})",
                        "workspace": str(workspace_path),
                        "model": {"primary": model},
                    }
                ],
            },
            "mcp": {
                "servers": {
                    "masp_tools": {
                        "url": mcp_url,
                        "transport": "sse",
                    }
                }
            },
        }

        if model.startswith("openai/"):
            model_id = model.split("/", 1)[1]
            config["models"] = {
                "providers": {
                    "openai": {
                        "baseUrl": OPENAI_HARDCODED_BASE_URL,
                        "models": [
                            {
                                "id": model_id,
                                "name": model_id,
                            }
                        ],
                    }
                }
            }


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

    def cleanup(self) -> None:
        if self._root_dir is None:
            return
        shutil.rmtree(self._root_dir, ignore_errors=True)
        self._root_dir = None
