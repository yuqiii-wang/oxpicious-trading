"""Shared per-sec_type source tables and price expressions
(analysis.mov_ave_spread.config.price_sources).

The private SELECT-fragment constants the fetchers compose: the base
OHLCV table + price convention per sec_type (the parent mov_ave_spread
analysis's own convention — ETF uses the adjusted close when
available), the trading-amount / margin-buy sources and the
OHLC-estimated exclusion. Formerly analysis.analysis_forecasts.fetch.
_sources (retired with the forecast layer 2026-10-05); re-homed to the
parent analysis that owns the convention.

All numeric columns are cast ``::float8`` at the SQL source (asyncpg
would otherwise hand back Decimal objects that poison the cudf.pandas
fast path); dates leave as ``extract(epoch ...)::float8`` and
materialize to datetime64[us] via epoch_col_to_dt64 at the frame
boundary.
"""
from __future__ import annotations

# Base table + price expression per sec_type (ETF uses the adjusted
# close when available). Industry composes a rename subquery over the
# pool-'all' composite slice — industry_basic_stats carries
# (industry_id, mean_*) columns while every consumer binds ``b.code`` /
# ``b.close`` / ``b.is_ohl_estimated`` against the aliased base (the
# plain projection inlines; PG pushes the code/close predicates in).
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
    "industry": (
        "(SELECT industry_id AS code, date, "
        " mean_close AS close, mean_open AS open, mean_high AS high, "
        " mean_low AS low, total_trading_amount AS trading_amount, "
        " FALSE AS is_ohl_estimated "
        " FROM stats.industry_basic_stats WHERE pool_size = 3) b",
        "b.close",
    ),
}

# Trading-amount + margin-buy source per sec_type. Index turnover lives
# on the base table and indices have NO margin data (NULL rz_buy);
# ETF / stock turnover and rz_buy live on their *_liquidity_margin
# tables (LEFT JOIN — a missing margin row NULLs trading_amount /
# rz_buy). Industry mirrors index: the composite turnover is the base
# subquery's trading_amount column (the slice's union-stock sum), and
# there is no margin data. Close-less rows are excluded by the
# fetchers' close IS NOT NULL predicate.
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
    "industry": (
        "",
        "b.trading_amount",
        "NULL::float8",
        "",
    ),
}

# The OHLC-estimated exclusion: rows whose open/high/low are SYNTHETIC
# (is_ohl_estimated — the index/ETF OHLC estimated from constituents on
# non-traded days) must never feed an OHLC-derived rule. All base
# tables carry the flag; the industry base subquery projects a constant
# FALSE (the composite mean_* columns carry no estimated rows).
OHL_EST_FILTER = {
    "index": " AND COALESCE(b.is_ohl_estimated, FALSE) = FALSE",
    "etf": " AND COALESCE(b.is_ohl_estimated, FALSE) = FALSE",
    "stock": " AND COALESCE(b.is_ohl_estimated, FALSE) = FALSE",
    "industry": " AND COALESCE(b.is_ohl_estimated, FALSE) = FALSE",
}
