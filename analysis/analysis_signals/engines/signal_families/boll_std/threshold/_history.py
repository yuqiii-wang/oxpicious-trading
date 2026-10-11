"""boll_std history-row construction — the strategies' snapshot-owned
trigger days (analysis.analysis_signals.engines.signal_families.
boll_std.threshold._history).

Each trigger day becomes its history_signals row (the plain record;
BOOST twins only where the (family, sec_type, SIDE) carries an
ohlcv-boost config — boll_std carries none yet, so the plain record is
the whole emission). The stored identity holds exactly: signal rounds
BEFORE the excess is computed from it (live tier convention). The
excess-sign convention falls out of the bar's sign: top breaches sit
above +k (excess >= 0, the sell rows), bottom breaches below −k
(excess <= 0, the buy rows) — the schema's check constraint.

Vectorized cudf.pandas throughout; frames stay NATIVE-dtype only.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from _common.labels import SEC_TYPE_CODE, SIGNAL_TYPE_CODE

from analysis.analysis_signals.config import (
    BOLL_SCALE_FACTOR_DEFAULT,
    BOLL_STDS_EMIT,
    EXCESS_PCT_SCALE,
    EXCESS_SCALE,
    HISTORY_TIME,
    SCALE_BASE_DEFAULT,
    SIGNAL_SCALE,
    THRESHOLD_SCALE,
    format_std,
)
from analysis.analysis_signals.engines._base import SignalEngine
from analysis.analysis_signals.engines._primitives import SELL_SIDE_CODES
from analysis.analysis_signals.engines.ohlcv_boost import side_configs


def history_rows(
    engine: SignalEngine,
    sec_type: str,
    stat_date: date,
    snap_trig: pd.DataFrame,
    long: pd.DataFrame,
) -> list[dict]:
    """The COPY-boundary history records for the strategies'
    snapshot-owned trigger days (``long`` = the melted (code, date,
    window, value) σ-depth frame; ``snap_trig`` is already semi-joined
    to the strategies + snapshot-owned by the ABC)."""
    # The σ-depth value AT each trigger day.
    rows = snap_trig.merge(
        long,
        left_on=["code", "trig_date", "boll_window"],
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

    dir_sign = pd.Series(1.0, index=rows.index)
    dir_sign = dir_sign.mask(rows["side"].isin(SELL_SIDE_CODES), -1.0)
    # confidence_base = the owning strategy's sign-aligned dir_ave; the
    # record carries the SCALED breach confidence — ROUND(10000 x base
    # x scale(|excess|)) integer basis points, the row's stamped linear
    # scale with the family's per-σ slope. Same formula as live's
    # evaluator.
    conf_base = dir_sign * rows["ave_change"]
    scale = (
        SCALE_BASE_DEFAULT + BOLL_SCALE_FACTOR_DEFAULT * excess.abs()
    ).clip(lower=0.0)
    _conf_bp = (conf_base * scale).mul(10_000).round().astype(int)

    std_lit = rows["std"].map(
        {float(k): format_std(k) for k in BOLL_STDS_EMIT},
    )
    sub_type = "boll" + rows["boll_window"].astype(str) \
        + "_" + std_lit + "std"

    def _out(boosted: bool, confidence: pd.Series) -> pd.DataFrame:
        return pd.DataFrame({
            "code": rows["code"],
            "signal_sub_type": sub_type,
            # the population VARIANT (PK member): FALSE = the plain
            # event, TRUE = the ohlcv-boost twin (same day, w-scaled
            # confidence)
            "is_ohlcv_boosted": pd.Series(
                bool(boosted), index=rows.index,
            ),
            "action": engine.action_of(rows["side"]),
            "signal_excess": excess,
            "signal_excess_pct": excess_pct,
            "signal": signal,
            "signal_threshold": threshold,
            "confidence": confidence,
            "date": rows["trig_date"],
            # delay 0 since the delay ladder's removal — the trigger IS
            # the qualifying episode's first day (the schema column
            # stays)
            "signal_delay_days": rows["delay"],
            # the trigger day's regime (rides the CELLS frame — matches
            # the strategy's own regime split)
            "regime_state": rows["regime_state"],
            # the trigger day's episode-PASS attribution (rides the
            # CELLS frame's pop_flag — the day's own window's
            # is_typical flag): TRUE = the typical pass, FALSE = the
            # re-entry pass (the UI history panel's pass toggle)
            "is_typical": rows["pop_flag"].eq(1.0),
        })

    parts = [_out(False, _conf_bp)]
    # the BOOST twins: only sides carrying an ohlcv-boost config (w is
    # the anchor's trust weight from the engine's _apply_boost)
    cfg_sides = list(side_configs(engine.signal_type, sec_type))
    if cfg_sides:
        boost_mask = rows["side"].isin(cfg_sides)
        conf_w = (rows["boost_weight"] * conf_base * scale) \
            .mul(10_000).round().astype(int)
        parts.append(_out(True, conf_w)[boost_mask])
    records = engine.frame_records(
        pd.concat(parts, ignore_index=True), date_cols=("date",),
    )
    for rec in records:
        rec.update(
            sec_type=SEC_TYPE_CODE[sec_type],
            signal_type=SIGNAL_TYPE_CODE[engine.signal_type],
            time=HISTORY_TIME,
            is_day_close_trigger=True,
        )
    return records
