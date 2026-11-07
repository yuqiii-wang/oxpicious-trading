"""llm_agents.deep_agents.supervisor.watchdog — RETIRED module
(2026-11-06): the watchdog tail sweep became
:meth:`supervisor.poller.SupervisorSession.sweep` — the session's own
method, the poll loop and the tail one object. Only the writer-factory
alias stays here for its importers (the poller re-exports it)."""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:  # no runtime langgraph import — the runner injects
    from llm_agents.deep_agents.langgraph.state_store import NodeRunWriter

# The runner-side writer factory: (llm: bool) -> a NodeRunWriter for
# THIS run's supervisor namespace (the marker is the llm=False form).
WriterFactory = Callable[[bool], "NodeRunWriter"]
