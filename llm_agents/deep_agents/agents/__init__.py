"""llm_agents.deep_agents.agents — the AGENT LAYER (2026-11-06): every
agent in the harness is a first-class object representing its graph
NODE. ``NodeAgent`` owns the two things a node has — its IDENTITY
(the thread id, the node name, its ``llm.deep_agent_states`` row id)
and its ONE chained LLM context (the ``NodeRunWriter`` lifecycle +
the request-log chain) — and the concrete agents own their engines:

* :class:`PlanAgent` — the planning session (the PLAN graph's core
  node),
* :class:`SupervisorAgent` — the repair-lane session (beside the
  graphs; its interventions are first-class context rows),
* :class:`StepAgent` — one planned step's conversation (the inline
  hop; ``resume()`` re-runs a supervisor-instructed retry WITH its
  context instead of cold),
* :class:`OutputAgent` — a graph's FINAL OUTPUT NODE: the
  fixed-directive synthesis conversation (the custom graph's findings
  synthesis → the graph-output handoff; the result graph's final
  answer → the final-result document).

THE NODE == AGENT PRINCIPLE: a failed node's fix is not a cold row
re-open — the supervisor instructs the node's OWN agent to resume
with its context.

Layering: imports planning.*, supervisor.* (domain), core (provider,
loop); imports langgraph ONLY under TYPE_CHECKING — the runner
injects the ``NodeRunWriter`` factory, so only langgraph composes at
runtime.
"""
from llm_agents.deep_agents.agents.base import NodeAgent, NodeWriterFactory
from llm_agents.deep_agents.agents.output_agent import OutputAgent, OutputDeps
from llm_agents.deep_agents.agents.plan_agent import PlanAgent
from llm_agents.deep_agents.agents.step_agent import StepAgent, StepDeps
from llm_agents.deep_agents.agents.supervisor_agent import SupervisorAgent

__all__ = [
    "NodeAgent", "NodeWriterFactory", "OutputAgent", "OutputDeps",
    "PlanAgent", "StepAgent", "StepDeps", "SupervisorAgent",
]
