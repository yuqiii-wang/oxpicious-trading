"""Step 1: pivot benchmarks + ETF amounts to wide/long format.

Pre-computed ONCE per call; reused by all subjects.
"""
from __future__ import annotations

import pandas as pd


# ---------------------------------------------------------------------------
#  Step 1: pivot benchmarks + ETF amounts to wide/long format ONCE per call
# ---------------------------------------------------------------------------
def prepare_pivots(
    index_closes: pd.DataFrame,
    etf_amount_by_index: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Pre-pivot benchmarks and ETF amounts to wide format.

    Returns (benchmark_close_wide, etf_amount_wide, etf_amount_long):
      - benchmark_close_wide: date x benchmark_code (close prices).
      - etf_amount_wide     : date x index_code (aggregate ETF turnover).
      - etf_amount_long     : long format (date, index_code, etf_amount).

    Both wide pivots are sorted by date. etf_amount_long is pre-built ONCE
    so each subject reuses it instead of recomputing stack+reset_index.
    """
    benchmark_close_wide = (
        index_closes.pivot(index="date", columns="benchmark_code",
                           values="benchmark_close")
        .sort_index()
    )

    if not etf_amount_by_index.empty:
        etf_amount_wide = (
            etf_amount_by_index.pivot(index="date", columns="index_code",
                                      values="etf_amount")
            .sort_index()
        )
        # Keep `date` as datetime64: converting to python date objects
        # (object dtype) poisons every downstream merge for cudf.  The
        # asyncpg boundary conversion happens in sanitize_for_db_insert.
        etf_amount_long = etf_amount_wide.stack().reset_index()
        etf_amount_long.columns = ["date", "index_code", "etf_amount"]
    else:
        etf_amount_wide = pd.DataFrame()
        etf_amount_long = pd.DataFrame(
            columns=["date", "index_code", "etf_amount"]
        )

    return benchmark_close_wide, etf_amount_wide, etf_amount_long
