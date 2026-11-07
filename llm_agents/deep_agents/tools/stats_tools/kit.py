"""llm_agents.deep_agents.tools.stats_tools.kit — ``StatsToolSet``,
the kit the runner mounts on every step conversation and the
delegation hooks stamp on every spawned child: the six frequently-used
study calculations as direct tools (no sandbox round-trip for the
standard reads).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from llm_agents.deep_agents.tools.base import Tool, ToolKit
from llm_agents.deep_agents.tools.stats_tools.flow import (
    StatsRangeTool, StatsVolumeTool,
)
from llm_agents.deep_agents.tools.stats_tools.momentum import (
    StatsMacdTool, StatsRocTool, StatsRsiTool,
)
from llm_agents.deep_agents.tools.stats_tools.trend import StatsMaTool


class StatsToolSet(ToolKit):
    """The six ``stats_*`` study tools (the kit carries no system-
    prompt addendum — the schemas carry the teaching, like the plan
    kits).

    ``descriptions`` (the run's resolved ``llm.ai_prompts`` tool
    rows, prompt_type 4) overrides each tool's schema description with
    its DB row's instruction — fail-soft to the built-in constants."""

    def __init__(self, *,
                 descriptions: Optional[Dict[str, str]] = None) -> None:
        super().__init__(descriptions)

    def tools(self) -> List[Tool]:
        return [
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

    @staticmethod
    def schemas() -> List[Dict[str, Any]]:
        """The six schemas WITHOUT any state (the CLI's --list-tools —
        description overrides need a run's descriptions map; the plain
        constants are the no-DB form)."""
        return [tool_cls().schema for tool_cls in (
            StatsMaTool, StatsRsiTool, StatsMacdTool, StatsRocTool,
            StatsVolumeTool, StatsRangeTool)]
