"""Pure pandas transformation logic for analyze.mov_ave_spread.

Builds the wide-format detail rows (one row per sec_type, code, date)
with 9 gap columns + 12 slope/curvature columns + peaks_and_floors_date FK.

Broken into smaller, cuDF-friendly steps:
  - _compute_pf_date_mapping: nearest-preceding extreme via sort + concat
    + groupby.ffill (decomposition of merge_asof backward into cuDF-native
    primitives — cuDF lacks merge_asof)
  - _assemble_detail_columns: vectorized gap + slope/curv + std assembly
  - _null_overflow_columns: NUMERIC(10,6) overflow guard
  - build_detail_rows: orchestrates the steps + sanitizes for DB insert

GPU acceleration: when the cuDF router determines the GPU is worthwhile
for the row count, the sort + concat + groupby.ffill + filter steps in
_compute_pf_date_mapping run on cuDF. All four ops are cuDF-native
(cuDF lacks pandas.merge_asof, so the merge_asof backward join is
decomposed into the equivalent sort + concat + forward-fill pipeline).
The CPU path (pandas) is always available as a fallback.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import should_use_gpu
from analyze._common.sanitize import sanitize_for_db_insert
from analyze.mov_ave_spread.config import (
    TRADING_AMT_MA_COLUMNS,
    TRADING_AMT_MARKET_SHARE_MA_COLUMNS,
    TRADING_AMT_MA_SLOPE_COLUMNS,
    TRADING_AMT_MARKET_SHARE_VS_MA_COLUMNS,
)
from analyze.mov_ave_spread.helpers import gap_col, null_if_overflow


def _compute_pf_date_mapping(
    df: pd.DataFrame, pf_rows: list[dict] | None
) -> np.ndarray:
    """Compute peaks_and_floors_date for each detail row.

    For each detail row, finds the largest peaks_and_floors date <= the
    detail row's date (the "nearest preceding extreme") within the same
    (sec_type, code) group.

    Returns an array of date objects (or None) aligned to df's rows.

    Implementation — sort + concat + forward-fill decomposition:
      cuDF has no ``merge_asof``, so the backward asof-join is decomposed
      into four cuDF-native primitives:

        1. Build a "detail stub" frame: (sec_type, code, date, _orig_idx,
           _kind=1). _kind=1 so detail rows sort AFTER pf rows on the
           same date (the detail row on the extreme date itself should
           map to that same extreme).
        2. Build a "pf stub" frame: (sec_type, code, date=pf_date,
           pf_date, _kind=0). _kind=0 so pf rows sort BEFORE detail rows
           on the same date.
        3. Concat the two stubs into one timeline per (sec_type, code).
        4. Sort by (sec_type, code, date, _kind) — pf rows precede
           detail rows on the same date so the forward-fill picks them up.
        5. Forward-fill pf_date within each (sec_type, code) group. After
           this step, every detail row's pf_date is the largest extreme
           date <= its own date (NULL for detail rows before the first
           extreme in their group).
        6. Filter back to detail rows, sort by _orig_idx to restore the
           original df order, extract pf_date as python date objects.

      This decomposition is semantically equivalent to
      ``pd.merge_asof(df, pf_df, on="date", by=[...], direction="backward")``
      and uses only ``concat``, ``sort_values``, ``groupby.ffill`` and
      boolean indexing — all first-class cuDF operations.

    GPU acceleration: when the cuDF router determines the GPU is
    worthwhile for the combined frame size (the dominant input is
    df_keyed, ~df rows; pf_df is typically <50K rows), the concat +
    sort + ffill + filter steps run on cuDF. Only the minimal column
    subset (sec_type, code, date, _orig_idx, _kind, pf_date) is
    transferred to VRAM. The H2D/D2H transfer is amortized over the
    full pipeline.
    """
    if not pf_rows or len(pf_rows) == 0:
        return np.array([None] * len(df), dtype=object)

    # Step 1: detail stub. _kind=1 so detail sorts AFTER pf on same date.
    df_keyed = df[["sec_type", "code", "date"]].copy()
    df_keyed["_orig_idx"] = np.arange(len(df_keyed))
    df_keyed["date"] = pd.to_datetime(df_keyed["date"])
    df_keyed["_kind"] = 1

    # Step 2: peaks_and_floors stub. _kind=0 so pf sorts BEFORE detail
    # on same date (the detail row on the extreme date itself maps to
    # that same extreme — forward-fill picks up the pf row first).
    pf_df = pd.DataFrame(pf_rows)[["sec_type", "code", "date"]].copy()
    pf_df = pf_df.drop_duplicates(subset=["sec_type", "code", "date"])
    pf_df["date"] = pd.to_datetime(pf_df["date"])
    pf_df["pf_date"] = pf_df["date"]
    pf_df["_kind"] = 0

    # Decision is based on df_keyed — the dominant input. The combined
    # frame is only marginally larger (pf_df is tiny relative to df),
    # so df_keyed's row count is an accurate size estimate for the
    # VRAM check.
    if should_use_gpu(df_keyed, op_type="merge"):
        print(f"    [cuDF router] {len(df_keyed):,} rows — merge (GPU-worthy)", flush=True)

    # CPU path (pandas).
    combined = pd.concat([df_keyed, pf_df], ignore_index=True)
    combined = combined.sort_values(
        ["sec_type", "code", "date", "_kind"]
    )
    combined["pf_date"] = combined.groupby(
        ["sec_type", "code"], sort=False
    )["pf_date"].ffill()
    result = combined[combined["_kind"] == 1]
    merged = result.sort_values("_orig_idx").reset_index(drop=True)

    pf_dates = merged["pf_date"].dt.date.values
    # Forward-fill leaves NaN/NaT for rows with no preceding extreme
    # (detail rows before the first peaks_and_floors row in their group).
    # Convert NaT to None so asyncpg encodes them as SQL NULL (NaT is
    # not a valid datetime.date and would raise on encode).
    na_mask = pd.isna(pf_dates)
    if na_mask.any():
        pf_dates = pf_dates.copy()
        pf_dates[na_mask] = None
    return pf_dates


def _assemble_detail_columns(
    df: pd.DataFrame, pf_dates: np.ndarray
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
        "peaks_and_floors_date": pf_dates,
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

    numeric_cols = [c for c in out_df.columns if c not in non_numeric_cols]
    nulled_counts = {}
    for c in numeric_cols:
        if c in wide_numeric_cols:
            # NUMERIC(16,4): |value| < 10^(16-4) = 10^12.
            before_na = int(out_df[c].isna().sum())
            out_df[c] = null_if_overflow(
                out_df[c], max_abs=NUMERIC_WIDE_MAX_ABS, scale=4,
            )
        else:
            before_na = int(out_df[c].isna().sum())
            out_df[c] = null_if_overflow(out_df[c])
        n = int(out_df[c].isna().sum()) - before_na
        if n > 0:
            nulled_counts[c] = n
    return nulled_counts


def build_detail_rows(df: pd.DataFrame, pf_rows: list | None = None):
    """For each (sec_type, code, date) row, compute all 9 gap values and
    emit a wide-format dict list suitable for bulk_upsert into
    analysis.mov_ave_spreads_detail.

    Orchestrates 3 smaller steps:
      1. _compute_pf_date_mapping — merge_asof for FK dates
      2. _assemble_detail_columns — vectorized column assembly
      3. _null_overflow_columns — NUMERIC(10,6) overflow guard for gap /
         slope / curvature / std columns, NUMERIC(24,4) overflow guard
         for trading_amt_ma* columns.
      4. sanitize_for_db_insert — NaN/inf/None + to_dict
    """
    if df.empty:
        return []

    # Step 1: peaks_and_floors_date FK mapping.
    pf_dates = _compute_pf_date_mapping(df, pf_rows)

    # Step 2: assemble all detail columns (vectorized).
    out_df = _assemble_detail_columns(df, pf_dates)

    # Step 3: overflow guard. trading_amt_ma* and trading_amt_market_share_ma*
    # columns are NUMERIC(24,4) — pass them as wide_numeric_cols so the guard
    # uses the 10^20 bound (default NUMERIC(10,6) bound of 10^4 would wrongly
    # null them).
    non_numeric_cols = ("sec_type", "code", "date", "peaks_and_floors_date")
    nulled_counts = _null_overflow_columns(
        out_df, non_numeric_cols,
        wide_numeric_cols=TRADING_AMT_MA_COLUMNS + TRADING_AMT_MARKET_SHARE_MA_COLUMNS,
    )
    if nulled_counts:
        total = sum(nulled_counts.values())
        per_col = ", ".join(f"{c}={n}" for c, n in nulled_counts.items())
        print(f"    -> overflow-guard nulled {total:,} value(s) across "
              f"{len(nulled_counts)} column(s): {per_col}", flush=True)

    # Step 4: sanitize for DB insert (NaN/inf -> None + to_dict).
    numeric_cols = [c for c in out_df.columns if c not in non_numeric_cols]
    return sanitize_for_db_insert(out_df, numeric_cols=numeric_cols)
