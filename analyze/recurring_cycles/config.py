"""Configuration constants for analyze.recurring_cycles.

Centralizes the table name, window sizes, and source-table mapping so the
pipeline modules share a single source of truth.
"""
from __future__ import annotations

# ---- Target table (analysis schema) --------------------------------------
TABLE_NAME = "analysis.recurring_cycles"
ANALYSIS_NAME = "recurring_cycles"

DESCRIPTION = (
    "Per-(sec_type, code, last_date, range_days) RECURRING rise/drop "
    "periodicity of close prices. For each security and trading date, "
    "takes the trailing range_days close prices and audits every integer "
    "cycle period d (trading days, 2..range_days/2) for RECURRENCE — "
    "price actually cycling up-and-down with spacing d — via two "
    "time-domain factors: count(d) = prominence-filtered alternating-"
    "extrema evidence (swings within ±15% of d over the max possible "
    "cycles) × MA-detrended ACF coherence (multiples m·d with biased "
    "acf ≥ 1.96/√N); and strength(d) = (amp(d)/σ_band) × count(d) where "
    "amp(d) is the energy-merged FFT amplitude of the day (Fourier "
    "reference) and σ_band the swing-band σ. The stored headline "
    "period_days = argmax of strength (0 = no recurring period detected "
    "— a one-off swing or trend gets count 0 and is rejected), with the "
    "per-day amplitude/count/strength spectra (arrays indexed by day − 2, "
    "length floor(range_days/2) − 1) stored for the bar charts. FFT is "
    "used ONLY as the amplitude reference and to compute the ACF "
    "(Wiener–Khinchin); recurrence itself is measured in the time "
    "domain. POISSON AUDIT (added 2026-09): the raw extrema-pool hit "
    "count hits(d) is tested against the chance expectation λ̂₀(d) of a "
    "point-process null, empirically calibrated per (N, d, pool size) on "
    "random-walk + stochastic-vol nulls (poisson_calibration.json, "
    "regenerate via temp_scripts/gen_rc_poisson_calibration.py); "
    "significance(d) = −log10 of the Bonferroni p-value "
    "P(Poisson(λ̂₀) ≥ hits) over the auditable days (d ≤ N/3) — 0 = not "
    "significant, ≥ 1.30 ⇔ p < 0.05 (validated FPR ≤ 5% on nulls, power "
    "99%+ on synthetic 20d cycles); evidence_ratio = hits(d*)/λ̂₀(d*) at "
    "the headline period. significance_spectrum, hits_spectrum (observed "
    "swing-hit count) and lam0_spectrum (chance expectation λ̂₀(d)) "
    "stored day-aligned like the other spectra — the raw observed/"
    "expected pair behind the audit. range_days constrained to (20, 60, "
    "255, 500, "
    "750, 1275). Source: index=index_basic_stats.close, "
    "etf=COALESCE(etf_adjustment.adj_close, etf_basic_stats.close), "
    "stock=stock_basic_stats.close. Built by analyze.recurring_cycles "
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
# (code, date) universe the windows are built on) — used by the
# incremental missing-target detection in __main__.
SEC_TYPE_CLOSE_TABLE = {
    "etf":   "stats.etf_basic_stats",
    "index": "stats.index_basic_stats",
    "stock": "stats.stock_basic_stats",
}

# ---- Window sizes (trading days) ------------------------------------------
# Constrained by the SQL CHECK: range_days IN (20, 60, 255, 500, 750, 1275).
#   20   — ~1 trading month (short-term cycles)
#   60   — ~1 trading quarter
#   255  — ~1 trading year
#   500  — ~2 trading years
#   750  — ~3 trading years
#   1275 — ~5 trading years
RANGE_DAYS = (20, 60, 255, 500, 750, 1275)
