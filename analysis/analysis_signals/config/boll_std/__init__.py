"""boll_std signal-family configuration.

Emission slice: the fixed-σ Bollinger band thresholds 2 / 2.5 / 3 σ (2.0
the classic default — the rsi14 analog), both sides, every regime split
(regime_state is a signal_thresholds PK member — each split registers on
its own gate pass). Unlike mov_rsi's per-(code, window) percentile bars,
the boll bar is the FIXED σ multiplier in the z space (top bar = +k, the
upper band; bottom bar = −k, the lower band) — the band breach IS
|z| >= k and the breach excess depth is |z| − k, in σ units, the same
quantity the curve tier reads as its x axis.

The family is IN-ENGINE since 2026-10-10: the z values + the episode
layer are computed on the fetched price frame by
engines/signal_families/boll_std/_compute.py (the classic trailing
SMA/σ construction per window, plus the shared mask-driven episode
classifier) — there is no analysis-layer boll table. This module owns
the window grid (a subset of mov_ave_spread's MA_WINDOWS, so the
5-day band has the same σ support the ma-spread suite publishes — the
retired 10-day band sat outside that grid), beside the episode layer's
band edge (0.6745σ — the Gaussian 25% tail, the rsi rank-band analog)
and the shared re-entry exit K (the episode boundary BOTH signal tiers
debounce with — the same constant the rsi analysis layer's flags
segment on, imported from the rsi analysis layer where the semantics
live).
"""
from analysis.mov_ave_spread.steps.rsi_typical import RSI_REENTRY_EXIT_DAYS

# The Bollinger windows (days): 20 is THE classic (SMA-20 ± kσ — the
# family's default, the rsi14 analog); 5 is the short-term band; 60
# (~3 trading months) the slow regime-depth window. A subset of the
# parent ma-spread pipeline's MA_WINDOWS grid (5/20/60/120/255).
BOLL_WINDOWS = (5, 20, 60)

# The emission slice: the emitted Bollinger σ-multiplier thresholds.
# 2.0 = the classic Bollinger band default (the rsi14 analog);
# 2.5 / 3.0 = the deeper breach thresholds (one-sided Gaussian tail
# masses 0.62% / 0.13% vs 2.28% at 2σ).
BOLL_STDS_EMIT = (2.0, 2.5, 3.0)

# The episode band's σ edge: the Gaussian 25% tail (Φ^-1(0.75) =
# 0.6745) — the RSI layer's full-history rank-band analog read in the
# z space the band itself lives in. Days with |z| >= this edge are the
# tail-episode population the family's tiers ride (computed in-engine
# beside z by _compute.py).
BOLL_TYPICAL_BAND_S = 0.6745

# σ below this is degenerate (a flat W-day window) — the depth is
# undefined, never a huge number (the in-engine z guard).
BOLL_SIGMA_FLOOR = 1e-12

# Bucket sides: top = upper-band breach (z >= +k) → sell,
#               bottom = lower-band breach (z <= −k) → buy.
BOLL_SIDES = ("top", "bottom")

# The confidence-scale slope over the breach depth (σ units — the
# mov_rsi default 0.05 is per RSI point on a 0..100 scale; the boll
# excess typically spans 0..2σ, so the per-σ slope is proportionally
# larger: a 1σ-deep breach amplifies the base confidence by 1.25x).
BOLL_SCALE_FACTOR_DEFAULT = 0.25

# The stored fit_curve's s grid (lo, hi, step) — the boll family's grid
# extends past the 3σ bar (3.09 ≈ the 0.1% Gaussian tail edge) so the
# deepest emitted threshold sits INSIDE the drawn fit, not at its edge.
BOLL_CURVE_FIT_S_GRID = (0.66, 3.10, 0.02)

# The BUY side's curve-fit deep-arm edge (2026-10-11): the pre-registered
# σ-depth where the "deep arm" of the loosely-monotone fit
# (engines/_curve_fit.deep_arm_iso) begins — fixed at the 2.5σ emitted
# bar, NOT data-selected, so the deep-vs-rest contrast the gate reads is
# chosen before the data is looked at. The measured buy-side response is
# a shallow plateau with the favorable rise concentrated past ~2.5σ (a
# U over the mid band — the grind — and a snap-back at the capitulation
# tail), which the global rank correlation reads ~0-to-negative and the
# strict gate stored flat everywhere; the arm contrast restores it
# (permutation-null calibrated at ~5% false-draw at t >= 2). The SELL
# side keeps the rank-gated isotonic — its response IS rank-monotone.
BOLL_CURVE_DEEP_ARM_S = 2.5


def format_std(k: float) -> str:
    """The σ multiplier's sub_type literal: whole values bare ("2"),
    fractional as {int}p{tenth} ("2p5" for 2.5)."""
    if float(k) == int(k):
        return str(int(k))
    return f"{int(k)}p{int(round((float(k) - int(k)) * 10))}"


def boll_sub_type(window: int, k: float) -> str:
    """The strategy sub_type: boll{W}_{k}std (e.g. boll20_2std,
    boll20_2p5std, boll5_3std) — the first integer run is the window
    (the live tier's sub_type_window), the σ multiplier rides after it."""
    return f"boll{int(window)}_{format_std(k)}std"


__all__ = [
    "BOLL_WINDOWS", "BOLL_STDS_EMIT", "BOLL_SIDES", "BOLL_TYPICAL_BAND_S",
    "BOLL_SIGMA_FLOOR", "BOLL_CURVE_FIT_S_GRID", "BOLL_SCALE_FACTOR_DEFAULT",
    "BOLL_CURVE_DEEP_ARM_S",
    "RSI_REENTRY_EXIT_DAYS", "format_std", "boll_sub_type",
]
