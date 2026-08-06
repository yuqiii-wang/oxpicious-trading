"""Large-swings detection for analyze.mov_ave_spread.

Per (sec_type, code, date) row, detects "large swing" trading days —
days with big intraday moves, consistent multi-day trends, and sharp
trend reversals (big turns).

Slope rebasing
==============
The ``price_ma3_slope`` column is computed on a price series rebased
to 100 at each code's FIRST record date. This makes slopes comparable
across codes with different absolute price levels (a 1-unit slope on
the rebased series = a 1% move relative to the starting price).

For each (sec_type, code):
  1. rebase_factor = 100 / price[first_date]
  2. rebased_price = price * rebase_factor
  3. ma3 = rebased_price.rolling(3, min_periods=3).mean()
  4. price_ma3_slope = ma3.diff()   (1st derivative of MA3)

Gap percentages
===============
All gaps are expressed as FRACTIONS (0.05 = 5%) relative to
yesterday's close:
  - today_high_low_gap_pct   = (high  - low)  / prev_close
  - today_open_close_gap_pct = (close - open) / prev_close

Flags
=====
  - is_likely_trading_curbed: |today_open_close_gap_pct| > 9.5%
    (China A-share daily price limit is ±10%; >9.5% open-to-close
    gap strongly suggests the stock hit its limit.)

  - is_3day_consistent_trend: today's slope has the same sign as the
    previous 2 days' slopes AND |today_slope| > 1 (a meaningful move
    on the rebased scale: >1% of starting price in one day).

  - is_4day_consistent_trend: is_3day AND the 3rd prior day's slope
    also has the same sign.

  - is_5day_consistent_trend: is_4day AND the 4th prior day's slope
    also has the same sign.

  - is_big_turn: today's slope has the OPPOSITE sign of yesterday's
    slope AND a consistent 3/4/5-day trend existed THROUGH YESTERDAY
    (using the previous row's is_3/4/5day flags, shifted by 1) AND
    |today_open_close_gap_pct| > 5%.

    Financial meaning: the stock was trending consistently for 3+ days,
    then today reversed sharply (>5% open-to-close gap against the
    prior trend). Uses yesterday's consistent-trend flags because
    today's own flags would be False (today's slope is opposite of
    yesterday's, so today can't be part of a same-sign trend).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from analyze._common.rolling import grouped_rolling_agg
from analyze._common.sanitize import sanitize_for_db_insert
from analyze.mov_ave_spread.config import NUMERIC_MAX_ABS

# NUMERIC(9,6) holds values with |value| < 1000 after rounding to 6 dp.
_GAP_MAX_ABS = 1000.0

# MA3 window for the rebased price slope.
_MA3_WINDOW = 3

# |slope| threshold for "meaningful move" on the rebased-to-100 scale.
# 1 unit = 1% of the starting price — filters out noise.
_SLOPE_ABS_THRESHOLD = 1.0

# |today_open_close_gap_pct| thresholds.
_TRADING_CURBED_THRESHOLD = 0.095   # 9.5%
_BIG_TURN_GAP_THRESHOLD = 0.05      # 5%

_GRP_KEYS = ["sec_type", "code"]


def compute_large_swings(df: pd.DataFrame) -> list[dict]:
    """Compute large-swings rows from the source DataFrame.

    Args:
        df: Source DataFrame with at least columns: sec_type, code,
            date, price (close), open, high, low. Must be the FULL
            per-code history (not filtered to target_dates) so the
            rebase-to-100 and MA3/slope lookback computations are
            correct. Extra columns (ma5, slopes, stds, etc.) are
            ignored.

    Returns:
        List of dicts with keys: sec_type, code, date,
        price_ma3_slope, today_high_low_gap_pct,
        today_open_close_gap_pct, is_likely_trading_curbed,
        is_3day_consistent_trend, is_4day_consistent_trend,
        is_5day_consistent_trend, is_big_turn.
        Rows where any NOT NULL column is NaN or would overflow the
        column's NUMERIC range are filtered out (first ~3 rows per
        code where MA3/slope/prev_close are not yet available).
    """
    if df.empty:
        return []

    work = df.copy()
    work = work.sort_values(_GRP_KEYS + ["date"]).reset_index(drop=True)

    # ---- Rebase price to 100 at each code's first record date --------
    first_price = work.groupby(_GRP_KEYS, sort=False)["price"].transform("first")
    work["rebased_price"] = work["price"] / first_price * 100.0

    # ---- MA3 of rebased price ----------------------------------------
    work["ma3"] = grouped_rolling_agg(
        work, _GRP_KEYS, "rebased_price",
        window=_MA3_WINDOW, min_periods=_MA3_WINDOW, agg="mean",
        sort=False,
    )

    # ---- price_ma3_slope = MA3.diff() per code -----------------------
    work["price_ma3_slope"] = work.groupby(_GRP_KEYS, sort=False)["ma3"].diff()

    # ---- Previous close (yesterday's close) per code -----------------
    work["prev_close"] = work.groupby(_GRP_KEYS, sort=False)["price"].shift(1)

    # ---- Gap percentages (as fractions, relative to prev_close) ------
    work["today_high_low_gap_pct"] = (
        (work["high"] - work["low"]) / work["prev_close"]
    )
    work["today_open_close_gap_pct"] = (
        (work["price"] - work["open"]) / work["prev_close"]
    )

    # ---- is_likely_trading_curbed ------------------------------------
    work["is_likely_trading_curbed"] = (
        work["today_open_close_gap_pct"].abs() > _TRADING_CURBED_THRESHOLD
    ).fillna(False)

    # ---- Consistent trend flags --------------------------------------
    slope = work["price_ma3_slope"]
    slope_prev1 = work.groupby(_GRP_KEYS, sort=False)["price_ma3_slope"].shift(1)
    slope_prev2 = work.groupby(_GRP_KEYS, sort=False)["price_ma3_slope"].shift(2)
    slope_prev3 = work.groupby(_GRP_KEYS, sort=False)["price_ma3_slope"].shift(3)
    slope_prev4 = work.groupby(_GRP_KEYS, sort=False)["price_ma3_slope"].shift(4)

    sign_today = np.sign(slope)
    sign_prev1 = np.sign(slope_prev1)
    sign_prev2 = np.sign(slope_prev2)
    sign_prev3 = np.sign(slope_prev3)
    sign_prev4 = np.sign(slope_prev4)

    slope_abs_gt1 = slope.abs() > _SLOPE_ABS_THRESHOLD

    # 3-day: today + 2 prior all same sign (non-zero) AND |slope| > 1
    same_sign_3 = (
        (sign_today == sign_prev1)
        & (sign_today == sign_prev2)
        & (sign_today != 0)
    )
    same_sign_4 = same_sign_3 & (sign_today == sign_prev3)
    same_sign_5 = same_sign_4 & (sign_today == sign_prev4)

    work["is_3day_consistent_trend"] = (same_sign_3 & slope_abs_gt1).fillna(False)
    work["is_4day_consistent_trend"] = (same_sign_4 & slope_abs_gt1).fillna(False)
    work["is_5day_consistent_trend"] = (same_sign_5 & slope_abs_gt1).fillna(False)

    # ---- is_big_turn -------------------------------------------------
    # Today's slope has opposite sign of yesterday's slope, AND a
    # consistent 3/4/5-day trend existed THROUGH YESTERDAY (shifted
    # flags), AND |today_open_close_gap_pct| > 5%.
    opposite_sign = (sign_today * sign_prev1) < 0

    is_3day_yesterday = work.groupby(
        _GRP_KEYS, sort=False
    )["is_3day_consistent_trend"].shift(1).fillna(False)
    is_4day_yesterday = work.groupby(
        _GRP_KEYS, sort=False
    )["is_4day_consistent_trend"].shift(1).fillna(False)
    is_5day_yesterday = work.groupby(
        _GRP_KEYS, sort=False
    )["is_5day_consistent_trend"].shift(1).fillna(False)

    gap_gt_5pct = work["today_open_close_gap_pct"].abs() > _BIG_TURN_GAP_THRESHOLD

    work["is_big_turn"] = (
        opposite_sign
        & (is_3day_yesterday | is_4day_yesterday | is_5day_yesterday)
        & gap_gt_5pct
    ).fillna(False)

    # ---- Filter out rows with NaN/invalid/overflowing NOT NULL columns ----
    # First ~3 rows per code have NaN price_ma3_slope (MA3 needs 3 rows,
    # diff needs 2 MA3 values) and/or NaN gap_pct (no prev_close).
    # Also filter rows where open/high/low are NULL or <= 0 (some index
    # basic_stats only populate close, leaving OHLC as 0/NULL — gap pcts
    # computed from those are meaningless, e.g. (close-0)/prev_close).
    valid = (
        work["price_ma3_slope"].notna()
        & work["today_high_low_gap_pct"].notna()
        & work["today_open_close_gap_pct"].notna()
        & (work["open"] > 0)
        & (work["high"] > 0)
        & (work["low"] > 0)
        & (work["price_ma3_slope"].abs() < NUMERIC_MAX_ABS)
        & (work["today_high_low_gap_pct"].abs() < _GAP_MAX_ABS)
        & (work["today_open_close_gap_pct"].abs() < _GAP_MAX_ABS)
    )
    n_filtered = len(work) - int(valid.sum())
    if n_filtered > 0:
        print(f"    -> large_swings: filtered {n_filtered:,} of {len(work):,} "
              f"rows with NaN/overflowing NOT NULL columns (first ~3 rows "
              f"per code)", flush=True)

    out = work[valid].reset_index(drop=True)

    # ---- Select output columns + sanitize ----------------------------
    out_cols = [
        "sec_type", "code", "date",
        "price_ma3_slope",
        "today_high_low_gap_pct",
        "today_open_close_gap_pct",
        "is_likely_trading_curbed",
        "is_3day_consistent_trend",
        "is_4day_consistent_trend",
        "is_5day_consistent_trend",
        "is_big_turn",
    ]
    out_df = out[out_cols].copy()

    # Boolean columns: ensure Python bool (not numpy bool) for asyncpg.
    bool_cols = [
        "is_likely_trading_curbed",
        "is_3day_consistent_trend",
        "is_4day_consistent_trend",
        "is_5day_consistent_trend",
        "is_big_turn",
    ]
    for c in bool_cols:
        out_df[c] = out_df[c].astype(bool)

    numeric_cols = ["price_ma3_slope", "today_high_low_gap_pct",
                    "today_open_close_gap_pct"]
    return sanitize_for_db_insert(out_df, numeric_cols=numeric_cols, round_to=6)
