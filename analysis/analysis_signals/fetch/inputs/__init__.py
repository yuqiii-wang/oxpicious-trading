"""Input loaders of the in-engine signal-family computations
(analysis.analysis_signals.fetch.inputs) — one module per family,
mirroring engines/signal_families: _mov_rsi, _boll_std, _ma_cross,
_options_wall, _options_vix, _momentum, _rf_break, _rebound,
_macd (the classic MACD construction computed at the fetch
boundary), _crowdedness (the nightly market crowdedness
composite, data-driven universe) and _volatility (the realized-vol
panel, data-driven universe), plus _hedge_pairs (the portfolio
family's pair universe + the three-curve trio frames), over the
cross-family vocabulary in _shared (table names, the active-code /
first-date lookups, the market-breadth states, the shared
frame-finalize step). The daily news-text MARKET state lives in the
AMPLIFIER tier now (signal_amplification/news_anatomy — the news
states amplify existing signals, never create them; the news_wash
family was deleted 2026-11-22).

The signals tier computes its OWN buckets off the raw value data
since the forecast layer was removed (2026-10-05): per sec_type the
joined (code, date) frames of price + each family's value columns,
the active-code universe, per-code first-data dates (the live gate)
and each family's latest data date (the ROLLING LATEST snapshot
key). The long-format frames also carry the day-0 ohlcv-boost axis
columns (_common/labels — the ohlc_gaps pattern + the price_vs_amt
(vol_state, px_speed) cell + the turnover-climax amt grade,
LEFT JOINed per (code, date), missing → the AXIS_MISSING int8 code).

All NUMERIC columns are cast to native float8 in SQL (Decimal objects
would poison the cudf.pandas fast path) and the date columns arrive as
epoch float8, materialized via epoch_col_to_dt64 — the frames the
engines receive are cudf-representable from construction.
"""
from __future__ import annotations

from ._shared import (
    fetch_active_codes,
    fetch_first_dates,
    fetch_index_classes,
    fetch_market_breadth,
    fetch_options_universe,
    resolve_options_universe,
    resolve_sec_type_universe,
)
from ._mov_rsi import (
    fetch_latest_rsi_date,
    fetch_rsi_codes,
    fetch_rsi_inputs,
)
from ._boll_std import (
    fetch_boll_inputs,
    fetch_latest_boll_date,
)
from ._ma_cross import (
    fetch_latest_ma_date,
    fetch_ma_inputs,
)
from ._options_wall import (
    fetch_latest_options_wall_date,
    fetch_options_wall_inputs,
)
from ._options_vix import (
    fetch_latest_options_vix_date,
    fetch_options_vix_inputs,
)
from ._momentum import (
    fetch_momentum_codes,
    fetch_momentum_inputs,
)
from ._rf_break import (
    fetch_latest_rf_date,
    fetch_rf_inputs,
)
from ._rebound import (
    fetch_latest_rebound_date,
    fetch_rebound_inputs,
)
from ._macd import (
    fetch_latest_macd_date,
    fetch_macd_inputs,
)
from ._crowdedness import (
    fetch_crowdedness_inputs,
    fetch_latest_crowdedness_date,
    resolve_crowdedness_universe,
)
from ._volatility import (
    fetch_latest_volatility_date,
    fetch_volatility_inputs,
    resolve_volatility_universe,
)
from ._hedge_pairs import (
    fetch_hedge_pair_inputs,
    resolve_hedge_pair_universe,
)

__all__ = [
    "fetch_active_codes",
    "fetch_boll_inputs",
    "fetch_crowdedness_inputs",
    "fetch_first_dates",
    "fetch_hedge_pair_inputs",
    "fetch_index_classes",
    "fetch_latest_boll_date",
    "fetch_latest_crowdedness_date",
    "fetch_latest_macd_date",
    "fetch_latest_ma_date",
    "fetch_latest_options_vix_date",
    "fetch_latest_options_wall_date",
    "fetch_latest_rebound_date",
    "fetch_latest_rf_date",
    "fetch_latest_rsi_date",
    "fetch_latest_volatility_date",
    "fetch_ma_inputs",
    "fetch_market_breadth",
    "fetch_macd_inputs",
    "fetch_momentum_codes",
    "fetch_momentum_inputs",
    "fetch_options_universe",
    "fetch_options_vix_inputs",
    "fetch_options_wall_inputs",
    "fetch_rebound_inputs",
    "fetch_rf_inputs",
    "fetch_rsi_codes",
    "fetch_rsi_inputs",
    "fetch_volatility_inputs",
    "resolve_crowdedness_universe",
    "resolve_hedge_pair_universe",
    "resolve_sec_type_universe",
    "resolve_volatility_universe",
]
