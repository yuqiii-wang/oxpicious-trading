"""llm_agents.llm_ask.providers.registry — Provider registry.

Single place where providers become visible to ``get_provider`` / the
CLI. To add one: subclass ``BaseLlmAskProvider`` in
``llm_agents.llm_ask.providers.<name>.py``, then add it to ``PROVIDERS``
here.
"""
from __future__ import annotations

from typing import Dict, Type

from llm_agents.llm_ask.providers.base import BaseLlmAskProvider
from llm_agents.llm_ask.providers.zhipu import ZhipuLlmAskProvider

PROVIDERS: Dict[str, Type[BaseLlmAskProvider]] = {
    ZhipuLlmAskProvider.name: ZhipuLlmAskProvider,
}


def get_provider(name: str) -> Type[BaseLlmAskProvider]:
    try:
        return PROVIDERS[name]
    except KeyError:
        raise ValueError(f"unknown llm-ask provider {name!r} "
                         f"(known: {sorted(PROVIDERS)})") from None
