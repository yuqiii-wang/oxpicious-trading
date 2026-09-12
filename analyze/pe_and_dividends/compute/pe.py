"""PE-side compute logic for analyze.pe_and_dividends.

Covers:
  - pe: raw PE with the invalid-value rule applied (PE <= 0 / NULL → NaN)
  - monthly 5y rolling min/max of PE (month-end rows for
    analysis.pe_and_dividend_stats)
"""
from __future__ import annotations

import pandas as pd

from _common.df_utils import to_dt64
from _common.df_utils.rolling import grouped_rolling_agg
from analyze.pe_and_dividends.config import ROLLING_5Y_DAYS


# ---------------------------------------------------------------------------
#  pe — raw PE per code (index/etf/stock), invalid values masked
# ---------------------------------------------------------------------------
def clean_pe(df: pd.DataFrame) -> pd.Series:
    """Return the RAW PE series with invalid values masked to NaN.

    INVALID-VALUE RULE: a PE of <= 0 (zero, negative, or NULL) is treated
    as missing data — 0 means "no earnings reported" and negative PE is a
    sign-flip that breaks the linear scale. PE <= 0 / NULL → NaN, so the
    stored column is strictly positive or NULL ("entry val NULL for
    invalid data").
    Mirrors the rz_balance 0/NULL→NaN rule in analyze.margins.compute
    ("skip the date as a holiday; denominator does not count for null").

    Args:
        df: DataFrame with a ``pe`` column (index / etf / stock PE source
            — for index from stats.index_valuation.pe, for etf / stock
            pre-computed by builds.etf / builds.stock).

    Returns:
        Series aligned to df's index with cleaned pe values (NaN where
        the source PE is invalid).
    """
    if df.empty:
        return pd.Series(dtype=float)
    # Clean: PE <= 0 or NULL → NaN. A 0 / negative PE is meaningless
    # (no earnings / sign-flip). Treated as missing.
    # where(bool Series) — where(callable) has no cuDF fast path.
    pe_num = pd.to_numeric(df["pe"], errors="coerce")
    return pe_num.where(pe_num > 0)


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
