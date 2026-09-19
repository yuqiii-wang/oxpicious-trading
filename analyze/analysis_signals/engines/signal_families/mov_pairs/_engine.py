"""The two pair-cross signal-family engines
(analyze.analysis_signals.engines.signal_families.mov_pairs._engine).

mov_pairs (fast legs ma5 + price on the ma5_vs_ma{W} /
price_vs_ma{W} spreads) and mov_pairs_ema (fast legs ema6 + price on
the ema6_vs_ema{W} / price_vs_ema{W} spreads) — the MA / EMA cross
families with their close-price legs. ONE shared base carries the
family lifecycle; each concrete engine names its identity (signal_type
/ stage key / per-leg sub_type prefixes) — the compute_pairs precedent
(the forecast side's own source-agnostic cross engine).

Emission slice: the CROSS-DOWN (death cross, side bottom → buy) buckets
on 120d-or-longer slow legs, BOTH fast legs, BOTH hype splits. A bucket
whose MIXED forecast_results row passes the plain gate (sign-aligned
dir_ave > 0.75% AND reverse_prob > 1%) AND the final SignalQuality gate
becomes ONE strategy row over the bucket's forecast period (start_date
.. end_date = the snapshot month); the strategy's trigger days INSIDE
the snapshot month become its history rows. The bar is the ZERO line
(the crossed level), recovered as spread(d) − trigger_excess(d) — the
mov_rsi / mov_std recovery pattern kept uniform.

The sub_type encodes fast leg + slow window (pair{W} = ma5 /
emapair{W} = ema6 / pxpair{W} = the close-price MA leg / pxemapair{W} =
the close-price EMA leg — the live tier's spread-key parsing reads the
leading letters + the window), so each (fast leg, window, side) is its
own strategy.

The wide spread-value frame (one ``{prefix}_{W}`` column per fast leg ×
window) is melted to long (code, date, fast_leg, window, value) by
concat-of-window-slices (native ops only; no melt, no string ops).

Frames stay NATIVE-dtype only (see engines._base): the per-month
constants (sec_type / signal_type / period bounds / is_active) attach
to the records at the boundary, never through the frames.
"""
from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from analyze.analysis_forecasts.config import (
    MOV_PAIRS_EMA_WINDOWS,
    MOV_PAIRS_WINDOWS,
)
from analyze.analysis_signals.config import (
    PAIRS_SIGNAL_SIDES,
    PAIRS_SIGNAL_WINDOW_MIN,
)
from analyze.analysis_signals.engines._base import SignalEngine
from analyze.analysis_signals.engines.signal_families.mov_pairs._history import (
    history_rows,
)
from analyze.analysis_signals.engines.signal_families.mov_pairs._strategies import (  # noqa: E501
    strategy_rows,
)
from analyze.analysis_signals.fetch import (
    fetch_mov_pairs_buckets,
    fetch_mov_pairs_values,
)

logger = logging.getLogger(__name__)

_IDENTITY_DESCRIPTION = (
    "{family} signal strategies over analysis_forecasts: the pair-cross "
    "buckets ({label}) whose MIXED forecast row passes the plain gate "
    "(sign-aligned blended mean reversal > 0.75% AND reverse_prob > 1%) "
    "AND the final SignalQuality gate (breach coherence and sign-"
    "aligned per-period forward means on every quality period, plus "
    "the 0.75 risk cap as ONE weight-blended verdict over 5d/20d/60d — "
    "bar 0.50, the 5d bar carries the decision), one strategy per "
    "bucket over its forecast period + the strategy's trigger days "
    "(the cross days) inside its own snapshot month as history "
    "signals. The bar is the ZERO line. Emission slice: BOTH fast legs "
    "({legs}), slow-leg windows >= 120d, cross-down (bottom) side, "
    "both hype splits."
)


class _PairsEngineBase(SignalEngine):
    """The shared lifecycle of the two pair-cross families — the
    concrete classes name the identity (signal_type / stage key /
    per-leg sub_type prefixes / spread labels) and the slow-leg window
    grid."""

    bucket_keys = ("code", "fast_leg", "pair_window", "side",
                   "is_market_hyped")

    # fast_leg → the sub_type's literal prefix ("pair" / "pxpair" ...)
    # and the spread-column stem for the reason string.
    sub_type_prefixes: dict[str, str]
    spread_labels: dict[str, str]
    legs: tuple[tuple[str, str], ...]  # (fast_leg, value-col prefix)
    windows: tuple[int, ...]           # the family's slow-leg grid

    # ---- phases -------------------------------------------------------------

    async def fetch_buckets(
        self, conn, sec_type: str, month: date,
    ) -> pd.DataFrame:
        return await fetch_mov_pairs_buckets(
            conn, sec_type, month, self.signal_type,
            PAIRS_SIGNAL_WINDOW_MIN, PAIRS_SIGNAL_SIDES,
        )

    async def fetch_values(
        self, conn, sec_type: str, codes: list[str], dates: list[date],
    ) -> pd.DataFrame:
        return await fetch_mov_pairs_values(
            conn, sec_type, codes, dates, self.signal_type,
        )

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

        # The long value frame: one row per (code, date, fast_leg,
        # window).
        long = pd.concat(
            [
                values[["code", "date"]].assign(
                    fast_leg=leg, window=w, value=values[f"{prefix}_{w}"],
                )
                for leg, prefix in self.legs
                for w in self.windows
            ],
            ignore_index=True,
        ).dropna(subset=["value"])

        return (
            strategy_rows(self, sec_type, month, passing, long),
            history_rows(self, sec_type, month, month_trig, long),
        )


class MovPairsEngine(_PairsEngineBase):
    """mov_pairs: MA cross (golden / death cross) strategies — fast
    legs ma5 (pair{W}) and the close price (pxpair{W})."""

    signal_type = "mov_pairs"
    bucket = "mov_pairs"
    stage_key = "pairs"
    identity_name = "signals_mov_pairs"
    sub_type_prefixes = {"ma5": "pair", "price": "pxpair"}
    spread_labels = {"ma5": "ma5_vs_ma", "price": "price_vs_ma"}
    legs = (("ma5", "pair"), ("price", "px_pair"))
    windows = MOV_PAIRS_WINDOWS
    identity_description = _IDENTITY_DESCRIPTION.format(
        family="mov_pairs",
        label="ma5_vs_ma{W} / price_vs_ma{W} sign flips",
        legs="ma5 + price",
    )


class MovPairsEmaEngine(_PairsEngineBase):
    """mov_pairs_ema: EMA cross strategies — fast legs ema6
    (emapair{W}) and the close price (pxemapair{W})."""

    signal_type = "mov_pairs_ema"
    bucket = "mov_pairs_ema"
    stage_key = "epairs"
    identity_name = "signals_mov_pairs_ema"
    sub_type_prefixes = {"ema6": "emapair", "price": "pxemapair"}
    spread_labels = {"ema6": "ema6_vs_ema", "price": "price_vs_ema"}
    legs = (("ema6", "ema_pair"), ("price", "px_ema_pair"))
    windows = MOV_PAIRS_EMA_WINDOWS
    identity_description = _IDENTITY_DESCRIPTION.format(
        family="mov_pairs_ema",
        label="ema6_vs_ema{W} / price_vs_ema{W} sign flips",
        legs="ema6 + price",
    )
