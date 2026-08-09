"""Peaks-and-floors (extreme) detection for analyze.mov_ave_spread.

Algorithm (per sec_type, code), symmetric for floors and peaks:

  FLOORS — downward trends → local minima (valley lows):
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
     constituent belts' valley_lows, since each belt's extreme is already
     the min close in its own span). This ensures "one trend, one extreme".
  4. Deduplicate valley_lows: no two surviving valley_lows may be within
     ±EXTREME_MIN_GAP_DAYS trading days of each other. When multiple
     valley_lows fall within the window, only the one with the minimum
     extreme_val is kept (the rest are discarded).
  5. For each surviving valley_low, compute nearby_extreme_date: within
     ±NEARBY_EXTREME_WINDOW_DAYS trading days of the extreme_date, find
     the furthest date whose OHLC low is strictly lower than the
     valley_low's OHLC high. NULL when no qualifying date exists.

  PEAKS — upward trends → local maxima:
  Symmetric to floors. Belts where close > MA60 + 2σ (upper Bollinger
  band), OR close > MA60 for > 20 trading days (both with <5 day
  bridging). Each merged trend contributes one peak — the day with the
  MAX close price across the trend span. Peaks within
  ±EXTREME_MIN_GAP_DAYS trading days are deduplicated (the MAX is kept).
  For now, peaks only store (date, extreme_val) — nearby_extreme_date is
  NULL (symmetric nearby-extreme logic for peaks may be added later).

  6. Emit one peaks_and_floors row per surviving extreme (floor OR peak):
       date                       = the trend's extreme_date (the actual
                                    biz date of the min/max close — this
                                    IS the extreme date)
       extreme_val                = the min (floor) or max (peak) close
                                    on that date
       nearby_extreme_date        = (floors only) the furthest date
                                    within ±30 trading days whose OHLC
                                    low < valley_low's OHLC high (or
                                    NULL). NULL for peaks.
       is_extreme_peak_not_floor  = True for peaks (local max, upward
                                    trend), False for floors (local min,
                                    downward trend).
     No month-start logic — rows are per-extreme-date, not per-month. Each
     detail row's peaks_and_floors_date FK is set to the nearest preceding
     extreme date (largest extreme date <= detail.date) via pandas
     merge_asof in compute.build_detail_rows; NULL only when no extreme
     exists before the detail date.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# Maximum interruption (consecutive trading days where the belt condition
# is not met) that is still bridged into a single belt. Interruptions >=
# this many days start a new belt. "interruption below 5 days" => bridge
# 0..4 days, break at 5+. Shared by floor (close < band) and peak
# (close > band) belts.
MAX_INTERRUPTION_DAYS = 5

# Minimum gap (in trading days, on each side) that must separate any two
# surviving extremes OF THE SAME KIND (two floors or two peaks). After
# merging overlapping belts into trends, a deduplication pass collapses
# any extremes within ±this many trading days — keeping the min for
# floors (lowest price) or the max for peaks (highest price).
EXTREME_MIN_GAP_DAYS = 30

# Maximum gap (in trading days) within which a peak and a floor are
# considered "oscillating" — the trend is switching between upward and
# downward too frequently to be meaningful. Extremes of BOTH kinds are
# combined into one date-sorted timeline and chained into clusters where
# consecutive extremes are within this many trading days. Any cluster
# containing BOTH a peak and a floor is dropped entirely (marked flat —
# no row emitted), since the region represents noise/consolidation
# rather than a directional trend. Matches MAX_INTERRUPTION_DAYS: a
# peak-floor switch within the same bridging window means the trend
# never established itself.
OSCILLATION_GAP_DAYS = 5

# Window (in trading days, on each side of the extreme_date) for the
# nearby_extreme_date search (floors only). The search scans ±this many
# trading days for the furthest date whose OHLC low is strictly lower
# than the valley_low's OHLC high.
NEARBY_EXTREME_WINDOW_DAYS = 30


def compute_peaks_and_floors(df: pd.DataFrame):
    """Compute per-extreme-date peaks_and_floors rows from the full source DataFrame.

    Detects BOTH floors (downward trends → local minima) and peaks
    (upward trends → local maxima) per (sec_type, code), and emits one
    row per surviving extreme.

    Floors (downward trends):
      Belts where close < MA60 − 2σ (2σ lower Bollinger band) OR close <
      MA60 for > 20 trading days. Each merged trend contributes one
      valley_low (the min close in its span). Valley_lows within
      ±EXTREME_MIN_GAP_DAYS trading days are deduplicated (min kept).
      nearby_extreme_date is computed for each surviving floor.

    Peaks (upward trends):
      Symmetric to floors: belts where close > MA60 + 2σ (2σ upper
      Bollinger band) OR close > MA60 for > 20 trading days. Each merged
      trend contributes one peak_high (the max close in its span). Peaks
      within ±EXTREME_MIN_GAP_DAYS trading days are deduplicated (max
      kept). For now peaks only store (date, extreme_val) —
      nearby_extreme_date is NULL (symmetric nearby-extreme logic may be
      added later).

    Args:
        df: Full source DataFrame with columns: sec_type, code, date, price,
            low, high, ma60, std_60days. Must be the FULL per-code history
            (not filtered to target_dates) so belt detection works correctly
            across month boundaries.

    Returns:
        List of dicts with keys: sec_type, code, date (extreme biz date),
        extreme_val, nearby_extreme_date, is_extreme_peak_not_floor.
        is_extreme_peak_not_floor is True for peaks (local max, upward
        trend) and False for floors (local min, downward trend). One row
        per surviving extreme (floor or peak). Empty list when no belts
        are found for a given code.
    """
    if df.empty:
        return []

    df = df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date

    results = []

    for (sec_type, code), group in df.groupby(["sec_type", "code"], sort=False):
        group = group.sort_values("date").reset_index(drop=True)

        # ---- Detect belts + merge into trends (per kind) ----
        floor_belts = _detect_belts(group, direction="down")
        floor_trends = _merge_overlapping_belts(floor_belts, direction="down")
        peak_belts = _detect_belts(group, direction="up")
        peak_trends = _merge_overlapping_belts(peak_belts, direction="up")

        # ---- Cross-kind oscillation filter (±5 trading days) ----
        # Drop clusters where peaks and floors alternate within
        # OSCILLATION_GAP_DAYS trading days — an oscillating/flat region
        # where no meaningful trend extreme exists. Runs BEFORE the
        # within-kind ±30-day dedup so the larger same-kind smoothing
        # window doesn't blur the tight ±5-day oscillation pairs.
        floor_trends, peak_trends = _drop_oscillating_extremes(
            group, floor_trends, peak_trends,
        )

        # ---- Within-kind dedup (±30 trading days) ----
        # Collapse same-kind extremes within ±EXTREME_MIN_GAP_DAYS,
        # keeping the min for floors / max for peaks.
        floor_trends = _deduplicate_extremes(
            group, floor_trends, direction="down",
        )
        peak_trends = _deduplicate_extremes(
            group, peak_trends, direction="up",
        )

        # ---- Emit rows ----
        for trend in floor_trends:
            nearby_date = _compute_nearby_extreme_date(group, trend)
            results.append({
                "sec_type": sec_type,
                "code": code,
                "date": trend["extreme_date"],
                "extreme_val": trend["extreme_val"],
                "nearby_extreme_date": nearby_date,
                "is_extreme_peak_not_floor": False,
            })
        for trend in peak_trends:
            # For now peaks only store (date, extreme_val); the symmetric
            # nearby_extreme_date logic is not computed (NULL). "for now
            # just select upward trend max price and date".
            results.append({
                "sec_type": sec_type,
                "code": code,
                "date": trend["extreme_date"],
                "extreme_val": trend["extreme_val"],
                "nearby_extreme_date": None,
                "is_extreme_peak_not_floor": True,
            })

    return results


# ---------------------------------------------------------------------------
#  Belt detection
# ---------------------------------------------------------------------------

# Minimum total span (in trading days, including bridged interruptions) for
# an MA60 belt to qualify (both directions). "close keeps deviating away
# from MA60 for more than 20 days" => span > 20 => >= 21 days. 2σ belts
# have no minimum duration (a sharp V-dip below the lower band or V-spike
# above the upper band qualifies even if short).
MA60_BELT_MIN_SPAN = 21


def _detect_belts(group: pd.DataFrame, direction: str):
    """Detect continuous belts for a single (sec_type, code) group.

    Two belt definitions are detected (a belt is "continuous" if it meets
    EITHER definition), symmetric by ``direction``:

    1. 2σ Bollinger belt:
       direction='down': in_belt = (close < MA60 − 2σ lower band).
       direction='up':   in_belt = (close > MA60 + 2σ upper band).
       Runs separated by < MAX_INTERRUPTION_DAYS consecutive non-belt days
       are bridged. No minimum duration — a sharp V-dip (down) / V-spike
       (up) below/above the band qualifies.

    2. MA60 belt:
       direction='down': in_belt = (close < MA60).
       direction='up':   in_belt = (close > MA60).
       Same <5 day bridging. Requires total span > 20 trading days (close
       keeps deviating away from MA60 for more than 20 days with little
       interruption). The belt ends when close crosses back and stays
       crossed for >= MAX_INTERRUPTION_DAYS (handled naturally by the
       bridging logic — a >=5 day gap of close on the opposite side breaks
       the belt). An ongoing belt also qualifies if it's >20 days long.

    The belt's extreme is the MIN close (direction='down', valley low) or
    the MAX close (direction='up', peak high) within the belt (including
    bridged interruption days).

    Args:
        direction: 'down' for valley/floor belts, 'up' for peak belts.

    Returns list of dicts: start_date, end_date, extreme_date, extreme_val.
    """
    price = group["price"].values
    ma60 = group["ma60"].values
    std_60 = group["std_60days"].values
    dates = group["date"].values

    n = len(price)
    if n == 0:
        return []

    # ---- Belt type 1: 2σ Bollinger band ----
    # down: close < MA60 − 2σ (lower band); up: close > MA60 + 2σ (upper band)
    if direction == "down":
        band = ma60 - 2.0 * std_60
        in_belt_2sig = (price < band) & np.isfinite(price) & np.isfinite(band)
    else:  # "up"
        band = ma60 + 2.0 * std_60
        in_belt_2sig = (price > band) & np.isfinite(price) & np.isfinite(band)

    # ---- Belt type 2: MA60, span > 20 days ----
    # down: close < MA60; up: close > MA60
    if direction == "down":
        in_belt_ma60 = (price < ma60) & np.isfinite(price) & np.isfinite(ma60)
    else:  # "up"
        in_belt_ma60 = (price > ma60) & np.isfinite(price) & np.isfinite(ma60)

    belts_2sig = _split_into_belts(
        in_belt_2sig, price, dates, min_span=1, direction=direction,
    )
    belts_ma60 = _split_into_belts(
        in_belt_ma60, price, dates,
        min_span=MA60_BELT_MIN_SPAN, direction=direction,
    )

    return belts_2sig + belts_ma60


def _merge_overlapping_belts(belts, direction="down"):
    """Merge overlapping belts into "trends".

    A trend is the maximal union of overlapping belts (by [start_date,
    end_date] interval). Two belts overlap if belt2.start_date <=
    belt1.end_date (touching counts as overlap).

    Each merged trend has exactly ONE extreme — the most extreme
    extreme_val among its constituent belts: the MIN for direction='down'
    (valley low — lowest close), the MAX for direction='up' (peak high —
    highest close). Each belt's extreme is already the min/max close in
    its own span, so the most extreme among them is the min/max close
    across the entire merged trend span. This ensures "one trend, one
    extreme".

    Returns list of dicts with the same shape as input belts:
    start_date, end_date, extreme_date, extreme_val.
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
            # Keep the more extreme value: lower for floors, higher for peaks.
            keep_new = (
                belt["extreme_val"] < cur["extreme_val"]
                if direction == "down"
                else belt["extreme_val"] > cur["extreme_val"]
            )
            if keep_new:
                cur["extreme_val"] = belt["extreme_val"]
                cur["extreme_date"] = belt["extreme_date"]
        else:
            # No overlap — start a new trend.
            merged.append(cur)
            cur = dict(belt)

    merged.append(cur)
    return merged


# ---------------------------------------------------------------------------
#  Extreme deduplication (±30 trading-day gap enforcement, within a kind)
# ---------------------------------------------------------------------------

def _deduplicate_extremes(
    group: pd.DataFrame, trends: list[dict],
    direction: str = "down",
    max_gap: int = EXTREME_MIN_GAP_DAYS,
) -> list[dict]:
    """Merge extremes within ±max_gap trading days, keeping the most extreme.

    After merging overlapping belts into trends, each trend has one
    extreme. This step ensures no two surviving extremes OF THE SAME KIND
    (two floors or two peaks) are within ``max_gap`` trading days of each
    other: when multiple extremes fall within the window, only the most
    extreme one survives — the MIN for direction='down' (lowest valley
    low), the MAX for direction='up' (highest peak).

    Trading-day distance is computed via the group's sorted date array
    (index positions), not calendar days.

    Algorithm (greedy, single pass over date-sorted extremes):
      - Maintain a ``cur`` cluster with a surviving extreme (the most
        extreme so far).
      - For each subsequent extreme, if it is within ``max_gap`` trading
        days of the surviving extreme, merge it (update the survivor if
        the new value is more extreme). Otherwise, close the cluster and
        start a new one.

    Because the extremes are sorted by date, the surviving extreme of
    the previous cluster is always the closest survivor to the current
    candidate — any earlier cluster's survivor is further away. This
    guarantees the output satisfies the ±max_gap constraint.
    """
    if len(trends) <= 1:
        return trends

    dates = group["date"].values
    date_to_idx = {d: i for i, d in enumerate(dates)}

    sorted_trends = sorted(trends, key=lambda t: t["extreme_date"])

    merged: list[dict] = []
    cur = dict(sorted_trends[0])
    cur_idx = date_to_idx.get(cur["extreme_date"])
    if cur_idx is None:
        # extreme_date not found in group — shouldn't happen, but guard.
        return trends

    for trend in sorted_trends[1:]:
        t_idx = date_to_idx.get(trend["extreme_date"])
        if t_idx is None:
            continue
        if abs(t_idx - cur_idx) <= max_gap:
            # Within window — merge, keeping the most extreme value
            # (min for floors, max for peaks).
            keep_new = (
                trend["extreme_val"] < cur["extreme_val"]
                if direction == "down"
                else trend["extreme_val"] > cur["extreme_val"]
            )
            if keep_new:
                cur = dict(trend)
                cur_idx = t_idx
        else:
            merged.append(cur)
            cur = dict(trend)
            cur_idx = t_idx

    merged.append(cur)
    return merged


# ---------------------------------------------------------------------------
#  Cross-kind oscillation filter (drop flat / oscillating regions)
# ---------------------------------------------------------------------------

def _drop_oscillating_extremes(
    group: pd.DataFrame,
    floor_trends: list[dict],
    peak_trends: list[dict],
    gap: int = OSCILLATION_GAP_DAYS,
) -> tuple[list[dict], list[dict]]:
    """Drop clusters of extremes where peaks and floors alternate within
    ``gap`` trading days (an oscillating / flat region).

    Combines all floor and peak extremes into one date-sorted timeline,
    builds clusters by chaining consecutive extremes whose trading-day
    distance is <= ``gap``, and drops EVERY extreme in any cluster that
    contains BOTH at least one floor AND at least one peak. Such a
    cluster represents rapid switching between upward and downward
    trends — i.e. an oscillating/flat region where no meaningful trend
    extreme exists ("mark them altogether to flat").

    Chaining (not pairwise) is used so a long run of rapid switches
    (peak→floor→peak→floor, each within `gap` of the next) forms one
    mixed cluster and is dropped in full. Pure-kind clusters (only
    floors or only peaks, even if chained within `gap`) are passed
    through unchanged — same-kind proximity is handled later by the
    within-kind ±30-day dedup, not here.

    Returns (surviving_floor_trends, surviving_peak_trends).
    """
    if not floor_trends and not peak_trends:
        return [], []

    dates = group["date"].values
    date_to_idx = {d: i for i, d in enumerate(dates)}

    # Tag each extreme with its trading-day position + kind, for sorting
    # and cluster-splitting. Extremes whose date is missing from the
    # group (shouldn't happen) are kept as-is by short-circuiting below.
    tagged: list[tuple] = []  # (pos, kind, trend_dict)
    for t in floor_trends:
        idx = date_to_idx.get(t["extreme_date"])
        if idx is not None:
            tagged.append((idx, "down", t))
    for t in peak_trends:
        idx = date_to_idx.get(t["extreme_date"])
        if idx is not None:
            tagged.append((idx, "up", t))

    if not tagged:
        return floor_trends, peak_trends

    tagged.sort(key=lambda x: x[0])

    # Build clusters: a new cluster starts when the trading-day gap to
    # the previous extreme exceeds `gap`. Within `gap`, chain into the
    # current cluster regardless of kind.
    clusters: list[list[tuple]] = []
    cur_cluster: list[tuple] = [tagged[0]]
    for item in tagged[1:]:
        if item[0] - cur_cluster[-1][0] <= gap:
            cur_cluster.append(item)
        else:
            clusters.append(cur_cluster)
            cur_cluster = [item]
    clusters.append(cur_cluster)

    # Keep extremes only from pure-kind clusters. Mixed clusters (both
    # up & down) are oscillating → drop all their extremes (flat).
    keep_floors: list[dict] = []
    keep_peaks: list[dict] = []
    for cluster in clusters:
        kinds = {k for _, k, _ in cluster}
        if kinds == {"down"}:
            keep_floors.extend(t for _, _, t in cluster)
        elif kinds == {"up"}:
            keep_peaks.extend(t for _, _, t in cluster)
        # else: mixed cluster (both up & down) → oscillating → drop all.

    return keep_floors, keep_peaks


# ---------------------------------------------------------------------------
#  nearby_extreme_date computation (floors only)
# ---------------------------------------------------------------------------

def _compute_nearby_extreme_date(
    group: pd.DataFrame, trend: dict,
    window: int = NEARBY_EXTREME_WINDOW_DAYS,
):
    """Find the furthest date within ±window trading days of the
    extreme_date (a valley low) whose OHLC low is strictly lower than the
    valley low's OHLC high.

    Only invoked for floors (direction='down'). Peaks do not compute a
    nearby_extreme_date (NULL for now).

    Scans outward from the extreme_date (distance = window down to 1)
    on both sides. At each distance, the negative side (earlier date) is
    checked first so ties are broken in favour of the earlier date.

    Returns the python date of the first qualifying row at the maximum
    distance, or None when no date (other than the extreme itself)
    qualifies.
    """
    dates = group["date"].values
    lows = group["low"].values
    highs = group["high"].values
    n = len(dates)

    ex_date = trend["extreme_date"]
    # Locate the extreme_date's index in the sorted group.
    ex_idx = None
    for i in range(n):
        if dates[i] == ex_date:
            ex_idx = i
            break
    if ex_idx is None:
        return None

    ex_high = highs[ex_idx]
    if not np.isfinite(ex_high):
        return None

    # Scan from the furthest position inward (max distance first).
    for dist in range(window, 0, -1):
        neg_idx = ex_idx - dist
        pos_idx = ex_idx + dist

        # Negative side (earlier date) wins ties.
        if neg_idx >= 0:
            lo_val = lows[neg_idx]
            if np.isfinite(lo_val) and lo_val < ex_high:
                return dates[neg_idx]
        if pos_idx < n:
            lo_val = lows[pos_idx]
            if np.isfinite(lo_val) and lo_val < ex_high:
                return dates[pos_idx]

    return None


def _split_into_belts(in_belt, price, dates, min_span, direction="down"):
    """Split a boolean in_belt array into belts using <5 day bridging.

    A belt is a maximal run of in_belt days, where runs separated by <
    MAX_INTERRUPTION_DAYS consecutive non-belt days are bridged into a
    single belt. Belts with total span (end_idx - start_idx + 1) <
    min_span are discarded.

    For each belt, the extreme is the min close (direction='down', valley
    low) or the max close (direction='up', peak high) in [start_idx,
    end_idx] (including bridged interruption days, since those are part
    of the belt's duration).

    Args:
        direction: 'down' → extreme = min close; 'up' → extreme = max close.

    Returns list of dicts: start_date, end_date, extreme_date, extreme_val.
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

        # Extreme = min/max close in [start_idx, end_idx] (including bridged
        # interruption days, since those are part of the belt's duration).
        belt_prices = price[start_idx:end_idx + 1]
        valid = np.isfinite(belt_prices)
        if not valid.any():
            continue
        if direction == "down":
            extreme_pos = int(np.argmin(belt_prices[valid]))
        else:
            extreme_pos = int(np.argmax(belt_prices[valid]))
        valid_positions = np.where(valid)[0]
        extreme_offset = int(valid_positions[extreme_pos])
        extreme_idx = start_idx + extreme_offset

        belts.append({
            "start_date": dates[start_idx],
            "end_date": dates[end_idx],
            "extreme_date": dates[extreme_idx],
            "extreme_val": float(price[extreme_idx]),
        })

    return belts
