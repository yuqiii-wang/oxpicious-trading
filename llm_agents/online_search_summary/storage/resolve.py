"""llm_agents.online_search_summary.storage.resolve — Per-reference resolution.

Classifies every citation tag of a stored summary as ``exact`` or
``relevant`` (text.llm_qa_refs.ref_type) and links it to a text.news row;
the typing vocabulary and match helpers live in ``storage.matching``, the
verification network calls in ``storage.fetching``. Per-reference
resolution is sequential (network-friendly, and later hits dedupe against
rows created for earlier ones):

  1. zhihu ONLINE search — the ref title is searched against live zhihu
     content via the zhihu provider (the downloads zhihu-news downloader
     API); the best title match's ContentText becomes the stored article
     content → ``relevant`` / 'zhihu'. When zhihu surfaces anything, this
     IS the resolution — ddgs is skipped.
  2. ddgs + markitdown (only when zhihu surfaces nothing) — DuckDuckGo
     (ddgs) search for the ref title, fetch the top candidate URL,
     extract the page with markitdown, and require the extracted text to
     CONTAIN the ref title (or its snippet). A confirmation at the
     response's own link → ``exact`` / 'summary' (the row is upserted
     with the fetched full content); a confirmation at a ddgs-found URL →
     ``relevant`` / 'ddgs'.
  3. nothing found — a NULL-CONTENT placeholder news row (title / source /
     date only, like the titles-only gov/ndrc rows) typed ``exact`` /
     'summary' — the metadata IS the response's own — so every ref links
     to a news_id and can be re-resolved by a later run (upsert refreshes
     typing + content in place).
"""
from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from _common.build_commons import bulk_upsert_async

from llm_agents.online_search_summary.core.citations import ref_number
from llm_agents.online_search_summary.core.models import (
    SHANGHAI_TZ, SearchHit,
)
from llm_agents.online_search_summary.storage.fetching import PageFetcher
from llm_agents.online_search_summary.storage.matching import (
    REF_TYPE_EXACT, REF_TYPE_RELEVANT, VIA_CORPUS, VIA_DDGS, VIA_SUMMARY,
    VIA_ZHIHU, hit_ref_time, is_exact_article, normalize_text,
    source_from_url,
)

logger = logging.getLogger(__name__)

NEWS_TABLE = "text.news"


# ----------------------------------------------------------------------------
# Resolution result
# ----------------------------------------------------------------------------
@dataclass
class ResolvedRef:
    """Outcome of resolving one citation tag to a text.news article."""
    ref: str                          # citation tag (ref_N)
    ref_type: str                     # exact (from summary) | relevant (post-hoc title match)
    news_id: int
    resolved_url: Optional[str] = None  # candidate URL the check ran against
    ref_time: Optional[datetime.datetime] = None  # publish time per the response (None -> now())
    via: str = VIA_SUMMARY            # provenance: summary | corpus | ddgs

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ref": self.ref,
            "ref_type": self.ref_type,
            "news_id": self.news_id,
            "resolved_url": self.resolved_url,
            "ref_time": (self.ref_time.isoformat() if self.ref_time
                         else None),
            "via": self.via,
        }


def build_ref_rows(
    qa_id: int,
    resolved: Sequence[ResolvedRef],
    *,
    cited: Sequence[str],
    now: Optional[datetime.datetime] = None,
) -> List[Dict[str, Any]]:
    """text.llm_qa_refs rows for one stored Q&A — one per resolved article.

    The table's PK is (qa_id, news_id): refs resolving to the same article
    (two tags, one page) collapse onto a single row keyed by a
    REPRESENTATIVE tag — the smallest tag the answer cites among the
    group, else the smallest tag — and ``is_used`` marks whether any cited
    tag hit the group. ``cited`` is the answer's own tag set
    (extract_cited_refs); refs the summarizer never cited are kept too,
    unused. ``now`` is the ref_time fallback (ref_time stays now() when
    the response carried no date).
    """
    groups: Dict[int, List[ResolvedRef]] = {}
    order: List[int] = []
    for r in resolved:
        if r.news_id not in groups:
            groups[r.news_id] = []
            order.append(r.news_id)
        groups[r.news_id].append(r)
    fallback = now or datetime.datetime.now(SHANGHAI_TZ)
    wanted = set(cited)
    rows: List[Dict[str, Any]] = []
    for news_id in order:
        group = groups[news_id]
        used = [r for r in group if r.ref in wanted]
        repr_ref = min(used or group, key=lambda r: ref_number(r.ref))
        rows.append({
            "qa_id": qa_id,
            "ref": repr_ref.ref,
            "ref_type": repr_ref.ref_type,
            "news_id": news_id,
            "is_used": bool(used),
            "resolved_url": repr_ref.resolved_url,
            "ref_time": repr_ref.ref_time or fallback,
            "resolved_via": repr_ref.via,
        })
    return rows


class RefResolver:
    """Resolves SearchHits to typed text.news rows (see module docstring).

    All blocking work (ddgs search, page fetch + markitdown conversion)
    runs via the ``PageFetcher``'s thread wrappers; the DB writes stay on
    the event loop's asyncpg connection.
    """

    def __init__(self, conn, *, industry_id: Optional[str] = None,
                 taxonomy=None, ddgs_max_results: int = 5,
                 max_fetches: int = 2, fetch_timeout: float = 20.0,
                 content_max_chars: int = 50_000) -> None:
        self._conn = conn
        self._industry_id = industry_id
        self._taxonomy = taxonomy
        self.max_fetches = max(1, max_fetches)
        self.content_max_chars = content_max_chars
        self._fetcher = PageFetcher(ddgs_max_results=ddgs_max_results,
                                    fetch_timeout=fetch_timeout)
        self._zhihu = None            # lazy zhihu online-search provider
        self._zhihu_broken = False    # set when the provider errors

    # -- public entry ------------------------------------------------------
    async def resolve(self, hits: Sequence[SearchHit]) -> List[ResolvedRef]:
        """Resolve every hit, in order (sequential: dedupe-friendly)."""
        if self._taxonomy is None:
            from builds.text.keywords import get_taxonomy
            self._taxonomy = get_taxonomy()
        out: List[ResolvedRef] = []
        for hit in hits:
            resolved = await self._resolve_one(hit)
            out.append(resolved)
            logger.info("    [resolve] %s -> %s/%s (news_id=%s, url=%s)",
                        hit.refer, resolved.ref_type, resolved.via,
                        resolved.news_id, resolved.resolved_url or "-")
        n_exact = sum(1 for r in out if r.ref_type == REF_TYPE_EXACT)
        logger.info("    [resolve] %d ref(s): %d exact, %d relevant",
                    len(out), n_exact, len(out) - n_exact)
        return out

    async def _resolve_one(self, hit: SearchHit) -> ResolvedRef:
        ref_time = hit_ref_time(hit)
        # 1. zhihu ONLINE search — the ref title searched against live
        #    zhihu content (the zhihu provider / downloader API); a match
        #    links the real zhihu article with its ContentText and is the
        #    resolution — ddgs is only the fallback when zhihu has nothing.
        res = await self._match_zhihu_online(hit)
        if res is not None:
            news_id, link = res
            return ResolvedRef(hit.refer, REF_TYPE_RELEVANT, news_id, link,
                               ref_time, via=VIA_ZHIHU)

        # 2. ddgs search + markitdown content verification. A confirmation
        #    at the response's own link means the article IS the one the
        #    summary returned (exact/summary); a ddgs-discovered candidate
        #    is a post-summary title find (relevant/ddgs).
        candidates = await self._candidate_urls(hit)
        tried_url: Optional[str] = candidates[0] if candidates else hit.link
        for url in candidates[:self.max_fetches]:
            page_text = await self._fetcher.page_markdown(url)
            if page_text and is_exact_article(hit.title, hit.content,
                                              page_text):
                news_id = await self._upsert_news_row(
                    hit, content=page_text[:self.content_max_chars],
                    source=(source_from_url(url) or hit.media or "web"),
                    url=url)
                from_summary = (hit.link is not None and url == hit.link)
                return ResolvedRef(
                    hit.refer,
                    REF_TYPE_EXACT if from_summary else REF_TYPE_RELEVANT,
                    news_id, url, ref_time,
                    via=VIA_SUMMARY if from_summary else VIA_DDGS)

        # 3. search not found -> null-content placeholder news row carrying
        #    the response's own title/link/date metadata.
        news_id = await self._upsert_news_row(hit, content=None,
                                              source=hit.media or "web",
                                              url=hit.link)
        return ResolvedRef(hit.refer, REF_TYPE_EXACT, news_id, tried_url,
                           ref_time, via=VIA_SUMMARY)

    # -- step 1: zhihu ONLINE search -----------------------------------------
    async def _match_zhihu_online(
        self, hit: SearchHit,
    ) -> Optional[Tuple[int, Optional[str]]]:
        """Search zhihu ONLINE for the ref title (the zhihu provider wraps
        the downloads zhihu-news downloader API); the best title match's
        ContentText is stored as the article content. Returns
        (news_id, link), or None when the provider is unavailable or zhihu
        surfaces nothing (the caller then falls back to ddgs)."""
        if self._zhihu_broken:
            return None
        try:
            if self._zhihu is None:
                from llm_agents.online_search_summary.providers.registry                     import get_provider
                self._zhihu = get_provider("zhihu")()
            resp = await self._zhihu.search(hit.title)
        except Exception as e:
            self._zhihu_broken = True
            logger.warning("    [resolve] zhihu online search disabled: "
                           "%s: %s", type(e).__name__, e)
            return None
        # best title match first; zhihu content itself is trusted, so even
        # a weaker hit resolves here — ddgs is never consulted when zhihu
        # surfaced anything.
        t = normalize_text(hit.title)
        best = None
        best_len = 0
        first_content = None
        for h in resp.hits:
            ht = normalize_text(h.title)
            if h.content is not None and first_content is None:
                first_content = h
            if not ht:
                continue
            if t and (t in ht or ht in t):
                if best is None or len(ht) > best_len:
                    best, best_len = h, len(ht)
        if best is None:
            best = first_content
        if best is None or best.content is None:
            return None
        news_id = await self._upsert_news_row(
            best, content=best.content[:self.content_max_chars],
            source="zhihu", url=best.link)
        return news_id, best.link

    # -- step 2: ddgs + markitdown -------------------------------------------
    async def _candidate_urls(self, hit: SearchHit) -> List[str]:
        """URLs to try: the hit's own link when present, plus ddgs hits.

        ddgs runs even when the hit carries a link — the provided URL may
        be dead or bot-blocked, and a search hit can still verify exact.
        """
        urls = [hit.link] if hit.link else []
        results = await self._fetcher.search_web_async(hit.title)
        for r in results:
            href = r.get("href")
            if href and href not in urls:
                urls.append(href)
        return urls

    # -- row shaping / upsert (steps 2 + 3) -----------------------------------
    async def _upsert_news_row(self, hit: SearchHit, *, content: Optional[str],
                               source: str, url: Optional[str]) -> int:
        """Upsert one text.news row for the hit; return its news_id.

        ``content=None`` creates the null-content placeholder; a later
        exact resolution upserts over it in place (same natural key).
        """
        fallback_date = datetime.datetime.now(SHANGHAI_TZ).date()
        text = hit.title + "\n" + (content or "")
        if self._industry_id is not None:
            industry_id = self._industry_id
        else:
            _, industry_id = self._taxonomy.match(text)
        row = {
            "title": hit.title,
            "content": content,
            "date": hit.publish_date or fallback_date,
            "source": source,
            "url": url,
            "author": None,
            "industry_id": industry_id,
            "sector_id": self._taxonomy.sector_of(industry_id),
            "word_count": self._taxonomy.word_count(text) if content else None,
            "votes": None,
        }
        await bulk_upsert_async(self._conn, NEWS_TABLE, [row],
                                ["title", "source", "date"])
        news_id = await self._conn.fetchval(
            f'SELECT news_id FROM {NEWS_TABLE} '
            f'WHERE title = $1 AND source = $2 AND date = $3',
            row["title"], row["source"], row["date"])
        return int(news_id)
