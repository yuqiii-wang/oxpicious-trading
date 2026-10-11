"""Forward horizons and the period vocabulary
(analysis.analysis_signals.config.horizons).

Ported from the retired analysis.analysis_forecasts config when the
forecast layer was removed 2026-10-05: the signals engine blends the
same per-horizon forward means into the mixed dir_ave the gate reads.
"""
from __future__ import annotations

# Forward-change horizons (trading days): next-day, 3d, 5d, 10d, 20d.
FORWARD_HORIZONS = (1, 3, 5, 10, 20)

# period string for each horizon (n = forward days).
PERIOD_FOR_HORIZON: dict[int, str] = {
    1: "next",
    3: "3d",
    5: "5d",
    10: "10d",
    20: "20d",
}

# The mixed row's FIXED horizon weights (horizon n → weight; 5d 0.50 /
# 3d 0.15 / 10d 0.15 / next 0.10 / 20d 0.10) — the blend behind the
# gate's dir_ave and the strategies' confidence. Weights are
# renormalized over the horizons whose stats exist when blending.
# (2026-10-06: 10d leg added and 5d rebalanced 0.65 -> 0.50 — the
# response's 5-10d window now carries its own measured leg instead of
# leaning on the 5d/20d endpoints.)
MIXED_HORIZON_WEIGHTS: dict[int, float] = {
    1: 0.10,   # next
    3: 0.15,   # 3d
    5: 0.50,   # 5d
    10: 0.15,  # 10d
    20: 0.10,  # 20d
}
