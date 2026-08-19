"""Shared async DB fetch primitives for live.sec_alloc_live_attribution.

All SQL here is read-only SELECT. INSERTs/UPDATEs live in ref.py / ticks.py
via ``bulk_upsert_async`` (per project rule: ad-hoc SQL insert/update
operations must be consolidated into Python code).
"""
from __future__ import annotations

import datetime
import math
from typing import Sequence

import asyncpg

from .config import BROAD_EXCLUDED, TICK_CLASS_TYPES


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
#  Find missing REF (benchmark_code, date) pairs for the heavy once-per-date
#  reference table.
#
#  Scope is BY DESIGN the single LATEST intraday date (today) — no date
#  parameter. The query forces the target date FIRST via a CTE so the
#  planner can use the index_intraday_5min PK (date, code, time); a
#  parameterized OR-guard (``$1::date[] IS NULL OR date = ANY($1)``) here
#  defeated the index and seq-scanned the whole multi-year 5-min table
#  (prepared-statement generic plan — lesson learned).
#
#  A pair is missing iff:
#    • the benchmark appears in today_bench (has intraday data), AND
#    • it has an ELIGIBLE member universe — ALL classified indices with a
#      non-BROAD industry_id are members (not just those with non-zero
#      composition overlap — zero-weight indices still carry price pct for
#      the equal-weight Market Movements shading), with at least one
#      TICK-ELIGIBLE member (type IN ('index','etf')) having intraday bars
#      on that date, AND
#    • no row exists in live.sec_alloc_live_prev_ref for (benchmark, date).
#
#  "Skip if today's ref already present" is inherent: pairs with existing
#  ref rows are not missing → skipped on every subsequent 5-min run.
# ----------------------------------------------------------------------------
_FIND_MISSING_REF_PAIRS_SQL = """
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
  AND NOT EXISTS (
      SELECT 1
      FROM live.sec_alloc_live_prev_ref r
      WHERE r.benchmark_code = tb.code
        AND r.date = (SELECT d FROM target)
  )
ORDER BY tb.code
"""


async def find_missing_ref_pairs(
    conn: asyncpg.Connection,
    benchmarks: Sequence[str] | None = None,
) -> list[tuple[str, datetime.date]]:
    """Return (benchmark_code, latest_date) pairs whose heavy ref is missing."""
    bench_param = list(benchmarks) if benchmarks else None
    rows = await conn.fetch(
        _FIND_MISSING_REF_PAIRS_SQL,
        bench_param,
        list(BROAD_EXCLUDED),
        list(TICK_CLASS_TYPES),
    )
    return [(r["benchmark_code"], r["tick_date"]) for r in rows]


# ----------------------------------------------------------------------------
#  Find ALL tick-eligible (benchmark, date) pairs for the LIVE process
#  (--mode live — the 5-min equal-weight path). Same eligibility rules as
#  _FIND_MISSING_REF_PAIRS_SQL but WITHOUT the "ref missing" NOT EXISTS —
#  the live process runs the ref-less FALLBACK tick loader for every
#  eligible pair regardless of ref state (its anti-join skips (code, time)
#  rows that already exist with ANY flag, so pairs fully covered by
#  weighted rows are natural no-ops and brand-new bars get equal-weight
#  rows until the next yday-ref run upgrades them).
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
"""


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
#  HEAVY fetch: member universe + prev-day reference values for ONE
#  (benchmark, date) ref pair.
#
#  Split prev-row selection (IMPORTANT — stats.index_basic_stats contains
#  literal NaN numerics for trading_amount on some dates, and IS NOT NULL
#  does NOT filter them out):
#    • prev close/prev_date: latest row with close IS NOT NULL per member
#      (close is clean — 0 NaN) → consistent pct base.
#    • prev trading_amount: latest row with a REAL amount (NOT NULL AND
#      text <> 'NaN') within a bounded 14-day lookback → liquidity weight
#      as-of. Amount and close may come from different dates; that is
#      intentional (weights are relative liquidity shares, pct must not
#      mix bases). Members without a real amount in the lookback get NULL
#      amount/weight — excluded from the weighted aggregate (renormalized
#      at query time) but still usable for the equal-weighted aggregate.
#
#  Benchmark prev close = benchmark's latest close date strictly before
#  the live date (independent of member prev dates).
# ----------------------------------------------------------------------------
_REF_MEMBERS_SQL = """
WITH universe AS (
    SELECT
        sc.code                    AS member_code,
        sc.type                    AS member_sec_type,
        sc.industry_id,
        sc.is_industry_not_strategy
    FROM stats.sec_classification sc
    WHERE sc.is_active = TRUE
      AND sc.industry_id IS NOT NULL AND sc.industry_id <> ''
      AND sc.industry_id <> ALL($3::text[])
      AND sc.code != $1::text
),
bench_comp AS (
    -- Benchmark's latest composition snapshot (most recent per stock)
    SELECT stock_code,
           LEFT(stock_code, 6) AS normalized_code,
           weight_pct
    FROM (
        SELECT sc.stock_code, sc.weight_pct,
               ROW_NUMBER() OVER (PARTITION BY sc.stock_code
                                  ORDER BY sc.snapshot_date DESC) AS rn
        FROM stats.sec_composition sc
        WHERE sc.code = $1::text
          AND sc.stock_code IS NOT NULL
    ) t
    WHERE rn = 1
),
member_comp AS (
    -- Each member's latest composition snapshot (most recent per code+stock)
    SELECT mc.code,
           LEFT(mc.stock_code, 6) AS normalized_code,
           mc.weight_pct,
           ROW_NUMBER() OVER (PARTITION BY mc.code, mc.stock_code
                              ORDER BY mc.snapshot_date DESC) AS rn
    FROM stats.sec_composition mc
    JOIN universe u ON u.member_code = mc.code
    WHERE mc.stock_code IS NOT NULL
),
shared AS (
    -- Member's composition overlap weight vs benchmark
    -- Only members with at least one overlapping stock get a row here;
    -- others will be COALESCE'd to 0 in the final SELECT.
    SELECT mc.code AS member_code,
           SUM(mc.weight_pct) AS code_sec_shared_weight
    FROM member_comp mc
    JOIN bench_comp bc
      ON mc.normalized_code = bc.normalized_code AND mc.rn = 1
    WHERE mc.code != $1::text
    GROUP BY mc.code
),
member_prev_close AS (
    SELECT DISTINCT ON (u.member_code)
        u.member_code,
        b.date                       AS prev_date,
        b.close                      AS prev_close
    FROM universe u
    JOIN stats.index_basic_stats b
        ON b.code = u.member_code
       AND b.date < $2::date
       AND b.close IS NOT NULL
    ORDER BY u.member_code, b.date DESC
),
member_prev_amt AS (
    SELECT DISTINCT ON (u.member_code)
        u.member_code,
        b.trading_amount             AS prev_trading_amount
    FROM universe u
    JOIN stats.index_basic_stats b
        ON b.code = u.member_code
       AND b.date < $2::date
       AND b.date > $2::date - INTERVAL '14 days'
       AND b.trading_amount IS NOT NULL
       AND b.trading_amount::text <> 'NaN'
    ORDER BY u.member_code, b.date DESC
),
bench_prev AS (
    SELECT close AS bench_prev_close
    FROM stats.index_basic_stats
    WHERE code = $1::text
      AND date < $2::date
      AND close IS NOT NULL
      AND close::text <> 'NaN'
    ORDER BY date DESC
    LIMIT 1
)
SELECT
    mp.member_code,
    u.member_sec_type,
    u.industry_id,
    u.is_industry_not_strategy,
    mp.prev_date,
    mp.prev_close,
    ma.prev_trading_amount,
    bp.bench_prev_close,
    COALESCE(sw.code_sec_shared_weight, 0) AS code_sec_shared_weight
FROM member_prev_close mp
JOIN universe u ON u.member_code = mp.member_code
LEFT JOIN member_prev_amt ma ON ma.member_code = mp.member_code
LEFT JOIN bench_prev bp ON true
LEFT JOIN shared sw ON sw.member_code = mp.member_code
ORDER BY mp.member_code
"""


async def fetch_ref_members(
    conn: asyncpg.Connection,
    benchmark_code: str,
    live_date: datetime.date,
) -> list[dict]:
    """Fetch heavy prev-date reference inputs for one (benchmark, date).

    Returns one row per eligible member (ANY type incl. stocks — stocks
    feed share weights only) with: member_code, member_sec_type,
    industry_id, is_industry_not_strategy, prev_date, prev_close,
    prev_trading_amount, bench_prev_close (same for all members).
    """
    rows = await conn.fetch(
        _REF_MEMBERS_SQL, benchmark_code, live_date, list(BROAD_EXCLUDED)
    )
    return [
        {
            "member_code": r["member_code"],
            "member_sec_type": r["member_sec_type"],
            "industry_id": r["industry_id"],
            "is_industry_not_strategy": r["is_industry_not_strategy"],
            "prev_date": r["prev_date"],
            "prev_close": _f(r["prev_close"]),
            "prev_trading_amount": _f(r["prev_trading_amount"]),
            "bench_prev_close": _f(r["bench_prev_close"]),
            "code_sec_shared_weight": _f(r["code_sec_shared_weight"]),
        }
        for r in rows
    ]


# ----------------------------------------------------------------------------
#  Find (benchmark, date) ref pairs that have at least one pending WEIGHTED
#  tick row: either a genuinely missing (code, time) row, or an existing
#  FALLBACK row (is_without_trading_amt = TRUE) that can now be UPGRADED to
#  a ref-based weighted row. Drives the light 5-min incremental pass.
# ----------------------------------------------------------------------------
_PAIRS_WITH_MISSING_TICKS_SQL = """
SELECT DISTINCT r.benchmark_code, r.date
FROM live.sec_alloc_live_prev_ref r
JOIN stats.index_intraday_5min i5
    ON i5.code = r.code
   AND i5.date = r.date
   AND i5.close IS NOT NULL
WHERE ($1::text[] IS NULL OR r.benchmark_code = ANY($1::text[]))
  AND NOT EXISTS (
      SELECT 1
      FROM live.sec_alloc_live_attribution t
      WHERE t.benchmark_code = r.benchmark_code
        AND t.date = r.date
        AND t.code = r.code
        AND t.sec_type = r.sec_type
        AND t.time = i5.time
        AND t.is_without_trading_amt = FALSE
  )
ORDER BY r.benchmark_code, r.date
"""


async def find_pairs_with_missing_ticks(
    conn: asyncpg.Connection,
    benchmarks: Sequence[str] | None = None,
) -> list[tuple[str, datetime.date]]:
    """Return (benchmark, date) pairs with missing OR upgrade-eligible ticks."""
    bench_param = list(benchmarks) if benchmarks else None
    rows = await conn.fetch(_PAIRS_WITH_MISSING_TICKS_SQL, bench_param)
    return [(r["benchmark_code"], r["date"]) for r in rows]


# ----------------------------------------------------------------------------
#  LIGHT fetch: ONLY the pending WEIGHTED member-tick rows for one
#  (benchmark, date).
#
#  Joins ref (identity + prev closes) to intraday bars, scoped to
#  TICK-ELIGIBLE members (stats.sec_classification.type IN ('index','etf')
#  — stocks hold weights but never get tick rows). Anti-joins the tick
#  table per (code, time) on is_without_trading_amt = FALSE: rows missing
#  entirely OR present only as FALLBACK (TRUE) rows are (re)fetched — the
#  upsert then upgrades fallback rows in place to weighted rows. Benchmark
#  tick closes are joined per time for the denormalized benchmark pct.
# ----------------------------------------------------------------------------
_MISSING_TICKS_SQL = """
WITH bench_bars AS (
    SELECT i5.time, i5.close
    FROM stats.index_intraday_5min i5
    WHERE i5.code = $1::text
      AND i5.date = $2::date
      AND i5.close IS NOT NULL
)
SELECT
    r.code,
    r.sec_type,
    r.date,
    i5.time,
    i5.close                       AS tick_close,
    r.code_prev_date_close,
    r.benchmark_prev_date_close,
    bb.close                       AS bench_tick_close
FROM live.sec_alloc_live_prev_ref r
JOIN stats.index_intraday_5min i5
    ON i5.code = r.code
   AND i5.date = r.date
   AND i5.close IS NOT NULL
JOIN stats.sec_classification sc
    ON sc.code = r.code
   AND sc.type = ANY($3::text[])
LEFT JOIN bench_bars bb ON bb.time = i5.time
WHERE r.benchmark_code = $1::text
  AND r.date = $2::date
  AND NOT EXISTS (
      SELECT 1
      FROM live.sec_alloc_live_attribution t
      WHERE t.benchmark_code = r.benchmark_code
        AND t.date = r.date
        AND t.code = r.code
        AND t.sec_type = r.sec_type
        AND t.time = i5.time
        AND t.is_without_trading_amt = FALSE
  )
ORDER BY r.code, i5.time
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
        _MISSING_TICKS_SQL, benchmark_code, live_date, list(TICK_CLASS_TYPES)
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
#  FALLBACK fetch (no ref dependency): tick rows for one (benchmark, date)
#  whose ref is NOT ready (heavy pass still running elsewhere, or prev-day
#  basic_stats lagging). Used when:
#    • the advisory lock is held by another instance (fallback-only mode),
#    • or the heavy ref build produced 0 rows for the pair.
#
#  Prev-close basis = the member's LAST 5-min bar close of its latest
#  intraday date strictly BEFORE the live date (self-contained in
#  stats.index_intraday_5min — no basic_stats dependency). Benchmark prev
#  close likewise. Rows are written with is_without_trading_amt = TRUE;
#  they are upgraded in place (PK upsert) to FALSE once the ref exists.
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
member_prev_close AS (
    SELECT DISTINCT ON (code)
        code,
        close AS prev_close
    FROM stats.index_intraday_5min
    WHERE date < $2::date
      AND date > $2::date - INTERVAL '14 days'
      AND close IS NOT NULL
      AND close::text <> 'NaN'
      AND code IN (SELECT member_code FROM universe)
    ORDER BY code, date DESC, time DESC
),
bench_prev_close AS (
    SELECT close AS prev_close
    FROM stats.index_intraday_5min
    WHERE code = $1::text
      AND date < $2::date
      AND date > $2::date - INTERVAL '14 days'
      AND close IS NOT NULL
      AND close::text <> 'NaN'
    ORDER BY date DESC, time DESC
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
