"""DB fetchers + value resolution for the live breach check
(live.live_signals.analysis.fetch — one file per concern):

  _values    LiveValues / the SIGNAL_VALUE_SOURCE map / the sub_type
             parser / resolve_value / resolve_threshold (the cell's
             bar_def → its bar: bollinger's fixed ±kσ / mov_rsi's
             adaptive percentile tail) — the value-bar SPACE (no SQL).
  _sources   the code's CURRENT values (the latest RSI row + the
             mov_rsi adaptive trailing-quantile bars), fetched at most
             once per check, as-of bounded.
  _bars      the as-of bar resolution (last intraday bar ON D, else
             the official daily close — the on-demand --date mode).
  _active    the ACTIVE RULE CELLS (analysis_signals.signal_rules —
             the rules-era threshold set, live-wired families only),
             the market-regime probe and the day's TREND state (the
             cell-match inputs).

Everything here is DATA ACCESS — no decision logic beyond the
declarative value-source map; the ONE generic breach check lives in
analysis.evaluator. The historical ``...analysis.fetch.X`` import
path keeps working: this package re-exports the full former surface.
"""
from ._active import (
    fetch_active_codes,
    fetch_active_signals,
    fetch_regime_state,
    fetch_trend_state,
)
from ._bars import (
    fetch_daily_close_on,
    fetch_intraday_bar_on,
)
from ._sources import fetch_current_values, fetch_rsi_tail_bars
from ._values import (
    SIGNAL_VALUE_SOURCE,
    LiveValues,
    resolve_threshold,
    resolve_value,
    sub_type_window,
)

__all__ = [
    "fetch_active_codes",
    "fetch_active_signals",
    "fetch_regime_state",
    "fetch_trend_state",
    "fetch_daily_close_on",
    "fetch_intraday_bar_on",
    "fetch_current_values",
    "fetch_rsi_tail_bars",
    "SIGNAL_VALUE_SOURCE",
    "LiveValues",
    "resolve_threshold",
    "resolve_value",
    "sub_type_window",
]
