from __future__ import annotations

from masp_bench.frameworks.mcp_bank_server import _load_runtime_components


def test_load_runtime_components_uses_sasp_bank_and_catalog() -> None:
    bank, catalog = _load_runtime_components("finance", "sasp")

    assert "sasp_bench/fixtures/finance" in str(bank.seed_dir)
    assert catalog.domain == "finance"
    assert any(tool.name == "get_portfolio_summary" for tool in catalog.tools)


def test_load_runtime_components_defaults_to_masp_bank_and_catalog() -> None:
    bank, catalog = _load_runtime_components("finance", "masp")

    assert "masp_bench/fixtures/finance" in str(bank.seed_dir)
    assert catalog.domain == "finance"
    assert any(tool.name == "get_portfolio_summary" for tool in catalog.tools)
