"""mov_rsi CURVE emission (mov_rsi.curve) — the continuous
depth-response path: curve_records, one analysis_signals.signal_curves
row per (code, rsi_window, side, regime_state, snapshot) carrying
curve(s) = mu_center + interp(fit_curve, s) over the Gaussian depth
score (see curve/_curves.py for the formulation, the 2026-10
depth-response study and the 2026-10-06 episode-population change it
encodes)."""
from analysis.analysis_signals.engines.signal_families.mov_rsi.curve._curves import (  # noqa: E501
    curve_records,
)

__all__ = ["curve_records"]
