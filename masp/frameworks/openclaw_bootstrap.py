from __future__ import annotations

from pathlib import Path


class OpenClawBootstrapManager:
    """Temporarily hide OpenClaw's BOOTSTRAP.md during a benchmark run."""

    def __init__(self) -> None:
        self._backup_content: str | None = None

    @staticmethod
    def _bootstrap_path() -> Path:
        return Path.home() / ".openclaw" / "workspace" / "BOOTSTRAP.md"

    def suppress(self) -> None:
        if self._backup_content is not None:
            return

        bootstrap = self._bootstrap_path()
        if not bootstrap.exists():
            return

        self._backup_content = bootstrap.read_text(encoding="utf-8")
        bootstrap.unlink()

    def restore(self) -> None:
        if self._backup_content is None:
            return

        bootstrap = self._bootstrap_path()
        bootstrap.parent.mkdir(parents=True, exist_ok=True)
        bootstrap.write_text(self._backup_content, encoding="utf-8")
        self._backup_content = None
