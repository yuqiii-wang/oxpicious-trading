"""mov_pairs strategy-row construction — ONE row per bucket that passed
the plain gate AND the final SignalQuality gate
(analyze.analysis_signals.engines.signal_families.mov_pairs._strategies).
Shared by both pair-cross families (mov_pairs / mov_pairs_ema — each
covering BOTH its fast legs).

The bar is the ZERO line — the crossed level of every golden / death
cross — recovered per bucket as `spread(d) − trigger_excess(d)` at the
bucket's WINDOW-END trigger (exactly 0 for the pairs families: the
trigger excess IS the day's spread, so the recovery stays uniform with
the mov_rsi / mov_std pattern instead of hard-coding 0). The fast leg
+ slow-leg window ride IN the sub_type (pair{W} / pxpair{W} /
emapair{W} / pxemapair{W} — the live tier's spread-key parsing), so
each (fast leg, window, side) is its own strategy.

Vectorized cudf.pandas throughout; frames stay NATIVE-dtype only.
"""
from __future__ import annotations

import json
from datetime import date

import pandas as pd

from analyze.analysis_signals.config import THRESHOLD_SCALE
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
    passing buckets (``long`` = the melted (code, date, fast_leg,
    window, value) spread frame)."""
    if passing.empty:
        return []

    # The spread AT the window-end trigger defines the bar (the zero
    # line: value − its stored excess).
    bucket = passing.merge(
        long,
        left_on=["code", "trig_date", "fast_leg", "pair_window"],
        right_on=["code", "date", "fast_leg", "window"],
        how="left",
    ).drop(columns=["date", "window"])
    bucket = bucket[bucket["value"].notna()].reset_index(drop=True)
    if bucket.empty:
        return []

    threshold = (bucket["value"] - bucket["trig_excess"]).round(
        THRESHOLD_SCALE,
    )

    sub_type = (
        bucket["fast_leg"].map(engine.sub_type_prefixes)
        + bucket["pair_window"].astype(str)
    )
    cmp_op = pd.Series("<=", index=bucket.index)
    cmp_op = cmp_op.mask(bucket["side"].isin(("top",)), ">=")
    spread_col = (
        bucket["fast_leg"].map(engine.spread_labels)
        + bucket["pair_window"].astype(str)
    )
    reason = (
        spread_col + " spread " + bucket["value"].round(4).astype(str)
        + " " + cmp_op + " the " + bucket["side"] + " cross bar "
        + threshold.round(4).astype(str)
        + " (" + sub_type + " sign flip) over the 5y window ending "
        + month.isoformat()
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
        "fast_leg": bucket["fast_leg"],
        "pair_window": bucket["pair_window"],
        "spread": bucket["value"],
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
            "fast_leg": rec.pop("fast_leg"),
            "pair_window": rec.pop("pair_window"),
            "side": rec["side"],
            "is_market_hyped": rec["is_market_hyped"],
            "lookback_period": LOOKBACK_PERIOD,
            "conf_period": "mixed",
            "spread": rec.pop("spread"),
            "dir_ave": rec.pop("dir_ave"),
            "reverse_prob": rec.pop("reverse_prob"),
            "occurrence_count": rec.pop("occurrence_count"),
        })
    return records
