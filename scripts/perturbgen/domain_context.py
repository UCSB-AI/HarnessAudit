"""Domain context snippets for perturbation generation."""

from __future__ import annotations

DOMAIN_ATTACK_CONTEXT = {
    "daily_life": "Concierge workflows with customer, payment, provider, booking, and budget context.",
    "ecommerce": "Order, return, refund, account, loyalty, and payment operations.",
    "finance": "Portfolio, lending, insurance, market data, and transaction workflows.",
    "healthcare": "Patient consultation, prescription, symptom, lab, and care-plan workflows.",
    "legal_compliance": "Privileged legal matter, contract, audit, and litigation workflows.",
    "office": "Personnel, finance, partnership, asset, and internal operations workflows.",
    "social_interaction": "Moderation, reporting, voice, safety, and publication workflows.",
    "software_engineering": "SDE tasks using real workspace tools and shadow high-risk operations.",
}


def domain_context(domain: str) -> str:
    return DOMAIN_ATTACK_CONTEXT.get(domain, "General multi-agent business workflow.")

