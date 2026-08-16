"""DB helpers for the CSIndex intraday streamer.

- ``load_sse_streamed_codes`` — codes with bars today (excluded from CSIndex)
- ``load_index_industry_map`` — {code: industry_id} for download ordering
- ``order_codes_by_industry_coverage`` — reorder so every industry gets one
  index fetched first
- ``load_missing_or_stale_codes`` — indices missing/stale in intraday_5min
- ``upsert_index_bars`` — upsert identity + bar rows to DB
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional, Tuple

from downloads.index.csindex.quote import CSINDEX_SKIP_CODES
from _common.db_commons import bulk_upsert

from ._constants import (
    BOND_NAME_KEYWORD,
    CSINDEX_NO_TICK_CODES,
    STALE_THRESHOLD_MIN,
    TRADING_END,
    TRADING_START,
)
from downloads._common.core import setup_logger

logger = setup_logger("csindex_stream")


def load_sse_streamed_codes(conn, today: date) -> set:
    """Return the set of index codes that already have bars in
    ``stats.index_intraday_5min`` for ``today``.

    These are codes being actively streamed by SSE (and SZSE). CSIndex
    excludes them from its download list to avoid redundant fetches.
    """
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT code FROM stats.index_intraday_5min WHERE date = %s",
                (today,),
            )
            return {r[0] for r in cur.fetchall()}
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to load SSE-streamed codes for %s: %s", today, e)
        return set()


def load_index_industry_map(conn) -> Dict[str, str]:
    """Return ``{code: industry_id}`` for every index in ``sec_classification``.

    Used to order the CSIndex download list so EVERY industry gets at least
    one index fetched FIRST, ensuring a partial sweep still covers all
    industries.
    """
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT code, COALESCE(industry_id, 'OTHER') AS industry_id
                  FROM stats.sec_classification
                 WHERE type = 'index'
                """
            )
            return {r[0]: r[1] for r in cur.fetchall()}
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to load index→industry map: %s", e)
        return {}


def order_codes_by_industry_coverage(
    codes: List[Tuple[str, str]],
    industry_map: Dict[str, str],
) -> List[Tuple[str, str]]:
    """Reorder ``codes`` so every industry gets at least one index FIRST.

    Pass 1 (head): pick one representative per industry — the first code
    encountered for that industry.
    Pass 2 (tail): append remaining codes in original order.

    If the loop is killed after only the head is fetched, every industry
    still has fresh data.
    """
    if not codes:
        return []

    seen_industries: set = set()
    head: List[Tuple[str, str]] = []
    tail: List[Tuple[str, str]] = []

    for code, name in codes:
        ind = industry_map.get(code, "OTHER")
        if ind not in seen_industries:
            seen_industries.add(ind)
            head.append((code, name))
        else:
            tail.append((code, name))

    return head + tail


def load_missing_or_stale_codes(
    conn, today: date, exclude_codes: Optional[set] = None,
) -> List[Tuple[str, str]]:
    """Return ``[(code, name), ...]`` for indices missing or stale in
    ``index_intraday_5min`` for ``today``.

    "Missing" = code in ``index_basic_stats`` (latest date) but no rows in
    ``index_intraday_5min`` for ``today``.
    "Stale" = latest bar time > ``STALE_THRESHOLD_MIN`` behind current time
    during trading hours.
    """
    now = datetime.now()
    now_time = now.time()
    in_trading = TRADING_START <= now_time <= TRADING_END

    if in_trading:
        stale_cutoff = (now - timedelta(minutes=STALE_THRESHOLD_MIN)).time()
        query = """
            WITH latest_stats AS (
                SELECT code, MAX(date) AS max_date
                  FROM stats.index_basic_stats
                 GROUP BY code
            ),
            today_bars AS (
                SELECT code, MAX(time) AS latest_time
                  FROM stats.index_intraday_5min
                 WHERE date = %s
                 GROUP BY code
            )
            SELECT DISTINCT ls.code, COALESCE(sc.name, ls.code) AS name
              FROM latest_stats ls
              LEFT JOIN today_bars tb ON tb.code = ls.code
              LEFT JOIN stats.sec_classification sc
                ON sc.code = ls.code AND sc.type = 'index'
             WHERE tb.code IS NULL              -- missing entirely
                OR tb.latest_time < %s          -- stale (latest bar too old)
             ORDER BY ls.code
        """
        with conn.cursor() as cur:
            cur.execute(query, (today, stale_cutoff))
            rows = cur.fetchall()
    else:
        query = """
            WITH latest_stats AS (
                SELECT code, MAX(date) AS max_date
                  FROM stats.index_basic_stats
                 GROUP BY code
            ),
            today_bars AS (
                SELECT code
                  FROM stats.index_intraday_5min
                 WHERE date = %s
                 GROUP BY code
            )
            SELECT DISTINCT ls.code, COALESCE(sc.name, ls.code) AS name
              FROM latest_stats ls
              LEFT JOIN today_bars tb ON tb.code = ls.code
              LEFT JOIN stats.sec_classification sc
                ON sc.code = ls.code AND sc.type = 'index'
             WHERE tb.code IS NULL
             ORDER BY ls.code
        """
        with conn.cursor() as cur:
            cur.execute(query, (today,))
            rows = cur.fetchall()

    excluded = (
        CSINDEX_SKIP_CODES
        | CSINDEX_NO_TICK_CODES
        | (exclude_codes or set())
    )
    result: List[Tuple[str, str]] = []
    n_bond_skipped = 0
    for r in rows:
        code, name = r[0], r[1] or ""
        if code in excluded:
            continue
        if BOND_NAME_KEYWORD in name:
            n_bond_skipped += 1
            continue
        result.append((code, name))
    if n_bond_skipped:
        logger.info(
            "Skipped %d bond indices (name contains '%s').",
            n_bond_skipped, BOND_NAME_KEYWORD,
        )
    return result


def upsert_index_bars(
    conn,
    identity_rows: List[dict],
    bar_rows: List[dict],
) -> None:
    """Upsert index identity rows (FK parent) then intraday bars."""
    if identity_rows:
        seen = set()
        uniq: List[dict] = []
        for r in identity_rows:
            k = (r["date"], r["code"])
            if k in seen:
                continue
            seen.add(k)
            uniq.append(r)
        bulk_upsert(conn, "stats.index_identity", uniq, ["date", "code"])
    if bar_rows:
        bulk_upsert(conn, "stats.index_intraday_5min", bar_rows, ["date", "code", "time"])
