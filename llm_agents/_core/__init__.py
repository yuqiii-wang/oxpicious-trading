"""llm_agents._core — Shared LLM-provider plumbing.

The layer every llm_agents provider stack consumes; dependencies point
one way (providers/* -> _core, never back). Home of the pieces the agent
packages used to duplicate between ``online_search_summary`` and
``llm_ask``:

  * ``errors``    — LlmProviderError (status + provider error code); the
    per-agent names (OnlineSearchError, LlmAskError) are aliases of it.
  * ``models``    — SHANGHAI_TZ, the shared "today" reference.
  * ``prompts``   — LANG_LABELS / lang_label, the prompt language labels.
  * ``transport`` — RetryingJsonClient (Bearer-auth retrying JSON POST;
    retries ONLY genuinely transient failures) + load_project_env, the
    project-root ``.env`` loader behind API-key resolution.
  * ``base``      — BaseLlmProvider: API-key resolution, retry policy,
    and the _post_json helpers every provider base inherits.

NOT a CLI / agent: nothing here parses args, touches the DB, or defines
a domain contract — those stay in the per-agent packages.
"""
from __future__ import annotations

from llm_agents._core.errors import LlmProviderError
from llm_agents._core.models import SHANGHAI_TZ
from llm_agents._core.prompts import LANG_LABELS, lang_label
from llm_agents._core.transport import (
    RetryingJsonClient, load_project_env,
)
from llm_agents._core.base import BaseLlmProvider

__all__ = [
    "LlmProviderError", "SHANGHAI_TZ",
    "LANG_LABELS", "lang_label",
    "RetryingJsonClient", "load_project_env",
    "BaseLlmProvider",
]
