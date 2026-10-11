"""The ohlcv-boost axis vocabulary (family-agnostic market anatomy).

The boost scores a trigger day from THREE stored day-0 axes; this
module owns their frame encodings — closed vocabularies as SMALL
INT codes, never strings in a cudf frame (the label-code contract):

  pattern    analysis.mov_ave_spreads_detail_ohlc_gaps.pattern (TEXT
             in the DB — deliberately NOT a label_dim domain): the
             day's classified OHLC shape. Mapped to int8 PATTERN_CODE
             at the fetch boundary (missing/unknown → AXIS_MISSING).
  vol_state  stats.label_dim 'vol_state' (analysis.mov_ave_price_vs_amt
             stores the SMALLINT code directly): the day's
             trading-amount level state.
  px_speed   stats.label_dim 'px_speed' (same store): the day's
             sigma-normalized price-speed state.

Codes are 0-based and dense; AXIS_MISSING (-1) marks a day the axis
store has no row for (the boost reads it as the NEUTRAL 0 edge).
"""
from __future__ import annotations

from _common.labels import PX_SPEED_CODE, VOL_STATE_CODE

# The ohlc_gaps pattern vocabulary (the store's CHECK constraint order —
# steps/ohlc_gaps.py's np.select precedence list, 'neutral' last).
PATTERN_CODE: dict[str, int] = {
    "spike_fade": 0,        # 冲高回落 (top-decile upper wick)
    "flush_recover": 1,     # 探底回升 (top-decile lower wick)
    "gap_up_advance": 2,    # 高开高走
    "gap_up_decline": 3,    # 高开低走
    "gap_down_advance": 4,  # 低开高走
    "gap_down_decline": 5,  # 低开低走
    "neutral": 6,           # |gap_open| inside the deadband
}

# The frame encoding for "the axis store has no row for this day" —
# never a valid code (the code vocabularies are 0-based dense).
AXIS_MISSING = -1

__all__ = [
    "PATTERN_CODE",
    "VOL_STATE_CODE",
    "PX_SPEED_CODE",
    "AXIS_MISSING",
]
