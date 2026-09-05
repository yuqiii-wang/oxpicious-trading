"""Configuration constants for builds.cross_stats.

Pure constants — no SQL building, no I/O.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
#  Tables
# ---------------------------------------------------------------------------
TABLE = "stats.cross_stats"
DATES_MAP_TABLE = "stats.cross_stats_dates"
# Per-(sec_type, code) rollup the API reads instead of re-aggregating the
# main table per request (~30s live). Maintained by _summary.py — see
# database/sql/stats/15_cross_stats_code_summary.sql.
SUMMARY_TABLE = "stats.cross_stats_code_summary"

# Secondary (sec_type, date) index — NOT in the DDL; post-created by the
# pipeline after the bulk COPY (a live-maintained index costs far more
# during a multi-million-row load than one rebuild at the end).
SEC_TYPE_DATE_INDEX = "idx_cross_stats_sec_type_date"
SEC_TYPE_DATE_INDEX_SQL = (
    f"CREATE INDEX IF NOT EXISTS {SEC_TYPE_DATE_INDEX} "
    f"ON {TABLE} (sec_type, date)"
)

# ---------------------------------------------------------------------------
#  Pair-grain universe (parity with the migrated sec_alloc pipeline)
# ---------------------------------------------------------------------------
# Benchmark selection: keep ALL broad-market indices + top-N highest-traded
# non-broad indices (ranked by aggregate ETF turnover). Bounds the
# subject x benchmark cross product while retaining the most liquid
# sector/industry benchmarks.
TOP_N_NON_BROAD = 3

# Cap on |benchmark_etf_trading_amount / code_etf_trading_amount| mirrored
# from the NUMERIC(10,4) limit (max 999,999.9999). Ratios exceeding this
# cap are set to NULL in BOTH the value and the MA5 computation.
RATIO_CAP = 1_000_000

# Rolling-correlation windows (trading days); materialized ONLY on the
# stride-20 grid. min_periods = max(2N/3, 3) allows up to 1/3 NaN.
CORR_WINDOWS = (20, 60, 255)
COMPUTE_CORR: bool = False  # corr OFF by default (dedicated --corr build)

# ---------------------------------------------------------------------------
#  Industry grain (parity with the migrated broad-market attributions)
# ---------------------------------------------------------------------------
# Session tuning for the big industry INSERT...SELECT (hash aggregate over
# the cross_stats pair rows + liquidity join). Session-scoped.
SET_WORK_MEM_SQL = "SET work_mem = '512MB'"
SET_MAINTENANCE_WORK_MEM_SQL = "SET maintenance_work_mem = '512MB'"

_DESCRIPTION = (
    "Cross-security composition overlap + trading-amount share stats "
    "(stats.cross_stats). PAIR grain (sec_type='index'): per "
    "(code, benchmark_code, date) — code_sec_shared_weight and "
    "benchmark_sec_shared_weight from the LATEST stats.sec_composition "
    "snapshot overlap (stocks held by BOTH subject and benchmark; "
    "zero-overlap pairs explicit (0,0)); ETF-market liquidity "
    "benchmark_etf_trading_amount / code_etf_trading_amount from "
    "stats.index_exts.total_etf_trading_amount (NULL when no ETF tracks "
    "the index), ratio bench/code (+ MA5) with the 1e6 cap; corr_20d/60d/"
    "255d Pearson close correlations on stride-20 grid dates (--corr "
    "build). INDUSTRY grain (sec_type='industry', code=industry_id): "
    "industry union overlap vs broad-market benchmarks — "
    "code_sec_shared_weight = SUM of member indices' pair shared weights "
    "(can exceed 100), benchmark_sec_shared_weight = benchmark weight on "
    "the industry stock UNION (union, no member double-counting), plus "
    "the trading-amount split benchmark_trading_amount / "
    "shared_trading_amount / non_shared_trading_amount (yuan; stock "
    "contributes only with a non-NULL close that date). Temporal "
    "convention: LATEST composition snapshot for ALL dates. Built by "
    "builds.cross_stats (incremental missing-dates / --force / --corr)."
)


def description() -> str:
    return _DESCRIPTION
