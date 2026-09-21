"""mov_pairs history-row construction — the strategies' month-owned
trigger days (analyze.analysis_signals.engines.signal_families.
mov_pairs._history). Shared by both pair-cross families (mov_pairs /
mov_pairs_ema — each covering BOTH its fast legs).

Each trigger day becomes ONE history_signals row: the day's stored
relative spread, the zero line it crossed and the excess (a structural
clone of the live tier). The stored identity holds exactly: signal
rounds BEFORE the excess is computed from it (live tier convention);
the bar is 0, so signal_excess reads as the spread's signed distance
from the zero line on the cross day and signal_excess_pct is NULL
(0-denominator).

Vectorized cudf.pandas throughout; frames stay NATIVE-dtype only.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from analyze.analysis_signals.config import (
    EXCESS_PCT_SCALE,
    EXCESS_SCALE,
    HISTORY_TIME,
    SIGNAL_SCALE,
    THRESHOLD_SCALE,
)
from analyze.analysis_signals.engines._base import SignalEngine


def history_rows(
    engine: SignalEngine,
    sec_type: str,
    month: date,
    month_trig: pd.DataFrame,
    long: pd.DataFrame,
) -> list[dict]:
    """The COPY-boundary history records for the strategies' month-
    owned trigger days (``long`` = the melted (code, date, pair_window,
    value) spread frame; ``month_trig`` is already semi-joined to the
    strategies + month-owned by the ABC)."""
    # The spread AT each trigger day.
    rows = month_trig.merge(
        long,
        left_on=["code", "trig_date", "fast_leg", "pair_window"],
        right_on=["code", "date", "fast_leg", "window"],
        how="left",
    ).drop(columns=["date", "window"])
    rows = rows[rows["value"].notna()].reset_index(drop=True)
    if rows.empty:
        return []

    # The stored identity holds exactly: signal rounds BEFORE the
    # excess is computed from it (live tier convention). The bar is
    # the zero line (value − its stored excess).
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

    out = pd.DataFrame({
        "code": rows["code"],
        "signal_sub_type": (
            rows["fast_leg"].map(engine.sub_type_prefixes)
            + rows["pair_window"].astype(str)
        ),
        "action": engine.action_of(rows["side"]),
        "signal_excess": excess,
        "signal_excess_pct": excess_pct,
        "signal": signal,
        "signal_threshold": threshold,
        "confidence": (rows["reverse_prob"] * 100).round().astype(int),
        "date": rows["trig_date"],
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
