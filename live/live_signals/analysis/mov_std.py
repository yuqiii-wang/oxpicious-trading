"""mov_std signal evaluator — Bollinger band breach (price space).

Compares the live intraday close against the ``signal_threshold``
(the band level stored in analysis_signals.signals).
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
    close: float,
) -> tuple[bool, float] | None:
    """Evaluate one mov_std signal config against the live close.

    Args:
        sig: Row dict from analysis_signals.signals (signal_type='mov_std').
        close: Live intraday close price.

    Returns:
        (triggered, compared_value), or None if not comparable
        (missing side in params).
    """
    params = _parse_params(sig["params"])
    if params is None:
        return None

    side = params.get("side")
    if side is None:
        return None

    thr: float = sig["signal_threshold"]
    above: bool = side in BREACH_ABOVE_SIDES
    triggered: bool = close > thr if above else close < thr
    return triggered, close
