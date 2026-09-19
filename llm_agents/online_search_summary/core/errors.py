"""llm_agents.online_search_summary.core.errors — Provider exception type.

The exception lives in ``llm_agents._core.errors`` (``LlmProviderError``)
since the transport is shared across the llm_agents providers;
``OnlineSearchError`` is kept as an alias so existing imports and
``except`` clauses catch the same class the shared transport raises.
"""
from __future__ import annotations

from llm_agents._core.errors import LlmProviderError

OnlineSearchError = LlmProviderError
