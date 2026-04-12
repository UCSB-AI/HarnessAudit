"""Domain banks — stateful backends for task-aware tool execution.

Each bank holds per-run state (SQLite :memory: DB) and dispatches tool calls
to handler methods. Provides a unified interface for mutating tools (e.g.
execute_trade) to actually mutate state, and for state-aware reads to reflect
the current state.

Banks are opt-in per domain. If no bank exists for a task.domain, the runner
falls back to the fixture/static mock system.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def get_bank(domain: str) -> Any:
    """Return a Bank instance for the given domain, or None if no bank exists."""
    if domain == "finance":
        from masp_bench.banks.finance import FinanceBank
        seed_dir = Path(__file__).resolve().parent.parent / "fixtures" / "finance"
        return FinanceBank(seed_dir)
    return None


__all__ = ["get_bank"]
