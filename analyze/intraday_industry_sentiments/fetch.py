"""Async DB fetch primitives for analyze.intraday_industry_sentiments.

All SQL here is read-only SELECT. INSERTs/UPDATEs are in __main__.py via
``bulk_upsert_async`` (per project rule: ad-hoc SQL insert/update
operations must be consolidated into Python code).
"""
from __future__ import annotations

import datetime
from typing import Sequence

import asyncpg

from .config import BROAD_EXCLUDED


# ----------------------------------------------------------------------------
#  Latest distinct intraday dates present in stats.index_intraday_5min.
#
#  By default the script only processes the latest N (=2) distinct dates:
#  "today" (the current trading day, which has 5-min bars being populated
#  during market hours) and "last biz day" (the previous trading day). On
#  weekends/holidays this naturally falls back to the last 2 trading days
#  because the table only has rows for trading days.
# ----------------------------------------------------------------------------
_LATEST_DATES_SQL = """
SELECT DISTINCT date
FROM stats.index_intraday_5min
WHERE close IS NOT NULL
ORDER BY date DESC
LIMIT $1::int
"""


async def fetch_latest_intraday_dates(
    conn: asyncpg.Connection,
    n_dates: int = 2,
) -> list[datetime.date]:
    """Return the latest N distinct intraday 5-min dates (today + last biz day)."""
    rows = await conn.fetch(_LATEST_DATES_SQL, n_dates)
    return [r["date"] for r in rows]


# ----------------------------------------------------------------------------
#  Find missing (benchmark_code, date) pairs within the given date scope.
#
#  A pair is missing if (benchmark, date) is in stats.index_intraday_5min
#  AND the benchmark has an ELIGIBLE member universe (mirrors _MEMBERS_SQL:
#  latest snapshot row with code_sec_shared_weight > 0, joined to an active
#  stats.sec_classification row with a non-BROAD industry_id) with at least
#  one member having intraday bars on that date, AND no row exists in
#  analysis.intraday_industry_market_movements for that (benchmark, date).
#
#  The eligibility check is REQUIRED: many benchmarks appear in
#  sec_alloc_perf_attribution with all code_sec_shared_weight = 0 (zero
#  attributable members). Without the check, such pairs compute to 0 rows,
#  insert nothing, and get re-detected as "missing" on every run forever.
#
#  ``target_dates`` narrows the search to those dates only (default scope:
#  today + last biz day). Pass None to search across ALL dates (full
#  historical backfill — use sparingly).
#
#  Optional ``benchmarks`` filter narrows to a specific set (used by
#  --benchmark CLI arg). When empty/None, all benchmarks are considered.
# ----------------------------------------------------------------------------
_FIND_MISSING_PAIRS_SQL = """
WITH sap_latest AS (
    SELECT benchmark_code, MAX(date) AS snap_date
    FROM analysis.sec_alloc_perf_attribution
    WHERE sec_type = 'index'
    GROUP BY benchmark_code
),
member_universe AS (
    SELECT DISTINCT sap.benchmark_code, sap.code AS member_code
    FROM analysis.sec_alloc_perf_attribution sap
    JOIN sap_latest sl
        ON sl.benchmark_code = sap.benchmark_code
       AND sap.date = sl.snap_date
    JOIN stats.sec_classification sc
        ON sc.code = sap.code AND sc.type = 'index' AND sc.is_active = TRUE
       AND sc.industry_id IS NOT NULL AND sc.industry_id <> ''
       AND sc.industry_id <> ALL($3::text[])
    WHERE sap.sec_type = 'index'
      AND sap.code_sec_shared_weight > 0
)
SELECT DISTINCT i5.code AS benchmark_code, i5.date AS tick_date
FROM stats.index_intraday_5min i5
WHERE i5.close IS NOT NULL
  AND EXISTS (
      SELECT 1
      FROM member_universe mu
      JOIN stats.index_intraday_5min mi5
          ON mi5.code = mu.member_code
         AND mi5.date = i5.date
         AND mi5.close IS NOT NULL
      WHERE mu.benchmark_code = i5.code
  )
  AND ($1::date[] IS NULL OR i5.date = ANY($1::date[]))
  AND ($2::text[] IS NULL OR i5.code = ANY($2::text[]))
  AND NOT EXISTS (
      SELECT 1
      FROM analysis.intraday_industry_market_movements m
      WHERE m.benchmark_code = i5.code
        AND m.date = i5.date
  )
ORDER BY i5.code, i5.date
"""


async def find_missing_pairs(
    conn: asyncpg.Connection,
    benchmarks: Sequence[str] | None = None,
    target_dates: Sequence[datetime.date] | None = None,
) -> list[tuple[str, datetime.date]]:
    """Return list of (benchmark_code, tick_date) pairs needing computation.

    ``target_dates`` defaults to None (search ALL dates). Callers that want
    the default scope (today + last biz day) should pass the result of
    ``fetch_latest_intraday_dates``.
    """
    bench_param = list(benchmarks) if benchmarks else None
    dates_param = list(target_dates) if target_dates else None
    rows = await conn.fetch(
        _FIND_MISSING_PAIRS_SQL, dates_param, bench_param, list(BROAD_EXCLUDED)
    )
    return [(r["benchmark_code"], r["tick_date"]) for r in rows]


# ----------------------------------------------------------------------------
#  Load benchmark 5-min bars + prev_day_close for one (benchmark, date).
#
#  Returns one row per 5-min tick with: date, time, close, prev_close.
#  prev_close is the close on the latest date strictly less than tick_date
#  with non-NULL close (constant across all ticks of the same day).
# ----------------------------------------------------------------------------
_BENCHMARK_BARS_SQL = """
WITH prev AS (
    SELECT close AS prev_close
    FROM stats.index_basic_stats
    WHERE code = $1::text
      AND date < $2::date
      AND close IS NOT NULL
    ORDER BY date DESC
    LIMIT 1
)
SELECT
    i5.date,
    i5.time,
    i5.close,
    p.prev_close
FROM stats.index_intraday_5min i5
LEFT JOIN prev p ON true
WHERE i5.date = $2::date
  AND i5.code = $1::text
  AND i5.close IS NOT NULL
ORDER BY i5.time
"""


async def fetch_benchmark_bars(
    conn: asyncpg.Connection,
    benchmark_code: str,
    tick_date: datetime.date,
) -> list[dict]:
    """Fetch benchmark 5-min bars + prev_day_close for one date.

    Returns one row per 5-min tick with: date, time, close, prev_close.
    """
    rows = await conn.fetch(_BENCHMARK_BARS_SQL, benchmark_code, tick_date)
    return [
        {
            "date": r["date"],
            "time": r["time"],
            "close": float(r["close"]) if r["close"] is not None else None,
            "prev_close": (
                float(r["prev_close"]) if r["prev_close"] is not None else None
            ),
        }
        for r in rows
    ]


# ----------------------------------------------------------------------------
#  Load member indices for a benchmark + their 5-min bars + prev_day_close.
#
#  Member indices come from analysis.sec_alloc_perf_attribution (latest
#  snapshot per benchmark where code_sec_shared_weight > 0), joined to
#  stats.sec_classification for industry_id / is_industry_not_strategy /
#  industry_label, and to stats.index_intraday_5min for the 5-min bars on
#  the target tick_date, and to stats.index_basic_stats for prev_day_close.
#
#  BROAD_* industry_ids are excluded (they are benchmarks, not industries).
#
#  Returns one row per (member_code, time) with: member_code, industry_id,
#  is_industry_not_strategy, industry_label, date, time, close, prev_close.
# ----------------------------------------------------------------------------
_MEMBERS_SQL = """
WITH sap_date AS (
    SELECT MAX(date) AS d
    FROM analysis.sec_alloc_perf_attribution
    WHERE sec_type = 'index' AND benchmark_code = $1::text
),
prev AS (
    SELECT DISTINCT ON (code) code, close AS prev_close
    FROM stats.index_basic_stats
    WHERE date < $2::date AND close IS NOT NULL
      AND code IN (
          SELECT code
          FROM analysis.sec_alloc_perf_attribution
          WHERE benchmark_code = $1::text
            AND sec_type = 'index'
            AND date = (SELECT d FROM sap_date)
            AND code_sec_shared_weight > 0
      )
    ORDER BY code, date DESC
),
member_universe AS (
    SELECT
        sap.code                    AS member_code,
        sap.code_sec_shared_weight  AS member_weight,
        sc.industry_id,
        sc.is_industry_not_strategy,
        COALESCE(sc.industry_label, sc.industry_id) AS industry_label
    FROM analysis.sec_alloc_perf_attribution sap
    JOIN stats.sec_classification sc
        ON sc.code = sap.code AND sc.type = 'index' AND sc.is_active = TRUE
       AND sc.industry_id IS NOT NULL AND sc.industry_id <> ''
       AND sc.industry_id <> ALL($3::text[])
    WHERE sap.benchmark_code = $1::text
      AND sap.sec_type = 'index'
      AND sap.date = (SELECT d FROM sap_date)
      AND sap.code_sec_shared_weight > 0
)
SELECT
    mu.member_code,
    mu.member_weight,
    mu.industry_id,
    mu.is_industry_not_strategy,
    mu.industry_label,
    i5.date,
    i5.time,
    i5.close,
    p.prev_close
FROM member_universe mu
JOIN stats.index_intraday_5min i5
    ON i5.code = mu.member_code
   AND i5.date = $2::date
   AND i5.close IS NOT NULL
LEFT JOIN prev p ON p.code = mu.member_code
ORDER BY mu.industry_id, mu.member_code, i5.time
"""


async def fetch_member_bars(
    conn: asyncpg.Connection,
    benchmark_code: str,
    tick_date: datetime.date,
) -> list[dict]:
    """Fetch member indices' 5-min bars + prev_day_close for one date.

    Returns one row per (member_code, time) with: member_code, member_weight,
    industry_id, is_industry_not_strategy, industry_label, date, time,
    close, prev_close.
    """
    rows = await conn.fetch(
        _MEMBERS_SQL, benchmark_code, tick_date, list(BROAD_EXCLUDED)
    )
    return [
        {
            "member_code": r["member_code"],
            "member_weight": (
                float(r["member_weight"]) if r["member_weight"] is not None else None
            ),
            "industry_id": r["industry_id"],
            "is_industry_not_strategy": r["is_industry_not_strategy"],
            "industry_label": r["industry_label"],
            "date": r["date"],
            "time": r["time"],
            "close": float(r["close"]) if r["close"] is not None else None,
            "prev_close": (
                float(r["prev_close"]) if r["prev_close"] is not None else None
            ),
        }
        for r in rows
    ]
