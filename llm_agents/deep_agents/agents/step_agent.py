"""llm_agents.deep_agents.agents.step_agent — ``StepAgent``: ONE
planned step's conversation as an agent (2026-11-06). ONE agent ==
ONE graph node: the agent owns the step node's identity (the
``step-<state_id>`` namespace, its ``llm.deep_agent_states`` row) and
its ONE chained context, and runs the inline hop's conversation (the
former ``runner._inline_hop`` body, verbatim, with the runner's
closures INJECTED as a :class:`StepDeps` so this module stays
langgraph-free).

THE RESUME (the node==agent payoff): :meth:`resume` re-runs the step
after the supervisor's ``retry-step`` verdict WITH its prior context —
the supervisor's instruction, the prior attempt's error, and the
pointer to the prior attempt's recorded traffic (the row's
``llm_context_id`` chain, reachable through the mounted
``search_thread_context`` tool) ride a ``[Resume — supervisor
instruction]`` block on the step message. No transcript replay — the
context-hygiene rule holds; the agent re-reads what it needs.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

from llm_agents.deep_agents.agents.base import NodeAgent
from llm_agents.deep_agents.core.loop import DeepAgent
from llm_agents.deep_agents.core.provider import ZhipuChatProvider
from llm_agents.deep_agents.planning.store import (StateRow, extract_critical, PLAN_STORE)

from llm_agents.deep_agents.agents.data_definitions import (
    StepDeps,
)
logger = logging.getLogger("deep_agents.agents.step")




class StepAgent(NodeAgent):
    """ONE planned step's conversation — the inline hop's agent."""

    node = "step"  # the item name composes to f"step-{state_id}"

    def __init__(
        self,
        provider: ZhipuChatProvider,
        *,
        deps: StepDeps,
        thread_id: str,
        model: Optional[str] = None,
        writer_factory,
    ) -> None:
        super().__init__(
            provider.chat_complete, thread_id=thread_id, model=model,
            writer_factory=writer_factory)
        self._provider = provider
        self._deps = deps

    async def run(
        self,
        step_row: StateRow,
        *,
        plan_lock: Optional[Any] = None,
        resume: Optional[Dict[str, str]] = None,
    ) -> Tuple[str, str, int, int]:
        """The inline hop: the node context, the in_progress tick, the
        DeepAgent conversation, the settle. Returns (status, finding,
        n_llm, n_tool) — the lane's tuple, unchanged. ``resume`` (the
        supervisor's instruction + the prior attempt's error + its
        chain id) switches the agent into resume mode. ``plan_lock``
        serializes update_plan writes inside a mixed wave — rides the
        plan kit through deps.make_tools."""
        deps = self._deps
        writer = self.new_writer()
        await self.begin_node(writer)
        self.state_id = step_row.state_id
        try:
            await PLAN_STORE.tick_step(step_row.state_id, "in_progress",
                            thread_id=self.thread_id, node=step_row.node,
                            llm_context_id=writer.context_id)
        except Exception as exc:  # noqa: BLE001 — bookkeeping
            logger.warning("step in_progress tick failed (%s): %s",
                           step_row.state_id, exc)
        try:
            broker = deps.make_broker(self.thread_id)
            hooks = deps.make_hooks(
                step_row, broker, writer.context_id)
            agent = deps.agent_factory(
                self._provider, deps.mcp,
                system=deps.system,
                builtin_tools=deps.make_tools(
                    step_row, broker, plan_lock, writer),
                sandbox=deps.sandbox(),
                subagent_input_hook=hooks.input_hook,
                subagent_output_hook=hooks.output_hook,
                subagent_run_wrapper=hooks.run_wrapper,
                **deps.common)
            broker.agent = agent
            # The step row points at ITS hop's conversation (the
            # per-task LLM-request view joins on it); a RE-OPENED row
            # keeps its FIRST attempt's stamp (stamp_states_context is
            # NULL-guarded) — the resume's own context rides the node
            # item instead.
            if writer.context_id is not None:
                try:
                    await PLAN_STORE.stamp_states_context(
                        [step_row.state_id], writer.context_id)
                except Exception as exc:  # noqa: BLE001 — bookkeeping
                    logger.warning(
                        "step context stamp failed: %s", exc)
            # THE DYNAMIC RUNG: this step's own directive keyword-
            # matches the not-yet-loaded catalog (runner closure).
            step_node_prompts = await deps.get_node_prompts(
                f"{step_row.node} {step_row.title}")
            message = deps.build_message(
                step_row, step_node_prompts,
                self._resume_block(resume))
            with self.chain(writer):
                result = await agent.run(message)
            # The reply's trailing [CRITICAL] marker (the step
            # addendum's critical-discovery contract) is metadata, not
            # prose: strip it from the finding the lane and the
            # findings bridge carry, and hand it to the settle tick as
            # the row's is_critical flag.
            critical, finding = extract_critical(result.answer)
            status = "completed"
            n_llm = result.steps
            n_tool = len(result.tool_calls)
            await self.close_node(
                writer, "completed", n_llm_calls=result.steps,
                n_tool_calls=len(result.tool_calls), n_states=1,
                answer_chars=len(result.answer))
        except Exception as exc:  # noqa: BLE001 — advance, don't die
            logger.warning("step %s (%s) failed, the chain advances: %s",
                           step_row.state_id, step_row.title, exc)
            finding = f"STEP FAILED — {type(exc).__name__}: {exc}"
            status = "failed"
            critical = False
            n_llm = n_tool = 0
            await self.close_node(writer, "failed",
                                  error=f"{type(exc).__name__}: {exc}",
                                  n_states=1)
        try:
            await PLAN_STORE.tick_step(
                step_row.state_id, status,
                thread_id=self.thread_id,
                node=step_row.node,
                llm_context_id=writer.context_id,
                note=" ".join(finding.split())[:500] or None,
                error=(" ".join(finding.split())[:300]
                       if status == "failed" else None),
                critical=critical)
        except Exception as exc:  # noqa: BLE001 — finalize settles it
            logger.warning("step settle tick failed (%s): %s",
                           step_row.state_id, exc)
        return status, finding, n_llm, n_tool

    async def resume(
        self,
        step_row: StateRow,
        *,
        instruction: str,
        prior_error: str = "",
        prior_context_id: Optional[str] = None,
    ) -> Tuple[str, str, int, int]:
        """The supervisor-instructed retry WITH context: the step
        message carries the instruction, the prior attempt's error and
        the prior chain ref (its recorded traffic stays reachable
        through the step's search_thread_context tool)."""
        return await self.run(
            step_row,
            resume={"instruction": instruction,
                    "prior_error": prior_error,
                    "prior_context_id": prior_context_id or ""})

    # -- internals -----------------------------------------------------
    @staticmethod
    def _resume_block(resume: Optional[Dict[str, str]]) -> Optional[str]:
        if not resume:
            return None
        parts = ["[Resume — supervisor instruction]",
                 resume.get("instruction") or "(no instruction recorded)"]
        if resume.get("prior_error"):
            parts.append(
                f"prior attempt failed with: {resume['prior_error']}")
        if resume.get("prior_context_id"):
            parts.append(
                "your PRIOR attempt's recorded traffic is chain "
                f"{resume['prior_context_id']} — search_thread_context "
                "finds what you already asked, fetched or concluded; "
                "build on it instead of starting over.")
        return "\n".join(parts)
