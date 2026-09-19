"""mov_rsi strategy-row construction — ONE row per bucket that passed
the plain gate AND the final SignalQuality gate
(analyze.analysis_signals.engines.signal_families.mov_rsi._strategies).

The bar lives in the RSI's own space: the window's linear-interpolated
top/bottom-1% RSI quantile, recovered per bucket as
`rsi(d) − trigger_excess(d)` at the bucket's WINDOW-END trigger (the
bar is constant per bucket). The row carries the bucket's OWN side as
a STORED column and its params JSONB. The emission pct rides IN the
sub_type as the literal suffix _{pct}pct (rsi{W}_{pct}pct — "1pct"
at the current build's 1% slice), so each (window, pct, side) is its
own strategy and the live rows name their bar width.

Vectorized cudf.pandas throughout; frames stay NATIVE-dtype only.
"""
from __future__ import annotations

import json
from datetime import date

import pandas as pd

from analyze.analysis_signals.config import RSI_PCT, THRESHOLD_SCALE
from analyze.analysis_forecasts.config import LOOKBACK_PERIOD
from analyze.analysis_signals.engines._base import SignalEngine


def strategy_rows(
    engine: SignalEngine,
    sec_type: str,
    month: date,
    passing: pd.DataFrame,
    long: pd.DataFrame,
) -> list[dict]:
    """The COPY-boundary strategy records for the month's quality-
    passing buckets (``long`` = the melted (code, date, window, value)
    RSI frame)."""
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

    # The bar in the RSI's own space: value at the window-end
    # trigger minus its stored excess (the percentile bar is
    # constant per bucket).
    threshold = (bucket["value"] - bucket["trig_excess"]).round(
        THRESHOLD_SCALE,
    )

    sub_type = (
        "rsi" + bucket["rsi_window"].astype(str)
        + "_" + str(RSI_PCT) + "pct"
    )
    cmp_op = pd.Series("<=", index=bucket.index)
    cmp_op = cmp_op.mask(bucket["side"].isin(("top",)), ">=")
    reason = (
        sub_type + "=" + bucket["value"].round(1).astype(str)
        + " " + cmp_op + " " + bucket["side"] + "-1% bar "
        + threshold.round(1).astype(str)
        + " over the 5y window ending " + month.isoformat()
    )

    out = pd.DataFrame({
        "code": bucket["code"],
        "signal_sub_type": sub_type,
        "side": bucket["side"],
        "is_market_hyped": bucket["is_market_hyped"],
        "action": engine.action_of(bucket["side"]),
        "signal_threshold": threshold,
        "confidence": bucket["reverse_prob"].round(6),
        "reason": reason,
        # helpers for the params materialization
        "rsi_window": bucket["rsi_window"],
        "dir_ave": bucket["ave_change"],
        "reverse_prob": bucket["reverse_prob"],
        "occurrence_count": bucket["occurrence_count"],
    })
    records = engine.frame_records(out)
    for rec in records:
        rec.update(
            sec_type=sec_type,
            signal_type=engine.signal_type,
            start_date=engine.window_start(month),
            end_date=month,
            signal_order=None,
            is_active=False,
        )
        rec["params"] = json.dumps({
            "rsi_window": rec.pop("rsi_window"),
            "side": rec["side"],
            "is_market_hyped": rec["is_market_hyped"],
            "pct": RSI_PCT,
            "lookback_period": LOOKBACK_PERIOD,
            "conf_period": "mixed",
            "dir_ave": rec.pop("dir_ave"),
            "reverse_prob": rec.pop("reverse_prob"),
            "occurrence_count": rec.pop("occurrence_count"),
        })
    return records
