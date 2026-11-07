"""llm_agents.deep_agents.supervisor.tools — ``SupervisorToolKit``, the
watchdog's OWN diagnostic kit (``search_thread_context`` +
``kg_search``): the two evidence checks a failure diagnosis needs,
taken from the existing Tool classes so their type-4 ``ai_prompts``
description rows (db_keys ``search_thread_context|plan`` and
``kg_search|kg``) hook in unchanged — the kit applies them through the
same ``descriptions`` map the delegation hooks use for forks.

``search_thread_context`` re-reads the failed run's OWN recorded
traffic (what the failed node was asked, answered, fetched — the
ground truth behind its error); ``kg_search`` resolves the DB
semantics / prompt catalog when the verdict must judge whether the
PLAN's topology implicated the right tables or skills. No sandbox, no
writes — the supervisor stays read-only and observe-mode by
construction.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from llm_agents.deep_agents.tools.base import Tool, ToolKit
from llm_agents.deep_agents.tools.kg_tools.kit import KgSearchTool
from llm_agents.deep_agents.tools.thread_search import ThreadSearchTool

# The kit's db_keys (the type-4 rows fetch_supervisor_prompts filters
# to and this kit's db_description lookups).
KIT_DB_KEYS = (
    ThreadSearchTool.db_key,
    KgSearchTool.db_key,
)


class SupervisorToolKit(ToolKit):
    """The supervisor watchdog's diagnostic kit — closed over ONE
    thread id (the run it watches). ``descriptions`` is the run's
    resolved type-4 map (fail-soft to the built-in constants), fetched
    by ``prompts.fetch_supervisor_prompts`` ONLY when the watchdog
    wakes."""

    def __init__(self, thread_id: str, *,
                 descriptions: Optional[Dict[str, str]] = None) -> None:
        super().__init__(descriptions)
        self._thread_id = thread_id

    def tools(self) -> List[Tool]:
        return [
            ThreadSearchTool(
                self._thread_id,
                description=self.db_description(ThreadSearchTool.db_key)),
            KgSearchTool(
                description=self.db_description(KgSearchTool.db_key)),
        ]

    def prompt_addendum(self) -> str:
        """The supervisor's tool teaching — appended to its system
        prompt only when the kit is mounted."""
        return (
            "[Your tools]\n"
            "- search_thread_context: keyword-search THIS run's own "
            "recorded LLM traffic. USE IT to re-check what the failed "
            "node was asked, answered or fetched before judging its "
            "error — cite the recorded evidence, never guess.\n"
            "- kg_search: the DB-semantics (namespace 0) and prompt-"
            "catalog (namespace 1) semantic index. USE IT when the "
            "plan/topology verdict needs the tables' or skills' "
            "business meaning (e.g. a step aimed at the wrong table).\n"
            "Bounded: a few evidence checks at most — then produce the "
            "three labeled lines.")
