"""Internal holiday / non-trading-day risk step for analysis.mov_ave_spread.

For each trading day D in the source data, captures:
  - Whether the previous calendar day (D-1) was a trading day
  - Whether D-1 was a weekend (non-trading Sat/Sun)
  - Whether D-1 was an official Chinese public holiday
  - Whether D-1 was part of a long holiday period (>= 3 consecutive
    non-trading days including at least one official holiday)
  - The count of consecutive non-trading days ending on D-1
  - Today's intraday high-low gap: (high - low) / close
  - Today's intraday open-close gap: (close - open) / open

One row per (sec_type, code, date) in analysis.mov_ave_rsi_holiday.

Source: same DataFrame as the mov_ave_spread parent pipeline (price,
open, high, low columns — no second DB round-trip). Holiday classification
uses the project calendar in _common._holidays_and_weekdays (CN_HOLIDAYS
+ CN_ADJUSTED_WORKDAYS, 2020-2026).

This module is an INTERNAL step of analysis.mov_ave_spread — it is invoked
from __main__.py after the RSI step has repopulated analysis.mov_ave_rsi,
reusing the same DB connection + source DataFrame. It is NOT a standalone
runnable.

Incremental mode (force=False):
  Only dates present in source identity tables but NOT yet in
  analysis.mov_ave_rsi_holiday are (re)computed and upserted. The missing-
  date check is PER-sec_type.

Force mode (force=True):
  Truncate analysis.mov_ave_rsi_holiday, then recompute and insert all
  rows for the active universe.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from _common._holidays_and_weekdays import (
    CN_ADJUSTED_WORKDAYS,
    CN_HOLIDAYS,
    is_trading_day,
)
from _common.df_utils import host_array
from analysis._common import sanitize_for_db_insert
from analysis.mov_ave_spread._common import run_table_step
from analysis.mov_ave_spread.config import (
    HOLIDAY_ANALYSIS_NAME,
    HOLIDAY_COLUMNS,
    HOLIDAY_DESCRIPTION,
    HOLIDAY_TABLE,
)

import logging
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
#  Calendar builder
# ---------------------------------------------------------------------------

# Threshold for "long holiday": a consecutive non-trading period must
# contain at least this many non-trading calendar days AND include at
# least one official CN_HOLIDAY date. 3 captures Spring Festival (7+ days),
# Golden Week (7 days), Labor Day (5 days), and also Tomb Sweeping /
# Dragon Boat / Mid-Autumn when they fall adjacent to a weekend (3 days).
LONG_HOLIDAY_MIN_DAYS = 3

# Buffer calendar days before / after the source date range to ensure
# correct previous-day classification for the first source date.
CALENDAR_BUFFER_DAYS = 14


def _build_calendar_df(
    min_date: date, max_date: date,
) -> pd.DataFrame:
    """Build a per-calendar-day classification DataFrame.

    For every calendar date D in [min_date - buffer, max_date], computes:
      - is_trading: is D a trading day?
      - is_weekend: is D a non-trading weekend day?
      - is_holiday: is D an official CN_HOLIDAY?
      - non_trading_streak: how many consecutive non-trading days
        ending on D (0 if D is a trading day)

    Long holiday periods are identified after the streak computation:
    any non-trading streak >= LONG_HOLIDAY_MIN_DAYS that contains at
    least one CN_HOLIDAY date is marked as a "long holiday" period,
    and all days in that streak get is_long_holiday = True.

    Returns a DataFrame with columns:
      date, is_trading, is_weekend, is_holiday, is_long_holiday,
      non_trading_streak
    """
    start = min_date - timedelta(days=CALENDAR_BUFFER_DAYS)
    end = max_date + timedelta(days=CALENDAR_BUFFER_DAYS)
    n = (end - start).days + 1
    # Plain python dates — ALL arithmetic below is host-pure. (The former
    # implementation passed proxy Timestamps in and built ``dates`` with
    # ``start + timedelta(days=i)`` — one _Timestamp.__add__ cudf fallback
    # per calendar day, ~2,460 per stock-scale run.)
    dates = [start + timedelta(days=i) for i in range(n)]

    # Host-pure classification via python set membership / function calls
    # (the former ``df["date"].apply(...)`` dispatched through cudf's
    # UDF-JIT attempt and fell back per call).
    is_trading_arr = np.fromiter(
        (is_trading_day(d) for d in dates), dtype=bool, count=n
    )
    is_holiday_arr = np.fromiter(
        (d in CN_HOLIDAYS for d in dates), dtype=bool, count=n
    )
    # is_weekend: D is a weekend (Sat/Sun) that was NOT an adjusted workday.
    # Independent of is_holiday — a day can be both weekend AND holiday
    # (e.g., a holiday that falls on a Saturday).
    is_weekend_arr = np.fromiter(
        (d.weekday() >= 5 and d not in CN_ADJUSTED_WORKDAYS for d in dates),
        dtype=bool, count=n,
    )

    # Consecutive non-trading day streak (ending on each date).
    # We track the current streak length and reset on trading days.
    streak = np.zeros(n, dtype=np.int32)
    current = 0
    for i in range(n):
        if is_trading_arr[i]:
            current = 0
        else:
            current += 1
        streak[i] = current

    # Identify long holiday periods: any non-trading streak >= threshold
    # that contains at least one CN_HOLIDAY date. Mark all days in that
    # streak as is_long_holiday = True.
    is_long = np.zeros(n, dtype=bool)

    # We need to identify streaks that are "long holidays":
    # Walk through the data, tracking each non-trading streak's start/end
    # and whether it contains a holiday.
    i = 0
    while i < n:
        if is_trading_arr[i]:
            i += 1
            continue
        # Start of a non-trading streak
        streak_start = i
        has_holiday = False
        while i < n and not is_trading_arr[i]:
            if is_holiday_arr[i]:
                has_holiday = True
            i += 1
        streak_end = i  # exclusive end
        streak_len = streak_end - streak_start
        if streak_len >= LONG_HOLIDAY_MIN_DAYS and has_holiday:
            is_long[streak_start:streak_end] = True

    # datetime64[ns] date column from the start — the merge key needs no
    # object-dtype ``pd.to_datetime`` conversion (a cudf fallback source).
    return pd.DataFrame({
        "date": np.array(dates, dtype="datetime64[D]").astype("datetime64[ns]"),
        "is_trading": is_trading_arr,
        "is_weekend": is_weekend_arr,
        "is_holiday": is_holiday_arr,
        "non_trading_streak": streak,
        "is_long_holiday": is_long,
    })


# ---------------------------------------------------------------------------
#  Compute helpers
# ---------------------------------------------------------------------------

def compute_holiday_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Add holiday metric columns to the source DataFrame.

    For each (sec_type, code, date) row with trading day D:
      - Compute prev_date = D - 1 (calendar day)
      - Look up prev_date's classification from the calendar
      - Compute today_high_low_gap and today_open_close_gap from OHLC

    The calendar is built once for the full date range, then joined
    to the source DataFrame via ONE left-merge on the previous calendar
    day (vectorized — no per-row dict lookups under cudf.pandas).
    """
    if df.empty:
        return df

    df = df.sort_values(["sec_type", "code", "date"]).reset_index(drop=True)

    # Host-pure min/max: Series.min()/max() return proxy Timestamps whose
    # timedelta arithmetic in _build_calendar_df falls back per call.
    dates_host = host_array(df["date"].to_numpy())
    min_date: date = dates_host.min().astype("datetime64[D]").item()
    max_date: date = dates_host.max().astype("datetime64[D]").item()
    calendar_df = _build_calendar_df(min_date, max_date)

    # Previous calendar day for each row (datetime64-normalized so the
    # merge key matches the calendar's date column regardless of the
    # source date dtype). int64-seconds arithmetic — datetime64 -
    # Timedelta is a cuDF fast-path fallback.
    df["_prev_date"] = pd.to_datetime(
        df["date"].astype("datetime64[s]").astype("int64") - 86400,
        unit="s",
    )

    # One left-merge on _prev_date: the calendar's classification
    # columns are renamed directly to the output names (rows whose
    # prev day predates the buffered calendar stay NaN → filled below).
    cal = calendar_df.rename(columns={
        "date": "_prev_date",
        "is_trading": "is_prev_day_trading",
        "is_weekend": "is_prev_day_weekend",
        "is_holiday": "is_prev_day_holiday",
        "is_long_holiday": "is_prev_day_long_holiday",
        "non_trading_streak": "non_trading_day_count",
    })
    cal["_prev_date"] = pd.to_datetime(cal["_prev_date"])
    df = df.merge(
        cal[[
            "_prev_date", "is_prev_day_trading", "is_prev_day_weekend",
            "is_prev_day_holiday", "is_prev_day_long_holiday",
            "non_trading_day_count",
        ]],
        on="_prev_date", how="left",
    )

    df["is_prev_day_trading"] = (
        df["is_prev_day_trading"].fillna(False).astype(bool)
    )
    df["is_prev_day_weekend"] = (
        df["is_prev_day_weekend"].fillna(False).astype(bool)
    )
    df["is_prev_day_holiday"] = (
        df["is_prev_day_holiday"].fillna(False).astype(bool)
    )
    df["is_prev_day_long_holiday"] = (
        df["is_prev_day_long_holiday"].fillna(False).astype(bool)
    )
    df["non_trading_day_count"] = (
        df["non_trading_day_count"].fillna(0).astype(int)
    )

    # Today's intraday gaps.
    # today_high_low_gap = (high - low) / close
    high = df["high"]
    low = df["low"]
    close = df["price"]  # close is stored as "price" in the source
    gap_hl = (high - low) / close
    # NULL when close is near-zero or non-finite
    gap_hl_mask = close.isna() | (close.abs() < 1e-12) | ~np.isfinite(gap_hl)
    df["today_high_low_gap"] = gap_hl.where(~gap_hl_mask, 0.0)
    # Clamp to NUMERIC(18,4) range (|value| < 10^14 after rounding to 4dp).
    # Realistic max for a single day: (2x price) / price = 2.0, so this is
    # purely a safety net.
    df["today_high_low_gap"] = df["today_high_low_gap"].clip(-1e10, 1e10)

    # today_open_close_gap = (close - open) / open
    open_px = df["open"]
    gap_oc = (close - open_px) / open_px
    gap_oc_mask = open_px.isna() | (open_px.abs() < 1e-12) | ~np.isfinite(gap_oc)
    df["today_open_close_gap"] = gap_oc.where(~gap_oc_mask, 0.0)
    df["today_open_close_gap"] = df["today_open_close_gap"].clip(-1e10, 1e10)

    df = df.drop(columns=["_prev_date"])
    return df


def sanitize_holiday_rows(df: pd.DataFrame) -> list[dict]:
    """Select the mov_ave_rsi_holiday columns and sanitize for DB insert.

    The boolean and integer columns are NOT NULL with defaults, so they
    are always present. The gap columns (NUMERIC(18,4)) are also NOT NULL
    DEFAULT 0.0. We sanitize numeric columns (the two gap ratios) for
    NaN/inf → None conversion.
    """
    if df.empty:
        return []

    out_cols = list(HOLIDAY_COLUMNS)
    out = df[out_cols].copy()

    # Numeric columns that need sanitization (the two gap ratios).
    non_numeric = ("sec_type", "code", "date",
                   "is_prev_day_trading", "is_prev_day_weekend",
                   "is_prev_day_holiday", "is_prev_day_long_holiday",
                   "non_trading_day_count")
    numeric_cols = [c for c in out_cols if c not in non_numeric]

    # Round gap columns to 4 decimal places (NUMERIC(18,4)).
    if numeric_cols:
        out[numeric_cols] = out[numeric_cols].round(4)

    return sanitize_for_db_insert(out, numeric_cols=numeric_cols)


# ---------------------------------------------------------------------------
#  Pipeline (internal step — invoked from mov_ave_spread.__main__; the
#  skeleton lives in _common.steps.run_table_step)
# ---------------------------------------------------------------------------

async def run_holiday(
    conn,
    df: pd.DataFrame,
    *,
    force: bool = False,
    pool=None,
    max_concurrent: int = 20,
    sec_type: str | None = None,
    code_filter: str | None = None,
) -> None:
    """Run the holiday / non-trading-day risk pipeline.

    Reuses the caller's DB connection and source DataFrame (the ``price``,
    ``open``, ``high``, ``low`` columns are reused — no second DB fetch).
    The DataFrame must contain the FULL per-code history for correct
    previous-day classification (the calendar is built once for the full
    date range).

    Pipeline (see _common.steps.run_table_step)
      0. Determine target dates (per-sec_type) by checking missing dates
         in analysis.mov_ave_rsi_holiday against source identity tables.
         In force mode, purge the table scope instead.
      1. Build the calendar and compute all holiday metrics over the
         FULL date range, then filter to target_dates.
      2. Upsert into analysis.mov_ave_rsi_holiday (chunked by date).
      3. Upsert analysis.analysis_identity registry.
    """
    await run_table_step(
        conn, df,
        title="MOV_AVE_RSI_HOLIDAY (internal step of mov_ave_spread)",
        tag="h",
        table=HOLIDAY_TABLE,
        analysis_name=HOLIDAY_ANALYSIS_NAME,
        detail_name="mov_ave_rsi_holiday",
        description=HOLIDAY_DESCRIPTION,
        needed_cols=["sec_type", "code", "date", "price", "open", "high",
                     "low"],
        compute_fn=compute_holiday_metrics,
        sanitize_fn=sanitize_holiday_rows,
        force=force,
        pool=pool,
        max_concurrent=max_concurrent,
        sec_type=sec_type,
        code_filter=code_filter,
    )
