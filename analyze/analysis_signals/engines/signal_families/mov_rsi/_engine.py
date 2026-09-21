"""MovRsiEngine — the mov_rsi signal-family engine
(analyze.analysis_signals.engines.signal_families.mov_rsi._engine).

Emission slice: the top/bottom-1% RSI percentile buckets, BOTH sides,
every regime split. A bucket whose MIXED forecast_results row
passes the plain gate (sign-aligned dir_ave > 0.75% AND reverse_prob
> 1%) AND the final SignalQuality gate becomes ONE strategy row over
the bucket's forecast period (start_date .. end_date = the snapshot
month); the strategy's trigger days INSIDE the snapshot month become
its history rows. Row construction sits in _strategies / _history.

The wide RSI value frame (one column per window) is melted to long
(code, date, window, value) by concat-of-window-slices (native ops
only; no melt, no string ops).

Frames stay NATIVE-dtype only (see engines._base): the per-month
constants (sec_type / signal_type / period bounds / is_active) attach
to the records at the boundary, never through the frames.
"""
from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from analyze.analysis_signals.config import RSI_PCT, RSI_WINDOWS
from analyze.analysis_signals.engines._base import SignalEngine
from analyze.analysis_signals.engines.signal_families.mov_rsi._history import (
    history_rows,
)
from analyze.analysis_signals.engines.signal_families.mov_rsi._strategies import (  # noqa: E501
    strategy_rows,
)
from analyze.analysis_signals.fetch import (
    fetch_mov_rsi_buckets,
    fetch_mov_rsi_values,
)

logger = logging.getLogger(__name__)

# The wide RSI value columns (one per window) → the melt's
# window → column mapping.
_VALUE_COLS = {w: f"rsi_{w}days" for w in RSI_WINDOWS}


class MovRsiEngine(SignalEngine):
    """mov_rsi: RSI top/bottom-1% percentile strategies + history."""

    signal_type = "mov_rsi"
    bucket = "mov_rsi"
    stage_key = "rsi"
    identity_name = "signals_mov_rsi"
    bucket_keys = ("code", "rsi_window", "side", "regime_state")
    identity_description = (
        "mov_rsi signal strategies over analysis_forecasts: the "
        "top/bottom-1% RSI percentile buckets whose MIXED forecast row "
        "passes the plain gate (sign-aligned blended mean reversal > "
        "0.75% AND reverse_prob > 1%) AND the final SignalQuality gate "
        "(breach coherence and sign-aligned per-period forward means "
        "on every quality period, plus the 0.75 risk cap as ONE "
        "weight-blended verdict over 5d/20d — bar 0.50, the 5d "
        "bar carries the decision), "
        "one strategy per bucket over its forecast period + the "
        "strategy's trigger days inside its own snapshot month as "
        "history signals."
    )

    # ---- phases -------------------------------------------------------------

    async def fetch_buckets(
        self, conn, sec_type: str, month: date,
    ) -> pd.DataFrame:
        return await fetch_mov_rsi_buckets(conn, sec_type, month, RSI_PCT)

    async def fetch_values(
        self, conn, sec_type: str, codes: list[str], dates: list[date],
    ) -> pd.DataFrame:
        return await fetch_mov_rsi_values(conn, sec_type, codes, dates)

    def build(
        self,
        sec_type: str,
        month: date,
        passing: pd.DataFrame,
        month_trig: pd.DataFrame,
        values: pd.DataFrame,
    ) -> tuple[list[dict], list[dict]]:
        # No value points (no strategies passed the gates) → no merge
        # material at all (the empty values frame carries object-dtype
        # date columns — never merge against it).
        if values.empty:
            return [], []

        # The long value frame: one row per (code, date, window).
        long = pd.concat(
            [
                values[["code", "date"]].assign(
                    window=w, value=values[col],
                )
                for w, col in _VALUE_COLS.items()
            ],
            ignore_index=True,
        ).dropna(subset=["value"])

        return (
            strategy_rows(self, sec_type, month, passing, long),
            history_rows(self, sec_type, month, month_trig, long),
        )
