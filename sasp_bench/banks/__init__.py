"""SA bank factory — same Bank classes, SA-specific fixture paths."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _sa_fixtures_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "fixtures"


def get_bank(domain: str) -> Any:
    """Return a Bank for the given domain using SA fixture seeds."""
    seed_dir = _sa_fixtures_dir() / domain

    if domain == "finance":
        from masp_bench.banks.finance import FinanceBank
        return FinanceBank(seed_dir)
    if domain == "ecommerce":
        from masp_bench.banks.ecommerce import EcommerceBank
        return EcommerceBank(seed_dir)
    if domain == "healthcare":
        from masp_bench.banks.healthcare import HealthcareBank
        return HealthcareBank(seed_dir)
    if domain == "daily_life":
        from masp_bench.banks.daily_life import DailyLifeBank
        return DailyLifeBank(seed_dir)
    if domain == "social_interaction":
        from masp_bench.banks.social_interaction import SocialInteractionBank
        return SocialInteractionBank(seed_dir)
    if domain == "legal_compliance":
        from masp_bench.banks.legal_compliance import LegalComplianceBank
        return LegalComplianceBank(seed_dir)
    if domain == "office":
        from masp_bench.banks.office import OfficeEnterpriseManagementBank
        return OfficeEnterpriseManagementBank(seed_dir)
    return None
