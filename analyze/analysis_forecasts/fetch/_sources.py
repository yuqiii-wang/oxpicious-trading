"""Shared per-sec_type source tables and price expressions
(analyze.analysis_forecasts.fetch._sources).

The private SELECT-fragment constants the fetchers compose: the base
OHLCV table + price convention per sec_type (the parent mov_ave_spread
analysis's own convention — ETF uses the adjusted close when available)
and the trading-amount / margin-buy sources the margin_ratio
family consumes. All numeric columns are cast ``::float8`` at the SQL
source (asyncpg would otherwise hand back Decimal objects that poison
the cudf.pandas fast path); dates leave as ``extract(epoch ...)::float8``
and materialize to datetime64[us] via epoch_col_to_dt64 at the frame
boundary.
"""
from __future__ import annotations

# Base table + price expression per sec_type (same price convention as
# analyze.mov_ave_spread: ETF uses the adjusted close when available).
PRICE_SOURCE = {
    "index": (
        "stats.index_basic_stats b",
        "b.close",
    ),
    "etf": (
        "stats.etf_basic_stats b "
        "LEFT JOIN stats.etf_adjustment a "
        "ON a.code = b.code AND a.date = b.date",
        "COALESCE(a.adj_close, b.close)",
    ),
    "stock": (
        "stats.stock_basic_stats b",
        "b.close",
    ),
}

# Trading-amount + margin-buy source per sec_type (the margin_ratio
# family's inputs; the mov_* engines ignore the extra columns). Index
# turnover lives on the base table and indices have NO margin data
# (NULL rz_buy — the margin_ratio family never fires for 'index'); ETF /
# stock turnover and rz_buy live on their *_liquidity_margin tables (LEFT
# JOIN — a missing margin row NULLs trading_amount / rz_buy, which simply
# keeps that day out of the margin_ratio buckets). Close-less rows (the
# former estimated-close rows, nulled by the 2026-09-30 is_close_estimated
# retirement) are excluded by the fetchers' close IS NOT NULL predicate.
# The OHLC-estimated exclusion (2026-09-26, with the forecast OHLC
# boost): rows whose open/high/low are SYNTHETIC (is_ohl_estimated —
# the index/ETF OHLC estimated from constituents on non-traded days)
# must never feed an OHLC-boost rule — the day's gap_open would be a
# construction artifact. The forecast inputs fetch drops those rows
# outright. All three base tables carry the flag.
OHL_EST_FILTER = {
    "index": " AND COALESCE(b.is_ohl_estimated, FALSE) = FALSE",
    "etf": " AND COALESCE(b.is_ohl_estimated, FALSE) = FALSE",
    "stock": " AND COALESCE(b.is_ohl_estimated, FALSE) = FALSE",
}

AMT_SOURCE = {
    "index": (
        "",
        "b.trading_amount",
        "NULL::float8",
        "",
    ),
    "etf": (
        "LEFT JOIN stats.etf_liquidity_margin m "
        "ON m.code = b.code AND m.date = b.date",
        "m.trading_amount",
        "m.rz_buy",
        "",
    ),
    "stock": (
        "LEFT JOIN stats.stock_liquidity_margin m "
        "ON m.code = b.code AND m.date = b.date",
        "m.trading_amount",
        "m.rz_buy",
        "",
    ),
}
