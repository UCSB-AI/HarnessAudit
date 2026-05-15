"""Scoped harness config mutation helpers for Claude, Codex, and OpenClaw."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MASP_OWNED_KEY = "masp_owned"
MASP_SERVER_NAMES = ("multi_agent_tools", "masp_tools", "clawteam")


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            Path(tmp_name).unlink(missing_ok=True)
        except Exception:
            pass


def _locked_json_update(path: Path, mutator: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    with open(lock_path, "a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle, fcntl.LOCK_EX)
        try:
            try:
                raw = path.read_text(encoding="utf-8")
            except OSError:
                raw = ""
            payload = json.loads(raw) if raw.strip() else {}
            mutator(payload)
            _atomic_write_text(path, json.dumps(payload, indent=2))
        finally:
            fcntl.flock(lock_handle, fcntl.LOCK_UN)


def _locked_text_update(path: Path, mutator: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    with open(lock_path, "a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle, fcntl.LOCK_EX)
        try:
            try:
                raw = path.read_text(encoding="utf-8")
            except OSError:
                raw = ""
            updated = mutator(raw)
            _atomic_write_text(path, updated)
        finally:
            fcntl.flock(lock_handle, fcntl.LOCK_UN)


def _parse_toml_value(raw_value: str) -> Any:
    value = raw_value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    if value in ("true", "false"):
        return value == "true"
    return value


@dataclass
class _TomlSection:
    header: str
    lines: list[str]

    def parsed_values(self) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for line in self.lines:
            body = line.split("#", 1)[0].strip()
            if not body or "=" not in body:
                continue
            key, raw_value = body.split("=", 1)
            values[key.strip()] = _parse_toml_value(raw_value)
        return values


@dataclass
class _TomlDocument:
    preamble_lines: list[str]
    sections: list[_TomlSection]

    def section(self, header: str) -> _TomlSection | None:
        for section in self.sections:
            if section.header == header:
                return section
        return None

    def upsert_managed_section(self, header: str, values: dict[str, Any]) -> None:
        section = self.section(header)
        rendered = _render_managed_section_lines(values)
        if section is None:
            self.sections.append(_TomlSection(header=header, lines=rendered))
            return
        if section.parsed_values().get(MASP_OWNED_KEY) is True:
            section.lines = rendered

    def replace_section(self, header: str, values: dict[str, Any]) -> None:
        section = self.section(header)
        rendered = _render_managed_section_lines(values)
        if section is None:
            self.sections.append(_TomlSection(header=header, lines=rendered))
            return
        section.lines = rendered

    def upsert_preamble_value(self, key: str, value: Any) -> None:
        rendered = f"{key} = {_render_toml_value(value)}"
        for idx, line in enumerate(self.preamble_lines):
            body = line.split("#", 1)[0].strip()
            if not body or "=" not in body:
                continue
            existing_key, _ = body.split("=", 1)
            if existing_key.strip() == key:
                self.preamble_lines[idx] = rendered
                return

        insert_at = len(self.preamble_lines)
        while insert_at > 0 and not self.preamble_lines[insert_at - 1].strip():
            insert_at -= 1
        self.preamble_lines.insert(insert_at, rendered)

    def remove_managed_sections(self, headers: set[str]) -> None:
        self.sections = [
            section
            for section in self.sections
            if not (
                section.header in headers
                and section.parsed_values().get(MASP_OWNED_KEY) is True
            )
        ]

    def render(self) -> str:
        chunks: list[str] = []
        chunks.extend(self.preamble_lines)
        if self.preamble_lines and self.preamble_lines[-1] != "":
            chunks.append("")
        for index, section in enumerate(self.sections):
            if index > 0 and chunks and chunks[-1] != "":
                chunks.append("")
            chunks.append(f"[{section.header}]")
            chunks.extend(section.lines)
        return "\n".join(chunks).rstrip() + "\n"


def _parse_toml_document(text: str) -> _TomlDocument:
    preamble_lines: list[str] = []
    sections: list[_TomlSection] = []
    current_header: str | None = None
    current_lines: list[str] = []

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("[") and stripped.endswith("]") and not stripped.startswith("[["):
            if current_header is None:
                preamble_lines = current_lines
            else:
                sections.append(_TomlSection(header=current_header, lines=current_lines))
            current_header = stripped[1:-1]
            current_lines = []
            continue
        current_lines.append(raw_line)

    if current_header is None:
        preamble_lines = current_lines
    else:
        sections.append(_TomlSection(header=current_header, lines=current_lines))

    return _TomlDocument(preamble_lines=preamble_lines, sections=sections)


def _render_toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list | tuple):
        return "[{}]".format(", ".join(_render_toml_value(item) for item in value))
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _render_managed_section_lines(values: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for key in ("url", "transport", "command", "env", "default_tools_approval_mode", MASP_OWNED_KEY):
        if key not in values:
            continue
        value = values[key]
        if key == "env" and isinstance(value, dict):
            items = ", ".join(
                f"{_render_toml_value(str(k))} = {_render_toml_value(v)}"
                for k, v in sorted(value.items())
            )
            lines.append(f"{key} = {{ {items} }}")
            continue
        lines.append(f"{key} = {_render_toml_value(value)}")
    return lines


@dataclass
class HarnessConfigStore:
    claude_config_path: Path = Path.home() / ".claude.json"
    codex_config_path: Path = Path.home() / ".codex" / "config.toml"
    openclaw_config_path: Path = Path.home() / ".openclaw" / "openclaw.json"

    def register_claude(
        self,
        *,
        project_paths: list[Path],
        mcp_url: str,
        clawteam_command: str,
    ) -> None:
        def _mutate(cfg: dict[str, Any]) -> None:
            projects = cfg.setdefault("projects", {})
            for project_path in project_paths:
                project = projects.setdefault(str(project_path), {})
                servers = project.setdefault("mcpServers", {})
                servers["multi_agent_tools"] = {
                    "type": "sse",
                    "url": mcp_url,
                    MASP_OWNED_KEY: True,
                }
                servers["clawteam"] = {
                    "type": "stdio",
                    "command": clawteam_command,
                    MASP_OWNED_KEY: True,
                }

        _locked_json_update(self.claude_config_path, _mutate)

    def unregister_claude(self, *, project_paths: list[Path]) -> None:
        def _mutate(cfg: dict[str, Any]) -> None:
            projects = cfg.get("projects", {})
            for project_path in project_paths:
                project = projects.get(str(project_path), {})
                servers = project.get("mcpServers", {})
                for name in MASP_SERVER_NAMES:
                    if isinstance(servers.get(name), dict) and servers[name].get(MASP_OWNED_KEY):
                        servers.pop(name, None)

        _locked_json_update(self.claude_config_path, _mutate)

    def register_codex(
        self,
        *,
        mcp_url: str,
        clawteam_command: str,
    ) -> None:
        def _mutate(raw: str) -> str:
            doc = _parse_toml_document(raw)
            doc.upsert_managed_section(
                "mcp_servers.multi_agent_tools",
                {
                    "url": mcp_url,
                    "default_tools_approval_mode": "auto",
                    MASP_OWNED_KEY: True,
                },
            )
            doc.upsert_managed_section(
                "mcp_servers.clawteam",
                {
                    "command": clawteam_command,
                    "default_tools_approval_mode": "auto",
                    MASP_OWNED_KEY: True,
                },
            )
            return doc.render()

        _locked_text_update(self.codex_config_path, _mutate)

    def unregister_codex(self) -> None:
        def _mutate(raw: str) -> str:
            doc = _parse_toml_document(raw)
            doc.remove_managed_sections(
                {"mcp_servers.multi_agent_tools", "mcp_servers.masp_tools", "mcp_servers.clawteam"}
            )
            return doc.render()

        _locked_text_update(self.codex_config_path, _mutate)

    def register_openclaw(
        self,
        *,
        mcp_url: str,
        clawteam_command: str,
    ) -> None:
        def _mutate(cfg: dict[str, Any]) -> None:
            servers = cfg.setdefault("mcp", {}).setdefault("servers", {})
            servers["multi_agent_tools"] = {
                "url": mcp_url,
                "transport": "sse",
                MASP_OWNED_KEY: True,
            }
            servers["clawteam"] = {
                "command": clawteam_command,
                MASP_OWNED_KEY: True,
            }

        _locked_json_update(self.openclaw_config_path, _mutate)

    def unregister_openclaw(self) -> None:
        def _mutate(cfg: dict[str, Any]) -> None:
            servers = cfg.get("mcp", {}).get("servers", {})
            for name in MASP_SERVER_NAMES:
                if isinstance(servers.get(name), dict) and servers[name].get(MASP_OWNED_KEY):
                    servers.pop(name, None)

        _locked_json_update(self.openclaw_config_path, _mutate)
