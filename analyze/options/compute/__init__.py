"""Pure pandas computation logic for analyze.options — package form.

One module per data source / greek (see the module docstrings for the
metric semantics and industry anchors):

  skewness.py   — oi_moneyness (OI-wtd mean moneyness, positioning)
  oi_stats.py   — plain put/call OI ratio correlation stats
  walls.py      — 80pct + large_num wall levels
  iv_skew.py    — IV skew stats (ATM/25d wings/risk reversal) + iv_smile
                 (OI-wtd 3rd moment of IV, pricing)
  greek_delta.py — delta-weighted put/call OI ratio dpcr (PCR refinement,
                 neutral 0.5)
  greek_gamma.py — GEX-style call-minus-put gamma balance (neutral 0)
  greek_vega.py  — OTM-wing vega balance, the open-interest mirror of the
                 25d risk reversal (neutral 0)

The greek_* metrics are PAIR-level CALL-vs-PUT contrasts. theta/rho
have no industry-standard positioning skew (theta ≈ −½σ²S²Γ/365 is
collinear with gamma; rho ∝ T is negligible short-dated) and are
deliberately NOT computed.

GPU note: the pipeline is plain groupby/agg/cummax/rolling on ~1M rows;
the should_use_gpu import is included per project convention (the CPU
path handles this volume in seconds, so no cuDF-specific branch is
needed).
"""
from __future__ import annotations

from analyze.options.compute.greek_delta import (
    compute_options_greek_delta_skew_stats,
)
from analyze.options.compute.greek_gamma import (
    compute_options_greek_gamma_skew_stats,
)
from analyze.options.compute.greek_vega import (
    compute_options_greek_vega_skew_stats,
)
from analyze.options.compute.iv_skew import (
    compute_options_iv_skew_stats,
    compute_options_iv_smile_corr_stats,
)
from analyze.options.compute.oi_stats import compute_options_oi_stats
from analyze.options.compute.skewness import compute_options_skewness_stats
from analyze.options.compute.walls import compute_options_walls

# Per-greek dispatch for the greek skew pipeline (__main__), keyed by
# the greek names in analyze.options.config.GREEK_NAMES.
GREEK_SKEW_COMPUTERS = {
    "delta": compute_options_greek_delta_skew_stats,
    "gamma": compute_options_greek_gamma_skew_stats,
    "vega": compute_options_greek_vega_skew_stats,
}

__all__ = [
    "compute_options_skewness_stats",
    "compute_options_oi_stats",
    "compute_options_walls",
    "compute_options_iv_skew_stats",
    "compute_options_iv_smile_corr_stats",
    "compute_options_greek_delta_skew_stats",
    "compute_options_greek_gamma_skew_stats",
    "compute_options_greek_vega_skew_stats",
    "GREEK_SKEW_COMPUTERS",
]
