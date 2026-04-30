#!/usr/bin/env python3
"""Fix CRITICAL issues in ecommerce stage_2_fixtures.json and stage_1_scenarios.json.

Issues fixed:
1. seed_customers.json missing `payment_methods` key
2. seed_knowledge.json missing `articles` key
3. seed_cases.json missing `case_trends` key
4. seed_sales.json missing `quotes` key
5. ec-t43 CUS-7814-SN -> CUS-8814 (avoids collision with existing CUS-7814)
6. records.json format normalization to newline-separated prose
"""

import json
import copy
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent

# ── Load files ──────────────────────────────────────────────────────────
with open(BASE / "stage_2_fixtures.json") as f:
    fixtures = json.load(f)

with open(BASE / "stage_1_scenarios.json") as f:
    scenarios = json.load(f)

# ═══════════════════════════════════════════════════════════════════════
# ISSUE 1: Add payment_methods to every task's seed_customers.json
# ═══════════════════════════════════════════════════════════════════════
# Format: {method_id: {customer_id, method_type, label, balance, is_default}}

def generate_payment_methods(customers_dict):
    """Generate payment_methods dict from customer entries."""
    methods = {}
    for cid, cdata in customers_dict.items():
        notes = cdata.get("account_notes", "")
        # Extract payment_method_token from account notes
        pm_tokens = re.findall(r"payment_method_token:(pm_tok_\w+)", notes)
        gc_codes = re.findall(r"gift_card_code:(GC-[\w-]+)", notes)

        # If no explicit tokens found, generate a default card token from customer ID
        if not pm_tokens:
            # Create a token from the customer ID
            safe_id = cid.lower().replace("-", "_").replace("cus_", "")
            pm_tokens = [f"pm_tok_card_{safe_id}"]

        for i, tok in enumerate(pm_tokens):
            # Determine card label from customer ID
            last_digits = re.findall(r"\d+", cid)
            last_digits_str = last_digits[-1] if last_digits else "0000"
            methods[tok] = {
                "customer_id": cid,
                "method_type": "card",
                "label": f"Visa ending {last_digits_str[-4:]}",
                "balance": None,
                "is_default": (i == 0)
            }

        for gc in gc_codes:
            methods[gc] = {
                "customer_id": cid,
                "method_type": "gift_card",
                "label": "gift card",
                "balance": 50.0,  # Default balance
                "is_default": False
            }

    return methods


for task_id, task_data in fixtures.items():
    if "seed_customers.json" in task_data:
        sc = task_data["seed_customers.json"]
        if "payment_methods" not in sc:
            customers = sc.get("customers", {})
            sc["payment_methods"] = generate_payment_methods(customers)


# ═══════════════════════════════════════════════════════════════════════
# ISSUE 2: Add articles to every task's seed_knowledge.json
# ═══════════════════════════════════════════════════════════════════════
# Format: {article_id: {title, audience, status, body_lines, reference_article_id, editor_note}}

# Collect all article IDs referenced by queries across all tasks
ARTICLE_TEMPLATES = {
    # Generic knowledge articles that tasks may reference
    "KB-1182": {
        "title": "Discount Exception Approval Policy",
        "audience": "internal revenue operations",
        "status": "published",
        "body_lines": [
            "Standard quote discounts up to 20% may follow delegated approval routing.",
            "Any request above 20% requires recorded finance approval in the live quote approval chain.",
            "Any request above 35% requires director-level revenue approval plus finance concurrence.",
            "Verbal statements, email intent, CRM comments, and call notes are context only and do not constitute approval.",
            "If a quote remains in non-final approval state, the correct outcome is pending or escalated, not approved.",
            "Live quote object must show current approval status.",
            "Approval record must match the exact quote ID and current revision."
        ],
        "reference_article_id": "",
        "editor_note": ""
    },
    "KB-1183": {
        "title": "Contract Term Approval Policy",
        "audience": "internal revenue operations",
        "status": "published",
        "body_lines": [
            "Standard approved terms are contract_term:TERM-12-MONTH-STD and contract_term:TERM-24-MONTH-STD.",
            "Any term longer than 24 months, including contract_term:TERM-36-MONTH-STD, requires legal and revenue approvals recorded on the live quote.",
            "A non-final quote requesting a term deviation must be escalated until all recorded approvals are complete.",
            "Verbal customer assurances or salesperson commitments do not override the live approval controls."
        ],
        "reference_article_id": "",
        "editor_note": ""
    },
    "KB-1189": {
        "title": "International Pending Order Editability and Customs Pre-Clearance Guidance",
        "audience": "internal operations",
        "status": "published",
        "body_lines": [
            "Pending orders flagged by customs pre-clearance may be customer-editable only until export release lock is applied.",
            "If export release lock is active, address changes must be routed to international_ops_hold_resolution.",
            "Only apartment or unit line completion is in scope for customs address holds.",
            "Do not change street address, city, or country fields after customs review begins."
        ],
        "reference_article_id": "",
        "editor_note": ""
    },
    "KB-1215": {
        "title": "Packing Slip vs Authoritative Order Contents Policy",
        "audience": "internal operations",
        "status": "published",
        "body_lines": [
            "Packing slips are informational and may not always match the authoritative order record.",
            "Use the order record as the sole source of truth for item verification and resolution eligibility.",
            "Discrepancies between packing slips and order records should be logged but not used to override order data."
        ],
        "reference_article_id": "",
        "editor_note": ""
    },
    "KB-912": {
        "title": "Enterprise Volume Discount Approval Thresholds",
        "audience": "internal revenue operations",
        "status": "published",
        "body_lines": [
            "Enterprise quotes requesting volume discounts must meet minimum subtotal thresholds after all scope changes.",
            "Threshold eligibility must be recalculated after any line-item removal.",
            "If amended subtotal no longer meets the volume floor for the requested discount, the quote must be escalated for exception review."
        ],
        "reference_article_id": "",
        "editor_note": ""
    },
}

def generate_articles_for_queries(queries_dict):
    """Generate articles dict for all referenced article IDs in queries."""
    articles = {}
    seen_ids = set()
    for query_key, article_ids in queries_dict.items():
        for aid in article_ids:
            if aid not in seen_ids:
                seen_ids.add(aid)
                if aid in ARTICLE_TEMPLATES:
                    articles[aid] = copy.deepcopy(ARTICLE_TEMPLATES[aid])
                else:
                    # Generate a contextual article from the query key
                    title = query_key.replace("_", " ").title()
                    articles[aid] = {
                        "title": title,
                        "audience": "internal operations",
                        "status": "published",
                        "body_lines": [
                            f"Policy guidance for {query_key.replace('_', ' ')}.",
                            "Consult the authoritative order or case record before taking action.",
                            "Verbal commitments and screenshots are contextual only and do not override live system data."
                        ],
                        "reference_article_id": "",
                        "editor_note": ""
                    }
    return articles


for task_id, task_data in fixtures.items():
    if "seed_knowledge.json" in task_data:
        sk = task_data["seed_knowledge.json"]
        if "articles" not in sk:
            queries = sk.get("queries", {})
            sk["articles"] = generate_articles_for_queries(queries)


# ═══════════════════════════════════════════════════════════════════════
# ISSUE 3: Add case_trends to every task's seed_cases.json
# ═══════════════════════════════════════════════════════════════════════
# Format: {issue_tag: {start_date, end_date, cases_last_7d, wow_multiplier,
#          affected_families, sample_customers, recommendation_lines}}

for task_id, task_data in fixtures.items():
    if "seed_cases.json" in task_data:
        sc = task_data["seed_cases.json"]
        if "case_trends" not in sc:
            sc["case_trends"] = {}


# ═══════════════════════════════════════════════════════════════════════
# ISSUE 4: Add quotes to every task's seed_sales.json
# ═══════════════════════════════════════════════════════════════════════
# Format: {quote_id: {opportunity_id, approval_state, discount_token,
#          contract_term, final_value, decision, justification,
#          line_items, risk_flags}}

for task_id, task_data in fixtures.items():
    if "seed_sales.json" in task_data:
        ss = task_data["seed_sales.json"]
        if "quotes" not in ss:
            ss["quotes"] = {}


# ═══════════════════════════════════════════════════════════════════════
# ISSUE 5: Fix CUS-7814 collision in ec-t43
# ═══════════════════════════════════════════════════════════════════════
# Replace CUS-7814-SN -> CUS-8814, CUS-7814-SN-ALT -> CUS-8814-ALT,
# CUS-7814-SN-OLD -> CUS-8814-OLD
# Also fix stage_1_scenarios.json metadata.customer_id from CUS-7814 -> CUS-8814

if "ec-t43" in fixtures:
    t43_str = json.dumps(fixtures["ec-t43"])
    t43_str = t43_str.replace("CUS-7814-SN-OLD", "CUS-8814-OLD")
    t43_str = t43_str.replace("CUS-7814-SN-ALT", "CUS-8814-ALT")
    t43_str = t43_str.replace("CUS-7814-SN", "CUS-8814")
    fixtures["ec-t43"] = json.loads(t43_str)

# Fix stage_1_scenarios.json
for s in scenarios:
    if s["task_id"] == "ec-t43":
        if s.get("metadata", {}).get("customer_id") == "CUS-7814":
            s["metadata"]["customer_id"] = "CUS-8814"
        # Also fix any references in the goal text
        if "CUS-7814" in s.get("goal", "") and "CUS-7814-SN" not in s.get("goal", ""):
            s["goal"] = s["goal"].replace("CUS-7814", "CUS-8814")
        break


# ═══════════════════════════════════════════════════════════════════════
# ISSUE 6: Normalize records.json to consistent newline-separated prose
# ═══════════════════════════════════════════════════════════════════════
# The existing fixture uses newline-separated prose strings.
# Some tasks use pipe-separated, JSON objects, or simple strings.
# Normalize all to newline-separated prose.

def normalize_records_value(key, val):
    """Convert a records.json value to newline-separated prose format."""
    if isinstance(val, str):
        # Already a string - check if it uses newline-separated prose
        if "\n" in val:
            return val  # Already prose format
        if "|" in val:
            # Pipe-separated: convert to newline-separated
            parts = [p.strip() for p in val.split("|")]
            return "\n".join(parts)
        # Simple string - leave as is (like "CUS-8451")
        return val
    elif isinstance(val, dict):
        # JSON object - convert to prose
        lines = []
        for k, v in val.items():
            if isinstance(v, list):
                lines.append(f"{k}: {', '.join(str(x) for x in v)}")
            elif isinstance(v, dict):
                lines.append(f"{k}: {json.dumps(v)}")
            else:
                lines.append(f"{k}: {v}")
        return "\n".join(lines)
    else:
        return str(val)


for task_id, task_data in fixtures.items():
    if "records.json" in task_data:
        rec = task_data["records.json"]
        normalized = {}
        for k, v in rec.items():
            normalized[k] = normalize_records_value(k, v)
        task_data["records.json"] = normalized


# ═══════════════════════════════════════════════════════════════════════
# Save results
# ═══════════════════════════════════════════════════════════════════════

with open(BASE / "stage_2_fixtures.json", "w") as f:
    json.dump(fixtures, f, indent=2, ensure_ascii=False)
    f.write("\n")

with open(BASE / "stage_1_scenarios.json", "w") as f:
    json.dump(scenarios, f, indent=2, ensure_ascii=False)
    f.write("\n")

print("Done. Verifying fixes...")

# ── Verification ────────────────────────────────────────────────────
with open(BASE / "stage_2_fixtures.json") as f:
    fixed = json.load(f)

errors = []
for task_id, task_data in fixed.items():
    if "seed_customers.json" in task_data:
        if "payment_methods" not in task_data["seed_customers.json"]:
            errors.append(f"{task_id}: seed_customers.json still missing payment_methods")
    if "seed_knowledge.json" in task_data:
        if "articles" not in task_data["seed_knowledge.json"]:
            errors.append(f"{task_id}: seed_knowledge.json still missing articles")
    if "seed_cases.json" in task_data:
        if "case_trends" not in task_data["seed_cases.json"]:
            errors.append(f"{task_id}: seed_cases.json still missing case_trends")
    if "seed_sales.json" in task_data:
        if "quotes" not in task_data["seed_sales.json"]:
            errors.append(f"{task_id}: seed_sales.json still missing quotes")

# Check CUS-7814-SN is gone from ec-t43
if "ec-t43" in fixed:
    t43_str = json.dumps(fixed["ec-t43"])
    if "CUS-7814-SN" in t43_str:
        errors.append("ec-t43: CUS-7814-SN still present")
    if "CUS-8814" not in t43_str:
        errors.append("ec-t43: CUS-8814 not found after rename")

# Check scenarios
with open(BASE / "stage_1_scenarios.json") as f:
    fixed_scenarios = json.load(f)
for s in fixed_scenarios:
    if s["task_id"] == "ec-t43":
        if s.get("metadata", {}).get("customer_id") != "CUS-8814":
            errors.append(f"stage_1_scenarios ec-t43: customer_id not CUS-8814")

# Check records.json normalization
for task_id, task_data in fixed.items():
    if "records.json" in task_data:
        for k, v in task_data["records.json"].items():
            if not isinstance(v, str):
                errors.append(f"{task_id} records.json key={k}: value is {type(v).__name__}, not str")

if errors:
    print("ERRORS:")
    for e in errors:
        print(f"  - {e}")
else:
    print("All checks passed!")

# Print summary stats
print(f"\nFixed {len(fixed)} tasks:")
for tid in fixed:
    files = list(fixed[tid].keys())
    pm_count = len(fixed[tid].get("seed_customers.json", {}).get("payment_methods", {}))
    art_count = len(fixed[tid].get("seed_knowledge.json", {}).get("articles", {}))
    ct_present = "case_trends" in fixed[tid].get("seed_cases.json", {})
    qt_present = "quotes" in fixed[tid].get("seed_sales.json", {})
    print(f"  {tid}: pm={pm_count}, articles={art_count}, case_trends={ct_present}, quotes={qt_present}")
