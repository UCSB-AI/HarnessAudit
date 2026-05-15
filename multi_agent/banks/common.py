"""Shared pure helpers for SQLite-backed domain banks."""

from __future__ import annotations

from typing import Any, Iterable


_ALL_QUERY_TOKENS = {"all", "full", "complete"}


def stringify(value: Any) -> str:
    """Coerce a nullable value to a string without turning None into "None"."""

    return str(value) if value is not None else ""


def normalize_query_text(value: Any) -> str:
    """Normalize loose task/tool query text for bank-side substring matching."""

    return (
        str(value or "")
        .strip()
        .lower()
        .replace("-", " ")
        .replace("_", " ")
    )


def parse_csv_values(value: Any) -> list[str]:
    """Parse comma-separated or list-like tool argument values."""

    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [part.strip() for part in str(value).split(",") if part.strip()]


def first_nonempty(*values: Any) -> str:
    """Return the first non-empty scalar, expanding one level of lists."""

    for value in values:
        if isinstance(value, list):
            for item in value:
                text = str(item).strip()
                if text:
                    return text
            continue
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _normalized_queries(query: Any) -> list[str]:
    return [
        normalized
        for part in parse_csv_values(query)
        if (normalized := normalize_query_text(part)) not in _ALL_QUERY_TOKENS
    ]


def filter_rows_by_query(
    rows: Iterable[Any],
    query: Any,
    *,
    fields: tuple[str, ...],
    token_fallback: bool = False,
) -> list[Any]:
    """Filter sqlite rows by loose substring matching across selected fields."""

    row_list = list(rows)
    queries = parse_csv_values(query)
    if not queries:
        return row_list

    normalized_queries = _normalized_queries(query)
    if not normalized_queries:
        return row_list

    selected: list[Any] = []
    for row in row_list:
        haystacks = [normalize_query_text(row[field]) for field in fields]
        if any(
            q in haystack or haystack in q
            for q in normalized_queries
            for haystack in haystacks
        ):
            selected.append(row)

    if selected or not token_fallback:
        return selected

    query_words: set[str] = set()
    for q in normalized_queries:
        for word in q.replace(".", " ").split():
            word = word.strip()
            if len(word) >= 3:
                query_words.add(word)
    if not query_words:
        return selected

    for row in row_list:
        combined = " ".join(normalize_query_text(row[field]) for field in fields)
        if any(word in combined for word in query_words):
            selected.append(row)
    return selected
