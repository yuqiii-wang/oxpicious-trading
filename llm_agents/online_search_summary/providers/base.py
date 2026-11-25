"""llm_agents.online_search_summary.providers.base — Abstract provider contract.

``BaseOnlineSearchProvider`` defines the contract every online-search(+summary)
provider implements on top of the shared client plumbing
(``llm_agents._core.base.BaseLlmProvider``: API-key resolution from the
project-root ``.env``, the retrying JSON transport, and the POST
helpers). The domain half kept here:

  * the search-engine vocabulary (``ENGINES`` / ``DEFAULT_ENGINE`` /
    ``resolve_engine``);
  * per-call ``search_timeout`` / ``chat_timeout`` knobs;
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
import logging
from typing import Any, ClassVar, Dict, Optional, Sequence, Tuple

from llm_agents._core.base import BaseLlmProvider
from llm_agents.online_search_summary.core.errors import OnlineSearchError
from llm_agents.online_search_summary.core.citations import format_references
from llm_agents.online_search_summary.core.date_scan import DateScanner
from llm_agents.online_search_summary.core.models import (
    SHANGHAI_TZ, SearchHit, SearchOptions, SearchResponse, SearchSummary,
)
from llm_agents.online_search_summary.core.prompts import (
    stale_refs_check_block, summary_system_prompt,
)

logger = logging.getLogger(__name__)


class BaseOnlineSearchProvider(BaseLlmProvider):
    """Abstract online-search(+summary) provider.

    Subclasses implement the transport-specific ``search`` (standalone web
    search) and ``_chat_complete`` (plain LLM completion) methods; the
    shared plumbing (API key from the project ``.env``, the retrying JSON
    transport, POST helpers) is inherited from ``BaseLlmProvider``, and
    this base offers ``summarize_via_compose`` — a provider-agnostic
    search-then-summarize flow following the documented
    ``{search_result}`` prompt pattern. Register new providers in
    ``PROVIDERS`` to expose them through ``get_provider`` / the CLI.
    """

    ENGINES: ClassVar[Dict[str, str]] = {}
    DEFAULT_ENGINE: ClassVar[Optional[str]] = None

    def __init__(self, *, api_key: Optional[str] = None,
                 search_timeout: float = 30.0,
                 chat_timeout: float = 180.0,
                 max_attempts: int = 3) -> None:
        super().__init__(api_key=api_key, max_attempts=max_attempts)
        self.search_timeout = search_timeout
        self.chat_timeout = chat_timeout

    # -- engines ----------------------------------------------------------
    def resolve_engine(self, engine: Optional[str]) -> str:
        engine = engine or self.DEFAULT_ENGINE
        if engine not in self.ENGINES:
            raise ValueError(f"{self.name}: unknown engine {engine!r} "
                             f"(known: {sorted(self.ENGINES)})")
        return engine

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
        mode: str = "compose",
        lang: str = "zh",
    ) -> SearchSummary:
        """Search + summarize; mode selects the provider's strategy.

        Default ``compose`` — the documented two-step where ``search``
        and the LLM answer stay separated (refs cannot go missing).
        ``native`` is the provider's coupled one-shot, when it has one.

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
        context: Optional[str] = None,
        anchor_date: Optional[datetime.date] = None,
    ) -> SearchSummary:
        """Compose summarization over PRE-FETCHED hits (no search here).

        For flows that control their own retrieval: they search first,
        filter the hits to the question's time range, then hand the
        survivors here; citation tags are rebuilt from the filtered set
        so refs always align. ``context`` is optional caller-supplied
        text anchored between the date and the search results (the AI
        Ask chart adviser passes its plot-info block there).

        ``anchor_date`` is the date the QUESTION is about, for asks about
        a PAST time point (the coverage backfill's why-did-X-move-on-D
        questions): the whole-text date scan anchors on it instead of
        today — a reference whose entire text only mentions years older
        than the anchor year is the stale one — and the user message
        pins the target date with an explicit no-post-dated-sources
        instruction. None (default) keeps the exact today-anchored
        behavior.

        The WHOLE-TEXT DATE SCAN double check rides every call: a
        reference whose entire text (title + snippet) only mentions
        years older than the scan anchor's year — the undated snippet
        describing 2023 while the question is about 2026 — is named in a
        caution block appended to the user message (the model must
        double check it before citing it for the asked state); the
        flagged tags ride back on the summary as ``stale_text_refs``."""
        model = model or getattr(self, "DEFAULT_MODEL", None)
        if not model:
            raise OnlineSearchError(
                f"{self.name}: no chat model configured for compose mode")
        if not hits:
            raise OnlineSearchError(
                f"{self.name}: no references to summarize for {query!r}")
        references = format_references(hits)
        today = datetime.datetime.now(SHANGHAI_TZ)
        scan_anchor = anchor_date or today.date()
        scanner = DateScanner()
        stale_refs = [
            (h.refer, scan.sample_years())
            for h in hits
            if (scan := scanner.scan(
                f"{h.title}\n{h.content or ''}")).is_all_older(
                scan_anchor)
        ]
        check_block = stale_refs_check_block(
            stale_refs, today.date(), anchor=scan_anchor)
        if stale_refs:
            logger.info("    [%s] whole-text date scan: %d/%d reference(s) "
                        "only mention pre-%d years — double-check block "
                        "appended (%s)", self.name, len(stale_refs),
                        len(hits), scan_anchor.year,
                        ", ".join(t for t, _ in stale_refs))
        user_msg = (
            f"今天的日期是{today:%Y-%m-%d}。\n\n"
            + (f"问题所问的目标日期是{anchor_date:%Y-%m-%d}；引用来源的发布"
               f"时间不得晚于该日期。\n\n" if anchor_date else "")
            + (f"{context}\n\n" if context else "")
            + f"网络搜索结果：\n\n{references}\n\n"
            + (f"{check_block}\n\n" if check_block else "")
            + f"用户问题：{query}")
        content, usage = await self._chat_complete(
            [{"role": "system",
              "content": system_prompt or summary_system_prompt(lang)},
             {"role": "user", "content": user_msg}],
            model=model, request_id=request_id)
        return SearchSummary(
            question=query, answer=content, hits=list(hits),
            provider=self.name, model=model, engine=engine, mode="compose",
            request_id=request_id, created=created, usage=usage,
            stale_text_refs=[t for t, _ in stale_refs] or None)

    async def summarize_via_compose(
        self, query: str, *, model: Optional[str] = None,
        opts: Optional[SearchOptions] = None,
        system_prompt: Optional[str] = None,
        lang: str = "zh",
    ) -> SearchSummary:
        """Two-step composition usable by ANY provider: ``search`` then a
        plain ``_chat_complete`` whose user message embeds the formatted
        references (the documented ``{search_result}`` pattern, with the
        citation tags under our control so refs always align).

        When ``opts.ref_recency_days`` > 0 the POST-SEARCH pre-filter
        drops references published before that window — a stronger
        guarantee than the engine's own coarse recency knob (the recency
        DOUBLE CHECK) — and the survivors, renumbered, are what the
        summary LLM sees; when the filter empties the hit set the flow
        raises no-references like an empty search would."""
        opts = opts or SearchOptions()
        resp = await self.search(query, opts)
        hits = resp.hits
        if opts.ref_recency_days > 0 and hits:
            # storage.matching is pure (core.models only) — but importing
            # it pulls the package facade, so keep the import at this
            # seam like the rest of the search stack's lazy edges.
            from llm_agents.online_search_summary.storage.matching import (
                RefRecencyFilter,
            )
            outcome = RefRecencyFilter(
                window_days=opts.ref_recency_days).apply(hits)
            if outcome.n_dropped:
                logger.info(
                    "    [%s] ref pre-filter (-%dd): dropped %d/%d "
                    "reference(s) as stale, %d survive",
                    self.name, opts.ref_recency_days, outcome.n_dropped,
                    len(hits), len(outcome.hits))
            hits = outcome.hits
        if not hits:
            raise OnlineSearchError(
                f"{self.name}: search returned no references for {query!r}")
        return await self.summarize_hits_via_compose(
            query, hits, model=model, system_prompt=system_prompt,
            lang=lang, engine=resp.engine,
            request_id=resp.request_id or opts.request_id,
            created=resp.created)
