"""llm_agents.online_search_summary.providers.zhipu — ZhiPu BigModel provider.

Studied reference (docs.bigmodel.cn/cn/guide/tools/web-search +
/api-reference/工具-api/网络搜索):

  * Standalone Web Search API — ``POST
    https://open.bigmodel.cn/api/paas/v4/web_search`` (Bearer auth).
    Request: ``search_query`` (required, recommended <= 70 chars),
    ``search_engine`` (search_std / search_pro / search_pro_sogou /
    search_pro_quark), ``search_intent`` (bool, intent recognition
    first), ``count`` (1-50, default 10), ``search_domain_filter``,
    ``search_recency_filter`` (oneDay / oneWeek / oneMonth / oneYear /
    noLimit), ``content_size`` (medium / high), ``request_id``.
    Response: ``{id, created, request_id, search_intent[],
    search_result[]}`` where each ``search_intent`` item is ``{query,
    intent, keywords}`` and each ``search_result`` item is ``{title,
    content, link, media, icon, refer, publish_date}``.
  * Search-in-chat summary — ``POST .../v4/chat/completions`` with
    ``tools=[{"type": "web_search", "web_search": {...}}]``:
    ``enable=true`` activates the search, ``search_result=true`` echoes
    the retrieved results on the response message (``message.web_search``),
    and ``search_prompt`` is an instruction template whose literal
    ``{search_result}`` placeholder receives the formatted results. The
    generated answer cites sources inline as ``[来源：ref_N]``.

``search``    — the standalone Web Search API above.
``summarize`` — native one-shot search-in-chat; falls back to / can be
forced through the provider-agnostic compose flow (``mode="compose"``).
"""
from __future__ import annotations

import datetime
import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple

from llm_agents.online_search_summary.core.citations import (
    extract_cited_refs, normalize_refer, parse_publish_date,
)
from llm_agents.online_search_summary.core.errors import OnlineSearchError
from llm_agents.online_search_summary.core.models import (
    SHANGHAI_TZ, SearchHit, SearchIntent, SearchOptions, SearchResponse,
    SearchSummary,
)
from llm_agents.online_search_summary.core.prompts import native_search_prompt
from llm_agents.online_search_summary.providers.base import (
    BaseOnlineSearchProvider,
)

logger = logging.getLogger(__name__)


class ZhipuSearchProvider(BaseOnlineSearchProvider):
    """Zhipu BigModel implementation (open.bigmodel.cn, zhipu web search)."""

    name = "zhipu"
    API_KEY_ENV_VARS = ("GLM_ONLINE_SEARCH_KEY", "ZHIPU_API_KEY",
                        "BIGMODEL_API_KEY")
    ENGINES = {
        "search_std": "self-developed basic engine",
        "search_pro": "multi-engine, lowest empty-result rate",
        "search_pro_sogou": "Tencent ecosystem + Zhihu",
        "search_pro_quark": "vertical content",
    }
    DEFAULT_ENGINE = "search_pro"

    SEARCH_URL = "https://open.bigmodel.cn/api/paas/v4/web_search"
    CHAT_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    # The docs' examples use glm-4-air for search-in-chat; glm-5.2 is the
    # project default (accepts the same tools config). Override per
    # call/CLI when needed.
    DEFAULT_MODEL = "glm-5.2"

    # ------------------------------------------------------------------
    # Parsing helpers (shared by search + summarize response shapes)
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_hits(items: Any) -> List[SearchHit]:
        if not isinstance(items, list):
            return []
        hits: List[SearchHit] = []
        for i, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue
            title = (item.get("title") or "").strip()
            if not title:
                continue
            raw_date = item.get("publish_date")
            raw_date = str(raw_date).strip() if raw_date else None
            hits.append(SearchHit(
                refer=normalize_refer(item.get("refer"), i),
                title=title,
                content=(item.get("content") or "").strip() or None,
                link=(item.get("link") or "").strip() or None,
                media=(item.get("media") or "").strip() or None,
                icon=(item.get("icon") or "").strip() or None,
                publish_date=parse_publish_date(raw_date),
                publish_date_raw=raw_date or None,
            ))
        return hits

    @staticmethod
    def _parse_intents(items: Any) -> List[SearchIntent]:
        if not isinstance(items, list):
            return []
        return [SearchIntent(
            query=(it or {}).get("query"),
            intent=(it or {}).get("intent"),
            keywords=(it or {}).get("keywords"),
        ) for it in items if isinstance(it, dict)]

    def _search_payload(self, query: str, opts: SearchOptions) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "search_query": query,
            "search_engine": self.resolve_engine(opts.engine),
            "search_intent": bool(opts.intent),
            "count": opts.count,
            "search_recency_filter": opts.recency,
            "content_size": opts.content_size,
            "request_id": opts.request_id or str(uuid.uuid4()),
        }
        if opts.domain:
            payload["search_domain_filter"] = opts.domain
        return payload

    @staticmethod
    def _message(data: Dict[str, Any]) -> Dict[str, Any]:
        """choices[0].message of a chat completion ({} when absent)."""
        choices = data.get("choices") or []
        return (choices[0].get("message") or {}
                if isinstance(choices[0], dict) else {})

    @staticmethod
    def _created(data: Dict[str, Any]) -> Optional[datetime.datetime]:
        if isinstance(data.get("created"), int):
            return datetime.datetime.fromtimestamp(
                data["created"], tz=SHANGHAI_TZ)
        return None

    # ------------------------------------------------------------------
    # Contract implementation
    # ------------------------------------------------------------------
    async def search(self, query: str,
                     opts: Optional[SearchOptions] = None) -> SearchResponse:
        opts = opts or SearchOptions()
        opts.validate()
        if len(query) > 70:
            logger.warning("    [%s] search_query longer than the recommended "
                           "70 chars (%d) — provider may truncate",
                           self.name, len(query))
        data = await self._post_json_async(
            self.SEARCH_URL, self._search_payload(query, opts),
            timeout=self.search_timeout)
        hits = self._parse_hits(data.get("search_result"))
        logger.info("    [%s] engine=%s hits=%d request_id=%s", self.name,
                    data.get("search_engine") or opts.engine, len(hits),
                    data.get("request_id"))
        return SearchResponse(
            hits=hits,
            intents=self._parse_intents(data.get("search_intent")),
            provider=self.name,
            engine=data.get("search_engine") or opts.engine,
            request_id=data.get("request_id"),
            created=self._created(data))

    async def _chat_complete(
        self, messages, *, model: str,
        request_id: Optional[str] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        payload: Dict[str, Any] = {
            "model": model,
            "messages": list(messages),
            "stream": False,
        }
        if request_id:
            payload["request_id"] = request_id
        data = await self._post_json_async(self.CHAT_URL, payload,
                                           timeout=self.chat_timeout)
        message = self._message(data)
        return (message.get("content") or ""), (data.get("usage") or {})

    async def summarize(
        self, query: str, *, model: Optional[str] = None,
        opts: Optional[SearchOptions] = None,
        mode: str = "native",
        lang: str = "zh",
    ) -> SearchSummary:
        if mode not in ("native", "compose"):
            raise ValueError(f"mode must be native|compose, got {mode!r}")
        if mode == "compose":
            return await self.summarize_via_compose(query, model=model,
                                                    opts=opts, lang=lang)

        opts = opts or SearchOptions()
        opts.validate()
        model = model or self.DEFAULT_MODEL
        # Built per call: carries the output contract (subtitle >= 5 EN
        # words / >= 8 ZH chars, refs + keywords on every key point), the
        # answer language, and today's date. The literal {search_result}
        # placeholder is substituted by ZhiPu server-side and survives.
        search_prompt = native_search_prompt(lang)
        web_search_cfg: Dict[str, Any] = {
            "enable": True,
            "search_engine": self.resolve_engine(opts.engine),
            "search_result": True,          # echo message.web_search refs
            "search_prompt": search_prompt,
            "count": opts.count,
            "search_recency_filter": opts.recency,
            "content_size": opts.content_size,
        }
        if opts.domain:
            web_search_cfg["search_domain_filter"] = opts.domain
        payload: Dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": query}],
            "tools": [{"type": "web_search", "web_search": web_search_cfg}],
            "stream": False,
            "request_id": opts.request_id or str(uuid.uuid4()),
        }
        data = await self._post_json_async(self.CHAT_URL, payload,
                                           timeout=self.chat_timeout)
        message = self._message(data)
        answer = (message.get("content") or "").strip()
        if not answer:
            raise OnlineSearchError(
                f"{self.name}: chat completion returned no content "
                f"(request_id={data.get('request_id')})")
        # search_result=true echoes the retrieved references, but WHERE
        # depends on the model: glm-4-air puts them on the message
        # (message.web_search), glm-5.x at the top level of the response
        # (data.web_search). Accept both field spellings in both places.
        raw_hits = (message.get("web_search")
                    or message.get("search_result")
                    or data.get("web_search")
                    or data.get("search_result"))
        hits = self._parse_hits(raw_hits)
        if not hits:
            logger.warning("    [%s] native summary carried no web_search "
                           "references — falling back to compose mode",
                           self.name)
            return await self.summarize_via_compose(query, model=model,
                                                    opts=opts, lang=lang)
        cited = extract_cited_refs(answer)
        logger.info("    [%s] native summary model=%s hits=%d cited=%s "
                    "request_id=%s", self.name, model, len(hits),
                    cited or "none", data.get("request_id"))
        return SearchSummary(
            question=query, answer=answer, hits=hits,
            provider=self.name, model=model,
            engine=web_search_cfg["search_engine"], mode="native",
            request_id=data.get("request_id"), created=self._created(data),
            usage=data.get("usage"))
