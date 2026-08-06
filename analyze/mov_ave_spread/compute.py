"""Pure pandas transformation logic for analyze.mov_ave_spread.

Builds the wide-format detail rows (one row per sec_type, code, date)
with 9 gap columns + 12 slope/curvature columns + peaks_and_floors_date FK.

Broken into smaller, cuDF-friendly steps:
  - _compute_pf_date_mapping: merge_asof for nearest-preceding extreme
  - _assemble_detail_columns: vectorized gap + slope/curv + std assembly
  - _null_overflow_columns: NUMERIC(10,6) overflow guard
  - build_detail_rows: orchestrates the steps + sanitizes for DB insert

GPU acceleration: when the cuDF router determines the GPU is worthwhile
for the row count, the merge_asof in _compute_pf_date_mapping runs on
cuDF (op_type='merge' — cuDF's hash merge_asof is ~13× faster than
pandas on the 8M-row mov_ave_spread source). The CPU path (pandas
Cython) is always available as a fallback.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from analyze._common._cuDF import should_use_gpu
from analyze._common.sanitize import sanitize_for_db_insert
from analyze.mov_ave_spread.helpers import gap_col, null_if_overflow


def _compute_pf_date_mapping(
    df: pd.DataFrame, pf_rows: list[dict] | None
) -> np.ndarray:
    """Compute peaks_and_floors_date for each row via merge_asof.

    For each detail row, finds the nearest preceding extreme date
    (largest extreme date <= detail.date) using pd.merge_asof with
    direction="backward".

    Returns an array of date objects (or None) aligned to df's rows.

    GPU acceleration: when the cuDF router determines the GPU is
    worthwhile for the left DataFrame size (merge op_type — cuDF's
    sorted merge_asof is ~13× faster than pandas on 8M+ rows), the
    merge runs on cuDF. The left DataFrame (df_keyed, 8M+ rows) is the
    dominant input; the right (pf_df, typically <50K rows) is small.
    The router checks the larger input. The H2D/D2H transfer is
    amortized over the full merge.
    """
    if not pf_rows or len(pf_rows) == 0:
        return np.array([None] * len(df), dtype=object)

    pf_df = pd.DataFrame(pf_rows)[["sec_type", "code", "date"]].copy()
    pf_df = pf_df.drop_duplicates(subset=["sec_type", "code", "date"])
    pf_df["date"] = pd.to_datetime(pf_df["date"])
    pf_df["pf_date"] = pf_df["date"]

    df_keyed = df[["sec_type", "code", "date"]].copy()
    df_keyed["_orig_idx"] = np.arange(len(df_keyed))
    df_keyed["date"] = pd.to_datetime(df_keyed["date"])

    # merge_asof requires the on-key to be globally monotonic even when
    # `by` is used. Sort by date (with sec_type/code as tiebreakers).
    pf_df = pf_df.sort_values(["date", "sec_type", "code"]).reset_index(drop=True)
    df_keyed = df_keyed.sort_values(["date", "sec_type", "code"])

    # GPU path: cuDF merge_asof. The left frame (df_keyed) is the
    # dominant input — the router checks its row count against the
    # merge breakeven (~520K rows conservative).
    if should_use_gpu(df_keyed, op_type="merge"):
        import cudf  # type: ignore[import-untyped]
        # cuDF merge_asof requires both frames as cuDF DataFrames with
        # datetime64[ns] on-key. Both are already pd.to_datetime'd above.
        # Transfer only the needed columns: sec_type, code, date, pf_date
        # (right) / _orig_idx (left). This avoids transferring the full
        # 30+ column source DataFrame.
        g_left = cudf.from_pandas(
            df_keyed[["sec_type", "code", "date", "_orig_idx"]]
        )
        g_right = cudf.from_pandas(
            pf_df[["sec_type", "code", "date", "pf_date"]]
        )
        # cuDF requires both frames sorted by the on-key for merge_asof.
        g_left = g_left.sort_values("date")
        g_right = g_right.sort_values("date")
        g_merged = cudf.merge_asof(
            g_left, g_right,
            on="date", by=["sec_type", "code"], direction="backward",
        )
        merged = g_merged.to_pandas()
    else:
        # CPU path (pandas Cython).
        merged = pd.merge_asof(
            df_keyed, pf_df,
            on="date", by=["sec_type", "code"], direction="backward",
        )

    merged = merged.sort_values("_orig_idx").reset_index(drop=True)
    pf_dates = merged["pf_date"].dt.date.values
    # merge_asof returns NaT for unmatched rows (no preceding extreme).
    # Convert NaT to None so asyncpg encodes them as SQL NULL (NaT is not
    # a valid datetime.date and would raise on encode).
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
    out_df: pd.DataFrame, non_numeric_cols: tuple[str, ...]
) -> dict[str, int]:
    """Null any value whose absolute value would overflow NUMERIC(10,6).

    Returns a dict of {column: count_nulled} for logging.
    """
    numeric_cols = [c for c in out_df.columns if c not in non_numeric_cols]
    nulled_counts = {}
    for c in numeric_cols:
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
      3. _null_overflow_columns — NUMERIC(10,6) overflow guard
      4. sanitize_for_db_insert — NaN/inf/None + to_dict
    """
    if df.empty:
        return []

    # Step 1: peaks_and_floors_date FK mapping.
    pf_dates = _compute_pf_date_mapping(df, pf_rows)

    # Step 2: assemble all detail columns (vectorized).
    out_df = _assemble_detail_columns(df, pf_dates)

    # Step 3: NUMERIC(10,6) overflow guard.
    non_numeric_cols = ("sec_type", "code", "date", "peaks_and_floors_date")
    nulled_counts = _null_overflow_columns(out_df, non_numeric_cols)
    if nulled_counts:
        total = sum(nulled_counts.values())
        per_col = ", ".join(f"{c}={n}" for c, n in nulled_counts.items())
        print(f"    -> NUMERIC(10,6) overflow-guard nulled {total:,} value(s) "
              f"across {len(nulled_counts)} column(s): {per_col}", flush=True)

    # Step 4: sanitize for DB insert (NaN/inf -> None + to_dict).
    numeric_cols = [c for c in out_df.columns if c not in non_numeric_cols]
    return sanitize_for_db_insert(out_df, numeric_cols=numeric_cols)
