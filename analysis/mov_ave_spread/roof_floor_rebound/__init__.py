"""Roof/floor REBOUND AUDIT — internal step package of
analysis.mov_ave_spread.

Target table: analysis.mov_ave_spreads_roof_floor_rebounds (one row per
recorded extreme event: the episode extreme of fresh W-day extremes,
with its rebound / retest / hold / break outcome and amplitudes — the
2026-10 retest study formulated as an auditable mov_ave table). The
audit answers, per (code, side, window): how often a recorded extreme
REBOUNDS (rebound frequency), how far (rebound amplitude), and — once
rebounded — whether the level HOLDS or BREAKS on retest, with the
bounce / break-depth amplitudes and durability flags.

Rebuilt WHOLESALE per sec_type (outcomes depend on post-event data).
See events.py for the computation and step.py for the runner.
"""
from analysis.mov_ave_spread.roof_floor_rebound.events import (
    compute_rebound_events,
)
from analysis.mov_ave_spread.roof_floor_rebound.step import (
    run_roof_floor_rebounds,
)

__all__ = [
    "compute_rebound_events",
    "run_roof_floor_rebounds",
]
