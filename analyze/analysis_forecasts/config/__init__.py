"""Configuration for analyze.analysis_forecasts.

Monthly per-security forecast analysis stored in the
``analysis_forecasts`` schema, split into MOTIVATION (bucket-defining)
and RESULT tables:

  - mov_rsi: per (sec_type, code, stat_date, rsi_window, side, pct,
    regime_state) the days whose rsi_{W}days sits in the top/bottom
    pct% of the trailing 5-year window ending at stat_date (the RSI
    values themselves join from analysis.mov_ave_rsi via rsi_window).

  - mov_std: per (sec_type, code, stat_date, ma_window, k, side,
    regime_state) the Bollinger-breach days (price beyond
    ma_{W} ± k·std_{W}days) within the same window (band inputs join
    from analysis.mov_ave_spreads_detail / stats.*_tech_stats).

  - forecast_results: the RESULT data keyed by the surrogate
    forecast_id — mean forward changes at the next, 5d, 20d
    horizons; max/min forward changes (close-based) at the 5d/20d
    horizons only; and
    occurrence counts. Each mov_rsi /
    mov_std row carries a
    forecast_id linking 1:1 to its result rows (4 periods — the three
    horizons plus the weight-blended mixed row, see PERIOD_MIXED).

  - base_rates: per (sec_type, code, stat_date, period) the
    UNCONDITIONAL same-window reference — mean n-day forward change
    over ALL of the code's
    window trading days — so bucket ave_change reads
    as lift vs base rate.

Each snapshot sits on the ANNUAL grid — the last N_YEARS COMPLETED
year-ends plus the ROLLING LATEST snapshot (keyed at the sec_type's
latest available data date, refreshed on every run while its forward
windows grow, per config.mutable_dates) — so the incremental contract
holds at snapshot granularity. Completed year-ends' results are
immutable once their 20-trading-day forward windows have realized
(closes / RSI / MA / std inside the window are historical facts).
"""
from __future__ import annotations

# The package re-exports every public constant so the historical
# ``analyze.analysis_forecasts.config.X`` import paths keep working
# (analyze.analysis_signals, live.live_signals.analysis.fetch, ...).

from .grid import *  # noqa: F401,F403
from .horizons import *  # noqa: F401,F403
from .buckets import *  # noqa: F401,F403
from .tables import *  # noqa: F401,F403
from .descriptions import *  # noqa: F401,F403
from .regimes import *  # noqa: F401,F403
