"""Configuration for analysis.pair_pct_change_ratios."""
from __future__ import annotations

from _common.labels import PAIR_GRAIN_CODE

# ---------------------------------------------------------------------------
#  Table / identity
# ---------------------------------------------------------------------------
TABLE = "analysis.pair_pct_change_ratios"
ANALYSIS_NAME = "pair_pct_change_ratios"

# Mirrors the identity description in
# database/sql/analysis/27_pair_pct_change_ratios.sql.
ANALYSIS_DESCRIPTION = (
    "Industry x industry HEDGE RATIOS at two modes (grain 1 only, "
    "static.label_dim pair_grain). THREE CODES per row: code = the "
    "subject industry (the hedging instrument), benchmark_code = the "
    "exposure industry (the position), benchmark_market = the "
    "broad-market index whose pct change is subtracted in the offset "
    "mode. The stored value is the MINIMUM-VARIANCE hedge ratio "
    "h* = −Cov(A, B) / Var(A) over trailing 5/20/60/255-day windows "
    "— how many units of the subject to hold per unit long of the "
    "benchmark industry so the combined position's variance is "
    "minimal; h* > 0 (negative covariance — the opposite-moves "
    "regime) = BUY that many units of the subject, h* < 0 = short it. "
    "hedge_raw_ma{N}d: A and B are the industries' aggregate daily pct "
    "changes (ind_pct = null-skipped mean over member indexes' "
    "OWN-calendar pcts). hedge_offset_ma{N}d: A' = A − market pct, "
    "B' = B − market pct (excess over the benchmark market index; the "
    "market factor cancels — e.g. 科技 vs 红利 raw corr +0.42 but "
    "−0.49 vs 000300, −0.70 vs 000001, so the offset mode is where "
    "the buy-to-hedge answer lives). Windows are corr-parity POOLED "
    "CALENDAR (the offsets convention): each mode's trailing N COMMON "
    "trading days (raw: dates where both industries have aggregates; "
    "offset: both plus the market print) — a date only one side "
    "trades is not a day of the pair's calendar, so the live frontier "
    "sample is always a full window; a pair with fewer than "
    "ceil(0.9·N) complete days (young/sparse pairs — the >10% null "
    "rule) emits NULL. Var(A) at/below 1e-16 emits NULL "
    "(near-motionless instrument); |h*| > 9999.999999 dropped "
    "backstop). Materialized on stride-grid dates of the global "
    "index calendar (grid position % interval = interval − 1; default "
    "20, PK member so strides coexist; self-industry pairs excluded) "
    "PLUS one mutable off-grid LIVE sample at the calendar frontier "
    "whenever it is past the last grid date (the offsets table's "
    "convention — the freshest windows plot at the frontier so the "
    "chart's right edge is never up to a stride-interval stale; each "
    "incremental run refreshes it and purges the previous frontier's "
    "leftovers). "
    "Subjects = exposure industries = ALL industries with active "
    "type='index' members carrying closes. Source: "
    "stats.index_basic_stats closes + stats.sec_classification. Built "
    "by python -m analysis.pair_pct_change_ratios (incremental / "
    "--force / --stride / --code filtered by industry_id)."
)

# ---------------------------------------------------------------------------
#  Grid + windows
# ---------------------------------------------------------------------------
# Stride (trading days) between consecutive GRID dates on the GLOBAL index
# calendar — the corr materialization pattern (builds.cross_stats
# compute/_gpu_corr.INTERVAL_DAYS). Values are sampled only on grid dates;
# "interval" is a PK member so alternative strides coexist.
INTERVAL_DAYS = 20

# Trailing window lengths (trading days) of the rolling Cov/Var hedge
# ratio, per mode.
RATIO_WINDOWS = (5, 20, 60, 255)

# Broad-market benchmark INDEX codes materialized as benchmark_market
# (the offset mode's market factor — its own-calendar pct change is
# subtracted from both industries). 000300 沪深300 is the offsets
# table's materialized benchmark and the page default.
MARKET_BENCHMARKS = ("000300",)

# ---------------------------------------------------------------------------
#  Sources / universe
# ---------------------------------------------------------------------------
SOURCE_TABLE = "stats.index_basic_stats"
CLASSIFICATION_TABLE = "stats.sec_classification"

# The subject grain discriminator (static.label_dim domain 'pair_grain':
# 0 = per-index pair rows, 1 = industry aggregate over member indexes).
GRAIN_CODE = PAIR_GRAIN_CODE

__all__ = [
    "TABLE",
    "ANALYSIS_NAME",
    "ANALYSIS_DESCRIPTION",
    "INTERVAL_DAYS",
    "RATIO_WINDOWS",
    "MARKET_BENCHMARKS",
    "SOURCE_TABLE",
    "CLASSIFICATION_TABLE",
    "GRAIN_CODE",
]
