"""ma_cross's no-action rule (engines/no_action/ma_rules.py).

`ma_whipsaw` (NO BUY) — the classic MA criticism, the reason the
`is_typical` uninterrupted-run split exists: a reclaim whose recent
history is a rapid alternation of below<->above flips is a RANGE,
not a turn. The trigger-time split covers the CURRENT run's
character; this rule covers the RECENT HISTORY — a fresh "first
close above" after >= NO_ACTION_MA_WHIPSAW_FLIPS flips in the
trailing NO_ACTION_MA_WHIPSAW_WINDOW traded rows is the same chop
wearing a new run.

Per fired sub type: ma20_cross counts gap20 flips, ma60_cross
counts gap60 flips (the frame's price-vs-MA GAP columns — the
family's own state axis, read independently here).
"""
from __future__ import annotations

from typing import ClassVar

import pandas as pd

from _common.df_utils import grouped_rolling_agg, grouped_shift
from _common.labels import SIGNAL_SUB_TYPE_CODE

from analysis.analysis_signals import config as cfg
from analysis.analysis_signals.engines.no_action.base import NoActionRule

#: fired sub type -> the gap column whose flips count
_SUB_WINDOW = {"ma20_cross": "gap20", "ma60_cross": "gap60"}


class MaWhipsaw(NoActionRule):
    """NO BUY at a reclaim inside a recent below/above flip chop."""

    REASON: ClassVar[str] = "ma_whipsaw"
    FAMILY: ClassVar[str | None] = "ma_cross"
    ACTIONS: ClassVar[tuple[str, ...]] = ("buy",)
    SUB_TYPES: ClassVar[tuple[str, ...]] = ("ma20_cross", "ma60_cross")
    CONTEXT_COLS: ClassVar[tuple[str, ...]] = ("_na_ma_flips20",
                                               "_na_ma_flips60")

    def attach(self, frame: pd.DataFrame, ctx) -> None:
        for sub, gap_col in _SUB_WINDOW.items():
            w = int(gap_col[3:])
            above = frame[gap_col].fillna(0.0) >= 0
            frame["_na_ma_above"] = above
            grouped_shift(frame, ["code"], "_na_ma_above",
                          out_names="_na_ma_above_p", periods=1)
            flip = above != frame["_na_ma_above_p"].fillna(False)
            frame["_na_ma_flipf"] = flip.astype("float64")
            cnt = grouped_rolling_agg(
                frame, ["code"], "_na_ma_flipf",
                cfg.NO_ACTION_MA_WHIPSAW_WINDOW, min_periods=1,
                agg="sum",
            )
            frame[f"_na_ma_flips{w}"] = cnt

    def condition(self, row: dict, ctxv: dict) -> bool:
        sub = {v: k for k, v in SIGNAL_SUB_TYPE_CODE.items()}.get(
            row["signal_sub_type"])
        gap_col = _SUB_WINDOW.get(sub or "")
        if gap_col is None:
            return False
        cnt = ctxv.get(f"_na_ma_flips{gap_col[3:]}")
        return cnt is not None and cnt >= cfg.NO_ACTION_MA_WHIPSAW_FLIPS

    def condition_params(self) -> dict:
        return {
            "flips": f">= {cfg.NO_ACTION_MA_WHIPSAW_FLIPS} "
                     f"below->above flips in the trailing "
                     f"{cfg.NO_ACTION_MA_WHIPSAW_WINDOW} traded rows",
            "per_sub": "ma20_cross counts gap20 flips, ma60_cross "
                       "gap60",
        }


__all__ = ["MaWhipsaw"]
