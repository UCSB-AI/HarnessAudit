#!/usr/bin/env python3
"""Generate PDF assets for multimodal tasks using reportlab.

Reads asset specs from stage_5_assets.json and task YAMLs, generates
text-based PDF documents (contracts, invoices, policies, reports, memos).

Usage:
    python scripts/generate_pdfs.py                    # All domains
    python scripts/generate_pdfs.py --domain finance   # Single domain
    python scripts/generate_pdfs.py --task fin-t13     # Single task
    python scripts/generate_pdfs.py --dry-run          # List without generating
    python scripts/generate_pdfs.py --regenerate       # Regenerate even if file exists
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import textwrap
from pathlib import Path

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        HRFlowable, PageBreak,
    )
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
except ImportError:
    print("ERROR: reportlab is required. Install with: pip install reportlab")
    sys.exit(1)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TASKS_ROOT = PROJECT_ROOT / "multi_agent" / "tasks"

DOMAIN_DIRS = {
    "daily_life": "daily_life",
    "ecommerce": "ecommerce",
    "finance": "finance",
    "healthcare": "healthcare",
    "legal_compliance": "legal_compliance",
    "office": "office",
    "social_interaction": "social_interaction",
}


# =========================================================================
# Styles
# =========================================================================

def _build_styles():
    """Build PDF paragraph styles."""
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        "DocTitle", parent=styles["Title"], fontSize=14, spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        "DocSubtitle", parent=styles["Normal"], fontSize=10,
        textColor=colors.grey, spaceAfter=8, alignment=TA_CENTER,
    ))
    styles.add(ParagraphStyle(
        "SectionHead", parent=styles["Heading2"], fontSize=11,
        spaceBefore=14, spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        "FieldLabel", parent=styles["Normal"], fontSize=9,
        leading=12, spaceAfter=2, fontName="Helvetica-Bold",
    ))
    styles.add(ParagraphStyle(
        "FieldValue", parent=styles["Normal"], fontSize=9,
        leading=12, spaceAfter=4, leftIndent=12,
    ))
    styles.add(ParagraphStyle(
        "BodyText2", parent=styles["Normal"], fontSize=9,
        leading=13, spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        "SmallFooter", parent=styles["Normal"], fontSize=7,
        textColor=colors.grey, fontName="Helvetica-Oblique",
        alignment=TA_CENTER,
    ))
    styles.add(ParagraphStyle(
        "TableCell", parent=styles["Normal"], fontSize=8, leading=10,
    ))
    return styles


# =========================================================================
# Content extraction from generation_prompt
# =========================================================================

def _parse_generation_prompt(prompt: str) -> dict:
    """Extract structured hints from the generation_prompt.

    Returns dict with optional keys:
      doc_type, org_name, sections (list of section dicts),
      header_lines, footer_lines, table_data, body_paragraphs
    """
    result: dict = {}
    if not prompt:
        return result

    prompt_lower = prompt.lower()

    # Detect document type
    for dtype in ["invoice", "contract", "referral", "appraisal",
                  "discharge", "pay stub", "payroll", "policy",
                  "report", "memo", "letter", "notice", "summary",
                  "instructions", "verification", "certificate",
                  "statement", "schedule", "proposal", "quote",
                  "assessment", "order form", "filing", "complaint",
                  "declaration", "brief", "motion", "petition"]:
        if dtype in prompt_lower:
            result["doc_type"] = dtype
            break

    # Extract organization/facility name from prompt
    org_match = re.search(
        r"(?:from|for|by|on.*letterhead)\s+['\"]?([A-Z][A-Za-z\s&.'-]{3,40}?)(?:['\"]|[.,;]|\s+(?:with|include|the|address))",
        prompt,
    )
    if org_match:
        result["org_name"] = org_match.group(1).strip()

    # Extract sections mentioned in prompt
    section_matches = re.findall(
        r"(?:Section|section|Page|page)\s+\d+[:\s]+([A-Z][A-Za-z\s,&-]{3,60})",
        prompt,
    )
    if section_matches:
        result["sections"] = section_matches

    # Extract explicit body text hints
    body_lines = []
    for line in prompt.split("."):
        line = line.strip()
        if any(kw in line.lower() for kw in
               ["include", "show", "display", "list", "note",
                "state that", "mention", "reference"]):
            # Clean out meta-instructions
            cleaned = re.sub(
                r"(?i)^(include|show|display|list|add|create|render|generate|make sure|ensure)\s+",
                "", line,
            ).strip()
            if cleaned and len(cleaned) > 10:
                body_lines.append(cleaned)
    if body_lines:
        result["body_hints"] = body_lines[:15]  # cap

    return result


def _facts_to_field_table(facts: list, styles) -> list:
    """Convert canonical_facts into a clean field→value table."""
    rows = []
    for fact in facts:
        if isinstance(fact, dict):
            for k, v in fact.items():
                rows.append((
                    str(k).replace("_", " ").title(),
                    str(v),
                ))
        elif isinstance(fact, str):
            if "=" in fact:
                key_part, _, val_part = fact.partition("=")
                # Strip dotted prefixes: "header.quote_id = Q-8816" → "Quote Id"
                key_clean = key_part.split(".")[-1].strip().replace("_", " ").title()
                rows.append((key_clean, val_part.strip()))
            elif ":" in fact and not fact.startswith("http"):
                key_part, _, val_part = fact.partition(":")
                key_clean = key_part.strip().replace("_", " ").title()
                rows.append((key_clean, val_part.strip()))
            else:
                rows.append(("", fact))

    if not rows:
        return []

    # Build reportlab table
    table_data = []
    for label, value in rows:
        if label:
            table_data.append([
                Paragraph(f"<b>{label}</b>", styles["TableCell"]),
                Paragraph(value, styles["TableCell"]),
            ])
        else:
            table_data.append([
                Paragraph(value, styles["TableCell"]),
                Paragraph("", styles["TableCell"]),
            ])

    t = Table(table_data, colWidths=[2.2 * inch, 4.3 * inch])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.lightgrey),
    ]))
    return [t]


# =========================================================================
# PDF rendering
# =========================================================================

def generate_pdf(
    out_path: Path,
    title: str,
    description: str,
    canonical_facts: list,
    generation_prompt: str = "",
) -> None:
    """Generate a realistic-looking PDF from asset spec.

    Uses canonical_facts as the authoritative data content.
    Uses generation_prompt as layout/structure hints (NEVER rendered as text).
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    styles = _build_styles()

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
    )

    story = []
    hints = _parse_generation_prompt(generation_prompt)

    # ── Header / org name ──
    org = hints.get("org_name", "")
    if org:
        story.append(Paragraph(org, styles["DocSubtitle"]))
        story.append(Spacer(1, 4))

    # ── Title ──
    clean_title = title.replace("_", " ").replace(".pdf", "")
    # Capitalize intelligently
    clean_title = " ".join(
        w.upper() if len(w) <= 3 and w.isalpha() else w.title()
        for w in clean_title.split()
    )
    story.append(Paragraph(clean_title, styles["DocTitle"]))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.black))
    story.append(Spacer(1, 8))

    # ── Description line ──
    if description:
        story.append(Paragraph(description, styles["DocSubtitle"]))
        story.append(Spacer(1, 6))

    # ── Main content from canonical_facts ──
    if canonical_facts:
        # Group facts by prefix/section
        sections: dict[str, list] = {}
        for fact in canonical_facts:
            if isinstance(fact, dict):
                for k, v in fact.items():
                    sections.setdefault("Details", []).append(f"{k} = {v}")
                continue
            fact_str = str(fact)
            # Try to group by dotted prefix: "header.field = val"
            if "." in fact_str and "=" in fact_str:
                prefix = fact_str.split(".")[0].strip()
                sections.setdefault(prefix, []).append(fact_str)
            elif ":" in fact_str and not fact_str.startswith("http"):
                # "claim_case:HC-90314 appears on..." → extract as field
                sections.setdefault("Details", []).append(fact_str)
            else:
                sections.setdefault("Details", []).append(fact_str)

        for section_name, section_facts in sections.items():
            section_title = section_name.replace("_", " ").title()
            story.append(Paragraph(section_title, styles["SectionHead"]))
            story.extend(_facts_to_field_table(section_facts, styles))
            story.append(Spacer(1, 6))

    # ── Body hints from generation_prompt (rendered as realistic paragraphs) ──
    body_hints = hints.get("body_hints", [])
    if body_hints:
        story.append(Paragraph("Additional Information", styles["SectionHead"]))
        for hint in body_hints:
            # Clean any remaining meta-language
            hint = re.sub(r"(?i)(?:should|must|needs to)\s+", "", hint)
            if hint and len(hint) > 5:
                story.append(Paragraph(hint, styles["BodyText2"]))
        story.append(Spacer(1, 6))

    # ── Prompt-specified sections ──
    section_names = hints.get("sections", [])
    if section_names:
        for sname in section_names:
            story.append(Paragraph(sname.strip(), styles["SectionHead"]))
            story.append(Paragraph(
                "<i>See attached documentation.</i>", styles["BodyText2"],
            ))

    # ── Footer ──
    story.append(Spacer(1, 20))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.lightgrey))
    doc_type = hints.get("doc_type", "document").title()
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        f"This {doc_type.lower()} is confidential and intended for authorized recipients only.",
        styles["SmallFooter"],
    ))

    doc.build(story)


# =========================================================================
# Asset discovery
# =========================================================================

def find_pdf_assets(*, regenerate: bool = False) -> list[dict]:
    """Find all PDF assets declared in task YAMLs.

    If regenerate=False (default), only returns assets that don't exist on disk.
    If regenerate=True, returns ALL PDF assets (for re-rendering).
    """
    import yaml

    assets = []
    for yf in sorted(TASKS_ROOT.rglob("*.yaml")):
        if "/assets/" in str(yf):
            continue
        data = yaml.safe_load(yf.read_text())
        if not data or "task_id" not in data:
            continue

        for asset in data.get("input_assets", []):
            if asset.get("asset_type") != "pdf":
                continue
            path = asset.get("path", "")
            resolved = yf.parent / path

            if not regenerate and resolved.exists():
                continue

            assets.append({
                "task_id": data["task_id"],
                "domain": data.get("domain", ""),
                "yaml_path": str(yf),
                "asset_path": path,
                "resolved_path": str(resolved),
                "description": asset.get("description", ""),
                "filename": path.split("/")[-1] if path else "",
            })
    return assets


def load_stage5_facts(domain: str) -> dict[str, dict[str, dict]]:
    """Load canonical_facts + generation_prompt from stage_5_assets.json.

    Searches ALL batch directories under _staging/<domain>/ (not just hardcoded names).
    Returns {task_id: {filename: {"facts": [...], "prompt": "..."}}}.
    """
    staging_root = PROJECT_ROOT / "scripts" / "_staging"
    domain_dir = staging_root / domain
    if not domain_dir.exists():
        return {}

    result: dict[str, dict[str, dict]] = {}

    # Search all batch dirs, newest first (by mtime)
    batch_dirs = sorted(
        [d for d in domain_dir.iterdir() if d.is_dir()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    for batch_dir in batch_dirs:
        s5_path = batch_dir / "stage_5_assets.json"
        if not s5_path.exists():
            continue
        data = json.loads(s5_path.read_text())
        for tid, asset_list in data.items():
            if tid in result:
                continue  # already loaded from a newer batch
            if not isinstance(asset_list, list):
                continue
            by_file: dict[str, dict] = {}
            for a in asset_list:
                fname = a.get("filename", "")
                if not fname:
                    continue
                by_file[fname] = {
                    "facts": a.get("canonical_facts", []),
                    "prompt": a.get("generation_prompt", "") or a.get("content_spec", ""),
                }
            result[tid] = by_file

    return result


# =========================================================================
# Main
# =========================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate PDF assets for HarnessAudit-Bench")
    parser.add_argument("--domain", help="Only process this domain")
    parser.add_argument("--task", help="Only process this task ID")
    parser.add_argument("--dry-run", action="store_true", help="List PDFs without generating")
    parser.add_argument("--regenerate", action="store_true",
                        help="Regenerate all PDFs (including existing ones)")
    args = parser.parse_args()

    assets = find_pdf_assets(regenerate=args.regenerate)
    if args.domain:
        assets = [a for a in assets if a["domain"] == args.domain]
    if args.task:
        assets = [a for a in assets if a["task_id"] == args.task]

    print(f"Found {len(assets)} PDF assets to generate")

    if args.dry_run:
        for a in assets:
            print(f"  [{a['task_id']}] {a['asset_path']}")
        return

    # Load stage5 facts per domain
    facts_cache: dict[str, dict] = {}
    for a in assets:
        domain = a["domain"]
        if domain not in facts_cache:
            facts_cache[domain] = load_stage5_facts(domain)

    generated = 0
    failed = 0
    no_facts = 0
    for a in assets:
        tid = a["task_id"]
        domain = a["domain"]
        fname = a["filename"]
        out = Path(a["resolved_path"])

        # Get canonical facts + generation prompt from stage5
        task_assets = facts_cache.get(domain, {}).get(tid, {})
        asset_spec = task_assets.get(fname, {})
        facts = asset_spec.get("facts", [])
        prompt = asset_spec.get("prompt", "")

        if not facts and not prompt:
            no_facts += 1

        try:
            generate_pdf(
                out_path=out,
                title=fname,
                description=a["description"],
                canonical_facts=facts,
                generation_prompt=prompt,
            )
            generated += 1
            tag = "" if facts else " [no stage5 data]"
            print(f"  OK {tid}: {a['asset_path']}{tag}")
        except Exception as exc:
            failed += 1
            print(f"  FAIL {tid}: {a['asset_path']} — {exc}")

    print(f"\nGenerated: {generated}, Failed: {failed}")
    if no_facts:
        print(f"  ({no_facts} PDFs had no stage5 canonical_facts — rendered with title+description only)")


if __name__ == "__main__":
    main()
