"""hedge_pairs bar_def builders — the WINNING tail + RSI window each
emitted cell records on rule_params.bar_def (readable by the UI bar
decoders; the grids themselves ride the portfolio fragment's
coordination.tail_pcts / .rsi_windows)."""
from __future__ import annotations

from analysis.analysis_signals import config as cfg


def trio_bar_def(kind: str, pct: int,
                 window: int | None = None) -> dict:
    """The trio candidate's own bar_def — the WINNING tail + RSI window
    ride the emitted cell's rule_params (the self-picked bar, readable
    by the UI's bar decoder; the grids themselves ride the portfolio
    fragment's coordination.tail_pcts / .rsi_windows)."""
    return {
        "kind": kind,
        "tail_pct": pct,
        "window": window if window is not None else cfg.HP_LEG_RSI_WINDOW,
        "bar_window_days": cfg.HP_LEG_BAR_WINDOW_DAYS,
        "shift": 1,
    }


def spread_bar_def(kind: str, pct: int,
                   window: int | None = None) -> dict:
    """The spread candidate's own bar_def — the same shape as the
    trio's (the WINNING tail + RSI window are the cell's recorded bar);
    the axis is the pair's own long-short SPREAD curve."""
    return trio_bar_def(kind, pct, window)
