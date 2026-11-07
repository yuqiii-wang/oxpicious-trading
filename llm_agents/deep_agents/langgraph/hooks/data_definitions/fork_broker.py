"""ForkBroker — data definition.

Migrated verbatim from :mod:`llm_agents.deep_agents.langgraph.hooks.broker` (one file per ``@dataclass``)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from llm_agents.deep_agents.core.loop import DeepAgent
from llm_agents.deep_agents.planning.store import PLAN_STORE
from llm_agents.deep_agents.langgraph.hooks.slug import _fork_node_slug


logger = logging.getLogger("deep_agents.langgraph.hooks")


@dataclass
class ForkBroker:
    """The main authority's fork canceller — update_plan's terminate op
    routed to the execute agent's own registry. Late-bound (the agent
    exists only after its handlers, which close over the authority that
    closes over the broker); ``state_by_run`` is the SAME map the
    subagent input hook registers child states in, and
    ``context_by_run`` the SAME map it registers their LLM-context ids
    in, so a cancelled fork's state settles with its chained-context
    keys instead of waiting for finalize."""

    agent: Optional[DeepAgent] = None
    thread_id: str = ""
    state_by_run: Dict[str, int] = field(default_factory=dict)
    context_by_run: Dict[str, Optional[str]] = field(default_factory=dict)

    async def cancel(self, run_ids: Optional[List[str]]) -> List[str]:
        """Stop the named background forks (every running one when
        *run_ids* is None) and settle their fork states; returns the ids
        that actually stopped."""
        if self.agent is None:
            return []
        live = [r for r in self.agent.subagent_runs()
                if r.background and r.task is not None and not r.task.done()]
        targets = ([r for r in live if r.run_id in set(run_ids)]
                   if run_ids is not None else live)
        stopped: List[str] = []
        for run in targets:
            if not self.agent.cancel_subagent(run.run_id):
                continue
            stopped.append(run.run_id)
            state_id = self.state_by_run.get(run.run_id)
            if state_id is None:
                continue
            try:
                await PLAN_STORE.finish_child_state(
                    state_id, "failed",
                    error="cancelled by plan update (fork terminated)",
                    thread_id=self.thread_id,
                    node=_fork_node_slug(run.request.goal),
                    llm_context_id=self.context_by_run.get(run.run_id))
            except Exception as exc:  # noqa: BLE001 — settle at finalize
                logger.warning("cancelled fork %s state settle failed: %s",
                               run.run_id, exc)
        return stopped
