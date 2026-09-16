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

from builds.text.upsert import NEWS_TABLE

logger = logging.getLogger(__name__)

DIGESTIONS_TABLE = "text.news_digestions"
DIGESTION_COLUMNS = ("news_id", "date", "industry_id", "summary",
                     "sentiment_level", "llm_model")


async def fetch_pending_articles(
    conn,
    *,
    start: Optional[datetime.date] = None,
    end: Optional[datetime.date] = None,
    industry_id: Optional[str] = None,
    source: Optional[str] = None,
    include_empty: bool = False,
    force: bool = False,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Articles in scope still missing a text.news_digestions row.

    Newest first. ``force`` selects every in-scope article regardless of
    existing digestions (they get overwritten on upsert); ``include_empty``
    also admits NULL/empty-content rows (title-only placeholders).
    """
    sql = (
        f'SELECT n.news_id, n.title, n.content, n.date, n.source, '
        f'       n.industry_id, n.word_count '
        f'FROM {NEWS_TABLE} n '
        f'LEFT JOIN {DIGESTIONS_TABLE} d ON d.news_id = n.news_id '
        f'WHERE ($1::date IS NULL OR n.date >= $1) '
        f'  AND ($2::date IS NULL OR n.date <= $2) '
        f'  AND ($3::text IS NULL OR n.industry_id = $3) '
        f'  AND ($4::text IS NULL OR n.source = $4) '
        f'  AND (d.news_id IS NULL OR $5) '
        f'  AND ($6 OR (n.content IS NOT NULL AND btrim(n.content) <> \'\')) '
        f'ORDER BY n.date DESC, n.news_id DESC')
    params: List[Any] = [start, end, industry_id, source,
                         force, include_empty]
    if limit:
        sql += ' LIMIT $7'
        params.append(limit)
    return [dict(r) for r in await conn.fetch(sql, *params)]


async def upsert_digestions(conn,
                            rows: Sequence[Dict[str, Any]]) -> int:
    """Write digestion rows (PK news_id; re-digestion overwrites)."""
    if not rows:
        return 0
    rows = [{c: r.get(c) for c in DIGESTION_COLUMNS} for r in rows]
    return await bulk_upsert_async(conn, DIGESTIONS_TABLE, rows, ["news_id"])


async def fetch_digestions(
    conn,
    *,
    limit: int = 20,
    industry_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Newest-first digestion listing (joined with the article title)."""
    rows = await conn.fetch(
        f'SELECT d.news_id, d.date, d.industry_id, d.summary, '
        f'       d.sentiment_level, d.llm_model, d.created_at, '
        f'       n.title, n.source '
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
