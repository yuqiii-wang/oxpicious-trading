"""builds.text.backfill_comments — re-fetch zhihu comments for stored news.

Comments are normally scraped only while an item is first downloaded
(``downloads.macro.zhihu.news --with-comments``), so items that predate the
flag, arrived through a path without comment fetching, or whose comment
requests silently failed stay comment-less forever — the corpus had ~97% of
its votes>20 zhihu answers without a single stored comment.

This task closes that gap against the LIVE DB (no artifacts involved):

  1. Select zhihu rows from text.news that are missing comments
     (``--force`` widens the scope to items that already have some, so
     late-arriving comments are picked up too).
  2. Probe the public root_comments API per item via the downloader's
     :func:`fetch_item_comments` (plain-session fetch, light sleep pacing).
  3. Write through builds.text.upsert's comment pipeline (PK-dedupe +
     ``filter_new_comments`` + bulk upsert), in batches so long runs
     persist progress and a re-run skips already-covered items.

It is a one-off / maintenance task — the daily download keeps fetching
comments for fresh items; this only backfills what is already stored.
"""
from __future__ import annotations

import datetime
import logging
import random
import time
from typing import Any, Dict, List, Optional

from builds.text import upsert
from builds.text.loaders._parsing import parse_date_str
from downloads.macro.zhihu.news import COMMENT_SLEEP_SEC, fetch_item_comments

logger = logging.getLogger(__name__)

SOURCE = "zhihu"

# Items per DB write batch — bounds both the in-batch PK set and the loss on
# an interrupted run (everything before the last commit is permanent).
BATCH_SIZE = 25


async def fetch_target_items(
    conn,
    *,
    start: Optional[datetime.date] = None,
    end: Optional[datetime.date] = None,
    min_votes: Optional[int] = None,
    limit: Optional[int] = None,
    force: bool = False,
) -> List[Dict[str, Any]]:
    """Zhihu news rows needing a comment probe, best-first.

    Default scope: items with NO stored comments (the gap). ``--force``
    widens to every zhihu item in the window so items that gained comments
    since their (single) download get refreshed too. Ordering — votes desc
    NULLS LAST, then date desc — spends the rate budget on the answers most
    likely to carry comments first.
    """
    where = ["n.source = $1", "n.url IS NOT NULL AND n.url <> ''"]
    params: List[Any] = [SOURCE]
    if start:
        params.append(start)
        where.append(f"n.date >= ${len(params)}")
    if end:
        params.append(end)
        where.append(f"n.date <= ${len(params)}")
    if min_votes is not None:
        params.append(min_votes)
        where.append(f"COALESCE(n.votes, 0) >= ${len(params)}")
    if not force:
        where.append("NOT EXISTS (SELECT 1 FROM text.news_comments c "
                     "WHERE c.news_id = n.news_id)")
    sql = (
        "SELECT n.news_id, n.url FROM text.news n WHERE "
        + " AND ".join(where)
        + " ORDER BY n.votes DESC NULLS LAST, n.date DESC, n.news_id ASC"
    )
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return [dict(r) for r in await conn.fetch(sql, *params)]


def to_comment_rows(
    raw_rows: List[Dict[str, Any]], news_id: int
) -> List[Dict[str, Any]]:
    """Downloader comment rows → the upsert pipeline's column shape.

    ``fetch_item_comments`` returns ISO date strings (asyncpg needs real
    dates) and no source/news_id; this adds both and PK-dedupes in-batch
    (overlapping API pages may repeat an id, and a duplicated PK inside one
    bulk upsert raises "cannot affect row a second time").
    """
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for r in raw_rows:
        pk = int(r["comment_id"])
        if pk in seen:
            continue
        seen.add(pk)
        out.append({
            "comment_id": pk,
            "parent_comment_id": r.get("parent_comment_id"),
            "source": SOURCE,
            "news_id": news_id,
            "author": r.get("author"),
            "content": r.get("content"),
            "date": parse_date_str(r.get("date")),
            "votes": r.get("votes"),
            "is_reply": bool(r.get("is_reply")),
        })
    return out


async def run_backfill(
    conn,
    *,
    start: Optional[datetime.date] = None,
    end: Optional[datetime.date] = None,
    min_votes: Optional[int] = None,
    limit: Optional[int] = None,
    force: bool = False,
    sleep_sec: Optional[float] = None,
) -> Dict[str, int]:
    """Probe every target item and upsert the fetched comments in batches.

    *sleep_sec* None falls back to the downloader's COMMENT_SLEEP_SEC.
    Returns the counters for the run summary. A failed item's rows are
    simply absent — it stays in the default (missing-comments) scope, so a
    later re-run retries it for free.
    """
    if sleep_sec is None:
        sleep_sec = COMMENT_SLEEP_SEC
    import requests

    from downloads.macro.zhihu.news import _COMMENT_HEADERS

    items = await fetch_target_items(
        conn, start=start, end=end, min_votes=min_votes,
        limit=limit, force=force,
    )
    logger.info("    %d item(s) to probe (force=%s)", len(items), force)
    counters = {"items": len(items), "with_comments": 0, "empty": 0, "rows": 0}
    if not items:
        return counters

    session = requests.Session()
    pending: List[Dict[str, Any]] = []
    done = 0
    try:
        for it in items:
            news_id, url = it["news_id"], it["url"]
            raw = fetch_item_comments(session, url, sleep_sec=sleep_sec)
            done += 1
            if raw:
                counters["with_comments"] += 1
                pending.extend(to_comment_rows(raw, int(news_id)))
            else:
                # [] covers both "no comments on site" and a failed request —
                # either way the item stays in the default (missing-comments)
                # scope, so a later re-run retries it for free.
                counters["empty"] += 1
            if done % 50 == 0:
                logger.info("    probed %d/%d — %d comment rows pending …",
                            done, len(items), len(pending))
            if len(pending) >= BATCH_SIZE:
                counters["rows"] += await _flush(conn, pending)
                pending = []
            time.sleep(random.uniform(sleep_sec * 0.8, sleep_sec * 1.2))
    finally:
        counters["rows"] += await _flush(conn, pending)
    return counters


async def _flush(conn, pending: List[Dict[str, Any]]) -> int:
    """Write one batch through the standard comment pipeline (PK-checked)."""
    if not pending:
        return 0
    existing_pks = await upsert.fetch_existing_comment_pks(conn)
    fresh = upsert.filter_new_comments(pending, existing_pks)
    await upsert.insert_comments(conn, fresh)
    return len(fresh)
