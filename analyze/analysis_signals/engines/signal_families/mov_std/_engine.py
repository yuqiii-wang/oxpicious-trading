"""MovStdEngine — the mov_std signal-family engine
(analyze.analysis_signals.engines.signal_families.mov_std._engine).

Emission slice: the Bollinger-breach buckets on 60d-or-longer MA/σ
windows at 2.0σ-or-tighter bands, BOTH sides (upper/lower), BOTH
regime splits (the 20d window was removed 2026-09-30 — the OOS study
temp_scripts/study_std20_oos.py: its sell side sign-flipped out-of-
sample and its events vanish for whole years; the 20d buckets stay in
analysis_forecasts). Same strategy/history split as mov_rsi (see
mov_rsi._engine): a gate-passing bucket becomes ONE strategy row over
its forecast period; the strategy's trigger days inside the snapshot's
calendar year become history rows. Row construction sits in _strategies /
_history; the bars/sub_types they build live in the indicator's own
space (price / std{W}_{k}).

Frames stay NATIVE-dtype only (see engines._base): the per-snapshot
constants (sec_type / signal_type / period bounds / is_active) attach
to the records at the boundary, never through the frames.
"""
from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from analyze.analysis_signals.config import STD_K_MIN, STD_MA_WINDOW_MIN
from analyze.analysis_signals.config.mov_std import SLOW_CLAIM
from analyze.analysis_signals.engines._base import SignalEngine
from analyze.analysis_signals.engines.signal_families.mov_std._history import (
    history_rows,
)
from analyze.analysis_signals.engines.signal_families.mov_std._strategies import (  # noqa: E501
    strategy_rows,
)
from analyze.analysis_signals.fetch import (
    fetch_mov_std_buckets,
    fetch_mov_std_values,
)

logger = logging.getLogger(__name__)


class MovStdEngine(SignalEngine):
    """mov_std: Bollinger-breach strategies (60d+ windows, 2σ+ bands)
    + history."""

    signal_type = "mov_std"
    bucket = "mov_std"
    stage_key = "std"
    identity_name = "signals_mov_std"
    bucket_keys = ("code", "ma_window", "k", "side", "regime_state")
    slow_claim = SLOW_CLAIM
    identity_description = (
        "mov_std signal strategies over analysis_forecasts: the "
        "Bollinger-breach buckets on 60d-or-longer MA/σ windows at "
        "2.0σ-or-tighter bands (both sides) whose MIXED forecast row "
        "passes the plain gate (sign-aligned blended mean reversal > "
        "0.75%) at the per-side robustness floors (sample floor 4 buys / "
        "3 sells; stability 2 buys / 1 sell of rungs 0-2 positive); "
        "ladder stability 2 of rungs 0-2 positive) AND the final "
        "claim-profile SignalQuality gate (breach coherence + 5d and "
        "20d forward-mean alignment — every emitted window is a slow "
        "band-swing claim — plus the 0.75 risk cap as ONE "
        "weight-blended verdict over 5d/20d — bar 0.50, the 5d "
        "bar carries the decision), "
        "one strategy per bucket over its forecast period + the "
        "strategy's trigger days inside its own snapshot year as "
        "history signals."
    )

    # ---- phases -------------------------------------------------------------

    async def fetch_buckets(
        self, conn, sec_type: str, stat_date: date,
        population: str | None = None,
        codes: list[str] | None = None,
    ) -> pd.DataFrame:
        return await fetch_mov_std_buckets(
            conn, sec_type, stat_date, STD_MA_WINDOW_MIN, STD_K_MIN, codes,
        )

    async def fetch_values(
        self, conn, sec_type: str, codes: list[str], dates: list[date],
    ) -> pd.DataFrame:
        return await fetch_mov_std_values(conn, sec_type, codes, dates)

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

        return (
            strategy_rows(self, sec_type, stat_date, passing, values),
            history_rows(self, sec_type, stat_date, snap_trig, values),
        )
