"""signals — ask-candidate detection for the sudden-move Q&A agent.

* ``base``      — shared constants, IndustrySignal, index close queries,
  MA5 helpers.
* ``market``    — 上证指数 (000001) large-move triggers and episodes, and
  the index's parent industry/sector tags.
* ``industry``  — per-industry MA5-deviation episodes (partitioned by
  industry_id, one industry's series in memory at a time).
* ``seasonal``  — legacy seasonal top-5 ranking helpers (UI's Hypes &
  Drains view).
"""
from __future__ import annotations

from .base import (
    BENCHMARK_CLOSES_SQL, DEFAULT_BENCHMARK, DEFAULT_PERIOD_DAYS,
    DEFAULT_TOP_N, DEFAULT_WEIGHTING, INDEX_CLOSES_SQL, IndustrySignal,
    MARKET_GRID_SQL, _f, _ma5, _ma5_slope_pct, _next_td,
    fetch_market_grid_dates,
)
from .market import (
    MARKET_ANNUAL_CAP, MARKET_DAILY_MOVE_PCT, MARKET_DEDUPE_WINDOW,
    MARKET_INDEX_CODE, MARKET_LOOKBACK_RANKING_DATES, MARKET_WINDOW_DAYS,
    MARKET_WINDOW_MOVE_PCT, fetch_index_parent_tags, fetch_market_episodes,
    fetch_market_triggers,
)
from .industry import (
    IND_ANNUAL_HIGH_CAP, IND_ANNUAL_LOW_CAP, IND_ANNUAL_LOW_STD,
    IND_MA5_DEV_PCT, IND_MIN_SPACING_TD, iter_industry_episodes,
)
from .seasonal import (
    fetch_ranking_dates, fetch_top_industries, latest_signal_date,
)
