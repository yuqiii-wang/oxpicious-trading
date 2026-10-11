"""Moneyness series — materialized oi_moneyness aggregates per (underlying,
date, expiry-month) group (analysis.options_moneyness_series) plus the per
(underlying, date) spot close (analysis.options_spot_daily).

Replicates the former LIVE api SQL (api/services/options.service.ts
computeMoneynessSeries) EXACTLY — the endpoint used to run this aggregate
per request over the full history (~4.8s cold on the biggest CFFEX
underlying); the daily build now materializes it and the API reads the
table:

  • valid rows   : expiry_date >= date AND 0 < implied_vol < 5
                   (NULL IV drops the row — fillna(0) sentinel compare)
  • weights      : GREATEST(open_interest, 1) — NULL OI votes with weight 1
  • per group    : n_valid = COUNT(*) of valid rows; wsum = Σ w;
                   wsum_k = Σ w·K (NULL strike contributes nothing — the
                   NaN product is skipped by the group sum, matching SQL
                   SUM over NULL); expiry_date = MIN over valid rows
  • active rows  : expiry_date >= date AND open_interest > 0 — the group's
                   call/put OI and OTM OI sums (OTM call: K >= S, OTM put:
                   K <= S; NULL strike/close make the comparison FALSE via
                   ±inf sentinels, matching SQL NULL-comparison semantics);
                   a group with no active rows stores 0s (the API maps the
                   live SQL's LEFT-JOIN NULLs to 0)
  • e_m          : (wsum_k / wsum) / day_spot_close, NULL when
                   n_valid < 3 or the day close is missing / <= 0
  • day spot     : per (underlying, date) MAX(underlying_close) over ALL
                   fetched rows (LEFT-join basis — the e_m gate's close);
                   options_spot_daily keeps only dates with a settlement
                   row (INNER basis — the response spot axis, whose dates
                   without settlement rows never rendered)

GPU layout: whole-frame pandas — ONE full-frame assign of where-masked
sum columns, ONE groupby per aggregation basis (valid rows / active
columns / day spot), merges to join them. Host numpy appears only for
the month-truncation of expiry_date (datetime64[M] cast, then [ns] for
the frame) — the sanctioned boundary pattern.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import should_use_gpu  # noqa: F401 — per project convention
from _common.df_utils import host_array
from _common.labels import OPTION_TYPE_CODE


def _month_trunc_ns(s: pd.Series) -> np.ndarray:
    """Month-start of a datetime64 column as a host ``datetime64[ns]``
    array (ONE transfer + datetime-to-datetime casts — value-preserving)."""
    arr = host_array(s.to_numpy())
    return arr.astype("datetime64[M]").astype("datetime64[ns]")


def compute_options_moneyness_series(
    df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Compute the materialized moneyness series tables.

    Args:
        df: DataFrame with columns date, contract_code, option_type,
            underlying_code, expiry_date, strike_price, underlying_close,
            open_interest, implied_vol (LEFT-join basis — NULLs preserved
            as NaN; dates datetime64).

    Returns:
        (groups, spot) — groups with MONEYNESS_RESULT_COLUMNS, spot with
        SPOT_RESULT_COLUMNS.
    """
    from analysis.options.config import (
        MONEYNESS_MIN_VALID,
        MONEYNESS_RESULT_COLUMNS,
        SPOT_RESULT_COLUMNS,
    )

    if df.empty:
        return (
            pd.DataFrame(columns=MONEYNESS_RESULT_COLUMNS),
            pd.DataFrame(columns=SPOT_RESULT_COLUMNS),
        )

    key3 = ["underlying_code", "date", "expiry_month"]
    key2 = ["underlying_code", "date"]
    df = df.assign(expiry_month=_month_trunc_ns(df["expiry_date"]))

    # ---- valid-row aggregates (per_group) --------------------------------
    iv = df["implied_vol"].fillna(0.0)
    valid = (
        (df["expiry_date"] >= df["date"]) & (iv > 0) & (iv < 5)
    )
    v = df.loc[valid]
    w = v["open_interest"].fillna(1.0).clip(lower=1.0)
    # _one: int64 row counter — cuDF-native group count (a ("col","size")
    # named agg on a str column falls back to host pandas).
    v = v.assign(_w=w, _wk=w * v["strike_price"], _one=np.int64(1))
    per_group = v.groupby(key3, as_index=False, sort=False).agg(
        expiry_date=("expiry_date", "min"),
        n_valid=("_one", "sum"),
        wsum=("_w", "sum"),
        wsum_k=("_wk", "sum"),
    )

    # ---- active-row OI sums (active_agg) ---------------------------------
    # NULL OI/strike/close rows sit out via fillna sentinels — pure bool
    # masks (cudf rule) whose False side matches the SQL NULL-comparison
    # semantics (NULL compares to nothing; CASE falls to ELSE 0).
    active = (df["expiry_date"] >= df["date"]) & (
        df["open_interest"].fillna(0.0) > 0
    )
    is_call = df["option_type"] == OPTION_TYPE_CODE["CALL"]
    is_put = ~is_call
    strike = df["strike_price"]
    close = df["underlying_close"]
    call_otm_m = strike.fillna(-np.inf) >= close.fillna(np.inf)
    put_otm_m = strike.fillna(np.inf) <= close.fillna(-np.inf)
    oi_active = df["open_interest"].where(active)
    act = df.assign(
        call_oi=oi_active.where(is_call),
        put_oi=oi_active.where(is_put),
        call_otm_oi=oi_active.where(is_call & call_otm_m),
        put_otm_oi=oi_active.where(is_put & put_otm_m),
    )
    active_agg = act.groupby(key3, as_index=False, sort=False).agg(
        call_oi=("call_oi", "sum"),
        put_oi=("put_oi", "sum"),
        call_otm_oi=("call_otm_oi", "sum"),
        put_otm_oi=("put_otm_oi", "sum"),
    )

    # ---- day spot (LEFT basis — the e_m close) + spot table (INNER) ------
    day_spot = df.groupby(key2, as_index=False, sort=False).agg(
        day_close=("underlying_close", "max"),
    )
    settled = df.loc[df["underlying_close"].notna()]
    spot = settled.groupby(key2, as_index=False, sort=False).agg(
        close=("underlying_close", "max"),
    )

    # ---- join + e_m gate ---------------------------------------------------
    groups = per_group.merge(day_spot, on=key2, how="left")
    groups = groups.merge(active_agg, on=key3, how="left")
    for c in ("call_oi", "put_oi", "call_otm_oi", "put_otm_oi"):
        groups[c] = groups[c].fillna(0.0)

    em = (groups["wsum_k"] / groups["wsum"]) / groups["day_close"]
    groups["e_m"] = em.where(
        (groups["n_valid"] >= MONEYNESS_MIN_VALID)
        & (groups["day_close"].fillna(0.0) > 0)
    )

    groups = groups[MONEYNESS_RESULT_COLUMNS].sort_values(
        ["underlying_code", "date", "expiry_month"]
    ).reset_index(drop=True)
    spot = spot[SPOT_RESULT_COLUMNS].sort_values(
        ["underlying_code", "date"]
    ).reset_index(drop=True)
    return groups, spot
