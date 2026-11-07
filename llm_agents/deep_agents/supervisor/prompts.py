"""llm_agents.deep_agents.supervisor.prompts — the supervisor's OWN
skills fetch (self-fed, no runner pass-down).

Two fail-soft reads, run ONLY when the watchdog wakes (a healthy run
pays zero fetches, same as its zero LLM):

* the type-3 ``deep-agent-supervisor-system`` row — the supervisor's
  system prompt (the runner used to pass the whole ``fetch_agent_prompts``
  bundle in; the supervisor now fetches its ONE row itself),
* the kit's type-4 description rows (``fetch_tool_prompts`` filtered to
  ``tools.KIT_DB_KEYS``) — the same per-tool instruction overrides the
  delegation hooks hand the fork kits.

Every read fail-softs to None/empty — the built-in
``SUPERVISOR_SYSTEM_PROMPT`` and each tool's ``DEFAULT_DESCRIPTION``
carry the behavior.
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

from pydantic import BaseModel, Field

from llm_agents.deep_agents.planning.store import (
    AGENT_PROMPT_SUPERVISOR_INTERVENTION, AGENT_PROMPT_SUPERVISOR_SYSTEM,
    PLAN_STORE,
)
from llm_agents.deep_agents.supervisor.tools import KIT_DB_KEYS

logger = logging.getLogger("deep_agents.supervisor")


class SupervisorPrompts(BaseModel):
    """The supervisor's resolved prompt rows (None/empty = the built-in
    constants apply)."""

    system: Optional[str] = None
    intervention: Optional[str] = None
    tool_descriptions: Dict[str, str] = Field(default_factory=dict)


async def fetch_supervisor_prompts() -> SupervisorPrompts:
    """The ONE waking fetch: the system row + the intervention message
    frame + the kit's description rows, fail-soft end to end (an outage
    degrades to the built-in prompts, never blocks the intervention)."""
    prompts = SupervisorPrompts()
    try:
        agent_prompts = await PLAN_STORE.fetch_agent_prompts()
        prompts.system = agent_prompts.get(AGENT_PROMPT_SUPERVISOR_SYSTEM)
        prompts.intervention = agent_prompts.get(
            AGENT_PROMPT_SUPERVISOR_INTERVENTION)
    except Exception as exc:  # noqa: BLE001 — fail-soft to built-in
        logger.warning("supervisor system prompt fetch failed: %s", exc)
    try:
        descriptions = await PLAN_STORE.fetch_tool_prompts()
    except Exception as exc:  # noqa: BLE001 — fail-soft to built-ins
        logger.warning("supervisor tool prompt fetch failed: %s", exc)
        return prompts
    prompts.tool_descriptions = {
        key: text for key, text in descriptions.items()
        if any(key == f"{k[0]}|{k[1]}" for k in KIT_DB_KEYS)}
    return prompts
