"""Configuration constants for analyze.futures.

Centralizes the target table, product→underlying mapping, rolling window
sizes, and column definitions so the pipeline modules share a single
source of truth.
"""
from __future__ import annotations

# ---- Target table (analysis schema) ---------------------------------------
TABLE_NAME = "analysis.futures_ext"
ANALYSIS_NAME = "futures_ext"

DESCRIPTION = (
    "Futures basis and correlation analysis. One row per (date, code) "
    "comparing each CFFEX futures contract against its underlying: index "
    "futures (IC/IF/IH/IM) vs underlying index close; bond futures "
    "(T/TF/TL/TS) vs treasury yield curve converted to a zero-coupon "
    "bond price proxy (100 / (1 + y/2)^(2*tenor_years)). Stores the "
    "basis gap (price + MA5), its 1st-order derivative gap_changing_rate "
    "(negative = basis converging toward underlying, positive = "
    "diverging), and 20-day rolling correlations (price + MA5). Built "
    "by analyze.futures (--force = DELETE + chunked COPY; default = "
    "incremental missing-(date,code) upsert); all INSERTs in Python per "
    "project rule."
)

# ---- Rolling correlation window (trading days) ----------------------------
CORR_WINDOW = 20

# ---- Rolling max-of-gap windows (trading days) ----------------------------
# Two windows: 20-day (monthly) and 60-day (quarterly) basis max.
MAX_GAP_WINDOWS = [20, 60]

# ---- Bond product → (treasury_yield_column, tenor_years) mapping ---------
# Used to convert treasury yield (%) → a zero-coupon bond price proxy:
#   price = 100 / (1 + yield/2)^(2·tenor_years)
# Product codes match builds/futures/config.py.
BOND_PRODUCT_TENOR: dict[str, tuple[str, float]] = {
    "T":  ("cb_10y", 10.0),
    "TF": ("cb_5y",  5.0),
    "TL": ("cb_30y", 30.0),
    "TS": ("cb_2y",  2.0),
}

# ---- Index product → underlying index code mapping ------------------------
INDEX_PRODUCT_UNDERLYING: dict[str, str] = {
    "IC": "000905",
    "IF": "000300",
    "IH": "000016",
    "IM": "000852",
}

# ---- Numeric columns for sanitize_for_db_insert ---------------------------
NUMERIC_COLS = [
    "gap_price_vs_underlying",
    "gap_price_ma5_vs_underlying_ma5",
    "gap_changing_rate_price_vs_underlying",
    "gap_changing_rate_price_ma5_vs_underlying_ma5",
    "corr_price_vs_underlying",
    "corr_price_ma5_vs_underlying_ma5",
    "gap_max_price_vs_underlying_over_20days",
    "gap_max_price_vs_underlying_over_60days",
]
