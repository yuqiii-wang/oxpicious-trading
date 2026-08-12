"""Peaks-and-floors (extreme) detection for analyze.mov_ave_spread.

CAUSAL algorithm — NO future data is used for the peak/floor JUDGEMENT.
Every extreme is decided using only history up to and including that day.

Per (sec_type, code), symmetric for floors and peaks:

  DETECTION (fully causal — lookback only):
    A day D is a FLOOR candidate when BOTH hold using only data up to D:
      1. Trailing 60-day low: price[D] is the MIN close in [D-59, D]
         ("history 60 d max/low").
      2. D is inside a downward "belt" (trend context, lookback only):
         (a) 2σ Bollinger belt: price[D] < MA60[D] − 2·std_60[D], OR
         (b) MA60 belt: price has stayed below MA60 for a bridged run of
             > 20 trading days ending at D (interruptions < 5 days bridged,
             >= 5 days break the run). The run length is accumulated
             causally — only past days count.
    PEAK candidates are symmetric (trailing 60-day MAX, upward belt:
    price > MA60 + 2σ, or price > MA60 for a bridged run > 20 days).

  SELECTION (post-processing — decides which DETECTED extreme survives;
  the detection itself is already causal):
    3. Oscillation filter (backward-only): drop a candidate if an
       opposite-kind candidate exists within the previous 5 trading days
       (rapid peak↔floor switching = flat / noise region).
    4. Within-kind dedup (30-day clusters, backward): walk forward; cluster
       consecutive same-kind candidates within 30 trading days of the
       cluster's current most-extreme survivor and keep the most extreme
       per cluster (min for floors, max for peaks).
    5. nearby_extreme_date (floors only, backward-only): within the
       PREVIOUS 30 trading days of the extreme_date, find the furthest
       date whose OHLC low is strictly lower than the valley's OHLC high.
       NULL when no qualifying date exists. NULL for peaks.

  Emit one peaks_and_floors row per surviving extreme (floor OR peak):
       date                       = the extreme biz date
       extreme_val                = the close on that date
       nearby_extreme_date        = (floors) furthest past date within 30
                                    td whose OHLC low < valley's OHLC high
                                    (or NULL). NULL for peaks.
       is_extreme_peak_not_floor  = True for peaks, False for floors.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# Trailing window (trading days, inclusive of D) for causal max/min extreme
# detection. "history 60 d max/low" — a day qualifies as a floor candidate
# when its close is the lowest in the trailing LOOKBACK_WINDOW days, and as
# a peak candidate when its close is the highest.
LOOKBACK_WINDOW = 60

# Maximum interruption (consecutive trading days where the belt condition
# is not met) that is still bridged into a single belt run. Interruptions
# >= this many days break the run. Shared by floor and peak belts.
MAX_INTERRUPTION_DAYS = 5

# Minimum bridged-run span (trading days, including bridged gaps) for an
# MA60 belt to qualify (both directions). Span > 20 => >= 21 days. 2σ
# belts have no minimum duration (a sharp V-dip / V-spike qualifies).
MA60_BELT_MIN_SPAN = 21

# Within-kind dedup: cluster consecutive same-kind candidates within this
# many trading days (of the cluster's current survivor); keep the most
# extreme per cluster.
EXTREME_MIN_GAP_DAYS = 30

# Cross-kind oscillation: drop a candidate if an opposite-kind candidate
# exists within this many PREVIOUS trading days (rapid peak↔floor switch).
OSCILLATION_GAP_DAYS = 5

# Backward-only window (trading days BEFORE the extreme_date) for the
# nearby_extreme_date search (floors only). The search scans the previous
# this-many trading days for the furthest date whose OHLC low is strictly
# lower than the valley's OHLC high.
NEARBY_EXTREME_WINDOW_DAYS = 30


def compute_peaks_and_floors(df: pd.DataFrame):
    """Compute per-extreme-date peaks_and_floors rows (CAUSAL: no future
    data used for the peak/floor judgement).

    Detects BOTH floors (downward trends → trailing 60-day lows inside a
    down-belt) and peaks (upward trends → trailing 60-day highs inside an
    up-belt) per (sec_type, code), and emits one row per surviving
    extreme.

    Args:
        df: Full source DataFrame with columns: sec_type, code, date,
            price, low, high, ma60, std_60days. Must be the FULL per-code
            history (not filtered to target_dates) so the trailing 60-day
            lookback and the causal MA60 belt run are correct.

    Returns:
        List of dicts with keys: sec_type, code, date (extreme biz date),
        extreme_val, nearby_extreme_date, is_extreme_peak_not_floor.
        is_extreme_peak_not_floor is True for peaks and False for floors.
        One row per surviving extreme. Empty list when no candidates.
    """
    if df.empty:
        return []

    df = df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date

    results = []
    for (sec_type, code), group in df.groupby(["sec_type", "code"], sort=False):
        group = group.sort_values("date").reset_index(drop=True)
        results.extend(_compute_for_group(group, sec_type, code))
    return results


# ---------------------------------------------------------------------------
#  Per-group processing
# ---------------------------------------------------------------------------

def _compute_for_group(group: pd.DataFrame, sec_type: str, code: str):
    """Process one (sec_type, code) group → list of peaks_and_floors rows."""
    n = len(group)
    if n < LOOKBACK_WINDOW:
        # Not enough history for a trailing 60-day window.
        return []

    price = group["price"].values
    ma60 = group["ma60"].values
    std_60 = group["std_60days"].values
    dates = group["date"].values
    lows = group["low"].values
    highs = group["high"].values

    # ---- Causal trailing 60-day rolling min/max (inclusive of D) --------
    s = pd.Series(price)
    roll_min = s.rolling(LOOKBACK_WINDOW, min_periods=LOOKBACK_WINDOW).min().values
    roll_max = s.rolling(LOOKBACK_WINDOW, min_periods=LOOKBACK_WINDOW).max().values

    # ---- Causal belt masks (lookback only) -----------------------------
    in_belt_down = _compute_causal_belt_mask(price, ma60, std_60, direction="down")
    in_belt_up = _compute_causal_belt_mask(price, ma60, std_60, direction="up")

    valid = np.isfinite(price)
    # Floor candidate: close is the trailing 60-day min AND inside a down-belt.
    # price <= roll_min is False where roll_min is NaN (first 59 days), so
    # no candidates emerge before the window is populated.
    floor_mask = valid & (price <= roll_min) & in_belt_down
    peak_mask = valid & (price >= roll_max) & in_belt_up

    floor_cands = [
        {"extreme_date": dates[i], "extreme_val": float(price[i]), "_idx": int(i)}
        for i in np.where(floor_mask)[0]
    ]
    peak_cands = [
        {"extreme_date": dates[i], "extreme_val": float(price[i]), "_idx": int(i)}
        for i in np.where(peak_mask)[0]
    ]

    # ---- Oscillation filter (backward-only) ----------------------------
    floor_cands, peak_cands = _drop_oscillating_backward(
        floor_cands, peak_cands, gap=OSCILLATION_GAP_DAYS,
    )

    # ---- Within-kind dedup (30-day clusters, keep most extreme) --------
    floor_cands = _deduplicate_backward(
        floor_cands, direction="down", max_gap=EXTREME_MIN_GAP_DAYS,
    )
    peak_cands = _deduplicate_backward(
        peak_cands, direction="up", max_gap=EXTREME_MIN_GAP_DAYS,
    )

    # ---- Emit rows ------------------------------------------------------
    rows = []
    for cand in floor_cands:
        nearby = _compute_nearby_extreme_date(
            cand["_idx"], lows, highs, dates, n,
        )
        rows.append({
            "sec_type": sec_type,
            "code": code,
            "date": cand["extreme_date"],
            "extreme_val": cand["extreme_val"],
            "nearby_extreme_date": nearby,
            "is_extreme_peak_not_floor": False,
        })
    for cand in peak_cands:
        rows.append({
            "sec_type": sec_type,
            "code": code,
            "date": cand["extreme_date"],
            "extreme_val": cand["extreme_val"],
            "nearby_extreme_date": None,
            "is_extreme_peak_not_floor": True,
        })
    return rows


# ---------------------------------------------------------------------------
#  Causal belt masks
# ---------------------------------------------------------------------------

def _compute_causal_belt_mask(
    price: np.ndarray, ma60: np.ndarray, std_60: np.ndarray,
    direction: str,
) -> np.ndarray:
    """Causal belt mask: True on day D if D is inside a belt (lookback only).

    Down belt (direction='down'): D qualifies when EITHER
      (a) 2σ Bollinger: price[D] < MA60[D] − 2·std_60[D], OR
      (b) MA60 belt: price[D] < MA60[D] AND the bridged run of
          (price < MA60) ending at D spans > MA60_BELT_MIN_SPAN trading
          days (interruptions < MAX_INTERRUPTION_DAYS bridged, >= breaks).

    Up belt (direction='up'): symmetric (price > MA60 + 2σ, or
    price > MA60 with bridged run > MA60_BELT_MIN_SPAN).

    Both conditions use only data up to and including D (causal).
    """
    finite_p = np.isfinite(price)
    if direction == "down":
        band = ma60 - 2.0 * std_60
        in_2sig = finite_p & np.isfinite(band) & (price < band)
        in_ma60 = finite_p & np.isfinite(ma60) & (price < ma60)
    else:  # "up"
        band = ma60 + 2.0 * std_60
        in_2sig = finite_p & np.isfinite(band) & (price > band)
        in_ma60 = finite_p & np.isfinite(ma60) & (price > ma60)

    run_len = _causal_bridged_run_length(in_ma60, MAX_INTERRUPTION_DAYS)
    in_ma60_belt = in_ma60 & (run_len > MA60_BELT_MIN_SPAN)
    return in_2sig | in_ma60_belt


def _causal_bridged_run_length(in_belt: np.ndarray, max_interruption: int) -> np.ndarray:
    """Causal bridged run length ending at each position (lookback only).

    On day D, returns the span (in trading days, including bridged gap
    days) of the current bridged run of in_belt=True days ending at D.
    A run is "bridged" if interruptions of < max_interruption consecutive
    non-belt days are stitched into the run. An interruption of
    >= max_interruption consecutive non-belt days breaks the run (length
    resets to 0).

    The value is non-zero only while the run is active (bridging or
    in-belt); it is 0 once a break has occurred (and until a fresh
    in-belt day starts a new run).

    Causal: position D's length depends only on in_belt[0..D].
    """
    n = len(in_belt)
    run = np.zeros(n, dtype=np.int64)
    cur = 0
    gap = 0
    for i in range(n):
        if in_belt[i]:
            if cur > 0:
                # Bridge the gap (gap < max_interruption here, since a
                # gap >= max_interruption would have reset cur to 0).
                cur += gap + 1
            else:
                cur = 1  # fresh start after a break (or at the beginning)
            gap = 0
        else:
            gap += 1
            if gap >= max_interruption:
                cur = 0
        run[i] = cur
    return run


# ---------------------------------------------------------------------------
#  Cross-kind oscillation filter (backward-only)
# ---------------------------------------------------------------------------

def _drop_oscillating_backward(
    floor_cands: list[dict], peak_cands: list[dict],
    gap: int = OSCILLATION_GAP_DAYS,
) -> tuple[list[dict], list[dict]]:
    """Backward-only oscillation filter.

    Drop a candidate if an opposite-kind candidate exists within the
    previous ``gap`` trading days (rapid peak↔floor switching = a flat /
    noise region where no meaningful trend extreme exists).

    Uses "last seen" index (always updated, even for dropped candidates)
    so a chain of rapid alternation (peak→floor→peak→floor, each within
    `gap` of the previous) is dropped in full. Pure-kind proximity (two
    floors or two peaks within `gap`) is NOT filtered here — that is
    handled later by the within-kind dedup.

    Returns (surviving_floor_cands, surviving_peak_cands).
    """
    if not floor_cands and not peak_cands:
        return [], []

    tagged: list[tuple] = []  # (idx, kind, cand)
    for c in floor_cands:
        tagged.append((c["_idx"], "down", c))
    for c in peak_cands:
        tagged.append((c["_idx"], "up", c))
    tagged.sort(key=lambda x: x[0])

    keep_floors: list[dict] = []
    keep_peaks: list[dict] = []
    last_floor_idx = None
    last_peak_idx = None
    for idx, kind, cand in tagged:
        if kind == "down":
            if last_peak_idx is not None and (idx - last_peak_idx) <= gap:
                last_floor_idx = idx  # dropped, but still updates "last seen"
                continue
            keep_floors.append(cand)
            last_floor_idx = idx
        else:  # "up"
            if last_floor_idx is not None and (idx - last_floor_idx) <= gap:
                last_peak_idx = idx  # dropped, but still updates "last seen"
                continue
            keep_peaks.append(cand)
            last_peak_idx = idx
    return keep_floors, keep_peaks


# ---------------------------------------------------------------------------
#  Within-kind dedup (30-day clusters, keep most extreme — backward)
# ---------------------------------------------------------------------------

def _deduplicate_backward(
    cands: list[dict], direction: str, max_gap: int = EXTREME_MIN_GAP_DAYS,
) -> list[dict]:
    """Cluster consecutive same-kind candidates within ``max_gap`` trading
    days of the cluster's current survivor; keep the most extreme per
    cluster (min for direction='down', max for direction='up').

    Walks forward (sorted by trading-day index). A candidate within
    ``max_gap`` trading days of the current survivor joins the cluster
    (the survivor updates if the candidate is more extreme). A candidate
    beyond ``max_gap`` closes the cluster (survivor emitted) and starts a
    new one.

    Because candidates are sorted by date, each candidate is only
    compared to the previous cluster's survivor (which is earlier) — the
    selection is backward-only.
    """
    if len(cands) <= 1:
        return list(cands)

    sorted_cands = sorted(cands, key=lambda c: c["_idx"])
    result: list[dict] = []
    pending = dict(sorted_cands[0])
    pending_idx = pending["_idx"]

    for cand in sorted_cands[1:]:
        c_idx = cand["_idx"]
        if c_idx - pending_idx <= max_gap:
            # Within the cluster window — keep the more extreme value
            # (min for floors, max for peaks).
            keep_new = (
                cand["extreme_val"] < pending["extreme_val"]
                if direction == "down"
                else cand["extreme_val"] > pending["extreme_val"]
            )
            if keep_new:
                pending = dict(cand)
                pending_idx = c_idx
        else:
            # Beyond the window — close this cluster, start a new one.
            result.append(pending)
            pending = dict(cand)
            pending_idx = c_idx

    result.append(pending)
    return result


# ---------------------------------------------------------------------------
#  nearby_extreme_date computation (floors only, backward-only)
# ---------------------------------------------------------------------------

def _compute_nearby_extreme_date(
    ex_idx: int, lows: np.ndarray, highs: np.ndarray,
    dates: np.ndarray, n: int,
    window: int = NEARBY_EXTREME_WINDOW_DAYS,
):
    """Backward-only: within the PREVIOUS ``window`` trading days of the
    extreme_date (a valley low), find the furthest date whose OHLC low is
    strictly lower than the valley's OHLC high.

    Only invoked for floors. Peaks do not compute a nearby_extreme_date
    (NULL).

    Scans outward from the extreme_date (distance = window down to 1) on
    the PAST side only. The furthest qualifying date is returned first.

    Returns the python date of the furthest qualifying past date, or None
    when no date (other than the extreme itself) qualifies.
    """
    ex_high = highs[ex_idx]
    if not np.isfinite(ex_high):
        return None

    for dist in range(window, 0, -1):
        neg_idx = ex_idx - dist
        if neg_idx < 0:
            break  # no more past dates
        lo_val = lows[neg_idx]
        if np.isfinite(lo_val) and lo_val < ex_high:
            return dates[neg_idx]

    return None
