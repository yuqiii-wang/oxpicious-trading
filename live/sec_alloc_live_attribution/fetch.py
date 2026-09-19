"""Shared async DB fetch primitives for live.sec_alloc_live_attribution.

All SQL here is read-only SELECT. INSERTs/UPDATEs live in ticks.py via
``bulk_upsert_async`` (per project rule: ad-hoc SQL insert/update
operations must be consolidated into Python code).

The former live.sec_alloc_live_prev_ref heavy reference table was
CONSOLIDATED AWAY (2026-09-08): its prev-day values are derivable from the
base tables and are now computed at tick time — prev closes from
stats.index_basic_stats, composition-overlap weights from
stats.cross_stats (read-side, see the API service). The weighted pass's
prev close basis is the member's official prev-day DAILY close (the same
basis the former ref materialized); the fallback pass keeps its
self-contained last-5-min-bar basis.
"""
from __future__ import annotations

import datetime
import math
from typing import Sequence

import asyncpg

from .config import BROAD_EXCLUDED, CURATED_BENCHMARK_FILTER, TICK_CLASS_TYPES


def _f(value) -> float | None:
    """NaN-safe float conversion (DB numerics may carry literal NaN)."""
    if value is None:
        return None
    f = float(value)
    return None if math.isnan(f) else f

# ----------------------------------------------------------------------------
#  Latest distinct intraday date present in stats.index_intraday_5min.
#  The heavy ref is (re)built only for the LATEST date — "today" during
#  market hours. On weekends/holidays this is simply the last trading day.
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
    n_dates: int = 1,
) -> list[datetime.date]:
    """Return the latest N distinct intraday 5-min dates (default: today only)."""
    rows = await conn.fetch(_LATEST_DATES_SQL, n_dates)
    return [r["date"] for r in rows]


# ----------------------------------------------------------------------------
#  Find ALL tick-eligible (benchmark, date) pairs for the LIVE process
#  (--mode live — the 5-min equal-weight path). Eligibility rules:
#    • the benchmark has intraday bars on the target (latest) date AND is in
#      the CURATED broad-market tag set (the UI's benchmark universe —
#      see config.CURATED_BENCHMARK_FILTER), AND
#    • it has an ELIGIBLE member universe — ALL classified indices with a
#      non-BROAD industry_id are members, with at least one TICK-ELIGIBLE
#      member (type IN ('index','etf')) having intraday bars on that date.
#  The loader's anti-join skips (code, time) rows that already exist with
#  ANY flag, so pairs fully covered by weighted rows are natural no-ops and
#  brand-new bars get equal-weight rows until the next yday-ref run
#  upgrades them.
# ----------------------------------------------------------------------------
_LIVE_TICK_PAIRS_SQL = """
WITH target AS MATERIALIZED (
    SELECT MAX(date) AS d
    FROM stats.index_intraday_5min
    WHERE close IS NOT NULL
),
today_bench AS MATERIALIZED (
    SELECT DISTINCT i5.code
    FROM stats.index_intraday_5min i5
    WHERE i5.date = (SELECT d FROM target)
      AND i5.close IS NOT NULL
      AND {curated_filter}
),
classified_members AS (
    SELECT DISTINCT sc.code
    FROM stats.sec_classification sc
    WHERE sc.is_active = TRUE
      AND sc.industry_id IS NOT NULL AND sc.industry_id <> ''
      AND sc.industry_id <> ALL($2::text[])
),
today_member AS MATERIALIZED (
    SELECT DISTINCT cm.code AS benchmark_code
    FROM classified_members cm
    JOIN stats.sec_classification sc2
        ON sc2.code = cm.code
       AND sc2.type = ANY($3::text[])
    JOIN stats.index_intraday_5min mi5
        ON mi5.code = cm.code
       AND mi5.date = (SELECT d FROM target)
       AND mi5.close IS NOT NULL
)
SELECT tb.code AS benchmark_code, (SELECT d FROM target) AS tick_date
FROM today_bench tb
WHERE EXISTS (
      SELECT 1 FROM today_member tm
  )
  AND ($1::text[] IS NULL OR tb.code = ANY($1::text[]))
ORDER BY tb.code
""".format(curated_filter=CURATED_BENCHMARK_FILTER)


async def find_live_tick_pairs(
    conn: asyncpg.Connection,
    benchmarks: Sequence[str] | None = None,
) -> list[tuple[str, datetime.date]]:
    """Return ALL tick-eligible (benchmark, latest_date) pairs (live mode)."""
    bench_param = list(benchmarks) if benchmarks else None
    rows = await conn.fetch(
        _LIVE_TICK_PAIRS_SQL,
        bench_param,
        list(BROAD_EXCLUDED),
        list(TICK_CLASS_TYPES),
    )
    return [(r["benchmark_code"], r["tick_date"]) for r in rows]


# ----------------------------------------------------------------------------
#  Find tick-eligible (benchmark, date) pairs for an ARBITRARY target date
#  (--mode compute — the on-demand backfill path, invoked by the API service
#  when a selected date has no tick rows). Same eligibility rules as the LIVE
#  finder above, but the target date is the $2 parameter instead of the
#  latest raw intraday date: the benchmark must have bars ON the target date
#  AND be in the curated broad-market tag set, and at least one tick-eligible
#  member must have bars on that date. The loaders' anti-joins make pairs
#  whose rows already exist natural no-ops (idempotent re-runs), and
#  fallback-only rows get upgraded to daily-close-basis rows.
# ----------------------------------------------------------------------------
_COMPUTE_TICK_PAIRS_SQL = """
WITH target AS MATERIALIZED (
    SELECT $2::date AS d
),
today_bench AS MATERIALIZED (
    SELECT DISTINCT i5.code
    FROM stats.index_intraday_5min i5
    WHERE i5.date = (SELECT d FROM target)
      AND i5.close IS NOT NULL
      AND {curated_filter}
),
classified_members AS (
    SELECT DISTINCT sc.code
    FROM stats.sec_classification sc
    WHERE sc.is_active = TRUE
      AND sc.industry_id IS NOT NULL AND sc.industry_id <> ''
      AND sc.industry_id <> ALL($3::text[])
),
today_member AS MATERIALIZED (
    SELECT DISTINCT cm.code AS benchmark_code
    FROM classified_members cm
    JOIN stats.sec_classification sc2
        ON sc2.code = cm.code
       AND sc2.type = ANY($4::text[])
    JOIN stats.index_intraday_5min mi5
        ON mi5.code = cm.code
       AND mi5.date = (SELECT d FROM target)
       AND mi5.close IS NOT NULL
)
SELECT tb.code AS benchmark_code, (SELECT d FROM target) AS tick_date
FROM today_bench tb
WHERE EXISTS (
      SELECT 1 FROM today_member tm
)
  AND ($1::text[] IS NULL OR tb.code = ANY($1::text[]))
ORDER BY tb.code
""".format(curated_filter=CURATED_BENCHMARK_FILTER)


async def find_compute_tick_pairs(
    conn: asyncpg.Connection,
    benchmarks: Sequence[str] | None,
    target_date: datetime.date,
) -> list[tuple[str, datetime.date]]:
    """Return ALL tick-eligible (benchmark, target_date) pairs (compute mode).

    ``benchmarks`` = None means every curated benchmark with bars on the
    target date. An empty result means the date has no raw intraday data
    (outside the raw table's retention, a non-trading day, or the future).
    """
    bench_param = list(benchmarks) if benchmarks else None
    rows = await conn.fetch(
        _COMPUTE_TICK_PAIRS_SQL,
        bench_param,
        target_date,
        list(BROAD_EXCLUDED),
        list(TICK_CLASS_TYPES),
    )
    return [(r["benchmark_code"], r["tick_date"]) for r in rows]


# ----------------------------------------------------------------------------
#  Find (benchmark, date) pairs with at least one pending WEIGHTED
#  tick row: either a genuinely missing (code, time) row, or an existing
#  FALLBACK row (is_without_trading_amt = TRUE) that can now be UPGRADED to
#  a daily-close-basis weighted row. Drives the yday-ref mode's incremental
#  pass.
#
#  Scoped to the single LATEST intraday date (target CTE first, same
#  pattern as the live pair finder above) — without the date constant the
#  planner seq-scans the whole multi-million-row tick table per
#  correlated NOT EXISTS; with it the (benchmark_code, date, time) index
#  applies and only the live date's rows are touched. Historical dates
#  are intentionally out of scope: the pipeline is latest-date-scoped
#  by design (the live/fallback process owns older fallback rows).
# ----------------------------------------------------------------------------
_PAIRS_WITH_MISSING_TICKS_SQL = """
WITH target AS MATERIALIZED (
    SELECT MAX(date) AS d
    FROM stats.index_intraday_5min
    WHERE close IS NOT NULL
),
today_bench AS MATERIALIZED (
    SELECT DISTINCT i5.code
    FROM stats.index_intraday_5min i5
    WHERE i5.date = (SELECT d FROM target)
      AND i5.close IS NOT NULL
      AND {curated_filter}
),
members AS MATERIALIZED (
    SELECT sc.code, sc.type
    FROM stats.sec_classification sc
    WHERE sc.is_active = TRUE
      AND sc.type = ANY($3::text[])
      AND sc.industry_id IS NOT NULL AND sc.industry_id <> ''
      AND sc.industry_id <> ALL($2::text[])
),
pairs AS MATERIALIZED (
    SELECT tb.code AS benchmark_code, (SELECT d FROM target) AS tick_date
    FROM today_bench tb
    WHERE EXISTS (
        SELECT 1
        FROM members m
        JOIN stats.index_intraday_5min mi5
            ON mi5.code = m.code
           AND mi5.date = (SELECT d FROM target)
           AND mi5.close IS NOT NULL
    )
)
SELECT DISTINCT p.benchmark_code, p.tick_date AS date
FROM pairs p
JOIN stats.index_intraday_5min i5
    ON i5.date = p.tick_date
   AND i5.close IS NOT NULL
JOIN members m ON m.code = i5.code
WHERE ($1::text[] IS NULL OR p.benchmark_code = ANY($1::text[]))
  AND NOT EXISTS (
      SELECT 1
      FROM live.sec_alloc_live_attribution t
      WHERE t.benchmark_code = p.benchmark_code
        AND t.date = p.tick_date
        AND t.code = i5.code
        AND t.sec_type = m.type
        AND t.time = i5.time
        AND t.is_without_trading_amt = FALSE
  )
ORDER BY 1, 2
""".format(curated_filter=CURATED_BENCHMARK_FILTER)


async def find_pairs_with_missing_ticks(
    conn: asyncpg.Connection,
    benchmarks: Sequence[str] | None = None,
) -> list[tuple[str, datetime.date]]:
    """Return (benchmark, date) pairs with missing OR upgrade-eligible ticks."""
    bench_param = list(benchmarks) if benchmarks else None
    rows = await conn.fetch(
        _PAIRS_WITH_MISSING_TICKS_SQL,
        bench_param,
        list(BROAD_EXCLUDED),
        list(TICK_CLASS_TYPES),
    )
    return [(r["benchmark_code"], r["date"]) for r in rows]


# ----------------------------------------------------------------------------
#  LIGHT fetch: ONLY the pending WEIGHTED member-tick rows for one
#  (benchmark, date).
#
#  Prev closes are computed AT TICK TIME from stats.index_basic_stats (the
#  former live.sec_alloc_live_prev_ref materialization was consolidated
#  away — same values, same basis):
#    • prev_d: ONE resolved prev date — the latest close-bearing date
#      strictly before the live date, resolved ONCE so every join is an
#      exact-date equality (one-day scans only, never a history sweep).
#    • member prev close: the member's DAILY close on prev_d (NaN-filtered;
#      members without a row on prev_d are skipped — they keep their
#      fallback rows).
#    • benchmark prev close: same date (one row, shared by all members).
#
#  Anti-joins the tick table per (code, time) on is_without_trading_amt =
#  FALSE: rows missing entirely OR present only as FALLBACK (TRUE) are
#  (re)fetched — the upsert then upgrades fallback rows in place.
# ----------------------------------------------------------------------------
_MISSING_TICKS_SQL = """
WITH bench_bars AS (
    SELECT i5.time, i5.close
    FROM stats.index_intraday_5min i5
    WHERE i5.code = $1::text
      AND i5.date = $2::date
      AND i5.close IS NOT NULL
),
members AS MATERIALIZED (
    SELECT sc.code, sc.type
    FROM stats.sec_classification sc
    WHERE sc.is_active = TRUE
      AND sc.type = ANY($3::text[])
      AND sc.industry_id IS NOT NULL AND sc.industry_id <> ''
      AND sc.industry_id <> ALL($4::text[])
      AND sc.code <> $1::text
),
prev_d AS (
    SELECT MAX(date) AS d
    FROM stats.index_basic_stats
    WHERE date < $2::date
      AND close IS NOT NULL
),
member_prev_close AS (
    SELECT b.code, b.close AS prev_close
    FROM stats.index_basic_stats b
    WHERE b.date = (SELECT d FROM prev_d)
      AND b.close IS NOT NULL AND b.close::text <> 'NaN'
      AND b.code IN (SELECT code FROM members)
),
bench_prev_close AS (
    SELECT close AS prev_close
    FROM stats.index_basic_stats
    WHERE code = $1::text
      AND date = (SELECT d FROM prev_d)
      AND close IS NOT NULL
      AND close::text <> 'NaN'
    LIMIT 1
)
SELECT
    i5.code,
    m.type                          AS sec_type,
    i5.date,
    i5.time,
    i5.close                        AS tick_close,
    mp.prev_close                   AS code_prev_date_close,
    bp.prev_close                   AS benchmark_prev_date_close,
    bb.close                        AS bench_tick_close
FROM stats.index_intraday_5min i5
JOIN members m ON m.code = i5.code
JOIN member_prev_close mp ON mp.code = i5.code
CROSS JOIN bench_prev_close bp
LEFT JOIN bench_bars bb ON bb.time = i5.time
WHERE i5.date = $2::date
  AND i5.close IS NOT NULL
  AND NOT EXISTS (
      SELECT 1
      FROM live.sec_alloc_live_attribution t
      WHERE t.benchmark_code = $1::text
        AND t.date = i5.date
        AND t.code = i5.code
        AND t.sec_type = m.type
        AND t.time = i5.time
        AND t.is_without_trading_amt = FALSE
  )
ORDER BY i5.code, i5.time
"""


async def fetch_missing_ticks(
    conn: asyncpg.Connection,
    benchmark_code: str,
    live_date: datetime.date,
) -> list[dict]:
    """Fetch pending weighted (code, time) tick rows for one pair.

    Returns rows with: code, sec_type, date, time, tick_close,
    code_prev_date_close, benchmark_prev_date_close, bench_tick_close.
    """
    rows = await conn.fetch(
        _MISSING_TICKS_SQL,
        benchmark_code,
        live_date,
        list(TICK_CLASS_TYPES),
        list(BROAD_EXCLUDED),
    )
    return [
        {
            "code": r["code"],
            "sec_type": r["sec_type"],
            "date": r["date"],
            "time": r["time"],
            "tick_close": _f(r["tick_close"]),
            "code_prev_date_close": _f(r["code_prev_date_close"]),
            "benchmark_prev_date_close": _f(r["benchmark_prev_date_close"]),
            "bench_tick_close": _f(r["bench_tick_close"]),
        }
        for r in rows
    ]


# ----------------------------------------------------------------------------
#  FALLBACK fetch (self-contained): tick rows for one (benchmark, date)
#  written by the 5-min LIVE pass so equal-weighted data flows immediately.
#
#  Prev-close basis = the member's LAST 5-min bar close ON the resolved
#  prev intraday date (single latest close-bearing date strictly before
#  the live date, resolved ONCE via the prev_d CTE — one-day scans only,
#  never a multi-day history sweep) from stats.index_intraday_5min
#  itself (self-contained — no basic_stats dependency). Benchmark prev
#  close likewise. Rows are written with is_without_trading_amt = TRUE;
#  the yday-ref pass upgrades them in place (PK upsert) to daily-close-
#  basis weighted rows.
#  Anti-join skips (code, time) rows already present with ANY flag —
#  identical values between fallback runs (no churn), and weighted rows
#  are never downgraded.
# ----------------------------------------------------------------------------
_FALLBACK_TICKS_SQL = """
WITH universe AS (
    SELECT DISTINCT
        sc.code AS member_code,
        sc.type AS member_sec_type
    FROM stats.sec_classification sc
    WHERE sc.is_active = TRUE
      AND sc.type = ANY($3::text[])
      AND sc.industry_id IS NOT NULL AND sc.industry_id <> ''
      AND sc.industry_id <> ALL($4::text[])
      AND sc.code != $1::text
),
prev_d AS (
    -- Single resolved prev intraday date (latest close-bearing date
    -- strictly before the live date) — one global resolution, all
    -- prev-value CTEs below are exact-date one-day scans on it.
    SELECT MAX(date) AS d
    FROM stats.index_intraday_5min
    WHERE date < $2::date
      AND close IS NOT NULL
),
member_prev_close AS (
    SELECT DISTINCT ON (code)
        code,
        close AS prev_close
    FROM stats.index_intraday_5min
    WHERE date = (SELECT d FROM prev_d)
      AND close IS NOT NULL
      AND close::text <> 'NaN'
      AND code IN (SELECT member_code FROM universe)
    ORDER BY code, time DESC
),
bench_prev_close AS (
    SELECT close AS prev_close
    FROM stats.index_intraday_5min
    WHERE code = $1::text
      AND date = (SELECT d FROM prev_d)
      AND close IS NOT NULL
      AND close::text <> 'NaN'
    ORDER BY time DESC
    LIMIT 1
),
bench_bars AS (
    SELECT i5.time, i5.close
    FROM stats.index_intraday_5min i5
    WHERE i5.code = $1::text
      AND i5.date = $2::date
      AND i5.close IS NOT NULL
)
SELECT
    u.member_code                 AS code,
    u.member_sec_type             AS sec_type,
    i5.date,
    i5.time,
    i5.close                      AS tick_close,
    mp.prev_close                 AS code_prev_date_close,
    bp.prev_close                 AS benchmark_prev_date_close,
    bb.close                      AS bench_tick_close
FROM stats.index_intraday_5min i5
JOIN universe u ON u.member_code = i5.code
JOIN member_prev_close mp ON mp.code = i5.code
CROSS JOIN bench_prev_close bp
LEFT JOIN bench_bars bb ON bb.time = i5.time
WHERE i5.date = $2::date
  AND i5.close IS NOT NULL
  AND NOT EXISTS (
      SELECT 1
      FROM live.sec_alloc_live_attribution t
      WHERE t.benchmark_code = $1::text
        AND t.date = i5.date
        AND t.code = i5.code
        AND t.sec_type = u.member_sec_type
        AND t.time = i5.time
  )
ORDER BY i5.code, i5.time
"""


async def fetch_fallback_ticks(
    conn: asyncpg.Connection,
    benchmark_code: str,
    live_date: datetime.date,
) -> list[dict]:
    """Fetch fallback (ref-less) pending tick rows for one pair.

    Returns rows with the same shape as fetch_missing_ticks.
    """
    rows = await conn.fetch(
        _FALLBACK_TICKS_SQL,
        benchmark_code,
        live_date,
        list(TICK_CLASS_TYPES),
        list(BROAD_EXCLUDED),
    )
    return [
        {
            "code": r["code"],
            "sec_type": r["sec_type"],
            "date": r["date"],
            "time": r["time"],
            "tick_close": _f(r["tick_close"]),
            "code_prev_date_close": _f(r["code_prev_date_close"]),
            "benchmark_prev_date_close": _f(r["benchmark_prev_date_close"]),
            "bench_tick_close": _f(r["bench_tick_close"]),
        }
        for r in rows
    ]
