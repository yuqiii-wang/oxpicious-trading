"""Internal holiday / non-trading-day risk step for analyze.mov_ave_spread.

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

This module is an INTERNAL step of analyze.mov_ave_spread — it is invoked
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

import time
from datetime import date, timedelta
from typing import Optional, Set

import numpy as np
import pandas as pd

from _common._holidays_and_weekdays import (
    CN_ADJUSTED_WORKDAYS,
    CN_HOLIDAYS,
    is_trading_day,
)
from _common.build_commons import (
    truncate_table_async,
    find_missing_analysis_dates,
)
from analyze._common import (
    build_and_insert_chunked,
    upsert_analysis_identity,
    sanitize_for_db_insert,
)
from analyze.mov_ave_spread.config import (
    HOLIDAY_ANALYSIS_NAME,
    HOLIDAY_COLUMNS,
    HOLIDAY_DESCRIPTION,
    HOLIDAY_TABLE,
    SEC_TYPES,
    SEC_TYPE_IDENTITY_TABLE,
)


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
    days = (end - start).days + 1
    dates = [start + timedelta(days=i) for i in range(days)]

    df = pd.DataFrame({"date": dates})
    df["is_trading"] = df["date"].apply(is_trading_day)
    df["is_holiday"] = df["date"].isin(CN_HOLIDAYS)
    # is_weekend: D is a weekend (Sat/Sun) that was NOT an adjusted workday.
    # Independent of is_holiday — a day can be both weekend AND holiday
    # (e.g., a holiday that falls on a Saturday).
    df["is_weekend"] = (
        df["date"].apply(lambda d: d.weekday() >= 5)
        & ~df["date"].isin(CN_ADJUSTED_WORKDAYS)
    )

    # Consecutive non-trading day streak (ending on each date).
    # We track the current streak length and reset on trading days.
    is_trading_arr = df["is_trading"].values
    streak = np.zeros(len(df), dtype=np.int32)
    current = 0
    for i in range(len(df)):
        if is_trading_arr[i]:
            current = 0
        else:
            current += 1
        streak[i] = current
    df["non_trading_streak"] = streak

    # Identify long holiday periods: any non-trading streak >= threshold
    # that contains at least one CN_HOLIDAY date. Mark all days in that
    # streak as is_long_holiday = True.
    holiday_arr = df["is_holiday"].values
    is_long = np.zeros(len(df), dtype=bool)

    # We need to identify streaks that are "long holidays":
    # Walk through the data, tracking each non-trading streak's start/end
    # and whether it contains a holiday.
    i = 0
    n = len(df)
    while i < n:
        if is_trading_arr[i]:
            i += 1
            continue
        # Start of a non-trading streak
        streak_start = i
        has_holiday = False
        while i < n and not is_trading_arr[i]:
            if holiday_arr[i]:
                has_holiday = True
            i += 1
        streak_end = i  # exclusive end
        streak_len = streak_end - streak_start
        if streak_len >= LONG_HOLIDAY_MIN_DAYS and has_holiday:
            is_long[streak_start:streak_end] = True

    df["is_long_holiday"] = is_long
    return df


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
    to the source DataFrame via a map (O(1) per row).
    """
    if df.empty:
        return df

    df = df.sort_values(["sec_type", "code", "date"]).reset_index(drop=True)

    min_date = df["date"].min()
    max_date = df["date"].max()
    calendar_df = _build_calendar_df(min_date, max_date)

    # Build a date → (is_trading, is_weekend, is_holiday, is_long_holiday,
    # non_trading_streak) lookup.
    cal_indexed = calendar_df.set_index("date")
    cal_lookup = cal_indexed[
        ["is_trading", "is_weekend", "is_holiday",
         "is_long_holiday", "non_trading_streak"]
    ].to_dict("index")

    # Previous calendar day for each row.
    df["_prev_date"] = df["date"] - timedelta(days=1)

    # Map each row's prev_date to its classification.
    # We use .map() with a dict for O(1) lookup per row.
    prev_trading = df["_prev_date"].map(
        lambda d: cal_lookup.get(d, {}).get("is_trading", False)
    )
    prev_weekend = df["_prev_date"].map(
        lambda d: cal_lookup.get(d, {}).get("is_weekend", False)
    )
    prev_holiday = df["_prev_date"].map(
        lambda d: cal_lookup.get(d, {}).get("is_holiday", False)
    )
    prev_long_holiday = df["_prev_date"].map(
        lambda d: cal_lookup.get(d, {}).get("is_long_holiday", False)
    )
    prev_streak = df["_prev_date"].map(
        lambda d: cal_lookup.get(d, {}).get("non_trading_streak", 0)
    )

    df["is_prev_day_trading"] = prev_trading.fillna(False).astype(bool)
    df["is_prev_day_weekend"] = prev_weekend.fillna(False).astype(bool)
    df["is_prev_day_holiday"] = prev_holiday.fillna(False).astype(bool)
    df["is_prev_day_long_holiday"] = prev_long_holiday.fillna(False).astype(bool)
    df["non_trading_day_count"] = prev_streak.fillna(0).astype(int)

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
#  Pipeline (internal step — invoked from mov_ave_spread.__main__)
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

    Pipeline
      1. Determine target dates (per-sec_type) by checking missing dates
         in analysis.mov_ave_rsi_holiday against source identity tables.
         In force mode, truncate the table instead.
      2. Build the calendar and compute all holiday metrics over the
         FULL date range, then filter to target_dates.
      3. Upsert into analysis.mov_ave_rsi_holiday (chunked by date).
      4. Upsert analysis.analysis_identity registry.

    Args:
      conn: asyncpg connection (reused from parent).
      df: source DataFrame with at least columns [sec_type, code, date,
          price, open, high, low]. Must be the FULL per-code history.
      force: when True, truncate analysis.mov_ave_rsi_holiday first and
             recompute all rows.
      pool: optional connection pool for parallel upsert chunks.
      max_concurrent: max parallel upsert chunks.
      sec_type: when provided, process only this sec_type.
    """
    t0 = time.time()
    print("\n" + "=" * 78, flush=True)
    print("  MOV_AVE_RSI_HOLIDAY (internal step of mov_ave_spread)", flush=True)
    print("=" * 78, flush=True)

    # Select only the columns holiday needs.
    needed_cols = ["sec_type", "code", "date", "price", "open", "high", "low"]
    available = [c for c in needed_cols if c in df.columns]
    holiday_df = df[available].copy()

    if holiday_df.empty:
        print("    -> no source data; skipping holiday step.", flush=True)
        return

    if sec_type is not None:
        sec_types = (sec_type,)
    else:
        sec_types = tuple(sorted(holiday_df["sec_type"].unique()))

    # ---- Step 0: determine target dates (per-sec_type) --------------
    if code_filter is not None:
        # Single-code mode (--code): the caller already DELETEd this
        # code's rows from the table, so compute ALL dates for this code
        # and bypass the per-sec_type skip-filter (sec_types=() at the
        # insert below keeps every row — dates covered by OTHER codes
        # would otherwise mask this code's gaps).
        print("    mode: SINGLE-CODE (full recompute for this code)",
              flush=True)
        target_dates_union: Optional[Set] = None
    elif force:
        print("    mode: FORCE (full recompute)", flush=True)
        print("\n[h0/3] Force mode: truncating mov_ave_rsi_holiday...",
              flush=True)
        await truncate_table_async(conn, HOLIDAY_TABLE)
        target_dates_union: Optional[Set] = None
        print("    -> truncated; will recompute all rows", flush=True)
    else:
        print("    mode: incremental (missing dates only)", flush=True)
        print("\n[h0/3] Detecting missing dates PER-sec_type "
              "(etf_identity vs holiday[etf], etc.)...",
              flush=True)
        target_dates_per_st: dict = {}
        for st in sec_types:
            td_st = await find_missing_analysis_dates(
                conn, HOLIDAY_TABLE,
                [SEC_TYPE_IDENTITY_TABLE[st]], sec_type=st,
            )
            target_dates_per_st[st] = td_st
            print(f"    -> {st}: {len(td_st)} missing dates", flush=True)
        target_dates_union = set()
        for s in target_dates_per_st.values():
            target_dates_union |= s
        print(f"    -> union across sec_types: "
              f"{len(target_dates_union)} dates to (re)compute",
              flush=True)
        if not target_dates_union:
            print("    -> DB is up to date; nothing to do.", flush=True)
            return

    # ---- Step 1: compute holiday metrics over full history ----------
    print("\n[h1/3] Computing holiday metrics (calendar classification + "
          "today gaps) per (sec_type, code, date) over full history...",
          flush=True)
    holiday_df = compute_holiday_metrics(holiday_df)

    if target_dates_union is not None and len(target_dates_union) > 0:
        n_before = len(holiday_df)
        holiday_df = holiday_df[
            holiday_df["date"].isin(target_dates_union)
        ].reset_index(drop=True)
        print(f"    -> incremental filter: {len(holiday_df):,} of "
              f"{n_before:,} rows are in target_dates_union", flush=True)

    if holiday_df.empty:
        print("    -> no rows to upsert; skipping holiday upsert.", flush=True)
        return

    # ---- Step 2: build + insert (chunked by date) -------------------
    print(f"\n[h2/3] Building + inserting {len(holiday_df):,} "
          f"mov_ave_rsi_holiday rows in date-bounded chunks "
          f"({'COPY' if force else 'upsert'} per chunk)...", flush=True)
    n = await build_and_insert_chunked(
        conn, pool, holiday_df,
        sanitize_holiday_rows,
        table_name=HOLIDAY_TABLE,
        key_columns=["sec_type", "code", "date"],
        force=force,
        sec_types=() if code_filter is not None else sec_types,
        max_concurrent=max_concurrent,
        label="mov_ave_rsi_holiday",
    )
    del holiday_df
    print(f"    -> inserted {n:,} rows", flush=True)

    # ---- Step 3: register in analysis_identity ----------------------
    print(f"\n[h3/3] Upserting analysis.analysis_identity registry...",
          flush=True)
    await upsert_analysis_identity(
        conn,
        name=HOLIDAY_ANALYSIS_NAME,
        detail_name="mov_ave_rsi_holiday",
        description=HOLIDAY_DESCRIPTION,
    )

    print(f"\n  mov_ave_rsi_holiday wall time: {time.time() - t0:.1f}s",
          flush=True)