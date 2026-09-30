"""Shared assembly logic for analyze.pe_and_dividends.

This package's ``__init__`` holds what both metric subpackages (``pe`` /
``dividends``) share:
  - build_detail_rows — the combined daily detail frame assembly (one
    frame carrying the pe + dividend_yield columns; the __main__
    processors split it per metric at the write boundary)
  - fetch — the source-data fetch layer (the metrics share the fetch:
    index dividends need constituent closes; the date axis is the same)

cudf.pandas conventions (B-A2 / B-A3 fixes, 2026-08-29):
  - Dates stay datetime64[us] end-to-end; python ``date`` objects are
    materialized ONLY inside ``sanitize_for_db_insert`` via its
    ``date_cols`` param (host numpy pass at the DB-write boundary).
    NEVER pre-convert object-date columns into the frame — cuDF cannot
    represent them and every subsequent frame op (even unrelated numeric
    column access) pays a MixedTypeError fast-path failure + fallback.
  - Per-row lookups (iterrows + nested reversed scans) are replaced by
    merge_asof / year-key merge / groupby aggregation — never iterrows.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
#  Build the combined detail frame (the writer splits it into
#  analysis.pe / analysis.dividends)
# ---------------------------------------------------------------------------
def build_detail_rows(
    close_df: pd.DataFrame,
    pe_series: pd.Series | None,
    dividend_yield_df: pd.DataFrame,
    sec_type: str,
) -> pd.DataFrame:
    """Assemble the combined detail frame for the two split tables.

    Returns a DataFrame with columns sec_type, code, date (datetime64),
    pe, dividend_yield. NOT yet sanitized — the caller materializes
    DB rows via ``sanitize_for_db_insert(date_cols=["date"])`` so the
    datetime64 frame stays reusable for the pe pct-bands compute.

    Args:
        close_df: DataFrame with columns code, date, close (and pe).
        pe_series: Series aligned to close_df's index with cleaned pe
            values (the clean_pe output).
        dividend_yield_df: DataFrame with columns code, date, dividend_yield.
        sec_type: 'index', 'etf', or 'stock'.
    """
    if close_df.empty:
        return pd.DataFrame(
            columns=["sec_type", "code", "date", "pe", "dividend_yield"]
        )

    # Start with close_df as the base
    out = close_df[["code", "date"]].copy()
    out["sec_type"] = sec_type

    # Add pe (Series assignment aligns on index — no .values copy)
    if pe_series is not None:
        out["pe"] = pe_series
    else:
        out["pe"] = np.nan

    # Add dividend_yield
    if dividend_yield_df is not None and not dividend_yield_df.empty:
        out = out.merge(
            dividend_yield_df[["code", "date", "dividend_yield"]],
            on=["code", "date"],
            how="left",
        )
    else:
        out["dividend_yield"] = np.nan

    return out[["sec_type", "code", "date", "pe", "dividend_yield"]]


__all__ = [
    "build_detail_rows",
]
