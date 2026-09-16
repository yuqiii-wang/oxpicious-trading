"""llm_agents.online_search_summary.providers.registry — Provider registry.

Single place where providers become visible to ``get_provider`` / the
CLI. To add one: subclass ``BaseOnlineSearchProvider`` in
``llm_agents.online_search_summary.providers.<name>.py``, then add it to
``PROVIDERS`` here.
"""
from __future__ import annotations

from typing import Dict, Type

from llm_agents.online_search_summary.providers.base import (
    BaseOnlineSearchProvider,
)
from llm_agents.online_search_summary.providers import (
    ZhipuSearchProvider, ZhihuSearchProvider,
)

PROVIDERS: Dict[str, Type[BaseOnlineSearchProvider]] = {
    ZhipuSearchProvider.name: ZhipuSearchProvider,
    ZhihuSearchProvider.name: ZhihuSearchProvider,
}


def get_provider(name: str) -> Type[BaseOnlineSearchProvider]:
    try:
        return PROVIDERS[name]
    except KeyError:
        raise ValueError(f"unknown online-search provider {name!r} "
                         f"(known: {sorted(PROVIDERS)})") from None
