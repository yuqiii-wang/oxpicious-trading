"""mov_gap signals (analysis_signals.signals) — N-day price-return
extreme-percentile days.

compute_gap_signals — gap_{W}days (the W-day fractional price return,
from analysis.mov_ave_rsi) in the top pct% (GAP_PCT = 1 → action=sell,
sharp W-day rally) or bottom pct% (action=buy, sharp W-day selloff) of
the window's non-NULL values — the exact compute_rsi_signals machinery
applied to unbounded fractional returns (the percentile thresholding is
rank-based, so identical). Threshold / cooldown / gate machinery in
_base.
"""
from __future__ import annotations

from datetime import date
from typing import Iterator

import numpy as np

from analyze.analysis_forecasts.config import GAP_WINDOWS
from analyze.analysis_forecasts.wide import MonthWindow
from analyze.analysis_signals.config import GAP_PCT, sub_type_gap
from analyze.analysis_signals.signals._base import (
    ConfirmMap,
    _compute_pct_signals,
)


def compute_gap_signals(
    mats: dict[str, np.ndarray],
    windows: list[MonthWindow],
    codes: list[str],
    sec_type: str,
    first_ord: np.ndarray,
    grid_ord: np.ndarray,
    confirm: ConfirmMap,
    gap_windows: tuple = GAP_WINDOWS,
) -> Iterator[tuple[date, list[dict]]]:
    """Yield (stat_month, signal rows) per stat month — gap family.

    Identical machinery to compute_rsi_signals applied to the
    gap_{W}days N-day price-return matrices (analysis.mov_ave_rsi):
    top 1% = sharp W-day rally → sell, bottom 1% = sharp W-day selloff
    → buy.

    Args:
        mats: wide gap matrices keyed f"gap_{w}".
        confirm: keyed (stat_month, "gap_{w}", side) — see
              _compute_pct_signals.
        gap_windows: gap windows to emit (default: forecasts config).
    """
    return _compute_pct_signals(
        mats, windows, codes, sec_type, first_ord, grid_ord, confirm,
        keys=[f"gap_{w}" for w in gap_windows],
        pct=GAP_PCT,
        signal_type="mov_gap",
        sub_type={f"gap_{w}": sub_type_gap(w) for w in gap_windows},
        param_key="gap_window",
        fmt=".4f",
    )
