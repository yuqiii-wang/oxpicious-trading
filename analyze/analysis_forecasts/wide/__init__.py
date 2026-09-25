"""Snapshot-grid specs for analyze.analysis_forecasts.

The annual-snapshot engines (``_dfengine.WideDfEngine`` subclasses —
one metric one file) work on cudf.pandas DataFrames end to end: the
fetch layer loads and derives every long-format column with vectorized
grouped ops (grouped_shift / grouped_rolling_agg), and the engines
aggregate per (code chunk × snapshot) in frames. The former numpy
(date × code) matrix machinery (grid / changes / thresholds / signals /
horizons / results — kept alive only by the retired opp_pair legacy
pipeline) was removed with that family in 2026-09; this package is now
the snapshot-grid spec alone:

  1. ``build_stat_specs`` — the target snapshot dates (the completed
     year-ends of the ANNUAL grid plus the ROLLING LATEST snapshot,
     keyed at the sec_type's latest available data date — NOT the
     year-end) and each snapshot's inclusive trailing-window start
     (stat_date minus WINDOW_YEARS + 1 day — config.window_lower).
"""
from __future__ import annotations

from .months import (
    StatSpec,
    _shift_years,
    build_stat_specs,
)

__all__ = [
    "StatSpec",
    "_shift_years",
    "build_stat_specs",
]
