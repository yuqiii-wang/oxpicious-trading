"""CBOE VIX-style 30-day model-free implied-volatility index (per underlying).

compute_options_vol_index — prices each day's OTM strip off RAW settlement
quotes (no IV/delta calibration involved — the whole point of the model-free
methodology) and reduces it to a constant-30-day variance:

Per (date, underlying_code, expiry_date) group:
  F  = S * exp(r*T)                    (r = 0.02, repo convention)
  K0 = largest strike <= F             (fallback: smallest listed strike)
  Q(K) = put settle for K < K0, call settle for K > K0,
          average of both at K = K0    (CBOE white-paper convention)
  dK_i = half-gap to the neighboring strikes (one-sided at the strip ends)
  sigma^2 = (2/T) * e^(rT) * sum_i (dK_i / K_i^2) * Q(K_i)
            - (1/T) * (F/K0 - 1)^2

Per (date, underlying_code): the two expiries bracketing 30 days are
time-interpolated to a constant maturity:
  var30 = (var_near*T_near*(T_far-30) + var_far*T_far*(30-T_near))
          / ((T_far - T_near) * 30)            [all T in calendar days]
  vol_index_30d = 100 * sqrt(var30)            [vol points, comparable to VIX]
When no pair of expiries brackets 30 days (early listings), the single
expiry nearest 30 days is used (far_* columns stay NULL).

Adaptations vs the official CBOE recipe, documented in
docs/options_vol_smile_study.md: exchange settlement prices instead of
mid-quotes (no zero-bid truncation needed — settle > 0 enforced at fetch);
calendar-day T instead of minute precision; r fixed at 0.02 like the rest
of the repo's option analytics.

Venue units mirror ``compute_iv_and_greeks`` exactly: CFFEX index quotes
are native index points; SZSE ETF quotes are x1000 strike/underlying (厘)
and x10000 settle (-> yuan per share) — Q and K end up in the same
per-underlying-unit basis, which is all the ratio dK*K^-2*Q requires.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from analyze.options.config import (
    VOL_INDEX_RESULT_COLUMNS,
    VOL_INDEX_TARGET_DAYS,
)

# Matches black_scholes.DEFAULT_RISK_FREE_RATE used across options builds.
RISK_FREE_RATE = 0.02

# Venue unit scales (mirror compute_iv_and_greeks defaults).
_SZSE_PRICE_SCALE = 1000.0   # SZSE strike/underlying: 厘 -> yuan
_SZSE_OPT_SCALE = 10000.0    # SZSE settle -> yuan per share

_GROUP_KEY = ["date", "underlying_code", "expiry_date"]
_PAIR_KEY = ["date", "underlying_code"]

_DAYS_PER_YEAR = 365.0


def _strike_level_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Scale units and reduce CALL/PUT rows to one row per (group, strike).

    Returns a frame with S/T/F (group-level forward inputs) plus Q_put /
    Q_call per strike.
    """
    out = df[["date", "option_type", "underlying_code", "expiry_date",
              "days_to_expiry", "strike_price", "underlying_close",
              "settle", "underlying_target_type"]].copy()
    is_etf = out["underlying_target_type"].eq("ETF")
    price_scale = np.where(is_etf, _SZSE_PRICE_SCALE, 1.0)
    opt_scale = np.where(is_etf, _SZSE_OPT_SCALE, 1.0)
    out["S"] = out["underlying_close"].to_numpy() / price_scale
    out["K"] = out["strike_price"].to_numpy() / price_scale
    q = out["settle"].to_numpy() / opt_scale
    out["Q"] = q
    out["dte"] = out["days_to_expiry"]
    out["T"] = out["dte"] / _DAYS_PER_YEAR

    puts = out[out["option_type"] == "PUT"][_GROUP_KEY + ["K", "Q"]] \
        .rename(columns={"Q": "Q_put"})
    calls = out[out["option_type"] == "CALL"][_GROUP_KEY + ["K", "Q"]] \
        .rename(columns={"Q": "Q_call"})
    strikes = puts.merge(calls, on=_GROUP_KEY + ["K"], how="outer")

    meta = (
        out.groupby(_GROUP_KEY, as_index=False, sort=False)
        .agg(S=("S", "first"), T=("T", "first"), dte=("dte", "first"))
    )
    meta["F"] = meta["S"] * np.exp(RISK_FREE_RATE * meta["T"])
    return strikes.merge(meta, on=_GROUP_KEY, how="inner")


def _expiry_variances(strikes: pd.DataFrame) -> pd.DataFrame:
    """Model-free variance per (date, underlying, expiry) group.

    Implements the OTM-strip selection (puts below the forward, calls
    above, average at K0), the dK strike spacing and the variance formula
    — fully vectorized (groupby shifts for spacing, no row loops).
    """
    # K0: largest strike <= F (groups entirely above the forward fall back
    # to their smallest listed strike).
    below = strikes[strikes["K"] <= strikes["F"]]
    k0 = (
        below.groupby(_GROUP_KEY, as_index=False, sort=False)["K"]
        .max()
        .rename(columns={"K": "K0"})
    )
    kmin = (
        strikes.groupby(_GROUP_KEY, as_index=False, sort=False)["K"]
        .min()
        .rename(columns={"K": "K_min"})
    )
    strikes = strikes.merge(k0, on=_GROUP_KEY, how="left")
    strikes = strikes.merge(kmin, on=_GROUP_KEY, how="left")
    strikes["K0"] = strikes["K0"].fillna(strikes["K_min"])

    # OTM price per strike: put below K0, call above, average at K0.
    strikes["Q_o"] = np.select(
        [strikes["K"] < strikes["K0"], strikes["K"] > strikes["K0"]],
        [strikes["Q_put"], strikes["Q_call"]],
        default=(strikes["Q_put"] + strikes["Q_call"]) / 2.0,
    )
    sel = strikes[strikes["Q_o"].notna() & (strikes["Q_o"] > 0)].copy()
    if sel.empty:
        return pd.DataFrame(columns=_GROUP_KEY + ["dte", "var"])

    # Strike spacing: half-gap to neighbors within the group, one-sided at
    # the strip ends (first/last selected strike).
    sel = sel.sort_values(_GROUP_KEY + ["K"]).reset_index(drop=True)
    grp = sel.groupby(_GROUP_KEY, sort=False)["K"]
    prev = grp.shift(1)
    nxt = grp.shift(-1)
    sel["dK"] = (nxt - prev) / 2.0
    sel.loc[prev.isna(), "dK"] = nxt - sel["K"]
    sel.loc[nxt.isna(), "dK"] = sel["K"] - prev
    sel = sel[sel["dK"] > 0]
    if sel.empty:
        return pd.DataFrame(columns=_GROUP_KEY + ["dte", "var"])

    sel["w"] = sel["dK"] / sel["K"] ** 2 * sel["Q_o"]
    agg = (
        sel.groupby(_GROUP_KEY, as_index=False, sort=False)
        .agg(T=("T", "first"), F=("F", "first"), K0=("K0", "first"),
             dte=("dte", "first"), w_sum=("w", "sum"))
    )
    agg["var"] = (
        (2.0 / agg["T"]) * np.exp(RISK_FREE_RATE * agg["T"]) * agg["w_sum"]
        - (1.0 / agg["T"]) * (agg["F"] / agg["K0"] - 1.0) ** 2
    )
    return agg.loc[agg["var"] > 0, _GROUP_KEY + ["dte", "var"]] \
        .reset_index(drop=True)


def compute_options_vol_index(df: pd.DataFrame) -> pd.DataFrame:
    """Compute the daily per-underlying 30d model-free vol index.

    Args:
        df: DataFrame from fetch.fetch_vol_index_rows with columns:
            date, option_type, underlying_code, underlying_target_type,
            expiry_date, days_to_expiry, strike_price, underlying_close,
            settle.

    Returns:
        DataFrame with VOL_INDEX_RESULT_COLUMNS (dates datetime64;
        far_* NULL on the single-expiry fallback).
    """
    from analyze.options.config import VOL_INDEX_RESULT_COLUMNS as _cols

    if df.empty:
        return pd.DataFrame(columns=_cols)

    t = VOL_INDEX_TARGET_DAYS
    exp_var = _expiry_variances(_strike_level_frame(df))
    if exp_var.empty:
        return pd.DataFrame(columns=_cols)

    # Near = largest dte <= 30; far = smallest dte > 30 (per date+underlying).
    near_idx = exp_var.loc[exp_var["dte"] <= t] \
        .groupby(_PAIR_KEY, sort=False)["dte"].idxmax()
    far_idx = exp_var.loc[exp_var["dte"] > t] \
        .groupby(_PAIR_KEY, sort=False)["dte"].idxmin()
    near = exp_var.loc[near_idx].set_index(_PAIR_KEY)
    far = exp_var.loc[far_idx].set_index(_PAIR_KEY)

    # Pairs whose nearest listed expiry is still > 30 days (early
    # listings): that single expiry becomes the near side, far stays NULL.
    only_far = far.index.difference(near.index)
    if len(only_far) > 0:
        near = pd.concat([near, far.loc[only_far]])

    result = near.join(
        far.rename(columns={
            "expiry_date": "far_expiry_date", "dte": "dte_far",
            "var": "var_far",
        }),
        how="left",
    ).reset_index()

    # Fallback rows re-join their own expiry as the far side — a genuine
    # bracket never has near == far, so null the far columns there. (The
    # near side still carries its pre-rename name expiry_date here.)
    fb_mask = result["far_expiry_date"].notna() & (
        result["far_expiry_date"] == result["expiry_date"]
    )
    result.loc[fb_mask, "far_expiry_date"] = pd.NaT
    result.loc[fb_mask, "dte_far"] = np.nan
    result.loc[fb_mask, "var_far"] = np.nan

    # Single-expiry fallback (no bracket): the near expiry's variance IS
    # the index variance — deliberately NOT rescaled to exactly 30 days.
    result["variance_30d"] = result["var"]

    # Constant-maturity interpolation where both bracket sides exist
    # (near columns still carry their pre-rename names var / dte here):
    #   var30 = (var_near*dte_near*(dte_far-30)
    #            + var_far*dte_far*(30-dte_near))
    #           / ((dte_far - dte_near) * 30)
    span = result["dte_far"] - result["dte"]
    interp = span.notna() & (span > 0)
    vn = result.loc[interp, "var"].to_numpy()
    dn = result.loc[interp, "dte"].to_numpy()
    vf = result.loc[interp, "var_far"].to_numpy()
    dfr = result.loc[interp, "dte_far"].to_numpy()
    result.loc[interp, "variance_30d"] = (
        vn * dn * (dfr - t) + vf * dfr * (t - dn)
    ) / (dfr - dn) / t

    result = result.rename(columns={
        "expiry_date": "near_expiry_date", "dte": "dte_near",
        "var": "var_near",
    })
    result["vol_index_30d"] = 100.0 * np.sqrt(
        result["variance_30d"].clip(lower=0.0),
    )
    result = result.loc[
        result["vol_index_30d"].notna() & np.isfinite(result["vol_index_30d"])
        & (result["vol_index_30d"] > 0)
    ]
    result["dte_near"] = result["dte_near"].astype(int)
    result["dte_far"] = result["dte_far"].astype("Int64")

    result = result[_cols].sort_values(["date", "underlying_code"]) \
        .reset_index(drop=True)
    return result
