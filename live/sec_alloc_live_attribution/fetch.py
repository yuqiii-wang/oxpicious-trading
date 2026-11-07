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

from _common.sql_views import frag, render_sql_view

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


# ----------------------------------------------------------------------------
#  Tick-eligible (benchmark, date) pairs — LIVE mode (latest intraday date)
#  and COMPUTE mode (arbitrary target date). NOT migrated to sql_views
#  templates: the originals bind benchmarks as $1 although $2/$3 (and $4 in
#  compute mode) appear EARLIER in the SQL text — the loader numbers slots
#  by first-use order, so the rendered text could never byte-match.
#  Eligibility: the benchmark has intraday bars on the target date AND is
#  in the CURATED broad-market tag set (the UI's benchmark universe), and
#  at least one tick-eligible member (type IN ('index','etf'), non-BROAD
#  industry_id) has bars on that date. The loader's anti-join skips
#  (code, time) rows that already exist with ANY flag, so pairs fully
#  covered by weighted rows are natural no-ops and brand-new bars get
#  equal-weight rows until the next yday-ref run upgrades them.
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


# ----------------------------------------------------------------------------
#  COMPUTE-mode pair finder (on-demand backfill of a SELECTED date) —
#  same NOT-migrated reason as the LIVE finder above ($2 = target date
#  appears first in the text, ahead of $1/$3/$4).
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


# ----------------------------------------------------------------------------
#  (benchmark, date) pairs with at least one pending WEIGHTED tick row —
#  either a genuinely missing (code, time) row, or an existing FALLBACK row
#  (is_without_trading_amt = TRUE) that can now be UPGRADED to a
#  daily-close-basis weighted row. Drives the yday-ref mode's incremental
#  pass. NOT migrated to a sql_views template: $1 = benchmarks appears
#  AFTER $2/$3 in the SQL text — the loader numbers slots by first-use
#  order, so the rendered text could never byte-match.
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


# ----------------------------------------------------------------------------
#  LIGHT fetch: ONLY the pending WEIGHTED member-tick rows for one
#  (benchmark, date) — template fetch_missing_ticks. Prev closes are
#  computed AT TICK TIME from stats.index_basic_stats (prev_d resolved ONCE
#  so every join is an exact-date one-day scan); the anti-join on
#  is_without_trading_amt = FALSE (re)fetches rows missing entirely OR
#  present only as FALLBACK, so the upsert upgrades fallback rows in place.
#  Zero closes (tick bar, prev closes, benchmark bar) are the sources'
#  no-quote placeholders — filtered like NULL so no tick is computed
#  against a zero price (which would read as a -100% move).
# ----------------------------------------------------------------------------


# ----------------------------------------------------------------------------
#  FALLBACK fetch (self-contained): tick rows for one (benchmark, date)
#  written by the 5-min LIVE pass so equal-weighted data flows immediately.
#  NOT migrated to a sql_views template: $1 = benchmark_code appears
#  AFTER $3/$4 in the SQL text — the loader numbers slots by first-use
#  order, so the rendered text could never byte-match.
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
#  Zero closes (tick bar, prev closes, benchmark bar) are the sources'
#  no-quote placeholders — filtered like NULL so no tick is computed
#  against a zero price (which would read as a -100% move).
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
      AND close <> 0
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
      AND close <> 0
    ORDER BY time DESC
    LIMIT 1
),
bench_bars AS (
    SELECT i5.time, i5.close
    FROM stats.index_intraday_5min i5
    WHERE i5.code = $1::text
      AND i5.date = $2::date
      AND i5.close IS NOT NULL
      AND i5.close <> 0
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
  AND i5.close <> 0
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


class AttributionStore:
    """Read-only SELECT layer of the attribution pipeline (conn-scoped
    store).

    Constructed once per run connection: ``AttributionStore(conn)`` —
    every former conn-first module function of this file is a method with
    the same name, SQL and sequencing (``conn`` became ``self._conn``);
    the raw SQL constants above are the deliberately-not-migrated-to-
    sql_views templates (their param slots are numbered by first-use
    order and could never byte-match the loader's rendering)."""

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def fetch_latest_intraday_dates(
        self, n_dates: int = 1,
    ) -> list[datetime.date]:
        """Return the latest N distinct intraday 5-min dates (default: today only)."""
        # live/sec_alloc_live_attribution/fetch/fetch_latest_intraday_dates
        view = render_sql_view(
            "live/sec_alloc_live_attribution/fetch/fetch_latest_intraday_dates",
            n_dates=n_dates,
        )
        rows = await self._conn.fetch(view.sql, *view.params)
        return [r["date"] for r in rows]

    async def find_live_tick_pairs(
        self,
        benchmarks: Sequence[str] | None = None,
    ) -> list[tuple[str, datetime.date]]:
        """Return ALL tick-eligible (benchmark, latest_date) pairs (live mode)."""
        bench_param = list(benchmarks) if benchmarks else None
        rows = await self._conn.fetch(
            _LIVE_TICK_PAIRS_SQL,
            bench_param,
            list(BROAD_EXCLUDED),
            list(TICK_CLASS_TYPES),
        )
        return [(r["benchmark_code"], r["tick_date"]) for r in rows]

    async def find_compute_tick_pairs(
        self,
        benchmarks: Sequence[str] | None,
        target_date: datetime.date,
    ) -> list[tuple[str, datetime.date]]:
        """Return ALL tick-eligible (benchmark, target_date) pairs (compute mode).

        ``benchmarks`` = None means every curated benchmark with bars on the
        target date. An empty result means the date has no raw intraday data
        (outside the raw table's retention, a non-trading day, or the future).
        """
        bench_param = list(benchmarks) if benchmarks else None
        rows = await self._conn.fetch(
            _COMPUTE_TICK_PAIRS_SQL,
            bench_param,
            target_date,
            list(BROAD_EXCLUDED),
            list(TICK_CLASS_TYPES),
        )
        return [(r["benchmark_code"], r["tick_date"]) for r in rows]

    async def find_pairs_with_missing_ticks(
        self,
        benchmarks: Sequence[str] | None = None,
    ) -> list[tuple[str, datetime.date]]:
        """Return (benchmark, date) pairs with missing OR upgrade-eligible ticks."""
        bench_param = list(benchmarks) if benchmarks else None
        rows = await self._conn.fetch(
            _PAIRS_WITH_MISSING_TICKS_SQL,
            bench_param,
            list(BROAD_EXCLUDED),
            list(TICK_CLASS_TYPES),
        )
        return [(r["benchmark_code"], r["date"]) for r in rows]

    async def fetch_missing_ticks(
        self,
        benchmark_code: str,
        live_date: datetime.date,
    ) -> list[dict]:
        """Fetch pending weighted (code, time) tick rows for one pair.

        Returns rows with: code, sec_type, date, time, tick_close,
        code_prev_date_close, benchmark_prev_date_close, bench_tick_close.
        """
        # live/sec_alloc_live_attribution/fetch/fetch_missing_ticks
        view = render_sql_view(
            "live/sec_alloc_live_attribution/fetch/fetch_missing_ticks",
            benchmark_code=benchmark_code,
            live_date=live_date,
            tick_class_types=list(TICK_CLASS_TYPES),
            broad_excluded=list(BROAD_EXCLUDED),
        )
        rows = await self._conn.fetch(view.sql, *view.params)
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

    async def fetch_fallback_ticks(
        self,
        benchmark_code: str,
        live_date: datetime.date,
    ) -> list[dict]:
        """Fetch fallback (ref-less) pending tick rows for one pair.

        Returns rows with the same shape as fetch_missing_ticks.
        """
        rows = await self._conn.fetch(
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
