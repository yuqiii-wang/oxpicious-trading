"""Breach semantics + record numerics of live.live_signals (config).

THE rule's direction convention, the rules-era confidence scale the
breach depth applies to the cell's measured base, and the decimal
scales that keep the stored identity signal_excess = signal -
signal_threshold exact.
"""
from __future__ import annotations

from _common.labels import ACTION_CODE

# ---- Breach semantics ---------------------------------------------------------

# THE rule's direction convention: the analysis row's action CODE whose
# breach is an UPWARD crossing (value > threshold → signal_excess >
# 0). Every other action (buy, 0) breaches below (signal_excess < 0).
# action rides the rule rows and the live records as its
# static.label_dim SMALLINT code end-to-end (decode only for display).
ABOVE_ACTION = ACTION_CODE["sell"]

# ---- The confidence scale (the rules-era breach-depth amplifier) -------------
#
# An active RULE CELL carries its own confidence scale in
# rule_params.confidence — base_pct (the cell's measured on-bar
# sign-aligned dir_ave, in PERCENT) amplified LINEARLY over the
# normalized breach depth by depth_factor, capped at depth_cap:
#
#     confidence(pct) = base_pct × (1 + depth_factor × min(depth, cap))
#                       × sample_scale
#
# — the IDENTICAL formula the history writer (rule_base.confidence_pct)
# applies, so the two breach tiers stay comparable. sample_scale is
# the starvation haircut (<= 1). The depth
# normalization unit rides the bar_def kind (the engines' own scales):
#
#     rolling_quantile  |excess| / 10  (RSI points — mov_rsi's
#                          _DEPTH_SCALE 10.0; a 10-point pierce = 1.0)
#     fixed_sigma       |excess| / 1   (σ units — bollinger's depth is
#                          the raw σ excess past ±k)
#
# Negative amplification clamps to 0 — EXCEPT the REJECT tier
# (2026-11-16, engines/reject.py): a reject cell's base_pct (its latest
# snapshot's dir_ave) is NEGATIVE, and the negative sign IS the product
# — the live row reads "do NOT take this action at this price", never
# the inverse action. The record stores the INTEGER basis-point scale
# (×100), clamped SYMMETRICALLY to [-10000, 10000]
# (CONFIDENCE_CAP_BP; live.live_signals' CHECK relaxed the same day).
DEPTH_NORM_UNITS: dict[str, float] = {
    "rolling_quantile": 10.0,
    "fixed_sigma": 1.0,
}


def breach_depth_norm(sig: dict, excess: float) -> float:
    """One rule cell's NORMALIZED breach depth: |excess| in the cell's
    own depth unit (per its bar_def kind). Unknown kinds normalize by
    1.0 (raw excess)."""
    unit = DEPTH_NORM_UNITS.get(
        (sig.get("bar_def") or {}).get("kind"), 1.0,
    )
    return abs(excess) / unit


def rules_confidence_bp(sig: dict, excess: float) -> int:
    """One rule cell's SCALED breach confidence on the INTEGER
    basis-point record scale: base_pct × (1 + depth_factor ×
    min(depth_norm, depth_cap)) × sample_scale
    percent over the normalized breach depth — the history writer's
    identical formula (rule_base.confidence_pct for trading cells,
    engines.reject.py's mirrored scale for reject cells) converted
    ×100, clamped SYMMETRICALLY to [-10000, 10000] (2026-11-16): a
    NEGATIVE base (the reject cell's measured dir_ave) yields a
    NEGATIVE confidence — REJECT the action at this price, never the
    inverse action. sample_scale is the starvation haircut
    min(n/floor, 1) RECOMPUTED by the fetch since 2026-10-20 (the
    latest snapshot's n_episodes against the rule's
    gate.sample_floor constant — the digest left the slimmed cell
    snapshot; missing = no haircut)."""
    conf = sig.get("confidence") or {}
    base_pct = float(conf.get("base_pct") or 0.0)
    factor = float(conf.get("depth_factor") or 0.0)
    cap = float(conf.get("depth_cap") or 2.0)
    scale = float(conf.get("sample_scale") or 1.0)
    depth = min(breach_depth_norm(sig, excess), cap)
    return int(min(max(round(
        base_pct * (1.0 + factor * depth) * scale * 100.0,
    ), -10_000), 10_000))

# ---- Record defaults ---------------------------------------------------------

# (confidence is NOT a default: writers store the SCALED breach
# confidence — rules_confidence_bp above — on the live INTEGER
# basis-point scale. live.live_signals.confidence keeps DEFAULT 100 (a
# legacy pre-2026-09-25 fill).)

# Decimal scale of live.live_signals.signal (NUMERIC(16,4)): records
# round the compared value to this scale BEFORE writing signal, and
# signal_excess = rounded_signal - signal_threshold, so the stored
# identity signal_excess = signal - signal_threshold holds exactly.
SIGNAL_SCALE = 4

# Decimal scale of live.live_signals.signal_excess_pct (NUMERIC(12,4)):
# signal_excess_pct = signal_excess / |signal_threshold| * 100 — rounded
# to this scale so the stored identity holds exactly. Guarded against
# divide-by-zero (None when signal_threshold == 0).
SIGNAL_PCT_SCALE = 4

__all__ = [
    "ABOVE_ACTION",
    "DEPTH_NORM_UNITS",
    "breach_depth_norm",
    "rules_confidence_bp",
    "SIGNAL_SCALE",
    "SIGNAL_PCT_SCALE",
]
