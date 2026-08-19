"""Date utilities for forecast decisions.

- future_trading_dates: compute n future business days skipping weekends
- _compute_required_columns: derive algo REQUIRED_COLUMNS (MA20/MA60, std)
  from a close_price series for the combined actual+forecast DataFrame
"""
from __future__ import annotations

import datetime
from typing import List

import numpy as np
import pandas as pd


def future_trading_dates(
    forecast_date: datetime.date, n_days: int,
) -> List[datetime.date]:
    """Compute n future trading dates (skipping weekends)."""
    dates: List[datetime.date] = []
    d = forecast_date
    for _ in range(n_days):
        d += datetime.timedelta(days=1)
        while d.weekday() >= 5:  # Sat=5, Sun=6
            d += datetime.timedelta(days=1)
        dates.append(d)
    return dates


def compute_required_columns(
    df: pd.DataFrame, required_columns: tuple,
) -> pd.DataFrame:
    """Compute the algo's REQUIRED_COLUMNS from close_price on the combined
    actual+forecast DataFrame.

    For Bollinger Bands: computes MA20/MA60, price_vs_ma20/price_vs_ma60,
    std_20days/std_60days from close_price. The 255d actual history provides
    ample warmup so all windows are fully populated for the 20 forecast days.

    For MACD: REQUIRED_COLUMNS is empty — nothing to compute (the algo
    computes EMAs internally from close_price).
    """
    if not required_columns:
        return df
    close = df["close_price"]
    df = df.copy()
    if "price_vs_ma20" in required_columns or "std_20days" in required_columns:
        ma20 = close.rolling(20, min_periods=1).mean()
        df["price_vs_ma20"] = (close - ma20) / ma20.where(ma20 != 0, np.nan)
        df["std_20days"] = close.rolling(20, min_periods=2).std()
    if "price_vs_ma60" in required_columns or "std_60days" in required_columns:
        ma60 = close.rolling(60, min_periods=1).mean()
        df["price_vs_ma60"] = (close - ma60) / ma60.where(ma60 != 0, np.nan)
        df["std_60days"] = close.rolling(60, min_periods=2).std()
    return df
