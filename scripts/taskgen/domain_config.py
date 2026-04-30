"""Per-domain configuration for the task generation pipeline.

Defines target counts, category taxonomies, exemplar tasks, tool catalog
paths, fixture paths, and bank schema references for each non-SDE domain.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

MASP_ROOT = Path(__file__).resolve().parents[2] / "masp_bench"


@dataclass(frozen=True)
class DomainConfig:
    """Static configuration for a single domain."""

    domain: str
    task_prefix: str
    target_new: int
    target_multimodal_new: int
    categories: list[str]
    exemplar_task_ids: list[str]
    tools_yaml: Path
    fixtures_dir: Path
    bank_module: str
    recognizer_domain: str
    # Override if task directory name differs from domain name
    tasks_dir_name: str = ""
    # Scenario seed ideas for diversity — fed into Stage 1 prompt
    scenario_seeds: tuple[str, ...] = ()
    # Preferred worker role names for naming consistency
    role_templates: tuple[str, ...] = ()

    @property
    def tasks_dir(self) -> Path:
        return MASP_ROOT / "tasks" / (self.tasks_dir_name or self.domain)

    @property
    def existing_count(self) -> int:
        """Dynamically count existing tasks by scanning task YAML files."""
        if not self.tasks_dir.exists():
            return 0
        return sum(1 for _ in self.tasks_dir.rglob("*.yaml"))

    def _max_existing_task_num(self) -> int:
        """Find the highest task number among existing tasks (e.g., ec-t15 → 15)."""
        pattern = re.compile(rf"^{re.escape(self.task_prefix)}-t(\d+)\.yaml$")
        max_num = 0
        if not self.tasks_dir.exists():
            return 0
        for p in self.tasks_dir.rglob("*.yaml"):
            m = pattern.match(p.name)
            if m:
                max_num = max(max_num, int(m.group(1)))
        return max_num

    def next_task_id(self, offset: int) -> str:
        return f"{self.task_prefix}-t{self._max_existing_task_num() + offset + 1}"


# ---------------------------------------------------------------------------
# Domain registry
# ---------------------------------------------------------------------------

DOMAINS: dict[str, DomainConfig] = {}


def _reg(
    domain: str,
    prefix: str,
    target_new: int,
    target_mm: int,
    categories: list[str],
    exemplars: list[str],
    bank_module: str = "",
    tasks_dir_name: str = "",
    scenario_seeds: tuple[str, ...] = (),
    role_templates: tuple[str, ...] = (),
) -> None:
    DOMAINS[domain] = DomainConfig(
        domain=domain,
        task_prefix=prefix,
        target_new=target_new,
        target_multimodal_new=target_mm,
        categories=categories,
        exemplar_task_ids=exemplars,
        tools_yaml=MASP_ROOT / "tools" / f"{domain}.yaml",
        fixtures_dir=MASP_ROOT / "fixtures" / domain,
        bank_module=bank_module or f"masp_bench.banks.{domain}",
        recognizer_domain=domain,
        tasks_dir_name=tasks_dir_name,
        scenario_seeds=scenario_seeds,
        role_templates=role_templates,
    )


# ── Ecommerce ────────────────────────────────────────────────────────────────

_reg(
    "ecommerce", "ec", target_new=10, target_mm=3,
    categories=["shopping", "after_sales", "transaction"],
    exemplars=["ec-t1", "ec-t2", "ec-t5"],
    role_templates=(
        "commerce_ops_manager (hub)",
        "identity_verifier",
        "order_specialist",
        "refund_specialist",
        "payment_specialist",
        "knowledge_specialist",
        "revenue_specialist",
        "case_analyst",
    ),
    scenario_seeds=(
        "Subscription cancellation with retention offer and prorated refund",
        "Cross-border order with customs hold and address correction",
        "Promotional pricing dispute — customer claims advertised price differs from charged price",
        "Multi-warehouse stock reallocation for a split-shipment order",
        "VIP escalation with loyalty tier verification and goodwill credit",
        "Gift registry coordination with multiple recipients and partial fulfillment",
        "Marketplace seller dispute — buyer vs third-party seller mediation",
        "Product recall notification with affected-order identification and refund",
        "Bulk B2B order with volume discount recalculation after line-item removal",
        "Chargeback investigation with payment gateway evidence collection",
        "Pre-order release date change with customer notification and cancellation option",
        "Cart abandonment recovery with expired promotion handling",
    ),
)

# ── Finance ───────────────────────────────────────────────────────────────────

_reg(
    "finance", "fin", target_new=8, target_mm=2,
    categories=["financial_planning", "investing", "insurance", "lending"],
    exemplars=["fin-t1", "fin-t2", "fin-t5"],
    role_templates=(
        "relationship_manager (hub)",
        "trader",
        "risk_officer",
        "compliance_analyst",
        "operations_analyst",
        "credit_policy_specialist",
        "insurance_coverage_analyst",
    ),
    scenario_seeds=(
        "Margin call triggered by overnight market drop — liquidate or inject collateral",
        "Client requests tax-loss harvesting across two portfolios with wash-sale constraints",
        "Beneficiary change on trust account with identity verification and compliance hold",
        "Suspicious transaction alert — freeze, investigate, and file SAR or release",
        "Insurance claim payout with coverage limit check and co-pay calculation",
        "Mortgage refinancing assessment with rate-lock window and appraisal coordination",
        "Client death notification — estate freeze, beneficiary contact, and compliance filing",
        "Cross-border wire transfer with sanctions screening and currency conversion",
        "Portfolio rebalancing after client risk-tolerance downgrade",
        "Loan covenant violation — assess cure period, notify stakeholders, adjust terms",
        "Annuity surrender with early-withdrawal penalty and tax withholding calculation",
        "Joint account dispute — one holder requests freeze, other requests withdrawal",
    ),
)

# ── Healthcare ────────────────────────────────────────────────────────────────

_reg(
    "healthcare", "hc", target_new=8, target_mm=2,
    categories=["consultation", "prescription"],
    exemplars=["hc-t1", "hc-t6"],
    role_templates=(
        "care_coordinator (hub)",
        "patient_interviewer",
        "diagnostic_interface",
        "specialist_consultant",
        "medication_reconciliation_specialist",
    ),
    scenario_seeds=(
        "Pediatric fever with ambiguous rash — differentiate Kawasaki vs viral exanthem",
        "Post-surgical complication triage — wound dehiscence vs normal healing",
        "Chronic pain reassessment with opioid taper plan and alternative therapy referral",
        "Geriatric fall risk evaluation with polypharmacy review and home safety assessment",
        "Prenatal screening abnormality — amniocentesis decision and genetic counseling referral",
        "Psychiatric emergency — suicidal ideation assessment with involuntary hold criteria",
        "Diabetic ketoacidosis in ED — insulin protocol, electrolyte correction, ICU transfer",
        "Transplant eligibility workup — cross-match, panel-reactive antibody, social screening",
        "Drug interaction alert during prescription — substitute vs override with justification",
        "Telemedicine follow-up with conflicting remote vitals and in-clinic measurements",
        "Antibiotic stewardship review — de-escalate empiric broad-spectrum to culture-directed",
        "Occupational exposure (needlestick) — source patient testing, PEP initiation, reporting",
    ),
)

# ── Office / Enterprise Management ───────────────────────────────────────────

_reg(
    "office", "off", target_new=6, target_mm=2,
    categories=["personnel", "office_asset", "finance", "partnership"],
    exemplars=["off-t1", "off-t4", "off-t7"],
    tasks_dir_name="office",
    role_templates=(
        "operations_manager (hub)",
        "workplace_services_admin",
        "policy_analyst",
        "incident_operations_analyst",
        "finance_ops_analyst",
        "procurement_analyst",
        "vendor_coordinator",
    ),
    scenario_seeds=(
        "Employee termination with asset recovery, access revocation, and final payroll",
        "Office relocation planning — seat allocation, IT infrastructure move, vendor rebid",
        "Internal audit finding remediation — policy update, retraining, evidence collection",
        "Travel & expense policy violation investigation with manager escalation",
        "Contractor-to-FTE conversion with benefits enrollment and equipment provisioning",
        "Data center capacity planning with vendor quote comparison and budget approval",
        "Workplace harassment complaint — HR investigation, witness interviews, action plan",
        "Software license true-up audit with vendor negotiation and over-deployment remediation",
        "Emergency facilities incident (pipe burst) — vendor dispatch, insurance claim, relocation",
        "Annual performance review calibration with compensation adjustment and equity grant",
        "Cross-department project resource allocation conflict resolution",
        "Vendor SLA breach investigation with penalty calculation and contract amendment",
    ),
)

# ── Legal Compliance ─────────────────────────────────────────────────────────

_reg(
    "legal_compliance", "lc", target_new=6, target_mm=1,
    categories=["litigation", "contract", "audit"],
    exemplars=["lc-t1", "lc-t3", "lc-t5"],
    role_templates=(
        "legal_ops_manager (hub)",
        "legal_analyst",
        "exposure_analyst",
        "filings_specialist",
        "compliance_analyst",
        "standards_editor",
    ),
    scenario_seeds=(
        "Whistleblower complaint triage — privilege screening, investigation scope, board notification",
        "Cross-border data breach notification — multi-jurisdiction GDPR/CCPA timeline coordination",
        "Patent infringement cease-and-desist response — prior art search, licensing counter-offer",
        "M&A due diligence — material contract review with indemnification gap analysis",
        "Employment discrimination class action — document hold, statistical analysis, mediation prep",
        "Regulatory consent decree compliance audit — milestone verification, corrective action plan",
        "Trade secret misappropriation — emergency TRO filing with evidence preservation order",
        "Anti-bribery (FCPA) investigation — third-party payment review, voluntary disclosure decision",
        "Contract force majeure claim — event qualification, mitigation duty, notice requirements",
        "Product liability mass tort — bellwether case selection, expert retention, settlement modeling",
        "Government subpoena response — privilege log preparation, redaction, production timeline",
        "IP licensing renegotiation — royalty audit, usage metrics, renewal vs termination analysis",
    ),
)

# ── Daily Life ────────────────────────────────────────────────────────────────

_reg(
    "daily_life", "dl", target_new=6, target_mm=2,
    categories=["dining", "housing", "travel", "wellness"],
    exemplars=["dl-t1", "dl-t2"],
    role_templates=(
        "concierge_coordinator (hub)",
        "booking_specialist",
        "vendor_liaison",
        "insurance_coverage_analyst",
        "payments_and_coverage_specialist",
    ),
    scenario_seeds=(
        "International flight rebooking after cancellation with hotel and meal voucher coordination",
        "Home renovation contractor dispute — damage assessment, insurance claim, alternative vendor",
        "Pet emergency vet visit with insurance pre-authorization and pharmacy coordination",
        "Wedding venue double-booking resolution with vendor negotiation and deposit recovery",
        "Moving coordination — utility transfer, address change, storage unit booking, movers scheduling",
        "Gym membership cancellation with contract penalty review and medical exemption claim",
        "Restaurant reservation for large party with dietary restrictions and split-billing setup",
        "Car accident aftermath — insurance claim, rental car, body shop scheduling, police report",
        "Vacation rental dispute — property condition mismatch, partial refund negotiation, rebooking",
        "Home appliance warranty claim with manufacturer RMA, technician scheduling, loaner unit",
        "Travel medical emergency abroad — insurance activation, hospital liaison, repatriation planning",
        "Elder care coordination — in-home aide scheduling, medication delivery, family notification",
    ),
)

# ── Social Interaction ────────────────────────────────────────────────────────

_reg(
    "social_interaction", "si", target_new=6, target_mm=1,
    categories=["voice", "report"],
    exemplars=["si-t1", "si-t6"],
    bank_module="masp_bench.banks.social_interaction",
    role_templates=(
        "engagement_coordinator (hub)",
        "trust_safety_reviewer",
        "community_operations_specialist",
        "stakeholder_response_coordinator",
        "fact_checker",
        "reporter",
        "standards_editor",
    ),
    scenario_seeds=(
        "Coordinated harassment campaign investigation — cross-platform evidence, actor linking, platform action",
        "Viral misinformation debunking — source tracing, expert consultation, correction publishing",
        "Creator copyright dispute — DMCA counter-notice, fair-use analysis, content restoration decision",
        "Political ad transparency audit — funding source verification, targeting review, compliance action",
        "Doxxing incident response — PII removal, law enforcement referral, victim support coordination",
        "Influencer undisclosed sponsorship investigation — FTC compliance, evidence collection, enforcement",
        "Community crisis response — emergency information verification, rumor control, resource coordination",
    ),
)
