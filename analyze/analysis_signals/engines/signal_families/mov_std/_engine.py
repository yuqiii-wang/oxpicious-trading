"""MovStdEngine — the mov_std signal-family engine
(analyze.analysis_signals.engines.signal_families.mov_std._engine).

Emission slice: the Bollinger-breach buckets on 60d-or-longer MA/σ
windows at 2.0σ-or-tighter bands, BOTH sides (upper/lower), BOTH
hype splits. Same strategy/history split as mov_rsi (see
mov_rsi._engine): a gate-passing bucket becomes ONE strategy row over
its forecast period; the strategy's trigger days inside the snapshot
month become history rows. Row construction sits in _strategies /
_history; the bars/sub_types they build live in the indicator's own
space (price / std{W}_{k}).

Frames stay NATIVE-dtype only (see engines._base): the per-month
constants (sec_type / signal_type / period bounds / is_active) attach
to the records at the boundary, never through the frames.
"""
from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from analyze.analysis_signals.config import STD_K_MIN, STD_MA_WINDOW_MIN
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
    bucket_keys = ("code", "ma_window", "k", "side", "is_market_hyped")
    identity_description = (
        "mov_std signal strategies over analysis_forecasts: the "
        "Bollinger-breach buckets on 60d-or-longer MA/σ windows at "
        "2.0σ-or-tighter bands (both sides) whose MIXED forecast row "
        "passes the plain gate (sign-aligned blended mean reversal > "
        "0.75% AND reverse_prob > 1%) AND the final SignalQuality gate "
        "(breach coherence and sign-aligned per-period forward means "
        "on every quality period, plus the 0.75 risk cap as ONE "
        "weight-blended verdict over 5d/20d/60d — bar 0.50, the 5d "
        "bar carries the decision), "
        "one strategy per bucket over its forecast period + the "
        "strategy's trigger days inside its own snapshot month as "
        "history signals."
    )

    # ---- phases -------------------------------------------------------------

    async def fetch_buckets(
        self, conn, sec_type: str, month: date,
    ) -> pd.DataFrame:
        return await fetch_mov_std_buckets(
            conn, sec_type, month, STD_MA_WINDOW_MIN, STD_K_MIN,
        )

    async def fetch_values(
        self, conn, sec_type: str, codes: list[str], dates: list[date],
    ) -> pd.DataFrame:
        return await fetch_mov_std_values(conn, sec_type, codes, dates)

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

        return (
            strategy_rows(self, sec_type, month, passing, values),
            history_rows(self, sec_type, month, month_trig, values),
        )
