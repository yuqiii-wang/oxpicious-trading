"""DB fetchers for analysis.analysis_signals.

Everything here is DATA ACCESS — every read lands in a cudf.pandas
DataFrame (the repo's cudf contract: ``::float8`` numeric casts at the
SQL source, epoch float8 dates materialized to datetime64[us] in ONE
host pass via epoch_col_to_dt64, column-materialized construction so
no numeric path ever sees object dtype). The engines consume the
frames; no dict-row processing anywhere.

Since the forecast layer's removal (2026-10-05) the fetchers load the
RAW inputs the in-engine rsi-pct bucket computation reads — inputs
(the active universe, first dates, the latest RSI data date, the
joined price + RSI frame) and regimes (the daily market-regime
labels); the retired bucket/results registry reads went with the
forecast tables.
"""
from __future__ import annotations

from .inputs import (
    fetch_active_codes,
    fetch_boll_inputs,
    fetch_first_dates,
    fetch_index_classes,
    fetch_latest_boll_date,
    fetch_latest_rsi_date,
    fetch_rsi_codes,
    fetch_rsi_inputs,
)
from .regimes import fetch_market_regimes

__all__ = [
    "fetch_active_codes",
    "fetch_boll_inputs",
    "fetch_first_dates",
    "fetch_index_classes",
    "fetch_latest_boll_date",
    "fetch_latest_rsi_date",
    "fetch_rsi_codes",
    "fetch_rsi_inputs",
    "fetch_market_regimes",
]
