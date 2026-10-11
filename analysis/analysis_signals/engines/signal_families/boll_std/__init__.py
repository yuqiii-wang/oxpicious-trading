"""The boll_std signal family (fixed-σ Bollinger band breaches — the
excess σ-depth over the bands replaces the mov_rsi percentile axis):
the σ-depth z computed IN-ENGINE on the fetched prices (signal_type
'bollinger' in the DB), thresholds at 2 / 2.5 / 3σ, the breach excess
|z| − k as the depth-response curve's x axis."""
from analysis.analysis_signals.engines.signal_families.boll_std._engine import (  # noqa: E501
    BollStdEngine,
)

__all__ = [
    "BollStdEngine",
]
