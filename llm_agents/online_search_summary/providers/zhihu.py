"""llm_agents.online_search_summary.providers.zhihu — Zhihu search provider.

Wraps the Zhihu content-search API already used by the
``downloads.macro.zhihu.news`` downloader as an online-search provider:

  * endpoint — ``GET
    https://developer.zhihu.com/api/v1/content/zhihu_search`` with
    ``Query`` / ``Count`` params and Bearer ``ZHIHU_API_KEY`` auth (plus
    ``X-Request-Timestamp``), routed through the shared ``AntiBotProxy``
    (browser-fingerprint rotation, host-blocking detection, sleep
    cadence). Response: ``{Code, Message, Data: {SearchHashId, HasMore,
    EmptyReason, Items: [...]}}``; ``Code`` 0 = ok, 30001 = rate limit
    (retried with backoff), 10001/20001/90001 = param/auth/internal.
  * item fields — ``Title``, ``ContentText``, ``Url``, ``EditTime`` (unix
    seconds, Asia/Shanghai), ``VoteUpCount``, ``AuthorName``,
    ``ContentID``; hits carry ``media="zhihu"`` so stored rows join the
    corpus's zhihu source convention.

Zhihu is a SEARCH-only platform: ``_chat_complete`` delegates to a
chat-capable provider (default: the registered zhipu provider), so
``summarize`` always runs the compose flow — zhihu search results +
delegated LLM summarization.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from llm_agents.online_search_summary.core.errors import OnlineSearchError
from llm_agents.online_search_summary.core.models import (
    SHANGHAI_TZ, SearchHit, SearchOptions, SearchResponse, SearchSummary,
)
from llm_agents.online_search_summary.providers.base import (
    BaseOnlineSearchProvider,
)

logger = logging.getLogger(__name__)

ZHIHU_SEARCH_URL = "https://developer.zhihu.com/api/v1/content/zhihu_search"

# API response codes: 0 ok; 30001 frequency limit (retryable);
# 10001 param / 20001 auth / 90001 internal (fatal).
_ZHIHU_RETRY_CODES = frozenset({30001})


class ZhihuSearchProvider(BaseOnlineSearchProvider):
    """Zhihu content search (developer.zhihu.com), compose-only summary."""

    name = "zhihu"
    API_KEY_ENV_VARS = ("ZHIHU_API_KEY",)
    ENGINES: Dict[str, str] = {}          # zhihu exposes a single engine
    DEFAULT_ENGINE = None

    SEARCH_URL = ZHIHU_SEARCH_URL
    # The delegated chat provider's model answers the summary prompt.
    DEFAULT_MODEL = "glm-5.2"

    def __init__(self, *, chat_provider: Optional[BaseOnlineSearchProvider] = None,
                 base_sleep_sec: float = 2.0, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._chat_provider = chat_provider
        self._proxy = None
        self.base_sleep_sec = base_sleep_sec

    # -- delegated chat ------------------------------------------------------
    def chat_provider(self) -> BaseOnlineSearchProvider:
        """The chat-capable delegate (default: registered zhipu provider)."""
        if self._chat_provider is None:
            from llm_agents.online_search_summary.providers.registry import (
                get_provider,
            )
            delegate = get_provider("zhipu")
            self._chat_provider = delegate()
        return self._chat_provider

    async def _chat_complete(
        self, messages: Sequence[Dict[str, str]], *, model: str,
        request_id: Optional[str] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """Zhihu has no completion API — delegate to the chat provider."""
        return await self.chat_provider()._chat_complete(
            messages, model=model, request_id=request_id)

    # -- anti-bot transport ---------------------------------------------------
    def _anti_bot_proxy(self):
        if self._proxy is None:
            from downloads._common import AntiBotConfig, AntiBotProxy
            self._proxy = AntiBotProxy(AntiBotConfig(
                base_sleep_sec=self.base_sleep_sec,
                enable_host_tracking=True,
            ))
        return self._proxy

    def _search_request(self, query: str, count: int,
                        *, max_retries: int = 3) -> Dict[str, Any]:
        """Synchronous anti-bot GET of the zhihu search API (to_thread).

        Mirrors downloads.macro.zhihu.news.search_zhihu: Bearer + timestamp
        headers, Query/Count params, retry on rate-limit (Code=30001) and
        transport failure with exponential backoff.
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "X-Request-Timestamp": str(int(time.time())),
            "Content-Type": "application/json",
        }
        params = {"Query": query, "Count": count}
        proxy = self._anti_bot_proxy()
        last_err: Optional[str] = None
        for attempt in range(1, max_retries + 1):
            resp = proxy.get(self.session, self.SEARCH_URL,
                             params=params, headers=headers,
                             logger=logger, log_tag="    ")
            if resp is None:
                last_err = "request failed"
                logger.warning("    [%s] %s (attempt %d/%d)", self.name,
                               last_err, attempt, max_retries)
                time.sleep(min(2 ** attempt, 30))
                continue
            try:
                data = resp.json()
            except ValueError as e:
                raise OnlineSearchError(
                    f"{self.name}: non-JSON response: {resp.text[:200]!r}"
                ) from e
            code = data.get("Code")
            if code == 0:
                return data
            if code in _ZHIHU_RETRY_CODES and attempt < max_retries:
                wait = min(2 ** attempt * 5, 60)
                logger.warning("    [%s] rate limit (Code=30001), backing "
                               "off %ds (attempt %d/%d)", self.name, wait,
                               attempt, max_retries)
                time.sleep(wait)
                continue
            raise OnlineSearchError(
                f"{self.name}: API error Code={code}: {data.get('Message')}",
                code=str(code) if code is not None else None)
        raise OnlineSearchError(
            f"{self.name}: exhausted {max_retries} retries ({last_err})")

    # -- contract ---------------------------------------------------------------
    @staticmethod
    def _parse_items(items: Any) -> List[SearchHit]:
        """Data.Items -> SearchHits (refer = position, media = 'zhihu').

        The raw Title is the question-page title shared by every answer of
        the same question on the same day, but text.news PKs on
        (title, source, date) — later duplicates are disambiguated with the
        author name (then ContentID tail), mirroring the loader convention
        in builds.text.loaders._finalize_zhihu_titles.
        """
        if not isinstance(items, list):
            return []
        parsed: List[Dict[str, Any]] = []
        for i, it in enumerate(items, start=1):
            if not isinstance(it, dict):
                continue
            title = (it.get("Title") or "").strip()
            content = (it.get("ContentText") or "").strip() or None
            if not title or not content:
                continue
            pub_date = None
            edit_time = it.get("EditTime")
            if edit_time:
                try:
                    pub_date = datetime.datetime.fromtimestamp(
                        int(edit_time), tz=SHANGHAI_TZ).date()
                except (TypeError, ValueError, OSError):
                    pub_date = None
            parsed.append({
                "position": i,
                "title": title,
                "content": content,
                "link": (it.get("Url") or "").strip() or None,
                "publish_date": pub_date,
                "author": (it.get("AuthorName") or "").strip(),
                "content_id": (it.get("ContentID") or "").strip(),
            })

        # (title, date) disambiguation — first occurrence keeps the plain
        # title so corpus rows downloaded by builds.text (same convention)
        # can match.
        used: set = set()
        hits: List[SearchHit] = []
        for p in parsed:
            candidates = [p["title"]]
            suffixes = [p["author"], p["content_id"][-6:]]
            candidates += [f"{p['title']} · {s}" for s in suffixes if s]
            base, i = p["title"], 2
            final = next(
                (c for c in candidates
                 if (c, p["publish_date"]) not in used), None)
            while final is None:
                final = f"{base} · #{i}"
                if (final, p["publish_date"]) not in used:
                    break
                i += 1
            used.add((final, p["publish_date"]))
            hits.append(SearchHit(
                refer=f"ref_{p['position']}",
                title=final,
                content=p["content"],
                link=p["link"],
                media="zhihu",
                publish_date=p["publish_date"],
                publish_date_raw=(p["publish_date"].isoformat()
                                  if p["publish_date"] else None),
            ))
        return hits

    async def search(self, query: str,
                     opts: Optional[SearchOptions] = None) -> SearchResponse:
        opts = opts or SearchOptions()
        data = await asyncio.to_thread(
            self._search_request, query, opts.count)
        data_obj = data.get("Data") or {}
        hits = self._parse_items(data_obj.get("Items"))
        logger.info("    [%s] hits=%d has_more=%s", self.name, len(hits),
                    data_obj.get("HasMore"))
        return SearchResponse(hits=hits, provider=self.name)

    async def summarize(
        self, query: str, *, model: Optional[str] = None,
        opts: Optional[SearchOptions] = None,
        mode: str = "compose",
        lang: str = "zh",
    ) -> SearchSummary:
        """Compose-only: zhihu search + delegated chat summarization."""
        return await self.summarize_via_compose(query, model=model,
                                                opts=opts, lang=lang)
