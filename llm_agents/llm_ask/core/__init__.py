"""llm_agents.llm_ask.core — Pure domain layer.

Provider-independent contracts, mirroring the core layer of
``llm_agents.online_search_summary.core`` minus the search vocabulary:
normalized result models and the ask prompt builders. NO network, NO DB,
and NO imports from the providers/cli layers — the dependency direction
is providers/cli -> core, never back.

  * ``errors``  — LlmAskError (status + provider error code).
  * ``models``  — AskOptions / ChatCompletion / AskResult + SHANGHAI_TZ.
  * ``prompts`` — the default ask system prompt and its builders
    (ask_system_prompt, lang_label).
"""
from __future__ import annotations

from llm_agents.llm_ask.core.errors import LlmAskError
from llm_agents.llm_ask.core.models import (
    SHANGHAI_TZ, AskOptions, AskResult, ChatCompletion,
)
from llm_agents.llm_ask.core.prompts import (
    LANG_LABELS, ask_system_prompt, lang_label,
)

__all__ = [
    "LlmAskError",
    "AskOptions", "AskResult", "ChatCompletion", "SHANGHAI_TZ",
    "LANG_LABELS", "ask_system_prompt", "lang_label",
]
