"""llm_agents.news_digestions.store — text.news_digestions persistence.

One digestion row per article (PK news_id): the upsert overwrites in
place, so re-digesting an article with a newer model refreshes its row —
history is deliberately not kept (DDL contract:
database/sql/text/04_news_digestions.sql).

  * ``fetch_pending_articles`` — the work queue: articles with no
    digestion row yet (``force=True`` widens to every article in scope,
    overwriting), optionally filtered by date window / industry / source.
    Title-only rows (the null-content gov/ndrc + unresolved-ref
    placeholders) are skipped unless ``include_empty`` — a digestion of a
    bare title would burn API calls on noise.
  * ``fetch_prior_articles``  — the fake-news detector's prior hunt: the
    same ORIGIN's (author when captured, else source) strictly-earlier
    items inside a lookback window, newest first.
  * ``upsert_digestions``     — bulk ON CONFLICT (news_id) DO UPDATE.
  * ``fetch_digestions``      — newest-first listing for the CLI (joined
    back to text.news for title/source).

industry_id / date are denormalized copies of the text.news values at
digestion time (same pattern as text.news_keywords) — they are NOT
back-filled when the parent row is re-tagged; --force re-digestion
refreshes them.
"""
from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, List, Optional, Sequence

from _common.build_commons import bulk_upsert_async
from _common.tokenization import SearchIndexer

from builds.text.upsert import NEWS_TABLE

logger = logging.getLogger(__name__)

DIGESTIONS_TABLE = "text.news_digestions"
DIGESTION_COLUMNS = ("news_id", "date", "industry_id", "summary",
                     "sentiment_level", "p_bullish", "p_bearish",
                     "intensity_score", "category", "is_fake_news",
                     "llm_model")


async def fetch_pending_articles(
    conn,
    *,
    start: Optional[datetime.date] = None,
    end: Optional[datetime.date] = None,
    industry_id: Optional[str] = None,
    source: Optional[str] = None,
    include_empty: bool = False,
    force: bool = False,
    news_ids: Optional[Sequence[int]] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Articles in scope still missing a text.news_digestions row.

    Newest first. ``force`` selects every in-scope article regardless of
    existing digestions (they get overwritten on upsert); ``include_empty``
    also admits NULL/empty-content rows (title-only placeholders);
    ``news_ids`` (the builds.text side task's just-loaded ids, empty list
    = no rows) further restricts the queue to those ids.
    """
    sql = (
        f'SELECT n.news_id, n.title, n.content, n.date, n.source, '
        f'       n.author, n.industry_id, n.word_count '
        f'FROM {NEWS_TABLE} n '
        f'LEFT JOIN {DIGESTIONS_TABLE} d ON d.news_id = n.news_id '
        f'WHERE ($1::date IS NULL OR n.date >= $1) '
        f'  AND ($2::date IS NULL OR n.date <= $2) '
        f'  AND ($3::text IS NULL OR n.industry_id = $3) '
        f'  AND ($4::text IS NULL OR n.source = $4) '
        f'  AND (d.news_id IS NULL OR $5) '
        f'  AND ($6 OR (n.content IS NOT NULL AND btrim(n.content) <> \'\')) '
        f'  AND ($7::bigint[] IS NULL OR n.news_id = ANY($7)) '
        f'ORDER BY n.date DESC, n.news_id DESC')
    params: List[Any] = [start, end, industry_id, source,
                         force, include_empty,
                         list(news_ids) if news_ids else None]
    if limit:
        sql += ' LIMIT $8'
        params.append(limit)
    return [dict(r) for r in await conn.fetch(sql, *params)]


async def fetch_prior_articles(
    conn,
    article: Dict[str, Any],
    *,
    lookback_days: int = 14,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """The fake-news detector's prior hunt: the same ORIGIN's strictly
    earlier items inside the lookback window, newest first.

    ORIGIN = author when the article carries one (zhihu answerers — the
    daily-series accounts), else the source at large (authorless items:
    the asking-titled 复盘/明天 articles). Same-day items are excluded —
    the rule reads what the origin said on EARLIER days.
    """
    author = (article.get("author") or "").strip()
    sql = (
        f'SELECT n.news_id, n.title, n.content, n.date, n.source, '
        f'       n.author, n.industry_id, n.word_count '
        f'FROM {NEWS_TABLE} n '
        f'WHERE n.source = $1 '
        f'  AND ($2::text IS NULL OR n.author = $2) '
        f'  AND n.date < $3 AND n.date >= $3 - ($4::int) '
        f'  AND n.news_id <> $5 '
        f'  AND n.content IS NOT NULL AND btrim(n.content) <> \'\' '
        f'ORDER BY n.date DESC, n.news_id DESC LIMIT $6')
    rows = await conn.fetch(sql, article.get("source"), author or None,
                            article["date"], lookback_days,
                            article["news_id"], limit)
    return [dict(r) for r in rows]


async def fetch_undetected_articles(
    conn,
    *,
    start: Optional[datetime.date] = None,
    end: Optional[datetime.date] = None,
    industry_id: Optional[str] = None,
    source: Optional[str] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Digested rows still at the detector's un-classified default
    (``category = 99`` other) — the category backfill's work queue.

    Joined back to text.news for the article fields the detector reads
    (newest first). Re-running the backfill after an interruption skips
    everything already classified — the queue shrinks to the remainder.
    """
    sql = (
        f'SELECT n.news_id, n.title, n.content, n.date, n.source, '
        f'       n.author, n.industry_id, n.word_count '
        f'FROM {DIGESTIONS_TABLE} d '
        f'JOIN {NEWS_TABLE} n ON n.news_id = d.news_id '
        f'WHERE d.category = 99 '
        f'  AND ($1::date IS NULL OR n.date >= $1) '
        f'  AND ($2::date IS NULL OR n.date <= $2) '
        f'  AND ($3::text IS NULL OR n.industry_id = $3) '
        f'  AND ($4::text IS NULL OR n.source = $4) '
        f'  AND n.content IS NOT NULL AND btrim(n.content) <> \'\' '
        f'ORDER BY n.date DESC, n.news_id DESC')
    params: List[Any] = [start, end, industry_id, source]
    if limit:
        sql += ' LIMIT $5'
        params.append(limit)
    return [dict(r) for r in await conn.fetch(sql, *params)]


async def update_digestion_verdict(conn, news_id: int, category: int,
                                   is_fake_news: bool) -> None:
    """Write one detector verdict onto an existing digestion row.

    Backfill-only write: touches the two detector columns and nothing
    else (summary / sentiment / llm_model keep the value their engine
    produced). category and search_tsv are orthogonal — the tsv indexes
    the summary, which this does not touch.
    """
    await conn.execute(
        f'UPDATE {DIGESTIONS_TABLE} '
        f'SET category = $1, is_fake_news = $2 WHERE news_id = $3',
        int(category), bool(is_fake_news), news_id)


async def upsert_digestions(conn,
                            rows: Sequence[Dict[str, Any]]) -> int:
    """Write digestion rows (PK news_id; re-digestion overwrites)."""
    if not rows:
        return 0
    rows = [{c: r.get(c) for c in DIGESTION_COLUMNS} for r in rows]
    # category / is_fake_news are NOT NULL: a row that skipped the
    # detector pass (no key / None) lands as the honest defaults (99
    # other / False), never a NULL the columns would reject.
    for r in rows:
        r["category"] = int(r.get("category")
                            if r.get("category") is not None else 99)
        r["is_fake_news"] = bool(r.get("is_fake_news"))
    n = await bulk_upsert_async(conn, DIGESTIONS_TABLE, rows, ["news_id"])
    # search_tsv (TSV_SPECS): summary B. Unconditional — a
    # re-digestion rewrites the summary, so the tsvector must follow.
    await SearchIndexer.get().set_search_tsv_async(conn, DIGESTIONS_TABLE, rows)
    # High-DF lexeme stats follow the tsv write (text.search_lexicon_df —
    # full replace, tiny for this table).
    await SearchIndexer.get().refresh_lexicon_df_async(
        conn, DIGESTIONS_TABLE)
    return n


async def fetch_digestions(
    conn,
    *,
    limit: int = 20,
    industry_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Newest-first digestion listing (joined with the article title)."""
    rows = await conn.fetch(
        f'SELECT d.news_id, d.date, d.industry_id, d.summary, '
        f'       d.sentiment_level, d.category, d.is_fake_news, '
        f'       d.llm_model, d.created_at, n.title, n.source '
        f'FROM {DIGESTIONS_TABLE} d '
        f'JOIN {NEWS_TABLE} n ON n.news_id = d.news_id '
        f'WHERE ($1::text IS NULL OR d.industry_id = $1) '
        f'ORDER BY d.date DESC, d.news_id DESC LIMIT $2',
        industry_id, limit)
    return [dict(r) for r in rows]


async def count_pending(conn) -> int:
    """Articles still without a digestion row (excludes title-only rows,
    matching what a default `run` would pick up)."""
    return await conn.fetchval(
        f'SELECT COUNT(*) FROM {NEWS_TABLE} n '
        f'LEFT JOIN {DIGESTIONS_TABLE} d ON d.news_id = n.news_id '
        f'WHERE d.news_id IS NULL '
        f'  AND n.content IS NOT NULL AND btrim(n.content) <> \'\'')
