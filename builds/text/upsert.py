"""builds.text.upsert — DB writers for text.news / text.news_keywords.

Write order (one connection, no cross-table transactions beyond what
bulk_upsert_async already wraps):

  1. text.news          — upsert on the natural key (title, source, date).
                          Before the upsert, the stored state decides which
                          articles need keyword work: new ones, and stored
                          ones whose word_count / industry_id changed
                          (downloaded files are immutable per key, so equal
                          counts ⇒ equal content; --force rewrites
                          everything anyway).
  2. text.news_comments — zhihu comments (fetched by --with-comments
                          downloads): loader rows are resolved to news_id,
                          PK-checked against stored rows and in-batch
                          duplicates, then upserted.
  3. text.news_keywords — for those articles: replace all keyword rows
                          (delete + insert).
  4. doc_freq / idf     — corpus-level stats on every keyword row,
                          recalculated in ONE SQL UPDATE after inserts (the
                          pipeline owns these writes — no triggers, per the
                          DDL comments in database/sql/text/01_news.sql).
  5. today_industry_change — per (industry_id, date): the industry's
                          mean_close move (pool_size='all' from
                          stats.industry_basic_stats) on the article date, or
                          the NEXT trading date's move when the article date
                          is a non-trading day (weekend/holiday) — computed
                          data-driven from the stored close series.
"""
from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, List, Tuple

from _common.build_commons import bulk_upsert_async

logger = logging.getLogger(__name__)

# Kept aligned with database/sql/text/01_news.sql — the build only writes
# rows; schema changes belong to the DDL files.
NEWS_TABLE = "text.news"
KEYWORDS_TABLE = "text.news_keywords"
COMMENTS_TABLE = "text.news_comments"


# ----------------------------------------------------------------------------
# 1. text.news
# ----------------------------------------------------------------------------
async def fetch_existing_news(conn) -> Dict[Tuple[str, str, datetime.date], Dict[str, Any]]:
    """{(title, source, date): {news_id, word_count, industry_id}} currently stored."""
    rows = await conn.fetch(
        f'SELECT news_id, title, source, date, word_count, industry_id '
        f'FROM {NEWS_TABLE}')
    return {
        (r["title"], r["source"] or "", r["date"]): {
            "news_id": r["news_id"],
            "word_count": r["word_count"],
            "industry_id": r["industry_id"],
        }
        for r in rows
    }


def build_news_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Project parsed rows to the text.news column shape."""
    return [{
        "title": r["title"],
        "content": r.get("content"),
        "date": r["date"],
        "source": r["source"],
        "url": r.get("url"),
        "author": r.get("author"),
        "industry_id": r.get("industry_id"),
        "word_count": r.get("word_count"),
        "votes": r.get("votes"),
    } for r in rows]


def select_articles_needing_keywords(
    rows: List[Dict[str, Any]],
    pre_existing: Dict[Tuple[str, str, datetime.date], Dict[str, Any]],
    force: bool,
) -> List[Dict[str, Any]]:
    """Articles whose keyword rows must be refreshed (pre-upsert state).

    *pre_existing* must be the state of text.news BEFORE the news upsert:
    articles absent from it are NEW, present-but-changed ones need a rewrite,
    and only matching ones are skipped (downloaded files are immutable per
    (title, source, date), so equal word_count + industry_id ⇒ equal content).
    Returns the parsed rows; pair with their news_id via map_news_ids after
    the upsert.
    """
    out: List[Dict[str, Any]] = []
    for r in rows:
        key = (r["title"], r["source"], r["date"])
        cur = pre_existing.get(key)
        if cur is None:  # new article
            out.append(r)
            continue
        unchanged = (
            cur["word_count"] is not None
            and cur["word_count"] == r.get("word_count")
            and (cur["industry_id"] or None) == (r.get("industry_id") or None)
        )
        if force or not unchanged:
            out.append(r)
    return out


def map_news_ids(
    rows: List[Dict[str, Any]],
    post_existing: Dict[Tuple[str, str, datetime.date], Dict[str, Any]],
) -> List[Tuple[int, Dict[str, Any]]]:
    """Pair rows with their news_id (fetched AFTER the news upsert)."""
    pairs: List[Tuple[int, Dict[str, Any]]] = []
    for r in rows:
        key = (r["title"], r["source"], r["date"])
        cur = post_existing.get(key)
        if cur is not None:
            pairs.append((cur["news_id"], r))
    return pairs


# ----------------------------------------------------------------------------
# 2. text.news_keywords  (delete + insert per touched article)
# ----------------------------------------------------------------------------
def build_keyword_rows(
    news_id: int,
    row: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """keyword rows for one article (doc_freq/idf/change filled by SQL later)."""
    word_count = row.get("word_count") or 0
    return [{
        "news_id": news_id,
        "keyword": kw,
        "date": row["date"],
        "industry_id": row.get("industry_id"),
        "today_industry_change": None,
        "count": count,
        "count_pct": (count / word_count) if word_count else None,
    } for kw, count in sorted(row.get("keywords", {}).items())]


async def refresh_keyword_rows(conn, articles: List[Tuple[int, Dict[str, Any]]]) -> int:
    """Replace keyword rows of *articles*: delete per news_id, insert fresh."""
    if not articles:
        return 0
    news_ids = [nid for nid, _ in articles]
    await conn.executemany(
        f'DELETE FROM {KEYWORDS_TABLE} WHERE news_id = $1',
        [(nid,) for nid in news_ids])
    all_rows: List[Dict[str, Any]] = []
    for nid, row in articles:
        all_rows.extend(build_keyword_rows(nid, row))
    if all_rows:
        # bulk_upsert conflicts on (keyword, news_id); rows are fresh deletes
        # so a plain insert would do — the shared helper keeps one code path.
        await bulk_upsert_async(conn, KEYWORDS_TABLE, all_rows,
                                ["keyword", "news_id"])
    logger.info("    [DB] keyword rows rewritten for %d articles "
                "(%d rows)", len(articles), len(all_rows))
    return len(all_rows)


# ----------------------------------------------------------------------------
# 3. doc_freq / idf  (corpus-wide, one UPDATE)
# ----------------------------------------------------------------------------
async def recompute_keyword_stats(conn) -> None:
    """doc_freq = # articles per keyword; idf = ln(1 + N / doc_freq).

    Full recompute each run: the news corpus is small (10^3–10^4 articles)
    and IDF is corpus-level — every keyword's stats change whenever the
    total article count changes, per the DDL contract in 01_news.sql.
    """
    await conn.execute("""
        WITH doc AS (
            SELECT keyword, COUNT(*) AS doc_freq
            FROM text.news_keywords
            GROUP BY keyword
        ), tot AS (
            SELECT COUNT(*) AS n FROM text.news
        )
        UPDATE text.news_keywords k
        SET doc_freq = d.doc_freq,
            idf = ln(1 + (t.n::double precision) / d.doc_freq)
        FROM doc d, tot t
        WHERE k.keyword = d.keyword
    """)


# ----------------------------------------------------------------------------
# 4. today_industry_change
# ----------------------------------------------------------------------------
def build_industry_change_map(
    stats_rows: List[Any],
) -> Dict[Tuple[str, datetime.date], float]:
    """{(industry_id, date): pct_change} from stats.industry_basic_stats rows.

    Input rows must carry (industry_id, date, mean_close) for ONE pool_size.
    change(d) = close(d) / close(prev trading date) - 1 — for a non-trading
    article date d this automatically resolves to the next stored date's
    move (see module docstring).
    """
    by_industry: Dict[str, Tuple[List[datetime.date], Dict[datetime.date, float]]] = {}
    for r in stats_rows:
        dates, closes = by_industry.setdefault(r["industry_id"], ([], {}))
        if r["mean_close"] is None:
            continue
        dates.append(r["date"])
        closes[r["date"]] = float(r["mean_close"])
    for dates in by_industry.values():
        dates[0].sort()

    out: Dict[Tuple[str, datetime.date], float] = {}
    for industry_id, (dates, closes) in by_industry.items():
        for i in range(1, len(dates)):
            prev, cur = closes[dates[i - 1]], closes[dates[i]]
            if prev:
                out[(industry_id, dates[i])] = (cur / prev - 1.0) * 100.0
    return out


async def fetch_industry_stats_rows(conn) -> List[Any]:
    return await conn.fetch(
        f'SELECT industry_id, date, mean_close '
        f'FROM stats.industry_basic_stats WHERE pool_size = \'all\'')


async def apply_today_industry_change(
    conn,
    articles: List[Tuple[int, Dict[str, Any]]],
) -> int:
    """Fill today_industry_change for the keyword rows just written."""
    if not articles:
        return 0
    change_map = build_industry_change_map(await fetch_industry_stats_rows(conn))
    # One value per (industry_id, date) across the touched articles.
    wanted = {
        (row.get("industry_id"), row["date"])
        for _, row in articles if row.get("industry_id")
    }
    updates = [
        (chg, industry_id, d)
        for (industry_id, d) in wanted
        if (chg := change_map.get((industry_id, d))) is not None
    ]
    if not updates:
        return 0
    await conn.executemany(
        f'UPDATE {KEYWORDS_TABLE} '
        f'SET today_industry_change = $1 '
        f'WHERE industry_id = $2 AND date = $3',
        updates)
    return len(updates)


# ----------------------------------------------------------------------------
# 5. text.news_comments (zhihu only for now)
# ----------------------------------------------------------------------------
async def fetch_existing_comment_pks(conn) -> set:
    """Stored comment PKs {(source, comment_id)} — the pre-load PK check.

    Re-loading an artifact must not re-write comments it already stored
    (duplicate load), and a batch must never carry a PK twice (bulk_upsert's
    ON CONFLICT DO UPDATE would raise "cannot affect row a second time").
    Both are filtered before any write happens.
    """
    rows = await conn.fetch(
        f'SELECT source, comment_id FROM {COMMENTS_TABLE}')
    return {(r["source"], r["comment_id"]) for r in rows}


def build_comment_rows(
    comment_rows: List[Dict[str, Any]],
    news_id_by_key: Dict[Tuple[str, str, datetime.date], int],
) -> List[Dict[str, Any]]:
    """Resolve loader comment rows to the text.news_comments column shape.

    Each row's parent (title, 'zhihu', parent_date) maps to the stored
    news_id; rows whose parent has no stored news row are dropped.
    """
    out: List[Dict[str, Any]] = []
    for r in comment_rows:
        news_id = news_id_by_key.get((r["title"], r["source"], r["parent_date"]))
        if news_id is None:
            continue
        out.append({
            "comment_id": r["comment_id"],
            "parent_comment_id": r.get("parent_comment_id"),
            "source": r["source"],
            "news_id": news_id,
            "author": r.get("author"),
            "content": r.get("content"),
            "date": r.get("date"),
            "votes": r.get("votes"),
            "is_reply": r.get("is_reply"),
        })
    return out


def filter_new_comments(
    comment_rows: List[Dict[str, Any]],
    existing_pks: set,
) -> List[Dict[str, Any]]:
    """Keep only comments whose PK is new — in-batch duplicates are dropped
    too (first occurrence wins), so the upsert batch never conflicts twice."""
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for r in comment_rows:
        pk = (r["source"], r["comment_id"])
        if pk in existing_pks or pk in seen:
            continue
        seen.add(pk)
        out.append(r)
    return out


async def insert_comments(conn, comment_rows: List[Dict[str, Any]]) -> int:
    """Idempotent write of PK-checked comment rows (bulk_upsert on the PK)."""
    if not comment_rows:
        return 0
    await bulk_upsert_async(conn, COMMENTS_TABLE, comment_rows,
                            ["source", "comment_id"])
    logger.info("    [DB] %d comment rows written", len(comment_rows))
    return len(comment_rows)
