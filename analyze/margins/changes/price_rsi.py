"""Price RSI fetch + margin/price RSI ratio for margin_changes.

Fetches price RSI(14) from ``analysis.mov_ave_rsi`` (index sec_type only —
ETF / stock have no rows in mov_ave_rsi) and computes
``ratio_rsi_margin_vs_price = margin RSI / price RSI`` per trend episode.

A ratio > 1 means leverage is leading price; < 1 means lagging.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from analyze.margins.changes.constants import INSERT_COLUMNS


async def fetch_price_rsi(conn) -> pd.DataFrame:
    """Fetch price RSI (rsi_14days) from analysis.mov_ave_rsi.

    Only ``sec_type='index'`` rows exist in mov_ave_rsi (ETF / stock
    price RSI is not materialized). Returns a DataFrame with columns
    [sec_type, code, date, rsi_14days] for merging with trend episodes.
    """
    rows = await conn.fetch(
        """
        SELECT sec_type, code, date, rsi_14days
        FROM analysis.mov_ave_rsi
        WHERE sec_type = 'index'
          AND rsi_14days IS NOT NULL
        """
    )
    if not rows:
        return pd.DataFrame(columns=["sec_type", "code", "date", "rsi_14days"])
    return pd.DataFrame({
        "sec_type": [r["sec_type"] for r in rows],
        "code": [r["code"] for r in rows],
        "date": [r["date"] for r in rows],
        "rsi_14days": [float(r["rsi_14days"]) for r in rows],
    })


def compute_price_rsi_ratio(
    episodes: pd.DataFrame,
    price_rsi: pd.DataFrame,
) -> pd.DataFrame:
    """Compute ratio_rsi_margin_vs_price = margin RSI / price RSI.

    For each trend episode, computes the mean price RSI(14) over the
    [start_date, end_date] window from mov_ave_rsi, then divides the
    margin RSI (rsi_trend) by it.

    Only ``sec_type='index'`` episodes can have a non-NULL ratio (ETF /
    stock have no rows in mov_ave_rsi). The ratio is NULL when:
      - sec_type != 'index'
      - price RSI is unavailable for the window
      - mean price RSI is 0 (NULLIF guard)
    """
    if episodes.empty:
        return episodes

    if price_rsi.empty:
        # No price RSI data at all — ratio stays NULL for all episodes.
        episodes["ratio_rsi_margin_vs_price"] = np.nan
        return episodes

    # Only index episodes can have a ratio.
    idx_mask = episodes["sec_type"] == "index"
    if not idx_mask.any():
        episodes["ratio_rsi_margin_vs_price"] = np.nan
        return episodes

    idx_episodes = episodes[idx_mask].copy()
    other_episodes = episodes[~idx_mask].copy()
    other_episodes["ratio_rsi_margin_vs_price"] = np.nan

    # For each index episode, compute mean price RSI over the window.
    # Merge price_rsi with episode date ranges, then groupby episode.
    #
    # Approach: for each episode, filter price_rsi to the date range and
    # take the mean. This is O(n_episodes * n_price_rsi_rows) if done
    # naively; instead, use a merge + filter approach:
    #   1. Cross-join episodes with price_rsi on (sec_type, code).
    #   2. Filter to rows where price_rsi.date is within [start_date, end_date].
    #   3. Group by episode index, take mean of rsi_14days.
    #
    # For ~300K index price_rsi rows and ~5K index episodes, the merge
    # is manageable. Use the episode index as the group key.
    idx_episodes["_ep_idx"] = range(len(idx_episodes))

    merged = idx_episodes[["_ep_idx", "code", "start_date", "end_date"]].merge(
        price_rsi[["code", "date", "rsi_14days"]],
        on="code",
        how="inner",
    )
    # Filter to within the episode date range.
    in_range = (
        (merged["date"] >= merged["start_date"])
        & (merged["date"] <= merged["end_date"])
    )
    merged = merged[in_range]

    # Mean price RSI per episode.
    price_rsi_mean = merged.groupby("_ep_idx")["rsi_14days"].mean()

    # Map back to episodes.
    idx_episodes["__price_rsi_mean"] = idx_episodes["_ep_idx"].map(price_rsi_mean)

    # ratio = margin_rsi / price_rsi. NULL when price_rsi is NaN or 0.
    price_safe = idx_episodes["__price_rsi_mean"]
    idx_episodes["ratio_rsi_margin_vs_price"] = (
        idx_episodes["rsi_trend"] / price_safe
    ).where(price_safe > 0)

    # Drop helper columns.
    idx_episodes = idx_episodes.drop(columns=["_ep_idx", "__price_rsi_mean"])

    # Recombine.
    result = pd.concat([idx_episodes, other_episodes], ignore_index=True)
    # Restore original column order.
    result = result[INSERT_COLUMNS]
    return result
