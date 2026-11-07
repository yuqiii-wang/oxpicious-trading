"""llm_agents.deep_agents.agents.supervisor_agent — ``SupervisorAgent``:
the supervisor session as an agent (2026-11-06). ONE agent == ONE
graph node: the agent owns the supervisor node's identity and contexts
— the run-start watching marker (``llm=False``; the idle circle the
Thread Graph draws) and, NEW, each waking intervention's OWN llm
context (a begun writer + bound chain handed to
``supervisor.poller``), so an intervention is a first-class context
row keyed into the review persistence — the gap the pre-agent poller
had (interventions ran with chain_id NULL).

:meth:`session` is the per-run backend loop (the former
``supervisor.poller.SupervisorSession.run``: poll → decide → resume/ignore
verdicts → the final sweep on exit, via
``SupervisorSession.sweep``). Fail-soft end to
end; never raises into the runner (CancelledError returns).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from llm_agents.deep_agents.agents.base import NodeAgent
from llm_agents.deep_agents.core.provider import ZhipuChatProvider
from llm_agents.deep_agents.supervisor.poller import SupervisorSession
from llm_agents.deep_agents.supervisor.repair import RepairChannel

if TYPE_CHECKING:  # no runtime langgraph import — the runner injects
    from langgraph.store.base import BaseStore

logger = logging.getLogger("deep_agents.agents.supervisor")


class SupervisorAgent(NodeAgent):
    """The SUPERVISOR node's agent — the repair-lane session beside
    the graph (idle on healthy runs; woken by a root-path failure)."""

    node = "supervisor"

    def new_llm_writer(self):
        """A begun-able writer WITH a context id — the interventions'
        factory (the ctor factory's llm defaults False for the marker;
        the poller prefers this hook when the agent provides it)."""
        return self._writer_factory(True)

    async def session(
        self,
        provider: ZhipuChatProvider,
        store: "BaseStore",
        *,
        goal: str,
        channel: RepairChannel,
        sweep_writer_factory,
        model: Optional[str] = None,
        super_thread_id: Optional[str] = None,
    ) -> None:
        """The per-run session: open the watching marker (the agent's
        own idle context), run the poll loop — every waking
        intervention runs under the agent's OWN llm writer + chain
        (:meth:`new_writer` / :meth:`chain`, passed to the poller as
        ``intervention_agent=self``) — and close with the final sweep
        (``sweep_writer_factory`` composes the sweep's writers).
        THE BINDING: the agent is constructed with the SUPER THREAD ID
        (the chain id, ``super_run`` passes ``self.super_thread_id``) —
        its marker, its reviews and its kit scope all key there, never
        to a segment langgraph thread; *super_thread_id* additionally
        widens the watched rows to the run's WHOLE CHAIN (the
        plan/custom/result threads all carry it). The runner owns the
        task; closing the channel ends the loop and the sweep closes
        the marker."""
        marker = self.new_writer()
        await self.begin_node(marker)
        await SupervisorSession(
            provider, store, thread_id=self.thread_id, goal=goal,
            channel=channel, marker=marker,
            writer_factory=sweep_writer_factory,
            model=model or self.model,
            intervention_agent=self,
            super_thread_id=super_thread_id,
        ).run()
