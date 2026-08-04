"""Pure pandas transformation logic for analyze.mov_ave_spread.

Builds the wide-format detail rows (one row per sec_type, code, date)
with 9 gap columns + 12 slope/curvature columns.
"""
from __future__ import annotations

import pandas as pd

from analyze.mov_ave_spread.helpers import gap_col, null_if_overflow


def build_detail_rows(df: pd.DataFrame):
    """For each (sec_type, code, date) row, compute all 9 gap values and
    emit a wide-format dict suitable for bulk_upsert into
    analysis.mov_ave_spreads_detail.

    Includes the precomputed price_slope / price_curvature and
    ma{W}_slope / ma{W}_curvature columns, plus the 5 rolling population σ
    columns (std_{5,20,60,120,255}days) used for Bollinger band envelopes.

    Uses vectorized pandas ops + to_dict(orient='records') for speed on
    large DataFrames (millions of rows).
    """
    if df.empty:
        return []

    out_df = pd.DataFrame({
        "sec_type":    df["sec_type"],
        "code":          df["code"],
        "date":          df["date"],
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
    # Null any value whose absolute value would overflow NUMERIC(10,6)
    # (|value| >= 10000 after rounding to 6 decimals). This is the exception
    # case -> NULL: rows are kept (with the offending column set to NULL)
    # rather than dropping the whole row or failing the bulk upsert. It
    # mainly affects the raw-difference slope/curvature columns of
    # high-priced ETFs/indices at corporate-action boundaries; gap ratios
    # are also guarded here as a final safety net for near-zero denominators.
    numeric_cols = [c for c in out_df.columns
                    if c not in ("sec_type", "code", "date")]
    nulled_counts = {}
    for c in numeric_cols:
        before_na = int(out_df[c].isna().sum())
        out_df[c] = null_if_overflow(out_df[c])
        n = int(out_df[c].isna().sum()) - before_na
        if n > 0:
            nulled_counts[c] = n
    if nulled_counts:
        total = sum(nulled_counts.values())
        per_col = ", ".join(f"{c}={n}" for c, n in nulled_counts.items())
        print(f"    -> NUMERIC(10,6) overflow-guard nulled {total:,} value(s) "
              f"across {len(nulled_counts)} column(s): {per_col}", flush=True)
    # Replace NaN with None so asyncpg serializes them as SQL NULL.
    out_df = out_df.where(pd.notna(out_df), None)
    return out_df.to_dict(orient="records")
