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
TABLE_FORCASTS = "analysis.margin_hype_to_price_forcasts"
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

# ---- Source tables for missing-date detection ----------------------------
# Maps each sec_type to the list of tables whose DISTINCT date column
# forms the "expected" set. For etf/stock these are the raw margin tables;
# for index it's the margin_index_series VIEW.
SEC_TYPE_SOURCE_TABLES: dict[str, list[str]] = {
    "etf": [SRC_TABLE_ETF],
    "stock": [SRC_TABLE_STOCK],
    "index": ["analysis.margin_index_series"],
}

# ---- margin_tech_stats: numeric columns for sanitize_for_db_insert ------
# Shared by the index path and _run_sec_type (identical schema).
TECH_STATS_NUMERIC_COLS: list[str] = [
    "margin_balance_ma5", "margin_balance_ma20",
    "margin_balance_ma60", "margin_balance_slope",
    "margin_balance_slope_ma5", "margin_balance_slope_ma20",
    "margin_balance_slope_ma255",
    "margin_balance_slope_std20", "margin_balance_slope_zscore_20d",
    "margin_buy_ma5", "margin_buy_ma20", "margin_buy_ma60",
    "margin_buy_slope",
    "margin_buy_slope_ma5", "margin_buy_slope_ma20",
    "margin_buy_slope_std20", "margin_buy_slope_zscore_20d",
]

# Column order for COPY-insert into margin_tech_stats (matches the table
# schema). Shared by the index path and _run_sec_type.
TECH_STATS_INSERT_COLUMNS: list[str] = [
    "sec_type", "code", "date",
    "margin_balance_ma5", "margin_balance_ma20",
    "margin_balance_ma60", "margin_balance_slope",
    "margin_balance_slope_ma5", "margin_balance_slope_ma20",
    "margin_balance_slope_ma255",
    "margin_balance_slope_std20",
    "margin_balance_slope_zscore_20d",
    "margin_buy_ma5", "margin_buy_ma20",
    "margin_buy_ma60", "margin_buy_slope",
    "margin_buy_slope_ma5", "margin_buy_slope_ma20",
    "margin_buy_slope_std20", "margin_buy_slope_zscore_20d",
]

# ---- margin_industry_stats: insert columns (excludes GENERATED columns) --
INDUSTRY_STATS_INSERT_COLUMNS: list[str] = [
    "date", "industry_id", "industry_label",
    "stock_count", "stock_margin_count",
    "stock_margin_weight_share",
    "etf_count", "etf_margin_count",
    "stock_margin_balance", "etf_margin_balance",
    "stock_margin_buy", "etf_margin_buy",
]
INDUSTRY_STATS_NUMERIC_COLS: list[str] = [
    "stock_count", "stock_margin_count",
    "stock_margin_weight_share",
    "etf_count", "etf_margin_count",
    "stock_margin_balance", "etf_margin_balance",
    "stock_margin_buy", "etf_margin_buy",
]

# ---- analysis.analysis_identity descriptions -----------------------------
TECH_STATS_DESCRIPTION: str = (
    "Per-(sec_type, code, date) technical indicators on RONGZI (融资 / "
    "cash-borrow) margin flows. sec_type ∈ {etf, stock}. Source: "
    "stats.etf_liquidity_margin / stats.stock_liquidity_margin. RONQIN "
    "(融券 / sec borrow) EXCLUDED. Two series: margin_balance (rz_balance, "
    "yuan, STOCK), margin_buy (rz_buy, yuan, FLOW). For each: ma5/ma20/ma60 "
    "(pandas rolling(W, min_periods=1) per code — partial mean for first "
    "W-1 rows, NOT NULL) and slope ((X[t]-X[t-1])/X[t-1], NULL on first "
    "date or X[t-1] <= 0). Universe filter: only securities with non-zero "
    "rz_balance in last calendar month. Built by analyze.margins "
    "(truncate-then-recompute); all INSERTs in Python per project rule."
)

INDUSTRY_STATS_DESCRIPTION: str = (
    "Per-(date, industry_id) SUM aggregation of stock AND ETF RONGZI (融资) "
    "margin flows. Stock->industry via sec_classification(type=stock, "
    "parent_index_is_primary=TRUE). ETF->industry via two-hop: "
    "etf.parent_index_code->index.industry_id. RONQIN (融券) EXCLUDED. "
    "Stock and ETF components stored SEPARATELY; total_margin_* columns "
    "GENERATED ALWAYS AS (stock + etf) STORED. *_margin_count + "
    "*_margin_count_share + *_margin_weight_share expose active-rongzi "
    "ratio per industry. margin_balance=SUM(rz_balance, yuan), "
    "margin_buy=SUM(rz_buy, yuan). Universe filter: only securities with "
    "non-zero rz_balance in last calendar month. Built by analyze.margins "
    "(truncate-then-recompute); all INSERTs in Python per project rule."
)

# ---- analysis.analysis_identity registration -----------------------------
ANALYSIS_NAMES = [
    "margin_tech_stats",
    "margin_industry_stats",
    "margin_industry_correlation",
    "margin_changes",
    "margin_hype_to_price_forcasts",
]
