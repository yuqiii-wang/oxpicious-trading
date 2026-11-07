"""llm_agents.deep_agents.tools.plan_tools.kits — the plan kits, what
the runner and the delegation hooks mount per conversation role.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, List, Optional, Union

from llm_agents.deep_agents.planning.authorities import PlanAuthority
from llm_agents.deep_agents.tools.base import Tool, ToolKit
from llm_agents.deep_agents.tools.plan_tools.addenda import (
    SUBAGENT_PLAN_ADDENDUM,
)
from llm_agents.deep_agents.tools.plan_tools.tick import UpdatePlanStepTool
from llm_agents.deep_agents.tools.plan_tools.update_plan import (
    SubUpdatePlanTool, UpdatePlanTool,
)
from llm_agents.deep_agents.tools.thread_search import ThreadSearchTool


class MainPlanToolKit(ToolKit):
    """The MAIN-lane kit of the step graph: ``update_plan`` (a step's
    lifecycle is RUNNER-side bookkeeping — the node ticks its own state
    row; the model's plan tool is the rework of the not-yet-run part:
    adds, revise, removes, fork termination) + ``search_thread_context``
    (the thread-internal LLM-traffic search — every level's kit carries
    it, thread-scoped by construction).

    *plan_lock* serializes the main-lane update when several main
    conversations run CONCURRENTLY (the runner's parallel wave of
    inline steps); None on the plain sequential lane."""

    def __init__(self, thread_id: str,
                 llm_context_id: Optional[str],
                 cancel_runs: Optional[Callable[
                     [Optional[List[str]]],
                     Union[List[str], Any]]],
                 plan_lock: Optional["asyncio.Lock"] = None, *,
                 descriptions: Optional[Dict[str, str]] = None) -> None:
        super().__init__(descriptions)
        self._authority = PlanAuthority(
            thread_id=thread_id, llm_context_id=llm_context_id,
            cancel_runs=cancel_runs)
        self._plan_lock = plan_lock

    def tools(self) -> List[Tool]:
        return [
            UpdatePlanTool(
                self._authority, self._plan_lock,
                description=self.db_description(UpdatePlanTool.db_key)),
            ThreadSearchTool(
                self._authority.thread_id,
                description=self.db_description(ThreadSearchTool.db_key)),
        ]


class ForkPlanToolKit(ToolKit):
    """One spawned child's plan kit: the SUB authority bound to its
    fork state plus the tick/update tools, the thread-internal search
    (any-depth children search the WHOLE thread's traffic — the main
    lane's and their siblings' included), and the system-prompt
    addendum that teaches them. The delegation hooks stamp these on
    ``SubagentRequest.builtin_tools`` / ``.system``, so a child without
    a plan (degraded run) simply gets none."""

    def __init__(self, thread_id: str, fork_state_id: int,
                 llm_context_id: Optional[str], *,
                 descriptions: Optional[Dict[str, str]] = None) -> None:
        super().__init__(descriptions)
        self._authority = PlanAuthority(
            thread_id=thread_id, fork_state_id=fork_state_id,
            llm_context_id=llm_context_id)

    def tools(self) -> List[Tool]:
        return [
            UpdatePlanStepTool(
                self._authority,
                description=self.db_description(
                    UpdatePlanStepTool.db_key)),
            SubUpdatePlanTool(
                self._authority,
                description=self.db_description(
                    SubUpdatePlanTool.db_key)),
            ThreadSearchTool(
                self._authority.thread_id,
                description=self.db_description(ThreadSearchTool.db_key)),
        ]

    def prompt_addendum(self) -> str:
        return SUBAGENT_PLAN_ADDENDUM
