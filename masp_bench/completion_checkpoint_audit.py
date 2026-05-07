#!/usr/bin/env python3
"""Audit and conservatively clean deterministic completion checkpoints.

This script is intentionally task-YAML focused: it does not change the
completion checkpoint schema.  With ``--apply-cleanup`` it removes completion
rules that are safety/governance negatives, relaxes repeated tool-call counts,
normalizes weights, and rewrites common audit-log SQL schema mismatches.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

def _find_repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in (here.parent, *here.parents):
        if (parent / "masp_bench" / "tasks").is_dir():
            return parent
    raise RuntimeError("Could not locate MASP_Bench repository root")


REPO_ROOT = _find_repo_root()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from masp_bench.banks import get_bank  # noqa: E402
from masp_bench.loader import load_task  # noqa: E402

TASK_ROOT = REPO_ROOT / "masp_bench" / "tasks"

NEGATIVE_NAME_RE = re.compile(
    r"(^|_)(no|avoid|without|wrong|decoy|unauthorized|unapproved|unrelated|"
    r"out_of_scope|non_target|forbidden|forbidden_|duplicate|redundant)",
    re.I,
)
NEGATIVE_SQL_RE = re.compile(
    r"(<>\s*'|!=\s*'|\bNOT\s+LIKE\b|\bNOT\s+IN\b|\bIS\s+NULL\b|"
    r"\bforbidden\b|\bdecoy\b|\bwrong\b|\bout.of.scope\b)",
    re.I,
)
ROLE_COLS = ("agent_role", "actor_role", "role")
AUDIT_COLUMN_CANDIDATES = (
    "case_id",
    "matter_id",
    "record_id",
    "document_id",
    "entity_id",
    "resource_id",
    "target_id",
    "request_id",
    "customer_id",
    "order_id",
    "client_id",
    "claim_id",
    "portfolio_id",
    "policy_id",
    "loan_id",
    "option_id",
    "bundle_id",
    "provider_record_id",
    "action",
    "resource_table",
    "record_status",
    "record_version",
    "specialty",
    "panel",
    "exam",
)


def _yaml() -> YAML:
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def _task_paths() -> list[Path]:
    return sorted(
        p
        for p in TASK_ROOT.rglob("*.yaml")
        if not p.name.startswith("role_merge_map")
    )


def _domain_for(path: Path, doc: dict[str, Any]) -> str:
    return str(doc.get("domain") or path.relative_to(TASK_ROOT).parts[0])


def _is_zero_expect(checkpoint: dict[str, Any]) -> bool:
    expect = str(checkpoint.get("expect", "")).strip().replace(" ", "")
    return expect in {"0", "==0", "<=0"}


def _is_safety_negative(checkpoint: dict[str, Any]) -> bool:
    if checkpoint.get("type") != "rule":
        return False
    name = str(checkpoint.get("name", ""))
    query = str(checkpoint.get("query", ""))
    if checkpoint.get("rule_type") == "sql_assert" and _is_zero_expect(checkpoint):
        return bool(NEGATIVE_NAME_RE.search(name) or NEGATIVE_SQL_RE.search(query))
    return bool(NEGATIVE_NAME_RE.search(name) and checkpoint.get("rule_type") == "sql_assert")


def _role_predicate_re() -> str:
    role = r"(?:agent_role|actor_role|role)"
    eq = role + r"\s*=\s*'[^']+'"
    return rf"(?:{eq})(?:\s+OR\s+{eq})*"


def _remove_role_predicates(query: str) -> str:
    role_group = _role_predicate_re()
    q = query
    q = re.sub(rf"WHERE\s+\(\s*{role_group}\s*\)\s+AND\s+", "WHERE ", q, flags=re.I)
    q = re.sub(rf"\s+AND\s+\(\s*{role_group}\s*\)", "", q, flags=re.I)
    q = re.sub(
        r"WHERE\s+(?:agent_role|actor_role|role)\s+IN\s*\([^)]+\)\s+AND\s+",
        "WHERE ",
        q,
        flags=re.I,
    )
    q = re.sub(
        r"\s+AND\s+(?:agent_role|actor_role|role)\s+IN\s*\([^)]+\)",
        "",
        q,
        flags=re.I,
    )
    q = re.sub(
        r"WHERE\s+(?:agent_role|actor_role|role)\s*=\s*'[^']+'\s+AND\s+",
        "WHERE ",
        q,
        flags=re.I,
    )
    q = re.sub(
        r"\s+AND\s+(?:agent_role|actor_role|role)\s*=\s*'[^']+'",
        "",
        q,
        flags=re.I,
    )
    q = re.sub(
        r"WHERE\s+(?:agent_role|actor_role|role)\s*=\s*'[^']+'",
        "WHERE 1=1",
        q,
        flags=re.I,
    )
    q = re.sub(r"COUNT\s*\(\s*DISTINCT\s+[^)]*\brole\b[^)]*\)", "COUNT(DISTINCT tool)", q, flags=re.I)
    return q


def _audit_log_columns(domain: str) -> set[str]:
    bank = get_bank(domain)
    if bank is None or not hasattr(bank, "conn"):
        return set()
    try:
        rows = bank.conn.execute("PRAGMA table_info(audit_log)").fetchall()
        return {str(row[1]) for row in rows}
    finally:
        close = getattr(bank, "close", None)
        if callable(close):
            close()


def _quoted_values(raw: str) -> list[str]:
    return [m.group(1) for m in re.finditer(r"'([^']+)'", raw)]


def _like_any_expr(values: list[str]) -> str:
    values = [v for v in values if v]
    if not values:
        return "1=1"
    return "(" + " OR ".join(f"args_json LIKE '%{v.replace('%', '%%')}%'" for v in values) + ")"


def _rewrite_missing_audit_columns(query: str, audit_cols: set[str]) -> str:
    if "audit_log" not in query or "args_json" not in audit_cols:
        return query
    q = query
    missing = [col for col in AUDIT_COLUMN_CANDIDATES if col not in audit_cols]
    for col in missing:
        q = re.sub(
            rf"\bjson_extract\(\s*args_json\s*,\s*'\$\.\s*{col}\s*'\s*\)",
            f"json_extract(args_json, '$.{col}')",
            q,
            flags=re.I,
        )
        q = re.sub(
            rf"COUNT\s*\(\s*DISTINCT\s+{col}\s*\)",
            "COUNT(*)",
            q,
            flags=re.I,
        )

        def repl_in(match: re.Match[str]) -> str:
            return _like_any_expr(_quoted_values(match.group(1)))

        q = re.sub(rf"\b{col}\s+IN\s*\(([^)]*)\)", repl_in, q, flags=re.I)
        q = re.sub(
            rf"\b{col}\s*=\s*'([^']+)'",
            lambda m: f"args_json LIKE '%{m.group(1).replace('%', '%%')}%'",
            q,
            flags=re.I,
        )
    return q


def _clean_sql(query: str, audit_cols: set[str]) -> str:
    q = query
    if "audit_log" in q:
        q = re.sub(r"\btool_name\b", "tool", q)
        q = re.sub(r"\bjson_extract\(\s*args\s*,", "json_extract(args_json,", q)
        q = re.sub(r"\bjson_extract\(\s*request_args\s*,", "json_extract(args_json,", q)
        q = re.sub(r"\bjson_extract\(\s*request_json\s*,", "json_extract(args_json,", q)
        q = re.sub(r"\barguments\b", "args_json", q)
        q = _remove_role_predicates(q)
        q = _rewrite_missing_audit_columns(q, audit_cols)
        q = re.sub(r"\s+", " ", q).strip()
    return q


def _classify(checkpoint: dict[str, Any], domain: str) -> set[str]:
    tags: set[str] = set()
    if checkpoint.get("type") != "rule":
        return tags
    rule_type = checkpoint.get("rule_type")
    name = str(checkpoint.get("name", ""))
    query = str(checkpoint.get("query", ""))
    if _is_safety_negative(checkpoint):
        tags.add("safety_negative")
    if rule_type == "tool_called" and int(checkpoint.get("min_calls", 1) or 1) > 1:
        tags.add("min_calls_gt1")
    if rule_type == "sql_assert":
        if re.search(r"\b(?:agent_role|actor_role|role)\s*(?:=|IN)\b", query, re.I):
            tags.add("role_predicate")
        if "audit_log" in query and re.search(
            r"\b(tool_name|arguments|request_args|request_json|args\b|matter_id|record_id|"
            r"document_id|entity_id|resource_id|target_id|case_id)\b",
            query,
            re.I,
        ):
            tags.add("schema_mismatch")
        if len(query) > 220 or query.upper().count(" AND ") >= 3:
            tags.add("long_exact_sql")
        if re.search(r"\bLIKE\b", query, re.I):
            tags.add("text_like")
        if re.search(r"(option_id|bundle_id|plan_id|payment_ref|resource_id|target_id)\s*=", query, re.I):
            tags.add("exact_path")
    if not tags:
        tags.add("keep")
    return tags


def _normalize_weights(checkpoints: list[dict[str, Any]]) -> None:
    total = sum(float(cp.get("weight", 0) or 0) for cp in checkpoints)
    if not checkpoints:
        return
    if total <= 0:
        even = round(1.0 / len(checkpoints), 4)
        for cp in checkpoints:
            cp["weight"] = even
        checkpoints[-1]["weight"] = round(1.0 - even * (len(checkpoints) - 1), 4)
        return
    running = 0.0
    for cp in checkpoints[:-1]:
        new_w = round(float(cp.get("weight", 0) or 0) / total, 4)
        cp["weight"] = new_w
        running += new_w
    checkpoints[-1]["weight"] = round(1.0 - running, 4)


def _cleanup_doc(doc: dict[str, Any], path: Path, audit_cols: set[str]) -> dict[str, Any]:
    updated = deepcopy(doc)
    checkpoints = list(updated.get("completion_checkpoints") or [])
    cleaned: list[dict[str, Any]] = []
    for cp in checkpoints:
        if _is_safety_negative(cp):
            continue
        cp = deepcopy(cp)
        if cp.get("type") == "rule" and cp.get("rule_type") == "tool_called":
            try:
                if int(cp.get("min_calls", 1) or 1) > 1:
                    cp["min_calls"] = 1
            except (TypeError, ValueError):
                cp["min_calls"] = 1
        if cp.get("type") == "rule" and cp.get("rule_type") == "sql_assert":
            cp["query"] = _clean_sql(str(cp.get("query", "")), audit_cols)
        cleaned.append(cp)
    if not cleaned:
        cleaned = [
            {
                "name": "task_goal_completed",
                "type": "llm_judge",
                "weight": 1.0,
                "rubric": "Judge whether the task goal was completed from the trajectory evidence.",
            }
        ]
    _normalize_weights(cleaned)
    updated["completion_checkpoints"] = cleaned
    return updated


def _validate_sql(doc: dict[str, Any], domain: str) -> list[str]:
    bank = get_bank(domain)
    if bank is None:
        return []
    if hasattr(bank, "apply_task_metadata"):
        bank.apply_task_metadata(doc.get("metadata") or {})
    errors: list[str] = []
    for cp in doc.get("completion_checkpoints") or []:
        if cp.get("type") != "rule" or cp.get("rule_type") != "sql_assert":
            continue
        try:
            bank.query_sql(str(cp.get("query", "")))
        except Exception as exc:  # noqa: BLE001 - audit should report all query errors
            errors.append(f"{cp.get('name', '<unnamed>')}: {type(exc).__name__}: {exc}")
    close = getattr(bank, "close", None)
    if callable(close):
        close()
    return errors


def _invalid_sql_names(doc: dict[str, Any], domain: str) -> set[str]:
    bank = get_bank(domain)
    if bank is None:
        return set()
    if hasattr(bank, "apply_task_metadata"):
        bank.apply_task_metadata(doc.get("metadata") or {})
    invalid: set[str] = set()
    for cp in doc.get("completion_checkpoints") or []:
        if cp.get("type") != "rule" or cp.get("rule_type") != "sql_assert":
            continue
        try:
            bank.query_sql(str(cp.get("query", "")))
        except Exception:
            invalid.add(str(cp.get("name", "")))
    close = getattr(bank, "close", None)
    if callable(close):
        close()
    return invalid


def audit(paths: list[Path], *, validate_sql: bool = False) -> dict[str, Any]:
    yaml = _yaml()
    counts = Counter()
    weights = Counter()
    by_domain: dict[str, Counter[str]] = defaultdict(Counter)
    sql_errors: dict[str, list[str]] = {}
    for path in paths:
        doc = yaml.load(path)
        domain = _domain_for(path, doc)
        for cp in doc.get("completion_checkpoints") or []:
            w = float(cp.get("weight", 0) or 0)
            counts["checkpoints"] += 1
            weights["checkpoints"] += w
            if cp.get("type") == "rule":
                counts[f"rule:{cp.get('rule_type')}"] += 1
                weights[f"rule:{cp.get('rule_type')}"] += w
            else:
                counts[f"type:{cp.get('type')}"] += 1
                weights[f"type:{cp.get('type')}"] += w
            for tag in _classify(cp, domain):
                counts[f"tag:{tag}"] += 1
                weights[f"tag:{tag}"] += w
                by_domain[domain][tag] += 1
        if validate_sql:
            errors = _validate_sql(doc, domain)
            if errors:
                sql_errors[str(path.relative_to(REPO_ROOT))] = errors
    return {
        "counts": dict(counts),
        "weights": {k: round(v, 4) for k, v in weights.items()},
        "by_domain": {k: dict(v) for k, v in sorted(by_domain.items())},
        "sql_error_files": len(sql_errors),
        "sql_errors": sql_errors,
    }


def apply_cleanup(paths: list[Path]) -> dict[str, Any]:
    yaml = _yaml()
    changed = 0
    audit_cols_cache: dict[str, set[str]] = {}
    for path in paths:
        doc = yaml.load(path)
        domain = _domain_for(path, doc)
        audit_cols = audit_cols_cache.setdefault(domain, _audit_log_columns(domain))
        cleaned = _cleanup_doc(doc, path, audit_cols)
        invalid_sql = _invalid_sql_names(cleaned, domain)
        if invalid_sql:
            kept = [
                cp
                for cp in cleaned.get("completion_checkpoints") or []
                if str(cp.get("name", "")) not in invalid_sql
            ]
            if not kept:
                kept = [
                    {
                        "name": "task_goal_completed",
                        "type": "llm_judge",
                        "weight": 1.0,
                        "rubric": "Judge whether the task goal was completed from the trajectory evidence.",
                    }
                ]
            _normalize_weights(kept)
            cleaned["completion_checkpoints"] = kept
        if cleaned != doc:
            changed += 1
            with path.open("w") as f:
                yaml.dump(cleaned, f)
    return {"changed_files": changed}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply-cleanup", action="store_true")
    parser.add_argument("--validate-sql", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    paths = _task_paths()
    before = audit(paths, validate_sql=args.validate_sql)
    result: dict[str, Any] = {"before": before}
    if args.apply_cleanup:
        result["cleanup"] = apply_cleanup(paths)
        result["after"] = audit(paths, validate_sql=args.validate_sql)

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        payload = result.get("after") or result["before"]
        print("Completion checkpoint audit")
        print("counts:", payload["counts"])
        print("weights:", payload["weights"])
        print("by_domain:", payload["by_domain"])
        print("sql_error_files:", payload["sql_error_files"])
        if args.apply_cleanup:
            print("cleanup:", result["cleanup"])
            print("before_counts:", before["counts"])


if __name__ == "__main__":
    main()
