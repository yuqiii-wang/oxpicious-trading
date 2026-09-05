"""Incremental-mode lookback pre-filter (pair grain).

Trims source frames to the lookback window (max corr window + MA5 buffer)
before the earliest target date — ~10-15x smaller cross product for
single-date rebuilds while keeping trailing windows correct.
"""
from __future__ import annotations

import datetime
from typing import Set, Tuple

import pandas as pd

from _common._holidays_and_weekdays import recent_trading_day_cutoff
from builds.cross_stats.config import CORR_WINDOWS

_MAX_CORR_WINDOW: int = max(CORR_WINDOWS)  # 255 trading days
_MA5_BUFFER: int = 5                       # for etf ratio MA5
LOOKBACK_TRADING_DAYS: int = _MAX_CORR_WINDOW + _MA5_BUFFER  # 260


def filter_dataframes_for_lookback(
    subject_closes: pd.DataFrame,
    index_closes: pd.DataFrame,
    etf_amount_by_index: pd.DataFrame,
    target_dates: Set[datetime.date],
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Filter frames to dates >= (LOOKBACK_TRADING_DAYS before earliest
    target). Empty target set → unchanged (full-recompute mode).

    Comparison via pd.Timestamp: frame date columns may be datetime64[s]
    which cannot compare against bare datetime.date.
    """
    if not target_dates:
        return subject_closes, index_closes, etf_amount_by_index

    min_target: datetime.date = min(target_dates)
    lookback_start: datetime.date = recent_trading_day_cutoff(
        LOOKBACK_TRADING_DAYS, ref=min_target
    )
    lookback_start_ts = pd.Timestamp(lookback_start)

    def _apply(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        return df[df["date"] >= lookback_start_ts].copy()

    sub_filtered = _apply(subject_closes)
    idx_filtered = _apply(index_closes)
    etf_filtered = _apply(etf_amount_by_index)

    print(
        f"    [lookback] filter to dates >= {lookback_start} "
        f"({LOOKBACK_TRADING_DAYS} trading days before {min_target}): "
        f"subjects {len(sub_filtered):,}/{len(subject_closes):,} "
        f"benchmarks {len(idx_filtered):,}/{len(index_closes):,} "
        f"etf_amounts {len(etf_filtered):,}/{len(etf_amount_by_index):,}",
        flush=True,
    )

    return sub_filtered, idx_filtered, etf_filtered
