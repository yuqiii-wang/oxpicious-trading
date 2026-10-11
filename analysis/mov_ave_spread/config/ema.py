"""EMA detail-table constants for analysis.mov_ave_spread.

analysis.mov_ave_spreads_detail_ema — the EMA counterpart of the MA detail
table. Internal step of the parent mov_ave_spread pipeline (see
steps/ema.py).
"""
EMA_DETAIL_TABLE = "analysis.mov_ave_spreads_detail_ema"
EMA_ANALYSIS_NAME = "mov_ave_spread_ema"

# EMA windows for slope (1st derivative) and curvature (2nd derivative).
# Matches the ema{W}_slope / ema{W}_curvature columns in the EMA detail
# table (2026-09: restored for the 6/20-day legs — slope = emaW[t] −
# emaW[t−1], a DIRECT diff of the EMA line, curvature = that slope's
# diff; the price leg's derivatives live on the SMA detail table).
# Source: stats.{etf,index,stock}_tech_stats.ema{6,20,...}.
EMA_DERIV_WINDOWS = (6, 20)

# 9 (num_col, den_col, gap_column_name) tuples in canonical order for the
# EMA detail table. num_col="price" is the price sentinel; num_col="ema6"
# uses ema6. gap_column_name matches the column in
# analysis.mov_ave_spreads_detail_ema.
EMA_PAIRS = [
    ("price", "ema6",   "price_vs_ema6"),
    ("price", "ema20",  "price_vs_ema20"),
    ("price", "ema60",  "price_vs_ema60"),
    ("price", "ema120", "price_vs_ema120"),
    ("price", "ema255", "price_vs_ema255"),
    ("ema6",  "ema20",  "ema6_vs_ema20"),
    ("ema6",  "ema60",  "ema6_vs_ema60"),
    ("ema6",  "ema120", "ema6_vs_ema120"),
    ("ema6",  "ema255", "ema6_vs_ema255"),
]

# EMA slope column names. Each is a RAW difference (EMA[t] - EMA[t-1])
# per (sec_type, code) ordered by date, so the NUMERIC(10,6) overflow
# guard applies (same as ma{W}_slope). Windows 6/20 only.
EMA_SLOPE_COLUMNS = (
    "ema6_slope",
    "ema20_slope",
)

# EMA curvature column names. Each is the 2nd derivative
# (slope[t] - slope[t-1]). Windows 6/20 only.
EMA_CURVATURE_COLUMNS = (
    "ema6_curvature",
    "ema20_curvature",
)

# EMA gap column names (canonical order). Used by steps/ema.py for column
# selection + overflow guard.
EMA_VS_COLUMNS = (
    "price_vs_ema6",
    "price_vs_ema20",
    "price_vs_ema60",
    "price_vs_ema120",
    "price_vs_ema255",
    "ema6_vs_ema20",
    "ema6_vs_ema60",
    "ema6_vs_ema120",
    "ema6_vs_ema255",
)

# Rolling population σ column names (Bollinger band widths) stored on the
# EMA detail table. Mirrors the SMA detail table's std_*days columns — same
# source data (σ of price over W days, ddof=0, computed by
# _common.derive.compute_rolling_stds in the parent pipeline) carried into
# the EMA table so it is self-contained for Bollinger rendering without a
# JOIN back to the SMA detail table.
#
# The column NAME uses the SMA window (5/20/60/120/255) — NOT the EMA window
# (6/20/60/120/255) — to match the SMA detail table's column names. For the
# EMA6 envelope, std_5days (5-day σ) is used as the closest available window
# (the 1-day difference vs the EMA6 window is negligible for σ). For all
# other EMA windows (20/60/120/255) the σ window matches the EMA window
# exactly.
EMA_STD_COLUMNS = (
    "std_5days",
    "std_20days",
    "std_60days",
    "std_120days",
    "std_255days",
)
