"""llm_agents.deep_agents.agents.plan_agent — ``PlanAgent``: the
planning node as an agent (2026-11-06). ONE agent == ONE graph node:
the agent owns the plan node's identity and its ONE chained context,
and :meth:`run` drives the planning engine (``planning.session``'s
batched proposal + prompt ladder — the pure domain stays there).

The lifecycle is the former ``run_plan_session`` body, verbatim: the
writer begun (checkpoint None — planning runs OUTSIDE the stateless
graph), the running planning row's context stamp, the request-chain
binding, the engine, the node-item finish (completed / failed /
skipped) and the planning row's settle.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Callable, Dict, List, Optional

from llm_agents.deep_agents.agents.base import NodeAgent
from llm_agents.deep_agents.planning.session import (
    PlanSession, PlanSessionResult,
)
from llm_agents.deep_agents.planning.store import (PLAN_STORE)

logger = logging.getLogger("deep_agents.agents.plan")


class PlanAgent(NodeAgent):
    """The PLAN node's agent — one planning run per invocation."""

    node = "plan"

    async def run(
        self,
        *,
        goal: str,
        data: Optional[Dict[str, Any]],
        prompt_keys: List[str],
        agent_prompts: Optional[Dict[str, str]] = None,
        plan_state_id: Optional[int] = None,
        plan_template_id: Optional[int] = None,
    ) -> PlanSessionResult:
        """One planning session: the node item, the context stamp, the
        chain-bound engine, the settles. Never raises into the caller
        beyond the engine's own fail-soft contract (a planning failure
        degrades to ``plan_ok=False`` and the run continues on the
        execute lane)."""
        writer = self.new_writer()
        await self.begin_node(writer)
        # The running planning node's row (begin_thread) points at THIS
        # session's one context — its proposal requests join the plan
        # task's LLM-request view like any step's.
        if plan_state_id is not None and writer.context_id is not None:
            try:
                await PLAN_STORE.stamp_states_context(
                    [int(plan_state_id)], writer.context_id)
            except Exception as exc:  # noqa: BLE001 — bookkeeping
                logger.warning(
                    "planning-node context stamp failed: %s", exc)
        # This session's request-log chain: its LLM calls (proposal +
        # correction rounds) record under chain_id = the context id.
        with self.chain(writer):
            result = await PlanSession(
                self._chat, goal, data, list(prompt_keys),
                model=self.model,
                thread_id=self.thread_id or uuid.uuid4().hex,
                agent_prompts=dict(agent_prompts or {}),
                plan_state_id=plan_state_id,
                llm_context_id=writer.context_id,
                plan_template_id=plan_template_id,
            ).run()
        plan = result.plan
        n_calls = result.n_calls
        n_states = len(plan.states) if plan is not None else 0
        # A proposal that never validated after spending LLM calls =
        # failed (the run degrades to the plain execute lane, it does
        # not die — the same fail-soft contract as plan_store).
        status = ("completed" if plan is not None
                  else "failed" if n_calls else "skipped")
        await self.close_node(writer, status, n_llm_calls=n_calls,
                              n_states=n_states)
        # Close the RUNNING PLANNING NODE's row (begin_thread's — the
        # record the run started with): completed with the step count,
        # failed with the reason. Must land BEFORE the step lane reads
        # the tree; fail-soft — the blanket finalize/stop settle owns
        # the row when this cannot.
        if plan_state_id is not None:
            try:
                await PLAN_STORE.settle_plan_state(
                    self.thread_id,
                    "completed" if plan is not None else "failed",
                    note=(f"laid out {n_states} steps"
                          if plan is not None else None),
                    error=(None if plan is not None
                           else "plan did not persist (proposal or "
                                "persistence failed)"),
                    llm_context_id=writer.context_id,
                )
            except Exception as exc:  # noqa: BLE001 — finalize settles it
                logger.warning("planning-node settle failed: %s", exc)
        return result
