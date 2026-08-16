"""Configuration constants for analyze.intraday_industry_sentiments.

Intraday Industry Sentiments — per-5-min-tick % change vs previous trading
day's close, decomposed to the industry (parent) and individual-index (child)
levels.

Two tables (parent → child strict composite FK):

  analysis.intraday_industry_market_movements   (PARENT)
    PK: (industry_id, tick_ts, benchmark_code)
    benchmark_price_pct_relative_prev_date_close
      = benchmark.close / prev_day_close - 1
    industry_price_pct_relative_prev_date_close
      = mean of member indices' code_price_pct across this industry for
        this benchmark at this tick (simple mean, not weighted)

  analysis.intraday_index_market_movements       (CHILD)
    PK: (code, tick_ts, sec_type, benchmark_code)
    FK: (industry_id, tick_ts, benchmark_code) → parent
    code_price_pct_relative_prev_date_close
      = member_index.close / prev_day_close - 1

Sources:
  stats.index_intraday_5min          — 5-min OHLC bars per (date, code, time)
  stats.index_basic_stats            — prev_day_close (latest close < tick date)
  stats.sec_classification           — industry_id, is_industry_not_strategy
  analysis.sec_alloc_perf_attribution — member indices per benchmark (latest
                                        snapshot per benchmark; member is
                                        active if code_sec_shared_weight > 0)
"""
from typing import Final

# Parent table (industry aggregate).
INDUSTRY_TABLE: Final[str] = "analysis.intraday_industry_market_movements"

# Child table (individual index move).
INDEX_TABLE: Final[str] = "analysis.intraday_index_market_movements"

# Registration metadata for analysis.analysis_identity.
ANALYSIS_NAME: Final[str] = "intraday_industry_sentiments"
ANALYSIS_DESCRIPTION: Final[str] = (
    "Intraday 5-min-tick % change vs previous trading day's close, "
    "decomposed to the industry (mean of member indices' code_price_pct) "
    "and individual-index levels. Parent table "
    "analysis.intraday_industry_market_movements: one row per "
    "(industry_id, tick_ts, benchmark_code) with benchmark_price_pct and "
    "industry_price_pct. Child table "
    "analysis.intraday_index_market_movements: one row per "
    "(code, tick_ts, sec_type, benchmark_code) with code_price_pct, FK to "
    "parent via (industry_id, tick_ts, benchmark_code). Sources: "
    "stats.index_intraday_5min (5-min bars), stats.index_basic_stats "
    "(prev_day_close), stats.sec_classification (industry_id mapping), "
    "analysis.sec_alloc_perf_attribution (member indices per benchmark). "
    "Built by analyze.intraday_industry_sentiments (incremental: missing "
    "(benchmark_code, tick_date) pairs only; force: full recompute)."
)

# sec_type currently supported. The schema reserves 'stock' / 'etf' for
# future expansion; only index intraday 5-min bars are populated today.
SUPPORTED_SEC_TYPES: Final[tuple[str, ...]] = ("index",)

# Broad-market industry_ids excluded from the per-industry shades on the
# UI (they ARE in the table for completeness, but the UI filters them out
# to keep the chart legible — broad-market benchmarks like 上证指数 /
# 沪深300 don't belong as "industries" in the HYPE/DRAIN view).
BROAD_EXCLUDED: Final[tuple[str, ...]] = (
    "BROAD_CSI", "BROAD_SSE", "BROAD_SZSE", "BROAD_STAR", "BROAD",
)
