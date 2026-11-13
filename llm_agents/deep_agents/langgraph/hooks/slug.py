"""llm_agents.deep_agents.langgraph.hooks.slug — the spawn child state's node tag: the goal-derived
business slug (``spawn_<content words>``) the graph canvas
labels spawns with.
"""
from __future__ import annotations

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from langgraph.store.base import BaseStore

from llm_agents._core.request_log import bind_request_chain, end_request_chain
from llm_agents.deep_agents.core.delegation import (
    SubagentRun, format_subagent_result,
)
from llm_agents.deep_agents.core.loop import DeepAgent
from llm_agents.deep_agents.langgraph.prompts import standing_system
from llm_agents.deep_agents.langgraph.state_store import (
    entry_checkpoint_id, new_context_id,
)
from llm_agents.deep_agents.limits import run_limits
from llm_agents.deep_agents.planning.plan_json import (
    MAX_NODE_LEN, MAX_TITLE_LEN,
)
from llm_agents.deep_agents.planning.store import (
    AGENT_PROMPT_SPAWN_PLAN_ADDENDUM,
    AGENT_PROMPT_SPAWN_DELEGATE_ADDENDUM,
    AGENT_PROMPT_SUBAGENT_SYSTEM,
    PlannedStep,
    PLAN_STORE,
)
from llm_agents.deep_agents.tools.plan_tools import (
    SUBAGENT_SPAWN_ADDENDUM, SpawnPlanToolKit,
)
from llm_agents.deep_agents.tools.kg_tools import KgToolSet
from llm_agents.deep_agents.tools.stats_tools import StatsToolSet

logger = logging.getLogger("deep_agents.langgraph.hooks")


_SPAWN_STOPWORDS = frozenset((
    "the", "a", "an", "of", "for", "in", "to", "on", "at", "and", "or",
    "with", "from", "by", "is", "are", "was", "were", "be", "been",
    "this", "that", "these", "those", "its", "it", "as", "into",
    "over", "under", "about", "you", "your", "we", "our", "use",
    "using", "then", "when", "which", "what", "how", "whether", "if",
    "does", "do", "did", "can", "could", "should", "would", "must",
    "not", "no", "vs", "per", "via", "please",
))


def _spawn_node_slug(goal: str) -> str:
    """The spawn child state's node tag, derived from the goal's OWN
    words: the first few content words (stopwords dropped), snake-
    joined under the ``spawn_`` prefix — the graph canvas labels the
    spawn with this tag, so it must name the spawn's business topic
    (``spawn_moving_average_000300``), never the spawn operation. Falls
    back to the bare prefix when the goal yields nothing slug-able."""
    return _child_node_slug(goal, "spawn")


def _fork_node_slug(goal: str) -> str:
    """The FORK child state's node tag (2026-11-13) — the same
    goal-derived business topic under the ``fork_`` prefix
    (``fork_momentum_deep_dive``): an inherit-context child's row wears
    the fork tag so the canvas/agent-steps surfaces can tell the two
    child kinds apart beside the typed ``child_kind`` column."""
    return _child_node_slug(goal, "fork")


def _old_node_slug() -> str:
    """The ABANDONED pre-fork tail's node tag (2026-11-13, child kind
    2): a fixed tag — the stub is topology, not business work, and the
    canvas labels it 'old chain' beside the drift note it carries."""
    return "old_drifted_tail"


def _child_node_slug(goal: str, prefix: str) -> str:
    """The shared goal→topic slug body: the first few content words
    (stopwords dropped), snake-joined under the given child-kind
    prefix; falls back to the bare prefix when the goal yields nothing
    slug-able."""
    tokens = re.findall(r"[0-9a-zA-Z\u4e00-\u9fff]+", (goal or "").lower())
    kept = [t for t in tokens if t not in _SPAWN_STOPWORDS][:4]
    slug = "_".join(kept).strip("_")
    return f"{prefix}_{slug}"[:MAX_NODE_LEN] if slug else prefix

