from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_harness_config_store_module():
    masp_pkg = types.ModuleType("masp_bench")
    masp_pkg.__path__ = [str(REPO_ROOT / "masp_bench")]
    sys.modules.setdefault("masp_bench", masp_pkg)

    frameworks_pkg = types.ModuleType("masp_bench.frameworks")
    frameworks_pkg.__path__ = [str(REPO_ROOT / "masp_bench" / "frameworks")]
    sys.modules.setdefault("masp_bench.frameworks", frameworks_pkg)

    spec = importlib.util.spec_from_file_location(
        "masp_bench.frameworks.harness_config_store",
        REPO_ROOT / "masp_bench" / "frameworks" / "harness_config_store.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


harness_config_store = _load_harness_config_store_module()
HarnessConfigStore = harness_config_store.HarnessConfigStore


def test_claude_config_mutation_is_lock_wrapped_and_preserves_unrelated_entries(
    tmp_path: Path,
    monkeypatch,
) -> None:
    lock_calls: list[int] = []
    monkeypatch.setattr(
        harness_config_store.fcntl,
        "flock",
        lambda handle, op: lock_calls.append(op),
    )

    claude_path = tmp_path / "claude.json"
    claude_path.write_text(
        json.dumps(
            {
                "projects": {
                    "/tmp/user-project": {
                        "mcpServers": {
                            "user_tools": {
                                "type": "sse",
                                "url": "http://user.example/sse",
                            }
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    store = HarnessConfigStore(
        claude_config_path=claude_path,
        codex_config_path=tmp_path / "config.toml",
        openclaw_config_path=tmp_path / "openclaw.json",
    )

    store.register_claude(
        project_paths=[Path("/tmp/masp-project")],
        mcp_url="http://127.0.0.1:8123/sse",
        clawteam_command="clawteam-mcp",
    )

    payload = json.loads(claude_path.read_text(encoding="utf-8"))
    assert payload["projects"]["/tmp/user-project"]["mcpServers"]["user_tools"] == {
        "type": "sse",
        "url": "http://user.example/sse",
    }
    assert payload["projects"]["/tmp/masp-project"]["mcpServers"]["masp_tools"] == {
        "type": "sse",
        "url": "http://127.0.0.1:8123/sse",
        "masp_owned": True,
    }
    assert payload["projects"]["/tmp/masp-project"]["mcpServers"]["clawteam"] == {
        "type": "stdio",
        "command": "clawteam-mcp",
        "masp_owned": True,
    }
    assert lock_calls == [
        harness_config_store.fcntl.LOCK_EX,
        harness_config_store.fcntl.LOCK_UN,
    ]


def test_codex_config_register_is_idempotent_and_cleanup_preserves_user_sections(
    tmp_path: Path,
) -> None:
    codex_path = tmp_path / "config.toml"
    codex_path.write_text(
        "\n".join(
            [
                '[settings]',
                'theme = "dark"',
                '',
                '[mcp_servers.user_tools]',
                'url = "http://user.example/mcp"',
                'default_tools_approval_mode = "manual"',
                '',
            ]
        ),
        encoding="utf-8",
    )
    store = HarnessConfigStore(
        claude_config_path=tmp_path / "claude.json",
        codex_config_path=codex_path,
        openclaw_config_path=tmp_path / "openclaw.json",
    )

    store.register_codex(
        mcp_url="http://127.0.0.1:8123/mcp",
        clawteam_command="clawteam-mcp",
    )
    first_write = codex_path.read_text(encoding="utf-8")

    store.register_codex(
        mcp_url="http://127.0.0.1:8123/mcp",
        clawteam_command="clawteam-mcp",
    )
    second_write = codex_path.read_text(encoding="utf-8")

    assert first_write == second_write
    assert '[mcp_servers.user_tools]' in second_write
    assert '[mcp_servers.masp_tools]' in second_write
    assert '[mcp_servers.clawteam]' in second_write
    assert 'masp_owned = true' in second_write

    store.unregister_codex()
    cleaned = codex_path.read_text(encoding="utf-8")

    assert '[mcp_servers.user_tools]' in cleaned
    assert '[mcp_servers.masp_tools]' not in cleaned
    assert '[mcp_servers.clawteam]' not in cleaned


def test_openclaw_cleanup_removes_only_masp_owned_servers(tmp_path: Path) -> None:
    openclaw_path = tmp_path / "openclaw.json"
    openclaw_path.write_text(
        json.dumps(
            {
                "mcp": {
                    "servers": {
                        "user_tools": {
                            "url": "http://user.example/sse",
                            "transport": "sse",
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    store = HarnessConfigStore(
        claude_config_path=tmp_path / "claude.json",
        codex_config_path=tmp_path / "config.toml",
        openclaw_config_path=openclaw_path,
    )

    store.register_openclaw(
        mcp_url="http://127.0.0.1:8123/sse",
        clawteam_command="clawteam-mcp",
    )
    registered = json.loads(openclaw_path.read_text(encoding="utf-8"))
    assert registered["mcp"]["servers"]["user_tools"] == {
        "url": "http://user.example/sse",
        "transport": "sse",
    }
    assert registered["mcp"]["servers"]["masp_tools"]["masp_owned"] is True
    assert registered["mcp"]["servers"]["clawteam"]["masp_owned"] is True

    store.unregister_openclaw()
    cleaned = json.loads(openclaw_path.read_text(encoding="utf-8"))

    assert cleaned["mcp"]["servers"] == {
        "user_tools": {
            "url": "http://user.example/sse",
            "transport": "sse",
        }
    }
