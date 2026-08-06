"""Peaks-and-floors (extreme) detection for analyze.mov_ave_spread.

Algorithm (per sec_type, code):
  1. Compute the 2-sigma lower Bollinger band: lower_band = MA60 - 2 * std_60days.
  2. Find "continuous belts" — a belt is continuous if it meets EITHER:
     (a) 2σ Bollinger belt: close < MA60 − 2σ for a run of consecutive
         trading days, with interruptions < 5 trading days bridged together.
         No minimum duration (a sharp V-dip below the lower band qualifies).
     (b) MA60 belt: close < MA60 for a run of consecutive trading days with
         the same <5 day bridging, AND total span > 20 trading days (close
         keeps falling beneath MA60 for more than 20 days with little
         interruption). The belt ends when close rises back above MA60 and
         stays above for >= 5 days (the bridging logic breaks the belt).
         An ongoing belt (close still below MA60) also qualifies if >20 days.
  3. Merge overlapping belts into "trends". A trend is the maximal union of
     overlapping belts. Each trend has exactly ONE extreme — the day with the
     minimum close price across the entire trend span (the lowest of the
     constituent belts' valley_lows, since each belt's valley_low is already
     the min close in its own span). This ensures "one trend, one extreme".
  4. Deduplicate valley_lows: no two surviving valley_lows may be within
     ±VALLEY_LOW_MIN_GAP_DAYS trading days of each other. When multiple
     valley_lows fall within the window, only the one with the minimum
     extreme_val is kept (the rest are discarded).
  5. For each surviving valley_low, compute nearby_extreme_date: within
     ±NEARBY_EXTREME_WINDOW_DAYS trading days of the valley_low_date, find
     the furthest date whose OHLC low is strictly lower than the
     valley_low's OHLC high. NULL when no qualifying date exists.
  6. Emit one peaks_and_floors row per surviving trend, where:
       date                  = the trend's valley_low_date (the actual biz
                               date of the min close — this IS the extreme date)
       extreme_val           = the min close price on that date
       nearby_extreme_date   = the furthest date within ±30 trading days
                               whose OHLC low < valley_low's OHLC high (or NULL).
     No month-start logic — rows are per-extreme-date, not per-month. Each
     detail row's peaks_and_floors_date FK is set to the nearest preceding
     extreme date (largest extreme date <= detail.date) via pandas
     merge_asof in compute.build_detail_rows; NULL only when no extreme
     exists before the detail date.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# Maximum interruption (consecutive trading days where close >= lower_band)
# that is still bridged into a single belt. Interruptions >= this many days
# start a new belt. "interruption below 5 days" => bridge 0..4 days, break at 5+.
MAX_INTERRUPTION_DAYS = 5

# Minimum gap (in trading days, on each side) that must separate any two
# surviving valley_lows. After merging overlapping belts into trends, a
# deduplication pass collapses any valley_lows within ±this many trading
# days, keeping only the one with the minimum extreme_val (lowest price).
VALLEY_LOW_MIN_GAP_DAYS = 30

# Window (in trading days, on each side of the valley_low_date) for the
# nearby_extreme_date search. The search scans ±this many trading days for
# the furthest date whose OHLC low is strictly lower than the valley_low's
# OHLC high.
NEARBY_EXTREME_WINDOW_DAYS = 30


def compute_peaks_and_floors(df: pd.DataFrame):
    """Compute per-extreme-date peaks_and_floors rows from the full source DataFrame.

    Args:
        df: Full source DataFrame with columns: sec_type, code, date, price,
            low, high, ma60, std_60days. Must be the FULL per-code history
            (not filtered to target_dates) so belt detection works correctly
            across month boundaries.

    Returns:
        List of dicts with keys: sec_type, code, date (extreme biz date),
        extreme_val, nearby_extreme_date.
        One row per surviving trend (per extreme date). Empty list when no
        belts are found for a given code.
    """
    if df.empty:
        return []

    df = df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date

    results = []

    for (sec_type, code), group in df.groupby(["sec_type", "code"], sort=False):
        group = group.sort_values("date").reset_index(drop=True)

        belts = _detect_belts(group)
        trends = _merge_overlapping_belts(belts)
        # Deduplicate valley_lows within ±30 trading days, keeping the min.
        trends = _deduplicate_valley_lows(group, trends)

        for trend in trends:
            nearby_date = _compute_nearby_extreme_date(group, trend)
            results.append({
                "sec_type": sec_type,
                "code": code,
                "date": trend["valley_low_date"],
                "extreme_val": trend["valley_low"],
                "nearby_extreme_date": nearby_date,
            })

    return results


# ---------------------------------------------------------------------------
#  Belt detection
# ---------------------------------------------------------------------------

# Minimum total span (in trading days, including bridged interruptions) for
# an MA60 belt to qualify. "close keeps falling beneath ma60 for more than
# 20 days" => span > 20 => >= 21 days. 2σ belts have no minimum duration
# (a sharp V-dip below the lower Bollinger band qualifies even if short).
MA60_BELT_MIN_SPAN = 21


def _detect_belts(group: pd.DataFrame):
    """Detect continuous belts for a single (sec_type, code) group.

    Two belt definitions are detected (a belt is "continuous" if it meets
    EITHER definition):

    1. 2σ Bollinger belt (original):
       in_belt = (close < MA60 − 2σ lower Bollinger band). Runs separated
       by < MAX_INTERRUPTION_DAYS consecutive non-belt days are bridged.
       No minimum duration — a sharp V-dip below the lower band qualifies.

    2. MA60 belt (new):
       in_belt = (close < MA60). Same <5 day bridging. Requires total
       span > 20 trading days (close keeps falling beneath MA60 for more
       than 20 days with little interruption). The belt ends when close
       rises back above MA60 and stays above for >= MAX_INTERRUPTION_DAYS
       (handled naturally by the bridging logic — a >=5 day gap of
       close >= MA60 breaks the belt). An ongoing belt (close still below
       MA60, not yet crossed back) also qualifies if it's >20 days long.

    Both belt types share the same valley_low definition: the day with the
    minimum close price within the belt (including bridged interruption
    days).

    Returns list of dicts: start_date, end_date, valley_low_date, valley_low.
    """
    price = group["price"].values
    ma60 = group["ma60"].values
    std_60 = group["std_60days"].values
    dates = group["date"].values

    n = len(price)
    if n == 0:
        return []

    # ---- Belt type 1: 2σ Bollinger band (close < MA60 − 2σ) ----
    lower_band = ma60 - 2.0 * std_60
    in_belt_2sig = (price < lower_band) & np.isfinite(price) & np.isfinite(lower_band)
    belts_2sig = _split_into_belts(in_belt_2sig, price, dates, min_span=1)

    # ---- Belt type 2: MA60 (close < MA60), span > 20 days ----
    in_belt_ma60 = (price < ma60) & np.isfinite(price) & np.isfinite(ma60)
    belts_ma60 = _split_into_belts(in_belt_ma60, price, dates,
                                   min_span=MA60_BELT_MIN_SPAN)

    return belts_2sig + belts_ma60


def _merge_overlapping_belts(belts):
    """Merge overlapping belts into "trends".

    A trend is the maximal union of overlapping belts (by [start_date,
    end_date] interval). Two belts overlap if belt2.start_date <=
    belt1.end_date (touching counts as overlap).

    Each merged trend has exactly ONE valley_low — the lowest valley_low
    among its constituent belts (each belt's valley_low is already the min
    close in its span, so the lowest among them is the min close across
    the entire merged trend span). This ensures "one trend, one valley_low".

    Returns list of dicts with the same shape as input belts:
    start_date, end_date, valley_low_date, valley_low.
    """
    if not belts:
        return []

    # Sort by start_date (then end_date for tie-breaking).
    sorted_belts = sorted(belts, key=lambda b: (b["start_date"], b["end_date"]))

    merged = []
    cur = dict(sorted_belts[0])  # copy

    for belt in sorted_belts[1:]:
        if belt["start_date"] <= cur["end_date"]:
            # Overlaps (or touches) — merge into current trend.
            cur["end_date"] = max(cur["end_date"], belt["end_date"])
            # Valley low = lowest among constituent belts.
            if belt["valley_low"] < cur["valley_low"]:
                cur["valley_low"] = belt["valley_low"]
                cur["valley_low_date"] = belt["valley_low_date"]
        else:
            # No overlap — start a new trend.
            merged.append(cur)
            cur = dict(belt)

    merged.append(cur)
    return merged


# ---------------------------------------------------------------------------
#  Valley-low deduplication (±30 trading-day gap enforcement)
# ---------------------------------------------------------------------------

def _deduplicate_valley_lows(
    group: pd.DataFrame, trends: list[dict],
    max_gap: int = VALLEY_LOW_MIN_GAP_DAYS,
) -> list[dict]:
    """Merge valley_lows within ±max_gap trading days, keeping the min.

    After merging overlapping belts into trends, each trend has one
    valley_low. This step ensures no two surviving valley_lows are within
    ``max_gap`` trading days of each other: when multiple valley_lows fall
    within the window, only the one with the minimum extreme_val survives.

    Trading-day distance is computed via the group's sorted date array
    (index positions), not calendar days.

    Algorithm (greedy, single pass over date-sorted valley_lows):
      - Maintain a ``cur`` cluster with a surviving valley_low (the min).
      - For each subsequent valley_low, if it is within ``max_gap`` trading
        days of the surviving valley_low, merge it (update the survivor if
        the new value is lower). Otherwise, close the cluster and start a
        new one.

    Because the valley_lows are sorted by date, the surviving valley_low of
    the previous cluster is always the closest surviving low to the current
    candidate — any earlier cluster's survivor is further away. This
    guarantees the output satisfies the ±max_gap constraint.
    """
    if len(trends) <= 1:
        return trends

    dates = group["date"].values
    date_to_idx = {d: i for i, d in enumerate(dates)}

    sorted_trends = sorted(trends, key=lambda t: t["valley_low_date"])

    merged: list[dict] = []
    cur = dict(sorted_trends[0])
    cur_idx = date_to_idx.get(cur["valley_low_date"])
    if cur_idx is None:
        # valley_low_date not found in group — shouldn't happen, but guard.
        return trends

    for trend in sorted_trends[1:]:
        t_idx = date_to_idx.get(trend["valley_low_date"])
        if t_idx is None:
            continue
        if abs(t_idx - cur_idx) <= max_gap:
            # Within window — merge, keeping the min extreme_val.
            if trend["valley_low"] < cur["valley_low"]:
                cur = dict(trend)
                cur_idx = t_idx
        else:
            merged.append(cur)
            cur = dict(trend)
            cur_idx = t_idx

    merged.append(cur)
    return merged


# ---------------------------------------------------------------------------
#  nearby_extreme_date computation
# ---------------------------------------------------------------------------

def _compute_nearby_extreme_date(
    group: pd.DataFrame, trend: dict,
    window: int = NEARBY_EXTREME_WINDOW_DAYS,
):
    """Find the furthest date within ±window trading days of the
    valley_low_date whose OHLC low is strictly lower than the valley_low's
    OHLC high.

    Scans outward from the valley_low_date (distance = window down to 1)
    on both sides. At each distance, the negative side (earlier date) is
    checked first so ties are broken in favour of the earlier date.

    Returns the python date of the first qualifying row at the maximum
    distance, or None when no date (other than the valley_low itself)
    qualifies.
    """
    dates = group["date"].values
    lows = group["low"].values
    highs = group["high"].values
    n = len(dates)

    vl_date = trend["valley_low_date"]
    # Locate the valley_low_date's index in the sorted group.
    vl_idx = None
    for i in range(n):
        if dates[i] == vl_date:
            vl_idx = i
            break
    if vl_idx is None:
        return None

    vl_high = highs[vl_idx]
    if not np.isfinite(vl_high):
        return None

    # Scan from the furthest position inward (max distance first).
    for dist in range(window, 0, -1):
        neg_idx = vl_idx - dist
        pos_idx = vl_idx + dist

        # Negative side (earlier date) wins ties.
        if neg_idx >= 0:
            lo_val = lows[neg_idx]
            if np.isfinite(lo_val) and lo_val < vl_high:
                return dates[neg_idx]
        if pos_idx < n:
            lo_val = lows[pos_idx]
            if np.isfinite(lo_val) and lo_val < vl_high:
                return dates[pos_idx]

    return None


def _split_into_belts(in_belt, price, dates, min_span):
    """Split a boolean in_belt array into belts using <5 day bridging.

    A belt is a maximal run of in_belt days, where runs separated by <
    MAX_INTERRUPTION_DAYS consecutive non-belt days are bridged into a
    single belt. Belts with total span (end_idx - start_idx + 1) <
    min_span are discarded.

    For each belt, the valley_low is the min close in [start_idx, end_idx]
    (including bridged interruption days, since those are part of the
    belt's duration).

    Returns list of dicts: start_date, end_date, valley_low_date, valley_low.
    """
    true_idx = np.where(in_belt)[0]
    if len(true_idx) == 0:
        return []

    # Split into belts: a new belt starts when the gap (number of consecutive
    # non-belt days) between two in_belt days is >= MAX_INTERRUPTION_DAYS.
    #   gap = true_idx[i+1] - true_idx[i] - 1  (non-belt days between)
    #   bridge if gap < MAX_INTERRUPTION_DAYS
    #   break  if gap >= MAX_INTERRUPTION_DAYS
    if len(true_idx) == 1:
        belt_groups = [true_idx]
    else:
        gaps = np.diff(true_idx) - 1  # number of non-belt days between
        break_points = np.where(gaps >= MAX_INTERRUPTION_DAYS)[0]
        belt_groups = np.split(true_idx, break_points + 1)

    belts = []
    for bg in belt_groups:
        if len(bg) == 0:
            continue
        start_idx = int(bg[0])
        end_idx = int(bg[-1])

        # Minimum span filter (total trading-day span including interruptions).
        span = end_idx - start_idx + 1
        if span < min_span:
            continue

        # Valley low = min close in [start_idx, end_idx] (including bridged
        # interruption days, since those are part of the belt's duration).
        belt_prices = price[start_idx:end_idx + 1]
        valid = np.isfinite(belt_prices)
        if not valid.any():
            continue
        min_pos = int(np.argmin(belt_prices[valid]))
        valid_positions = np.where(valid)[0]
        valley_offset = int(valid_positions[min_pos])
        valley_idx = start_idx + valley_offset

        belts.append({
            "start_date": dates[start_idx],
            "end_date": dates[end_idx],
            "valley_low_date": dates[valley_idx],
            "valley_low": float(price[valley_idx]),
        })

    return belts
