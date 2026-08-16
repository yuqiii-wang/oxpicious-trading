"""Configuration constants for analyze.margins.

Centralizes the table names, MA windows, and the universe-filter cutoff
so the pipeline modules share a single source of truth.

The within-industry security-pair correlation step (correlations.py)
defines its own WINDOWS / ATTRIBUTION_TYPES locally — it is a
self-contained internal step mirroring analyze.industry_sentiments.
correlations.py. The 'index' weighted-stock margin series it depends on
is provided by the analysis.margin_index_series SQL VIEW.
"""
from __future__ import annotations

# ---- Target tables (analysis schema) -------------------------------------
TABLE_TECH_STATS = "analysis.margin_tech_stats"
TABLE_INDUSTRY_STATS = "analysis.margin_industry_stats"
TABLE_CHANGES = "analysis.margin_changes"
# Kept for reference / future correlation population step. The table is
# currently created empty by 12_margin.sql (population deferred).
TABLE_INDUSTRY_CORRELATION = "analysis.margin_industry_correlation"

# ---- Source tables (stats schema) ----------------------------------------
# Both have the SAME column names: rz_balance, rz_buy (rongzi / 融资 only).
# rq_* (sec borrow) is intentionally excluded per spec.
SRC_TABLE_ETF = "stats.etf_liquidity_margin"
SRC_TABLE_STOCK = "stats.stock_liquidity_margin"

# sec_type values materialized in margin_tech_stats.
SEC_TYPES = ["etf", "stock"]

# ---- Moving-average windows for margin_tech_stats ------------------------
# pandas rolling(W, min_periods=1) per (sec_type, code) — partial mean for
# the first W-1 rows of each code, NOT NULL (mirrors stats.etf_tech_stats
# convention rather than mov_ave_spreads_detail which NULLs until full).
MA_WINDOWS = [5, 20, 60]

# ---- Universe filter -----------------------------------------------------
# Only securities with at least one non-zero rz_balance row in the LAST
# CALENDAR MONTH are materialized. 30 calendar days ≈ 22 trading days —
# wide enough to survive long weekends / holidays, narrow enough to drop
# stale / delisted / suspended names.
UNIVERSE_RECENT_DAYS = 30

# Minimum daily rz_balance (yuan) for an ETF to be included in the analysis
# universe. ETFs with daily margin < this threshold have too little rongzi
# activity to produce meaningful slope / zscore / RSI signals — they add
# noise without information. Applies to ETF sec_type only (stocks typically
# have much larger margin balances). 1,000,000 = 1 million yuan (100万).
MIN_ETF_DAILY_MARGIN_YUAN = 1_000_000

# ---- analysis.analysis_identity registration -----------------------------
ANALYSIS_NAMES = [
    "margin_tech_stats",
    "margin_industry_stats",
    "margin_industry_correlation",
    "margin_changes",
]
