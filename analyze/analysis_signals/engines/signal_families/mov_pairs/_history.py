"""mov_pairs history-row construction — the strategies' snapshot-owned
trigger days (analyze.analysis_signals.engines.signal_families.
mov_pairs._history). Shared by both pair-cross families (mov_pairs /
mov_pairs_ema — each covering BOTH its fast legs).

Each trigger day becomes ONE history_signals row: the day's FAST-LEG
level (ma5 / ema6 / the close) as the signal, the day's SLOW-LEG level
(ma{W} / ema{W}) as the bar and the excess — a structural clone of the
live tier's "cross" space (fast leg vs the day's slow leg, never the
zero line; the spread is fast − slow). The stored identity holds
exactly: signal rounds BEFORE the excess is computed from it (live
tier convention); signal_excess_pct is populated (the bar is a price
level, not 0).

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
from analyze.analysis_signals.engines._primitives import SELL_SIDES


def history_rows(
    engine: SignalEngine,
    sec_type: str,
    stat_date: date,
    snap_trig: pd.DataFrame,
    long: pd.DataFrame,
) -> list[dict]:
    """The COPY-boundary history records for the strategies'
    snapshot-owned trigger days (``long`` = the melted (code, date,
    fast_leg, window, value, fast, slow) frame — the day's spread plus
    the day's absolute legs; ``snap_trig`` is already semi-joined to
    the strategies + snapshot-owned by the ABC)."""
    # The day's legs AT each trigger day.
    rows = snap_trig.merge(
        long,
        left_on=["code", "trig_date", "fast_leg", "pair_window"],
        right_on=["code", "date", "fast_leg", "window"],
        how="left",
    ).drop(columns=["date", "window"])
    rows = rows[rows["fast"].notna() & rows["slow"].notna()]
    rows = rows.reset_index(drop=True)
    if rows.empty:
        return []

    # The stored identity holds exactly: signal rounds BEFORE the
    # excess is computed from it (live tier convention). The bar is
    # the day's slow-leg level; the signal the day's fast leg.
    signal = rows["fast"].round(SIGNAL_SCALE)
    threshold = rows["slow"].round(THRESHOLD_SCALE)
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
            rows["fast_leg"].map(engine.sub_type_prefixes)
            + rows["pair_window"].astype(str)
        ),
        "action": engine.action_of(rows["side"]),
        "signal_excess": excess,
        "signal_excess_pct": excess_pct,
        "signal": signal,
        "signal_threshold": threshold,
        "confidence": _dir_ave_bp,
        "date": rows["trig_date"],
        # the strategy's chosen delay rung (the day-d anchor this
        # event fired at — denormalized from the strategy row; the
        # pairs' one-day signals only ever occupy rung 0)
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
