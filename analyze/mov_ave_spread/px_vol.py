"""px_vol feature layer for the price_vs_amt registry
(analyze.mov_ave_spread.px_vol).

``add_px_vol_features`` derives the per-day RAW state inputs (no
look-ahead) that ``classify_price_vs_amt`` bins into the 15
price-speed × amount-state categories; ``fetch_price_vs_amt_source``
fetches the registry build's source frame (price + trading_amount per
(code, date) with the shared per-sec_type conventions — ETF price =
COALESCE(adj_close, close); estimated closes excluded for etf/index;
trading_amount from the sec_type's own source) so the registry
classifies on exactly the analysis_forecasts input price series.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.build_commons import rec_cols
from _common.df_utils import epoch_col_to_dt64

from analyze.mov_ave_spread.config import (
    PX_VOL_LB_WINDOW,
    PX_VOL_SIGMA_FLOOR,
    PX_VOL_SIGMA_MIN_DAYS,
    PX_VOL_SIGMA_WINDOW,
)

from analyze.analysis_forecasts.fetch._sources import (
    AMT_SOURCE,
    PRICE_SOURCE,
)


async def fetch_price_vs_amt_source(
    conn,
    sec_type: str,
    codes: list[str],
) -> pd.DataFrame:
    """Fetch the REGISTRY build's source frame — price + trading_amount
    per (code, date) with the shared per-sec_type price conventions
    (PRICE_SOURCE / AMT_SOURCE — ETF = COALESCE(adj_close, close);
    estimated closes excluded for etf/index).

    Returns an unbounded FULL-history frame (the trailing σ/z windows
    need the code's whole past) with columns [sec_type, code, date,
    price, trading_amount], sorted by (code, date).
    """
    base, price_expr = PRICE_SOURCE[sec_type]
    amt_join, amt_expr, _, est_filter = AMT_SOURCE[sec_type]
    sql = f"""
        SELECT b.code,
               extract(epoch from b.date)::float8 AS date,
               {price_expr}::float8 AS price,
               {amt_expr}::float8 AS trading_amount
        FROM {base}
        {amt_join}
        WHERE b.code = ANY($1::text[])
          AND b.close IS NOT NULL
          {est_filter}
        ORDER BY b.code, b.date ASC
    """
    rows = await conn.fetch(sql, sorted(codes))
    columns = ["sec_type", "code", "date", "price", "trading_amount"]
    if not rows:
        return pd.DataFrame(columns=columns)
    df = pd.DataFrame(rec_cols(rows), columns=columns[1:])
    df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
    df.insert(0, "sec_type", sec_type)
    return df


def add_px_vol_features(df) -> object:
    """Add the px_vol family's per-day RAW state inputs (no look-ahead).

    Derived on the long cudf.pandas frame with grouped ops (grouped_shift
    / grouped_rolling_agg). Serves the registry's own build path
    (analyze.mov_ave_spread.price_vs_amt).

    Columns added:
        ret_1d    — 1-row fractional price change per code.
        px_sigma  — σ_ret: rolling PX_VOL_SIGMA_WINDOW-row (min
                    PX_VOL_SIGMA_MIN_DAYS) sample std of ret_1d,
                    SHIFTED 1 row (yesterday's σ is today's bar).
        px_t      — t = ret_1d / px_sigma, NULL where px_sigma is
                    NaN/<=0 or below PX_VOL_SIGMA_FLOOR (bond-like
                    codes never join a state).
        px_z      — z-scored log trading-amount LEVEL vs the code's OWN
                    trailing PX_VOL_SIGMA_WINDOW-row moments (min
                    PX_VOL_SIGMA_MIN_DAYS) SHIFTED 1 row.
        amt_ratio — the classic 量比 (trading_amount / mean(trading_
                    amount, t-PX_VOL_LB_WINDOW..t-1)), EVIDENCE only —
                    the vol state does NOT consume it (the 5-day-baseline
                    ratio z fired "heavy" on drought bounces: in a
                    declining-volume regime the base collapses, so a
                    below-level amount day scored ratio ≈ 1.7 → z > 2).

    Conditions are kept PURE booleans (NULLs filled BEFORE each compare —
    nullable <NA> masks would poison the cudf frame into object dtype,
    the margin.add_margin_ratio_features note).
    """
    from _common.df_utils import grouped_rolling_agg, grouped_shift

    # --- price speed -----------------------------------------------------
    grouped_shift(df, ["code"], "price", out_names="_prev_price",
                  periods=1, sort=False)
    prev = df["_prev_price"]
    ret = df["price"] / prev - 1.0
    df["ret_1d"] = ret.where(prev.notna() & (prev.fillna(0.0).abs() > 1e-12)
                             & np.isfinite(ret))
    df = df.drop(columns=["_prev_price"])

    sigma = grouped_rolling_agg(
        df, "code", "ret_1d", PX_VOL_SIGMA_WINDOW,
        min_periods=PX_VOL_SIGMA_MIN_DAYS, agg="std", ddof=1, sort=False,
    )
    df["_sigma"] = sigma
    grouped_shift(df, ["code"], "_sigma", out_names="px_sigma",
                  periods=1, sort=False)
    df = df.drop(columns=["_sigma"])

    sig = df["px_sigma"].fillna(0.0)
    ok_sigma = (sig > 0) & (sig >= PX_VOL_SIGMA_FLOOR)
    df["px_t"] = (df["ret_1d"] / df["px_sigma"]).where(ok_sigma)

    # --- 量能水平 z (trading-amount LEVEL z-score) -------------------------
    df["_log_ta"] = np.log(df["trading_amount"]).where(
        df["trading_amount"] > 0)
    mu = grouped_rolling_agg(
        df, "code", "_log_ta", PX_VOL_SIGMA_WINDOW,
        min_periods=PX_VOL_SIGMA_MIN_DAYS, agg="mean", sort=False,
    )
    sig = grouped_rolling_agg(
        df, "code", "_log_ta", PX_VOL_SIGMA_WINDOW,
        min_periods=PX_VOL_SIGMA_MIN_DAYS, agg="std", ddof=1, sort=False,
    )
    df["_lvl_mu"] = mu
    df["_lvl_sig"] = sig
    grouped_shift(df, ["code"], ["_lvl_mu", "_lvl_sig"],
                  out_names=["_lvl_mu_lag", "_lvl_sig_lag"],
                  periods=1, sort=False)
    lvl = df["_log_ta"]
    mu_l = df["_lvl_mu_lag"]
    sig_l = df["_lvl_sig_lag"].fillna(0.0)
    df["px_z"] = ((lvl - mu_l) / sig_l).where(
        lvl.notna() & mu_l.notna() & (sig_l > 1e-12))

    # --- classic 量比 (evidence only — see the docstring) ------------------
    grouped_shift(df, ["code"], "trading_amount", out_names="_ta_prev",
                  periods=1, sort=False)
    df["_lb_base"] = grouped_rolling_agg(
        df, "code", "_ta_prev", PX_VOL_LB_WINDOW,
        min_periods=PX_VOL_LB_WINDOW, agg="mean", sort=False,
    )
    ta = df["trading_amount"]
    base = df["_lb_base"].fillna(0.0)
    df["amt_ratio"] = (ta / base).where(
        ta.notna() & (base > 1e-12))

    return df.drop(columns=[
        "_log_ta", "_lvl_mu", "_lvl_sig", "_lvl_mu_lag", "_lvl_sig_lag",
        "_ta_prev", "_lb_base",
    ])
