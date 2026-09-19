"""mov_std history-row construction — the strategies' month-owned
trigger days (analyze.analysis_signals.engines.signal_families.
mov_std._history).

Each trigger day becomes ONE history_signals row: the day's close,
the band it crossed and the excess (a structural clone of the live
tier). The stored identity holds exactly: signal rounds BEFORE the
excess is computed from it (live tier convention).

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
    values: pd.DataFrame,
) -> list[dict]:
    """The COPY-boundary history records for the strategies' month-
    owned trigger days (``values`` = the wide close-value frame;
    ``month_trig`` is already semi-joined to the strategies +
    month-owned by the ABC)."""
    # The close AT each trigger day.
    rows = month_trig.merge(
        values,
        left_on=["code", "trig_date"],
        right_on=["code", "date"],
        how="left",
    ).drop(columns=["date"])
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

    out = pd.DataFrame({
        "code": rows["code"],
        "signal_sub_type": (
            "std" + rows["ma_window"].astype(str)
            + "_" + rows["k_str"] + "std"
        ),
        "action": engine.action_of(rows["side"]),
        "signal_excess": excess,
        "signal_excess_pct": excess_pct,
        "signal": signal,
        "signal_threshold": threshold,
        "confidence": (rows["reverse_prob"] * 100).round().astype(int),
        "date": rows["trig_date"],
        # the trigger day's hype verdict (FrameMachinery.hype_flag —
        # structurally FALSE for non-hyped-bucket strategies, kept true
        # against the current episode table)
        "is_market_hyped": rows["is_hyped"],
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
