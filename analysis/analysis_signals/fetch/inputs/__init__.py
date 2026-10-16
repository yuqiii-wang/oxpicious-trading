"""Input loaders of the in-engine signal-family computations
(analysis.analysis_signals.fetch.inputs) — one module per family,
mirroring engines/signal_families: _mov_rsi, _boll_std, _ma_cross,
_options_wall, _options_vix, _momentum and _rf_break, over the
cross-family vocabulary in _shared (table names, the active-code /
first-date lookups, the market-breadth states, the shared
frame-finalize step).

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

__all__ = [
    "fetch_active_codes",
    "fetch_boll_inputs",
    "fetch_first_dates",
    "fetch_index_classes",
    "fetch_latest_boll_date",
    "fetch_latest_ma_date",
    "fetch_latest_options_vix_date",
    "fetch_latest_options_wall_date",
    "fetch_latest_rf_date",
    "fetch_latest_rsi_date",
    "fetch_ma_inputs",
    "fetch_market_breadth",
    "fetch_momentum_codes",
    "fetch_momentum_inputs",
    "fetch_options_universe",
    "fetch_options_vix_inputs",
    "fetch_options_wall_inputs",
    "fetch_rf_inputs",
    "fetch_rsi_codes",
    "fetch_rsi_inputs",
    "resolve_options_universe",
]
