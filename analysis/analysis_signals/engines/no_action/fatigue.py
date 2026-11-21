"""The cross-family FATIGUE filter (engines/no_action/fatigue.py —
2026-11-18, user-directed: "another goal of no action is for within
recent days too many same type signals are fired, study filter to
as no action").

Semantics (the conservative reading, per the invalidation study):
per (code, signal_type, ACTION) — buy fatigue and sell fatigue are
separate stories — when the trailing NO_ACTION_FATIGUE_WINDOW_ROWS
of the code's OWN traded rows contain >= NO_ACTION_FATIGUE_MIN_DAYS
DISTINCT FIRED DAYS of that triple, fired days BEYOND the bar carry
the no-action mark. Distinct DATES count once regardless of how
many sub types fired that date (a crisis day firing rsi14+rsi10+
rsi6 tails together is ONE day of the same wash — the nested carve
already deduplicated the tails); every counted day is a distinct
episode-first trigger (the K-absorb merges within-episode
re-entries), so N distinct days in W rows is genuine chop — the
bar region crossed and re-crossed. Market-wide bursts are
deliberately NOT counted: the cross-sectional washout (2024-09-30,
2025-04-07) is where the BUY edge is strongest in the whole record.

The count window is STRICTLY BEFORE the fired day (the day's own
flag never counts toward its own mark) — grouped rolling sum over
the day-flag column, shifted one row.
"""
from __future__ import annotations

from typing import ClassVar

import numpy as np
import pandas as pd

from _common.df_utils import grouped_rolling_agg, grouped_shift, host_array

from analysis.analysis_signals import config as cfg
from analysis.analysis_signals.engines.no_action.base import (
    NoActionCtx,
    NoActionRule,
)

_CNT_COL = {0: "_na_fcnt_buy", 1: "_na_fcnt_sell"}


class FatigueFilter(NoActionRule):
    """The recent-days same-type saturation mark — cross-family
    (every standalone family, both actions, every sub type)."""

    REASON: ClassVar[str] = "fatigue"
    FAMILY: ClassVar[str | None] = None
    ACTIONS: ClassVar[tuple[str, ...]] = ("buy", "sell")
    SUB_TYPES: ClassVar[tuple[str, ...] | None] = None
    CONTEXT_COLS: ClassVar[tuple[str, ...]] = (_CNT_COL[0], _CNT_COL[1])

    def attach(self, frame: pd.DataFrame, ctx: NoActionCtx) -> None:
        """Stamp the per-action FIRED-DAY flags positionally (host
        boundary — the driver's pos_index lookup, <= one set probe
        per fired day), then count them per code over the trailing
        window, strictly before each row (shift 1)."""
        n = len(frame)
        for action, days in ctx.fired_days.items():
            flag = np.zeros(n, dtype="float64")
            for key in days:
                i = ctx.pos_index.get(key)
                if i is not None:
                    flag[i] = 1.0
            fcol = f"_na_fday_{'buy' if action == 0 else 'sell'}"
            frame[fcol] = flag
            cnt = grouped_rolling_agg(
                frame, ["code"], fcol, cfg.NO_ACTION_FATIGUE_WINDOW_ROWS,
                min_periods=1, agg="sum",
            )
            frame[_CNT_COL[action]] = cnt
            # the window is STRICTLY BEFORE the row (grouped_shift
            # overwrites the column with its own one-row shift)
            grouped_shift(frame, ["code"], _CNT_COL[action], periods=1)

    def condition(self, row: dict, ctxv: dict) -> bool:
        cnt = ctxv.get(_CNT_COL[row["action"]])
        if cnt is None or np.isnan(cnt):
            return False
        return cnt >= cfg.NO_ACTION_FATIGUE_MIN_DAYS

    def condition_params(self) -> dict:
        return {
            "min_days": cfg.NO_ACTION_FATIGUE_MIN_DAYS,
            "window_rows": cfg.NO_ACTION_FATIGUE_WINDOW_ROWS,
            "unit": "distinct fired days of (code, signal_type, "
                    "action) in the code's own trailing traded rows, "
                    "strictly before the fired day",
        }


__all__ = ["FatigueFilter"]
