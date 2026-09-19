"""Configuration constants for live.sec_alloc_live_attribution.

Live Allocation Attribution — per-5-min-tick member % change vs the
PREVIOUS trading day's close, weighted by the PREVIOUS trading day's
trading amount (liquidity weight).

Single light table, appended every 5-min run:

  live.sec_alloc_live_attribution
    PK: (code, date, time, sec_type, benchmark_code)
    Stores per-tick member pct + benchmark pct vs prev-day close and a
    GENERATED diff. ``is_without_trading_amt`` marks the prev-close basis:
    TRUE = fallback row (prev-day last 5-min bar close, written by the
    5-min LIVE pass so equal-weighted data flows immediately); FALSE =
    daily-close-basis row (prev-day official close, written/upgraded by
    the yday-ref mode). The UI "by trading amt / without" toggle computes
    SUM(weight * pct) vs AVG(pct) AT QUERY TIME.

  The former heavy reference table live.sec_alloc_live_prev_ref was
  CONSOLIDATED AWAY (2026-09-08): its values are derivable from the base
  tables — prev-day closes/trading amounts from stats.index_basic_stats
  (computed at tick time by the weighted pass), composition-overlap
  weights from stats.cross_stats (computed at read time by the API
  service).

Storage model (retention + on-demand backfill):
  The table is a ROLLING CACHE, not an archive. Only the newest
  RETENTION_DATES trading dates are kept; older dates are pruned. When a
  consumer requests a date with no tick rows (selected on the UI date
  picker), the API service invokes ``--mode compute --date D`` which
  backfills that date from the base tables (weighted basis, fallback
  fill) — bounded by the RAW intraday table's own retention
  (stats.index_intraday_5min), NOT by this table's window.

Sources:
  stats.sec_classification            — member universe + industry_id mapping
  stats.index_basic_stats             — prev-day close + trading_amount
  stats.index_intraday_5min           — 5-min tick closes
"""
from typing import Final

# Light per-5-min tick table.
TICK_TABLE: Final[str] = "live.sec_alloc_live_attribution"

# Registration metadata for live.live_identity.
PIPELINE_NAME: Final[str] = "sec_alloc_live_attribution"

# live.live_identity row name used as the once-per-trading-day guard for
# the retention prune in the 5-min LIVE mode (a plain PK probe — the
# prune's unindexed date scan must not run on every tick). The row's
# last_run_datetime::date is compared against the latest intraday date:
# a NEW trading day triggers exactly one prune.
PRUNE_IDENTITY_NAME: Final[str] = "sec_alloc_live_attribution_prune"
PIPELINE_DESCRIPTION: Final[str] = (
    "Live per-5-min-tick member attribution under the live schema. Light "
    "per-tick rows (member + benchmark % vs prev-day close, GENERATED "
    "diff) are appended incrementally into "
    "live.sec_alloc_live_attribution every run (designed to be triggered "
    "every 5 min during trading hours by the Market Movements UI). "
    "is_without_trading_amt marks the prev-close basis: TRUE = fallback "
    "(prev-day last 5-min bar close, equal-weight only), FALSE = "
    "daily-close basis (upgraded in place by the yday-ref mode's weighted "
    "pass, which computes prev closes from stats.index_basic_stats at "
    "tick time — the former heavy live.sec_alloc_live_prev_ref table was "
    "consolidated away; trading-amount weights and composition-overlap "
    "shared weights are computed at READ time from stats.index_basic_stats "
    "and stats.cross_stats). Industry-level weighted (SUM weight*pct, "
    "renormalized) and equal-weighted (AVG pct) aggregates are computed "
    "at query time. A retention prune (ref/all modes) keeps only the "
    "newest 5 trading dates. "
    "Sources: stats.sec_classification (member universe), "
    "stats.index_basic_stats (prev-day close + trading_amount), "
    "stats.index_intraday_5min (tick closes)."
)

# sec_type currently supported for BENCHMARK scope (mirrors the intraday
# pipelines).
SUPPORTED_SEC_TYPES: Final[tuple[str, ...]] = ("index",)

# Retention window in TRADING DATES: rows older than the Nth-newest
# distinct intraday date are deleted from the tick table by the prune
# step. The prune runs in ref/all modes (once per run) and in the 5-min
# LIVE mode guarded to once per NEW trading day via the PRUNE_IDENTITY_NAME
# live_identity row (so the unindexed date scan never runs on every tick).
# Dates that fall out of the window are NOT lost: the API service
# re-backfills any selected date on demand via ``--mode compute`` (bounded
# by the raw intraday table's own retention).
RETENTION_DATES: Final[int] = 20

# BENCHMARK UNIVERSE — curated broad-market tags only. The Market
# Movements page (the only consumer) resolves its benchmark dropdown from
# stats.sec_index_tags WHERE industry_id = 'benchmark_broadmarket'; before
# this filter the pipeline computed the FULL (code x tick-bearing index)
# cross product — 492 benchmarks × ~460 members × ~65 ticks/day ≈ 12M
# rows/day, ~99% of it never read. Scope every pair finder to the same
# tag set so ticks, refs, and the UI agree.
CURATED_BENCHMARK_FILTER: Final[str] = (
    "i5.code IN (SELECT t.code FROM stats.sec_index_tags t "
    "WHERE t.industry_id = 'benchmark_broadmarket')"
)

# LIVE TICK SCOPE: stats.sec_classification.type values eligible for live
# tick rows. 'industry' members are indexes carrying an industry_id, so the
# effective set is ('index', 'etf'). STOCKS never get tick rows.
TICK_CLASS_TYPES: Final[tuple[str, ...]] = ("index", "etf")

# PG advisory-lock keys for single-instance coordination — ONE PER PROCESS
# (the pipeline is split into three independent processes):
#
#   • ADVISORY_LOCK_KEY (LIVE ticks process, --mode live): the 5-min
#     auto-refresh equal-weight path. A second concurrent instance simply
#     SKIPS (exits fast) — the next 5-min run catches up.
#   • REF_ADVISORY_LOCK_KEY (YDAY REF process, --mode ref): the once-per-day
#     daily-close-basis weighted tick upgrades, triggered manually from the
#     Market Movements UI button. Waits (bounded) for the lock instead of
#     skipping.
#   • COMPUTE_ADVISORY_LOCK_KEY (--mode compute): the on-demand backfill of
#     a SELECTED date, invoked by the API service when a requested date has
#     no tick rows. Bounded wait (the work is idempotent — aborting a
#     duplicate run is safe; the request-path tag dedupe usually prevents
#     the race entirely).
ADVISORY_LOCK_KEY: Final[int] = 482311001  # arbitrary stable constant
REF_ADVISORY_LOCK_KEY: Final[int] = 482311002  # arbitrary stable constant
COMPUTE_ADVISORY_LOCK_KEY: Final[int] = 482311003  # arbitrary stable constant

# Broad-market industry_ids excluded from the member universe (they are
# benchmarks, not industries).
BROAD_EXCLUDED: Final[tuple[str, ...]] = (
    "BROAD_CSI", "BROAD_SSE", "BROAD_SZSE", "BROAD_STAR", "BROAD",
)
