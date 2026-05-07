#!/usr/bin/env python3
"""Aggregate perturbation run reports into a summary JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from masp.perturbation_eval import aggregate_report_files


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize MASP perturbation reports")
    parser.add_argument("reports_dir", help="Directory containing result JSON files")
    parser.add_argument(
        "--output",
        default=None,
        help="Output summary path (default: reports_dir/perturbation_summary.json)",
    )
    args = parser.parse_args()
    reports_dir = Path(args.reports_dir)
    paths = sorted(reports_dir.rglob("*.json"))
    summary = aggregate_report_files(paths)
    output = Path(args.output) if args.output else reports_dir / "perturbation_summary.json"
    output.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()
