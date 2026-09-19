"""DB fetchers + value resolution for the live breach check
(live.live_signals.analysis.fetch — one file per concern):

  _values    LiveValues / the SIGNAL_VALUE_SOURCE map / the sub_type
             parsers / resolve_value / resolve_threshold — the
             value-threshold SPACE (no SQL).
  _sources   the code's CURRENT values (latest RSI row / spreads /
             px_t / recomputed margin z / ma+σ), each source fetched
             at most once per check, as-of bounded.
  _bars      the as-of bar resolution (last intraday bar ON D, else
             the official daily close — the on-demand --date mode).
  _active    the ACTIVE threshold set (analysis_signals.
             signal_strategies is_active rows) + the market-hype
             probe.

Everything here is DATA ACCESS — no decision logic beyond the
declarative value-source map; the ONE generic breach check lives in
analysis.evaluator. The historical ``...analysis.fetch.X`` import
path keeps working: this package re-exports the full former surface.
"""
from ._active import (
    fetch_active_codes,
    fetch_active_signals,
    fetch_is_market_hyped,
)
from ._bars import (
    fetch_daily_close_on,
    fetch_intraday_bar_on,
)
from ._sources import fetch_current_values
from ._values import (
    SIGNAL_VALUE_SOURCE,
    LiveValues,
    resolve_threshold,
    resolve_value,
    sub_type_fragment,
    sub_type_k,
    sub_type_window,
)

__all__ = [
    "fetch_active_codes",
    "fetch_active_signals",
    "fetch_is_market_hyped",
    "fetch_daily_close_on",
    "fetch_intraday_bar_on",
    "fetch_current_values",
    "SIGNAL_VALUE_SOURCE",
    "LiveValues",
    "resolve_threshold",
    "resolve_value",
    "sub_type_fragment",
    "sub_type_k",
    "sub_type_window",
]
