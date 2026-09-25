"""The two pair-cross signal-family engines
(analyze.analysis_signals.engines.signal_families.mov_pairs._engine).

mov_pairs (fast legs ma5 + price on the ma5_vs_ma{W} /
price_vs_ma{W} spreads) and mov_pairs_ema (fast legs ema6 + price on
the ema6_vs_ema{W} / price_vs_ema{W} spreads) — the MA / EMA cross
families with their close-price legs. ONE shared base carries the
family lifecycle; each concrete engine names its identity (signal_type
/ stage key / per-leg sub_type prefixes) — the compute_pairs precedent
(the forecast side's own source-agnostic cross engine).

Emission slice: BOTH cross sides on 60d-or-longer slow legs, BOTH fast
legs, every regime split — the cross-DOWN (death cross, side bottom →
buy) and the cross-UP (golden cross, side top → sell). A bucket
whose MIXED forecast_results row passes the plain gate (sign-aligned
dir_ave > 0.75%) AND the final SignalQuality gate
becomes ONE strategy row over the bucket's forecast period (start_date
.. end_date = the snapshot key); the strategy's trigger days INSIDE
the snapshot's calendar year become its history rows. The bar is the
day's SLOW-LEG level (ma{W} / ema{W}) and the compared signal the
day's FAST leg (ma5 / ema6 / the close) — the live tier's "cross"
value space; the stored spread survives only as the strategy's params
context (the excess IS fast − slow).

The sub_type encodes fast leg + slow window (pair{W} = ma5 /
emapair{W} = ema6 / pxpair{W} = the close-price MA leg / pxemapair{W} =
the close-price EMA leg — the live tier's spread-key parsing reads the
leading letters + the window), so each (fast leg, window, side) is its
own strategy.

The wide spread-value frame (one ``{prefix}_{W}`` column per fast leg ×
window, plus the absolute leg levels ``close`` / ``ma5`` / ``ema6`` /
``ma{W}`` / ``ema{W}``) is melted to long (code, date, fast_leg,
window, value = spread, fast, slow) by concat-of-window-slices (native
ops only; no melt, no string ops).

Frames stay NATIVE-dtype only (see engines._base): the per-snapshot
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
    "(sign-aligned blended mean reversal > 0.75%) "
    "AND the final SignalQuality gate (breach coherence and sign-"
    "aligned per-period forward means on every quality period, plus "
    "the 0.75 risk cap as ONE weight-blended verdict over 5d/20d — "
    "bar 0.50, the 5d bar carries the decision), one strategy per "
    "bucket over its forecast period + the strategy's trigger days "
    "(the cross days) inside its own snapshot year as history "
    "signals. The bar is the day's slow leg (ma{{W}} / ema{{W}}); the "
    "compared signal is the day's fast leg. Emission slice: BOTH fast "
    "legs ({legs}), slow-leg windows >= 60d, BOTH cross sides (bottom "
    "cross-down → buy, top cross-up → sell — every sign flip of the "
    "daily spread flags exactly once), every regime split."
)


class _PairsEngineBase(SignalEngine):
    """The shared lifecycle of the two pair-cross families — the
    concrete classes name the identity (signal_type / stage key /
    per-leg sub_type prefixes / spread labels) and the slow-leg window
    grid."""

    bucket_keys = ("code", "fast_leg", "pair_window", "side",
                   "regime_state")

    # fast_leg → the sub_type's literal prefix ("pair" / "pxpair" ...)
    # and the spread-column stem for the reason string.
    sub_type_prefixes: dict[str, str]
    spread_labels: dict[str, str]
    legs: tuple[tuple[str, str], ...]  # (fast_leg, value-col prefix)
    windows: tuple[int, ...]           # the family's slow-leg grid
    slow_stem: str                     # the slow-leg column stem (ma / ema)

    # ---- phases -------------------------------------------------------------

    async def fetch_buckets(
        self, conn, sec_type: str, stat_date: date,
    ) -> pd.DataFrame:
        return await fetch_mov_pairs_buckets(
            conn, sec_type, stat_date, self.signal_type,
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
        stat_date: date,
        passing: pd.DataFrame,
        snap_trig: pd.DataFrame,
        values: pd.DataFrame,
    ) -> tuple[list[dict], list[dict]]:
        # No value points (no strategies passed the gates) → no merge
        # material at all (the empty values frame carries object-dtype
        # date columns — never merge against it).
        if values.empty:
            return [], []

        # The long value frame: one row per (code, date, fast_leg,
        # window) carrying the day's spread AND the day's absolute
        # legs — the fast-leg level ("price" reads the close column;
        # the indicator legs ride their own-name tech_stats columns)
        # and the window's slow-leg level.
        long = pd.concat(
            [
                values[["code", "date"]].assign(
                    fast_leg=leg, window=w, value=values[f"{prefix}_{w}"],
                    fast=values["close" if leg == "price" else leg],
                    slow=values[f"{self.slow_stem}{w}"],
                )
                for leg, prefix in self.legs
                for w in self.windows
            ],
            ignore_index=True,
        ).dropna(subset=["value", "fast", "slow"])

        return (
            strategy_rows(self, sec_type, stat_date, passing, long),
            history_rows(self, sec_type, stat_date, snap_trig, long),
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
    slow_stem = "ma"
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
    slow_stem = "ema"
    identity_description = _IDENTITY_DESCRIPTION.format(
        family="mov_pairs_ema",
        label="ema6_vs_ema{W} / price_vs_ema{W} sign flips",
        legs="ema6 + price",
    )
