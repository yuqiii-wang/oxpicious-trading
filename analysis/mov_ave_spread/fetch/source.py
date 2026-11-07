"""Async DB fetch primitives for analysis.mov_ave_spread.

Loads per-(code, date) price + MA series from the stats schema, scoped to
the active universe (codes with recent identity-table data).

Incremental mode: when ``target_dates`` is supplied, only rows whose date
is in ``target_dates`` are RETURNED for insertion. However, slope and
curvature computations need 2 prior rows per code, so the SQL also fetches
up to 2 preceding trading-day rows per code as lookback context. These
context rows are used for the ``diff()`` computation but filtered out
before the DataFrame is returned, so only ``target_dates`` rows survive
to the detail table.
"""
from __future__ import annotations

from typing import Optional, Set
import datetime

import numpy as np
import pandas as pd

from _common.build_commons import (
    fetch_codes_with_recent_data_async,
    RECENT_TRADING_DAYS,
    recent_trading_day_cutoff,
    rec_cols,
)
from _common.df_utils import (
    compute_emas,
    compute_moving_averages,
    epoch_col_to_dt64,
    to_dt64,
)
from _common.importance import order_codes_or_sorted_async
from _common.labels import SEC_TYPE_CODE
from _common.sql_views import render_sql_view

from analysis.mov_ave_spread.config import (
    MA_WINDOWS,
    SEC_TYPE_IDENTITY_TABLE,
    TECH_STATS_EMA_COLUMNS,
)
from analysis.mov_ave_spread._common import (
    compute_slopes_curvatures,
    compute_ema_slopes_curvatures,
    compute_rolling_stds,
    compute_trading_amt_mas,
    compute_trading_amt_market_share_mas,
    compute_trading_amt_ma_slopes,
    compute_trading_amt_market_share_vs_mas,
)

import logging
logger = logging.getLogger(__name__)


# Number of prior trading-day rows per code needed as lookback context for
# slope (1st derivative, needs 1 prior row) and curvature (2nd derivative,
# needs 2 prior rows). Used only in incremental mode.
_SLOPE_LOOKBACK_ROWS = 2


# Source SELECT column order shared by the etf / index / stock branches —
# their MA/EMA columns arrive pre-computed from the stats.*_tech_stats
# tables.
_TECH_SOURCE_COLUMNS = [
    "code", "date", "price", "open", "low", "high",
    "ma5", "ma20", "ma60", "ma120", "ma255",
    "ema6", "ema12", "ema26",
    "trading_amount",
    "is_ohl_estimated",
]

# Industry branch base columns (no tech_stats table — the MA/EMA columns
# are computed below the load with the builds' own helpers).
_INDUSTRY_SOURCE_COLUMNS = [
    "code", "date", "price", "open", "low", "high",
    "trading_amount",
    "is_ohl_estimated",
]


# Per-sec_type source-fetch views (database/sql_views/analysis/
# mov_ave_spread/fetch/source/<sec_type>_source.sql) — each scopes to a
# caller-supplied code list through its single ``codes`` slot
# (``WHERE i.code = ANY($1::text[])``).
_SOURCE_FETCH_VIEWS = {
    "etf": "analysis/mov_ave_spread/fetch/source/etf_source",
    "index": "analysis/mov_ave_spread/fetch/source/index_source",
    "stock": "analysis/mov_ave_spread/fetch/source/stock_source",
    "industry": "analysis/mov_ave_spread/fetch/source/industry_source",
}


def _source_fetch_view(sec_type: str) -> str:
    """The sql-view name of the per-(code, date) source-fetch SELECT for
    the given sec_type (rendered by ``fetch_source_data``; each view is
    scoped to a caller-supplied code list via its ``codes`` slot).

    ETFs JOIN etf_identity + etf_basic_stats + LEFT JOIN etf_adjustment
    (for adj_close) + JOIN etf_tech_stats + JOIN etf_liquidity_margin
    (for trading_amount — used to compute trading_amt_ma{5,20,60,120,255}).

    Indices JOIN index_identity + index_basic_stats (close + trading_amount)
    + JOIN index_tech_stats (no adjustment table — indices have no
    corporate actions).

    Stocks JOIN stock_identity + stock_basic_stats + JOIN stock_tech_stats
    + JOIN stock_liquidity_margin (for trading_amount) (no adjustment
    table — stocks use raw close as price).

    Industry (sec_type='industry') JOINs the stats.industry_identity view
    (code = industry_id, name = industry_label) + industry_basic_stats'
    pool-'all' slice: price/open/low/high are the REBASED-TO-100 composite
    OHLC (mean_close / mean_open / mean_low / mean_high), trading_amount
    is the slice's union-stock turnover. Industry has NO *_tech_stats
    table — the MA/EMA columns are computed by fetch_source_data after
    the load (same compute_moving_averages / compute_emas conventions
    the builds use), so its view returns the 8 base columns only.
    """
    view = _SOURCE_FETCH_VIEWS.get(sec_type)
    if view is None:
        raise ValueError(f"Unknown sec_type: {sec_type!r}")
    return view


# Market-share denominator SELECT — sql view
# analysis/mov_ave_spread/fetch/source/market_share_denominator
# (per-date SUM of stats.exchange_trading_amt.total_trading_amount
# across exchanges whose stats.sec_classification.is_primary_exchange =
# TRUE; the primary-exchange set is derived at query time so flag
# changes are picked up without an analysis-script redeploy).
_MARKET_SHARE_DENOMINATOR_VIEW = (
    "analysis/mov_ave_spread/fetch/source/market_share_denominator"
)


async def _fetch_market_share_denominator(conn) -> dict:
    """Fetch the per-date market-share denominator from
    stats.exchange_trading_amt.

    The denominator for a given date = SUM(total_trading_amount) across
    exchanges whose stats.sec_classification.is_primary_exchange = TRUE
    (currently SZ via 399001 + SS via 000001; BJ will be included once a
    representative BJ index is added to exchange_trading_amt).

    Returns a {date: float} dict. Dates with no primary-exchange data
    in exchange_trading_amt are absent from the dict, so callers using
    Series.map will get NaN for those dates (the market-share MA helper
    nulls those rows).
    """
    rows = await conn.fetch(
        render_sql_view(_MARKET_SHARE_DENOMINATOR_VIEW).sql)
    out = {}
    for r in rows:
        if r["date"] is not None and r["denominator"] is not None:
            out[r["date"]] = float(r["denominator"])
    return out


async def fetch_source_data(
    conn,
    sec_type: str,
    *,
    target_dates: Optional[Set[datetime.date]] = None,
    code_filter: Optional[str] = None,
) -> pd.DataFrame:
    """Fetch per-(code, date) price + MAs from the stats schema for the
    given sec_type ('etf', 'index', 'stock', or 'industry'), then compute
    per-(code) slope and curvature for each MA window.

    Pre-filter: only codes with at least one identity-table row in the last
    RECENT_TRADING_DAYS trading days are loaded. A code with no recent data
    (delisted / suspended / never-traded) is excluded from the analysis
    universe entirely — its full history is skipped — so the detail table
    only carries the active universe.

    Incremental mode (``target_dates`` is a non-empty set):
        Only rows whose date is in ``target_dates`` are returned for
        insertion. However, slope/curvature require up to 2 prior rows per
        code as context — those lookback rows are fetched from the source
        tables (dates < min(target_dates) per code) and used for the
        ``diff()`` computation, then filtered out before return. This
        ensures slope/curvature are correctly computed for the first
        target date of each code even in incremental mode.

        Implementation: the full per-code history is fetched (same SQL as
        full mode), slopes/curvatures are computed over the full history,
        then the DataFrame is filtered to ``target_dates`` only. This is
        correct and simple; the per-code history is already in the DB and
        the fetch is a single indexed query.

    Full mode (``target_dates`` is None, or ``--force`` rebuild):
        All rows for active codes are returned (no date filtering).

    Returns a DataFrame with columns:
        sec_type, code, date, price, ma5, ma20, ma60, ma120, ma255,
        ema6, ema12, ema26,
        trading_amount,
        is_ohl_estimated,
        price_slope, price_curvature,
        ma5_slope, ma20_slope, ma60_slope, ma120_slope, ma255_slope,
        ma5_curvature, ma20_curvature,
        ema6_slope, ema12_slope,
        ema6_curvature, ema12_curvature,
        std_5days, std_12days, std_26days, std_20days, std_60days,
        std_120days, std_255days,
        trading_amt_ma5, trading_amt_ma20, trading_amt_ma60,
        trading_amt_ma120, trading_amt_ma255,
        trading_amt_market_share_ma5, trading_amt_market_share_ma20,
        trading_amt_market_share_ma60, trading_amt_market_share_ma120,
        trading_amt_market_share_ma255,
        trading_amt_ma5_slope, trading_amt_ma20_slope,
        trading_amt_ma60_slope, trading_amt_ma120_slope,
        trading_amt_ma255_slope,
        trading_amt_market_share_vs_ma5, trading_amt_market_share_vs_ma20,
        trading_amt_market_share_vs_ma60, trading_amt_market_share_vs_ma120,
        trading_amt_market_share_vs_ma255

    NOTE: ma{60,120,255}_slope are carried for the trading_amt_ratios
    step's denominators (TRADING_AMT_PRICE_SLOPE_SOURCE_COLUMNS) but are
    NOT persisted to the detail table. The persisted derivatives are
    price + ma{5,20} slope/curvature (detail table) and ema{6,12}
    slope/curvature (EMA detail table) — each a direct diff of the
    series line (config.MA_DERIV_WINDOWS / EMA_DERIV_WINDOWS).

    Uses INNER JOINs on both basic_stats and tech_stats so the resulting
    rows satisfy the detail table's data-integrity expectation (every row
    has both OHLCV and MA source data).
    """
    # ---- Pre-filter: keep only codes with data in the recent trading window.
    identity_table = SEC_TYPE_IDENTITY_TABLE[sec_type]
    cutoff = recent_trading_day_cutoff(RECENT_TRADING_DAYS)
    active_codes = await fetch_codes_with_recent_data_async(
        conn, identity_table, n_trading_days=RECENT_TRADING_DAYS,
    )
    logger.info(f"      pre-filter: {len(active_codes):,} {sec_type} codes have "
          f"data in the last {RECENT_TRADING_DAYS} trading days "
          f"(cutoff={cutoff.isoformat()})")
    if code_filter is not None:
        # Single-code mode (--code): bypass the active-universe pre-filter
        # and load exactly this code's full history. Empty df when the code
        # has no source data for this sec_type (caller skips it).
        active_codes = {code_filter}
        logger.info(f"      code filter: {code_filter} (single-code mode)")
    if not active_codes:
        return pd.DataFrame(columns=["sec_type", "code", "date", "price",
                                     "open", "low", "high",
                                     "ma5", "ma20", "ma60", "ma120", "ma255",
                                     "ema6", "ema12", "ema26",
                                     "trading_amount",
                                     "is_ohl_estimated",
                                     "price_slope", "price_curvature",
                                     "ma5_slope", "ma20_slope", "ma60_slope",
                                     "ma120_slope", "ma255_slope",
                                     "ma5_curvature", "ma20_curvature",
                                     "ema6_slope", "ema12_slope",
                                     "ema6_curvature", "ema12_curvature",
                                     "std_5days", "std_12days", "std_26days",
                                     "std_20days", "std_60days",
                                     "std_120days", "std_255days",
                                     "trading_amt_ma5", "trading_amt_ma20",
                                     "trading_amt_ma60", "trading_amt_ma120",
                                     "trading_amt_ma255",
                                     "trading_amt_market_share_ma5",
                                     "trading_amt_market_share_ma20",
                                     "trading_amt_market_share_ma60",
                                     "trading_amt_market_share_ma120",
                                     "trading_amt_market_share_ma255",
                                     "trading_amt_ma5_slope",
                                     "trading_amt_ma20_slope",
                                     "trading_amt_ma60_slope",
                                     "trading_amt_ma120_slope",
                                     "trading_amt_ma255_slope",
                                     "trading_amt_market_share_vs_ma5",
                                     "trading_amt_market_share_vs_ma20",
                                     "trading_amt_market_share_vs_ma60",
                                     "trading_amt_market_share_vs_ma120",
                                     "trading_amt_market_share_vs_ma255"])

    # Fetch the per-date market-share denominator (SUM of primary-exchange
    # representative-index trading amounts from stats.exchange_trading_amt).
    # Same dict for all sec_types — denominator is market-wide, not per-code.
    denominator_by_date = await _fetch_market_share_denominator(conn)
    logger.info(f"      market-share denominator: {len(denominator_by_date):,} "
          f"dates")

    # Importance rank 0 -> 9 (stats.sec_importance_ranks, monthly build):
    # bind the universe most-important-first (whole-frame compute is
    # order-invariant; this drives logs + any future batching/slicing).
    view = render_sql_view(
        _source_fetch_view(sec_type),
        codes=await order_codes_or_sorted_async(conn, active_codes),
    )
    rows = await conn.fetch(view.sql, *view.params)
    if not rows:
        return pd.DataFrame(columns=["sec_type", "code", "date", "price",
                                     "open", "low", "high",
                                     "ma5", "ma20", "ma60", "ma120", "ma255",
                                     "ema6", "ema12", "ema26",
                                     "trading_amount",
                                     "is_ohl_estimated",
                                     "price_slope", "price_curvature",
                                     "ma5_slope", "ma20_slope", "ma60_slope",
                                     "ma120_slope", "ma255_slope",
                                     "ma5_curvature", "ma20_curvature",
                                     "ema6_slope", "ema12_slope",
                                     "ema6_curvature", "ema12_curvature",
                                     "std_5days", "std_12days", "std_26days",
                                     "std_20days", "std_60days",
                                     "std_120days", "std_255days",
                                     "trading_amt_ma5", "trading_amt_ma20",
                                     "trading_amt_ma60", "trading_amt_ma120",
                                     "trading_amt_ma255",
                                     "trading_amt_market_share_ma5",
                                     "trading_amt_market_share_ma20",
                                     "trading_amt_market_share_ma60",
                                     "trading_amt_market_share_ma120",
                                     "trading_amt_market_share_ma255",
                                     "trading_amt_ma5_slope",
                                     "trading_amt_ma20_slope",
                                     "trading_amt_ma60_slope",
                                     "trading_amt_ma120_slope",
                                     "trading_amt_ma255_slope",
                                     "trading_amt_market_share_vs_ma5",
                                     "trading_amt_market_share_vs_ma20",
                                     "trading_amt_market_share_vs_ma60",
                                     "trading_amt_market_share_vs_ma120",
                                     "trading_amt_market_share_vs_ma255"])
    # One positional-unpack pass over the asyncpg records (repo
    # convention) — the per-row ``dict(r)`` construction is O(rows)
    # python and per-row proxy dispatch under cudf.pandas. The etf /
    # index / stock branches share one 18-column SELECT order; industry
    # loads its 8-column base set (no tech_stats) and derives the
    # MA/EMA columns below.
    source_columns = (
        _INDUSTRY_SOURCE_COLUMNS if sec_type == "industry"
        else _TECH_SOURCE_COLUMNS
    )
    df = pd.DataFrame(rec_cols(rows), columns=source_columns)
    # df is always constructed with the explicit column list above, so
    # "code" exists even when rows is empty (avoids the proxied
    # Index.__contains__ fallback under cudf.pandas).
    n_loaded_codes = df["code"].nunique()
    if n_loaded_codes < len(active_codes):
        # Some active codes had identity rows but no joined basic_stats /
        # tech_stats rows — they are dropped by the INNER JOINs.
        logger.info(f"      note: {len(active_codes) - n_loaded_codes:,} {sec_type} "
              f"codes had recent identity data but no joined basic/tech stats "
              f"rows -> dropped by INNER JOIN")
    # Tag every row with its sec_type so downstream detail/summary rows
    # carry the discriminant column required by the new schema. The
    # column rides ALL compute as its int16 frame code (the label-code
    # contract: string columns through cudf sort/merge/concat chains
    # fault at host transfer on cuDF 26.08); the TEXT value is
    # re-materialized at each write boundary (steps.materialize_sec_type_text).
    df["sec_type"] = np.int16(SEC_TYPE_CODE[sec_type])
    # Dates stay datetime64[us] through ALL compute (B-A2 convention):
    # the date column arrives as NATIVE float8 (extract(epoch) in SQL) and
    # epoch_col_to_dt64 materializes datetime64[us] in ONE host pass — an
    # object py-date column would poison every downstream GPU op (each
    # getitem/sort/groupby falls back with "Cannot convert a date of
    # object type" — 30k+ fallbacks/run at stock scale). python dates are
    # materialized only inside sanitize_for_db_insert at the write
    # boundary (datetime64[us] → datetime, NaT → None — asyncpg accepts
    # datetime for DATE columns, see _common.db_commons._to_date).
    df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
    # Numeric columns arrive as native float64 via the ::float8 SQL casts
    # (asyncpg NUMERIC → Python Decimal → object column would make every
    # pd.to_numeric fall back per column) — no post-parse conversion.
    # ---- Industry-only: derive the tech columns the other sec_types load
    # from their stats.*_tech_stats tables. The composite industry series
    # (rebased-to-100 mean OHLC) has no build-side tech_stats, so the
    # MA/EMA columns are computed here with the SAME helpers + conventions
    # the builds use for tech_stats: compute_moving_averages
    # (min_periods=1 partial windows, 6dp round) and compute_emas
    # (adjust=False, 6dp round). Whole-frame grouped passes (GPU under
    # cudf.pandas); the universe is ~106 industries — small.
    if sec_type == "industry":
        df = compute_moving_averages(
            df, group_key="code", value_col="price",
            windows=list(MA_WINDOWS), add_ratio=False,
        )
        df = compute_emas(
            df, group_key="code", value_col="price",
            spans=list(TECH_STATS_EMA_COLUMNS.keys()),
        )
        logger.info(f"      industry tech columns derived in-pipeline: "
              f"ma{list(MA_WINDOWS)} + ema{list(TECH_STATS_EMA_COLUMNS.keys())}")
    # Compute slope (1st derivative) and curvature (2nd derivative) per code.
    # This is computed over the FULL per-code history so the diff() values
    # are correct for the first target date of each code (incremental mode).
    # Persisted: price + ma{5,20} slope/curvature; the ma{60,120,255} slopes
    # are in-memory trading_amt_ratios denominators.
    df = compute_slopes_curvatures(df)
    # Compute EMA slope + curvature for the persisted 6/20-day windows
    # (direct diffs of the EMA lines). Computed over the FULL per-code
    # history for the same incremental-mode reasoning. Consumed by the
    # internal EMA step (ema.py).
    df = compute_ema_slopes_curvatures(df)
    # Compute rolling population σ (Bollinger band widths) per code over the
    # FULL per-code history. Same incremental-mode reasoning as slopes: the
    # rolling window needs up to 255 prior rows to populate σ_255days, so we
    # compute over the full history then filter to target_dates below.
    df = compute_rolling_stds(df)
    # Compute trading-amount moving averages (trading_amt_ma{5,20,60,120,
    # 255}) per code over the FULL per-code history. Same incremental-mode
    # reasoning as slopes/rolling-stds: needs up to 255 prior rows to
    # populate trading_amt_ma255.
    df = compute_trading_amt_mas(df)
    # Compute trading-amount MARKET-SHARE moving averages
    # (trading_amt_market_share_ma{5,20,60,120,255}) per code over the FULL
    # per-code history. Same incremental-mode reasoning as trading_amt_mas:
    # needs up to 255 prior rows.
    df = compute_trading_amt_market_share_mas(df, denominator_by_date)
    # Compute trading-amount MA SLOPES (trading_amt_ma{5,20,60,120,255}_slope)
    # — fractional daily change (ma[t]-ma[t-1])/ma[t-1] per code. Must run
    # AFTER compute_trading_amt_mas (reads the ma columns). Same incremental-
    # mode reasoning: needs 1 prior row (not 255), but computed over the
    # full history for consistency.
    df = compute_trading_amt_ma_slopes(df)
    # Compute trading-amount MARKET-SHARE-vs-MA gaps
    # (trading_amt_market_share_vs_ma{5,20,60,120,255}) — signed fractional
    # ratio (market_share - market_share_ma{W}) / market_share_ma{W} per code.
    # Must run AFTER compute_trading_amt_market_share_mas (reads the ma
    # columns). Same incremental-mode reasoning: no extra lookback rows
    # needed (gap is computed from same-row values), but computed over the
    # full history for consistency.
    df = compute_trading_amt_market_share_vs_mas(df, denominator_by_date)

    # ---- Incremental filter: keep only target_dates rows ----------------
    if target_dates is not None and len(target_dates) > 0:
        n_before = len(df)
        # datetime64 ndarray comparison — isin with a python-date SET
        # falls back (values won't convert); with a datetime64 ndarray it
        # hits the GPU hash join.
        td64 = to_dt64(sorted(target_dates))
        df = df[df["date"].isin(td64)].reset_index(drop=True)
        logger.info(f"      incremental filter: {len(df):,} of {n_before:,} rows "
              f"are in target_dates (slope/curv context rows dropped)")

    # Reorder columns for readability.
    df = df[["sec_type", "code", "date", "price", "open", "low", "high",
             "ma5", "ma20", "ma60", "ma120", "ma255",
             "ema6", "ema12", "ema26",
             "trading_amount",
             "is_ohl_estimated",
             "price_slope", "price_curvature",
             "ma5_slope", "ma20_slope", "ma60_slope", "ma120_slope", "ma255_slope",
             "ma5_curvature", "ma20_curvature",
             "ema6_slope", "ema12_slope",
             "ema6_curvature", "ema12_curvature",
             "std_5days", "std_12days", "std_26days", "std_20days", "std_60days", "std_120days", "std_255days",
             "trading_amt_ma5", "trading_amt_ma20", "trading_amt_ma60",
             "trading_amt_ma120", "trading_amt_ma255",
             "trading_amt_market_share_ma5", "trading_amt_market_share_ma20",
             "trading_amt_market_share_ma60", "trading_amt_market_share_ma120",
             "trading_amt_market_share_ma255",
             "trading_amt_ma5_slope", "trading_amt_ma20_slope",
             "trading_amt_ma60_slope", "trading_amt_ma120_slope",
             "trading_amt_ma255_slope",
             "trading_amt_market_share_vs_ma5",
             "trading_amt_market_share_vs_ma20",
             "trading_amt_market_share_vs_ma60",
             "trading_amt_market_share_vs_ma120",
             "trading_amt_market_share_vs_ma255"]]
    return df
