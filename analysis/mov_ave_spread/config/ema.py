"""EMA detail-table constants for analysis.mov_ave_spread.

analysis.mov_ave_spreads_detail_ema — the EMA counterpart of the MA detail
table. Internal step of the parent mov_ave_spread pipeline (see
steps/ema.py).

2026-10-26 the TYPICAL EMA rebase (user-directed "in ma-spread Pairs
(Exponential MA) is still using non-typical ema, which is wrong"): the
section's window set mirrored the SMA conventions (6/20/60/120/255) —
per the typical-EMA study the TYPICAL EMA spans are 6 (the A-share
short), 12 and 26 (the classic MACD pair, precomputed in tech_stats
since the same day). The EMA pairs now cover exactly {6, 12, 26}; the
SMA-convention windows stay in the Simple-MA section where they belong.
"""
EMA_DETAIL_TABLE = "analysis.mov_ave_spreads_detail_ema"
EMA_ANALYSIS_NAME = "mov_ave_spread_ema"

# The TYPICAL EMA pair vocabulary: price vs each typical EMA + the
# typical EMAs against each other (the classic 12/26 spread is the
# MACD line's components). (num_col, den_col, gap_column_name) tuples
# in canonical order; num_col="price" is the price sentinel.
# gap_column_name matches the column in
# analysis.mov_ave_spreads_detail_ema.
EMA_PAIRS = [
    ("price", "ema6",  "price_vs_ema6"),
    ("price", "ema12", "price_vs_ema12"),
    ("price", "ema26", "price_vs_ema26"),
    ("ema6",  "ema12", "ema6_vs_ema12"),
    ("ema6",  "ema26", "ema6_vs_ema26"),
    ("ema12", "ema26", "ema12_vs_ema26"),
]

# EMA slope column names. Each is a RAW difference (EMA[t] - EMA[t-1])
# per (sec_type, code) ordered by date, so the NUMERIC(10,6) overflow
# guard applies (same as ma{W}_slope). Windows 6/12 — the two shortest
# legs (every short leg + every long-but-26 leg of the pair set);
# the 26 leg carries no derivatives, like the SMA table's 60/120/255.
EMA_DERIV_WINDOWS = (6, 12)
EMA_SLOPE_COLUMNS = (
    "ema6_slope",
    "ema12_slope",
)

# EMA curvature column names. Each is the 2nd derivative
# (slope[t] - slope[t-1]). Windows 6/12 only.
EMA_CURVATURE_COLUMNS = (
    "ema6_curvature",
    "ema12_curvature",
)

# EMA gap column names (canonical order). Used by steps/ema.py for column
# selection + overflow guard.
EMA_VS_COLUMNS = (
    "price_vs_ema6",
    "price_vs_ema12",
    "price_vs_ema26",
    "ema6_vs_ema12",
    "ema6_vs_ema26",
    "ema12_vs_ema26",
)

# Rolling population σ column names (Bollinger band widths) stored on the
# EMA detail table — one per typical EMA long window {6, 12, 26}, computed
# by the parent pipeline's compute_rolling_stds (σ of price over W days,
# ddof=0) so the table is self-contained for Bollinger rendering without a
# JOIN back to the SMA detail table.
#
# The column NAME keeps the SMA-window convention for the 6 leg:
# std_5days (5-day σ) is the closest available window (the 1-day
# difference vs the EMA6 window is negligible for σ). std_12days /
# std_26days match the EMA windows exactly and exist ONLY on this
# table (the SMA detail table keeps its own 5/20/60/120/255 set).
EMA_STD_COLUMNS = (
    "std_5days",
    "std_12days",
    "std_26days",
)

# The σ windows compute_rolling_stds adds BEYOND the SMA table's
# MA_WINDOWS (5/20/60/120/255) — the typical EMA longs' own windows.
EMA_STD_EXTRA_WINDOWS = (12, 26)
