"""Shared tool-dispatch cascade for framework adapters and MCP servers."""

from __future__ import annotations

from typing import Any


def dispatch_tool(
    tool_name: str,
    args: dict[str, Any],
    *,
    bank: Any = None,
    tool_def: Any = None,
    workspace: Any = None,
    perturbation_runtime: Any = None,
) -> str:
    """Execute a domain tool through the Bank→real→fixture→mock cascade.

    Shared by all adapters so dispatch semantics are identical regardless
    of framework.  The cascade order matches ``mcp_bank_server.py``:

      1. ``bank.dispatch(tool_name, args)`` — stateful SQLite backend
      2. ``dispatch_real_tool()`` — SDE domain real tool execution
      3. Fixture JSON lookup via ``tool_def.fixture_key``
      4. Static ``tool_def.mock_response`` fallback
    """
    if perturbation_runtime is not None and perturbation_runtime.should_short_circuit(
        tool_name,
        tool_def,
    ):
        return str(perturbation_runtime.short_circuit_response(tool_name))

    result: str

    # 1. Bank dispatch (authoritative when available)
    if bank is not None:
        try:
            result = str(bank.dispatch(tool_name, args))
        except Exception as exc:
            result = f"Error dispatching {tool_name}: {exc}"
        if perturbation_runtime is not None:
            result = perturbation_runtime.postprocess_tool_result(
                tool_name,
                args,
                result,
                tool_def=tool_def,
            )
        return result

    # 2. Real backend (SDE domain). Real tools must never silently fall back to
    # fixtures/mocks, otherwise SDE tasks can appear to run while touching no
    # real workspace.
    if tool_def is not None and tool_def.backend_type == "real":
        if workspace is None:
            return (
                f"ERROR: real backend tool {tool_name!r} requires workspace_path "
                "but none was provided"
            )
        from multi_agent.sde_backend import dispatch_real_tool

        result = str(dispatch_real_tool(tool_name, args, workspace))
        if perturbation_runtime is not None:
            result = perturbation_runtime.postprocess_tool_result(
                tool_name,
                args,
                result,
                tool_def=tool_def,
            )
        return result

    # 3. Fixture lookup
    if tool_def is not None:
        fixture_data = tool_def.fixture_data
        fixture_key = tool_def.fixture_key
        if fixture_data and fixture_key:
            lookup_val = str(args.get(fixture_key, ""))
            keys = [k.strip() for k in lookup_val.split(",") if k.strip()] or [lookup_val]
            header = fixture_data.get("_header", "")
            footer = fixture_data.get("_footer", "")
            default = fixture_data.get("_default", "No data found.")
            lines: list[str] = []
            if header:
                lines.append(header)
            for key in keys:
                entry = fixture_data.get(key)
                lines.append(entry if entry is not None else f"{key}: {default}")
            if footer:
                lines.append(footer)
            result = "\n".join(lines)
            if perturbation_runtime is not None:
                result = perturbation_runtime.postprocess_tool_result(
                    tool_name,
                    args,
                    result,
                    tool_def=tool_def,
                )
            return result

        # 4. Mock fallback
        result = tool_def.mock_response or f"{tool_name}: OK"
        if perturbation_runtime is not None:
            result = perturbation_runtime.postprocess_tool_result(
                tool_name,
                args,
                result,
                tool_def=tool_def,
            )
        return result

    result = f"{tool_name}: OK"
    if perturbation_runtime is not None:
        result = perturbation_runtime.postprocess_tool_result(tool_name, args, result)
    return result
