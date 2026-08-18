"""Internal trading-amount step for analyze.mov_ave_spread.

Trading-amount metrics (5 trading-amount MA columns + 5 trading-amount
Bollinger band σ columns + 5 market-share MA columns + 5 MA slope columns
+ 1 raw slope column + 6 liquidity-impact ratio columns (trading-amount
/ 1M per price-slope, matching-timescale) + 5 market-share-vs-MA
gap columns) for ETF + Index + Stock. One row per
(sec_type, code, date) in analysis.mov_ave_trading_amt.

Source: the same source DataFrame already loaded by the parent
mov_ave_spread.fetch_source_data — reuses the same DataFrame, no second
DB round-trip. The trading_amt_ma{*}, trading_amt_market_share_ma{*},
trading_amt_ma{*}_slope, and trading_amt_market_share_vs_ma{*} columns
are already pre-computed by the parent's helper functions and carried in
the source DataFrame.

This module computes NEW columns on top of those:
  - Rolling population σ (ddof=0) of trading_amt_maW over W days
    (Bollinger band width for each MA window)
  - trading_amt_slope — fractional daily change of raw trading_amount
  - 6 slope-vs-price ratio columns (trading-amount slope / price slope)
    as liquidity-impact proxies

This module is an INTERNAL step of analyze.mov_ave_spread — it is invoked
from __main__.py after the detail + peaks_and_floors tables have been
repopulated, reusing the same DB connection + source DataFrame.

Incremental mode (``force=False``):
  Only dates present in source identity tables but NOT yet in
  analysis.mov_ave_trading_amt are (re)computed and upserted.
  The missing-date check is PER-sec_type.

Force mode (``force=True``):
  Truncate analysis.mov_ave_trading_amt, then recompute and
  insert all rows for the active universe.

GPU acceleration: the rolling std computations use the shared
``grouped_rolling_agg`` helper, which routes to cuDF when the row count
exceeds the rolling_std breakeven (~100K rows conservative).
"""
from __future__ import annotations

import time
from typing import Optional, Set

import numpy as np
import pandas as pd

from _common.build_commons import (
    truncate_table_async,
    find_missing_analysis_dates,
)
from _common.df_utils import grouped_rolling_agg
from analyze._common import (
    build_and_insert_chunked,
    upsert_analysis_identity,
    sanitize_for_db_insert,
)
from analyze.mov_ave_spread.config import (
    NUMERIC_WIDE_MAX_ABS,
    SEC_TYPES,
    SEC_TYPE_IDENTITY_TABLE,
    TRADING_AMT_ANALYSIS_NAME,
    TRADING_AMT_COLUMNS,
    TRADING_AMT_DESCRIPTION,
    TRADING_AMT_MA_COLUMNS,
    TRADING_AMT_MA_SLOPE_COLUMNS,
    TRADING_AMT_MARKET_SHARE_MA_COLUMNS,
    TRADING_AMT_MARKET_SHARE_VS_MA_COLUMNS,
    TRADING_AMT_PRICE_SLOPE_SOURCE_COLUMNS,
    TRADING_AMT_RAW_SLOPE_COLUMN,
    TRADING_AMT_SLOPE_VS_PRICE_RATIO_COLUMNS,
    TRADING_AMT_STD_COLUMNS,
    TRADING_AMT_TABLE,
)

TRADING_AMT_STD_WINDOWS = (5, 20, 60, 120, 255)


# ---------------------------------------------------------------------------
#  Compute helpers (pure pandas / cuDF)
# ---------------------------------------------------------------------------

def compute_trading_amt_stds(df: pd.DataFrame) -> pd.DataFrame:
    """Add rolling population σ (ddof=0) of trading_amt_maW over W days.

    For each window W in TRADING_AMT_STD_WINDOWS:
      - trading_amt_stdW = rolling population σ of trading_amt_maW
        over the past W days (min_periods=W so NULL until full window).

    Uses the shared ``grouped_rolling_agg`` helper for GPU acceleration.
    The σ columns are used for Bollinger-style envelopes (MA ± k×σ)
    around each trading-amount MA line.
    """
    if df.empty:
        for col in TRADING_AMT_STD_COLUMNS:
            df[col] = pd.Series(dtype="float64")
        return df

    df = df.sort_values(["sec_type", "code", "date"]).reset_index(drop=True)
    grp_keys = ["sec_type", "code"]

    for i, w in enumerate(TRADING_AMT_STD_WINDOWS):
        std_col = TRADING_AMT_STD_COLUMNS[i]
        ma_col = TRADING_AMT_MA_COLUMNS[i]

        df[std_col] = grouped_rolling_agg(
            df, grp_keys, ma_col, window=w,
            min_periods=w, agg="std", ddof=0, sort=False,
        )

    return df


def compute_trading_amt_slope(df: pd.DataFrame) -> pd.DataFrame:
    """Add trading_amt_slope — fractional daily change of raw trading_amount.

    trading_amt_slope[t] = (ta[t] - ta[t-1]) / ta[t-1]

    Same formula as trading_amt_ma{W}_slope but on the raw value.
    NUMERIC(10,4) — typical |slope| < 0.5 for broad indices.

    NULL on first date of each code or when ta[t]/ta[t-1] is NULL or
    ta[t-1] <= 0.
    """
    if df.empty:
        df[TRADING_AMT_RAW_SLOPE_COLUMN] = pd.Series(dtype="float64")
        return df

    df = df.sort_values(["sec_type", "code", "date"]).reset_index(drop=True)
    grp_keys = ["sec_type", "code"]

    cur = pd.to_numeric(df["trading_amount"], errors="coerce")
    prev = df.groupby(grp_keys, sort=False)["trading_amount"].shift(1)
    prev = pd.to_numeric(prev, errors="coerce")
    slope = (cur - prev) / prev
    bad = cur.isna() | prev.isna() | (prev <= 0) | ~np.isfinite(slope)
    df[TRADING_AMT_RAW_SLOPE_COLUMN] = slope.where(~bad)

    return df


def compute_trading_amt_slope_vs_price_ratios(df: pd.DataFrame) -> pd.DataFrame:
    """Add 6 liquidity-impact ratio columns (trading-amount / price-slope).

    Each column = (trading_amount_in_millions / price_slope):
      col[0] = (trading_amount / 1M) / price_slope             (raw vs raw)
      col[1] = (trading_amt_ma5 / 1M) / ma5_slope
      col[2] = (trading_amt_ma20 / 1M) / ma20_slope
      col[3] = (trading_amt_ma60 / 1M) / ma60_slope
      col[4] = (trading_amt_ma120 / 1M) / ma120_slope
      col[5] = (trading_amt_ma255 / 1M) / ma255_slope

    Trading amount is divided by 1,000,000 to express capital in millions.
    Matching-timescale: numerator and denominator use the same window.
    Denominator=0 is auto-set to 1.0 to avoid division-by-zero.

    Interpretation: how many millions of capital push price by one unit
    — an elasticity-like liquidity-impact proxy.

    NULL when numerator or denominator is NULL.
    NUMERIC(10,4).
    """
    if df.empty:
        for col in TRADING_AMT_SLOPE_VS_PRICE_RATIO_COLUMNS:
            df[col] = pd.Series(dtype="float64")
        return df

    # col[0]: (trading_amount / 1M) / price_slope
    _safe_divide_millions(
        df,
        numerator_col="trading_amount",
        denominator_col="price_slope",
        out_col=TRADING_AMT_SLOPE_VS_PRICE_RATIO_COLUMNS[0],
    )

    # col[1..5]: (trading_amt_maW / 1M) / maW_slope, matching timescale
    ma_numerator_cols = [
        "trading_amt_ma5", "trading_amt_ma20", "trading_amt_ma60",
        "trading_amt_ma120", "trading_amt_ma255",
    ]
    price_slope_cols = ["ma5_slope", "ma20_slope", "ma60_slope",
                        "ma120_slope", "ma255_slope"]
    for i in range(5):
        _safe_divide_millions(
            df,
            numerator_col=ma_numerator_cols[i],
            denominator_col=price_slope_cols[i],
            out_col=TRADING_AMT_SLOPE_VS_PRICE_RATIO_COLUMNS[i + 1],
        )

    return df


def _safe_divide_millions(df: pd.DataFrame, numerator_col: str,
                          denominator_col: str, out_col: str) -> None:
    """Safe division with million-unit conversion and zero-denominator guard.

    result = (numerator / 1_000_000) / denominator;
    denominator=0 auto-set to 1.0. NULL when either is NULL or
    result is not finite.
    """
    num = pd.to_numeric(df[numerator_col], errors="coerce")
    den = pd.to_numeric(df[denominator_col], errors="coerce")
    den = den.where(den != 0, 1.0)
    result = (num / 1_000_000) / den
    bad = num.isna() | den.isna() | ~np.isfinite(result)
    df[out_col] = result.where(~bad)


def _safe_divide(df: pd.DataFrame, numerator_col: str, denominator_col: str,
                 out_col: str) -> None:
    """Safe column-wise division with NULL guards.

    result = numerator / denominator; NULL when either is NULL,
    denominator is 0, or result is not finite.
    """
    num = pd.to_numeric(df[numerator_col], errors="coerce")
    den = pd.to_numeric(df[denominator_col], errors="coerce")
    result = num / den
    bad = num.isna() | den.isna() | (den == 0) | ~np.isfinite(result)
    df[out_col] = result.where(~bad)


def sanitize_trading_amt_rows(df: pd.DataFrame) -> list[dict]:
    """Select the mov_ave_trading_amt columns, apply overflow guards,
    and sanitize for asyncpg bulk upsert (NaN/inf -> None + to_dict).

    Wide columns (NUMERIC(24,4)): trading_amt_ma* and trading_amt_std*
    columns (yuan values up to 10^20).
    Narrow columns (NUMERIC(10,4)): market-share MAs, slopes, and gaps
    (ratios, typical |value| < 10).
    """
    if df.empty:
        return []

    out_cols = list(TRADING_AMT_COLUMNS)
    out = df[out_cols].copy()

    non_numeric = ("sec_type", "code", "date")
    numeric_cols = [c for c in out_cols if c not in non_numeric]

    wide_cols = set(TRADING_AMT_MA_COLUMNS) | set(TRADING_AMT_STD_COLUMNS)

    nulled = {}
    for c in numeric_cols:
        before = int(out[c].isna().sum())
        if c in wide_cols:
            out[c] = _null_if_overflow_wide(out[c])
        else:
            out[c] = _null_if_overflow(out[c])
        n = int(out[c].isna().sum()) - before
        if n > 0:
            nulled[c] = n
    if nulled:
        total = sum(nulled.values())
        per = ", ".join(f"{c}={n}" for c, n in nulled.items())
        print(f"    -> overflow-guard nulled {total:,} value(s) across "
              f"{len(nulled)} column(s): {per}", flush=True)

    return sanitize_for_db_insert(out, numeric_cols=numeric_cols)


NUMERIC_MAX_ABS = 10000.0


def _null_if_overflow(series):
    s = pd.to_numeric(series, errors="coerce")
    mask = s.isna() | ~np.isfinite(s) | (s.abs().round(4) >= NUMERIC_MAX_ABS)
    return s.where(~mask)


def _null_if_overflow_wide(series):
    s = pd.to_numeric(series, errors="coerce")
    mask = s.isna() | ~np.isfinite(s) | (s.abs().round(4) >= NUMERIC_WIDE_MAX_ABS)
    return s.where(~mask)


# ---------------------------------------------------------------------------
#  Pipeline (internal step — invoked from mov_ave_spread.__main__)
# ---------------------------------------------------------------------------

async def run_trading_amt(
    conn,
    df: pd.DataFrame,
    *,
    force: bool = False,
    pool=None,
    max_concurrent: int = 20,
    sec_type: str | None = None,
) -> None:
    """Run the trading-amount pipeline against the source data already
    loaded by the parent mov_ave_spread.

    Reuses the caller's DB connection and source DataFrame (the
    ``trading_amount``, ``trading_amt_ma{*}``, ``trading_amt_market_share_ma{*}``,
    ``trading_amt_ma{*}_slope``, and ``trading_amt_market_share_vs_ma{*}``
    columns are reused — no second DB fetch). The DataFrame must contain
    the FULL per-code history so rolling computations have enough lookback
    rows (up to 255).

    Pipeline
      1. Determine target dates (per-sec_type) by checking missing dates
         in analysis.mov_ave_trading_amt against source identity tables.
         In force mode, truncate the table instead.
      2. Compute rolling population σ (Bollinger band widths) of each
         trading_amt_maW over the FULL per-code history, then filter to
         target_dates.
      3. Upsert into analysis.mov_ave_trading_amt (chunked by date).
      4. Upsert analysis.analysis_identity registry.

    Args:
      conn: asyncpg connection (reused from parent).
      df: source DataFrame with at least columns [sec_type, code, date,
          trading_amount, trading_amt_ma5, and all pre-computed
          trading_amt_* columns]. Must be the FULL per-code history.
      force: when True, truncate analysis.mov_ave_trading_amt first
             and recompute all rows.
      pool: optional connection pool for parallel upsert chunks.
      sec_type: when provided, process only this sec_type (parent loop
                passes one sec_type at a time to bound memory). When
                None, infers sec_types from the DataFrame.
    """
    t0 = time.time()
    print("\n" + "=" * 78, flush=True)
    print("  MOV_AVE_TRADING_AMT (internal step of mov_ave_spread)", flush=True)
    print("=" * 78, flush=True)

    needed_cols = list(dict.fromkeys(
        ["sec_type", "code", "date", "trading_amount"]
        + list(TRADING_AMT_MA_COLUMNS)
        + list(TRADING_AMT_MARKET_SHARE_MA_COLUMNS)
        + list(TRADING_AMT_MA_SLOPE_COLUMNS)
        + list(TRADING_AMT_MARKET_SHARE_VS_MA_COLUMNS)
        + list(TRADING_AMT_PRICE_SLOPE_SOURCE_COLUMNS)
    ))
    available = [c for c in needed_cols if c in df.columns]
    ta_df = df[available].copy()

    if ta_df.empty:
        print("    -> no source data; skipping trading-amt step.", flush=True)
        return

    if sec_type is not None:
        sec_types = (sec_type,)
    else:
        sec_types = tuple(sorted(ta_df["sec_type"].unique()))

    # ---- Step 0: determine target dates (per-sec_type) --------------
    if force:
        print("    mode: FORCE (full recompute)", flush=True)
        print("\n[t0/3] Force mode: truncating mov_ave_trading_amt...",
              flush=True)
        await truncate_table_async(conn, TRADING_AMT_TABLE)
        target_dates_union: Optional[Set] = None
        print("    -> truncated; will recompute all rows", flush=True)
    else:
        print("    mode: incremental (missing dates only)", flush=True)
        print("\n[t0/3] Detecting missing dates PER-sec_type "
              "(etf_identity vs trading_amt[etf], etc.)...",
              flush=True)
        target_dates_per_st: dict = {}
        for st in sec_types:
            td_st = await find_missing_analysis_dates(
                conn, TRADING_AMT_TABLE,
                [SEC_TYPE_IDENTITY_TABLE[st]], sec_type=st,
            )
            target_dates_per_st[st] = td_st
            print(f"    -> {st}: {len(td_st)} missing dates", flush=True)
        target_dates_union = set()
        for s in target_dates_per_st.values():
            target_dates_union |= s
        print(f"    -> union across sec_types: "
              f"{len(target_dates_union)} dates to (re)compute",
              flush=True)
        if not target_dates_union:
            print("    -> DB is up to date; nothing to do.", flush=True)
            return

    # ---- Step 1: compute Bollinger band σ columns over full history --
    print("\n[t1/5] Computing rolling population σ (Bollinger bands) "
          "of trading_amt_ma{5,20,60,120,255} per (sec_type, code, date)...",
          flush=True)
    ta_df = compute_trading_amt_stds(ta_df)

    # ---- Step 2: compute raw trading_amt_slope over full history ----
    print("[t2/5] Computing trading_amt_slope (fractional daily change "
          "of raw trading_amount) per (sec_type, code, date)...",
          flush=True)
    ta_df = compute_trading_amt_slope(ta_df)

    # ---- Step 3: compute liquidity-impact ratio columns over full history
    print("[t3/5] Computing 6 liquidity-impact ratio columns "
          "((trading_amt / 1M) / price_slope, matching-timescale)...",
          flush=True)
    ta_df = compute_trading_amt_slope_vs_price_ratios(ta_df)

    if target_dates_union is not None and len(target_dates_union) > 0:
        n_before = len(ta_df)
        ta_df = ta_df[ta_df["date"].isin(target_dates_union)].reset_index(drop=True)
        print(f"    -> incremental filter: {len(ta_df):,} of {n_before:,} "
              f"rows are in target_dates_union", flush=True)

    if ta_df.empty:
        print("    -> no rows to upsert; skipping trading-amt upsert.", flush=True)
        return

    # ---- Step 4: build + insert (chunked by date) -------------------
    print(f"\n[t4/5] Building + inserting {len(ta_df):,} "
          f"mov_ave_trading_amt rows in date-bounded chunks "
          f"({'COPY' if force else 'upsert'} per chunk)...", flush=True)
    n = await build_and_insert_chunked(
        conn, pool, ta_df,
        sanitize_trading_amt_rows,
        table_name=TRADING_AMT_TABLE,
        key_columns=["sec_type", "code", "date"],
        force=force,
        sec_types=sec_types,
        max_concurrent=max_concurrent,
        label="mov_ave_trading_amt",
    )
    del ta_df
    print(f"    -> inserted {n:,} rows", flush=True)

    # ---- Step 5: register in analysis_identity ----------------------
    print(f"\n[t5/5] Upserting analysis.analysis_identity registry...",
          flush=True)
    await upsert_analysis_identity(
        conn,
        name=TRADING_AMT_ANALYSIS_NAME,
        detail_name="mov_ave_trading_amt",
        description=TRADING_AMT_DESCRIPTION,
    )

    print(f"\n  mov_ave_trading_amt wall time: "
          f"{time.time() - t0:.1f}s", flush=True)