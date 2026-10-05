"""mov_rsi CURVE emission (mov_rsi.curve) — the continuous
depth-response path: curve_records, one analysis_signals.signal_curves
row per (code, rsi_window, side, regime_state, snapshot) carrying the
fitted line mu(s) = mu_center + slope * (s - s_center) over the
Gaussian depth score (see curve/_curves.py for the formulation and the
2026-10 depth-response study it encodes)."""
from analyze.analysis_signals.engines.signal_families.mov_rsi.curve._curves import (  # noqa: E501
    curve_records,
)

__all__ = ["curve_records"]
