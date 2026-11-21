"""Config for analysis.volatility — the vol panel's sec_type setup,
run scope, universes, windows and estimator constants (single source;
the DDL header under database/sql/analysis/volatility.sql mirrors the
definitions).

The pipeline is STRUCTURED for all security sec_types (etf / index /
stock — each mapped to its own stats.*_basic_stats / *_intraday_5min
sources below), but only RUNS for the enabled subset — index for now,
per the study-scope rule. Industry stays out of scope (pool grain, not
a security)."""
from __future__ import annotations

from dataclasses import dataclass


# ---------------------------------------------------------------------------
#  Per-sec_type source mapping — each security sec_type's daily-bar table
#  and 5-minute intraday table (same OHLC shape on all three; the views
#  take the tables as fragment args).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SecTypeSources:
    """One security sec_type's source tables (fragment names passed to
    the pipeline's SQL views)."""

    bars_table: str
    intraday_table: str | None  # None -> the rv leg stays NULL


SEC_TYPE_SOURCES: dict[str, SecTypeSources] = {
    "etf": SecTypeSources(
        bars_table="stats.etf_basic_stats",
        intraday_table="stats.etf_intraday_5min",
    ),
    "index": SecTypeSources(
        bars_table="stats.index_basic_stats",
        intraday_table="stats.index_intraday_5min",
    ),
    "stock": SecTypeSources(
        bars_table="stats.stock_basic_stats",
        intraday_table="stats.stock_intraday_5min",
    ),
}

# The pipeline's run scope — index only for now (oxpicious-dev-conventions
# study scope). Widening = adding sec_type strings here (the universe
# resolver and per-sec_type sources are already wired; industry stays
# out — pool grain, not a security).
ENABLED_SEC_TYPES: tuple[str, ...] = ("index",)


# ---------------------------------------------------------------------------
#  Universes — which codes each sec_type processes.
# ---------------------------------------------------------------------------
# The 10 study indices (the index run scope per the study-scope rule;
# widening = a config change — the table and code are sec_type-ready).
STUDY_INDEX_CODES: tuple[str, ...] = (
    "000001", "931743", "931160", "931071", "000015", "930708",
    "931994", "931594", "000683", "000991",
)


def universe_for(sec_type: str) -> tuple[str, ...] | None:
    """The FIXED universe for a sec_type, or None = data-driven (resolve
    every code with bars in the sec_type's own basic-stats table at run
    time — the etf / stock path via the universe_codes view)."""
    if sec_type == "index":
        return STUDY_INDEX_CODES
    return None


# ---------------------------------------------------------------------------
#  Estimator constants
# ---------------------------------------------------------------------------
# Annualization: the repo-standard 252 trading-day year; *vol* columns
# are daily vol × sqrt(ANN_DAYS) × 100 (percent annualized).
ANN_DAYS = 252

# Close-to-close historical-vol windows (trading ROWS of the daily log
# return — hv_W is defined from row W+1 on).
HV_WINDOWS: tuple[int, ...] = (5, 10, 20, 60, 120, 252)

# Daily-OHLC range estimators + overnight/intraday split: rolling mean
# of the daily variance terms over this many rows.
RANGE_WINDOW = 20

# Yang-Zhang k constant: k = 0.34 / (1.34 + (n+1)/(n-1)), n = window.
YZ_K_NUMERATOR = 0.34

# RiskMetrics daily decay (λ) — EWMA alpha = 1 − λ.
EWMA_LAMBDA = 0.94

# Wilder ATR period (ewm alpha = 1/PERIOD, adjust=False).
ATR_PERIOD = 14

# Bollinger band width window (price-level σ, matching the boll20
# signal family).
BOLL_WINDOW = 20

# vol-of-vol window (rolling std of the hv_20 series) and the
# short/long ratio windows.
VOL_OF_VOL_WINDOW = 20
VOL_RATIO_SHORT = 20
VOL_RATIO_LONG = 252

# Regime gauges: trailing percentile rank and z-score windows
# (house convention — >= 60 obs for the percentile, z moments SHIFTED
# 1 row so today is scored against the past year only).
PCT_WINDOW = 252
MIN_PCT_OBS = 60
Z_WINDOW = 252
MIN_Z_OBS = 60

# 5-minute realized vol: a day needs >= MIN_RV_RETURNS valid intra-
# session returns for rv_5min and >= MIN_BPV_RETURNS consecutive-return
# pairs for the bipower jump ratio (an A-share session yields ~48 bars).
MIN_RV_RETURNS = 1
MIN_BPV_RETURNS = 2
