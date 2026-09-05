"""mov_rsi signals (analysis_signals.signals) — RSI extreme-percentile
days.

compute_rsi_signals — rsi_{W}days in the top pct% (RSI_PCT = 1 →
action=sell) or bottom pct% (action=buy) of the window's non-NULL
values (linear-interpolated percentile threshold, gathered from the
column-sorted window matrix — the same ``_thresholds`` helper the
forecast RSI engine uses). Threshold / cooldown / gate machinery in
_base.
"""
from __future__ import annotations

from datetime import date
from typing import Iterator

import numpy as np

from analyze.analysis_forecasts.config import RSI_WINDOWS
from analyze.analysis_forecasts.wide import MonthWindow
from analyze.analysis_signals.config import RSI_PCT, sub_type_rsi
from analyze.analysis_signals.signals._base import (
    ConfirmMap,
    _compute_pct_signals,
)


def compute_rsi_signals(
    mats: dict[str, np.ndarray],
    windows: list[MonthWindow],
    codes: list[str],
    sec_type: str,
    first_ord: np.ndarray,
    grid_ord: np.ndarray,
    confirm: ConfirmMap,
    rsi_windows: tuple = RSI_WINDOWS,
) -> Iterator[tuple[date, list[dict]]]:
    """Yield (stat_month, signal rows) per stat month — RSI family.

    Args:
        mats: wide rsi matrices keyed f"rsi_{w}".
        confirm: keyed (stat_month, "rsi_{w}", side) — see
              _compute_pct_signals (the window key is the matrix key).
        rsi_windows: RSI windows to emit (default: forecasts config).
    """
    return _compute_pct_signals(
        mats, windows, codes, sec_type, first_ord, grid_ord, confirm,
        keys=[f"rsi_{w}" for w in rsi_windows],
        pct=RSI_PCT,
        signal_type="mov_rsi",
        sub_type={f"rsi_{w}": sub_type_rsi(w) for w in rsi_windows},
        param_key="rsi_window",
        fmt=".2f",
    )
