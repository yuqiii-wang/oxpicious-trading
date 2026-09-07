"""Pure pandas computation logic for analyze.futures.

Computes per-(date, code) metrics comparing futures price against its
underlying (index close or treasury yield-derived bond price).

GPU note: The rolling operations (MA5, correlation) are straightforward
groupby-rolling-agg operations. The should_use_gpu check is included per
project convention, but cuDF.rolling().corr() may have limited support
for pairwise correlation — the CPU path is always used for the
correlation step.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import should_use_gpu  # noqa: F401 — per project convention
from analyze.futures.config import (
    AR1_WINDOW,
    CORR_WINDOW,
    MAX_GAP_WINDOWS,
    QUINTILE_HORIZONS,
    QUINTILE_N_BUCKETS,
)


def compute_futures_ext(
    df: pd.DataFrame,
) -> pd.DataFrame:
    """Compute all futures_ext metrics per (date, code).

    Steps:
      1. Compute underlying_price column (index_close for index futures,
         bond_theoretical_price for bond futures).
      2. Compute MA5 of futures_close per code (futures_ma5).
      3. Compute MA5 of underlying_price per code (underlying_ma5).
      4. Compute gap_price_vs_underlying.
      5. Compute gap_price_ma5_vs_underlying_ma5.
      6. Compute gap_changing_rate (1st-order derivative: day-over-day
         diff of the gaps — positive = basis widening (diverging),
         negative = basis narrowing (converging)).
      7. Compute rolling correlation (window=CORR_WINDOW).
      8. Compute rolling max of gap_price_vs_underlying over
         MAX_GAP_WINDOWS per code.
      9. Compute rolling AR(1) of gap_price_vs_underlying over
         AR1_WINDOW per code: slope b of gap_t ~ gap_{t-1}, and the
         implied mean-reversion half-life ln(0.5)/ln(b) (NaN unless
         0 < b < 1). b < 1 = basis mean-reverts toward the underlying
         (contrarian convergence).

    Args:
        df: DataFrame from fetch.fetch_futures_data with columns:
            date, code, product_code, contract_type, underlying_code,
            futures_close, index_close, bond_theoretical_price,
            is_index_future.

    Returns:
        DataFrame with columns: date, code, underlying_code,
        gap_price_vs_underlying,
        gap_price_ma5_vs_underlying_ma5,
        gap_changing_rate_price_vs_underlying,
        gap_changing_rate_price_ma5_vs_underlying_ma5,
        corr_price_vs_underlying,
        corr_price_ma5_vs_underlying_ma5,
        gap_max_price_vs_underlying_over_20days,
        gap_max_price_vs_underlying_over_60days,
        gap_ar1_slope_over_60days,
        gap_half_life_over_60days.
    """
    empty_cols = [
        "date", "code", "underlying_code",
        "gap_price_vs_underlying",
        "gap_price_ma5_vs_underlying_ma5",
        "gap_changing_rate_price_vs_underlying",
        "gap_changing_rate_price_ma5_vs_underlying_ma5",
        "corr_price_vs_underlying",
        "corr_price_ma5_vs_underlying_ma5",
        "gap_max_price_vs_underlying_over_20days",
        "gap_max_price_vs_underlying_over_60days",
        "gap_ar1_slope_over_60days",
        "gap_half_life_over_60days",
    ]
    if df.empty:
        return pd.DataFrame(columns=empty_cols)

    out = df.copy()
    out = out.sort_values(["code", "date"]).reset_index(drop=True)

    # ---- Step 1: underlying_price -----------------------------------------
    out["underlying_price"] = np.where(
        out["is_index_future"],
        out["index_close"],
        out["bond_theoretical_price"],
    )

    # Drop rows where underlying_price is still missing
    valid = out["underlying_price"].notna() & out["futures_close"].notna()
    out = out[valid].reset_index(drop=True)

    if out.empty:
        return pd.DataFrame(columns=empty_cols)

    # ---- Step 2: futures MA5 per code -------------------------------------
    out["futures_ma5"] = (
        out.groupby("code", sort=False)["futures_close"]
        .rolling(5, min_periods=5)
        .mean()
        .reset_index(level=0, drop=True)
    )

    # ---- Step 3: underlying MA5 per code ---------------------------------
    out["underlying_ma5"] = (
        out.groupby("code", sort=False)["underlying_price"]
        .rolling(5, min_periods=5)
        .mean()
        .reset_index(level=0, drop=True)
    )

    # ---- Step 4: gap_price_vs_underlying ---------------------------------
    out["gap_price_vs_underlying"] = (
        (out["futures_close"] - out["underlying_price"]) / out["underlying_price"]
    )
    # Guard: divide-by-zero
    out.loc[out["underlying_price"] == 0, "gap_price_vs_underlying"] = np.nan

    # ---- Step 5: gap_price_ma5_vs_underlying_ma5 -------------------------
    out["gap_price_ma5_vs_underlying_ma5"] = (
        (out["futures_ma5"] - out["underlying_ma5"]) / out["underlying_ma5"]
    )
    out.loc[out["underlying_ma5"] == 0, "gap_price_ma5_vs_underlying_ma5"] = np.nan

    # ---- Step 6: changing_rate (1st-order derivative of the gaps) --------
    # Day-over-day diff per code: positive = basis widening (diverging
    # from underlying), negative = basis narrowing (converging).
    out["gap_changing_rate_price_vs_underlying"] = (
        out.groupby("code", sort=False)["gap_price_vs_underlying"]
        .diff()
    )
    out["gap_changing_rate_price_ma5_vs_underlying_ma5"] = (
        out.groupby("code", sort=False)["gap_price_ma5_vs_underlying_ma5"]
        .diff()
    )

    # ---- Step 7: rolling correlation (CORR_WINDOW days) -------------------
    # corr_price_vs_underlying: rolling corr between futures_close and underlying_price
    out["corr_price_vs_underlying"] = _rolling_corr(
        out, "futures_close", "underlying_price", window=CORR_WINDOW
    )

    # corr_price_ma5_vs_underlying_ma5: rolling corr between futures_ma5 and underlying_ma5
    out["corr_price_ma5_vs_underlying_ma5"] = _rolling_corr(
        out, "futures_ma5", "underlying_ma5", window=CORR_WINDOW
    )

    # ---- Step 8: rolling max of gap_price_vs_underlying -------------------
    # Two windows: 20-day (monthly) and 60-day (quarterly) basis max.
    # Identifies historical basis extremes for each contract.
    for w in MAX_GAP_WINDOWS:
        col_name = f"gap_max_price_vs_underlying_over_{w}days"
        out[col_name] = (
            out.groupby("code", sort=False)["gap_price_vs_underlying"]
            .rolling(w, min_periods=w)
            .max()
            .reset_index(level=0, drop=True)
        )

    # ---- Step 9: rolling AR(1) of the basis -------------------------------
    # slope b of gap_t ~ gap_{t-1} over a trailing AR1_WINDOW: b < 1 means
    # the basis mean-reverts toward the underlying (contrarian
    # convergence); half-life = ln(0.5) / ln(b) in trading days.
    out["gap_ar1_slope_over_60days"] = _rolling_ar1_slope(
        out, "gap_price_vs_underlying", window=AR1_WINDOW
    )
    out["gap_half_life_over_60days"] = _ar1_half_life(
        out["gap_ar1_slope_over_60days"]
    )

    # ---- Final output ----------------------------------------------------
    result = out[[
        "date", "code", "underlying_code",
        "gap_price_vs_underlying",
        "gap_price_ma5_vs_underlying_ma5",
        "gap_changing_rate_price_vs_underlying",
        "gap_changing_rate_price_ma5_vs_underlying_ma5",
        "corr_price_vs_underlying",
        "corr_price_ma5_vs_underlying_ma5",
        "gap_max_price_vs_underlying_over_20days",
        "gap_max_price_vs_underlying_over_60days",
        "gap_ar1_slope_over_60days",
        "gap_half_life_over_60days",
    ]].copy()

    return result


def _rolling_corr(
    df: pd.DataFrame,
    col_x: str,
    col_y: str,
    window: int,
) -> pd.Series:
    """Compute rolling pairwise correlation between col_x and col_y
    over the given window, grouped by code.

    Uses pandas rolling().corr() which is CPU-only. cuDF does not
    fully support rolling correlation for two different columns.

    Args:
        df: DataFrame sorted by (code, date).
        col_x: first column name.
        col_y: second column name.
        window: rolling window size in trading days.

    Returns:
        Series with the same index as df, containing the rolling
        correlation values. NaN where not enough history or zero variance.
    """
    def _group_corr(g: pd.DataFrame) -> pd.Series:
        return g[col_x].rolling(window, min_periods=window).corr(g[col_y])

    result = (
        df.groupby("code", sort=False)
        .apply(_group_corr)
        .reset_index(level=0, drop=True)
    )
    return result


def _rolling_ar1_slope(
    df: pd.DataFrame,
    col: str,
    window: int,
) -> pd.Series:
    """Rolling AR(1) slope of ``col`` per code over ``window`` days.

    For each date, regresses col_t on col_{t-1} over the trailing
    ``window`` observations: b = Cov(col_t, col_{t-1}) / Var(col_{t-1}).
    The slope is stored as-is (interpretation: b < 1 mean-reversion,
    b ~ 1 random walk, b > 1 diverging); use _ar1_half_life for the
    mean-reversion reading.

    Args:
        df: DataFrame sorted by (code, date).
        col: column name (the level series).
        window: rolling window size in trading days.

    Returns:
        Series with the same index as df. NaN where not enough history.
    """
    def _group_ar1(g: pd.DataFrame) -> pd.Series:
        lag = g[col].shift(1)
        cov = g[col].rolling(window, min_periods=window).cov(lag)
        var = lag.rolling(window, min_periods=window).var()
        return cov / var

    return (
        df.groupby("code", sort=False)
        .apply(_group_ar1)
        .reset_index(level=0, drop=True)
    )


def _ar1_half_life(slope: pd.Series) -> pd.Series:
    """Implied mean-reversion half-life (trading days) from AR(1) slope b.

    half_life = ln(0.5) / ln(b). Defined only for 0 < b < 1 (geometric
    decay); b >= 1 (no mean reversion / explosive) yields NaN.
    """
    b = slope.astype(float)
    hl = np.log(0.5) / np.log(b)
    return hl.where((b > 0.0) & (b < 1.0))


def compute_gap_quintile_summary(
    result_df: pd.DataFrame,
    meta_df: pd.DataFrame,
) -> pd.DataFrame:
    """Build the basis-convergence quintile summary (per contract_type).

    Pools the gap distribution across all contracts of a contract_type,
    splits it into QUINTILE_N_BUCKETS quantile buckets, and records each
    bucket's mean gap (bps) and mean gap change over the next
    QUINTILE_HORIZONS trading days (bps). High-quintile (premium) gaps
    followed by NEGATIVE forward change — and low-quintile (discount)
    gaps by POSITIVE change — verify the contrarian convergence of the
    futures basis toward the underlying.

    Args:
        result_df: compute_futures_ext output (needs date, code,
            gap_price_vs_underlying).
        meta_df: fetch_futures_data output (needs date, code,
            contract_type).

    Returns:
        DataFrame with columns: asof_date, contract_type, horizon_days,
        quintile, n_obs, mean_gap_bps, mean_fwd_chg_bps. asof_date is
        the latest date in result_df (the run's snapshot stamp). Empty
        (correct columns) when there is no data.
    """
    cols = [
        "asof_date", "contract_type", "horizon_days", "quintile",
        "n_obs", "mean_gap_bps", "mean_fwd_chg_bps",
    ]
    if result_df.empty:
        return pd.DataFrame(columns=cols)

    d = result_df[["date", "code", "gap_price_vs_underlying"]].merge(
        meta_df[["date", "code", "contract_type"]],
        on=["date", "code"],
        how="inner",
    ).dropna(subset=["gap_price_vs_underlying", "contract_type"])
    if d.empty:
        return pd.DataFrame(columns=cols)

    asof = d["date"].max()
    rows = []
    for ct, g in d.groupby("contract_type", sort=True):
        g = g.sort_values(["code", "date"]).reset_index(drop=True)
        for h in QUINTILE_HORIZONS:
            gap = g["gap_price_vs_underlying"]
            fwd = (
                g.groupby("code", sort=False)["gap_price_vs_underlying"]
                .shift(-h)
            )
            sub = pd.DataFrame({"gap": gap, "gap_fwd_chg": fwd - gap}).dropna()
            if len(sub) < QUINTILE_N_BUCKETS:
                continue
            q = pd.qcut(
                sub["gap"],
                QUINTILE_N_BUCKETS,
                labels=False,
                duplicates="drop",
            ) + 1
            stats = sub.groupby(q, observed=True).agg(
                n_obs=("gap_fwd_chg", "size"),
                mean_gap=("gap", "mean"),
                mean_fwd_chg=("gap_fwd_chg", "mean"),
            )
            for quintile, r in stats.iterrows():
                rows.append({
                    "asof_date": asof,
                    "contract_type": ct,
                    "horizon_days": h,
                    "quintile": int(quintile),
                    "n_obs": int(r["n_obs"]),
                    "mean_gap_bps": float(r["mean_gap"]) * 1e4,
                    "mean_fwd_chg_bps": float(r["mean_fwd_chg"]) * 1e4,
                })

    return pd.DataFrame(rows, columns=cols)
