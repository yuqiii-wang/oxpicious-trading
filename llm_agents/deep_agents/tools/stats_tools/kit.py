"""llm_agents.deep_agents.tools.stats_tools.kit — ``StatsToolSet``,
the kit the runner mounts on every step conversation and the
delegation hooks stamp on every spawned child: the six frequently-used
study calculations as direct tools (no sandbox round-trip for the
standard reads).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from llm_agents.deep_agents.core.authorization import (
    ScopeViolation, TablePermission, gate_enabled,
)
from llm_agents.deep_agents.tools.base import Tool, ToolKit
from llm_agents.deep_agents.tools.stats_tools.bars import VIEWS
from llm_agents.deep_agents.tools.stats_tools.flow import (
    StatsRangeTool, StatsVolumeTool,
)
from llm_agents.deep_agents.tools.stats_tools.momentum import (
    StatsMacdTool, StatsRocTool, StatsRsiTool,
)
from llm_agents.deep_agents.tools.stats_tools.trend import StatsMaTool


class _GatedStatsTool(Tool):
    """One stats tool wrapped with the run's DB-table scope
    (core/authorization, 2026-11-13): the call's ``sec_type`` names the
    daily view it reads (:data:`VIEWS`) — an out-of-scope view rejects
    with the SAME [Scope] remedy text the loop's MCP gate produces,
    never raising. Every other argument passes through untouched."""

    def __init__(self, inner: Tool,
                 permission: TablePermission) -> None:
        super().__init__(description=inner.description)
        self._inner = inner
        self._permission = permission

    @property
    def name(self) -> str:  # type: ignore[override]
        return self._inner.name

    @property
    def parameters(self) -> Dict[str, Any]:  # type: ignore[override]
        return self._inner.parameters

    @property
    def db_key(self):  # type: ignore[override]
        return self._inner.db_key

    async def run(self, arguments: Dict[str, Any]) -> str:
        if gate_enabled():
            view = VIEWS.get(
                str(arguments.get("sec_type") or "index").strip().lower())
            if view is not None \
                    and not self._permission.allows(view):
                return str(ScopeViolation((view,), self._permission))
        return await self._inner.run(arguments)


class StatsToolSet(ToolKit):
    """The six ``stats_*`` study tools (the kit carries no system-
    prompt addendum — the schemas carry the teaching, like the plan
    kits).

    ``descriptions`` (the run's resolved ``llm.ai_prompts`` tool
    rows, prompt_type 4) overrides each tool's schema description with
    its DB row's instruction — fail-soft to the built-in constants.
    ``permission`` (core/authorization) wraps each tool with the run's
    table-scope gate (None = ungated, plain non-graph usage)."""

    def __init__(self, *,
                 descriptions: Optional[Dict[str, str]] = None,
                 permission: Optional[TablePermission] = None) -> None:
        super().__init__(descriptions)
        self._permission = permission

    def tools(self) -> List[Tool]:
        tools: List[Tool] = [
            StatsMaTool(
                description=self.db_description(StatsMaTool.db_key)),
            StatsRsiTool(
                description=self.db_description(StatsRsiTool.db_key)),
            StatsMacdTool(
                description=self.db_description(StatsMacdTool.db_key)),
            StatsRocTool(
                description=self.db_description(StatsRocTool.db_key)),
            StatsVolumeTool(
                description=self.db_description(
                    StatsVolumeTool.db_key)),
            StatsRangeTool(
                description=self.db_description(
                    StatsRangeTool.db_key)),
        ]
        if self._permission is not None:
            tools = [_GatedStatsTool(t, self._permission) for t in tools]
        return tools

    @staticmethod
    def schemas() -> List[Dict[str, Any]]:
        """The six schemas WITHOUT any state (the CLI's --list-tools —
        description overrides need a run's descriptions map; the plain
        constants are the no-DB form)."""
        return [tool_cls().schema for tool_cls in (
            StatsMaTool, StatsRsiTool, StatsMacdTool, StatsRocTool,
            StatsVolumeTool, StatsRangeTool)]
