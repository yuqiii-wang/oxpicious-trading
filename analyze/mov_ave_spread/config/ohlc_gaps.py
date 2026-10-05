"""OHLC-gaps table constants for analyze.mov_ave_spread.

analysis.mov_ave_spreads_detail_ohlc_gaps — daily intraday OHLC gap
metrics (高开低走/低开高走/冲高回落/探底回升 feature set of the 2026-09
OHLC studies). Internal step of the parent mov_ave_spread pipeline (see
steps/ohlc_gaps.py).
"""
OHLC_GAPS_TABLE = "analysis.mov_ave_spreads_detail_ohlc_gaps"
OHLC_GAPS_ANALYSIS_NAME = "mov_ave_spread_ohlc_gaps"

# 8 raw daily gap columns + the 2026-09-27 PATTERN block, in table order
# (signed fractions; the flags/pattern semantics in
# database/sql/analysis/mov_ave_spreads/09_mov_ave_spreads_detail_ohlc_gaps.sql).
# prev_close = previous VALID (non-estimated) close per (sec_type, code).
# The pattern block: uw_top10 / lw_top10 = the wick's trailing-252d
# top-decile flags (study_ohlc_patterns' rolling rank(pct=True) >= 0.90,
# min 120 obs); pattern = the classified day pattern (precedence:
# spike_fade → flush_recover → the gap×candle quadrants at the ±0.1%
# deadband → neutral; NULL = unknown anatomy). Computed on UNROUNDED
# values in steps/ohlc_gaps.compute_ohlc_gaps.
OHLC_GAPS_COLUMNS = (
    "gap_open",
    "intra",
    "uw",
    "lw",
    "rng",
    "up_gap",
    "dn_gap",
    "close_pos",
    "uw_top10",
    "lw_top10",
    "pattern",
)

# Estimated-row flag carried by the parent fetch (fetch/source.py) so the
# OHLC-gaps step can exclude synthetic-OHLC source rows entirely.
OHLC_GAPS_EXCLUDE_FLAG_COLUMNS = ("is_ohl_estimated",)
