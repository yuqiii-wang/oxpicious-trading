"""Pure helpers for analyze.mov_ave_spread.

No DB / IO dependencies - safe to unit-test in isolation.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import (
    grouped_diff,
    grouped_rolling_agg,
    host_array,
    safe_columns,
)
from analyze.mov_ave_spread.config import (
    EMA_WINDOWS,
    MA_WINDOWS,
    NUMERIC_MAX_ABS,
    NUMERIC_WIDE_MAX_ABS,
    TRADING_AMT_MA_COLUMNS,
    TRADING_AMT_MARKET_SHARE_MA_COLUMNS,
    TRADING_AMT_MA_SLOPE_COLUMNS,
    TRADING_AMT_MARKET_SHARE_VS_MA_COLUMNS,
)


def null_if_overflow(
    series: pd.Series,
    *,
    max_abs: float = NUMERIC_MAX_ABS,
    scale: int = 6,
) -> np.ndarray:
    """Return ``series`` values with overflow-risky entries nulled to NaN.

    Default: NUMERIC(10,6) — values with absolute value < 10^4 after
    rounding to 6 decimal places. Override ``max_abs``/``scale`` for
    wider columns (e.g. NUMERIC(16,4) → max_abs=10^12, scale=4).

    This helper nulls any value whose rounded absolute value >= max_abs,
    mirroring PostgreSQL's overflow check so the bulk upsert never fails.
    NaN/inf are also nulled.

    This is the safety net for:
      - slope/curvature columns (raw differences) — high-priced ETFs/indices
        can produce single-day MA changes exceeding 10000 at corporate-action
        or source-data-unit boundaries.
      - gap columns (ratios) — catches any near-zero-denominator ratio that
        slips through gap_col's zero/near-zero check.
      - trading_amt_ma* columns (NUMERIC(24,4)) — pass max_abs=NUMERIC_WIDE_
        MAX_ABS, scale=4. Daily trading_amount for a high-turnover index can
        reach ~10^13 yuan; NUMERIC(10,6) would overflow.

    Host-pure (B-A3): data leaves the proxy Series ONCE via ``to_numpy``
    + :func:`_common.df_utils.host_array`; the mask (NaN / inf /
    |rounded| >= max_abs) is computed in raw host numpy. Returns a plain
    float64 ndarray — assign directly with ``df[col] = result`` (the
    former proxied to_numeric/isna/abs/round/where chain cost ~5 cudf
    fallbacks per column).
    """
    arr = _to_float64_host(series)
    bad = ~np.isfinite(arr) | (np.abs(np.round(arr, scale)) >= max_abs)
    out = arr.copy()
    out[bad] = np.nan
    return out


def _to_float64_host(series: pd.Series) -> np.ndarray:
    """Series -> RAW host float64 ndarray, coercing non-numeric junk.

    Fast path: float columns skip ``pd.to_numeric`` entirely (one fewer
    proxied dispatch per column — the columns reaching the overflow
    guards are computed floats in practice). ``na_value=np.nan`` keeps
    nullable/missing columns on the cudf fast path — a plain
    ``to_numpy(dtype=np.float64)`` raises ValueError on missing values
    and falls back per column (~630 fallbacks per stock-scale run).
    Object/other dtypes take the to_numeric-coerce path.
    """
    try:
        return host_array(
            series.to_numpy(dtype=np.float64, na_value=np.nan)
        )
    except (TypeError, ValueError):
        return host_array(
            pd.to_numeric(series, errors="coerce").to_numpy(
                dtype=np.float64, na_value=np.nan
            )
        )


def null_if_overflow_counted(
    series: pd.Series,
    *,
    max_abs: float = NUMERIC_MAX_ABS,
    scale: int = 6,
) -> tuple[np.ndarray, int]:
    """:func:`null_if_overflow` + the number of values newly nulled.

    The count is derived from the same host mask (positions that were
    not NaN before and are NaN after — inf / overflow / coercion) at
    zero extra proxy dispatch, replacing the former per-column
    ``isna().sum()`` before/after proxied pairs.
    """
    arr = _to_float64_host(series)
    was_nan = np.isnan(arr)
    bad = ~np.isfinite(arr) | (np.abs(np.round(arr, scale)) >= max_abs)
    out = arr.copy()
    out[bad] = np.nan
    return out, int((bad & ~was_nan).sum())


def safe_ratio(num, den):
    """(num - den) / den — returns None for NaN/None/non-positive denominator.

    Mirrors the SQL NULL-on-bad-input semantics so that detail rows always
    match what a pure-SQL computation would produce. Also nulls results
    whose absolute value would overflow NUMERIC(10,6) (|result| >= 10000),
    which can occur when ``den`` is denormalized (near-zero but non-zero).
    """
    if num is None or den is None:
        return None
    try:
        n = float(num)
        d = float(den)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(n) or not np.isfinite(d) or d == 0:
        return None
    r = (n - d) / d
    if not np.isfinite(r) or abs(r) >= NUMERIC_MAX_ABS:
        return None
    return r


def compute_slopes_curvatures(df: pd.DataFrame) -> pd.DataFrame:
    """Add 1st-derivative (slope) and 2nd-derivative (curvature) columns for
    price and each MA window, computed per (sec_type, code) ordered by date.

    slope[t]      = value[t] - value[t-1]   (NULL on first date of each code)
    curvature[t]  = slope[t] - slope[t-1]   (NULL on first two dates of each code)

    Adds columns price_slope / price_curvature (from ``price``) and
    ma{W}_slope / ma{W}_curvature for W in MA_WINDOWS (from ``ma{W}``).

    GPU acceleration: uses the shared ``grouped_diff`` helper, which
    routes to cuDF when the row count exceeds the ``groupby_diff``
    breakeven (~320K rows conservative). Two batched calls cover all
    12 diff() operations: one for the 6 slopes, one for the 6
    curvatures (diff-of-diff). Each batch runs on a single cuDF
    transfer of only the needed columns (group_keys + input cols),
    avoiding the cost of transferring the full wide source frame.
    """
    df = df.sort_values(["sec_type", "code", "date"]).reset_index(drop=True)
    grp_keys = ["sec_type", "code"]

    # Slopes: 1st derivative of price + each MA window (6 columns).
    slope_cols = ["price"] + [f"ma{w}" for w in MA_WINDOWS]
    slope_out = ["price_slope"] + [f"ma{w}_slope" for w in MA_WINDOWS]
    grouped_diff(
        df, grp_keys,
        cols=slope_cols, out_names=slope_out,
        sort=False,  # df already sorted above
    )

    # Curvatures: 2nd derivative = diff of slope (6 columns).
    curv_out = ["price_curvature"] + [f"ma{w}_curvature" for w in MA_WINDOWS]
    grouped_diff(
        df, grp_keys,
        cols=slope_out, out_names=curv_out,
        sort=False,
    )

    return df


def compute_ema_slopes_curvatures(df: pd.DataFrame) -> pd.DataFrame:
    """Add 1st-derivative (slope) and 2nd-derivative (curvature) columns
    for each EMA window, computed per (sec_type, code) ordered by date.

    slope[t]      = ema{W}[t] - ema{W}[t-1]   (NULL on first date of each code)
    curvature[t]  = slope[t] - slope[t-1]       (NULL on first two dates)

    Adds columns ema{W}_slope / ema{W}_curvature for W in EMA_WINDOWS
    (6/20/60/120/255), sourced from stats.{etf,index,stock}_tech_stats.

    GPU acceleration: uses the shared ``grouped_diff`` helper, which
    routes to cuDF when the row count exceeds the ``groupby_diff``
    breakeven (~320K rows conservative). Two batched calls cover all
    10 diff() operations: one for the 5 slopes, one for the 5
    curvatures (diff-of-diff). Each batch runs on a single cuDF
    transfer of only the needed columns (group_keys + input cols),
    avoiding the cost of transferring the full wide source frame.
    """
    df = df.sort_values(["sec_type", "code", "date"]).reset_index(drop=True)
    grp_keys = ["sec_type", "code"]

    # Slopes: 1st derivative of each EMA window (5 columns).
    ema_cols = [f"ema{w}" for w in EMA_WINDOWS]
    slope_out = [f"ema{w}_slope" for w in EMA_WINDOWS]
    grouped_diff(
        df, grp_keys,
        cols=ema_cols, out_names=slope_out,
        sort=False,  # df already sorted above
    )

    # Curvatures: 2nd derivative = diff of slope (5 columns).
    curv_out = [f"ema{w}_curvature" for w in EMA_WINDOWS]
    grouped_diff(
        df, grp_keys,
        cols=slope_out, out_names=curv_out,
        sort=False,
    )

    return df


def compute_rolling_stds(df: pd.DataFrame) -> pd.DataFrame:
    """Add 5 rolling population σ columns (Bollinger band widths) for price,
    computed per (sec_type, code) ordered by date.

    std_{W}days[t] = population standard deviation of price over the last
    W rows (ddof=0, the Bollinger convention). NULL until W consecutive
    rows are available (pandas .rolling(W, min_periods=W).std(ddof=0)
    returns NaN for any window with fewer than W non-NaN values).

    Why population (ddof=0) instead of sample (ddof=1)?
      - Standard Bollinger Bands use population σ. Most charting platforms
        (TradingView, Bloomberg) follow this convention.
      - For N=5 the difference between ddof=0 and ddof=1 is meaningful
        (σ_sample = σ_pop × sqrt(5/4) ≈ 1.118 × σ_pop), so the choice
        matters for the band width.

    σ is in price units (not price²), so it fits NUMERIC(10,6) without
    overflow for any realistic ETF / index / stock price (σ << price
    because σ ≤ max(|price - mean|) ≤ price range).

    Adds columns: std_5days, std_20days, std_60days, std_120days, std_255days.

    Implementation: uses the shared ``grouped_rolling_agg`` helper
    (Cython-compiled ``groupby().rolling().std()``) instead of
    ``transform(lambda s: ...)``. The lambda wrapper forced pandas to
    call back into Python once per group (~5000+ groups × 5 windows =
    25K+ Python callbacks on the 8M-row DataFrame), which dominated
    runtime. The shared helper keeps the entire rolling-std computation
    inside Cython and is cuDF-compatible.
    """
    df = df.sort_values(["sec_type", "code", "date"]).reset_index(drop=True)
    grp_keys = ["sec_type", "code"]
    for w in MA_WINDOWS:
        col = f"std_{w}days"
        # min_periods=W ensures NULL until the window is fully populated —
        # matches the SQL COMMENT and avoids misleading early-window σ
        # values that would be computed from fewer than W observations.
        df[col] = grouped_rolling_agg(
            df, grp_keys, "price", window=w,
            min_periods=w, agg="std", ddof=0,
        )
    return df


def compute_trading_amt_mas(df: pd.DataFrame) -> pd.DataFrame:
    """Add 5 trading-amount moving-average columns
    (trading_amt_ma{5,20,60,120,255}) per (sec_type, code) ordered by date.

    trading_amt_ma{W}[t] = simple moving average of trading_amount over the
    last W rows. NULL until W rows are available (pandas
    .rolling(W, min_periods=W).mean()).

    NULL-date handling: source trading_amount can be NULL on some dates
    (e.g. stats.index_basic_stats.trading_amount is NULL for ~7.5% of
    index rows — typically newer indices whose source feed lacks turnover).
    Rather than letting a single NULL create a W-day NaN gap in the MA
    (the default pandas rolling-mean behavior with min_periods=W), NULL
    values are treated as 0 (zero turnover / no increase) in the rolling
    SUM, while still counting the date in the W-row denominator:

        trading_amt_ma{W}[t] = (sum of trading_amount in last W rows,
                                 NULL→0) / W

    This means a NULL date pulls the MA toward 0 (as if no trading
    happened that day), which is the semantically correct interpretation
    for "no turnover data available". The alternative (skip NULLs and
    divide by non-NULL count) would over-weight the remaining days and
    hide the data gap; the old behavior (NaN the entire window) was
    even worse — a single NULL wiped out 255 days of trading_amt_ma255.

    The original trading_amount column is NOT modified — a temporary
    filled column is used for the rolling computation and dropped
    afterward, so downstream callers (e.g. the API's chart SQL) still
    see the original NULL values.

    Source column: df["trading_amount"] (yuan), populated by
    fetch.fetch_source_data from stats.{etf_liquidity_margin,
    index_basic_stats, stock_liquidity_margin}.trading_amount.

    Same incremental-mode reasoning as compute_rolling_stds: the rolling
    window needs up to 255 prior rows to populate trading_amt_ma255, so
    the caller passes the FULL per-code history and the result is filtered
    to target_dates downstream (in fetch_source_data).

    Adds columns: trading_amt_ma5, trading_amt_ma20, trading_amt_ma60,
    trading_amt_ma120, trading_amt_ma255.

    Implementation: reuses the shared ``grouped_rolling_agg`` helper so the
    rolling-mean computation runs inside Cython and is cuDF-compatible
    (same pattern as compute_rolling_stds).
    """
    # Host-pure membership (proxied Index.__contains__ falls back).
    if "trading_amount" not in set(safe_columns(df)):
        # Defensive: fetch_source_data always returns the column, but if a
        # caller passes a DataFrame without it (e.g. a unit-test stub),
        # emit NULL columns so downstream assembly doesn't KeyError.
        for col in TRADING_AMT_MA_COLUMNS:
            df[col] = np.nan
        return df
    df = df.sort_values(["sec_type", "code", "date"]).reset_index(drop=True)
    grp_keys = ["sec_type", "code"]
    # Treat NULL trading_amount as 0 (zero turnover) in the rolling mean,
    # but still count the date in the W-row denominator. We fillna(0) a
    # TEMPORARY column so the original trading_amount is unchanged for
    # downstream callers. After fillna, min_periods=W just means "need W
    # rows in the window" (no NaN to skip), giving sum(filled) / W.
    tmp_col = "_trading_amount_filled"
    df[tmp_col] = df["trading_amount"].fillna(0.0)
    for w, col in zip(MA_WINDOWS, TRADING_AMT_MA_COLUMNS):
        df[col] = grouped_rolling_agg(
            df, grp_keys, tmp_col, window=w,
            min_periods=w, agg="mean",
        )
    df.drop(columns=[tmp_col], inplace=True)
    return df


def compute_trading_amt_market_share_mas(
    df: pd.DataFrame, denominator_by_date: dict
) -> pd.DataFrame:
    """Add 5 trading-amount MARKET-SHARE moving-average columns
    (trading_amt_market_share_ma{5,20,60,120,255}) per (sec_type, code).

    Pipeline:
      1. Map each row's date to a denominator via ``denominator_by_date``
         (SUM of stats.exchange_trading_amt.total_trading_amount across
         exchanges whose stats.sec_classification.is_primary_exchange =
         TRUE on that date). Rows whose date is not in the dict get NaN.
      2. daily market_share = trading_amount / denominator. NULL when
         either is NaN/None or denominator <= 0 (a single code cannot
         meaningfully have "0%" share when the total-market denominator
         is unknown).
      3. For the W-day MA, fillna(0) the daily market_share (same pattern
         as compute_trading_amt_mas: NULL → 0 in the rolling sum, counted
         in the W-row denominator so a single NULL date pulls the MA
         toward 0 without creating a W-day NaN gap).
      4. trading_amt_market_share_ma{W} = rolling mean of the filled
         market_share per (sec_type, code) with min_periods=W.

    Args:
        df: source DataFrame with columns ``sec_type``, ``code``,
            ``date``, ``trading_amount``. Typically the output of
            ``compute_trading_amt_mas`` (so trading_amt_ma* are already
            present), but this function only reads ``trading_amount``.
        denominator_by_date: {date: float} mapping each date to the
            total-market trading turnover (denominator). Sourced from
            stats.exchange_trading_amt by fetch._fetch_market_share_denominator.

    Adds columns: trading_amt_market_share_ma5, _ma20, _ma60, _ma120, _ma255.

    Same incremental-mode reasoning as compute_trading_amt_mas: the rolling
    window needs up to 255 prior rows, so the caller passes the FULL
    per-code history and the result is filtered to target_dates downstream.
    """
    if "trading_amount" not in set(safe_columns(df)) or not denominator_by_date:
        # Defensive: emit NULL columns so downstream assembly doesn't KeyError.
        for col in TRADING_AMT_MARKET_SHARE_MA_COLUMNS:
            df[col] = np.nan
        return df

    df = df.sort_values(["sec_type", "code", "date"]).reset_index(drop=True)

    # Step 1: map date -> denominator. Series.map on the date column.
    denom_series = df["date"].map(denominator_by_date)

    # Step 2: daily market_share = trading_amount / denominator.
    # NaN when trading_amount is NaN or denominator is NaN/<=0.
    ta = pd.to_numeric(df["trading_amount"], errors="coerce")
    den = pd.to_numeric(denom_series, errors="coerce")
    market_share = ta / den
    # Null where denominator is missing/zero/non-finite (whole-date null).
    bad_den = den.isna() | ~np.isfinite(den) | (den <= 0)
    market_share = market_share.where(~bad_den & ~ta.isna())

    # Step 3: fillna(0) a TEMPORARY column for the rolling mean (same
    # pattern as compute_trading_amt_mas: NULL → 0 in sum, counted in W-row
    # denominator). The original market_share is not exposed downstream;
    # only the MA columns are.
    tmp_col = "_market_share_filled"
    df[tmp_col] = market_share.fillna(0.0)

    # Step 4: rolling mean per (sec_type, code) with min_periods=W.
    grp_keys = ["sec_type", "code"]
    for w, col in zip(MA_WINDOWS, TRADING_AMT_MARKET_SHARE_MA_COLUMNS):
        df[col] = grouped_rolling_agg(
            df, grp_keys, tmp_col, window=w,
            min_periods=w, agg="mean",
        )
    df.drop(columns=[tmp_col], inplace=True)
    return df


def compute_trading_amt_ma_slopes(df: pd.DataFrame) -> pd.DataFrame:
    """Add 5 trading-amount MA SLOPE columns
    (trading_amt_ma{5,20,60,120,255}_slope) per (sec_type, code).

    Each slope is a RATIO (fractional daily change), NOT a raw difference:

        trading_amt_ma{W}_slope[t] = (ma[t] - ma[t-1]) / ma[t-1]

    This is the day-over-day percentage change in the W-day trading-amount
    MA. A ratio (not raw difference) is used so the column fits NUMERIC(10,4)
    — raw differences in yuan would overflow (trading_amt_ma values reach
    10^13 for broad indices, so day-to-day changes can be 10^9+).

    NULL semantics (per user directive "if denominator or numerator is
    null, the value is null as well"):
      - ma[t] is NULL        → numerator is NULL     → slope is NULL
      - ma[t-1] is NULL      → numerator AND denominator are NULL → slope NULL
      - ma[t-1] <= 0         → denominator guard      → slope is NULL
      - first date per code   → no prior row (ma[t-1] missing) → slope is NULL

    Source columns: df["trading_amt_ma{5,20,60,120,255}"] (already computed
    by compute_trading_amt_mas). Must be called AFTER compute_trading_amt_mas.

    Adds columns: trading_amt_ma5_slope, _ma20_slope, _ma60_slope,
    _ma120_slope, _ma255_slope.
    """
    df = df.sort_values(["sec_type", "code", "date"]).reset_index(drop=True)
    grp_keys = ["sec_type", "code"]

    for w, ma_col, slope_col in zip(
        MA_WINDOWS, TRADING_AMT_MA_COLUMNS, TRADING_AMT_MA_SLOPE_COLUMNS
    ):
        cur = pd.to_numeric(df[ma_col], errors="coerce")
        # Shift by 1 within each (sec_type, code) group to get ma[t-1].
        # Using groupby.shift to respect group boundaries (first row of
        # each code gets NaN, which correctly nulls the slope).
        prev = df.groupby(grp_keys, sort=False)[ma_col].shift(1)
        prev = pd.to_numeric(prev, errors="coerce")
        # Ratio = (cur - prev) / prev. Null where cur or prev is NaN, or
        # prev <= 0 (denominator guard).
        slope = (cur - prev) / prev
        bad = cur.isna() | prev.isna() | (prev <= 0) | ~np.isfinite(slope)
        df[slope_col] = slope.where(~bad)

    return df


def compute_trading_amt_market_share_vs_mas(
    df: pd.DataFrame, denominator_by_date: dict
) -> pd.DataFrame:
    """Add 5 trading-amount MARKET-SHARE-vs-MA gap columns
    (trading_amt_market_share_vs_ma{5,20,60,120,255}) per (sec_type, code).

    Each gap is a signed fractional ratio:

        trading_amt_market_share_vs_ma{W}[t] =
            (market_share[t] - market_share_ma{W}[t]) / market_share_ma{W}[t]

    where market_share[t] = trading_amount[t] / denominator[t] (denominator
    = SUM of primary-exchange total_trading_amount on date t, sourced from
    stats.exchange_trading_amt).

    A positive value means the security's current market share is ABOVE its
    W-day average (gaining relative liquidity); negative means BELOW (losing
    relative liquidity). Typical |ratio| < 1.0 (market share rarely swings
    more than ±100% relative to its own MA), so NUMERIC(10,4) is sufficient.

    NULL semantics:
      - market_share is NULL (trading_amount or denominator is NULL/<=0) →
        gap is NULL (numerator NULL).
      - market_share_ma{W} is NULL (fewer than W rows) → gap is NULL
        (denominator NULL).
      - market_share_ma{W} <= 0 → gap is NULL (denominator guard, same as
        gap_col's near-zero check).

    Must be called AFTER compute_trading_amt_market_share_mas (reads the
    trading_amt_market_share_ma{W} columns that it produces).

    Args:
        df: source DataFrame with columns ``sec_type``, ``code``, ``date``,
            ``trading_amount``, and ``trading_amt_market_share_ma{W}``.
        denominator_by_date: {date: float} mapping each date to the
            total-market trading turnover (same dict as
            compute_trading_amt_market_share_mas).

    Adds columns: trading_amt_market_share_vs_ma5, _vs_ma20, _vs_ma60,
    _vs_ma120, _vs_ma255.
    """
    if "trading_amount" not in set(safe_columns(df)) or not denominator_by_date:
        # Defensive: emit NULL columns so downstream assembly doesn't KeyError.
        for col in TRADING_AMT_MARKET_SHARE_VS_MA_COLUMNS:
            df[col] = np.nan
        return df

    # Recompute the raw daily market_share (same logic as
    # compute_trading_amt_market_share_mas steps 1-2). Stored in a temporary
    # column so it doesn't leak into the detail table (only the gap columns
    # are persisted, not the raw market_share itself).
    tmp_ms = "_market_share_raw"
    denom_series = df["date"].map(denominator_by_date)
    ta = pd.to_numeric(df["trading_amount"], errors="coerce")
    den = pd.to_numeric(denom_series, errors="coerce")
    market_share = ta / den
    bad_den = den.isna() | ~np.isfinite(den) | (den <= 0)
    df[tmp_ms] = market_share.where(~bad_den & ~ta.isna())

    # Gap = (market_share - market_share_ma{W}) / market_share_ma{W}.
    # Uses gap_col for NULL / near-zero-denominator semantics (same as
    # price_vs_ma{W} and ma5_vs_ma{W} columns).
    for ma_col, vs_col in zip(
        TRADING_AMT_MARKET_SHARE_MA_COLUMNS,
        TRADING_AMT_MARKET_SHARE_VS_MA_COLUMNS,
    ):
        df[vs_col] = gap_col(df, tmp_ms, ma_col)

    df.drop(columns=[tmp_ms], inplace=True)
    return df


def gap_col(df: pd.DataFrame, num_col: str, den_col: str) -> pd.Series:
    """Vectorized (num - den) / den with NULL semantics matching safe_ratio.

    Returns None where num/den is NaN, where the denominator is zero or
    denormalized (|den| < 1e-12, which would produce a huge or non-finite
    ratio), or where the result is non-finite. The null_if_overflow pass
    in build_detail_frame is the final safety net for any ratio that still
    exceeds the NUMERIC(10,6) range.
    """
    num = df[num_col]
    den = df[den_col]
    out = (num - den) / den
    mask = (num.isna() | den.isna()
            | (den.abs() < 1e-12)
            | ~np.isfinite(out))
    return out.where(~mask, other=None)
