"""Options wall levels per expiry group (analysis.options_walls).

Two wall types:
  80pct    — contiguous zone from one end of the chain where one side
             dominates >= 80% of total OI at each strike, interpolated
             boundary at the threshold.
  large_num — strike with the max OI among those exceeding 70% of the
             mean OI across all strikes in the expiry group.
"""
from __future__ import annotations

import pandas as pd

from analyze.options.compute._shared import _compute_mean_expiry_dates


def compute_options_walls(df: pd.DataFrame) -> pd.DataFrame:
    """Compute per-expiry-group options wall levels for both 80pct and
    large_num wall types.

    For each (date, underlying_code, expiry_date), aggregates OI by strike
    separately for CALL and PUT, then computes:

    80pct wall:
      - Bear (PUT): contiguous zone from lowest strike where putPct >= 80%,
        linearly interpolated boundary at the 80% threshold.
      - Bull (CALL): contiguous zone from highest strike where putPct <= 20%,
        linearly interpolated boundary at the 20% threshold.

    large_num wall:
      - For each option type, compute mean OI across all strikes.
      - Filter strikes where OI > 70% of the mean value.
      - Pick the strike with the maximum OI among qualifying strikes.

    Args:
        df: DataFrame with columns:
            date, contract_code, option_type, underlying_code, expiry_date,
            strike_price, open_interest.

    Returns:
        DataFrame with WALLS_RESULT_COLUMNS — one row per
        (date, option_type, underlying_code, expiry_date, wall_type).
    """
    from analyze.options.config import (
        WALLS_RESULT_COLUMNS,
        WALL_TYPE_80PCT,
        WALL_TYPE_LARGE_NUM,
        PUT_PCT_RED,
        PUT_PCT_GREEN,
        LARGE_NUM_MEAN_FRACTION,
        PRICE_SCALE,
    )

    if df.empty:
        return pd.DataFrame(columns=WALLS_RESULT_COLUMNS)

    # Copy and apply open-expiry collapse
    data = df.copy()
    dataset_max_date = data["date"].max()
    mean_map = _compute_mean_expiry_dates(data)
    open_mask = data["expiry_date"] > dataset_max_date
    if open_mask.any():
        data.loc[open_mask, "expiry_date"] = data.loc[open_mask].apply(
            lambda r: mean_map.get((r["option_type"], r["underlying_code"]), r["expiry_date"]),
            axis=1,
        )

    # Aggregate OI by (date, underlying, expiry, option_type, strike)
    agg = (
        data.groupby(
            ["date", "underlying_code", "expiry_date", "option_type", "strike_price"],
            as_index=False,
            sort=False,
        )
        .agg(open_interest=("open_interest", "sum"))
    )

    # Build result rows
    result_rows: list[dict] = []

    # Group by (date, underlying_code, expiry_date) to process each expiry group
    for (date, underlying, expiry), group in agg.groupby(
        ["date", "underlying_code", "expiry_date"], sort=False
    ):
        # Separate CALL and PUT strikes
        call_data = (
            group[group["option_type"] == "CALL"]
            .groupby("strike_price", as_index=False)["open_interest"]
            .sum()
        )
        put_data = (
            group[group["option_type"] == "PUT"]
            .groupby("strike_price", as_index=False)["open_interest"]
            .sum()
        )

        call_oi: dict[float, float] = dict(
            zip(call_data["strike_price"], call_data["open_interest"])
        )
        put_oi: dict[float, float] = dict(
            zip(put_data["strike_price"], put_data["open_interest"])
        )

        # All strikes (union of call and put strikes)
        all_strikes = sorted(set(list(call_oi.keys()) + list(put_oi.keys())))
        if not all_strikes:
            continue

        # Build per-strike data with putPct
        strike_data: list[dict] = []
        for k in all_strikes:
            c_oi = call_oi.get(k, 0)
            p_oi = put_oi.get(k, 0)
            total = c_oi + p_oi
            if total <= 0:
                continue
            strike_data.append({
                "strike": k,
                "call_oi": c_oi,
                "put_oi": p_oi,
                "total_oi": total,
                "put_pct": (p_oi / total) * 100,
            })

        if not strike_data:
            continue

        # Sort by strike ascending
        strike_data.sort(key=lambda x: x["strike"])

        # ---- 80% wall computation ----
        # Bear (PUT) wall: contiguous zone from lowest strike where putPct >= 80%
        bear_80pct_strike = _compute_80pct_wall(strike_data, side="bear")
        if bear_80pct_strike is not None:
            bear_oi_val = put_oi.get(bear_80pct_strike, 0)
            result_rows.append({
                "date": date,
                "option_type": "PUT",
                "underlying_code": underlying,
                "expiry_date": expiry,
                "wall_type": WALL_TYPE_80PCT,
                "wall_strike": bear_80pct_strike / PRICE_SCALE,
                "wall_oi": bear_oi_val,
                "mean_oi": None,
                "threshold": PUT_PCT_RED / 100.0,
            })

        # Bull (CALL) wall: contiguous zone from highest strike where putPct <= 20%
        bull_80pct_strike = _compute_80pct_wall(strike_data, side="bull")
        if bull_80pct_strike is not None:
            bull_oi_val = call_oi.get(bull_80pct_strike, 0)
            result_rows.append({
                "date": date,
                "option_type": "CALL",
                "underlying_code": underlying,
                "expiry_date": expiry,
                "wall_type": WALL_TYPE_80PCT,
                "wall_strike": bull_80pct_strike / PRICE_SCALE,
                "wall_oi": bull_oi_val,
                "mean_oi": None,
                "threshold": PUT_PCT_GREEN / 100.0,
            })

        # ---- large_num wall computation ----
        # CALL large num wall
        call_ois = list(call_oi.values())
        if call_ois:
            call_mean = sum(call_ois) / len(call_ois)
            call_threshold = call_mean * LARGE_NUM_MEAN_FRACTION
            best_call_strike = None
            best_call_oi = 0
            for k, oi in call_oi.items():
                if oi >= call_threshold and oi > best_call_oi:
                    best_call_oi = oi
                    best_call_strike = k
            if best_call_strike is not None:
                result_rows.append({
                    "date": date,
                    "option_type": "CALL",
                    "underlying_code": underlying,
                    "expiry_date": expiry,
                    "wall_type": WALL_TYPE_LARGE_NUM,
                    "wall_strike": best_call_strike / PRICE_SCALE,
                    "wall_oi": best_call_oi,
                    "mean_oi": call_mean,
                    "threshold": LARGE_NUM_MEAN_FRACTION,
                })

        # PUT large num wall
        put_ois = list(put_oi.values())
        if put_ois:
            put_mean = sum(put_ois) / len(put_ois)
            put_threshold = put_mean * LARGE_NUM_MEAN_FRACTION
            best_put_strike = None
            best_put_oi = 0
            for k, oi in put_oi.items():
                if oi >= put_threshold and oi > best_put_oi:
                    best_put_oi = oi
                    best_put_strike = k
            if best_put_strike is not None:
                result_rows.append({
                    "date": date,
                    "option_type": "PUT",
                    "underlying_code": underlying,
                    "expiry_date": expiry,
                    "wall_type": WALL_TYPE_LARGE_NUM,
                    "wall_strike": best_put_strike / PRICE_SCALE,
                    "wall_oi": best_put_oi,
                    "mean_oi": put_mean,
                    "threshold": LARGE_NUM_MEAN_FRACTION,
                })

    if not result_rows:
        return pd.DataFrame(columns=WALLS_RESULT_COLUMNS)

    result = pd.DataFrame(result_rows)
    result = result[WALLS_RESULT_COLUMNS].copy()
    result = result.sort_values(
        ["date", "option_type", "underlying_code", "expiry_date", "wall_type"]
    ).reset_index(drop=True)

    return result


def _compute_80pct_wall(
    strike_data: list[dict],
    side: str,
) -> float | None:
    """Compute the 80% wall strike for one side (bear or bull).

    Args:
        strike_data: List of dicts with keys: strike, call_oi, put_oi,
            total_oi, put_pct. Sorted ascending by strike.
        side: "bear" for put wall (putPct >= 80%), "bull" for call wall
            (putPct <= 20%).

    Returns:
        Strike price (raw, in 厘) of the interpolated wall boundary,
        or None if no valid wall exists.
    """
    from analyze.options.config import PUT_PCT_RED, PUT_PCT_GREEN

    if not strike_data:
        return None

    if side == "bear":
        # Bear wall: put-dominant zone from lowest strike where putPct >= 80%
        # Find the contiguous run from the bottom
        threshold = PUT_PCT_RED
        idx = 0
        while idx < len(strike_data) and strike_data[idx]["put_pct"] >= threshold:
            idx += 1

        if idx == 0:
            return None  # No strike at the bottom meets the threshold

        if idx >= len(strike_data):
            return strike_data[-1]["strike"]  # Entire chain is dominated

        # Interpolate between strike_data[idx-1] and strike_data[idx]
        # At idx-1, put_pct >= threshold; at idx, put_pct < threshold
        a = strike_data[idx - 1]
        b = strike_data[idx]
        d_pct = b["put_pct"] - a["put_pct"]
        if d_pct == 0:
            return b["strike"]
        frac = (threshold - a["put_pct"]) / d_pct
        return a["strike"] + frac * (b["strike"] - a["strike"])

    else:  # bull
        # Bull wall: call-dominant zone from highest strike where putPct <= 20%
        # Find the contiguous run from the top
        threshold = PUT_PCT_GREEN
        idx = len(strike_data) - 1
        while idx >= 0 and strike_data[idx]["put_pct"] <= threshold:
            idx -= 1

        if idx == len(strike_data) - 1:
            return None  # No strike at the top meets the threshold

        if idx < 0:
            return strike_data[0]["strike"]  # Entire chain is dominated

        # Interpolate between strike_data[idx] and strike_data[idx+1]
        a = strike_data[idx]
        b = strike_data[idx + 1]
        d_pct = b["put_pct"] - a["put_pct"]
        if d_pct == 0:
            return b["strike"]
        frac = (threshold - a["put_pct"]) / d_pct
        return a["strike"] + frac * (b["strike"] - a["strike"])
