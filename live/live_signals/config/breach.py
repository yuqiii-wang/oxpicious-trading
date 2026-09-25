"""Breach semantics + record numerics of live.live_signals (config).

THE rule's direction convention and the decimal scales that keep the
stored identity signal_excess = signal - signal_threshold exact.
"""
from __future__ import annotations

# ---- Breach semantics ---------------------------------------------------------

# THE rule's direction convention: the analysis row's action whose
# breach is an UPWARD crossing (value > threshold → signal_excess >
# 0). Every other action ('buy') breaches below (signal_excess < 0).
ABOVE_ACTION = "sell"

# ---- Record defaults ---------------------------------------------------------

# (confidence is NOT a default anymore: writers store
# ROUND(10000 × analysis_signals.signal_strategies.confidence) — the
# source confidence (the chosen rung's sign-aligned dir_ave, the
# expected favorable blended move as a fraction) on the live INTEGER
# basis-point scale. The scaling happens SQL-side in the writers'
# fetches; live.live_signals.confidence keeps DEFAULT 100 (a legacy
# pre-2026-09-25 fill).)

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
    "SIGNAL_SCALE",
    "SIGNAL_PCT_SCALE",
]
