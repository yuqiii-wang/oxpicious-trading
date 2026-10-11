"""The ohlcv boost — the GENERAL trust-score formula over a trigger
day's market anatomy (analysis.analysis_signals.engines.ohlcv_boost).

A signal family opts in by (a) fetching the day-0 axis columns (the
OHLC pattern code, the (vol_state, px_speed) amount/price-speed cell —
see _axes) onto its input frame, (b) passing its day-0 anchors through
anchor_scores() with its bucket identities and its per-side configs
(_configs — the measured ladders), and (c) stamping the verdicts:
per-anchor boost_flag on its history/trigger rows, the bucket-level
mean-score verdict (bucket_flags) on its threshold and curve rows as
``is_ohlcv_boosted``. The formula itself knows nothing about RSI — the
depth axis is the generic "value past the bucket's own bar" excess.

The flag is an analysis marker on the SAME rows — never a gate, never
a PK member, never part of is_active / the live threshold set (the
retired forecast-layer cohort POPULATIONS are not revived; the 2026-09
design is retraced in docs/ohlcv_boost_summary.md).
"""
from __future__ import annotations

from analysis.analysis_signals.engines.ohlcv_boost._axes import (
    AXIS_MISSING,
    PATTERN_CODE,
    PX_SPEED_CODE,
    VOL_STATE_CODE,
)
from analysis.analysis_signals.engines.ohlcv_boost._config import (
    OhlcvBoostConfig,
)
from analysis.analysis_signals.engines.ohlcv_boost._configs import (
    OHLCV_BOOST_CONFIGS,
    enabled,
    side_configs,
)
from analysis.analysis_signals.engines.ohlcv_boost._score import (
    anchor_scores,
    bucket_flags,
)

__all__ = [
    "AXIS_MISSING",
    "PATTERN_CODE",
    "PX_SPEED_CODE",
    "VOL_STATE_CODE",
    "OhlcvBoostConfig",
    "OHLCV_BOOST_CONFIGS",
    "enabled",
    "side_configs",
    "anchor_scores",
    "bucket_flags",
]
