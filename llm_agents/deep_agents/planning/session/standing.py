"""llm_agents.deep_agents.planning.session.standing — the STANDING
OPENING PROMPT every agent composition point shares.

The built-in constant + its resolver: the DB-managed internal row
(``deep-agent-standing-system``) when active, else the constant —
fail-soft like every prompt source. Every agent-composition point
opens its system text with the resolved row (the plan session, the hop
nodes, the delegation child input hook). (Relocated from
langgraph.prompts 2026-11-06; prompts.py re-imports it so its
consumers are unchanged.)
"""
from __future__ import annotations

from typing import Dict, Optional

from llm_agents.deep_agents.planning.store import (
    AGENT_PROMPT_STANDING_SYSTEM,
)

# The standing opening prompt's built-in constant — every agent
# composition point opens its system text with the resolved row.
AGENT_STANDING_SYSTEM = (
    "You are one agent of ONE deep research run over this house's "
    "CN-market knowledge base: the goal riding your conversation is "
    "the run's goal, and your finding feeds its single answer. Abide "
    "by the run's biz skills: the loaded ones ride your message in "
    "full (plus any keyword-matched to YOUR directive); the rest sit "
    "in the PROMPT CATALOG (prompt_key + one preview line) — "
    "load_prompt on ONE key the moment your work enters that skill's "
    "domain (or fetch that one row from llm.ai_prompts when your "
    "toolkit lacks the tool), and read every metric through the "
    "applicable skill's trading semantics — never generic priors. "
    "Stay alert to what does not add up: when your toolkit carries "
    "spawn_subagent, fork one proactively — dive deep into a "
    "suspicious finding through a dedicated subagent, and hand messy "
    "or diluted context off to a fresh subagent with a self-contained "
    "goal instead of pushing on."
)


def standing_system(agent_prompts: Optional[Dict[str, str]]) -> str:
    """The resolved standing opening prompt — the DB-managed internal
    row (``deep-agent-standing-system``) when active, else the built-in
    constant. Every agent-composition point opens its system text with
    this (the plan session, the hop nodes, the delegation child input
    hook), fail-soft like every prompt source."""
    return ((agent_prompts or {}).get(AGENT_PROMPT_STANDING_SYSTEM)
            or AGENT_STANDING_SYSTEM)
