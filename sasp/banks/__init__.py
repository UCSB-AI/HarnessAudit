"""SA bank factory — same Bank classes, SA-specific fixture paths."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _sa_fixtures_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "fixtures"


def _fixture_domain(domain: str) -> str:
    if domain == "office":
        return "office_enterprise_management"
    return domain


def get_bank(domain: str) -> Any:
    """Return a Bank for the given domain using SA fixture seeds."""
    seed_dir = _sa_fixtures_dir() / _fixture_domain(domain)

    if domain == "finance":
        from masp.banks.finance import FinanceBank
        return FinanceBank(seed_dir)
    if domain == "ecommerce":
        from masp.banks.ecommerce import EcommerceBank
        return EcommerceBank(seed_dir)
    if domain == "healthcare":
        from masp.banks.healthcare import HealthcareBank
        return HealthcareBank(seed_dir)
    if domain == "daily_life":
        from masp.banks.daily_life import DailyLifeBank
        return DailyLifeBank(seed_dir)
    if domain == "social_interaction":
        from masp.banks.social_interaction import SocialInteractionBank
        return SocialInteractionBank(seed_dir)
    if domain == "legal_compliance":
        from masp.banks.legal_compliance import LegalComplianceBank
        return LegalComplianceBank(seed_dir)
    if domain == "office":
        from masp.banks.office import OfficeEnterpriseManagementBank
        return OfficeEnterpriseManagementBank(seed_dir)
    return None
