"""signals.market — 上证指数 (000001) trigger and episode detection.

A day is a trigger when the index's |daily return| >= MARKET_DAILY_MOVE_PCT,
or when its trailing MARKET_WINDOW_DAYS-day move >= MARKET_WINDOW_MOVE_PCT.
Consecutive same-direction trigger days merge into one EPISODE; the ask is
anchored at the next trading day after the episode ends.
"""
from __future__ import annotations

import datetime
from typing import Dict, List, Optional, Tuple

from .base import INDEX_CLOSES_SQL, _next_td

MARKET_INDEX_CODE = "000001"          # 上证指数 in stats.index_basic_stats
MARKET_DAILY_MOVE_PCT = 3.0           # |daily return| bar
MARKET_WINDOW_DAYS = 5
MARKET_WINDOW_MOVE_PCT = 6.0          # trailing-week bar
# How many trading dates back from an anchor a trigger still nominates a
# market ask (the walk anchors are 5 trading days apart).
MARKET_LOOKBACK_RANKING_DATES = 10
# Same-direction market re-ask window (trading dates) — tighter than the
# industries': distinct market episodes a month apart each get asked.
MARKET_DEDUPE_WINDOW = 20
# Hard annual cap: at most this many broadmarket asks per calendar year
# (strongest episodes first) — enforced in run_backfill's market plan.
MARKET_ANNUAL_CAP = 12

INDEX_PARENT_TAGS_SQL = """
    SELECT sector_id, industry_id FROM stats.sec_index_tags
    WHERE code = $1::text AND industry_id IS NOT NULL
      AND industry_id != 'benchmark_broadmarket'
    LIMIT 1
"""


async def fetch_index_parent_tags(
    conn, code: str = MARKET_INDEX_CODE,
) -> Tuple[Optional[str], Optional[str]]:
    """(industry_id, sector_id) — the index's parent classification tags
    (e.g. 000001 -> BROAD_SSE / BROAD)."""
    row = await conn.fetchrow(INDEX_PARENT_TAGS_SQL, code)
    if row is None:
        return None, None
    return row["industry_id"], row["sector_id"]


async def fetch_market_triggers(
    conn, *, code: str = MARKET_INDEX_CODE,
) -> Dict[datetime.date, Tuple[str, float, int]]:
    """{date: (side, pct, window_days)} for every large broad-index move.

    side is HYPE for rises / DRAIN for drops, pct the move that fired and
    window_days the span it happened over (1 = a single day, 5 = the
    trailing-week window)."""
    rows = await conn.fetch(INDEX_CLOSES_SQL, code)
    closes = [(r["date"], float(r["close"])) for r in rows]
    triggers: Dict[datetime.date, Tuple[str, float, int]] = {}
    for i, (d, close) in enumerate(closes):
        hit: Optional[Tuple[str, float, int]] = None
        if i >= 1:
            daily = (close - closes[i - 1][1]) / closes[i - 1][1] * 100.0
            if abs(daily) >= MARKET_DAILY_MOVE_PCT:
                hit = ("HYPE" if daily > 0 else "DRAIN", round(daily, 2), 1)
        if hit is None and i >= MARKET_WINDOW_DAYS:
            window = ((close - closes[i - MARKET_WINDOW_DAYS][1])
                      / closes[i - MARKET_WINDOW_DAYS][1] * 100.0)
            if abs(window) >= MARKET_WINDOW_MOVE_PCT:
                hit = ("HYPE" if window > 0 else "DRAIN",
                       round(window, 2), MARKET_WINDOW_DAYS)
        if hit is not None:
            triggers[d] = hit
    return triggers


async def fetch_market_episodes(
    conn, *, code: str = MARKET_INDEX_CODE,
) -> Dict[datetime.date, Tuple[str, float]]:
    """{ask_date: (side, max_pct)} — one entry per rise/drop EPISODE
    (consecutive same-direction trigger days; a single quiet day does not
    split one), anchored at the next trading day after the episode ends."""
    raw = await fetch_market_triggers(conn, code=code)
    rows = await conn.fetch(INDEX_CLOSES_SQL, code)
    dates = [r["date"] for r in rows]
    devs = [raw[d][1] if d in raw else None for d in dates]
    episodes: Dict[datetime.date, Tuple[str, float]] = {}
    open_side: Optional[str] = None
    open_start = open_end = None
    open_max = 0.0
    open_last_i: Optional[int] = None
    for i, d in enumerate(dates):
        dev = devs[i]
        if dev is None:
            continue
        side = "HYPE" if dev > 0 else "DRAIN"
        if open_side == side and open_last_i is not None and i - open_last_i <= 2:
            open_end = d
            open_max = max(open_max, abs(dev))
            open_last_i = i
        else:
            if open_side is not None:
                episodes[_next_td(dates, open_end)] = (open_side, open_max)
            open_side, open_start, open_end = side, d, d
            open_max, open_last_i = abs(dev), i
    if open_side is not None:
        episodes[_next_td(dates, open_end)] = (open_side, open_max)
    return episodes
