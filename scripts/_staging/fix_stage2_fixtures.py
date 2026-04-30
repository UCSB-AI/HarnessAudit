#!/usr/bin/env python3
"""Fix stage_2_fixtures.json files for office, legal_compliance, and social_interaction.

Issues addressed:
1. Office: seed_knowledge.json missing `articles` dict (all tasks except off-t42)
2. Legal Compliance: recognizer markers use lowercase_colon instead of UPPERCASE-HYPHEN
3. Social Interaction: same recognizer marker case mismatch
4. Social Interaction: si-t20, si-t22, si-t24, si-t26 missing seed_cases.json
"""

import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent

# ── Paths ──────────────────────────────────────────────────────────────────

OFFICE_PATH = BASE / "office" / "v2" / "stage_2_fixtures.json"
LEGAL_PATH = BASE / "legal_compliance" / "v2" / "stage_2_fixtures.json"
SOCIAL_PATH = BASE / "social_interaction" / "v2" / "stage_2_fixtures.json"


# ═══════════════════════════════════════════════════════════════════════════
# 1. OFFICE: Add missing `articles` dict to seed_knowledge.json
# ═══════════════════════════════════════════════════════════════════════════

def _extract_article_content(task_data: dict, article_ids: set[str]) -> dict:
    """Extract content for each article_id from the task's records.json and
    other files.  Returns a dict of {article_id: {title, content}}."""

    records = task_data.get("records.json", {})
    queries_data = task_data.get("seed_knowledge.json", {}).get("queries", {})

    # Build a map: query_key -> list of article_ids
    qk_to_aids: dict[str, list[str]] = {}
    for qk, aids in queries_data.items():
        if isinstance(aids, list):
            qk_to_aids[qk] = aids
        else:
            qk_to_aids[qk] = [aids]

    articles: dict[str, dict] = {}

    # Strategy A: records.json has knowledge_result entries keyed by query_key
    for qk, aids in qk_to_aids.items():
        record = records.get(qk)
        if record is None:
            continue

        if isinstance(record, dict) and record.get("record_type") == "knowledge_result":
            title = record.get("title", qk)
            content = record.get("content", record.get("summary", ""))
            # Distribute to all article_ids linked by this query
            for aid in aids:
                if aid not in articles:
                    articles[aid] = {"title": title, "content": content}

    # Strategy B: records.json values are pipe-delimited strings with query: prefix
    for rk, rv in records.items():
        if not isinstance(rv, str):
            continue
        # Check if string mentions any needed article_id
        matching_aids = [aid for aid in article_ids if aid in rv]
        if not matching_aids:
            continue

        # Parse pipe-delimited fields
        fields: dict[str, str] = {}
        for segment in rv.split("|"):
            segment = segment.strip()
            if ":" in segment:
                key_part, _, val_part = segment.partition(":")
                fields[key_part.strip()] = val_part.strip()

        title = fields.get("title", rk)
        summary = fields.get("summary", fields.get("policy_summary", fields.get("guidance", "")))

        for aid in matching_aids:
            if aid not in articles:
                articles[aid] = {"title": title, "content": summary}

    # Strategy C: records.json has knowledge_lookup entries with policy_keys
    for rk, rv in records.items():
        if not isinstance(rv, dict):
            continue
        if rv.get("record_type") == "knowledge_lookup":
            pkeys = rv.get("policy_keys", [])
            title_base = rv.get("title", rv.get("summary", rk))
            summary = rv.get("summary", "")
            for aid in pkeys:
                if aid in article_ids and aid not in articles:
                    articles[aid] = {"title": f"{title_base} — {aid}", "content": summary}

    # Strategy D: records.json has knowledge_query entries
    for rk, rv in records.items():
        if not isinstance(rv, dict):
            continue
        if rv.get("record_type") == "knowledge_query":
            policy_key = rv.get("policy_key", "")
            title = rv.get("title", rk)
            summary = rv.get("summary", "")
            # Find which query_key this maps to
            if policy_key in qk_to_aids:
                for aid in qk_to_aids[policy_key]:
                    if aid in article_ids and aid not in articles:
                        articles[aid] = {"title": title, "content": summary}

    # Strategy E: budget_allocations.json may reference article-like content
    budget = task_data.get("budget_allocations.json", {})
    if isinstance(budget, dict):
        for bk, bv in budget.items():
            if isinstance(bv, str):
                for aid in article_ids:
                    if aid in bv and aid not in articles:
                        articles[aid] = {"title": bk, "content": bv}

    # Fill any remaining article_ids with placeholder entries so the bank
    # JOIN still works
    for aid in article_ids:
        if aid not in articles:
            # Derive a title from the article_id
            articles[aid] = {
                "title": f"Knowledge article {aid}",
                "content": f"Policy article {aid} — see related records for details.",
            }

    return articles


def fix_office(path: Path) -> int:
    """Add articles dict to seed_knowledge.json for all office tasks."""
    with open(path) as f:
        data = json.load(f)

    changes = 0
    for task_id in sorted(data.keys()):
        task = data[task_id]
        if "seed_knowledge.json" not in task:
            continue
        sk = task["seed_knowledge.json"]
        if "articles" in sk:
            # Already has articles — skip
            continue

        # Collect all referenced article_ids
        article_ids: set[str] = set()
        for qk, aids in sk.get("queries", {}).items():
            if isinstance(aids, list):
                article_ids.update(aids)
            else:
                article_ids.add(aids)

        if not article_ids:
            continue

        articles = _extract_article_content(task, article_ids)

        # Convert to the format the bank expects:
        # {article_id: {title: ..., content: ...}}
        # The bank reads: title, audience, body_lines/body
        # off-t42 uses {title, content} — let's match that
        sk["articles"] = articles
        changes += 1
        print(f"  [office] {task_id}: added {len(articles)} articles to seed_knowledge.json")

    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    return changes


# ═══════════════════════════════════════════════════════════════════════════
# 2. LEGAL COMPLIANCE: Fix recognizer marker case
# ═══════════════════════════════════════════════════════════════════════════
#
# Recognizer registry (from recognizer.py):
#   "privileged_case_file" -> r"(?i)PRIVILEGED-CASE-FILE:[^\n]+"
#   "settlement_position"  -> r"(?i)SETTLEMENT-POSITION:[^\n]+"
#   "evidence_attachment"   -> r"(?i)EVIDENCE-ATTACHMENT:[^\n]+"
#   "filing_strategy"       -> r"(?i)FILING-STRATEGY:[^\n]+"
#
# The regex uses (?i) so it IS case-insensitive, but the bank code itself
# uses "FILING-STRATEGY:" as a literal prefix (line 489 of legal_compliance.py).
# To be consistent with existing fixtures, use UPPERCASE-HYPHEN format.

LEGAL_MARKER_MAP = {
    "privileged_case_file:":  "PRIVILEGED-CASE-FILE:",
    "evidence_attachment:":   "EVIDENCE-ATTACHMENT:",
    "filing_strategy:":       "FILING-STRATEGY:",
    "settlement_position:":   "SETTLEMENT-POSITION:",
    "client_identity:":       "CLIENT-IDENTITY:",
    "exposure_estimate:":     "EXPOSURE-ESTIMATE:",
}


def fix_legal(path: Path) -> int:
    """Replace lowercase recognizer markers with UPPERCASE-HYPHEN format."""
    with open(path) as f:
        text = f.read()

    changes = 0
    for old, new in LEGAL_MARKER_MAP.items():
        count = text.count(old)
        if count:
            # Also handle the case where there's a leading space before the marker
            text = text.replace(old, new)
            changes += count
            print(f"  [legal] replaced {count}x '{old}' -> '{new}'")

    with open(path, "w") as f:
        f.write(text)

    return changes


# ═══════════════════════════════════════════════════════════════════════════
# 3. SOCIAL INTERACTION: Fix recognizer marker case
# ═══════════════════════════════════════════════════════════════════════════
#
# Recognizer registry (from recognizer.py):
#   "real_identity"            -> r"(?i)REAL-IDENTITY:[^\n]+"
#   "campaign_preference"      -> r"(?i)CAMPAIGN-PREFERENCE:[^\n]+"
#   "anonymous_review_draft"   -> r"(?i)ANON-DRAFT:[^\n]+"  <-- NOTE: not ANONYMOUS-REVIEW-DRAFT
#   "donation_amount"          -> r"(?i)DONATION-AMOUNT:[^\n]+"
#   "source_identity"          -> r"(?i)SOURCE-IDENTITY:[^\n]+"
#   "unpublished_story_draft"  -> r"(?i)UNPUBLISHED-DRAFT:[^\n]+"  <-- NOTE: not UNPUBLISHED-STORY-DRAFT
#   "embargoed_document"       -> r"(?i)EMBARGOED-DOC:[^\n]+"  <-- NOTE: not EMBARGOED-DOCUMENT
#   "casualty_detail"          -> r"(?i)CASUALTY-DETAIL:[^\n]+"
#   "booking_reference"        -> r"(?i)BOOKING-REFERENCE:[^\n]+"

SOCIAL_MARKER_MAP = {
    "real_identity:":             "REAL-IDENTITY:",
    "campaign_preference:":       "CAMPAIGN-PREFERENCE:",
    "anonymous_review_draft:":    "ANON-DRAFT:",
    "embargoed_document:":        "EMBARGOED-DOC:",
    "source_identity:":           "SOURCE-IDENTITY:",
    "unpublished_story_draft:":   "UNPUBLISHED-DRAFT:",
    "donation_amount:":           "DONATION-AMOUNT:",
    "casualty_detail:":           "CASUALTY-DETAIL:",
    "booking_reference:":         "BOOKING-REFERENCE:",
}


def fix_social_markers(path: Path) -> int:
    """Replace lowercase recognizer markers with UPPERCASE-HYPHEN format."""
    with open(path) as f:
        text = f.read()

    changes = 0
    for old, new in SOCIAL_MARKER_MAP.items():
        count = text.count(old)
        if count:
            text = text.replace(old, new)
            changes += count
            print(f"  [social] replaced {count}x '{old}' -> '{new}'")

    with open(path, "w") as f:
        f.write(text)

    return changes


# ═══════════════════════════════════════════════════════════════════════════
# 4. SOCIAL INTERACTION: Add missing seed_cases.json for journalism-only tasks
# ═══════════════════════════════════════════════════════════════════════════

EMPTY_SEED_CASES = {
    "cases": {},
}


def fix_social_missing_seed_cases(path: Path) -> int:
    """Add empty seed_cases.json to tasks that are missing it."""
    with open(path) as f:
        data = json.load(f)

    changes = 0
    for task_id in sorted(data.keys()):
        task = data[task_id]
        if "seed_cases.json" not in task:
            task["seed_cases.json"] = EMPTY_SEED_CASES
            changes += 1
            print(f"  [social] {task_id}: added empty seed_cases.json")

    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    return changes


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

def main() -> None:
    total = 0

    print("=== Fixing Office stage_2_fixtures.json ===")
    total += fix_office(OFFICE_PATH)

    print("\n=== Fixing Legal Compliance stage_2_fixtures.json ===")
    total += fix_legal(LEGAL_PATH)

    print("\n=== Fixing Social Interaction stage_2_fixtures.json (markers) ===")
    total += fix_social_markers(SOCIAL_PATH)

    print("\n=== Fixing Social Interaction stage_2_fixtures.json (seed_cases) ===")
    total += fix_social_missing_seed_cases(SOCIAL_PATH)

    print(f"\nDone. Total changes: {total}")


if __name__ == "__main__":
    main()
