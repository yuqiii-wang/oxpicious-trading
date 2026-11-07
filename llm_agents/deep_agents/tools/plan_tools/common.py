"""llm_agents.deep_agents.tools.plan_tools.common — the
authority-binding tool base class shared by the plan tools (the
lifecycle guard, the rendered-tree echo and the SUB-scope add body
are PlanAuthority methods, 2026-11-07)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from llm_agents.deep_agents.planning.authorities import (
    TICKABLE_FROM, PlanAuthority, render_added, render_plan,
)
from llm_agents.deep_agents.planning.store import (StateRow, PLAN_STORE)
from llm_agents.deep_agents.tools.base import Tool


class _PlanAuthorityTool(Tool):
    """Base of the plan tools: each closes over ONE conversation's
    authority (the permission model) — the model never sees a scope
    choice."""

    def __init__(self, authority: PlanAuthority, *,
                 description: Optional[str] = None) -> None:
        super().__init__(description=description)
        self._authority = authority
