"""MovRsiEngine — the mov_rsi signal-family engine
(analyze.analysis_signals.engines.signal_families.mov_rsi._engine).

Emission slice: the top/bottom-{1,2,3}% RSI percentile buckets, BOTH
sides, every regime split. A bucket whose MIXED forecast_results row
passes the plain gate (sign-aligned dir_ave > 0.75%) at the
robustness floors AND the final claim-profile SignalQuality gate
becomes ONE strategy row over the bucket's forecast period
(start_date .. end_date = the snapshot key); the strategy's trigger
days INSIDE the snapshot's calendar year become its history rows. Row
construction sits in _strategies / _history.

The wide RSI value frame (one column per window) is melted to long
(code, date, window, value) by concat-of-window-slices (native ops
only; no melt, no string ops).

Frames stay NATIVE-dtype only (see engines._base): the per-snapshot
constants (sec_type / signal_type / period bounds / is_active) attach
to the records at the boundary, never through the frames.
"""
from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from analyze.analysis_signals.config import RSI_PCTS_EMIT, RSI_WINDOWS
from analyze.analysis_signals.config.mov_rsi import SLOW_CLAIM
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
    """mov_rsi: RSI top/bottom-{1,2,3}% percentile strategies + history."""

    signal_type = "mov_rsi"
    bucket = "mov_rsi"
    stage_key = "rsi"
    identity_name = "signals_mov_rsi"
    bucket_keys = ("code", "rsi_window", "pct", "side", "regime_state")
    slow_claim = SLOW_CLAIM
    identity_description = (
        "mov_rsi signal strategies over analysis_forecasts: the "
        "top/bottom-{1,2,3}% RSI percentile buckets whose MIXED "
        "forecast row passes the plain gate (sign-aligned blended mean "
        "reversal > 0.75%) at the robustness floors (chosen-rung "
        "sample floor 4 buys / 3 sells; ladder stability 2 buys / 1 sell "
        "of rungs 0-2 positive) AND "
        "the final claim-profile SignalQuality gate (breach "
        "coherence + 5d forward-mean alignment on every bucket, + 20d "
        "alignment on the slow claims W >= 20, + the 0.75 risk cap as "
        "ONE weight-blended verdict over 5d/20d — bar 0.50, the 5d "
        "bar carries the decision), "
        "one strategy per bucket over its forecast period + the "
        "strategy's trigger days inside its own snapshot year as "
        "history signals."
    )

    # ---- phases -------------------------------------------------------------

    # The derived-population cohorts (the base class's populations
    # contract): the parent + the two ohlcv children, each its own
    # independently-gated, separately-ranked strategy cohort.
    populations = (None, "ohlcv_boost", "ohlcv_selection")

    async def fetch_buckets(
        self, conn, sec_type: str, stat_date: date,
        population: str | None = None,
        codes: list[str] | None = None,
    ) -> pd.DataFrame:
        return await fetch_mov_rsi_buckets(
            conn, sec_type, stat_date, RSI_PCTS_EMIT, population, codes,
        )

    async def fetch_values(
        self, conn, sec_type: str, codes: list[str], dates: list[date],
    ) -> pd.DataFrame:
        return await fetch_mov_rsi_values(conn, sec_type, codes, dates)

    def build(
        self,
        sec_type: str,
        stat_date: date,
        passing: pd.DataFrame,
        snap_trig: pd.DataFrame,
        values: pd.DataFrame,
        population: str | None = None,
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
            strategy_rows(self, sec_type, stat_date, passing, long,
                          population),
            history_rows(self, sec_type, stat_date, snap_trig, long,
                         population),
        )
