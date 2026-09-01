"""PE-side compute logic for analyze.pe_and_dividends.

Covers:
  - pe_ma20: 20-trading-day rolling mean of PE per code
  - monthly 5y rolling min/max of PE (month-end rows for
    analysis.pe_and_dividend_stats)
"""
from __future__ import annotations

import pandas as pd

from _common.df_utils import to_dt64
from _common.df_utils.rolling import grouped_rolling_agg
from analyze.pe_and_dividends.config import PE_MA_WINDOW, ROLLING_5Y_DAYS


# ---------------------------------------------------------------------------
#  pe_ma20 — 20-day rolling mean of PE per code (index/etf/stock)
# ---------------------------------------------------------------------------
def compute_pe_ma20(df: pd.DataFrame) -> pd.Series:
    """Compute 20-trading-day moving average of PE per code.

    INVALID-VALUE RULE: a PE of <= 0 (zero, negative, or NULL) is treated
    as missing data — 0 means "no earnings reported" and negative PE is a
    sign-flip that breaks the linear scale, so neither can be averaged
    meaningfully. PE <= 0 / NULL → NaN BEFORE the rolling mean, so:
      * The MA = mean of the non-NaN (genuinely positive) PE values in
        the window (pandas .mean() skipna=True default).
      * The MA column is MASKED to NaN on days where the source PE is
        NaN (<= 0 or NULL) — i.e. on a no-earnings / null-PE day the MA
        is also NULL (entry val NULL for invalid data), but the MA on
        the next valid day still correctly uses the rolling window of
        positive PE values.
    Mirrors the rz_balance 0/NULL→NaN rule in analyze.margins.compute
    ("skip the date as a holiday; denominator does not count for null").

    Args:
        df: DataFrame with columns code, date, pe (index / etf / stock
            PE source — for index from stats.index_valuation.pe, for
            etf / stock pre-computed by builds.etf / builds.stock).

    Returns:
        Series aligned to df's index with pe_ma20 values. NaN where the
        source PE is invalid (<=0 / NULL) OR where the rolling window
        contains no valid PE values.
    """
    if df.empty:
        return pd.Series(dtype=float)
    df = df.sort_values(["code", "date"]).reset_index(drop=True)
    # Clean: PE <= 0 or NULL → NaN. A 0 / negative PE is meaningless for
    # a moving average (no earnings / sign-flip). Treated as missing.
    # where(bool Series) — where(callable) has no cuDF fast path.
    pe_num = pd.to_numeric(df["pe"], errors="coerce")
    pe_clean = pe_num.where(pe_num > 0)
    df = df.assign(__pe_clean=pe_clean)
    # groupby(sort=False) rolling preserves row order, so after the
    # reset_index the result index is already df.index — no reindex.
    result = (
        df.groupby("code", sort=False)["__pe_clean"]
        .rolling(window=PE_MA_WINDOW, min_periods=1)
        .mean()
        .reset_index(level=0, drop=True)
    )
    # Mask: NULL the MA on days where source PE is invalid (NaN).
    # The rolling mean skips NaN source values, so on an invalid-PE day
    # the MA still gets a value (mean of last 20 valid PEs). Per spec
    # ("entry val NULL if zero or null data"), mask the MA output to NaN
    # on invalid-PE days.
    return result.where(pe_clean.notna())


# ---------------------------------------------------------------------------
#  Monthly 5y rolling min/max of PE (for analysis.pe_and_dividend_stats)
# ---------------------------------------------------------------------------
def compute_monthly_pe_extremes(
    pe_df: pd.DataFrame,
    month_end_ts: pd.DatetimeIndex,
) -> pd.DataFrame:
    """Compute 5y rolling min/max of daily PE, filtered to month-end dates.

    Args:
        pe_df: DataFrame with columns code, date (datetime64), pe — the
            full daily PE history (the 5y rolling window needs it).
        month_end_ts: DatetimeIndex of month-end trading dates
            (datetime64[us]).

    Returns:
        DataFrame with columns code, date, min_pe_5y, max_pe_5y — one row
        per (code, month-end date) present in pe_df.
    """
    if pe_df is None or pe_df.empty:
        return pd.DataFrame(
            columns=["code", "date", "min_pe_5y", "max_pe_5y"]
        )

    # Full daily PE for rolling computation (5y window needs history)
    pe_daily = pe_df[["code", "date", "pe"]].copy()
    pe_daily["date"] = to_dt64(pe_daily["date"])
    pe_daily = pe_daily.sort_values(["code", "date"]).reset_index(drop=True)
    pe_daily["min_pe_5y"] = grouped_rolling_agg(
        pe_daily, "code", "pe",
        window=ROLLING_5Y_DAYS, min_periods=1, agg="min", sort=False,
    )
    pe_daily["max_pe_5y"] = grouped_rolling_agg(
        pe_daily, "code", "pe",
        window=ROLLING_5Y_DAYS, min_periods=1, agg="max", sort=False,
    )
    # Filter to month-end dates
    return pe_daily[pe_daily["date"].isin(month_end_ts)][
        ["code", "date", "min_pe_5y", "max_pe_5y"]
    ]
