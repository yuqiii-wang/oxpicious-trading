"""Forward horizons and the period vocabulary
(analyze.analysis_signals.config.horizons).

Ported from the retired analyze.analysis_forecasts config when the
forecast layer was removed 2026-10-05: the signals engine blends the
same per-horizon forward means into the mixed dir_ave the gate reads.
"""
from __future__ import annotations

# Forward-change horizons (trading days): next-day, 5d, 20d.
FORWARD_HORIZONS = (1, 5, 20)

# period string for each horizon (n = forward days).
PERIOD_FOR_HORIZON: dict[int, str] = {
    1: "next",
    5: "5d",
    20: "20d",
}

# The mixed row's FIXED horizon weights (horizon n → weight; 5d 0.65 /
# next 0.25 / 20d 0.10) — the blend behind the gate's dir_ave and the
# strategies' confidence. Weights are renormalized over the horizons
# whose stats exist when blending.
MIXED_HORIZON_WEIGHTS: dict[int, float] = {
    1: 0.25,   # next
    5: 0.65,   # 5d
    20: 0.10,  # 20d
}
