"""Shared trading-calendar roll helpers for news-date analysis.

The news text pipelines (analysis.news_audit coverage grain,
analysis.news_text_daily) align calendar-dated articles onto the SSE
Composite trading calendar: a weekend/holiday article rolls FORWARD to
the next trading day (its information is first tradable there —
T+1-compatible). Dates ride as int64 day numbers since the unix epoch
through the compute; the python-date conversion happens once at the
SQL-arg / write boundary.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

SECONDS_PER_DAY = 86_400.0
_EPOCH = date(1970, 1, 1)


def day_numbers(date_epoch: pd.Series) -> pd.Series:
    """Epoch-seconds column -> int64 day numbers since the unix epoch."""
    return (date_epoch // SECONDS_PER_DAY).astype("int64")


def day_number_to_date(day: int) -> date:
    """int64 day number -> python date (SQL args + the write boundary)."""
    return _EPOCH + timedelta(days=int(day))


def roll_to_calendar(
    df: pd.DataFrame, cal_days: np.ndarray, day_col: str = "day"
) -> pd.DataFrame:
    """Roll each row's calendar day FORWARD to the next trading day.

    THE SANCTIONED HOST CORE: searchsorted gather on the unwrapped host
    day arrays (a positional gather with no cuDF-native form); the
    rolled day numbers come back as one int64 column. Rows beyond the
    calendar's end (an article dated after the last known trading day)
    drop out and reappear on the next run once the calendar extends.
    """
    from _common.df_utils import host_array

    art_days = host_array(df[day_col]).astype("int64")
    pos = cal_days.searchsorted(art_days, side="left")
    ok = pos < len(cal_days)
    rolled = cal_days[pos[ok]]
    out = df.iloc[ok].copy()
    out[day_col] = rolled
    return out
