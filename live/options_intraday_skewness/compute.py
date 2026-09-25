"""Vectorized intraday skewness computation for live.options_intraday_skewness.

Same metric semantics as the daily pipeline (analyze.options, skew_type =
'oi_moneyness') and the browser chart it feeds (computeOiWeightedSkew):

    E[M](t, g) = SUM(oi * K / S(t)) / SUM(oi)   per expiry group g
    skew_price = S(t) * E[M]      (the plotted skew-adjusted price level)
    skew_pct   = (E[M] - 1) * 100 (positioning skew vs ATM, in percent)

Contract weights: oi(t) = oi_base + cum_volume(t) floored at 1 lot — a
zero-OI contract must not bias the mean (the daily pipeline floors for
the same reason). A contract votes only while its expiry is active
(expiry_date >= the target date); OTM shares use the active rows with
raw OI > 0. No IV gate — oi_moneyness is IV-free (analyze.options'
canonical oi_moneyness implementation filters nothing else), so venues
without calibrated greeks (CFFEX) still get a series.

The mean row (sentinel expiry) blends ALL active groups: E[M] over every
valid contract of the underlying, not a weighted average of group E[M]s
— identical to the browser chart's aggregate curve.
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional

import numpy as np
import pandas as pd

from live.options_intraday_skewness.config import (
    MEAN_EXPIRY,
    MIN_GROUP_ROWS,
    PRICE_SCALE,
    SKEW_TYPE,
)

# Column layout of the upsert records — the FIRST record's key order is
# what bulk_upsert_async builds the INSERT from; every record carries the
# same keys in the same order. The identity columns (underlying_code /
# date / skew_type) are constant per run and added in _to_plain_records;
# the frames carry only the DATA_COLUMNS.
RECORD_COLUMNS = (
    "underlying_code",
    "date",
    "time",
    "expiry_date",
    "skew_type",
    "spot",
    "skew_price",
    "skew_pct",
    "oi_total",
    "otm_call_share",
    "otm_put_share",
)
DATA_COLUMNS = tuple(
    c for c in RECORD_COLUMNS
    if c not in ("underlying_code", "date", "skew_type")
)


def compute_skewness_frame(
    contracts: pd.DataFrame,
    bars: pd.DataFrame,
    cum_vols: pd.DataFrame,
    underlying: str,
    trade_date: _dt.date,
    strike_scale: float = PRICE_SCALE,
) -> list[dict]:
    """Compute the day's series records for ONE underlying.

    Args:
        contracts: prev-day snapshot frame from fetch.fetch_contracts —
            [contract_code, underlying_code, option_type, expiry_date,
             strike_price, implied_vol, oi_base].
        bars: the day's spot bars — [time, close].
        cum_vols: day-cumulative per-contract volume —
            [contract_code, time, cum_vol] (empty frame when the day has
            no streamed option bars — OI then stays at the flat base).
        underlying: the 6-digit option underlying to scope to.
        trade_date: the target trading day.
        strike_scale: divisor bringing strike_price into the spot's unit.
            ETF-target strikes are stored in 厘 (1/1000 yuan, the repo's
            PRICE_SCALE); INDEX-target (CFFEX) strikes are raw index
            points — scale 1.0.

    Returns:
        list of plain-scalar dicts (RECORD_COLUMNS layout, numpy/NaN
        scrubbed) ready for bulk_upsert_async; [] when nothing qualifies.
    """
    if bars.empty or contracts.empty:
        return []

    c = contracts[contracts["underlying_code"] == underlying].copy()
    if c.empty:
        return []
    # Strikes arrive in venue units (厘 for ETF targets, index points for
    # INDEX targets) — yuan/points like the spot series from here on.
    c["strike"] = c["strike_price"] / strike_scale

    # ---- Contract × bar grid, OI(t) and vote validity -------------------
    grid = bars.merge(c, how="cross")
    if not cum_vols.empty:
        grid = grid.merge(cum_vols, on=["contract_code", "time"], how="left")
    else:
        grid["cum_vol"] = 0.0
    grid["cum_vol"] = grid["cum_vol"].fillna(0.0)
    grid["oi_t"] = grid["oi_base"] + grid["cum_vol"]
    grid["w"] = grid["oi_t"].clip(lower=1.0)
    grid["m"] = grid["strike"] / grid["close"]
    grid["wm"] = grid["w"] * grid["m"]

    active = grid["expiry_date"] >= trade_date
    # NO IV filter here: oi_moneyness never consumes IV (the metric is
    # pure OI × strike geometry), and analyze.options/compute/skewness.py
    # — the metric's canonical implementation — filters nothing but the
    # OI floor. (The browser chart's extra IV-validity gate would zero
    # out whole venues — CFFEX rows carry no calibrated IV at all.)

    # ---- Per-(time, expiry) OI-weighted mean moneyness ------------------
    v = grid[active]
    agg = (
        v.groupby(["time", "expiry_date"], sort=False)
        .agg(n=("contract_code", "count"), w_sum=("w", "sum"), wm_sum=("wm", "sum"))
        .reset_index()
    )
    agg = agg[agg["n"] >= MIN_GROUP_ROWS]
    if agg.empty:
        return []
    agg["em"] = agg["wm_sum"] / agg["w_sum"]

    # ---- OTM shares from ALL active rows (valid or not, raw OI > 0) -----
    act = grid[active & (grid["oi_t"] > 0)].copy()
    is_call = act["option_type"] == "CALL"
    act["c_oi"] = act["oi_t"].where(is_call, 0.0)
    act["c_oi_otm"] = act["oi_t"].where(is_call & (act["strike"] >= act["close"]), 0.0)
    act["p_oi"] = act["oi_t"].where(~is_call, 0.0)
    act["p_oi_otm"] = act["oi_t"].where(~is_call & (act["strike"] <= act["close"]), 0.0)
    otm = (
        act.groupby(["time", "expiry_date"], sort=False)
        .agg(c_tot=("c_oi", "sum"), c_otm=("c_oi_otm", "sum"),
             p_tot=("p_oi", "sum"), p_otm=("p_oi_otm", "sum"))
        .reset_index()
    )
    agg = agg.merge(otm, on=["time", "expiry_date"], how="left")

    # ---- Spot join + skew levels ----------------------------------------
    agg = agg.merge(bars.rename(columns={"close": "spot"}), on="time", how="left")
    agg["skew_price"] = agg["spot"] * agg["em"]
    agg["skew_pct"] = (agg["em"] - 1.0) * 100.0
    agg["otm_call_share"] = agg["c_otm"] / agg["c_tot"].where(agg["c_tot"] > 0)
    agg["otm_put_share"] = agg["p_otm"] / agg["p_tot"].where(agg["p_tot"] > 0)

    # ---- MEAN row per time: every valid contract blended ----------------
    mean = (
        v.groupby("time", sort=False)
        .agg(w_sum=("w", "sum"), wm_sum=("wm", "sum"))
        .reset_index()
    )
    mean = mean.merge(bars.rename(columns={"close": "spot"}), on="time", how="left")
    mean["em"] = mean["wm_sum"] / mean["w_sum"]
    mean["skew_price"] = mean["spot"] * mean["em"]
    mean["skew_pct"] = (mean["em"] - 1.0) * 100.0
    # The mean row carries no single-group OI level / OTM share.
    mean["expiry_date"] = MEAN_EXPIRY
    mean["oi_total"] = np.nan
    mean["otm_call_share"] = np.nan
    mean["otm_put_share"] = np.nan

    # Per-expiry rows keep the group's total OI weight (the UI's
    # line-width encoding).
    per_expiry = agg.rename(columns={"w_sum": "oi_total"})

    out = pd.concat(
        [
            per_expiry[list(DATA_COLUMNS)],
            mean[list(DATA_COLUMNS)],
        ],
        ignore_index=True,
    )
    return _to_plain_records(out, underlying, trade_date)


def _to_plain_records(
    df: pd.DataFrame, underlying: str, trade_date: _dt.date
) -> list[dict]:
    """Scrub the frame into bulk_upsert_async-ready dicts: python scalars
    only (asyncpg rejects numpy types) and NaN → None (SQL NULL)."""
    df = df.astype(object)
    df = df.where(df.notna(), None)
    records: list[dict] = []
    for row in df.to_dict("records"):
        rec: dict = {
            "underlying_code": underlying,
            "date": trade_date,
            "skew_type": SKEW_TYPE,
        }
        for k in DATA_COLUMNS:
            v = row[k]
            if isinstance(v, np.generic):
                v = v.item()
            if isinstance(v, float) and np.isnan(v):
                v = None
            rec[k] = v
        records.append(rec)
    return records
