"""macd's no-action rule (engines/no_action/macd_rules.py).

`macd_chop` (NO BUY / NO SELL) — the textbook MACD whipsaw
criticism's REMAINDER: the K=5 absorb merges re-crosses within 5
traded days and the trend split separates the populations, but
DISTINCT cross episodes at wider spacing in rapid alternation
(cross-up, fade, cross-down, fade, cross-up inside a month) are a
range the absorb cannot see. The rule marks a fired cross when
>= NO_ACTION_MACD_CHOP_EPISODES distinct cross flips sit in the
trailing NO_ACTION_MACD_CHOP_WINDOW traded rows AND the trigger's
own |histogram| is below its trailing p25 — the rounding-scale
noise cross (the confidence depth already scales with |hist| in
trailing sigma; tiny-cross rows fire at small positive confidence
but still count as actions).

Both legs must hold (the AND is the conservatism): alternation
alone in a genuinely trending name with a deep histogram is a
strong pattern, and a tiny histogram alone may be an early turn.
"""
from __future__ import annotations

from typing import ClassVar

import numpy as np
import pandas as pd

from _common.df_utils import (
    grouped_rolling_agg,
    grouped_rolling_quantile,
    grouped_shift,
)

from analysis.analysis_signals import config as cfg
from analysis.analysis_signals.engines.no_action.base import NoActionRule

_HIST_SIGMA_WINDOW = 2520   # the family's own trailing depth window
_HIST_MIN_PERIODS = 252


class MacdChop(NoActionRule):
    """NO BUY / NO SELL at a noise-scale cross inside a chop."""

    REASON: ClassVar[str] = "macd_chop"
    FAMILY: ClassVar[str | None] = "macd"
    ACTIONS: ClassVar[tuple[str, ...]] = ("buy", "sell")
    SUB_TYPES: ClassVar[tuple[str, ...]] = ("macd_cross_up",
                                            "macd_cross_dn")
    CONTEXT_COLS: ClassVar[tuple[str, ...]] = ("_na_macd_chop",)

    def attach(self, frame: pd.DataFrame, ctx) -> None:
        sgn = np.sign(frame["macd_hist"].fillna(0.0))
        frame["_na_msgn"] = sgn
        grouped_shift(frame, ["code"], "_na_msgn",
                      out_names="_na_msgn_p", periods=1)
        flip = frame["_na_msgn"] != frame["_na_msgn_p"].fillna(0)
        frame["_na_mflipf"] = flip.astype("float64")
        cnt = grouped_rolling_agg(
            frame, ["code"], "_na_mflipf", cfg.NO_ACTION_MACD_CHOP_WINDOW,
            min_periods=1, agg="sum",
        )
        frame["_na_mabsh"] = np.abs(frame["macd_hist"])
        p25 = grouped_rolling_quantile(
            frame, ["code"], "_na_mabsh", _HIST_SIGMA_WINDOW,
            _HIST_MIN_PERIODS, cfg.NO_ACTION_MACD_HIST_PCTL,
        )
        frame["_na_mp25"] = p25
        grouped_shift(frame, ["code"], "_na_mp25", periods=1)
        frame["_na_macd_chop"] = (
            (cnt >= cfg.NO_ACTION_MACD_CHOP_EPISODES)
            & (frame["_na_mabsh"] < frame["_na_mp25"])
        )

    def condition_params(self) -> dict:
        return {
            "episodes": f">= {cfg.NO_ACTION_MACD_CHOP_EPISODES} cross "
                        f"flips in the trailing "
                        f"{cfg.NO_ACTION_MACD_CHOP_WINDOW} rows",
            "noise": f"|histogram| below its own trailing "
                     f"p{int(cfg.NO_ACTION_MACD_HIST_PCTL * 100)} "
                     f"(shifted)",
        }


__all__ = ["MacdChop"]
