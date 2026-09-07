"""builds.market_hypes — market-hype EPISODE detector build (ETF+Index+Stock).

Populates stats.mov_ave_market_hypes (see
database/sql/stats/16_mov_ave_market_hypes.sql): one row per
(sec_type, code, min_checkin_period, EPISODE) — a CONCATENATED hype
episode: a maximal span of trading dates anchored on a maximal run of
consecutive "hyped" dates (trading_amount AND price volatility BOTH
elevated, SUSTAINEDLY over a check-in window, each against its own
CENTERED 20-year percentile threshold), extended through the
surrounding check-in evidence and bucketed BY ITS SPAN
([W, next window)).

MIGRATED from analyze.mov_ave_spread.market_hypes (formerly an internal
step of the mov_ave_spread pipeline): the build now runs standalone via
``python -m builds.market_hypes``, fetching its own source data (price
+ trading_amount from the stats schema, computing the std_{W}days
volatility columns itself) instead of reusing the parent pipeline's
DataFrame. The computation semantics are unchanged.

Module layout (cross_stats convention):
  config.py  — pure constants (table, parameters, source SQL)
  fetch.py   — DB reads only (per-sec_type source + rolling stds)
  compute.py — pure pandas/cudf compute (flags -> episodes)
  runner.py  — orchestration + DELETE-scope / chunked COPY writes
  __main__.py — thin CLI (--force / --sec-type / --code)

Rebuild semantics (margin_changes precedent): episode boundaries shift
whenever new dates arrive and non-hyped dates leave no footprint, so
there is NO per-date incremental upsert — every run DELETEs its scope
(one sec_type, or one --code) and recomputes ALL episodes from the FULL
per-code history; --force additionally truncates the table upfront.
"""
# cudf.pandas activation — must run before pandas first import (the
# config / fetch / compute / runner submodules import pandas at module
# scope; importing this package activates the hook first).
from _common.df_utils._activate import activate

activate()

from builds.market_hypes.config import (  # noqa: E402
    MARKET_HYPES_COLUMNS,
    MARKET_HYPES_DESCRIPTION,
    TABLE,
)
from builds.market_hypes.runner import (  # noqa: E402
    run_build,
    run_market_hypes,
)

__all__ = [
    "MARKET_HYPES_COLUMNS",
    "MARKET_HYPES_DESCRIPTION",
    "TABLE",
    "run_build",
    "run_market_hypes",
]
