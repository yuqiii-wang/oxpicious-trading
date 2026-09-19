"""Configuration for analyze.analysis_forecasts.

Monthly per-security forecast analysis stored in the
``analysis_forecasts`` schema, split into MOTIVATION (bucket-defining)
and RESULT tables:

  - mov_rsi: per (sec_type, code, stat_month, rsi_window, side, pct,
    is_market_hyped) the days whose rsi_{W}days sits in the top/bottom
    pct% of the trailing 5-year window ending at stat_month (the RSI
    values themselves join from analysis.mov_ave_rsi via rsi_window).

  - mov_std: per (sec_type, code, stat_month, ma_window, k, side,
    is_market_hyped) the Bollinger-breach days (price beyond
    ma_{W} ± k·std_{W}days) within the same window (band inputs join
    from analysis.mov_ave_spreads_detail / stats.*_tech_stats).

  - forecast_results: the RESULT data keyed by the surrogate
    forecast_id — mean forward changes at the next, 5d, 20d, 60d
    horizons; max/min forward changes (close-based) at the 5d/20d/60d
    horizons only; per-horizon swing-aware reversal
    probabilities — P(the n-day forward window's adverse path extreme
    beyond the fixed 1% bar: the window swung past ±1% against the
    side at some close; at the 5d horizon about the closes of the 5
    days after the signal) — and
    occurrence counts. Each mov_rsi /
    mov_std row carries a
    forecast_id linking 1:1 to its result rows (5 periods — the four horizons plus the weight-blended mixed row, see PERIOD_MIXED).

  - base_rates: per (sec_type, code, stat_month, period) the
    UNCONDITIONAL same-window reference — mean n-day forward change
    and P(change < −reverse_threshold) / P(change > +reverse_threshold)
    over ALL of the code's
    window trading days — so bucket ave_change / reverse_prob read
    as lift vs base rate.

Each stat month is a COMPLETED calendar month-end, plus the RUNNING
month (keyed at its own month-end, computed only up to the latest
available data date — refreshed on every run while its forward windows
grow, and re-derived with complete data once the month completes, so
the incremental contract holds at month granularity). Completed
months' results are immutable once written (closes / RSI / MA / std
inside the window are historical facts).
"""
from __future__ import annotations

# The package re-exports every public constant so the historical
# ``analyze.analysis_forecasts.config.X`` import paths keep working
# (analyze.analysis_signals, analyze.mov_ave_spread.price_vs_amt,
# live.live_signals.analysis.fetch, ...).

from .grid import *  # noqa: F401,F403
from .horizons import *  # noqa: F401,F403
from .buckets import *  # noqa: F401,F403
from .tables import *  # noqa: F401,F403
from .descriptions import *  # noqa: F401,F403
