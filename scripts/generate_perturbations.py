#!/usr/bin/env python3
"""Generate MASP Layer 3 safety perturbation specs.

Template generation is file/env safe: it reads task YAMLs and tool catalogs,
but does not read runtime secrets.  Hybrid generation loads ``.env`` only to
configure the OpenAI client; secret values are never added to prompts or specs.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from masp_bench.perturbation import SCHEMA_VERSION
from scripts.perturbgen.generator import generate_variants_for_task
from scripts.perturbgen.inventory import build_attack_surface
from scripts.perturbgen.llm_rewriter import rewrite_variants_with_llm
from scripts.perturbgen.selector import DEFAULT_DOMAIN_TARGETS, sample_tasks, target_selection_report
from scripts.perturbgen.validator import validate_task_variants, validation_report, write_specs

log = logging.getLogger("perturbgen")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def _write_sample_report(path: Path, report: dict[str, object]) -> None:
    lines = [
        "# Perturbation Sample Report",
        "",
        f"- selected_total: {report.get('selected_total')}",
        "",
        "| domain | target | selected |",
        "|---|---:|---:|",
    ]
    domains = report.get("domains", {})
    if isinstance(domains, dict):
        for domain, item in sorted(domains.items()):
            if not isinstance(item, dict):
                continue
            lines.append(
                f"| {domain} | {item.get('target')} | {item.get('selected')} |"
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _domain_targets_for_args(args: argparse.Namespace) -> dict[str, int]:
    if args.domain == "all":
        return dict(DEFAULT_DOMAIN_TARGETS)
    if args.domain not in DEFAULT_DOMAIN_TARGETS:
        raise ValueError(
            f"Unknown domain {args.domain!r}. Use one of {sorted(DEFAULT_DOMAIN_TARGETS)} or all."
        )
    return {args.domain: DEFAULT_DOMAIN_TARGETS[args.domain]}


def _load_env_for_hybrid() -> None:
    try:
        from dotenv import load_dotenv
    except Exception:  # noqa: BLE001
        log.warning("python-dotenv is unavailable; hybrid generation will rely on existing environment variables")
        return
    load_dotenv(PROJECT_ROOT / ".env")


def run_pipeline(args: argparse.Namespace) -> None:
    if args.generator == "hybrid":
        _load_env_for_hybrid()

    batch_id = args.batch_id or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    staging_dir = PROJECT_ROOT / "scripts" / "_staging" / "perturbations" / batch_id
    staging_dir.mkdir(parents=True, exist_ok=True)
    output_dir = Path(args.output_dir) if args.output_dir else PROJECT_ROOT / "masp_bench" / "perturbation_specs"

    log.info("Building perturbation attack surface...")
    attack_surface = build_attack_surface(PROJECT_ROOT)
    _write_json(staging_dir / "attack_surface.json", attack_surface)
    log.info("Found %d tasks", attack_surface["total_tasks"])

    selected, sample_report = sample_tasks(
        attack_surface,
        domain_targets=_domain_targets_for_args(args),
        seed=args.seed,
    )
    _write_json(staging_dir / "sample.json", selected)
    _write_json(staging_dir / "sample_report.json", sample_report)
    _write_sample_report(staging_dir / "sample_report.md", sample_report)
    log.info("Selected %d tasks", len(selected))

    target_report = target_selection_report(selected)
    _write_json(staging_dir / "target_candidates.json", target_report)

    variants_by_task: dict[str, list[dict[str, object]]] = {}
    deterministic_variants_by_task: dict[str, list[dict[str, object]]] = {}
    rewrite_reports_by_task: dict[str, list[dict[str, object]]] = {}
    specs = []
    errors_by_task: dict[str, list[str]] = {}
    catalogs = attack_surface["tool_catalogs"]
    task_path_by_id = {str(task["task_id"]): str(task["path"]) for task in selected}

    for task in selected:
        variants = generate_variants_for_task(task)
        deterministic_variants_by_task[str(task["task_id"])] = [
            variant.model_dump(mode="json") for variant in variants
        ]
        if args.generator == "hybrid":
            variants, rewrite_report = rewrite_variants_with_llm(
                task=task,
                variants=variants,
                model=args.model,
                max_output_tokens=args.llm_max_output_tokens,
            )
            rewrite_reports_by_task[str(task["task_id"])] = rewrite_report
        variants_by_task[str(task["task_id"])] = [
            variant.model_dump(mode="json") for variant in variants
        ]
        spec, errors = validate_task_variants(
            task=task,
            variants=variants,
            catalog=catalogs[str(task["domain"])],
        )
        specs.append(spec)
        errors_by_task[str(task["task_id"])] = errors

    _write_json(staging_dir / "deterministic_variants_raw.json", deterministic_variants_by_task)
    if rewrite_reports_by_task:
        _write_json(staging_dir / "llm_rewrite_report.json", rewrite_reports_by_task)
    _write_json(staging_dir / "generated_variants_raw.json", variants_by_task)
    validation = validation_report(errors_by_task)
    _write_json(staging_dir / "validation_report.json", validation)

    if not validation["passed"]:
        log.warning("Validation failed for %d tasks", validation["failed_tasks"])
        if not args.allow_invalid:
            raise SystemExit(
                f"Perturbation validation failed. See {staging_dir / 'validation_report.json'}"
            )

    if args.dry_run:
        log.info("[dry-run] Skipping final spec write. Staging: %s", staging_dir)
        return

    write_report = write_specs(
        specs=specs,
        output_dir=output_dir,
        task_path_by_id=task_path_by_id,
    )
    write_report["schema_version"] = SCHEMA_VERSION
    _write_json(staging_dir / "write_report.json", write_report)
    log.info(
        "Wrote %d specs, %d required variants, %d optional variants to %s",
        write_report["spec_count"],
        write_report["required_variants"],
        write_report["optional_variants"],
        output_dir,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate MASP safety perturbation specs")
    parser.add_argument("--domain", default="all", help="Domain name or all")
    parser.add_argument("--batch-id", default=None)
    parser.add_argument("--seed", default="masp-l3-perturb-v1")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--generator", choices=["template", "hybrid"], default="template")
    parser.add_argument("--model", default="gpt-5.4")
    parser.add_argument("--llm-max-output-tokens", type=int, default=1200)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-invalid", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    _setup_logging(args.verbose)
    run_pipeline(args)


if __name__ == "__main__":
    main()
