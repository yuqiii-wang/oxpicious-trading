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

# Trading-amount + margin-buy source + estimated-close filter per
# sec_type (the margin_ratio family's inputs; the mov_*
# engines ignore the extra columns). Index turnover lives on the base
# table and indices have NO margin data (NULL rz_buy — the
# margin_ratio family never fires for 'index'); ETF / stock turnover
# and rz_buy live on their *_liquidity_margin tables (LEFT JOIN — a
# missing margin row NULLs trading_amount / rz_buy, which simply keeps
# that day out of the margin_ratio buckets). Estimated closes
# (synthetic flat closes on non-traded days) would pollute ret_1d /
# σ_ret — etf/index carry the is_close_estimated flag and those rows
# are filtered in SQL; stock basic stats have no such flag.
AMT_SOURCE = {
    "index": (
        "",
        "b.trading_amount",
        "NULL::float8",
        "AND COALESCE(b.is_close_estimated, FALSE) = FALSE",
    ),
    "etf": (
        "LEFT JOIN stats.etf_liquidity_margin m "
        "ON m.code = b.code AND m.date = b.date",
        "m.trading_amount",
        "m.rz_buy",
        "AND COALESCE(b.is_close_estimated, FALSE) = FALSE",
    ),
    "stock": (
        "LEFT JOIN stats.stock_liquidity_margin m "
        "ON m.code = b.code AND m.date = b.date",
        "m.trading_amount",
        "m.rz_buy",
        "",
    ),
}
