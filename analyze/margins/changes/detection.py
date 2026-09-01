"""Trend episode detection for margin_changes.

Segments the rz_balance curve into sustained UP / DOWN trends using
``margin_balance_slope_ma5`` sign as the direction signal, bridges short
opposite-direction gaps, and applies a zscore-magnitude significance
filter. Emits per-episode ``new_buy`` (rz_buy on the end_date) plus the
internal ``sum_rz_buy`` helper consumed by trading_amt.py for the
rz_buy_vs_trading_amt_ratio.

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
    ZSCORE_MAJORITY_THRESHOLD,
)


def detect_trend_episodes(
    history: pd.DataFrame,
    tech_stats: pd.DataFrame,
    sec_type: str,
    trading_amt: pd.DataFrame | None = None,
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

    TRADING AMT: when ``trading_amt`` is given (daily rows for THIS
    sec_type: [code, date, trading_amount]), it is equality-joined onto
    the daily rows on (code, date) and summed per segment — Σ over the
    episode's [start_date, end_date] (episodes are contiguous daily-row
    segments, so no asof join is needed; cudf has no merge_asof).

    Args:
        history: DataFrame[code, date, rz_balance, rz_buy] — raw margin
            history for this sec_type.
        tech_stats: DataFrame with columns [code, date,
            margin_balance_slope_ma5, margin_balance_slope_zscore_20d].
        sec_type: 'etf' | 'stock' | 'index'.
        trading_amt: optional daily trading_amount rows for this
            sec_type ([code, date, trading_amount]).

    Returns:
        DataFrame with INSERT_COLUMNS plus the internal ``sum_rz_buy``
        helper (Σ rz_buy over the window; dropped at DB write time).
        One row per trend episode. Empty if no trends.
    """
    if history.empty or tech_stats.empty:
        return pd.DataFrame(columns=INSERT_COLUMNS + ["sum_rz_buy"])

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
        return pd.DataFrame(columns=INSERT_COLUMNS + ["sum_rz_buy"])

    # ---- Daily trading_amount (equality join — cudf-native) ----------
    if trading_amt is not None and not trading_amt.empty:
        work = work.merge(
            trading_amt[["code", "date", "trading_amount"]],
            on=["code", "date"],
            how="left",
        )
    else:
        work["trading_amount"] = np.nan

    # Sort by (code, date) for correct temporal ordering within each code.
    work = work.sort_values(["code", "date"]).reset_index(drop=True)

    # ---- Clean rz_balance / rz_buy: 0 / NULL → NaN ------------------
    for col in ("rz_balance", "rz_buy"):
        cleaned = pd.to_numeric(work[col], errors="coerce")
        work[col] = cleaned.where(cleaned > 0)

    # ---- Step 1: Assign raw direction from slope_ma5 sign ------------
    slope_ma5 = work["margin_balance_slope_ma5"]
    work["__dir_raw"] = np.where(slope_ma5 > 0, 1.0, np.where(slope_ma5 < 0, -1.0, np.nan))

    # ---- Step 2: Initial segmentation (raw) --------------------------
    # Numeric dir key: 1.0 (U), -1.0 (D), 0.0 (X/break — NaN filled).
    # Keeping the key NUMERIC (not "U"/"D"/"X" strings) makes the whole
    # segmentation stencil GPU-native: no Series.map(dict.get) CPU
    # fallback, no null-propagation traps in string comparisons
    # (cudf's Kleene logic yields null for value != null).
    work["__dir_key_raw"] = work["__dir_raw"].fillna(0.0)
    prev_key = work.groupby("code", sort=False)["__dir_key_raw"].shift(1).fillna(999.0)
    work["__dir_changed_raw"] = work["__dir_key_raw"] != prev_key
    work["__seg_id_raw"] = work.groupby("code", sort=False)["__dir_changed_raw"].cumsum()

    # ---- Step 3: Bridge short opposite-direction gaps ----------------
    _bridge_gaps(work)

    # ---- Step 4: Re-segment with bridged directions ------------------
    work["__dir_key"] = work["__dir_bridged"].fillna(0.0)
    prev_key_b = work.groupby("code", sort=False)["__dir_key"].shift(1).fillna(999.0)
    work["__dir_changed"] = work["__dir_key"] != prev_key_b
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
    # fillna(0) before the comparison keeps the boolean result null-free
    # under cudf (null > 0 would propagate null instead of False).
    work["__zscore_sig"] = (
        work["margin_balance_slope_zscore_20d"].abs().fillna(0.0) > 0
    )

    segments = work.groupby(["code", "__seg_id"], sort=False).agg(
        start_date=("date", "first"),
        end_date=("date", "last"),
        days_of_trend=("date", "count"),
        direction=("__dir_bridged", "first"),
        sum_rz_buy=("rz_buy", "sum"),
        sum_trading_amt=("trading_amount", "sum"),
        zscore_sig_count=("__zscore_sig", "sum"),
    ).reset_index()

    # cudf groupby.sum over an ALL-NULL column yields null (pandas yields
    # 0.0) — rz_buy is cleaned 0/NULL -> NaN, so an episode with no
    # positive rz_buy days must sum to 0.0 to keep ratio parity.
    segments["sum_rz_buy"] = segments["sum_rz_buy"].fillna(0.0)

    # Filter 1: drop breaks (direction NaN) + short trends.
    segments = segments[
        segments["direction"].notna()
        & (segments["days_of_trend"] >= MIN_TREND_DAYS)
    ].copy()

    if segments.empty:
        return pd.DataFrame(columns=INSERT_COLUMNS + ["sum_rz_buy"])

    # Filter 2: zscore magnitude significance (majority of days).
    sig_ratio = segments["zscore_sig_count"] / segments["days_of_trend"]
    segments = segments[sig_ratio > ZSCORE_MAJORITY_THRESHOLD].copy()

    if segments.empty:
        return pd.DataFrame(columns=INSERT_COLUMNS + ["sum_rz_buy"])

    # ---- new_buy: rz_buy on the episode end_date --------------------
    # groupby "last" would SKIP trailing NaN (a 0/NULL end-date rz_buy
    # would leak the previous day's value), so mark each segment's final
    # row explicitly via a shift(-1) key-change stencil. The shifted
    # keys are fillna'd to sentinels so the != comparisons stay
    # null-free under cudf (Kleene logic: value != null -> null).
    next_code = work["code"].shift(-1).fillna("")
    next_seg = work["__seg_id"].shift(-1).fillna(-1.0)
    is_last_row = (work["code"] != next_code) | (work["__seg_id"] != next_seg)
    end_rows = work[is_last_row][["code", "__seg_id", "rz_buy"]].rename(
        columns={"rz_buy": "new_buy"}
    )
    segments = segments.merge(
        end_rows, on=["code", "__seg_id"], how="left"
    ).drop(columns=["__seg_id"])

    out = pd.DataFrame({
        "code": segments["code"],
        "sec_type": sec_type,
        "start_date": segments["start_date"],
        "end_date": segments["end_date"],
        "days_of_trend": segments["days_of_trend"].astype(int),
        "is_trend_up_not_down": segments["direction"] > 0,
        "new_buy": segments["new_buy"],
        # NULLIF guard: Σ trading_amount must be > 0 (all-NaN segments
        # sum to 0.0 under skipna — same NULL as the former asof-join's
        # unmatched-episode NaN).
        "rz_buy_vs_trading_amt_ratio": (
            segments["sum_rz_buy"] / segments["sum_trading_amt"]
        ).where(segments["sum_trading_amt"] > 0),
        "sum_rz_buy": segments["sum_rz_buy"],
    })

    return out
