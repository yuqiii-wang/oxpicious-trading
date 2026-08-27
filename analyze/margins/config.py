"""Configuration constants for analyze.margins.

Centralizes the table names and the universe-filter cutoff so the
pipeline modules share a single source of truth.

Cleaned up in the margin reduction: margin_industry_correlation was DROPPED;
margin_tech_stats / margin_industry_stats / margin_changes schemas were
reduced to the columns that margin-trends detection + UI actually consume.
"""
from __future__ import annotations

# ---- Target tables (analysis schema) -------------------------------------
TABLE_TECH_STATS = "analysis.margin_tech_stats"
TABLE_INDUSTRY_STATS = "analysis.margin_industry_stats"
TABLE_INDEX_SERIES = "analysis.margin_index_series"
TABLE_CHANGES = "analysis.margin_changes"

# ---- Source tables (stats schema) ----------------------------------------
# Both have the SAME column names: rz_balance, rz_buy (rongzi / 融资 only).
# rq_* (sec borrow) is intentionally excluded per spec.
SRC_TABLE_ETF = "stats.etf_liquidity_margin"
SRC_TABLE_STOCK = "stats.stock_liquidity_margin"

# sec_type values materialized in margin_tech_stats.
SEC_TYPES = ["etf", "stock"]

# ---- Universe filter -----------------------------------------------------
# Only securities with at least one non-zero rz_balance row in the LAST
# CALENDAR MONTH are materialized. 30 calendar days ≈ 22 trading days —
# wide enough to survive long weekends / holidays, narrow enough to drop
# stale / delisted / suspended names.
UNIVERSE_RECENT_DAYS = 30

# Minimum daily rz_balance (yuan) for an ETF to be included in the analysis
# universe. ETFs with daily margin < this threshold have too little rongzi
# activity to produce meaningful slope / zscore signals — they add
# noise without information. Applies to ETF sec_type only (stocks typically
# have much larger margin balances). 1,000,000 = 1 million yuan (100万).
MIN_ETF_DAILY_MARGIN_YUAN = 1_000_000

# ---- Source tables for missing-date detection ----------------------------
# Maps each sec_type to the list of tables whose DISTINCT date column
# forms the "expected" set. For etf/stock these are the raw margin tables;
# for index it's the margin_index_series TABLE (built by Python
# vectorization — see build_margin_index_series).
SEC_TYPE_SOURCE_TABLES: dict[str, list[str]] = {
    "etf": [SRC_TABLE_ETF],
    "stock": [SRC_TABLE_STOCK],
    "index": [TABLE_INDEX_SERIES],
}

# ---- margin_tech_stats: numeric columns for sanitize_for_db_insert ------
# REDUCED to the two regime-detection columns consumed by the
# margin_changes trend episode detection.
TECH_STATS_NUMERIC_COLS: list[str] = [
    "margin_balance_slope_ma5",
    "margin_balance_slope_zscore_20d",
]

# Column order for COPY-insert into margin_tech_stats (matches the table
# schema). Shared by the index path and _run_sec_type.
TECH_STATS_INSERT_COLUMNS: list[str] = [
    "sec_type", "code", "date",
    "margin_balance_slope_ma5",
    "margin_balance_slope_zscore_20d",
]

# ---- margin_industry_stats: insert columns (reduced — sums only) --------
INDUSTRY_STATS_INSERT_COLUMNS: list[str] = [
    "date", "industry_id", "industry_label",
    "stock_margin_balance", "etf_margin_balance",
    "stock_margin_buy", "etf_margin_buy",
]
INDUSTRY_STATS_NUMERIC_COLS: list[str] = [
    "stock_margin_balance", "etf_margin_balance",
    "stock_margin_buy", "etf_margin_buy",
]

# ---- margin_index_series: insert columns (Python-vectorized build) -------
INDEX_SERIES_INSERT_COLUMNS: list[str] = [
    "index_code", "industry_id", "date",
    "index_margin_balance", "index_margin_buy",
    "n_constituents", "n_with_balance",
]
INDEX_SERIES_NUMERIC_COLS: list[str] = [
    "index_margin_balance", "index_margin_buy",
    "n_constituents", "n_with_balance",
]

# ---- analysis.analysis_identity descriptions -----------------------------
TECH_STATS_DESCRIPTION: str = (
    "Per-(sec_type, code, date) regime-detection input for "
    "analysis.margin_changes trend detection: margin_balance_slope_ma5 "
    "(segmentation signal) + margin_balance_slope_zscore_20d "
    "(significance filter). sec_type ∈ {etf, stock, index} ('index' "
    "aggregated from analysis.margin_index_series TABLE). RONGZI only. "
    "Built by analyze.margins (truncate-then-recompute); all INSERTs in "
    "Python per project rule."
)

INDEX_SERIES_DESCRIPTION: str = (
    "Per-(index_code, date) weighted-average RONGZI (融资) margin series "
    "MATERIALIZED as a table (was a VIEW — aggregation moved to Python "
    "vectorization). Branch 1 (stock-based): Σ(rz_* × "
    "parent_index_weight) / Σ(parent_index_weight) over stock "
    "constituents. Branch 2 (ETF-proxy): weighted-average over TRACKING "
    "ETFs for indices with NO stock constituents (weight COALESCE 1.0). "
    "Source of the 'index' attribution series for the Margin Trends page "
    "+ sec_type='index' histories for margin_changes detection. RONQIN "
    "EXCLUDED. Built by analyze.margins (truncate-then-recompute); all "
    "INSERTs in Python per project rule."
)

INDUSTRY_STATS_DESCRIPTION: str = (
    "Per-(date, industry_id) SUM aggregation of stock AND ETF RONGZI (融资) "
    "margin flows: stock/etf margin_balance + margin_buy. Drives the "
    "margin-trends themes industry universe. RONGZI only. Built by "
    "analyze.margins (truncate-then-recompute); all INSERTs in Python per "
    "project rule."
)

# ---- analysis.analysis_identity registration -----------------------------
ANALYSIS_NAMES = [
    "margin_tech_stats",
    "margin_industry_stats",
    "margin_index_series",
    "margin_changes",
]
