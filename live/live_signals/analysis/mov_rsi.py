"""mov_rsi signal evaluator — RSI percentile breach (indicator space).

Compares the current RSI (latest analysis.mov_ave_rsi row, per window)
against the top/bottom-1% threshold stored in signal_threshold.
"""
from __future__ import annotations

import json

from live.live_signals.config import BREACH_ABOVE_SIDES


def _parse_params(params: object) -> dict | None:
    """Normalise params from asyncpg JSONB (str or dict) → dict or None."""
    if isinstance(params, str):
        params = json.loads(params)
    return params if isinstance(params, dict) else None


def evaluate(
    sig: dict,
    rsi_by_window: dict[int, float],
) -> tuple[bool, float] | None:
    """Evaluate one mov_rsi signal config against current RSI values.

    Args:
        sig: Row dict from analysis_signals.signals (signal_type='mov_rsi').
        rsi_by_window: {window: current_rsi} from analysis.mov_ave_rsi.

    Returns:
        (triggered, compared_value), or None if not comparable
        (missing side in params or RSI unavailable).
    """
    params = _parse_params(sig["params"])
    if params is None:
        return None

    side = params.get("side")
    if side is None:
        return None

    thr: float = sig["signal_threshold"]
    w: int = int(params["rsi_window"])
    value: float | None = rsi_by_window.get(w)
    if value is None:
        return None

    above: bool = side in BREACH_ABOVE_SIDES
    triggered: bool = value > thr if above else value < thr
    return triggered, value
