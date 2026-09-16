"""signals.industry — per-industry MA5-deviation episode detection.

An industry's member indices each contribute a daily return; the
industry's daily return is their mean, and its deviation from the
benchmark's daily return is the industry's relative move. The MA5 line
(5-day mean of that deviation series) rising or dropping faster than
IND_MA5_DEV_PCT pp/day vs zero marks a sudden rise/drop relative to 大盘.

Partitioned by industry_id: the label catalog is fetched once (small),
then each industry's own member closes are fetched separately — joining
the label catalog into the closes query explodes it to 100M+ rows
(sec_classification carries thousands of rows per industry). Peak memory
is bounded to a single industry's series.
"""
from __future__ import annotations

import datetime
import statistics
from typing import AsyncIterator, Dict, List, Optional, Tuple

from .base import BENCHMARK_CLOSES_SQL, _ma5, _ma5_slope_pct, _next_td

IND_MA5_DEV_PCT = 1.5
# Annual rate limiting: per industry per calendar year, the std of its
# daily MA5-slope deviation from the benchmark classifies the year —
# low-vol years get at most IND_ANNUAL_LOW_CAP asks, high-vol at most
# IND_ANNUAL_HIGH_CAP — and kept events must be >= IND_MIN_SPACING_TD
# trading days apart.
IND_ANNUAL_LOW_STD = 1.0
IND_ANNUAL_LOW_CAP = 4
IND_ANNUAL_HIGH_CAP = 12
IND_MIN_SPACING_TD = 10

INDUSTRY_LIST_SQL = """
    SELECT DISTINCT sit.industry_id AS industry_id,
           COALESCE(sc.industry_label, sit.industry_id) AS industry_label
    FROM stats.sec_index_tags sit
    LEFT JOIN stats.sec_classification sc ON sc.industry_id = sit.industry_id
    WHERE sit.industry_id IS NOT NULL AND sit.is_industry_not_strategy = TRUE
    ORDER BY industry_id
"""

INDUSTRY_MEMBER_CLOSES_SQL = """
    SELECT sit.code AS code, ib.date AS date, ib.close AS close
    FROM stats.index_basic_stats ib
    JOIN stats.sec_index_tags sit ON sit.code = ib.code
    WHERE sit.industry_id = $1::text AND sit.is_industry_not_strategy = TRUE
      AND ib.close IS NOT NULL
    ORDER BY sit.code, ib.date
"""


async def iter_industry_episodes(
    conn, *, benchmark_code: str = "000300",
    deviation_pct: float = IND_MA5_DEV_PCT,
) -> AsyncIterator[Tuple[str, str, Dict[int, float], Dict[int, int],
                         Dict[datetime.date, tuple]]]:
    """ASYNC GENERATOR — one partition per industry, peak memory bounded
    to a single industry's series.

    Yields (industry_id, label, year_std, year_cap, episodes):
      year_std  {year: std} of the industry's daily MA5-slope deviation
                from the benchmark, per calendar year (annual-std classing)
      episodes  {ask_date: (side, max_dev, ep_start, ep_end)} — one entry
                per rise/drop EPISODE, anchored at the next trading day
                after the episode ends (the move just completed)."""
    b_by_date = {r["date"]: float(r["close"])
                 for r in await conn.fetch(BENCHMARK_CLOSES_SQL,
                                           benchmark_code)}
    bdates = sorted(b_by_date)
    bma = _ma5([b_by_date[d] for d in bdates])
    bslope = _ma5_slope_pct(bma)
    bpos = {d: i for i, d in enumerate(bdates)}

    labels: Dict[str, str] = {}
    for r in await conn.fetch(INDUSTRY_LIST_SQL):
        labels[r["industry_id"]] = r["industry_label"]

    for ind in sorted(labels):
        label = labels[ind]
        rows = await conn.fetch(INDUSTRY_MEMBER_CLOSES_SQL, ind)
        per_member: Dict[str, Dict[datetime.date, float]] = {}
        for r in rows:
            per_member.setdefault(r["code"], {})[r["date"]] = float(r["close"])

        # per-date mean of member MA5 slopes (equal weight, scale-free)
        slope_sum: Dict[datetime.date, List[float]] = {}
        for code, series in per_member.items():
            ds = sorted(series)
            if len(ds) <= 5:
                continue
            closes = [series[d] for d in ds]
            ma = _ma5(closes)
            slope = _ma5_slope_pct(ma)
            for d, s in zip(ds, slope):
                if s is None:
                    continue
                slope_sum.setdefault(d, []).append(s)

        dates = sorted(slope_sum)
        if not dates:
            continue

        # deviation series: industry MA5 slope − benchmark MA5 slope (pp)
        devs: List[Optional[float]] = []
        for d in dates:
            bi = bpos.get(d)
            vals = slope_sum[d]
            if bi is None or bi == 0 or bslope[bi] is None:
                devs.append(None)
            else:
                devs.append(sum(vals) / len(vals) - bslope[bi])

        # annual std of the deviation series
        year_vals: Dict[int, List[float]] = {}
        for d, dev in zip(dates, devs):
            if dev is not None:
                year_vals.setdefault(d.year, []).append(dev)
        year_std = {y: (statistics.pstdev(v) if len(v) >= 2 else 0.0)
                    for y, v in sorted(year_vals.items())}
        # self-calibrated: years at/below the industry's OWN median annual
        # std are low-vol (LOW cap), above it high-vol (HIGH cap) — each
        # industry's natural volatility sets its own bar.
        med = (statistics.median(year_std.values()) if year_std else 0.0)
        year_cap = {y: (IND_ANNUAL_LOW_CAP if s <= med
                        else IND_ANNUAL_HIGH_CAP)
                    for y, s in year_std.items()}

        # episodes: consecutive same-direction trigger days (gap <= 2td)
        episodes: Dict[datetime.date, tuple] = {}
        open_side: Optional[str] = None
        open_start = open_end = None
        open_max = 0.0
        open_last_i: Optional[int] = None
        for i, d in enumerate(dates):
            dev = devs[i]
            if dev is None or abs(dev) < deviation_pct:
                continue
            side = "HYPE" if dev > 0 else "DRAIN"
            if open_side == side and open_last_i is not None \
                    and i - open_last_i <= 2:
                open_end = d
                open_max = max(open_max, abs(dev))
                open_last_i = i
            else:
                if open_side is not None:
                    episodes[_next_td(dates, open_end)] = (
                        open_side, open_max, open_start, open_end)
                open_side, open_start, open_end = side, d, d
                open_max, open_last_i = abs(dev), i
        if open_side is not None:
            episodes[_next_td(dates, open_end)] = (
                open_side, open_max, open_start, open_end)

        yield ind, label, year_std, year_cap, episodes
