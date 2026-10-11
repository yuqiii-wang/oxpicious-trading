"""Breach semantics + record numerics of live.live_signals (config).

THE rule's direction convention, the confidence scale the breach depth
applies to the row's confidence_base, and the decimal scales that keep
the stored identity signal_excess = signal - signal_threshold exact.
"""
from __future__ import annotations

from _common.labels import ACTION_CODE

# ---- Breach semantics ---------------------------------------------------------

# THE rule's direction convention: the analysis row's action CODE whose
# breach is an UPWARD crossing (value > threshold → signal_excess >
# 0). Every other action (buy, 0) breaches below (signal_excess < 0).
# action rides the threshold rows and the live records as its
# stats.label_dim SMALLINT code end-to-end (decode only for display).
ABOVE_ACTION = ACTION_CODE["sell"]

# ---- The confidence scale (the breach-depth amplifier) -----------------------
#
# An active threshold row carries its own scale model — scale_method /
# scale_base / scale_factor + confidence_base (the ON-BAR confidence,
# the bucket's sign-aligned dir_ave). The recorded breach confidence:
#
#     scale(|excess|) = scale_base + scale_factor * |excess|   -- linear
#     confidence(bps) = ROUND(10000 * confidence_base * scale)
#
# (The same formula the history writer applies — the breach depth
# amplifies the base: the more extreme the RSI beyond its percentile
# tail, the stronger the measured reversal. scale_base anchors the
# curve at zero excess, so a row sitting exactly on its bar carries
# exactly its base confidence. 'linear' is the only method the
# analysis build stamps; (linear, log, exponential) are reserved — an
# unknown method falls back to scale_base, the anchored on-bar point,
# never an invented curve. Negative scales clamp to 0.)
CONF_SCALE_METHOD_LINEAR = "linear"


def resolve_scale(sig: dict, excess: float) -> float:
    """One active row's confidence scale at its breach depth |excess|,
    per the row's OWN scale_method. Unknown method → scale_base (the
    anchored on-bar point; logged context upstream if ever hit)."""
    method = sig.get("scale_method")
    base = float(sig.get("scale_base") or 0.0)
    if method != CONF_SCALE_METHOD_LINEAR:
        return base
    factor = float(sig.get("scale_factor") or 0.0)
    return max(base + factor * abs(excess), 0.0)


def scaled_confidence_bp(sig: dict, excess: float) -> int:
    """One active row's SCALED breach confidence in integer basis
    points: ROUND(10000 x confidence_base x scale(|excess|)). The
    history writer (analysis.analysis_signals) applies the identical
    formula — the two breach tiers stay comparable."""
    conf_base = float(sig.get("confidence_base") or 0.0)
    scale = resolve_scale(sig, excess)
    return round(10_000 * conf_base * scale)


# ---- Record defaults ---------------------------------------------------------

# (confidence is NOT a default anymore: writers store the SCALED breach
# confidence — scaled_confidence_bp above — on the live INTEGER
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
    "CONF_SCALE_METHOD_LINEAR",
    "resolve_scale",
    "scaled_confidence_bp",
    "SIGNAL_SCALE",
    "SIGNAL_PCT_SCALE",
]
