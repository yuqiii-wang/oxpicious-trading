"""llm_agents.deep_agents.langgraph.hooks.slug — the fork child state's node tag: the goal-derived
business slug (``fork_<content words>``) the graph canvas
labels forks with.
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
    AGENT_PROMPT_FORK_PLAN_ADDENDUM,
    AGENT_PROMPT_FORK_SPAWN_ADDENDUM,
    AGENT_PROMPT_SUBAGENT_SYSTEM,
    PlannedStep,
    PLAN_STORE,
)
from llm_agents.deep_agents.tools.plan_tools import (
    SUBAGENT_SPAWN_ADDENDUM, ForkPlanToolKit,
)
from llm_agents.deep_agents.tools.kg_tools import KgToolSet
from llm_agents.deep_agents.tools.stats_tools import StatsToolSet

logger = logging.getLogger("deep_agents.langgraph.hooks")


_FORK_STOPWORDS = frozenset((
    "the", "a", "an", "of", "for", "in", "to", "on", "at", "and", "or",
    "with", "from", "by", "is", "are", "was", "were", "be", "been",
    "this", "that", "these", "those", "its", "it", "as", "into",
    "over", "under", "about", "you", "your", "we", "our", "use",
    "using", "then", "when", "which", "what", "how", "whether", "if",
    "does", "do", "did", "can", "could", "should", "would", "must",
    "not", "no", "vs", "per", "via", "please",
))


def _fork_node_slug(goal: str) -> str:
    """The fork child state's node tag, derived from the goal's OWN
    words: the first few content words (stopwords dropped), snake-
    joined under the ``fork_`` prefix — the graph canvas labels the
    fork with this tag, so it must name the fork's business topic
    (``fork_moving_average_000300``), never the spawn operation. Falls
    back to the bare prefix when the goal yields nothing slug-able."""
    tokens = re.findall(r"[0-9a-zA-Z\u4e00-\u9fff]+", (goal or "").lower())
    kept = [t for t in tokens if t not in _FORK_STOPWORDS][:4]
    slug = "_".join(kept).strip("_")
    return f"fork_{slug}"[:MAX_NODE_LEN] if slug else "fork"

