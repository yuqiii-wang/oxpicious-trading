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
    "diverging), 20-day rolling correlations (price + MA5), rolling "
    "gap maximums (20/60-day), and a rolling AR(1) of the basis "
    "(slope + implied half-life — slope below 1 means the basis "
    "mean-reverts toward the underlying, the contrarian convergence "
    "property). Built by analyze.futures (--force = DELETE + chunked "
    "COPY; default = incremental missing-(date,code) upsert); all "
    "INSERTs in Python per project rule."
)

# ---- Rolling correlation window (trading days) ----------------------------
CORR_WINDOW = 20

# ---- Rolling max-of-gap windows (trading days) ----------------------------
# Two windows: 20-day (monthly) and 60-day (quarterly) basis max.
MAX_GAP_WINDOWS = [20, 60]

# ---- Rolling AR(1) window (trading days) ----------------------------------
# Window for the rolling AR(1) regression of the basis:
#   gap_t = a + b * gap_{t-1}
# b < 1 means the basis mean-reverts toward the underlying (contrarian
# convergence); the implied half-life is ln(0.5) / ln(b). A longish
# window (60d ≈ quarterly) keeps the slope estimate stable — a 20-day
# AR(1) slope is too noisy to be usable.
AR1_WINDOW = 60

# ---- Basis-convergence quintile summary table ------------------------------
# Small per-run snapshot: mean next-horizon gap change by gap quintile
# per contract_type, quantifying the contrarian convergence (extreme
# premium quintiles should show NEGATIVE forward change — the basis
# narrows — and extreme discount quintiles POSITIVE change). One row
# per (asof_date, contract_type, horizon_days, quintile); asof_date
# stamps the run, so consecutive runs keep a history of snapshots.
QUINTILE_TABLE_NAME = "analysis.futures_gap_quintiles"
QUINTILE_ANALYSIS_NAME = "futures_gap_quintiles"

# Forward horizons (trading days) for the quintile conditional stats.
QUINTILE_HORIZONS = [5, 20]

# Number of gap quantile buckets (1 = lowest gap, 5 = highest).
QUINTILE_N_BUCKETS = 5

QUINTILE_DESCRIPTION = (
    "Basis-convergence quintile summary for analyze.futures. One row "
    "per (asof_date, contract_type, horizon_days, quintile): the "
    "pooled gap_price_vs_underlying distribution across all contracts "
    "of a contract_type (index / bond) is split into 5 quintiles, and "
    "each quintile's mean gap (bps) and mean gap change over the next "
    "horizon_days trading days (bps) are recorded. Quantifies the "
    "contrarian convergence property: high-quintile (premium) gaps "
    "should be followed by NEGATIVE forward change (basis narrowing "
    "toward the underlying) and low-quintile (discount) gaps by "
    "POSITIVE change. Observations without a complete forward window "
    "(the last horizon_days trading days of the sample) are excluded; "
    "asof_date stamps each pipeline run, so the table accumulates a "
    "snapshot history. Rebuilt (upsert on the natural PK) by "
    "analyze.futures on every run; all INSERTs in Python per project "
    "rule."
)

QUINTILE_NUMERIC_COLS = [
    "n_obs",
    "mean_gap_bps",
    "mean_fwd_chg_bps",
]

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
    "gap_ar1_slope_over_60days",
    "gap_half_life_over_60days",
]
