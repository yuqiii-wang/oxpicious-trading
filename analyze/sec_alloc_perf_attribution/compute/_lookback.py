"""Incremental-mode lookback pre-filter.

When ``target_dates`` is specified, trims the source DataFrames to
only dates within the lookback window (max rolling window + MA5 buffer)
before the earliest target date, reducing the cross-product volume
by ~10-15x for single-date rebuilds.
"""
from __future__ import annotations

import datetime
from typing import Set, Tuple

import pandas as pd

from _common._holidays_and_weekdays import recent_trading_day_cutoff
from analyze.sec_alloc_perf_attribution.config import CORR_WINDOWS


# ---------------------------------------------------------------------------
#  Lookback window constants
# ---------------------------------------------------------------------------
_MAX_CORR_WINDOW: int = max(CORR_WINDOWS)  # 255 trading days
_MA5_BUFFER: int = 5                       # for etf_trading_amount_ratio MA5
LOOKBACK_TRADING_DAYS: int = _MAX_CORR_WINDOW + _MA5_BUFFER  # 260


# ---------------------------------------------------------------------------
#  Step 0: incremental-mode lookback pre-filter
# ---------------------------------------------------------------------------
def filter_dataframes_for_lookback(
    subject_closes: pd.DataFrame,
    index_closes: pd.DataFrame,
    etf_amount_by_index: pd.DataFrame,
    target_dates: Set[datetime.date],
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Filter dataframes to only include dates within the lookback window.

    When ``target_dates`` is non-empty, finds the earliest target date
    and filters all three dataframes to only include dates >= the
    lookback start (``LOOKBACK_TRADING_DAYS`` trading days before the
    earliest target date).  This reduces the cross-product from ~250K
    rows per subject to ~25K rows while keeping trailing windows correct
    for all target dates within the window.

    When ``target_dates`` is empty, returns the input unchanged
    (full-recompute mode).
    """
    if not target_dates:
        return subject_closes, index_closes, etf_amount_by_index

    min_target: datetime.date = min(target_dates)
    lookback_start: datetime.date = recent_trading_day_cutoff(
        LOOKBACK_TRADING_DAYS, ref=min_target
    )
    # Compare as Timestamp: the DataFrame date columns may be datetime64[s]
    # (pandas 2.x), which cannot be compared against a bare datetime.date.
    lookback_start_ts = pd.Timestamp(lookback_start)

    def _apply_date_filter(
        df: pd.DataFrame, date_col: str
    ) -> pd.DataFrame:
        if df.empty:
            return df
        return df[df[date_col] >= lookback_start_ts].copy()

    sub_filtered = _apply_date_filter(subject_closes, "date")
    idx_filtered = _apply_date_filter(index_closes, "date")
    etf_filtered = _apply_date_filter(etf_amount_by_index, "date")

    n_sub_before = len(subject_closes)
    n_idx_before = len(index_closes)
    n_etf_before = len(etf_amount_by_index)
    n_sub_after = len(sub_filtered)
    n_idx_after = len(idx_filtered)
    n_etf_after = len(etf_filtered)

    print(
        f"    [lookback] filter to dates >= {lookback_start} "
        f"({LOOKBACK_TRADING_DAYS} trading days before {min_target}): "
        f"subjects {n_sub_after:,}/{n_sub_before:,} "
        f"benchmarks {n_idx_after:,}/{n_idx_before:,} "
        f"etf_amounts {n_etf_after:,}/{n_etf_before:,}",
        flush=True,
    )

    return sub_filtered, idx_filtered, etf_filtered
