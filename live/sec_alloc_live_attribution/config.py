"""Configuration constants for live.sec_alloc_live_attribution.

Live Allocation Attribution — per-5-min-tick member % change vs the
previous trading day's close, weighted by the PREVIOUS trading day's
trading amount (liquidity weight).

Two tables, split by computation weight (heavy ref built ONCE per date;
light ticks appended every 5-min run):

  live.sec_alloc_live_prev_ref     (HEAVY / once per date — ref.py)
    PK: (benchmark_code, date, code, sec_type)
    Stores prev_date, prev-day closes (member + benchmark), prev-day
    trading amount, and the normalized trading-amount market-share weight
    (code_trading_amount_weight, Σ = 1 per benchmark+date).

  live.sec_alloc_live_attribution  (LIGHT / per 5-min tick — ticks.py)
    PK: (code, date, time, sec_type, benchmark_code)
    FK: (benchmark_code, date, code, sec_type) → prev_ref
    Stores per-tick member pct + benchmark pct vs prev-day close and a
    GENERATED diff. The UI "by trading amt / without" toggle switches
    between SUM(ref.weight * tick.pct) and AVG(tick.pct) at QUERY TIME.

Sources:
  analysis.sec_alloc_perf_attribution — member universe per benchmark
  stats.index_basic_stats             — prev-day close + trading_amount
  stats.index_intraday_5min           — 5-min tick closes
  stats.sec_classification            — industry_id mapping
"""
from typing import Final

# Heavy static reference table (built once per (benchmark, date)).
REF_TABLE: Final[str] = "live.sec_alloc_live_prev_ref"

# Light per-5-min tick table (child, strict FK to REF_TABLE).
TICK_TABLE: Final[str] = "live.sec_alloc_live_attribution"

# Registration metadata for live.live_identity.
PIPELINE_NAME: Final[str] = "sec_alloc_live_attribution"
PIPELINE_DESCRIPTION: Final[str] = (
    "Live per-5-min-tick member attribution under the live schema. Heavy "
    "prev-date reference (prev-day closes, prev-day trading amounts, "
    "normalized trading-amount market-share weights) is built ONCE per "
    "(benchmark, date) into live.sec_alloc_live_prev_ref and skipped on "
    "subsequent runs of the same date. Light per-tick rows (member + "
    "benchmark % vs prev-day close, GENERATED diff) are appended "
    "incrementally into live.sec_alloc_live_attribution every run "
    "(designed to be triggered every 5 min during trading hours by the "
    "Market Movements UI). Industry-level weighted (SUM weight*pct) and "
    "equal-weighted (AVG pct) aggregates are computed at query time. "
    "Sources: analysis.sec_alloc_perf_attribution (member universe), "
    "stats.index_basic_stats (prev-day close + trading_amount), "
    "stats.index_intraday_5min (tick closes), stats.sec_classification "
    "(industry tags)."
)

# sec_type currently supported for BENCHMARK scope (mirrors the intraday
# pipelines).
SUPPORTED_SEC_TYPES: Final[tuple[str, ...]] = ("index",)

# LIVE TICK SCOPE: stats.sec_classification.type values eligible for live
# tick rows. 'industry' members are indexes carrying an industry_id, so the
# effective set is ('index', 'etf'). STOCKS never get tick rows — they are
# kept in the REF table for share-weight purposes only.
TICK_CLASS_TYPES: Final[tuple[str, ...]] = ("index", "etf")

# PG advisory-lock keys for single-instance coordination — ONE PER PROCESS
# (the pipeline is split into two independent processes):
#
#   • ADVISORY_LOCK_KEY (LIVE ticks process, --mode live): the 5-min
#     auto-refresh equal-weight path. A second concurrent instance simply
#     SKIPS (exits fast) — the next 5-min run catches up.
#   • REF_ADVISORY_LOCK_KEY (YDAY REF process, --mode ref): the heavy
#     once-per-date prev-day reference build + weighted tick upgrades,
#     triggered manually from the Market Movements UI button. Waits
#     (bounded) for the lock instead of skipping.
ADVISORY_LOCK_KEY: Final[int] = 482311001  # arbitrary stable constant
REF_ADVISORY_LOCK_KEY: Final[int] = 482311002  # arbitrary stable constant

# Broad-market industry_ids excluded from the member universe (they are
# benchmarks, not industries — same list as intraday_industry_sentiments).
BROAD_EXCLUDED: Final[tuple[str, ...]] = (
    "BROAD_CSI", "BROAD_SSE", "BROAD_SZSE", "BROAD_STAR", "BROAD",
)
