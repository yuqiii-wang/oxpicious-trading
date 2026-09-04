"""builds.bond.repo_lifecycle — Repo lifecycle tracking builder.

Builds stats.debt_repo (running cumulative repo balance) from OMO records:
  - repo_start_quantity: amount injected on repo start date
  - repo_end_quantity:   amount withdrawn on repo end date (negative)
  - repo_net_injection:  daily net money injection (start - end)
  - repo_cumulative:     cumulative outstanding repo balance
"""
from __future__ import annotations

import pandas as pd


def build_repo_lifecycle_df(omo_df):
    """Build repo lifecycle data from OMO records (fully vectorized).

    NOTE: repo_cumulative depends on the FULL chronological OMO history.
    Callers must pass the full omo_df (not a missing-dates-only subset) so
    the cumulative sum is correct. The INSERT step then filters to missing
    dates.
    """
    if omo_df is None or len(omo_df) == 0:
        return pd.DataFrame()

    legs_src = omo_df[["date", "omo_quantity", "omo_tenor_days"]].copy()
    legs_src["omo_quantity"] = pd.to_numeric(legs_src["omo_quantity"], errors="coerce")
    legs_src["omo_tenor_days"] = pd.to_numeric(legs_src["omo_tenor_days"], errors="coerce")
    legs_src = legs_src.dropna(subset=["omo_quantity", "omo_tenor_days"])
    if len(legs_src) == 0:
        return pd.DataFrame()

    qty = legs_src["omo_quantity"].astype(float)
    tenor_days = legs_src["omo_tenor_days"].astype(int)
    end_date = legs_src["date"] + pd.to_timedelta(tenor_days, unit="D")

    # Start leg (+qty at date) and end leg (-qty at date + tenor) — whole
    # column ops only. NEVER iterrows here: each row extraction from a
    # cudf-backed frame is a slow-path fallback (~12 per row).
    start_leg = legs_src[["date"]].copy()
    start_leg["repo_start_quantity"] = qty
    start_leg["repo_end_quantity"] = 0.0
    end_leg = legs_src[["date"]].copy()
    end_leg["date"] = end_date
    end_leg["repo_start_quantity"] = 0.0
    end_leg["repo_end_quantity"] = -qty
    legs_df = pd.concat([start_leg, end_leg], ignore_index=True)

    daily = legs_df.groupby("date", as_index=False).agg({
        "repo_start_quantity": "sum",
        "repo_end_quantity": "sum",
    })

    daily["repo_net_injection"] = daily["repo_start_quantity"] + daily["repo_end_quantity"]
    daily["repo_cumulative"] = daily["repo_net_injection"].cumsum()

    return daily
