"""mov_rsi strategy-row construction — ONE row per bucket×POPULATION
COHORT that passed the plain gate AND the final SignalQuality gate
(analyze.analysis_signals.engines.signal_families.mov_rsi._strategies).

The bar lives in the RSI's own space: the window's linear-interpolated
top/bottom-pct% RSI quantile, recovered per bucket as
`rsi(d) − trigger_excess(d)` at the bucket's WINDOW-END trigger (the
bar is constant per bucket). The row carries the bucket's OWN side as
a STORED column and its params JSONB. The emission pct rides IN the
sub_type as the literal suffix _{pct}pct (rsi{W}_{pct}pct — "1pct",
"2pct", "3pct" at the current build's {1,2,3}% slice), so each
(window, pct, side) is its own strategy and the live rows name their
bar width.

THE POPULATION COHORTS (the 2026-09-28 separate-cohort registration):
each forecast_identities.ohlcv_type verdict — the plain parent, the
@boost child (the same trigger days, score-weighted) and the
@selection child (the favorable-anatomy subset, bottom side) — is its
OWN independently-gated strategy cohort. Children suffix their
sub_type (@boost / @selection — PK-distinct; the parent's bare
sub_type stays what the forecast ✓ tick join and the live tier parse),
carry the ohlcv_type verdict as a STORED column, and rank in their OWN
signal_order pool (03_signal_order_rank partitions by ohlcv_type — the
populations NEVER blend into one confidence:
temp_scripts/study_ohlcv_signal_confidence.py measured every blend
degrading the OOS rank). Children never enter the live tier
(02_is_active keeps them inactive).

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

SUB_TYPE_SUFFIX = {
    "ohlcv_boost": "@boost",
    "ohlcv_selection": "@selection",
}


def strategy_rows(
    engine: SignalEngine,
    sec_type: str,
    stat_date: date,
    passing: pd.DataFrame,
    long: pd.DataFrame,
    population: str | None = None,
) -> list[dict]:
    """The COPY-boundary strategy records for the snapshot's
    quality-passing buckets of ONE population cohort (``long`` = the
    melted (code, date, window, value) RSI frame). The cohort's
    confidence is its OWN chosen-rung sign-aligned mean — the frame's
    ``ave_change`` IS the cohort's (the fetch read the cohort's own
    ladder)."""
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
        + "_" + bucket["pct"].astype(str) + "pct"
        + SUB_TYPE_SUFFIX.get(population, "")
    )
    cmp_op = pd.Series("<=", index=bucket.index)
    cmp_op = cmp_op.mask(bucket["side"].isin(("top",)), ">=")
    reason = (
        sub_type + "=" + bucket["value"].round(1).astype(str)
        + " " + cmp_op + " " + bucket["side"] + "-"
        + bucket["pct"].astype(str) + "% bar "
        + threshold.round(1).astype(str)
        + " over the 10y window ending " + stat_date.isoformat()
    )

    dir_sign = pd.Series(1.0, index=bucket.index)
    dir_sign = dir_sign.mask(bucket["side"].isin(SELL_SIDES), -1.0)
    _dir_ave = dir_sign * bucket["ave_change"]

    out = pd.DataFrame({
        "code": bucket["code"],
        "signal_sub_type": sub_type,
        # the cohort verdict — STORED (03_signal_order_rank partitions
        # the signal_order pools by it; 02_is_active keeps the
        # children out of the live tier's threshold set)
        "ohlcv_type": population,
        "side": bucket["side"],
        "regime_state": bucket["regime_state"],
        "action": engine.action_of(bucket["side"]),
        "signal_threshold": threshold,
        "signal_delay_days": bucket["delay"],
        # confidence = the cohort's OWN chosen-rung sign-aligned
        # dir_ave (the expected favorable blended move; history/live
        # rows scale it to ROUND(10000 x dir_ave) integer basis
        # points) — never blended across cohorts (the study citation
        # above)
        "confidence": _dir_ave.round(6),
        "reason": reason + "; entry delay " + bucket["delay"].astype(str)
                          + "d (ladder optimum)",
        # helpers for the params materialization
        "rsi_window": bucket["rsi_window"],
        "pct": bucket["pct"],
        "dir_ave": _dir_ave,
        "occurrence_count": bucket["occurrence_count"],
    })
    records = engine.frame_records(out)
    for rec in records:
        window = rec.pop("rsi_window")
        rec.update(
            sec_type=sec_type,
            signal_type=engine.signal_type,
            start_date=window_lower(stat_date),
            end_date=stat_date,
            signal_order=None,
            is_active=False,
            # the RSI extreme is a STATE — the live tier keeps flagging
            # on every qualifying observation while the breach persists
            is_triggered_once=False,
        )
        rec["params"] = json.dumps({
            "rsi_window": window,
            "side": rec["side"],
            "regime_state": rec["regime_state"],
            "pct": rec.pop("pct"),
            "lookback_period": LOOKBACK_PERIOD,
            "conf_period": "mixed",
            # the claim profile the quality gate held the bucket to
            "required_periods": (
                ["5d", "20d"] if window >= 20 else ["5d"]
            ),
            # NOT popped — the delay is ALSO a stored column
            "signal_delay_days": rec["signal_delay_days"],
            "dir_ave": rec.pop("dir_ave"),
            "occurrence_count": rec.pop("occurrence_count"),
            # the cohort provenance (mirrors the stored ohlcv_type;
            # 'parent' the plain population)
            "confidence_source": population or "parent",
        })
    return records
