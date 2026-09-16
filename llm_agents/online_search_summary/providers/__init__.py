"""llm_agents.online_search_summary.providers — Search provider integrations.

One module per online-search provider, all subclassing the contract in
``providers.base`` and sharing the ``providers.transport`` HTTP plumbing;
each registers in ``providers.registry``:

  * ``zhipu`` — ZhipuSearchProvider: ZhiPu BigModel web_search API +
    native search-in-chat summary (glm models).
  * ``zhihu`` — ZhihuSearchProvider: Zhihu content-search API (the same
    developer.zhihu.com endpoint the downloads.macro.zhihu.news downloader
    uses, via the shared anti-bot proxy); search-only — summarization
    composes its results with a delegated chat provider (default zhipu).

To add a provider: subclass the base in a new module here, then add it to
``PROVIDERS`` in the registry.
"""
from __future__ import annotations

from llm_agents.online_search_summary.providers.base import (
    BaseOnlineSearchProvider,
)
from llm_agents.online_search_summary.providers.transport import (
    RetryingJsonClient,
)
from llm_agents.online_search_summary.providers.zhipu import (
    ZhipuSearchProvider,
)
from llm_agents.online_search_summary.providers.zhihu import (
    ZhihuSearchProvider,
)

__all__ = [
    "BaseOnlineSearchProvider", "RetryingJsonClient",
    "ZhipuSearchProvider", "ZhihuSearchProvider",
]
