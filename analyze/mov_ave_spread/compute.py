"""Pure pandas transformation logic for analyze.mov_ave_spread.

Builds the wide-format detail frame (one row per sec_type, code, date)
with 9 gap columns + 12 slope/curvature columns.

Broken into smaller, cuDF-friendly steps:
  - _assemble_detail_columns: vectorized gap + slope/curv + std assembly
  - _null_overflow_columns: NUMERIC(10,6) overflow guard
  - build_detail_frame: orchestrates the steps, returns a DataFrame for
    the CSV COPY writer (csv_copy_from_frame_async) — no per-row dicts
"""
from __future__ import annotations

import pandas as pd

from _common.df_utils import safe_columns
from analyze.mov_ave_spread.config import (
    TRADING_AMT_MA_COLUMNS,
    TRADING_AMT_MARKET_SHARE_MA_COLUMNS,
    TRADING_AMT_MA_SLOPE_COLUMNS,
    TRADING_AMT_MARKET_SHARE_VS_MA_COLUMNS,
)
from analyze.mov_ave_spread.helpers import (
    gap_col,
    null_if_overflow_counted,
)


def _assemble_detail_columns(
    df: pd.DataFrame,
) -> pd.DataFrame:
    """Assemble the wide-format detail DataFrame with all gap + slope +
    curvature + std columns.

    Pure vectorized pandas — no Python loops, no object-dtype
    intermediates. cuDF-compatible.
    """
    return pd.DataFrame({
        "sec_type":    df["sec_type"],
        "code":        df["code"],
        "date":        df["date"],
        "trading_amt_ma5":   df["trading_amt_ma5"],
        "trading_amt_ma20":  df["trading_amt_ma20"],
        "trading_amt_ma60":  df["trading_amt_ma60"],
        "trading_amt_ma120": df["trading_amt_ma120"],
        "trading_amt_ma255": df["trading_amt_ma255"],
        "trading_amt_market_share_ma5":   df["trading_amt_market_share_ma5"],
        "trading_amt_market_share_ma20":  df["trading_amt_market_share_ma20"],
        "trading_amt_market_share_ma60":  df["trading_amt_market_share_ma60"],
        "trading_amt_market_share_ma120": df["trading_amt_market_share_ma120"],
        "trading_amt_market_share_ma255": df["trading_amt_market_share_ma255"],
        "trading_amt_ma5_slope":   df["trading_amt_ma5_slope"],
        "trading_amt_ma20_slope":  df["trading_amt_ma20_slope"],
        "trading_amt_ma60_slope":  df["trading_amt_ma60_slope"],
        "trading_amt_ma120_slope": df["trading_amt_ma120_slope"],
        "trading_amt_ma255_slope": df["trading_amt_ma255_slope"],
        "trading_amt_market_share_vs_ma5":   df["trading_amt_market_share_vs_ma5"],
        "trading_amt_market_share_vs_ma20":  df["trading_amt_market_share_vs_ma20"],
        "trading_amt_market_share_vs_ma60":  df["trading_amt_market_share_vs_ma60"],
        "trading_amt_market_share_vs_ma120": df["trading_amt_market_share_vs_ma120"],
        "trading_amt_market_share_vs_ma255": df["trading_amt_market_share_vs_ma255"],
        "price_vs_ma5":   gap_col(df, "price", "ma5"),
        "price_vs_ma20":  gap_col(df, "price", "ma20"),
        "price_vs_ma60":  gap_col(df, "price", "ma60"),
        "price_vs_ma120": gap_col(df, "price", "ma120"),
        "price_vs_ma255": gap_col(df, "price", "ma255"),
        "ma5_vs_ma20":    gap_col(df, "ma5",   "ma20"),
        "ma5_vs_ma60":    gap_col(df, "ma5",   "ma60"),
        "ma5_vs_ma120":   gap_col(df, "ma5",   "ma120"),
        "ma5_vs_ma255":   gap_col(df, "ma5",   "ma255"),
        "price_slope":     df["price_slope"],
        "price_curvature": df["price_curvature"],
        "ma5_slope":       df["ma5_slope"],
        "ma20_slope":      df["ma20_slope"],
        "ma60_slope":      df["ma60_slope"],
        "ma120_slope":     df["ma120_slope"],
        "ma255_slope":     df["ma255_slope"],
        "ma5_curvature":   df["ma5_curvature"],
        "ma20_curvature":  df["ma20_curvature"],
        "ma60_curvature":  df["ma60_curvature"],
        "ma120_curvature": df["ma120_curvature"],
        "ma255_curvature": df["ma255_curvature"],
        "std_5days":   df["std_5days"],
        "std_20days":  df["std_20days"],
        "std_60days":  df["std_60days"],
        "std_120days": df["std_120days"],
        "std_255days": df["std_255days"],
    })


def _null_overflow_columns(
    out_df: pd.DataFrame,
    non_numeric_cols: tuple[str, ...],
    wide_numeric_cols: tuple[str, ...] = (),
) -> dict[str, int]:
    """Null any value whose absolute value would overflow its column's
    NUMERIC bound.

    Default bound: NUMERIC(10,6) — |value| < 10^4 after rounding to 6 dp.
    Wide bound (columns in ``wide_numeric_cols``): NUMERIC(24,4) —
    |value| < 10^20 after rounding to 4 dp. Used for trading_amt_ma*
    columns whose values (yuan) can reach 10^13+ on high-turnover days
    (broad indices like SSE Composite).

    Returns a dict of {column: count_nulled} for logging.
    """
    from analyze.mov_ave_spread.config import NUMERIC_WIDE_MAX_ABS

    # Host-pure column list (iterating a proxied Index falls back under
    # cudf.pandas).
    numeric_cols = [c for c in safe_columns(out_df) if c not in non_numeric_cols]
    nulled_counts = {}
    for c in numeric_cols:
        if c in wide_numeric_cols:
            clean, n = null_if_overflow_counted(
                out_df[c], max_abs=NUMERIC_WIDE_MAX_ABS, scale=4,
            )
        else:
            clean, n = null_if_overflow_counted(out_df[c])
        out_df[c] = clean
        if n > 0:
            nulled_counts[c] = n
    return nulled_counts


def build_detail_frame(df: pd.DataFrame) -> pd.DataFrame:
    """For each (sec_type, code, date) row, compute all 9 gap values and
    return the wide-format detail DataFrame ready for
    ``csv_copy_from_frame_async`` (COPY ... FORMAT csv).

    Orchestrates 2 smaller steps:
      1. _assemble_detail_columns — vectorized column assembly
      2. _null_overflow_columns — NUMERIC(10,6) overflow guard for gap /
         slope / curvature / std columns, NUMERIC(24,4) overflow guard
         for trading_amt_ma* columns. Also nulls NaN/±inf — the CSV
         writer renders NaN as an empty field (SQL NULL), so no
         sanitize_for_db_insert pass is needed (the old dict path's
         astype(object).where + to_dict cost ~3s of client CPU per
         100k-row chunk for zero benefit here).
    """
    if df.empty:
        return df.iloc[0:0]

    # Step 1: assemble all detail columns (vectorized).
    out_df = _assemble_detail_columns(df)

    # Step 2: overflow guard. trading_amt_ma* and trading_amt_market_share_ma*
    # columns are NUMERIC(24,4) — pass them as wide_numeric_cols so the guard
    # uses the 10^20 bound (default NUMERIC(10,6) bound of 10^4 would wrongly
    # null them).
    non_numeric_cols = ("sec_type", "code", "date")
    nulled_counts = _null_overflow_columns(
        out_df, non_numeric_cols,
        wide_numeric_cols=TRADING_AMT_MA_COLUMNS + TRADING_AMT_MARKET_SHARE_MA_COLUMNS,
    )
    if nulled_counts:
        total = sum(nulled_counts.values())
        per_col = ", ".join(f"{c}={n}" for c, n in nulled_counts.items())
        print(f"    -> overflow-guard nulled {total:,} value(s) across "
              f"{len(nulled_counts)} column(s): {per_col}", flush=True)

    return out_df
