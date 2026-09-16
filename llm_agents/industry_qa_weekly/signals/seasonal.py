"""signals.seasonal — LEGACY seasonal top-5 ranking helpers.

These read the UI's "Market Trend → Hypes & Drains" seasonal table
(analysis.industry_hypes_seasonal / .industry_hypes_and_drains) and are
kept only for the live weekly run's anchor resolution and any callers
that still read the seasonal ranking. The plan-first agent's candidates
come from the MA5-deviation episodes (signals.industry) and the broad-
index triggers (signals.market) instead."""
from __future__ import annotations

import datetime
from typing import List, Optional

from .base import DEFAULT_BENCHMARK, DEFAULT_PERIOD_DAYS, \
    DEFAULT_TOP_N, DEFAULT_WEIGHTING, IndustrySignal, _f

TABLE = "analysis.industry_hypes_seasonal"
DATES_TABLE = "analysis.industry_hypes_and_drains"

AS_OF_DATE_SQL = f"""
    SELECT MAX(date) AS as_of
    FROM {DATES_TABLE}
    WHERE benchmark_code = $1::text
      AND period_days = $2::int
      AND weighting = $3::text
      AND date <= $4::date
"""

RANKING_DATES_SQL = f"""
    SELECT DISTINCT date
    FROM {DATES_TABLE}
    WHERE benchmark_code = $1::text
      AND period_days = $2::int
      AND weighting = $3::text
    ORDER BY date
"""

TOP_SIGNALS_SQL = f"""
    SELECT season_qkey, rank_side, rank, industry_id, industry_label,
           peak_metric_value
    FROM {TABLE}
    WHERE benchmark_code = $1::text
      AND period_days = $2::int
      AND weighting = $3::text
      AND season_qkey = to_char($4::date, 'YYYY-MM')
      AND rank <= $5::int
    ORDER BY rank_side, rank
"""


async def latest_signal_date(
    conn, *, benchmark_code: str = DEFAULT_BENCHMARK,
    period_days: int = DEFAULT_PERIOD_DAYS,
    weighting: str = DEFAULT_WEIGHTING,
    as_of: Optional[datetime.date] = None,
) -> Optional[datetime.date]:
    """Latest ranking date <= *as_of* (today when None); None when absent."""
    as_of = as_of or datetime.date.today()
    d = await conn.fetchval(AS_OF_DATE_SQL, benchmark_code, period_days,
                            weighting, as_of)
    return d


async def fetch_ranking_dates(
    conn, *, benchmark_code: str = DEFAULT_BENCHMARK,
    period_days: int = DEFAULT_PERIOD_DAYS,
    weighting: str = DEFAULT_WEIGHTING,
) -> List[datetime.date]:
    """Every distinct ranking date, ascending."""
    return [r["date"] for r in await conn.fetch(
        RANKING_DATES_SQL, benchmark_code, period_days, weighting)]


async def fetch_top_industries(
    conn, *, benchmark_code: str = DEFAULT_BENCHMARK,
    period_days: int = DEFAULT_PERIOD_DAYS,
    weighting: str = DEFAULT_WEIGHTING,
    as_of: Optional[datetime.date] = None,
    top_n: int = DEFAULT_TOP_N,
) -> List[IndustrySignal]:
    """The top-*top_n* HYPE + top-*top_n* DRAIN industries of the season
    containing the latest ranking date <= *as_of* — the block the Market
    Trend view renders for that month."""
    as_of = as_of or datetime.date.today()
    rows = await conn.fetch(TOP_SIGNALS_SQL, benchmark_code, period_days,
                            weighting, as_of, top_n)
    signals = [IndustrySignal(
        date=as_of, side=r["rank_side"], rank=r["rank"],
        industry_id=r["industry_id"],
        industry_label=r["industry_label"] or r["industry_id"],
        metric_value=_f(r["peak_metric_value"]),
        season_qkey=r["season_qkey"],
        benchmark_code=benchmark_code, period_days=period_days,
        weighting=weighting,
    ) for r in rows]
    return sorted(signals,
                  key=lambda s: (0 if s.side == "HYPE" else 1, s.rank))
