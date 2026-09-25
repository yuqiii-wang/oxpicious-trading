"""mov_rsi history-row construction — the strategies' snapshot-owned
trigger days (analyze.analysis_signals.engines.signal_families.
mov_rsi._history).

Each trigger day becomes ONE history_signals row: the day's RSI
value, the bar it crossed and the excess (a structural clone of the
live tier). The stored identity holds exactly: signal rounds BEFORE
the excess is computed from it (live tier convention).

Vectorized cudf.pandas throughout; frames stay NATIVE-dtype only.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from analyze.analysis_signals.config import (
    EXCESS_PCT_SCALE,
    EXCESS_SCALE,
    HISTORY_TIME,
    RSI_PCT,
    SIGNAL_SCALE,
    THRESHOLD_SCALE,
)
from analyze.analysis_signals.engines._base import SignalEngine
from analyze.analysis_signals.engines._primitives import SELL_SIDES


def history_rows(
    engine: SignalEngine,
    sec_type: str,
    stat_date: date,
    snap_trig: pd.DataFrame,
    long: pd.DataFrame,
) -> list[dict]:
    """The COPY-boundary history records for the strategies'
    snapshot-owned trigger days (``long`` = the melted (code, date, window,
    value) RSI frame; ``snap_trig`` is already semi-joined to the
    strategies + snapshot-owned by the ABC)."""
    # The RSI value AT each trigger day.
    rows = snap_trig.merge(
        long,
        left_on=["code", "trig_date", "rsi_window"],
        right_on=["code", "date", "window"],
        how="left",
    ).drop(columns=["date", "window"])
    rows = rows[rows["value"].notna()].reset_index(drop=True)
    if rows.empty:
        return []

    # The stored identity holds exactly: signal rounds BEFORE the
    # excess is computed from it (live tier convention).
    signal = rows["value"].round(SIGNAL_SCALE)
    threshold = (rows["value"] - rows["trig_excess"]).round(
        THRESHOLD_SCALE,
    )
    excess = (signal - threshold).round(EXCESS_SCALE)
    excess_pct = (
        (excess / threshold.abs() * 100)
        .round(EXCESS_PCT_SCALE)
        .where(threshold != 0)
    )

    # confidence = the strategy's chosen rung's sign-aligned dir_ave
    # scaled to integer basis points of expected move
    # (ROUND(10000 x dir_ave); the strategies row keeps the float).
    dir_sign = pd.Series(1.0, index=rows.index)
    dir_sign = dir_sign.mask(rows["side"].isin(SELL_SIDES), -1.0)
    _dir_ave_bp = (dir_sign * rows["ave_change"]).mul(10_000).round(
    ).astype(int)

    out = pd.DataFrame({
        "code": rows["code"],
        "signal_sub_type": (
            "rsi" + rows["rsi_window"].astype(str)
            + "_" + str(RSI_PCT) + "pct"
        ),
        "action": engine.action_of(rows["side"]),
        "signal_excess": excess,
        "signal_excess_pct": excess_pct,
        "signal": signal,
        "signal_threshold": threshold,
        "confidence": _dir_ave_bp,
        "date": rows["trig_date"],
        # the strategy's chosen delay rung (the day-d anchor this
        # event fired at — denormalized from the strategy row)
        "signal_delay_days": rows["delay"],
        # the trigger day's regime (FrameMachinery.regime_label —
        # matches the strategy's own regime split (joined 1:1 by the
        # against the current episode table)
        "regime_state": rows["regime"],
    })
    records = engine.frame_records(out, date_cols=("date",))
    for rec in records:
        rec.update(
            sec_type=sec_type,
            signal_type=engine.signal_type,
            time=HISTORY_TIME,
            is_day_close_trigger=True,
        )
    return records
