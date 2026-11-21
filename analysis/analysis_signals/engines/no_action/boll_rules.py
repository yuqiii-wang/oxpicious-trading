"""bollinger's no-action rules (engines/no_action/boll_rules.py).

`boll_squeeze_breakdown` (NO BUY) — MEASURED (the 2026-10-16 honest
negative): a downside band breach after bandwidth compression
pooled -0.15% — compression -> breakdown CONTINUES here, it does
not revert. A -2sigma pierce out of a squeezed band is a momentum
event wearing a mean-reversion costume.

`boll_bandwalk_sell` (NO SELL) — John Bollinger's own band-walk
warning (mirrors the mov_rsi band walk with the band's own
observable): price riding an EXPANDING upper band with the SMA20
slope positive is continuation, not exhaustion.

The bandwidth is re-derived independently (the family engine keeps
sma/sigma as temporaries — only boll_z rides the frame): sigma20 /
sma20 with population sigma (ddof=0), the family's own convention.
The squeeze/walk cuts are the bandwidth's own TRAILING terciles
(shifted — the day never sets its own bar).
"""
from __future__ import annotations

from typing import ClassVar

import numpy as np
import pandas as pd

from _common.df_utils import (
    grouped_rolling_agg,
    grouped_rolling_quantile,
    grouped_shift,
    safe_columns,
)

from analysis.analysis_signals import config as cfg
from analysis.analysis_signals.engines.no_action.base import NoActionRule

_WINDOW = 20  # the family's own window (boll_std engine's constant)


def _attach_bandwidth(frame: pd.DataFrame) -> None:
    """sigma20/sma20 (population sigma, ddof=0 — the family's own
    convention), shared by both rules; idempotent."""
    if "_na_bw" in set(safe_columns(frame)):
        return
    sma = grouped_rolling_agg(frame, ["code"], "price", _WINDOW,
                              min_periods=_WINDOW)
    sigma = grouped_rolling_agg(frame, ["code"], "price", _WINDOW,
                                min_periods=_WINDOW, agg="std", ddof=0)
    frame["_na_boll_sma"] = sma
    frame["_na_bw"] = sigma / sma


def _shifted_bw_quantile(frame: pd.DataFrame, q: float,
                         out: str) -> None:
    """The bandwidth's own trailing quantile, SHIFTED one row (the
    day never sets its own bar) — written to ``out``."""
    ser = grouped_rolling_quantile(
        frame, ["code"], "_na_bw", cfg.NO_ACTION_BOLL_BW_WINDOW,
        cfg.NO_ACTION_BOLL_BW_MIN_PERIODS, q,
    )
    frame[out] = ser
    grouped_shift(frame, ["code"], out, periods=1)


class BollSqueezeBreakdown(NoActionRule):
    """NO BUY at a downside breach out of a squeezed band — the
    calibration's ONE shipping rule (2026-11-18): the only
    candidate that separates on the fired population (marked 1.36%
    vs kept 2.30% aligned blend at the tercile; the shipped cut is
    the sweep's strictest separating quantile, config
    NO_ACTION_BOLL_SQUEEZE_Q)."""

    REASON: ClassVar[str] = "boll_squeeze_breakdown"
    FAMILY: ClassVar[str | None] = "bollinger"
    ACTIONS: ClassVar[tuple[str, ...]] = ("buy",)
    SUB_TYPES: ClassVar[tuple[str, ...] | None] = None
    CONTEXT_COLS: ClassVar[tuple[str, ...]] = ("_na_boll_sq",)

    def attach(self, frame: pd.DataFrame, ctx) -> None:
        _attach_bandwidth(frame)
        _shifted_bw_quantile(frame, cfg.NO_ACTION_BOLL_SQUEEZE_Q,
                             "_na_bw_lo")
        bw, lo = frame["_na_bw"], frame["_na_bw_lo"]
        # NaN comparisons read False — early rows never squeeze-mark
        frame["_na_boll_sq"] = bw.notna() & (bw < lo)

    def condition_params(self) -> dict:
        return {
            "bandwidth": "sigma20/sma20 (ddof=0)",
            "cut": f"bottom q{cfg.NO_ACTION_BOLL_SQUEEZE_Q:g} of its "
                   f"own trailing {cfg.NO_ACTION_BOLL_BW_WINDOW}-row "
                   f"distribution (shifted)",
            "evidence": "measured 2026-10-16: squeeze->breakdown "
                        "pooled -0.15%; calibration 2026-11-18: "
                        "separates inside the fired population",
        }


class BollBandwalkSell(NoActionRule):
    """NO SELL at an upside breach riding an expanding band."""

    REASON: ClassVar[str] = "boll_bandwalk_sell"
    FAMILY: ClassVar[str | None] = "bollinger"
    ACTIONS: ClassVar[tuple[str, ...]] = ("sell",)
    SUB_TYPES: ClassVar[tuple[str, ...] | None] = None
    CONTEXT_COLS: ClassVar[tuple[str, ...]] = ("_na_boll_bww",)

    def attach(self, frame: pd.DataFrame, ctx) -> None:
        _attach_bandwidth(frame)
        _shifted_bw_quantile(frame, 2.0 / 3.0, "_na_bw_hi")
        grouped_shift(frame, ["code"], "_na_bw", out_names="_na_bw_p",
                      periods=1)
        grouped_shift(frame, ["code"], "_na_boll_sma",
                      out_names="_na_boll_sma_p5", periods=5)
        bw, hi = frame["_na_bw"], frame["_na_bw_hi"]
        rising = bw > frame["_na_bw_p"].fillna(-np.inf)
        slope = frame["_na_boll_sma"] > frame["_na_boll_sma_p5"].fillna(
            -np.inf)
        frame["_na_boll_bww"] = (bw.notna() & hi.notna()
                                 & (bw > hi) & rising & slope)

    def condition_params(self) -> dict:
        return {
            "bandwidth": "sigma20/sma20 (ddof=0)",
            "cut": f"top tercile of its own trailing "
                   f"{cfg.NO_ACTION_BOLL_BW_WINDOW}-row "
                   f"distribution (shifted) AND rising",
            "slope": "sma20 rising over 5 rows",
        }


__all__ = ["BollBandwalkSell", "BollSqueezeBreakdown"]
