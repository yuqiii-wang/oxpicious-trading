"""llm_agents.deep_agents.tools.kg_tools — the knowledge-graph kit as
AGENT TOOLS (the thin adapter layer).

The KG domain — the traversal engine, the TTL graph loader, the
llm.kg_queries log / two-hit evidence and the llm.kg_hotpaths promoted
replay — lives in the INDEPENDENT ``llm_agents.kg`` package (nothing
there imports the harness). This package keeps only the model-facing
half: ``kit.py``'s ``KgToolSet`` — ``kg_search`` (traversal search,
hot-path replay) and ``kg_expand`` (one node's neighborhood) — the
tool schemas, the loose-argument coercion, the error formatting and
the agent-flow next-step hints, delegating the flow to
``llm_agents.kg.KNOWLEDGE_GRAPHS``, the process-wide
``KnowledgeGraphService``.
"""
from llm_agents.deep_agents.tools.kg_tools.kit import (
    KgExpandTool, KgSearchTool, KgToolSet,
)

__all__ = [
    "KgExpandTool", "KgSearchTool", "KgToolSet",
]
