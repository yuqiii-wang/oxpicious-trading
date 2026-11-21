"""mov_rsi's no-action rule (engines/no_action/rsi_rules.py).

`rsi_bandwalk_sell` (NO SELL) — the BAND WALK: a fired sell-side
RSI trigger while the code sits in a live RISE trend with a
positively-agreeing Kaufman guard. Overbought-persistence IS the
measured warning — the sell side pooled NEGATIVE for every window
in the record ("tops continue"; George-Hwang near-high
underreaction), and the 2026-10-21 trend regressions measured the
rise(travel) continuation populations forward-coherent (+0.6% hit
52%). A top-tail breach mid-rise is the trend doing its job, not
exhaustion.

The guard is re-derived independently here (the trends component
DROPS its helper columns at attach_trend): the Kaufman net change
over TREND_GUARD_WINDOW rows — the same construction
analysis.trends.trend_state uses, from the shared constant. NO BUY
rule exists for this family, ever: plunging days are the measured
BUY edge everywhere it was tested (see the invalidation study's
Method section — the standing asymmetry).
"""
from __future__ import annotations

from typing import ClassVar

import pandas as pd

from _common.df_utils import grouped_rolling_agg

from analysis.analysis_signals import config as cfg
from analysis.analysis_signals.engines.no_action.base import NoActionRule
from analysis.trends.trend_state import TREND_GUARD_WINDOW

_TREND_RISE = 2


class RsiBandwalkSell(NoActionRule):
    """NO SELL at a top-tail trigger inside an agreeing rise."""

    REASON: ClassVar[str] = "rsi_bandwalk_sell"
    FAMILY: ClassVar[str | None] = "mov_rsi"
    ACTIONS: ClassVar[tuple[str, ...]] = ("sell",)
    SUB_TYPES: ClassVar[tuple[str, ...] | None] = None
    CONTEXT_COLS: ClassVar[tuple[str, ...]] = ("_na_rsi_bw",)

    def attach(self, frame: pd.DataFrame, ctx) -> None:
        # the Kaufman guard, re-derived (the trends component drops
        # its helper columns): grouped price diff -> rolling net sum
        frame["_na_guard_d"] = frame.groupby("code")["price"].diff()
        guard = grouped_rolling_agg(
            frame, ["code"], "_na_guard_d", TREND_GUARD_WINDOW,
            min_periods=TREND_GUARD_WINDOW, agg="sum",
        )
        frame["_na_rsi_guard"] = guard
        g = frame["_na_rsi_guard"].fillna(0.0)
        frame["_na_rsi_bw"] = (frame["trend"] == _TREND_RISE) & (g > 0)

    def condition_params(self) -> dict:
        return {
            "trend": "rise",
            "guard": f"kaufman net change > 0 over "
                     f"{TREND_GUARD_WINDOW} rows",
        }


__all__ = ["RsiBandwalkSell"]
