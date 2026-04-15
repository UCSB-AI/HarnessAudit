"""Shared tool invocation runtime across MAS framework adapters."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from masp_bench.schemas.task import ToolDef

from .base import DefenseHook, DefenseMode, ToolInterceptionDecision


@dataclass(slots=True)
class ToolInvokeOutcome:
    """Normalized result from tool runtime dispatch."""

    output: str
    blocked: bool = False
    defense_meta: dict[str, Any] = field(default_factory=dict)


class ToolRuntime:
    """Run catalog tools through shared dispatch logic and defense hooks."""

    def __init__(
        self,
        *,
        run_id: str,
        defense_mode: DefenseMode,
        bank: Any,
        workspace: Any,
        defense_hook: DefenseHook | None = None,
    ) -> None:
        self.run_id: str = run_id
        self.defense_mode: DefenseMode = defense_mode
        self.bank: Any = bank
        self.workspace: Any = workspace
        self.defense_hook: DefenseHook | None = defense_hook

    @staticmethod
    def parse_args_json(args_json: str | None) -> dict[str, Any]:
        """Parse framework tool-argument JSON safely."""
        try:
            return json.loads(args_json) if args_json else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    async def invoke(
        self,
        *,
        tool_def: ToolDef,
        tool_args: dict[str, Any],
        caller_role: str = "",
    ) -> ToolInvokeOutcome:
        """Invoke one tool through bank/real/fixture/static dispatch cascade."""
        decision = ToolInterceptionDecision(allow=True, metadata={})
        if self.defense_hook:
            decision = self.defense_hook.before_tool_call(
                run_id=self.run_id,
                defense_mode=self.defense_mode,
                agent_role=caller_role or "unknown",
                tool_name=tool_def.name,
                tool_args=tool_args,
            )

        if not decision.allow:
            blocked_output = (
                decision.override_result
                or f"{tool_def.name}: blocked by enforced defense policy"
            )
            return ToolInvokeOutcome(
                output=blocked_output,
                blocked=True,
                defense_meta=decision.metadata,
            )

        if self.bank is not None:
            if caller_role and hasattr(self.bank, "set_current_agent"):
                try:
                    self.bank.set_current_agent(caller_role)
                except Exception:
                    pass
            result = self.bank.dispatch(tool_def.name, tool_args)
            return ToolInvokeOutcome(output=str(result), defense_meta=decision.metadata)

        if tool_def.backend_type == "real" and self.workspace is not None:
            from masp_bench.sde_backend import dispatch_real_tool

            result = dispatch_real_tool(tool_def.name, tool_args, Path(self.workspace.path))
            return ToolInvokeOutcome(output=str(result), defense_meta=decision.metadata)

        fixture_data = tool_def.fixture_data
        fixture_key = tool_def.fixture_key
        if fixture_data and fixture_key:
            lookup_val = str(tool_args.get(fixture_key, ""))
            keys = [k.strip() for k in lookup_val.split(",") if k.strip()]
            if not keys:
                keys = [lookup_val]

            header = fixture_data.get("_header", "")
            footer = fixture_data.get("_footer", "")
            default = fixture_data.get("_default", "No data found.")
            lines: list[str] = []
            if header:
                lines.append(header)
            for key in keys:
                entry = fixture_data.get(key)
                if entry is not None:
                    lines.append(entry)
                else:
                    lines.append(f"{key}: {default}")
            if footer:
                lines.append(footer)
            return ToolInvokeOutcome(output="\n".join(lines), defense_meta=decision.metadata)

        return ToolInvokeOutcome(
            output=tool_def.mock_response or f"{tool_def.name}: OK",
            defense_meta=decision.metadata,
        )

