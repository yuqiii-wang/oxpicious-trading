"""llm_agents.online_search_summary.storage.store — text-schema persistence.

``OnlineSearchSummaryStore`` persists a SearchSummary (one async conn):

  * references -> text.news rows, resolved per citation tag by
    ``RefResolver`` (llm_agents.online_search_summary.storage.resolve):
    the response's own article (echo metadata, or its link fetched and
    content-confirmed) is stored as ``exact`` / resolved_via 'summary';
    articles found AFTER the summary by title search — a corpus title
    match or a ddgs-discovered candidate page — as ``relevant``
    ('corpus' / 'ddgs'); unresolvable refs as a NULL-CONTENT placeholder
    news row carrying the echo's own metadata (``exact``) — every ref
    links to a news_id either way;
  * the per-ref typing lands in ``text.llm_qa_refs`` (qa_id, news_id) ->
    (ref — the representative citation tag, ref_type, is_used — whether
    the answer cites the article, resolved_url, ref_time — the
    reference's publish time per the search response, now() when absent,
    resolved_via — the provenance behind ref_type), upserted so
    re-storing refreshes the resolution in place; every hit lands here,
    cited or not (build_ref_rows collapses same-article refs);
  * Q&A -> text.llm_qa through llm_agents.llm_qa.upsert_qa with the
    resolved news_ids grouped as provenance (text.news_groups ->
    text.news_group_items), the formatted reference list as ``context``,
    and llm_model = the chat model.

``resolve=False`` (CLI ``--no-resolve``) keeps the legacy fast path: all
hits become snippet-content text.news rows typed ``exact`` /
resolved_via 'summary' — the rows are the response's own echo hits; no
network beyond the search call itself. Keyword rows (text.news_keywords)
are deliberately NOT written here — corpus keyword stats stay owned by
the builds.text pipeline (see the DDL contract in
database/sql/text/01_news.sql; llm_qa_refs DDL: 03_llm_qa_refs.sql).
"""
from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from _common.build_commons import bulk_upsert_async

from llm_agents.online_search_summary.core.citations import format_references
from llm_agents.online_search_summary.core.models import (
    SHANGHAI_TZ, SearchHit, SearchSummary,
)
from llm_agents.online_search_summary.storage.matching import (
    REF_TYPE_EXACT, VIA_SUMMARY, hit_ref_time,
)
from llm_agents.online_search_summary.storage.resolve import (
    RefResolver, ResolvedRef, build_ref_rows,
)

logger = logging.getLogger(__name__)


class OnlineSearchSummaryStore:
    """Persists search references + Q&A summaries into the text schema."""

    NEWS_TABLE = "text.news"
    REFS_TABLE = "text.llm_qa_refs"
    QA_TABLE = "text.llm_qa"
    _NEWS_COLUMNS = ("title", "content", "date", "source", "url", "author",
                     "industry_id", "sector_id", "word_count", "votes")

    def __init__(self, conn) -> None:
        self._conn = conn

    # -- text.news --------------------------------------------------------
    @staticmethod
    def build_news_rows(
        hits: Sequence[SearchHit],
        *,
        industry_id: Optional[str] = None,
        sector_id: Optional[str] = None,
        fallback_date: Optional[datetime.date] = None,
    ) -> List[Dict[str, Any]]:
        """Project hits onto text.news column rows, deduped on the natural
        key (title, source, date) — an in-batch PK duplicate would make
        bulk_upsert raise "cannot affect row a second time"."""
        fallback_date = fallback_date or datetime.datetime.now(
            SHANGHAI_TZ).date()
        seen: set = set()
        rows: List[Dict[str, Any]] = []
        for h in hits:
            source = h.media or "web"
            date = h.publish_date or fallback_date
            key = (h.title, source, date)
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "title": h.title,
                "content": h.content,
                "date": date,
                "source": source,
                "url": h.link,
                "author": None,          # search results carry no author
                "industry_id": industry_id,   # taxonomy fill below (or this)
                "sector_id": sector_id,       # taxonomy fill below (or this)
                "word_count": None,           # taxonomy fill below
                "votes": None,
            })
        return rows

    async def _fast_resolve(
        self, hits: Sequence[SearchHit],
        *, industry_id: Optional[str] = None,
    ) -> List[ResolvedRef]:
        """No-network path: snippet-content rows, every ref ``exact`` /
        'summary' — the rows ARE the response's echo hits (title, link,
        date metadata straight from the provider).

        One ResolvedRef per hit (hits sharing an article each keep their
        own ref pointing at the same news_id).
        """
        if not hits:
            return []
        # One fallback date for the whole batch so build_news_rows and the
        # key mapping below agree on the (title, source, date) natural key
        # even across a midnight boundary.
        fallback_date = datetime.datetime.now(SHANGHAI_TZ).date()
        rows = self.build_news_rows(
            hits, industry_id=industry_id,
            sector_id=self._sector_of(industry_id),
            fallback_date=fallback_date)
        self._fill_taxonomy(rows, industry_id)
        rows = [{c: r.get(c) for c in self._NEWS_COLUMNS} for r in rows]
        await bulk_upsert_async(self._conn, self.NEWS_TABLE, rows,
                                ["title", "source", "date"])
        keys = await self._conn.fetch(
            f'SELECT news_id, title, source, date FROM {self.NEWS_TABLE} '
            f'WHERE (title, source, date) IN ('
            f'  SELECT * FROM unnest($1::text[], $2::text[], $3::date[]))',
            [r["title"] for r in rows], [r["source"] for r in rows],
            [r["date"] for r in rows])
        id_by_key = {(r["title"], r["source"], r["date"]): r["news_id"]
                     for r in keys}
        out = []
        for h in hits:
            key = (h.title, h.media or "web",
                   h.publish_date or fallback_date)
            nid = id_by_key.get(key)
            if nid is None:
                # A re-find miss right after the batch upsert means key
                # drift — rewrite the row alone and fetch by its exact
                # natural key, so every hit keeps its news_id.
                nid = await self._reupsert_news_row(h, industry_id,
                                                    fallback_date)
            if nid is not None:
                out.append(ResolvedRef(h.refer, REF_TYPE_EXACT, nid,
                                       h.link, hit_ref_time(h),
                                       via=VIA_SUMMARY))
        return out

    @staticmethod
    def _sector_of(industry_id: Optional[str]) -> Optional[str]:
        if industry_id is None:
            return None
        from builds.text.keywords import get_taxonomy
        return get_taxonomy().sector_of(industry_id)

    @staticmethod
    def _fill_taxonomy(rows: List[Dict[str, Any]],
                       industry_id: Optional[str]) -> None:
        """In-place taxonomy fill shared by the batch and fallback paths."""
        from builds.text.keywords import get_taxonomy
        taxonomy = get_taxonomy()
        for row in rows:
            if industry_id is None:
                text = row["title"] + "\n" + (row.get("content") or "")
                _, row["industry_id"] = taxonomy.match(text)
                row["sector_id"] = taxonomy.sector_of(row["industry_id"])
            row["word_count"] = taxonomy.word_count(
                row["title"] + "\n" + (row.get("content") or ""))

    async def _reupsert_news_row(
        self, hit: SearchHit, industry_id: Optional[str],
        fallback_date: datetime.date,
    ) -> Optional[int]:
        """Re-upsert one hit as a single row; return its news_id (None —
        logged — only if the exact-key fetch still misses, which must not
        crash the ask)."""
        rows = self.build_news_rows([hit], industry_id=industry_id,
                                    sector_id=self._sector_of(industry_id),
                                    fallback_date=fallback_date)
        self._fill_taxonomy(rows, industry_id)
        row = {c: rows[0].get(c) for c in self._NEWS_COLUMNS}
        await bulk_upsert_async(self._conn, self.NEWS_TABLE, [row],
                                ["title", "source", "date"])
        nid = await self._conn.fetchval(
            f'SELECT news_id FROM {self.NEWS_TABLE} '
            f'WHERE title = $1 AND source = $2 AND date = $3',
            row["title"], row["source"], row["date"])
        if nid is None:
            logger.error("    [DB] %s: news row re-find failed for %r",
                         type(self).__name__, hit.title[:60])
        return nid

    # -- text.llm_qa + text.llm_qa_refs --------------------------------------
    async def store_summary(
        self, summary: SearchSummary,
        *,
        industry_id: Optional[str] = None,
        category: Optional[str] = None,
        language: str = "zh",
        resolve: bool = True,
        qa_date: Optional[datetime.datetime] = None,
    ) -> Tuple[int, List[ResolvedRef]]:
        """Store the summary; return (qa_id, per-ref resolutions).

        With *resolve* (default), each citation tag is classified by
        RefResolver: corpus/zhihu match or ddgs+markitdown verification,
        falling back to a null-content news placeholder. Every resolution
        is written to text.llm_qa_refs on (qa_id, ref). *qa_date* is the
        question's own data date (text.llm_qa.qa_date); None keeps the
        now() default.
        """
        from llm_agents.llm_qa import upsert_qa

        if resolve:
            resolved = await RefResolver(
                self._conn, industry_id=industry_id).resolve(summary.hits)
        else:
            resolved = await self._fast_resolve(summary.hits,
                                                industry_id=industry_id)

        # Group news_ids in ref order, deduplicated (two refs may resolve
        # to the same article).
        group_ids: List[int] = []
        seen: set = set()
        for r in resolved:
            if r.news_id not in seen:
                seen.add(r.news_id)
                group_ids.append(r.news_id)
        if not group_ids:
            logger.warning("    [DB] no reference rows resolved — storing "
                           "the Q&A without news-group provenance")
        context = format_references(summary.hits)
        from builds.text.keywords import get_taxonomy
        pinned_sector = get_taxonomy().sector_of(industry_id)
        qa_industry, qa_sector = industry_id, pinned_sector
        if qa_industry is None or qa_sector is None:
            # Surface the taxonomy's primary industry/sector of the stored
            # refs (either may already be pinned above).
            row = await self._conn.fetchrow(
                f'SELECT (SELECT industry_id FROM {self.NEWS_TABLE} '
                f'        WHERE news_id = ANY($1::bigint[]) '
                f'          AND industry_id IS NOT NULL LIMIT 1) AS industry_id, '
                f'       (SELECT sector_id FROM {self.NEWS_TABLE} '
                f'        WHERE news_id = ANY($1::bigint[]) '
                f'          AND sector_id IS NOT NULL LIMIT 1) AS sector_id',
                group_ids)
            if row is not None:
                qa_industry = qa_industry or row["industry_id"]
                qa_sector = qa_sector or row["sector_id"]
        qa_id = await upsert_qa(
            self._conn, summary.question, summary.answer,
            context=context or None, category=category,
            industry_id=qa_industry, sector_id=qa_sector,
            news_ids=group_ids or None,
            llm_model=summary.model, language=language, qa_date=qa_date)
        logger.info("    [DB] stored text.llm_qa qa_id=%s (sources=%d)",
                    qa_id, len(group_ids))
        # is_used per ref: the answer's own citation tags (the LLM-answer
        # half of the search/summary separation — refs load regardless).
        await self._upsert_qa_refs(qa_id, resolved,
                                   cited=summary.cited_refs)
        return qa_id, resolved

    async def _upsert_qa_refs(
        self, qa_id: int, resolved: Sequence[ResolvedRef],
        *, cited: Sequence[str],
    ) -> bool:
        """Write text.llm_qa_refs rows (PK qa_id+news_id; re-store
        refreshes) and set text.llm_qa.has_refs to whether ref rows now
        exist for this qa_id; return the flag.

        build_ref_rows collapses same-article refs onto one row per
        article — representative ref tag, is_used from the answer's cited
        tags; every resolved ref loads, cited or not. ref_time carries the
        reference's publish time as reported by the search response; refs
        the response carried no date for get now(). resolved_via records
        the provenance ('summary' | 'corpus' | 'ddgs' | 'zhihu' — see
        storage.matching) that ref_type is derived from.

        Deliberately NON-FATAL: the Q&A row is already stored at this
        point, so a failed refs write logs a warning instead of raising
        — one bad ref batch must not fail the ask (stored-record count
        has to track the provider's request count). has_refs then
        mirrors the table: no ref rows -> false.
        """
        if resolved:
            rows = build_ref_rows(qa_id, resolved, cited=cited)
            try:
                await bulk_upsert_async(self._conn, self.REFS_TABLE, rows,
                                        ["qa_id", "news_id"])
                n_used = sum(1 for r in rows if r["is_used"])
                logger.info("    [DB] %d text.llm_qa_refs row(s) written "
                            "(used=%d, unused=%d)", len(rows), n_used,
                            len(rows) - n_used)
            except Exception as e:
                logger.warning("    [DB] text.llm_qa_refs write FAILED "
                               "for qa_id=%s — keeping the Q&A (has_refs "
                               "stays false unless older refs exist): "
                               "%s: %s", qa_id, type(e).__name__, e)
        # has_refs mirrors the table state: true iff ref rows exist.
        has_refs = await self._conn.fetchval(
            f'SELECT EXISTS (SELECT 1 FROM {self.REFS_TABLE} '
            f'WHERE qa_id = $1)', qa_id)
        await self._conn.execute(
            f'UPDATE {self.QA_TABLE} SET has_refs = $2 WHERE qa_id = $1',
            qa_id, has_refs)
        return bool(has_refs)
