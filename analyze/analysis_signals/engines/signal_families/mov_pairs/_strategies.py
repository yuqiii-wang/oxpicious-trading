"""mov_pairs strategy-row construction — ONE row per bucket that passed
the plain gate AND the final SignalQuality gate
(analyze.analysis_signals.engines.signal_families.mov_pairs._strategies).
Shared by both pair-cross families (mov_pairs / mov_pairs_ema — each
covering BOTH its fast legs).

The bar is the day's SLOW-LEG level (ma{W} / ema{W}) at the bucket's
WINDOW-END trigger, and the compared signal the day's FAST leg (ma5 /
ema6 / the close) — the live tier's "cross" value space (fast leg vs
the day's slow leg, never the zero line). The spread survives as the
strategy's params context; the trigger excess IS fast − slow. The fast
leg + slow-leg window ride IN the sub_type (pair{W} / pxpair{W} /
emapair{W} / pxemapair{W} — the live tier's spread-key parsing), so
each (fast leg, window, side) is its own strategy.

Vectorized cudf.pandas throughout; frames stay NATIVE-dtype only.
"""
from __future__ import annotations

import json
from datetime import date

import pandas as pd

from analyze.analysis_signals.config import THRESHOLD_SCALE
from analyze.analysis_signals.engines._primitives import SELL_SIDES
from analyze.analysis_forecasts.config import (
    LOOKBACK_PERIOD,
    window_lower,
)
from analyze.analysis_signals.engines._base import SignalEngine


def strategy_rows(
    engine: SignalEngine,
    sec_type: str,
    stat_date: date,
    passing: pd.DataFrame,
    long: pd.DataFrame,
) -> list[dict]:
    """The COPY-boundary strategy records for the snapshot's
    quality-passing buckets (``long`` = the melted (code, date,
    fast_leg, window, value, fast, slow) frame — the day's spread plus
    the day's absolute legs)."""
    if passing.empty:
        return []

    # The day's legs AT the window-end trigger define the bar (the
    # slow leg) and the strategy's fast-leg context value.
    bucket = passing.merge(
        long,
        left_on=["code", "trig_date", "fast_leg", "pair_window"],
        right_on=["code", "date", "fast_leg", "window"],
        how="left",
    ).drop(columns=["date", "window"])
    bucket = bucket[bucket["fast"].notna() & bucket["slow"].notna()]
    bucket = bucket.reset_index(drop=True)
    if bucket.empty:
        return []

    threshold = bucket["slow"].round(THRESHOLD_SCALE)

    sub_type = (
        bucket["fast_leg"].map(engine.sub_type_prefixes)
        + bucket["pair_window"].astype(str)
    )
    cmp_op = pd.Series("<=", index=bucket.index)
    cmp_op = cmp_op.mask(bucket["side"].isin(("top",)), ">=")
    slow_name = (
        pd.Series(engine.slow_stem, index=bucket.index)
        + bucket["pair_window"].astype(str)
    )
    reason = (
        bucket["fast_leg"] + " " + bucket["fast"].round(4).astype(str)
        + " " + cmp_op + " " + slow_name + " "
        + threshold.round(4).astype(str)
        + " (" + sub_type + " " + bucket["side"] + " cross) over the "
        "10y window ending " + stat_date.isoformat()
    )

    dir_sign = pd.Series(1.0, index=bucket.index)
    dir_sign = dir_sign.mask(bucket["side"].isin(SELL_SIDES), -1.0)
    _dir_ave = dir_sign * bucket["ave_change"]

    out = pd.DataFrame({
        "code": bucket["code"],
        "signal_sub_type": sub_type,
        "side": bucket["side"],
        "regime_state": bucket["regime_state"],
        "action": engine.action_of(bucket["side"]),
        "signal_threshold": threshold,
        "signal_delay_days": bucket["delay"],
        # confidence = the chosen rung's sign-aligned dir_ave (the
        # expected favorable blended move; history/live rows scale it
        # to ROUND(10000 x dir_ave) integer basis points)
        "confidence": _dir_ave.round(6),
        "reason": reason + "; entry delay " + bucket["delay"].astype(str)
                          + "d (ladder optimum)",
        # helpers for the params materialization
        "fast_leg": bucket["fast_leg"],
        "pair_window": bucket["pair_window"],
        "fast_value": bucket["fast"],
        "spread": bucket["value"],
        "dir_ave": _dir_ave,
        "occurrence_count": bucket["occurrence_count"],
    })
    records = engine.frame_records(out)
    for rec in records:
        rec.update(
            sec_type=sec_type,
            signal_type=engine.signal_type,
            start_date=window_lower(stat_date),
            end_date=stat_date,
            signal_order=None,
            is_active=False,
            # the cross is an EVENT, not a state — the live tier flags
            # the breach ONCE per episode (until the legs reset), never
            # on every bar the fast leg stays beyond the slow leg
            is_triggered_once=True,
        )
        rec["params"] = json.dumps({
            "fast_leg": rec.pop("fast_leg"),
            "pair_window": rec.pop("pair_window"),
            "side": rec["side"],
            "regime_state": rec["regime_state"],
            "lookback_period": LOOKBACK_PERIOD,
            "conf_period": "mixed",
            # NOT popped — the delay is ALSO a stored column
            "signal_delay_days": rec["signal_delay_days"],
            "fast_value": rec.pop("fast_value"),
            "spread": rec.pop("spread"),
            "dir_ave": rec.pop("dir_ave"),
            "occurrence_count": rec.pop("occurrence_count"),
        })
    return records
