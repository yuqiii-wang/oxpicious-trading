"""Options wall zones per expiry group (analysis.options_walls).

Single wall type:
  zone — strength-scored OI wall ZONE with lifecycle: strikes with
         OI >= 2% of chain OI are clustered into adjacent-strike
         zones (<= 2 empty strike intervals apart); the dominant
         zone per side is emitted with wall_low/high/center (raw
         strike units), mass_share (zone OI / chain OI, eligible
         >= 6%), gap_pct (signed center-vs-spot distance), a
         lifecycle state machine (ACTIVE / ERODED / BREACHED) with
         day-over-day >=50% strike-range overlap persistence
         (days_persisted), and a strength score
         = mass_share * exp(-max(gap_pct,0)/8)
           * (1 + 0.25*min(days_persisted,20)/20).
         Thresholds empirically calibrated on 4,115 (date,
         nearest-expiry) observations 2020-2026 — see
         analyze/options/config.py ZONE_* constants.

(The legacy 80pct and large_num wall types were removed — the zone
wall supersedes them; both were positioning boundaries without OI
mass, spot reference or persistence, none of which carried
predictive power in the 2020-2026 hold-rate study.)

Fully vectorized: wide groupby-transform / run-length cumsum passes
over the whole frame — no per-group Python loops (cudf.pandas safe):

  - one CALL/PUT outer-merge on (key, strike) builds the union frame;
  - zone clustering uses a groupby diff/run-length cumsum (break
    where the strike gap exceeds N intervals), one named agg per
    zone, a groupby idxmax dominant pick, and a shift-based
    day-over-day overlap match for the lifecycle.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from analyze.options.compute._shared import _apply_open_expiry_collapse


_WALLS_GROUP_KEY = ["date", "underlying_code", "expiry_date"]

# Lifecycle sort/match key: zone identity within one underlying+expiry.
_ZONE_LIFECYCLE_KEY = ["underlying_code", "expiry_date", "option_type"]


def _zone_walls(
    union: pd.DataFrame,
    spot: pd.DataFrame,
) -> pd.DataFrame:
    """Strength-scored OI wall zones per (group, side) — wall_type='zone'.

    Clustering (per expiry group, per side):
      1. chain OI = total (call+put) OI across ALL strikes of the group;
      2. a strike enters a zone only if its side OI >= ZONE_MIN_STRIKE_MASS
         of chain OI;
      3. consecutive selected strikes merge into one zone while the gap
         between them is <= ZONE_MERGE_MAX_GAPS strike intervals (the
         interval is the per-group median strike diff);
      4. per (group, side) the zone with the max total OI is the
         DOMINANT wall zone (ties -> lowest strikes via idxmax order).

    Eligibility: mass_share >= ZONE_ELIGIBLE_MASS_SHARE AND the zone
    center within ZONE_MAX_GAP_PCT of spot (rows without spot are
    dropped — gap/state would be undefined).

    Lifecycle (sorted by underlying, expiry, side, date; one row per
    group+side+date after the dominant pick): the previous trading
    row's zone matches when the strike-range overlap >= 50% of the
    smaller range; matched rows continue their persistence run
    (days_persisted), else a new run starts at 1. State: BREACHED when
    spot is beyond the zone edge, else ERODED when mass fell below
    ZONE_ERODE_RATIO of the previous row's mass, else ACTIVE.

    Args:
        union: strike-sorted union frame (group key + strike_price +
            call_oi + put_oi, zero-filled, total OI > 0).
        spot: [date, underlying_code, _spot] median underlying close
            per (date, underlying).

    Returns:
        DataFrame with WALLS_RESULT_COLUMNS rows for wall_type='zone'
        (CALL and PUT), possibly empty.
    """
    from analyze.options.config import (
        WALL_TYPE_ZONE,
        WALL_STATE_ACTIVE,
        WALL_STATE_ERODED,
        WALL_STATE_BREACHED,
        ZONE_MIN_STRIKE_MASS,
        ZONE_MERGE_MAX_GAPS,
        ZONE_ELIGIBLE_MASS_SHARE,
        ZONE_MAX_GAP_PCT,
        ZONE_ERODE_RATIO,
        ZONE_PERSIST_MATCH,
        ZONE_GAP_DECAY,
        ZONE_PERSIST_WINDOW,
        ZONE_PERSIST_BONUS,
        PRICE_SCALE,
    )

    u = union
    gkeys = _WALLS_GROUP_KEY

    # ---- chain OI + per-group strike interval ---------------------------
    u = u.copy()
    u["_chain_oi"] = u.groupby(gkeys, sort=False)[
        "call_oi"
    ].transform("sum") + u.groupby(gkeys, sort=False)["put_oi"].transform(
        "sum"
    )
    prev_strike = u.groupby(gkeys, sort=False)["strike_price"].shift(1)
    u["_sdiff"] = u["strike_price"] - prev_strike
    # median of consecutive diffs ~= one strike interval (NaN-safe: the
    # first row of each group and single-strike groups stay NaN -> 0,
    # so any real gap breaks the zone there).
    interval = (
        u.groupby(gkeys, sort=False)["_sdiff"]
        .median()
        .reset_index()
        .rename(columns={"_sdiff": "_interval"})
    )
    interval["_interval"] = interval["_interval"].fillna(0.0)
    u = u.merge(interval, on=gkeys, how="left")

    frames: list[pd.DataFrame] = []
    for oi_col, ot, breacher in (
        ("call_oi", "CALL", "above"),   # CALL zone breached when spot ABOVE the zone
        ("put_oi", "PUT", "below"),     # PUT zone breached when spot BELOW the zone
    ):
        s = u.loc[
            u[oi_col] > 0,
            gkeys + ["strike_price", oi_col, "_chain_oi", "_interval"],
        ].copy()
        s = s[
            s[oi_col] >= ZONE_MIN_STRIKE_MASS * s["_chain_oi"]
        ]
        if s.empty:
            continue

        # ---- cluster: run-length ids over interval-sized gaps ----------
        s = s.sort_values(gkeys + ["strike_price"]).reset_index(drop=True)
        gap = s["strike_price"] - s.groupby(
            gkeys, sort=False
        )["strike_price"].shift(1)
        brk = (gap > ZONE_MERGE_MAX_GAPS * s["_interval"]) & gap.notna()
        s["_brk"] = brk.astype("int64")
        s["_zid"] = s.groupby(gkeys, sort=False)["_brk"].cumsum()

        # ---- zone aggregate + dominant pick -----------------------------
        s["_w"] = s["strike_price"] * s[oi_col]
        z = s.groupby(
            gkeys + ["_zid"], sort=False
        ).agg(
            wall_low=("strike_price", "min"),
            wall_high=("strike_price", "max"),
            wall_oi=(oi_col, "sum"),
            wall_center=("_w", "sum"),
            _chain=("_chain_oi", "first"),
        ).reset_index()
        z["wall_center"] = z["wall_center"] / z["wall_oi"]
        idx = z.groupby(gkeys, sort=False)["wall_oi"].idxmax()
        z = z.loc[idx].reset_index(drop=True)
        z["option_type"] = ot

        # ---- gap / breach / eligibility ---------------------------------
        z = z.merge(spot, on=["date", "underlying_code"], how="left")
        sp = z["_spot"]
        if breacher == "above":
            gap_pct = (z["wall_center"] - sp) / sp * 100.0
            breached = sp > z["wall_high"]
        else:
            gap_pct = (sp - z["wall_center"]) / sp * 100.0
            breached = sp < z["wall_low"]
        mass_share = z["wall_oi"] / z["_chain"]
        eligible = (
            (mass_share >= ZONE_ELIGIBLE_MASS_SHARE)
            & (sp > 0)
            & (gap_pct.abs() <= ZONE_MAX_GAP_PCT)
        )
        z = z.loc[eligible].reset_index(drop=True)
        if z.empty:
            continue
        z["mass_share"] = mass_share.loc[z.index]
        z["gap_pct"] = gap_pct.loc[z.index]
        z["_breached"] = breached.loc[z.index]

        # ---- lifecycle: overlap match + persistence run -----------------
        z = z.sort_values(
            _ZONE_LIFECYCLE_KEY + ["date"]
        ).reset_index(drop=True)
        g = z.groupby(_ZONE_LIFECYCLE_KEY, sort=False)
        prev_low = g["wall_low"].shift(1)
        prev_high = g["wall_high"].shift(1)
        prev_mass = g["mass_share"].shift(1)
        overlap = (
            np.minimum(z["wall_high"], prev_high)
            - np.maximum(z["wall_low"], prev_low)
        ).clip(lower=0.0)
        rng = z["wall_high"] - z["wall_low"]
        prng = prev_high - prev_low
        denom = np.maximum(np.minimum(rng, prng), 1e-9)
        matched = (overlap / denom >= ZONE_PERSIST_MATCH).fillna(False)
        # zero-range (single-strike) zones match only on the same strike
        matched = matched | (
            (rng <= 0) & (prng <= 0) & (z["wall_low"] == prev_low)
        )
        matched = matched.fillna(False)

        # days_persisted = position within the current matched run.
        # Run-length via cumsum of the run-start indicator (no scans).
        t = z.groupby(_ZONE_LIFECYCLE_KEY, sort=False).cumcount()
        z["_t"] = t
        run_start = (~matched).astype("int64")
        z["_rid"] = run_start.groupby(
            [z[c] for c in _ZONE_LIFECYCLE_KEY], sort=False
        ).cumsum()
        first_t = z.groupby(
            _ZONE_LIFECYCLE_KEY + ["_rid"], sort=False
        )["_t"].transform("min")
        z["days_persisted"] = (z["_t"] - first_t + 1).astype("int64")

        # ---- state machine ----------------------------------------------
        eroded = matched & (
            z["mass_share"] < ZONE_ERODE_RATIO * prev_mass
        )
        eroded = eroded.fillna(False)
        state = pd.Series(WALL_STATE_ACTIVE, index=z.index)
        state = state.where(~eroded, WALL_STATE_ERODED)
        state = state.where(
            ~z["_breached"].fillna(False), WALL_STATE_BREACHED
        )
        z["state"] = state

        # ---- strength score ----------------------------------------------
        gap_pos = z["gap_pct"].clip(lower=0.0)
        persist_frac = z["days_persisted"].clip(
            upper=ZONE_PERSIST_WINDOW
        ) / float(ZONE_PERSIST_WINDOW)
        z["strength_score"] = (
            z["mass_share"]
            * np.exp(-gap_pos / ZONE_GAP_DECAY)
            * (1.0 + ZONE_PERSIST_BONUS * persist_frac)
        )

        frames.append(pd.DataFrame({
            "date": z["date"],
            "option_type": z["option_type"],
            "underlying_code": z["underlying_code"],
            "expiry_date": z["expiry_date"],
            "wall_type": WALL_TYPE_ZONE,
            "wall_strike": z["wall_center"] / PRICE_SCALE,
            "wall_oi": z["wall_oi"],
            "mean_oi": np.nan,
            "threshold": ZONE_ELIGIBLE_MASS_SHARE,
            "wall_low": z["wall_low"],
            "wall_high": z["wall_high"],
            "wall_center": z["wall_center"],
            "mass_share": z["mass_share"],
            "gap_pct": z["gap_pct"],
            "days_persisted": z["days_persisted"],
            "state": z["state"],
            "strength_score": z["strength_score"],
        }))

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def compute_options_walls(df: pd.DataFrame) -> pd.DataFrame:
    """Compute per-expiry-group options wall zones (wall_type='zone').

    For each (date, underlying_code, expiry_date), aggregates OI by strike
    separately for CALL and PUT, unions the per-side strike frames and
    delegates to :func:`_zone_walls` (adjacent-strike OI cluster per side
    with strength score and lifecycle).

    Args:
        df: DataFrame with columns:
            date, contract_code, option_type, underlying_code, expiry_date,
            strike_price, open_interest, underlying_close.

    Returns:
        DataFrame with WALLS_RESULT_COLUMNS — one row per
        (date, option_type, underlying_code, expiry_date) for the
        dominant CALL and PUT zone.
    """
    from analyze.options.config import WALLS_RESULT_COLUMNS

    empty = pd.DataFrame(columns=WALLS_RESULT_COLUMNS)
    if df.empty:
        return empty

    # Open-expiry collapse (vectorized shared helper).
    data = _apply_open_expiry_collapse(df.copy())
    if data.empty:
        return empty

    # Aggregate OI by (date, underlying, expiry, option_type, strike)
    agg = (
        data.groupby(
            ["date", "underlying_code", "expiry_date", "option_type",
             "strike_price"],
            as_index=False,
            sort=False,
        )
        .agg(open_interest=("open_interest", "sum"))
    )

    # Per-side strike frames (a strike belongs to a side only if that
    # side actually has a row there).
    call_side = agg.loc[
        agg["option_type"] == "CALL",
        _WALLS_GROUP_KEY + ["strike_price", "open_interest"],
    ].rename(columns={"open_interest": "call_oi"})
    put_side = agg.loc[
        agg["option_type"] == "PUT",
        _WALLS_GROUP_KEY + ["strike_price", "open_interest"],
    ].rename(columns={"open_interest": "put_oi"})

    # Union of strikes per group; strikes with zero total OI are dropped.
    union = call_side.merge(
        put_side, on=_WALLS_GROUP_KEY + ["strike_price"], how="outer",
    )
    if union.empty:
        return empty
    union["call_oi"] = union["call_oi"].fillna(0.0)
    union["put_oi"] = union["put_oi"].fillna(0.0)
    total = union["call_oi"] + union["put_oi"]
    union = union[total > 0].copy()
    if union.empty:
        return empty
    union = union.sort_values(
        _WALLS_GROUP_KEY + ["strike_price"]
    ).reset_index(drop=True)

    # ---- zone walls (strength-scored, with lifecycle) --------------------
    # Spot = median underlying close per (date, underlying); rows without
    # a positive spot are dropped inside _zone_walls (gap undefined).
    spot = (
        data.loc[data["underlying_close"] > 0]
        .groupby(["date", "underlying_code"], sort=False)["underlying_close"]
        .median()
        .reset_index()
        .rename(columns={"underlying_close": "_spot"})
    )
    result = _zone_walls(union, spot)
    if result.empty:
        return empty

    result = result[WALLS_RESULT_COLUMNS].copy()
    result = result.sort_values(
        ["date", "option_type", "underlying_code", "expiry_date", "wall_type"]
    ).reset_index(drop=True)

    return result
