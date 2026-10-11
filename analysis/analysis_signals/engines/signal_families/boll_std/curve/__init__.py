"""boll_std CURVE emission (boll_std.curve) — the continuous σ-depth
response path: curve_records, one analysis_signals.signal_curves row
per (code, boll_window, side, regime_state, snapshot) carrying
curve(s) = mu_center + interp(fit_curve, s) over the σ-depth
s = |z| (which IS the Gaussian depth score — see curve/_curves.py for
the formulation and the fixed 2 / 2.5 / 3σ thresholds' place on it)."""
from analysis.analysis_signals.engines.signal_families.boll_std.curve._curves import (  # noqa: E501
    curve_records,
)

__all__ = ["curve_records"]
