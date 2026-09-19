"""llm_agents.llm_ask.providers — Plain-LLM provider integrations.

One module per chat provider, all subclassing the contract in
``providers.base`` (whose client plumbing — retrying JSON transport,
API-key resolution — is shared through ``llm_agents._core``); each
registers in ``providers.registry``:

  * ``zhipu`` — ZhipuLlmAskProvider: ZhiPu BigModel chat completions
    (glm models), plain LLM request only — no web-search tools.

To add a provider: subclass the base in a new module here, then add it to
``PROVIDERS`` in the registry.
"""
from __future__ import annotations

from llm_agents._core.transport import RetryingJsonClient
from llm_agents.llm_ask.providers.base import BaseLlmAskProvider
from llm_agents.llm_ask.providers.zhipu import ZhipuLlmAskProvider

__all__ = [
    "BaseLlmAskProvider", "RetryingJsonClient", "ZhipuLlmAskProvider",
]
