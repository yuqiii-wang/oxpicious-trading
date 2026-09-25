"""mov_std strategy-row construction — ONE row per bucket that passed
the plain gate AND the final SignalQuality gate
(analyze.analysis_signals.engines.signal_families.mov_std._strategies).

The bar lives in PRICE space: the band level ma_W ± k·std_Wdays at
the window-end trigger, recovered as `price(d) − trigger_excess(d)`
(never re-derived from MA/σ). The σ multiple rides IN the sub_type
(std{W}_{k}std — the k fragment is the SQL float8::text rendering
("2", "2.5" — the SAME fragment the UI tick join builds) with the
literal "std" suffix), so each (window, k, side) is its own
strategy and the live rows name their band width.

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
    values: pd.DataFrame,
) -> list[dict]:
    """The COPY-boundary strategy records for the snapshot's
    quality-passing buckets (``values`` = the wide close-value frame)."""
    if passing.empty:
        return []

    # The close AT the window-end trigger defines the bar.
    bucket = passing.merge(
        values,
        left_on=["code", "trig_date"],
        right_on=["code", "date"],
        how="left",
    ).drop(columns=["date"])
    bucket = bucket[bucket["value"].notna()].reset_index(drop=True)
    if bucket.empty:
        return []

    # The bar in PRICE space: the band level at the window-end
    # trigger (close − its stored excess).
    threshold = (bucket["value"] - bucket["trig_excess"]).round(
        THRESHOLD_SCALE,
    )

    w_str = bucket["ma_window"].astype(str)
    sub_type = "std" + w_str + "_" + bucket["k_str"] + "std"
    cmp_op = pd.Series("<=", index=bucket.index)
    cmp_op = cmp_op.mask(bucket["side"].isin(("upper",)), ">=")
    reason = (
        "close=" + bucket["value"].round(2).astype(str)
        + " " + cmp_op + " " + bucket["side"] + " band bar "
        + threshold.round(2).astype(str)
        + " (ma" + w_str + " ± " + bucket["k_str"] + "σ) over the "
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
        "ma_window": bucket["ma_window"],
        "k": bucket["k"],
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
            # the band breach is a STATE — the live tier keeps flagging
            # on every qualifying observation while the breach persists
            is_triggered_once=False,
        )
        rec["params"] = json.dumps({
            "ma_window": rec.pop("ma_window"),
            "k": rec.pop("k"),
            "side": rec["side"],
            "regime_state": rec["regime_state"],
            "lookback_period": LOOKBACK_PERIOD,
            "conf_period": "mixed",
            # NOT popped — the delay is ALSO a stored column
            "signal_delay_days": rec["signal_delay_days"],
            "dir_ave": rec.pop("dir_ave"),
            "occurrence_count": rec.pop("occurrence_count"),
        })
    return records
