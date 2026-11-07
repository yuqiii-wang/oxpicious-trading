"""llm_agents.deep_agents.agents.base — ``NodeAgent``, the base every
harness agent extends (2026-11-06): ONE agent == ONE graph node.

The base owns the node's two permanent possessions:

* IDENTITY — the thread id, the node's namespace name (``plan`` /
  ``supervisor`` / ``step``), and the ``llm.deep_agent_states`` row id
  when the node IS a plan step (state_id; None for the runner-lane
  nodes),
* CONTEXT — the node's ONE chained LLM context: the ``NodeRunWriter``
  lifecycle (:meth:`NodeAgent.begin_node` / the writer's ``finish``)
  and the request-log chain (:meth:`NodeAgent.chain`), so every
  conversation the agent runs records under the node's own context id
  and the UI's per-node request view finds them.

The langgraph-typed writer is INJECTED (``writer_factory`` under
``TYPE_CHECKING`` — no runtime langgraph import; only langgraph
composes). Subclasses implement their engines (:meth:`run`) and, when
the node is resumable, :meth:`resume` — the supervisor-repair entry
that re-runs the node WITH its prior context instead of cold.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Callable, Optional

from llm_agents._core.request_log import (
    bind_request_chain, end_request_chain,
)

if TYPE_CHECKING:  # no runtime langgraph import — the runner injects
    from llm_agents.deep_agents.langgraph.state_store import NodeRunWriter

    NodeWriterFactory = Callable[[], NodeRunWriter]
else:
    NodeWriterFactory = Callable[[], Any]

logger = logging.getLogger("deep_agents.agents")


class NodeAgent:
    """ONE agent == ONE graph node (see the module docstring).

    A node may run more than one conversation across a run — each
    mints its own context id under the same node namespace via
    :meth:`new_writer` (e.g. the supervisor's run-start marker vs. its
    waking interventions; a step's first attempt vs. its
    supervisor-instructed resume)."""

    # The node's namespace name — subclasses pin it ("" when the name
    # is per-instance, e.g. the step hops' f"step-{state_id}"; the
    # factory closure composes the concrete item name).
    node: str = ""

    def __init__(
        self,
        chat_complete: Callable[..., Any],
        *,
        thread_id: str,
        model: Optional[str] = None,
        writer_factory: NodeWriterFactory,
        state_id: Optional[int] = None,
    ) -> None:
        self._chat = chat_complete
        self.thread_id = thread_id
        self.model = model
        self._writer_factory = writer_factory
        self.state_id = state_id

    # -- context lifecycle (the base's whole point) -------------------
    def new_writer(self) -> "NodeRunWriter":
        """A fresh writer for THIS node's next conversation (the
        factory closure carries store/thread id/node name/model)."""
        return self._writer_factory()

    @staticmethod
    async def begin_node(writer: "NodeRunWriter",
                         checkpoint: Optional[str] = None) -> None:
        """Begin the node item (fail-soft inside the writer)."""
        await writer.begin(checkpoint)

    @staticmethod
    async def close_node(writer: "NodeRunWriter", status: str,
                         **kw: Any) -> None:
        """Finish the node item (the put replaces it; fail-soft)."""
        await writer.finish(status, **kw)

    @staticmethod
    @contextmanager
    def chain(writer: "NodeRunWriter"):
        """Bind THIS node's context id as the request-log chain — the
        agent's LLM calls record under chain_id = the node's context
        id (the UI's per-node/per-task request view joins on it, and
        the chain-wide search_thread_context scope resolves from
        these). ``bind_request_chain`` is a @contextmanager — it MUST
        be entered (``with``); a plain call is a silent no-op and every
        request lands with chain_id NULL (the 2026-11-06 live finding:
        a whole run's requests unbound until this was fixed)."""
        cid = getattr(writer, "context_id", None)
        with bind_request_chain(cid):
            yield cid
        # The conversation is over — its chain's last-full-payload
        # dedupe cache entry is dead weight now.
        end_request_chain(cid)

    # -- engines -------------------------------------------------------
    async def run(self, *args: Any, **kw: Any) -> Any:
        raise NotImplementedError("NodeAgent subclasses own their engine")

    async def resume(self, *args: Any, **kw: Any) -> Any:
        """The supervisor-repair entry: re-run the node WITH its prior
        context (the instruction + the prior attempt's error + its
        recorded-traffic chain). Default: not resumable."""
        raise NotImplementedError(f"{type(self).__name__} is not resumable")
