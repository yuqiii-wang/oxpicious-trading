"""llm_agents.online_search_summary.providers.base — Abstract provider contract.

``BaseOnlineSearchProvider`` defines the contract every online-search(+summary)
provider implements and the shared plumbing around it:

  * API-key resolution from the project-root ``.env``
    (``API_KEY_ENV_VARS`` declares the provider's env vars, first match
    wins) — the transport itself lives in ``transport``
    (``RetryingJsonClient``), the summary prompts in ``core.prompts``;
  * ``summarize_via_compose`` — a provider-agnostic search-then-summarize
    flow following the documented ``{search_result}`` prompt pattern, with
    the citation tags under our control so refs always align.

Subclasses implement ``search`` (standalone web search), ``_chat_complete``
(plain LLM completion) and ``summarize``; register them in
``llm_agents.online_search_summary.providers.registry`` to expose them
through ``get_provider`` / the CLI.
"""
from __future__ import annotations

import abc
import datetime
import os
from typing import Any, ClassVar, Dict, Optional, Sequence, Tuple

import requests

from llm_agents.online_search_summary.core.errors import OnlineSearchError
from llm_agents.online_search_summary.core.citations import format_references
from llm_agents.online_search_summary.core.models import (
    SHANGHAI_TZ, SearchHit, SearchOptions, SearchResponse, SearchSummary,
)
from llm_agents.online_search_summary.core.prompts import summary_system_prompt
from llm_agents.online_search_summary.providers.transport import (
    RetryingJsonClient, load_project_env,
)


class BaseOnlineSearchProvider(abc.ABC):
    """Abstract online-search(+summary) provider.

    Subclasses implement the transport-specific ``search`` (standalone web
    search) and ``_chat_complete`` (plain LLM completion) methods; the
    shared plumbing here resolves the API key from the project ``.env``,
    delegates retrying JSON POSTs to ``RetryingJsonClient``, and offers
    ``summarize_via_compose`` — a provider-agnostic search-then-summarize
    flow following the documented ``{search_result}`` prompt pattern.
    Register new providers in ``PROVIDERS`` to expose them through
    ``get_provider`` / the CLI.
    """

    name: ClassVar[str] = "<base>"
    API_KEY_ENV_VARS: ClassVar[Sequence[str]] = ()
    ENGINES: ClassVar[Dict[str, str]] = {}
    DEFAULT_ENGINE: ClassVar[Optional[str]] = None

    # Retry policy handed to the transport (see RetryingJsonClient):
    # provider codes — ZhiPu 1701 = concurrency limit, 1234 = upstream
    # "network error, retry later".
    _RETRY_STATUSES = frozenset({429, 502, 503, 504})
    _RETRY_CODES = frozenset({"1701", "1234"})
    _RETRY_BACKOFF_SEC = (1.0, 3.0)

    def __init__(self, *, api_key: Optional[str] = None,
                 search_timeout: float = 30.0,
                 chat_timeout: float = 180.0,
                 max_attempts: int = 3) -> None:
        self.api_key = api_key or self._resolve_api_key()
        self.search_timeout = search_timeout
        self.chat_timeout = chat_timeout
        self.max_attempts = max(1, max_attempts)
        self._http = RetryingJsonClient(
            self.name, self.api_key, max_attempts=self.max_attempts,
            retry_statuses=self._RETRY_STATUSES,
            retry_codes=self._RETRY_CODES,
            backoff=self._RETRY_BACKOFF_SEC)

    # -- key / transport -------------------------------------------------
    @classmethod
    def _resolve_api_key(cls) -> str:
        load_project_env()
        for var in cls.API_KEY_ENV_VARS:
            key = os.environ.get(var, "").strip()
            if key:
                return key
        raise OnlineSearchError(
            f"{cls.name}: no API key — set one of {list(cls.API_KEY_ENV_VARS)} "
            f"in the project-root .env or environment.")

    @property
    def session(self) -> requests.Session:
        return self._http.session

    def resolve_engine(self, engine: Optional[str]) -> str:
        engine = engine or self.DEFAULT_ENGINE
        if engine not in self.ENGINES:
            raise ValueError(f"{self.name}: unknown engine {engine!r} "
                             f"(known: {sorted(self.ENGINES)})")
        return engine

    def _post_json(self, url: str, payload: Dict[str, Any],
                   *, timeout: float) -> Dict[str, Any]:
        return self._http.post_json(url, payload, timeout=timeout)

    async def _post_json_async(self, url: str, payload: Dict[str, Any],
                               *, timeout: float) -> Dict[str, Any]:
        return await self._http.post_json_async(url, payload, timeout=timeout)

    # -- abstract contract -----------------------------------------------
    @abc.abstractmethod
    async def search(self, query: str,
                     opts: Optional[SearchOptions] = None) -> SearchResponse:
        """Standalone web search; returns normalized hits (+ intents)."""

    @abc.abstractmethod
    async def _chat_complete(
        self, messages: Sequence[Dict[str, str]], *, model: str,
        request_id: Optional[str] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """Plain LLM completion -> (content, usage). No tools attached."""

    @abc.abstractmethod
    async def summarize(
        self, query: str, *, model: Optional[str] = None,
        opts: Optional[SearchOptions] = None,
        mode: str = "native",
        lang: str = "zh",
    ) -> SearchSummary:
        """Search + summarize; mode selects the provider's strategy.

        *lang* sets the answer language (default Chinese) and is folded
        into the summary prompts along with the output contract: every key
        point must carry a subtitle (>= 5 English words or >= 8 Chinese
        chars), an inline [来源：ref_N] citation, and keywords.
        """

    # -- shared composition flow -------------------------------------------
    async def summarize_hits_via_compose(
        self, query: str, hits: Sequence[SearchHit], *,
        model: Optional[str] = None,
        system_prompt: Optional[str] = None,
        lang: str = "zh",
        engine: Optional[str] = None,
        request_id: Optional[str] = None,
        created: Optional[datetime.datetime] = None,
    ) -> SearchSummary:
        """Compose summarization over PRE-FETCHED hits (no search here).

        For flows that control their own retrieval — e.g. the industry
        weekly Q&A's period-scoped backfill, which searches a year-month
        query, filters hits to the question's time range, then hands the
        survivors here; citation tags are rebuilt from the filtered set
        so refs always align."""
        model = model or getattr(self, "DEFAULT_MODEL", None)
        if not model:
            raise OnlineSearchError(
                f"{self.name}: no chat model configured for compose mode")
        if not hits:
            raise OnlineSearchError(
                f"{self.name}: no references to summarize for {query!r}")
        references = format_references(hits)
        user_msg = (
            f"今天的日期是{datetime.datetime.now(SHANGHAI_TZ):%Y-%m-%d}。\n\n"
            f"网络搜索结果：\n\n{references}\n\n"
            f"用户问题：{query}")
        content, usage = await self._chat_complete(
            [{"role": "system",
              "content": system_prompt or summary_system_prompt(lang)},
             {"role": "user", "content": user_msg}],
            model=model, request_id=request_id)
        return SearchSummary(
            question=query, answer=content, hits=list(hits),
            provider=self.name, model=model, engine=engine, mode="compose",
            request_id=request_id, created=created, usage=usage)

    async def summarize_via_compose(
        self, query: str, *, model: Optional[str] = None,
        opts: Optional[SearchOptions] = None,
        system_prompt: Optional[str] = None,
        lang: str = "zh",
    ) -> SearchSummary:
        """Two-step composition usable by ANY provider: ``search`` then a
        plain ``_chat_complete`` whose user message embeds the formatted
        references (the documented ``{search_result}`` pattern, with the
        citation tags under our control so refs always align)."""
        opts = opts or SearchOptions()
        resp = await self.search(query, opts)
        if not resp.hits:
            raise OnlineSearchError(
                f"{self.name}: search returned no references for {query!r}")
        return await self.summarize_hits_via_compose(
            query, resp.hits, model=model, system_prompt=system_prompt,
            lang=lang, engine=resp.engine,
            request_id=resp.request_id or opts.request_id,
            created=resp.created)
