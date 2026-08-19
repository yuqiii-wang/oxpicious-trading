"""Trend episode detection for margin_changes.

Segments the rz_balance curve into sustained UP / DOWN trends using
``margin_balance_slope_ma5`` sign as the direction signal, bridges short
opposite-direction gaps, and applies a zscore-magnitude significance
filter. Also computes per-episode Wilder RSI(14) and OHLC margin balance.

DIRECTION comes from slope_ma5 SIGN (the actual balance movement).
Zscore is NOT used for direction — it measures how anomalous the slope
is vs its 20d mean, which can be positive even when the balance is
declining (if today's decline is smaller than the recent average
decline). Using zscore for direction caused misclassification (e.g. a
sustained balance decline labeled as "UP" because daily declines were
smaller than the 20d average decline).

GAP BRIDGING: short opposite-direction runs of <= ``BRIDGE_GAP_DAYS``
(3) days that occur between two same-direction runs are ABSORBED
(flipped to match the surrounding direction). This prevents 1-3 day
counter-trend blips from fragmenting what should be a single longer
trend.

SIGNIFICANCE FILTER: after segmentation + bridging, a trend is KEPT only
if a MAJORITY (> ``ZSCORE_MAJORITY_THRESHOLD`` = 50%) of its days have
a STATISTICALLY SIGNIFICANT slope — i.e. |zscore_20d| > 0. The zscore
SIGN is NOT checked against direction — only its MAGNITUDE.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from analyze.margins.changes.constants import (
    BRIDGE_GAP_DAYS,
    INSERT_COLUMNS,
    MIN_TREND_DAYS,
    RSI_WINDOW,
    ZSCORE_MAJORITY_THRESHOLD,
)


def detect_trend_episodes(
    history: pd.DataFrame,
    tech_stats: pd.DataFrame,
    sec_type: str,
) -> pd.DataFrame:
    """Detect sustained UP/DOWN trend episodes on the rz_balance curve.

    Segmentation signal: ``margin_balance_slope_ma5`` sign
    (slope_ma5 > 0 = UP, slope_ma5 < 0 = DOWN, NaN/0 = break). Direction
    comes from the ACTUAL balance movement (5-day smoothed slope sign).

    GAP BRIDGING: short opposite-direction runs of <= BRIDGE_GAP_DAYS
    between two same-direction runs are flipped to match the surrounding
    direction, preventing noise from fragmenting meaningful trends.

    SIGNIFICANCE FILTER: after bridging, a trend is KEPT only if a
    MAJORITY (>ZSCORE_MAJORITY_THRESHOLD) of its days have a
    STATISTICALLY SIGNIFICANT slope (|zscore_20d| > 0). The zscore SIGN
    is NOT checked against direction — only its MAGNITUDE (significance).

    Args:
        history: DataFrame[code, date, rz_balance, rz_buy] — raw margin
            history for this sec_type.
        tech_stats: DataFrame with columns [code, date,
            margin_balance_slope_ma5, margin_balance_slope_zscore_20d].
        sec_type: 'etf' | 'stock' | 'index'.

    Returns:
        DataFrame with INSERT_COLUMNS. One row per trend episode.
        Empty if no trends.
    """
    if history.empty or tech_stats.empty:
        return pd.DataFrame(columns=INSERT_COLUMNS)

    # Merge history (rz_balance, rz_buy) with tech_stats (slope_ma5 +
    # zscore_20d) on (code, date). Inner join — only rows present in
    # both are usable.
    work = history[["code", "date", "rz_balance", "rz_buy"]].merge(
        tech_stats[["code", "date",
                     "margin_balance_slope_ma5",
                     "margin_balance_slope_zscore_20d"]],
        on=["code", "date"],
        how="inner",
    )
    if work.empty:
        return pd.DataFrame(columns=INSERT_COLUMNS)

    # Sort by (code, date) for correct temporal ordering within each code.
    work = work.sort_values(["code", "date"]).reset_index(drop=True)

    # ---- Clean rz_balance / rz_buy: 0 / NULL → NaN ------------------
    for col in ("rz_balance", "rz_buy"):
        cleaned = pd.to_numeric(work[col], errors="coerce")
        work[col] = cleaned.where(cleaned > 0)

    # ---- Compute Wilder RSI(14) on rz_balance per code ---------------
    work["__bal_ffill"] = work.groupby("code", sort=False)["rz_balance"].ffill()
    work["__delta"] = work.groupby("code", sort=False)["__bal_ffill"].diff()
    work["__gain"] = work["__delta"].where(work["__delta"] > 0, 0.0)
    work["__loss"] = (-work["__delta"]).where(work["__delta"] < 0, 0.0)
    alpha = 1.0 / RSI_WINDOW
    # Use direct groupby.ewm (no lambda) for GPU-compatible computation.
    # groupby.ewm() returns MultiIndex (code, orig_idx) — drop code level.
    grp = work.groupby("code", sort=False)
    work["__avg_gain"] = grp["__gain"].ewm(
        alpha=alpha, adjust=False, min_periods=RSI_WINDOW
    ).mean().droplevel(0)
    work["__avg_loss"] = grp["__loss"].ewm(
        alpha=alpha, adjust=False, min_periods=RSI_WINDOW
    ).mean().droplevel(0)
    work["__rsi"] = 100.0 - 100.0 / (1.0 + work["__avg_gain"] / work["__avg_loss"])

    # ---- Step 1: Assign raw direction from slope_ma5 sign ------------
    slope_ma5 = work["margin_balance_slope_ma5"]
    work["__dir_raw"] = np.where(slope_ma5 > 0, 1.0, np.where(slope_ma5 < 0, -1.0, np.nan))

    # ---- Step 2: Initial segmentation (raw) --------------------------
    work["__dir_key_raw"] = work["__dir_raw"].map(
        {1.0: "U", -1.0: "D"}.get
    ).fillna("X")
    work["__dir_changed_raw"] = work.groupby("code", sort=False)["__dir_key_raw"].transform(
        lambda s: s != s.shift(1)
    )
    work["__seg_id_raw"] = work.groupby("code", sort=False)["__dir_changed_raw"].cumsum()

    # ---- Step 3: Bridge short opposite-direction gaps ----------------
    _bridge_gaps(work)

    # ---- Step 4: Re-segment with bridged directions ------------------
    work["__dir_key"] = work["__dir_bridged"].map(
        {1.0: "U", -1.0: "D"}.get
    ).fillna("X")
    work["__dir_changed"] = work.groupby("code", sort=False)["__dir_key"].transform(
        lambda s: s != s.shift(1)
    )
    work["__seg_id"] = work.groupby("code", sort=False)["__dir_changed"].cumsum()

    # ---- Step 5: Aggregate per final segment + filter ----------------
    return _aggregate_and_filter(work, sec_type)


def _bridge_gaps(work: pd.DataFrame) -> None:
    """Flip short opposite-direction segments sandwiched between two
    same-direction segments (in-place on ``work``).

    Fully cudf-native: uses groupby.shift() for stencil computation,
    boolean masks for eligibility, and a single merge to apply
    bridged directions — no Python for-loops over codes.

    Break segments (NaN direction) are forward-filled within each
    code so they become transparent to the shift-based neighbor
    lookups, matching the original per-code bridging behavior.
    """
    work["__dir_bridged"] = work["__dir_raw"].copy()

    # Build per-code segment metadata (cudf-backed)
    seg_meta = work.groupby(["code", "__seg_id_raw"]).agg(
        seg_dir=("__dir_raw", "first"),
        seg_len=("date", "count"),
    ).reset_index()

    # --- Fully vectorized cudf-native bridging (all codes at once) ---
    # Forward-fill break (NaN) segments so they become transparent
    # to shift-based neighbor lookups. The original per-code loop
    # excluded breaks from real_segs, making them invisible neighbors.
    seg_meta["_dir_ffill"] = seg_meta.groupby("code", sort=False)["seg_dir"].ffill()

    # Identify real segments (direction is not NaN)
    real_mask = seg_meta["seg_dir"].notna()
    # Need at least 3 real segments per code for bridging to matter
    real_count = seg_meta.groupby("code", sort=False)["seg_dir"].transform("count")
    can_bridge = real_count >= 3

    def _compute_bridged(seg: pd.DataFrame) -> pd.Series:
        """Run iterative bridging on seg_meta (cudf).

        Uses _dir_ffill for shift-based neighbor lookup so that
        break segments are transparent. Only real segments are
        eligible for flipping; break segments keep NaN.
        """
        result = seg["_dir_ffill"].copy()

        # Only real segments within bridgeable codes are eligible
        eligible_init = real_mask & can_bridge

        for _ in range(5):
            # Groupby shift — GPU-accelerated, on forward-filled direction
            prev_dir = result.groupby(seg["code"], sort=False).shift(1)
            next_dir = result.groupby(seg["code"], sort=False).shift(-1)

            # Vectorized eligibility (cudf boolean operations)
            elig = (
                eligible_init
                & (seg["seg_len"] <= BRIDGE_GAP_DAYS)
                & (result != prev_dir)
                & (prev_dir == next_dir)
            )

            if not elig.any():
                break

            # Vectorized flip on GPU
            result.loc[elig] = prev_dir.loc[elig]

        # Restore NaN for break segments (they were ffilled for bridging)
        result = result.where(real_mask, np.nan)
        return result

    bridged = _compute_bridged(seg_meta)

    # --- Apply bridged directions back to work ---
    seg_meta["__dir_bridged"] = bridged
    merged = work[["code", "__seg_id_raw"]].merge(
        seg_meta[["code", "__seg_id_raw", "__dir_bridged"]],
        on=["code", "__seg_id_raw"], how="left",
    )
    work["__dir_bridged"] = merged["__dir_bridged"].fillna(work["__dir_raw"])


def _aggregate_and_filter(work: pd.DataFrame, sec_type: str) -> pd.DataFrame:
    """Aggregate per-segment metrics, apply filters, and build output."""
    # zscore_significant: |zscore_20d| > 0 (MAGNITUDE only, not sign).
    work["__zscore_sig"] = work["margin_balance_slope_zscore_20d"].abs() > 0

    segments = work.groupby(["code", "__seg_id"], sort=False).agg(
        start_date=("date", "first"),
        end_date=("date", "last"),
        days_of_trend=("date", "count"),
        direction=("__dir_bridged", "first"),
        start_balance=("rz_balance", "first"),
        end_balance=("rz_balance", "last"),
        high_balance=("rz_balance", "max"),
        low_balance=("rz_balance", "min"),
        total_buy=("rz_buy", "sum"),
        rsi_mean=("__rsi", "mean"),
        zscore_sig_count=("__zscore_sig", "sum"),
    ).reset_index()

    # Filter 1: drop breaks (direction NaN) + short trends.
    segments = segments[
        segments["direction"].notna()
        & (segments["days_of_trend"] >= MIN_TREND_DAYS)
    ].copy()

    if segments.empty:
        return pd.DataFrame(columns=INSERT_COLUMNS)

    # Filter 2: zscore magnitude significance (majority of days).
    sig_ratio = segments["zscore_sig_count"] / segments["days_of_trend"]
    segments = segments[sig_ratio > ZSCORE_MAJORITY_THRESHOLD].copy()

    if segments.empty:
        return pd.DataFrame(columns=INSERT_COLUMNS)

    # ---- Compute episode metrics -------------------------------------
    segments["netting_buy"] = (
        segments["end_balance"] - segments["start_balance"]
    ) - segments["total_buy"]
    segments["rsi_trend"] = segments["rsi_mean"]
    segments["is_trend_up_not_down"] = segments["direction"] > 0

    # OHLC margin balance: open = first day, close = last day,
    # high = max, low = min over the trend window.
    segments["open_margin_balance"] = segments["start_balance"]
    segments["close_margin_balance"] = segments["end_balance"]
    segments["high_margin_balance"] = segments["high_balance"]
    segments["low_margin_balance"] = segments["low_balance"]

    out = pd.DataFrame({
        "code": segments["code"],
        "sec_type": sec_type,
        "start_date": segments["start_date"],
        "end_date": segments["end_date"],
        "days_of_trend": segments["days_of_trend"].astype(int),
        "is_trend_up_not_down": segments["is_trend_up_not_down"],
        "netting_buy": segments["netting_buy"],
        "rsi_trend": segments["rsi_trend"],
        "ratio_rsi_margin_vs_price": np.nan,
        "open_margin_balance": segments["open_margin_balance"],
        "high_margin_balance": segments["high_margin_balance"],
        "low_margin_balance": segments["low_margin_balance"],
        "close_margin_balance": segments["close_margin_balance"],
        "ratio_open_margin_vs_price": np.nan,
        "ratio_high_margin_vs_price": np.nan,
        "ratio_low_margin_vs_price": np.nan,
        "ratio_close_margin_vs_price": np.nan,
    })

    return out
