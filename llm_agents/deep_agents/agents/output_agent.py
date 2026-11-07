"""llm_agents.deep_agents.agents.output_agent — ``OutputAgent``: a
graph's FINAL OUTPUT NODE as an agent (2026-11-06, the three-graph
refactor). ONE agent == ONE graph node: the agent owns the output
node's identity (the ``output`` namespace, its ONE chained context)
and runs the node's synthesis conversation — a fresh step-flavored
conversation whose directive is FIXED (the graph's output charter, not
a plan step): it reads the accumulated findings bridge and produces
the graph's final document.

Two seats use it:

* the CUSTOM graph's output node — the findings synthesis, persisted
  as the kind ``graph-output`` context row
  (THE HANDOFF: what the RESULT graph's four reviews and final
  synthesis read as their input);
* the RESULT graph's output node — the run's final answer weighing
  the findings AND the four reviews, persisted as the kind
  ``final-result`` context row (the UI Result view's document).

No plan-mutation tools (there is no plan left to rework at an output
seat) and no subagent spawning (the node has no state row to parent
fork children under) — the stats / kg / prompt-fetch kits ride, so a
synthesis can still re-check a cited number or load a skill. No state
row is ticked: the seat is graph-level, not a plan step.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

from llm_agents.deep_agents.agents.base import NodeAgent
from llm_agents.deep_agents.core.loop import DeepAgent
from llm_agents.deep_agents.core.provider import ZhipuChatProvider

from llm_agents.deep_agents.agents.data_definitions import (
    OutputDeps,
)
logger = logging.getLogger("deep_agents.agents.output")




class OutputAgent(NodeAgent):
    """A graph's FINAL OUTPUT NODE — the fixed-directive synthesis
    conversation whose answer is the graph's output document."""

    node = "output"

    def __init__(
        self,
        provider: ZhipuChatProvider,
        *,
        deps: OutputDeps,
        thread_id: str,
        model: Optional[str] = None,
        writer_factory=None,
    ) -> None:
        super().__init__(
            provider.chat_complete, thread_id=thread_id, model=model,
            writer_factory=writer_factory)
        self._provider = provider
        self._deps = deps
        # The last run's ONE chained context id (set by :meth:`run`) —
        # the key the caller's context-row persistence stamps.
        self.last_context_id: Optional[str] = None

    async def run(self) -> Tuple[str, int, int]:
        """The output conversation: the node context, the DeepAgent
        call, the settle. Returns ``(synthesis, n_llm, n_tool)``; a
        failure returns the ``OUTPUT FAILED — …`` marker with zero
        counts (the caller records the fallback document — the run's
        always-answer contract holds at the graph boundary too).
        :attr:`last_context_id` carries the conversation's ONE chained
        context id for the caller's persistence (the graph-output
        handoff row keys to it)."""
        deps = self._deps
        writer = self.new_writer()
        self.last_context_id = writer.context_id
        await self.begin_node(writer)
        try:
            agent = deps.agent_factory(
                self._provider, deps.mcp,
                system=deps.system,
                builtin_tools=deps.make_tools(),
                sandbox=deps.sandbox(),
                **deps.common)
            with self.chain(writer):
                result = await agent.run(deps.build_message())
            await self.close_node(
                writer, "completed", n_llm_calls=result.steps,
                n_tool_calls=len(result.tool_calls),
                answer_chars=len(result.answer))
            return result.answer, result.steps, len(result.tool_calls)
        except Exception as exc:  # noqa: BLE001 — the run still answers
            logger.warning("output node conversation failed: %s", exc)
            await self.close_node(
                writer, "failed", error=f"{type(exc).__name__}: {exc}")
            return f"OUTPUT FAILED — {type(exc).__name__}: {exc}", 0, 0
