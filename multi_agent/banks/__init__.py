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
        from multi_agent.banks.finance import FinanceBank
        seed_dir = Path(__file__).resolve().parent.parent / "fixtures" / "finance"
        return FinanceBank(seed_dir)
    if domain == "ecommerce":
        from multi_agent.banks.ecommerce import EcommerceBank
        seed_dir = Path(__file__).resolve().parent.parent / "fixtures" / "ecommerce"
        return EcommerceBank(seed_dir)
    if domain == "healthcare":
        from multi_agent.banks.healthcare import HealthcareBank
        seed_dir = Path(__file__).resolve().parent.parent / "fixtures" / "healthcare"
        return HealthcareBank(seed_dir)
    if domain == "daily_life":
        from multi_agent.banks.daily_life import DailyLifeBank
        seed_dir = Path(__file__).resolve().parent.parent / "fixtures" / "daily_life"
        return DailyLifeBank(seed_dir)
    if domain == "social_interaction":
        from multi_agent.banks.social_interaction import SocialInteractionBank
        seed_dir = Path(__file__).resolve().parent.parent / "fixtures" / "social_interaction"
        return SocialInteractionBank(seed_dir)
    if domain == "legal_compliance":
        from multi_agent.banks.legal_compliance import LegalComplianceBank
        seed_dir = Path(__file__).resolve().parent.parent / "fixtures" / "legal_compliance"
        return LegalComplianceBank(seed_dir)
    if domain == "office":
        from multi_agent.banks.office import OfficeEnterpriseManagementBank
        seed_dir = (
            Path(__file__).resolve().parent.parent
            / "fixtures"
            / "office"
        )
        return OfficeEnterpriseManagementBank(seed_dir)
    return None


__all__ = ["get_bank"]
