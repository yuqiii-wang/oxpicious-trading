"""Configuration constants for analyze.fourier_freqs.

Centralizes the table name, FFT window sizes, and source-table mapping
so the pipeline modules share a single source of truth.
"""
from __future__ import annotations

# ---- Target table (analysis schema) --------------------------------------
TABLE_NAME = "analysis.fourier_freqs"
ANALYSIS_NAME = "fourier_freqs"

DESCRIPTION = (
    "Per-(sec_type, code, last_date, range_days) dominant Fourier "
    "frequency of close prices. For each security and trading date, "
    "takes the trailing range_days close prices, detrends (subtracts "
    "mean), applies numpy.rfft, and stores the dominant cycle period "
    "(freq, in trading days), its amplitude (amplitude_close_price, "
    "in yuan), the FULL one-sided amplitude spectrum "
    "(amplitude_spectrum, double-precision array of length "
    "floor(range_days/2), excluding DC), and the periodic-pattern "
    "audit factors per integer day freq (bin-aligned, same length): "
    "count_spectrum = extrema evidence × ACF coherence (recurrence "
    "COUNT factor) and strength_spectrum = (amp/σ_band) × count (the "
    "summarized strength; former consolidated pattern score). "
    "range_days constrained to "
    "(20, 60, 255, 500, 750, 1275). Source: index=index_basic_stats.close, "
    "etf=COALESCE(etf_adjustment.adj_close, etf_basic_stats.close), "
    "stock=stock_basic_stats.close. Built by analyze.fourier_freqs "
    "(truncate-then-recompute per sec_type on --force; incremental "
    "missing-date upsert otherwise); all INSERTs in Python per project "
    "rule."
)

# ---- sec_types ------------------------------------------------------------
SEC_TYPES = ("index", "etf", "stock")

# Identity table per sec_type — used by the recent-data pre-filter
# (fetch_codes_with_recent_data_async) to find codes with at least one
# row in the last RECENT_TRADING_DAYS trading days. A code with no recent
# data (delisted / suspended / never-traded) is excluded from the
# analysis universe entirely so its full history is skipped.
SEC_TYPE_IDENTITY_TABLE = {
    "etf":   "stats.etf_identity",
    "index": "stats.index_identity",
    "stock": "stats.stock_identity",
}

# Close-source table per sec_type (close IS NOT NULL rows define the
# (code, date) universe the FFT windows are built on) — used by the
# incremental missing-target detection in __main__.
SEC_TYPE_CLOSE_TABLE = {
    "etf":   "stats.etf_basic_stats",
    "index": "stats.index_basic_stats",
    "stock": "stats.stock_basic_stats",
}

# ---- FFT window sizes (trading days) --------------------------------------
# Constrained by the SQL CHECK: range_days IN (20, 60, 255, 500, 750, 1275).
#   20   — ~1 trading month (short-term cycles)
#   60   — ~1 trading quarter
#   255  — ~1 trading year
#   500  — ~2 trading years
#   750  — ~3 trading years
#   1275 — ~5 trading years
RANGE_DAYS = (20, 60, 255, 500, 750, 1275)
