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
    if domain == "ecommerce":
        from masp_bench.banks.ecommerce import EcommerceBank
        seed_dir = Path(__file__).resolve().parent.parent / "fixtures" / "ecommerce"
        return EcommerceBank(seed_dir)
    if domain == "healthcare":
        from masp_bench.banks.healthcare import HealthcareBank
        seed_dir = Path(__file__).resolve().parent.parent / "fixtures" / "healthcare"
        return HealthcareBank(seed_dir)
    if domain == "office_enterprise_management":
        from masp_bench.banks.office_enterprise_management import OfficeEnterpriseManagementBank
        seed_dir = (
            Path(__file__).resolve().parent.parent
            / "fixtures"
            / "office_enterprise_management"
        )
        return OfficeEnterpriseManagementBank(seed_dir)
    return None


__all__ = ["get_bank"]
