"""hedge_pairs benefit audit — the portfolio gate's numbers: the
inverse-vol pair's max-drawdown reduction vs the standalone legs (the
neg_corr_hedge_pair STRUCTURE claim) and the gate digest that turns it
into the member BENEFIT BOOST scale."""
from __future__ import annotations

import numpy as np

from analysis.analysis_signals import config as cfg


def benefit(grid: dict) -> dict:
    """The neg_corr_hedge_pair STRUCTURE claim: the inverse-vol
    pair value's own max drawdown vs each leg's standalone —
    reduction in percentage points (drawdowns are negative pcts;
    reduction = portfolio − leg in signed space, >= 0 = the
    coordination paid)."""
    port = float(np.nanmin(grid["dd_value"]))
    a = float(np.nanmin(grid["dd_a"]))
    b = float(np.nanmin(grid["dd_b"]))
    worst = min(a, b)
    mean = (a + b) / 2.0
    return {
        "kind": "max_drawdown_reduction",
        "max_dd": {
            "window": "full_history",
            "portfolio_pct": round(port * 100.0, 4),
            "leg_a_pct": round(a * 100.0, 4),
            "leg_b_pct": round(b * 100.0, 4),
            "worst_leg_pct": round(worst * 100.0, 4),
            "mean_leg_pct": round(mean * 100.0, 4),
            "reduction_vs_worst_pct": round((port - worst) * 100.0, 4),
            "reduction_vs_mean_pct": round((port - mean) * 100.0, 4),
        },
    }


def gate_digest(benefit: dict) -> dict:
    """The portfolio gate: the audited benefit vs the MEAN leg
    clearing PF_GATE_MIN_PP; conf_scale = the member benefit-boost
    scale (an INCREASE only — 1.0 when the gate failed)."""
    benefit_pp = benefit["max_dd"]["reduction_vs_mean_pct"]
    passed = benefit_pp >= cfg.PF_GATE_MIN_PP
    scale = 1.0
    if passed:
        scale = 1.0 + cfg.PF_CONF_FACTOR * min(
            benefit_pp / cfg.PF_CONF_UNIT_PP, cfg.PF_CONF_CAP)
    return {
        "metric": cfg.PF_GATE_METRIC,
        "min_pp": cfg.PF_GATE_MIN_PP,
        "passed": bool(passed),
        "conf_scale": round(scale, 4),
    }
