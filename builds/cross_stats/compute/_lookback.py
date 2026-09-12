"""Incremental-mode lookback pre-filter (pair grain).

Trims source frames to the lookback window (max corr window) before the
earliest target date — ~10-15x smaller cross product for single-date
rebuilds while keeping trailing windows correct.
"""
from __future__ import annotations

import datetime
from typing import Set, Tuple

import numpy as np
import pandas as pd

from _common._holidays_and_weekdays import recent_trading_day_cutoff
from builds.cross_stats.config import CORR_WINDOWS

import logging
logger = logging.getLogger(__name__)

_MAX_CORR_WINDOW: int = max(CORR_WINDOWS)  # 255 trading days
LOOKBACK_TRADING_DAYS: int = _MAX_CORR_WINDOW  # (the former +5 MA5 buffer
# for the etf ratio MA5 went away with the ETF amount columns, 2026-09-07)


def filter_dataframes_for_lookback(
    subject_closes: pd.DataFrame,
    index_closes: pd.DataFrame,
    target_dates: Set[datetime.date],
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Filter frames to dates >= (LOOKBACK_TRADING_DAYS before earliest
    target). Empty target set → unchanged (full-recompute mode).

    Comparison via np.datetime64 scalar: frame date columns may be
    datetime64[s] and a bare datetime.date cannot compare against them,
    while a pd.Timestamp proxy scalar costs a cudf fallback pair.
    """
    if not target_dates:
        return subject_closes, index_closes

    min_target: datetime.date = min(target_dates)
    lookback_start: datetime.date = recent_trading_day_cutoff(
        LOOKBACK_TRADING_DAYS, ref=min_target
    )
    lookback_start_ts = np.datetime64(lookback_start)

    def _apply(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        return df[df["date"] >= lookback_start_ts].copy()

    sub_filtered = _apply(subject_closes)
    idx_filtered = _apply(index_closes)

    logger.info(
        f"    [lookback] filter to dates >= {lookback_start} "
        f"({LOOKBACK_TRADING_DAYS} trading days before {min_target}): "
        f"subjects {len(sub_filtered):,}/{len(subject_closes):,} "
        f"benchmarks {len(idx_filtered):,}/{len(index_closes):,}",
    )

    return sub_filtered, idx_filtered
