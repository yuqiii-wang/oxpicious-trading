"""mov_rsi threshold-row construction — ONE row per bucket that passed
the plain gate at the robustness floors
(analysis.analysis_signals.engines.signal_families.mov_rsi.threshold.
_strategies).

The bar lives in the RSI's own space, at the side's OWN tail quantile
(bottom = the window's q(p/100), top = q(1−p/100) — see
_buckets._tail_quantile_bars; the {pct}pct sub_type names the tail
mass the bar isolates), recovered per bucket as
`rsi(d*) − trigger_excess(d*)` at the bucket's WINDOW-END trigger (the
bar is constant per bucket). The row carries the bucket's OWN side as
a STORED column and the confidence-scale model (scale_method /
scale_base / scale_factor + confidence_base — the on-bar sign-aligned
dir_ave; breach records scale it over |excess|). The emission pct
rides IN the sub_type as the literal suffix _{pct}pct (rsi{W}_{pct}pct
— "1pct", "2pct", "3pct" at the current build's {1,2,3}% slice), so
each (window, pct, side) is its own strategy and the live rows name
their bar width.

(The derived-population cohort suffixes went with the forecast layer
2026-10-05 — the plain rsi-pct population is the only cohort. The
is_ohlcv_boosted marker rides the SAME row: the bucket's engines/
ohlcv_boost day-0 verdict, an analysis flag that gates nothing.)

Vectorized cudf.pandas throughout; frames stay NATIVE-dtype only.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from _common.labels import SEC_TYPE_CODE, SIDE_LABEL, SIGNAL_TYPE_CODE

from analysis.analysis_signals.config import (
    SCALE_BASE_DEFAULT,
    SCALE_FACTOR_DEFAULT,
    SCALE_METHOD_DEFAULT,
    THRESHOLD_SCALE,
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
    value) RSI frame)."""
    if passing.empty:
        return []

    # The RSI value AT the window-end trigger defines the bar.
    bucket = passing.merge(
        long,
        left_on=["code", "trig_date", "rsi_window"],
        right_on=["code", "date", "window"],
        how="left",
    ).drop(columns=["date", "window"])
    bucket = bucket[bucket["value"].notna()].reset_index(drop=True)
    if bucket.empty:
        return []

    # The bar in the RSI's own space: value at the window-end trigger
    # minus its stored excess (the percentile bar is constant per
    # bucket).
    threshold = (bucket["value"] - bucket["trig_excess"]).round(
        THRESHOLD_SCALE,
    )

    sub_type = (
        "rsi" + bucket["rsi_window"].astype(str)
        + "_" + bucket["pct"].astype(str) + "pct"
    )
    cmp_op = pd.Series("<=", index=bucket.index)
    cmp_op = cmp_op.mask(bucket["side"].isin(SELL_SIDE_CODES), ">=")
    reason = (
        sub_type + "=" + bucket["value"].round(1).astype(str)
        + " " + cmp_op + " " + bucket["side"].map(SIDE_LABEL) + "-"
        + bucket["pct"].astype(str) + "% bar "
        + threshold.round(1).astype(str)
        + " over the 10y window ending " + stat_date.isoformat()
    )

    dir_sign = pd.Series(1.0, index=bucket.index)
    dir_sign = dir_sign.mask(bucket["side"].isin(SELL_SIDE_CODES), -1.0)
    # confidence_base = the bucket's sign-aligned blended dir_ave (the
    # expected favorable blended move ON the bar; breach records scale
    # it over the breach depth: ROUND(10000 x base x scale(|excess|))
    # integer basis points).
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
        # the confidence-scale model stamped at the build defaults
        # (see config — the columns exist so a row can carry a tuned
        # model without a code change)
        "scale_method": SCALE_METHOD_DEFAULT,
        "scale_base": SCALE_BASE_DEFAULT,
        "scale_factor": SCALE_FACTOR_DEFAULT,
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
