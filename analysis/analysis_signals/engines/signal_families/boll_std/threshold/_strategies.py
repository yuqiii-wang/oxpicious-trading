"""boll_std threshold-row construction — ONE row per bucket that passed
the plain gate at the robustness floors
(analysis.analysis_signals.engines.signal_families.boll_std.threshold.
_strategies).

The bar lives in the σ-depth (z) space as the FIXED band multiplier the
sub_type names: +k for the top side (the upper band), −k for the bottom
side (the lower band) — recovered per bucket as
`z(d*) − trigger_excess(d*)` at the bucket's WINDOW-END trigger (the
bar is constant per bucket by construction: excess = z ∓ k). The row
carries the bucket's OWN side as a STORED column and the
confidence-scale model (scale_method / scale_base / scale_factor +
confidence_base — the on-bar sign-aligned dir_ave; breach records scale
it over the breach depth |z| − k in σ units, the family's steeper
per-σ slope BOLL_SCALE_FACTOR_DEFAULT). The σ multiplier rides IN the
sub_type as the literal suffix _{k}std (boll{W}_{k}std — "2std" /
"2p5std" / "3std"), so each (window, k, side) is its own strategy and
the live rows name their band width.

(The is_ohlcv_boosted marker rides the SAME row: the bucket's
engines/ohlcv_boost day-0 verdict — an analysis flag that gates
nothing; boll_std carries no measured boost config yet, so the verdict
stays FALSE until one is calibrated.)

Vectorized cudf.pandas throughout; frames stay NATIVE-dtype only.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from _common.labels import SEC_TYPE_CODE, SIDE_LABEL, SIGNAL_TYPE_CODE

from analysis.analysis_signals.config import (
    BOLL_SCALE_FACTOR_DEFAULT,
    BOLL_STDS_EMIT,
    SCALE_BASE_DEFAULT,
    SCALE_METHOD_DEFAULT,
    THRESHOLD_SCALE,
    format_std,
    window_lower,
)
from analysis.analysis_signals.engines._primitives import (
    SELL_SIDE_CODES,
)
from analysis.analysis_signals.engines._base import SignalEngine


def strategy_rows(
    engine: SignalEngine,
    sec_type: str,
    stat_date: date,
    passing: pd.DataFrame,
    long: pd.DataFrame,
) -> list[dict]:
    """The COPY-boundary threshold records for the snapshot's
    gate-passing buckets (``long`` = the melted (code, date, window,
    value) σ-depth frame)."""
    if passing.empty:
        return []

    # The σ-depth value AT the window-end trigger defines the bar.
    bucket = passing.merge(
        long,
        left_on=["code", "trig_date", "boll_window"],
        right_on=["code", "date", "window"],
        how="left",
    ).drop(columns=["date", "window"])
    bucket = bucket[bucket["value"].notna()].reset_index(drop=True)
    if bucket.empty:
        return []

    # The bar in the z space: the value at the window-end trigger minus
    # its stored excess (the FIXED ±k band constant per bucket).
    threshold = (bucket["value"] - bucket["trig_excess"]).round(
        THRESHOLD_SCALE,
    )

    std_lit = bucket["std"].map(
        {float(k): format_std(k) for k in BOLL_STDS_EMIT},
    )
    sub_type = "boll" + bucket["boll_window"].astype(str) \
        + "_" + std_lit + "std"
    cmp_op = pd.Series("<=", index=bucket.index)
    cmp_op = cmp_op.mask(bucket["side"].isin(SELL_SIDE_CODES), ">=")
    reason = (
        sub_type + "=" + bucket["value"].round(2).astype(str)
        + " " + cmp_op + " " + bucket["side"].map(SIDE_LABEL) + " "
        + std_lit + "σ band " + threshold.round(2).astype(str)
        + " over the 10y window ending " + stat_date.isoformat()
    )

    dir_sign = pd.Series(1.0, index=bucket.index)
    dir_sign = dir_sign.mask(bucket["side"].isin(SELL_SIDE_CODES), -1.0)
    # confidence_base = the bucket's sign-aligned blended dir_ave (the
    # expected favorable blended move ON the bar; breach records scale
    # it over the breach depth in σ units: ROUND(10000 x base x
    # scale(|z − ±k|)) integer basis points).
    _conf_base = (dir_sign * bucket["ave_change"]).round(6)

    out = pd.DataFrame({
        "code": bucket["code"],
        "signal_sub_type": sub_type,
        # the bucket's ohlcv-boost verdict (rides `passing` from the
        # engine's _apply_boost; bool at the record boundary)
        "is_ohlcv_boosted": bucket["is_ohlcv_boosted"].astype(bool),
        "side": bucket["side"],
        "regime_state": bucket["regime_state"],
        "action": engine.action_of(bucket["side"]),
        "signal_threshold": threshold,
        # the confidence-scale model stamped at the family defaults
        # (the per-σ slope is BOLL_SCALE_FACTOR_DEFAULT — the excess is
        # in σ units, not RSI points)
        "scale_method": SCALE_METHOD_DEFAULT,
        "scale_base": SCALE_BASE_DEFAULT,
        "scale_factor": BOLL_SCALE_FACTOR_DEFAULT,
        "confidence_base": _conf_base,
        "reason": reason,
    })
    records = engine.frame_records(out)
    for rec in records:
        rec.update(
            sec_type=SEC_TYPE_CODE[sec_type],
            signal_type=SIGNAL_TYPE_CODE[engine.signal_type],
            start_date=window_lower(stat_date),
            end_date=stat_date,
            is_active=False,
        )
    return records
